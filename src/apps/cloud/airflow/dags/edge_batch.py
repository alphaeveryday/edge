"""EDGE 유한 배치를 Airflow 에서 실행하는 공통 연결 코드 — DAG 가 아니다(레인 DAG 들이 import).

책임 경계:
- Airflow: 슬롯 일정, task 의존, 인프라 재시도(컨테이너가 exit code 를 못 낸 경우만), 실행 조회.
- 기존 ECS 태스크 정의·`data_pipeline.run` 명령: 업무 실행 그대로. 업무 로직을 여기로 옮기지 않는다.
- 원장(ops_*): 계획·attempt·업무 완료 판정의 정본. Airflow task 성공은 "컨테이너 exit 0" 까지만 뜻한다.

**파싱 시 외부 호출 없음**: 모듈 수준에서는 os.environ 만 읽는다(AWS·DB·Variable 조회 없음).
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timedelta, timezone

from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator
from airflow.sdk.exceptions import AirflowException, AirflowFailException

KST = timezone(timedelta(hours=9))
CONTAINER = "data-pipeline"              # tasks.tf local.container_name
# db.PIPELINE_ID + "\x01" 구분자 — data_pipeline.db.stable_domain_id 와 **같아야 한다**.
# Airflow 환경에 업무 패키지를 설치하지 않으려고 복제했고, 동등성은 tests/ 가 실제 함수와 대조한다.
_PIPELINE_ID = "alphamale-etf-daily-v1"

# 배포 환경값 — terraform 출력(클러스터·서브넷·보안그룹·태스크 정의 이름 접두)을 Airflow 환경변수로 준다.
CLUSTER = os.environ.get("EDGE_ECS_CLUSTER", "")
TASKDEF_PREFIX = os.environ.get("EDGE_ECS_TASKDEF_PREFIX", "edge-dev-data-pipeline")
NETWORK = {"awsvpcConfiguration": {
    "subnets": [s for s in os.environ.get("EDGE_ECS_SUBNETS", "").split(",") if s],
    "securityGroups": [s for s in os.environ.get("EDGE_ECS_SECURITY_GROUPS", "").split(",") if s],
    "assignPublicIp": "DISABLED",
}}
LOG_GROUP = os.environ.get("EDGE_ECS_LOG_GROUP") or None   # 없으면 CloudWatch 로그를 끌어오지 않는다
# 태스크 정의별 awslogs-stream-prefix(tasks.tf `raw-ingest`, ops_ledger.tf `ops`).
_LOG_PREFIX = {"ops": "ops"}
# data_pipeline.ops.wrapper.STEP_NOT_RUN_EXIT — 실행권·이력을 확인 못 해 업무를 실행하지 않았다(재시도 대상).
STEP_NOT_RUN_EXIT = 75


def reprocess_slot(context) -> datetime | None:
    """재처리 run 이 지목한 기존 업무 슬롯(conf `reprocess_slot`, ISO). 없으면 None."""
    raw = (context["dag_run"].conf or {}).get("reprocess_slot")
    return datetime.fromisoformat(raw) if raw else None


def slot_time(context) -> datetime:
    """이 DAG run 의 업무 슬롯 시각.
    - 일정 run: logical_date(= cron 발화 시각).
    - 재처리 run: conf 의 reprocess_slot(요청 시각이 아니라 다시 처리할 슬롯).
    - logical_date 없이 수동 trigger 된 run: run_after(요청 시각) — 현행 수동 plan-run 과 같은 의미."""
    return reprocess_slot(context) or context["logical_date"] or context["dag_run"].run_after


def today_kst() -> date:
    """당일 판정의 '오늘'. 저장된 과거 슬롯을 재생하는 로컬 검증만 `EDGE_LAB_TODAY_KST` 로 고정한다 —
    운영 배포 환경에는 이 변수를 두지 않는다."""
    override = os.environ.get("EDGE_LAB_TODAY_KST")
    return date.fromisoformat(override) if override else datetime.now(KST).date()


def run_status(codes: dict) -> str:
    """스텝 exit code 들 → 런 판정(ASL RawPartialCheck 와 같은 뜻). 전부 0 이어야 SUCCEEDED."""
    return "SUCCEEDED" if codes and all(code == 0 for code in codes.values()) else "FAILED"


def run_key(lane: str, slot: datetime) -> str:
    """planner.slot_run_key 와 같은 형식 — `<lane>:<YYYY-MM-DDTHH:MM>`(KST, 분 단위)."""
    return f"{lane}:{slot.astimezone(KST).strftime('%Y-%m-%dT%H:%M')}"


def pipeline_run_id(lane: str, slot: datetime) -> str:
    """Planner 가 같은 슬롯에 만드는 pipeline_run_id — 업무 스텝의 `--run-id`."""
    material = "\x01".join([_PIPELINE_ID, run_key(lane, slot)])
    return f"run_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:26]}"


class EdgeStep(EcsRunTaskOperator):
    """기존 ECS 태스크 정의로 `data_pipeline.run` 한 스텝을 실행한다.

    exit code 해석(ASL `CheckExitCode`·카탈로그 `fulfilled_exit_codes` 와 대조해 정했다):
    - 0: 성공.
    - `partial_exit_codes`(예: 정제 2 = 성공 winner 는 commit 된 입력 부분 실패): task 는 성공으로 두어
      하류가 manifest 를 소비하게 하고 XCom `exit_code` 로 남긴다. 런 최종 실패는 verdict task 가 낸다.
    - 그 밖의 비0: 업무 실패. **Airflow 재시도 안 함**(AirflowFailException) — 업무 재시도는 앱 내부가
      이미 한다(HTTP 백오프·KIS rate 재시도). 여기서 또 돌리면 외부 호출 수가 곱해진다.
    - exit code 없음(기동 실패·호스트 중단·응답 유실) 또는 75(업무 미실행 — 실행권을 못 잡았거나 원장을
      못 읽음): `retries` 만큼 재시도한다.

    판정은 provider 가 예외를 냈는지가 아니라 **ECS 가 보고한 컨테이너 exit code** 로 한다 — provider 는
    대기(WaiterError)·로그 경로에서도 예외를 내고, 컨테이너 정보가 빈 응답엔 예외 없이 반환한다.

    중복 실행 방지:
    - `exclusive=True`(업무 스텝) → `OPS_EXCLUSIVE_STEP=1`: 컨테이너가 작업별 실행권(PostgreSQL 세션
      advisory lock)을 잡은 뒤에만 실행한다. 같은 작업이 돌고 있으면 기다린다(최대 600초). 원장을 읽지
      못하면 실행하지 않는다(75).
    - `skip_if_succeeded=True`(외부 호출이 있는 수집만, 재처리 run 제외) → `OPS_SKIP_IF_SUCCEEDED=1`:
      실행권 안에서 최신 attempt 가 exit 0 이면 실행 없이 끝낸다. provider 의 reattach 는 RUNNING 태스크만
      찾으므로 끝난 태스크의 응답을 잃은 재시도는 새 태스크를 띄운다. 정제·적재는 외부 호출이 없고 멱등이라
      다시 돌린다 — 다른 슬롯이 같은 파티션을 갱신한 뒤의 복구는 정제를 다시 돌려야만 된다.
    """

    def __init__(self, *, taskdef_key: str, command: list[str], env: dict[str, str] | None = None,
                 partial_exit_codes: tuple[int, ...] = (), same_day_only: bool = False,
                 noop_on_reprocess: bool = False, exclusive: bool = True,
                 skip_if_succeeded: bool = False, reprocess_env: dict[str, str] | None = None,
                 **kwargs):
        environment = [{"name": k, "value": v} for k, v in (env or {}).items()]
        super().__init__(
            cluster=CLUSTER, task_definition=f"{TASKDEF_PREFIX}-{taskdef_key}",
            launch_type="FARGATE", network_configuration=NETWORK,
            overrides={"containerOverrides": [
                {"name": CONTAINER, "command": command, "environment": environment}]},
            reattach=True,                  # scheduler·worker 재시작 뒤 RUNNING 태스크에 다시 붙는다
            # 환경 기본값(operators.default_deferrable)을 상속하지 않는다 — defer 하면 재개가 provider 의
            # execute_complete 로 가서 아래 exit code 판정·XCom 을 건너뛴다.
            deferrable=False,
            awslogs_group=LOG_GROUP,
            awslogs_stream_prefix=(f"{_LOG_PREFIX.get(taskdef_key, 'raw-ingest')}/{CONTAINER}"
                                   if LOG_GROUP else None),
            **kwargs,
        )
        self.partial_exit_codes = partial_exit_codes
        self.same_day_only = same_day_only
        self.noop_on_reprocess = noop_on_reprocess
        self.exclusive = exclusive
        self.skip_if_succeeded = skip_if_succeeded
        self.reprocess_env = reprocess_env or {}

    def execute(self, context):
        ti = context["ti"]
        reprocess = reprocess_slot(context) is not None
        if reprocess and self.noop_on_reprocess:
            # skip 이 아니라 성공 no-op 이다 — skip 은 하류 정제·적재까지 전파돼 재처리 자체가 안 돈다
            # (3.3.2 로컬 관찰: all_done_min_one_success 하류가 skipped). ECS 는 띄우지 않는다.
            self.log.info("재처리 run — 이미 있는 raw 를 쓰므로 수집하지 않는다(ECS 실행 없음)")
            return None
        if self.same_day_only and not reprocess:
            # 장중 추정처럼 날짜 파라미터가 없는 소스는 "지금"의 값을 슬롯 날짜로 라벨한다. 과거 슬롯을
            # 다시 수집하면 오늘 데이터가 과거 run_id 에 저장된다 → 슬롯 날짜가 오늘이 아니면 거부.
            slot_day = slot_time(context).astimezone(KST).date()
            today = today_kst()
            if slot_day != today:
                raise AirflowFailException(
                    f"소급 수집 불가: 슬롯 {slot_day} ≠ 오늘 {today}. 이 소스는 날짜 지정이 없다 — "
                    "이미 있는 슬롯의 재정제·재적재는 conf reprocess_slot 으로 trigger 한다.")
        environment = self.overrides["containerOverrides"][0]["environment"]
        if self.exclusive:
            environment.append({"name": "OPS_EXCLUSIVE_STEP", "value": "1"})
        if self.skip_if_succeeded and not reprocess:
            # 같은 run 의 재시도·clear 가 이미 성공한 수집을 다시 부르면 업무 코드가 실행 없이 끝낸다.
            environment.append({"name": "OPS_SKIP_IF_SUCCEEDED", "value": "1"})
        if reprocess:
            environment.extend({"name": k, "value": v} for k, v in self.reprocess_env.items())
        failure: Exception | None = None
        try:
            super().execute(context)
        except Exception as exc:        # AirflowException 밖(WaiterError 등)도 종료 코드로 판정한다
            failure = exc
        code = self._exit_code()
        if code is not None:
            ti.xcom_push(key="exit_code", value=code)
        if code == 0:
            if failure is not None:
                self.log.warning("ECS 확인 결과 exit 0 — provider 예외를 성공으로 본다: %s", failure)
            return None
        if code in self.partial_exit_codes:
            self.log.warning("부분 실패 exit %s — 하류는 계속, 런 판정은 verdict", code)
            return None
        if code is None or code == STEP_NOT_RUN_EXIT:
            # 인프라·미확정·업무 미실행 — 재시도 대상. 예외 없이 반환했어도 exit 를 못 봤으면 성공이 아니다.
            if failure is not None:
                raise failure
            raise AirflowException(f"ECS 종료 코드를 확인하지 못했다(ECS {self.arn}) — 재시도")
        raise AirflowFailException(f"업무 실패 exit {code} (ECS {self.arn})") from failure

    def _exit_code(self) -> int | None:
        """정지한 컨테이너의 정수 exit code. 없거나 확인 불가면 None(단정하지 않는다)."""
        if not self.arn:
            return None
        try:
            tasks = self.client.describe_tasks(cluster=self.cluster, tasks=[self.arn])["tasks"]
        except Exception:
            self.log.exception("exit code 확인 실패 — 인프라 실패로 취급")
            return None
        if not tasks or tasks[0].get("lastStatus") != "STOPPED":
            return None
        code = (tasks[0].get("containers") or [{}])[0].get("exitCode")
        return code if isinstance(code, int) and not isinstance(code, bool) else None
