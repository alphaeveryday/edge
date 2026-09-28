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
import time
from datetime import date, datetime, timedelta, timezone
from functools import cached_property

from airflow.providers.amazon.aws.exceptions import EcsOperatorError
from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator
from airflow.sdk.exceptions import AirflowException, AirflowFailException, AirflowSkipException
from botocore.exceptions import ClientError

KST = timezone(timedelta(hours=9))
CONTAINER = "data-pipeline"              # tasks.tf local.container_name
# db.PIPELINE_ID + "\x01" 구분자 — data_pipeline.db.stable_domain_id 와 **같아야 한다**.
# Airflow 환경에 업무 패키지를 설치하지 않으려고 복제했다. 대조는 두 쪽에서 한다: airflow/tests 는 실제
# dev run_id 벡터로, data-pipeline tests/test_airflow_dag_contract.py 는 이 파일의 상수·env 이름을 업무
# 코드의 정의와 직접 비교한다(이 파일을 바꾸면 test-python 도 돈다).
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
# data_pipeline.ops.wrapper.STEP_HELD_EXIT — 같은 작업의 앞선 실행 종료가 원장에 확인되지 않아 보류했다.
STEP_HELD_EXIT = 76
# data_pipeline.ops.states.HOLD_* — 보류 종류(원장 EXECUTION_HOLD 이슈 evidence.kind).
HOLD_OPEN_ATTEMPT = "OPEN_ATTEMPT"
HOLD_ECS_STATE_UNKNOWN = "ECS_STATE_UNKNOWN"
HOLD_RESULT_UNKNOWN = "RESULT_UNKNOWN"


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


class HoldExecution(AirflowFailException):
    """새 ECS 태스크를 시작하지 않고 보류한다 — 자동 재시도하지 않는다(AirflowFailException).

    task clear 로 다시 돌려도 같은 확인을 다시 거쳐 같은 이유로 멈춘다. 풀리는 길은 운영자가 기존 작업의
    종료를 확인한 뒤의 절차(README "보류 해제")뿐이다. XCom `hold` 로 종류·사유를 남기고, DAG report 가
    그것을 원장(EXECUTION_HOLD 이슈)에 옮긴다."""

    def __init__(self, kind: str, reason: str):
        super().__init__(f"실행 보류({kind}): {reason}")
        self.kind, self.reason = kind, reason


# ECS 태스크 하나의 판정. 새 태스크를 띄워도 되는 것은 NOT_RUN(업무를 시작하지 않았음이 확인됨)뿐이다.
ALIVE, SUCCESS, PARTIAL, FAILED, NOT_RUN, HELD, RESULT_UNKNOWN = (
    "ALIVE", "SUCCESS", "PARTIAL", "FAILED", "NOT_RUN", "HELD", "RESULT_UNKNOWN")


def ecs_stop_evidence(task: dict) -> tuple[str, int | None]:
    """DescribeTasks 의 태스크 하나 → 종료 증거 (종류, exit). data-pipeline `reconciler._ecs_stop_evidence` 와
    **같은 규칙**이다(두 쪽 테스트가 `tests/ecs_stop_cases.json` 한 표로 대조한다).

    - ALIVE: STOPPED 가 아니다(StopTask 요청 뒤 종료 중 포함).
    - NOT_STARTED: stopCode TaskFailedToStart — 컨테이너가 뜨지 않았다.
    - EXITED: 컨테이너가 스스로 끝났다 — exit 0, 또는 128 미만 비0 이면서 stopCode 가 없거나
      EssentialContainerExited. 이 exit 는 업무 결과다.
    - KILLED: 끝났지만 스스로 끝났다는 증거가 없다 — exit 없음, exit ≥ 128(신호로 죽음: SIGKILL·OOM 137,
      SIGTERM 143), 또는 외부 종료 stopCode(UserInitiated·SpotInterruption·ServiceSchedulerInitiated…)의 비0.
      업무 도중 끊겼을 수 있어 결과 미상이다. stopCode 가 없다는 것만으로 판정하지 않는다 — exit 가 신호
      범위인지가 증거다.
    """
    if task.get("lastStatus") != "STOPPED":
        return "ALIVE", None
    if task.get("stopCode") == "TaskFailedToStart":
        return "NOT_STARTED", None
    code = (task.get("containers") or [{}])[0].get("exitCode")
    if not isinstance(code, int) or isinstance(code, bool):
        return "KILLED", None
    if code == 0:
        return "EXITED", 0
    if code >= 128 or task.get("stopCode") not in (None, "EssentialContainerExited"):
        return "KILLED", code
    return "EXITED", code


def ecs_verdict(task: dict, partial_exit_codes=()) -> str:
    """종료 증거(`ecs_stop_evidence`) → 이 스텝의 판정. 모르는 것은 모른다고 판정한다.
    ALIVE·NOT_STARTED(→ NOT_RUN)·KILLED(→ RESULT_UNKNOWN) 는 그대로, EXITED 는 업무 exit 로 읽는다:
    0 SUCCESS · 75 NOT_RUN(업무 미시작) · 76 HELD(업무 미시작, 원장 보류) · 부분 실패 PARTIAL · 그 밖 FAILED."""
    kind, code = ecs_stop_evidence(task)
    if kind == "ALIVE":
        return ALIVE
    if kind == "NOT_STARTED":
        return NOT_RUN
    if kind == "KILLED":
        return RESULT_UNKNOWN
    if code == 0:
        return SUCCESS
    if code == STEP_NOT_RUN_EXIT:
        return NOT_RUN
    if code == STEP_HELD_EXIT:
        return HELD
    return PARTIAL if code in partial_exit_codes else FAILED


def settlement(ti, task_id: str) -> dict | None:
    """스텝의 결말 — verdict·report 가 쓰는 보류 판단. hold 가 있으면 그것, 시작했는데 결말(exit_code·no_ecs_task·
    hold)이 없으면 ECS_STATE_UNKNOWN 보류(worker 사망·수동 failed — 제출한 ECS 가 아직 돌 수 있다), 그 밖은 None.
    시작 표시가 없으면(skip·upstream_failed·큐 대기 중 종료) 그 스텝은 아무것도 제출하지 않았다."""
    hold = ti.xcom_pull(task_ids=task_id, key="hold")
    if hold:
        return hold
    if not ti.xcom_pull(task_ids=task_id, key="edge_started"):
        return None
    if ti.xcom_pull(task_ids=task_id, key="exit_code") is not None:
        return None
    if ti.xcom_pull(task_ids=task_id, key="no_ecs_task"):
        return None
    arn = ti.xcom_pull(task_ids=task_id, key="ecs_task_arn")
    return {"kind": HOLD_ECS_STATE_UNKNOWN,
            "reason": f"결말 없이 끝남(worker 중단·수동 종료·시간 초과) — ECS {arn or '제출 여부 미상'}"}


def _unsettled(ti, task_id: str) -> str | None:
    """선행 스텝의 결말이 확인되지 않았으면 이유. exit_code(ECS 가 끝을 보고함)·no_ecs_task(태스크 없음)가 있으면 확인,
    hold 가 있으면 보류, 셋 다 없으면 미확인이다(EdgeStep 이 아닌 선행 task 는 이 레인에 없다)."""
    hold = ti.xcom_pull(task_ids=task_id, key="hold")
    if hold:
        return f"보류 {hold.get('kind')}"
    if ti.xcom_pull(task_ids=task_id, key="exit_code") is not None:
        return None
    if ti.xcom_pull(task_ids=task_id, key="no_ecs_task"):
        return None
    return "결말 기록 없음(exit_code·보류·태스크 없음 표시 모두 없음)"


class _IdempotentRunTask:
    """RunTask 에 clientToken 을 붙이는 ECS 클라이언트 래퍼. provider 는 토큰 없이 부르고, boto 는 연결 끊김·
    5xx 에 같은 요청을 자동 재전송한다 — 토큰이 없으면 응답만 잃은 요청이 태스크를 하나 더 만든다."""

    def __init__(self, client, token):
        self._client, self._token = client, token

    def run_task(self, **kwargs):
        return self._client.run_task(clientToken=self._token(), **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


class EdgeStep(EcsRunTaskOperator):
    """기존 ECS 태스크 정의로 `data_pipeline.run` 한 스텝을 실행한다.

    **새 ECS 태스크는 앞선 시도의 결말을 확인한 뒤에만 띄운다**(초기 운영 정책, README "재시도·보류 정책"):
    1. 제출 전: 이 task instance 의 모든 시도가 쓰는 결정적 startedBy 로 ECS 를 조회한다(RUNNING·STOPPED).
       살아 있는 태스크가 하나면 재접속, 둘 이상이면 보류. 끝난 태스크가 있으면 가장 최근 것의 판정을 따른다 —
       업무 미시작(75·기동 실패·76)이면 새로 띄우고, 결과 미상이면 보류한다. 끝난 성공·실패는 자동 재시도면 그
       결과를 쓰고(업무를 반복하지 않는다), clear 뒤 첫 시도면 수집의 성공만 재사용하고 나머지는 새로 띄운다.
       아무것도 안 보이는데 첫 시도가 아니면 보류한다 — 빈 조회는 "제출하지 않았다"는 증거가 아니다.
       조회 실패(일시 오류 재시도 뒤)도 보류다.
    2. 제출: clientToken(시도별)을 붙인다. ECS 가 배치 거부를 확정한 경우(failures·Throttling)만 같은 시도
       안에서 다시 제출한다. 응답을 잃으면(연결·타임아웃·5xx) 다시 제출하지 않고 startedBy 로 새 태스크를
       찾아 붙는다 — 못 찾으면 보류.
    3. 종료 판정: `ecs_verdict`. 0·부분 실패는 성공(부분은 XCom exit_code 로 verdict 가 런 실패로 마감),
       업무 실패는 재시도 없이 실패, 미실행(75·기동 실패)은 재시도, 결과 미상·컨테이너 보류(76)는 보류,
       확인 불가(조회 실패·아직 도는 중)는 재시도 — 다음 시도가 1 의 확인부터 다시 한다.

    업무 재시도는 앱 내부가 한다(HTTP 백오프·KIS rate 재시도) — Airflow 가 업무 실패를 또 돌리면 외부 호출이
    곱해진다. 판정은 provider 예외가 아니라 ECS 가 보고한 컨테이너 상태로 한다(provider 는 대기·로그
    경로에서도 예외를 내고, 컨테이너 정보가 빈 응답엔 예외 없이 반환한다).

    컨테이너 안(wrapper, `exclusive=True` → `OPS_EXCLUSIVE_STEP=1`): 작업별 실행권(PostgreSQL advisory lock)을
    잡고, 원장에 종료가 확인되지 않은 같은 작업의 시도·ECS 보류가 있으면 업무를 시작하지 않는다(76). 다른
    run(슬롯·재처리)의 컨테이너는 startedBy 가 달라 1 에서 보이지 않으므로 이쪽이 막는다.
    `skip_if_succeeded=True`(외부 호출이 있는 수집만, 재처리 run 제외) → `OPS_SKIP_IF_SUCCEEDED=1`: 원장의
    최신 시도가 exit 0 이면 실행 없이 끝낸다(ECS 기록이 사라진 뒤의 재시도용 — 1 이 먼저 막는다).
    """

    # 조회·재제출 횟수와 간격. 일시 오류만 흡수한다 — 상태를 모르는 것을 기다려서 해결하지 않는다.
    READS = 3
    SUBMITS = 3
    PAUSE_SECONDS = 10.0

    def __init__(self, *, taskdef_key: str, command: list[str], env: dict[str, str] | None = None,
                 partial_exit_codes: tuple[int, ...] = (), same_day_only: bool = False,
                 noop_on_reprocess: bool = False, exclusive: bool = True,
                 skip_if_succeeded: bool = False, reprocess_env: dict[str, str] | None = None,
                 stop_on_upstream_hold: bool = True, **kwargs):
        # 이 ECS 태스크를 띄운 Airflow 시도 — wrapper 가 attempt 에 남긴다(원장 → Airflow 역추적).
        # try_number 는 렌더링 시점(=이 시도)의 값이다. 재접속한 시도는 새 attempt 를 만들지 않는다.
        env = {"OPS_ORCHESTRATOR_ATTEMPT_REF":
               "airflow:{{ dag.dag_id }}/{{ run_id }}/{{ task.task_id }}/{{ ti.try_number }}", **(env or {})}
        environment = [{"name": k, "value": v} for k, v in env.items()]
        super().__init__(
            cluster=CLUSTER, task_definition=f"{TASKDEF_PREFIX}-{taskdef_key}",
            launch_type="FARGATE", network_configuration=NETWORK,
            overrides={"containerOverrides": [
                {"name": CONTAINER, "command": command, "environment": environment}]},
            reattach=True,                  # 제출 전 확인(_try_reattach_task)을 provider 가 부르게 한다
            # 환경 기본값(operators.default_deferrable)을 상속하지 않는다 — defer 하면 재개가 provider 의
            # execute_complete 로 가서 아래 판정·XCom 을 건너뛴다.
            deferrable=False,
            # 대기 오류에 provider 가 **도는 업무 컨테이너를 StopTask 로 죽이지 않게** 한다 — 죽이면 업무 도중
            # 끊겨 결과 미상이 된다. 태스크는 그대로 두고 다음 시도가 재접속한다.
            stop_task_on_failure=False,
            container_name=CONTAINER,       # 로그 스트림 이름을 위해 provider 가 기동 뒤 조회·대기하지 않게
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
        self.stop_on_upstream_hold = stop_on_upstream_hold
        self._try_number = 1
        self._submission = 0
        self._known_arns: set[str] = set()
        self._ti = None
        self._new_request = True

    @cached_property
    def client(self):
        return _IdempotentRunTask(self.hook.conn, self._client_token)

    def _client_token(self) -> str:
        """시도·제출별 멱등 토큰(ECS 최대 64자). 같은 제출의 boto 재전송은 같은 토큰이다."""
        material = f"{self._started_by}/{self._try_number}/{self._submission}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:64]

    def execute(self, context):
        # 시작 표시 — 이 시도가 결말(exit_code·hold·no_ecs_task)을 남기기 전에 worker 가 죽거나 운영자가 failed 로
        # 표시하면, 이 표시만 남는다. verdict·report 는 그것을 "결말 없음 = 보류"로 읽는다(`settlement`).
        context["ti"].xcom_push(key="edge_started", value=True)
        try:
            return self._execute(context)
        except HoldExecution as hold:
            context["ti"].xcom_push(key="hold", value={"kind": hold.kind, "reason": hold.reason})
            raise

    def _execute(self, context):
        ti = self._ti = context["ti"]
        self._try_number = ti.try_number
        # clear 는 max_tries 를 "마지막 try + retries" 로 다시 잡는다(3.3.2 models/taskinstance.py) — 그래서 요청(최초
        # 실행·clear)의 첫 시도는 try_number == max_tries - retries + 1 이고, 그 밖의 try 는 자동 재시도다.
        max_tries = getattr(ti, "max_tries", None)
        self._new_request = not isinstance(max_tries, int) or ti.try_number == max_tries - self.retries + 1
        reprocess = reprocess_slot(context) is not None
        if reprocess and self.noop_on_reprocess:
            # skip 이 아니라 성공 no-op 이다 — skip 은 하류 정제·적재까지 전파돼 재처리 자체가 안 돈다
            # (3.3.2 로컬 관찰: all_done_min_one_success 하류가 skipped). ECS 는 띄우지 않는다.
            self.log.info("재처리 run — 이미 있는 raw 를 쓰므로 수집하지 않는다(ECS 실행 없음)")
            self._no_task()
            return None
        if self.same_day_only and not reprocess:
            # 장중 추정처럼 날짜 파라미터가 없는 소스는 "지금"의 값을 슬롯 날짜로 라벨한다. 과거 슬롯을
            # 다시 수집하면 오늘 데이터가 과거 run_id 에 저장된다 → 슬롯 날짜가 오늘이 아니면 거부.
            slot_day = slot_time(context).astimezone(KST).date()
            today = today_kst()
            if slot_day != today:
                self._no_task()
                raise AirflowFailException(
                    f"소급 수집 불가: 슬롯 {slot_day} ≠ 오늘 {today}. 이 소스는 날짜 지정이 없다 — "
                    "이미 있는 슬롯의 재정제·재적재는 conf reprocess_slot 으로 trigger 한다.")
        unsettled = {t: why for t in sorted(self.upstream_task_ids) if self.stop_on_upstream_hold
                     and (why := _unsettled(ti, t))}
        if unsettled:
            # 선행 스텝이 보류됐거나 결말을 남기지 못했다(마지막 시도 중 worker 사망·수동 failed 표시) — 그 ECS
            # 태스크가 아직 입력을 쓰고 있을 수 있다. trigger rule(부분 실패여도 정제)과 별개로 시작하지 않는다.
            # skip 이라 하류도 멈추고, report(ALL_DONE)는 돌아 보류를 원장에 옮긴다.
            self._no_task()
            raise AirflowSkipException(f"선행 스텝의 결말 미확인으로 시작하지 않는다: {unsettled}")
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
        except (HoldExecution, AirflowFailException):
            raise                       # 제출 전 확인·제출 단계가 이미 판정했다
        except Exception as exc:        # 대기·로그 경로 예외(WaiterError 등)도 컨테이너 상태로 판정한다
            failure = exc
        if not self.arn:
            # 제출 전 확인·제출이 태스크를 정하지 못했는데 판정 예외도 없었다 — 무엇이 떴는지 모른다.
            raise HoldExecution(HOLD_ECS_STATE_UNKNOWN, f"ECS 태스크를 식별하지 못했다: {failure}")
        task = self._describe_own()
        verdict = ecs_verdict(task, self.partial_exit_codes) if task is not None else None
        code = (task.get("containers") or [{}])[0].get("exitCode") if task is not None else None
        if isinstance(code, int) and not isinstance(code, bool):
            ti.xcom_push(key="exit_code", value=code)
        if verdict == SUCCESS:
            if failure is not None:
                self.log.warning("ECS 확인 결과 exit 0 — provider 예외를 성공으로 본다: %s", failure)
            return None
        if verdict == PARTIAL:
            self.log.warning("부분 실패 exit %s — 하류는 계속, 런 판정은 verdict", code)
            return None
        if verdict == FAILED:
            raise AirflowFailException(f"업무 실패 exit {code} (ECS {self.arn})") from failure
        if verdict == HELD:
            raise HoldExecution(HOLD_OPEN_ATTEMPT, f"컨테이너가 앞선 실행 미종료로 보류(ECS {self.arn}) — "
                                                   "원장 EXECUTION_HOLD 참조")
        if verdict == RESULT_UNKNOWN:
            raise HoldExecution(HOLD_RESULT_UNKNOWN,
                                f"ECS 종료는 확인됐으나 업무 결과 미상(ECS {self.arn} stopCode="
                                f"{task.get('stopCode')} exit={code})")
        # NOT_RUN(업무 미실행 확인)·ALIVE(대기 실패, 아직 돈다)·조회 불가 → 재시도. 다음 시도가 제출 전
        # 확인으로 재접속·새 태스크·보류를 다시 가린다 — 여기서 새 태스크를 띄우지 않는다.
        max_tries = getattr(ti, "max_tries", None)
        if verdict != NOT_RUN and isinstance(max_tries, int) and ti.try_number > max_tries:
            # 마지막 시도 — 도는(또는 상태를 모르는) 태스크를 두고 "실패"로 닫으면 운영자는 끝난 것으로 읽는다.
            raise HoldExecution(HOLD_ECS_STATE_UNKNOWN,
                                f"재시도 소진 — ECS {self.arn} 상태 {verdict or '조회 불가'}: {failure}")
        raise AirflowException(
            f"업무 미시작 또는 종료 판정 불가(ECS {self.arn}, {verdict}) — 재시도: {failure}") from failure

    def on_kill(self) -> None:
        """provider 는 여기서 ECS 태스크를 StopTask 로 멈춘다(run failed 표시·dagrun_timeout·worker 종료). 업무
        도중 끊으면 결과 미상이 되므로 **멈추지 않는다** — 태스크는 끝까지 돌고, 다음 시도·다른 run 은 제출 전
        확인과 원장 게이트로 그 옆에서 시작하지 않는다. 멈춰야 하면 운영자가 `aws ecs stop-task` 로 한다."""
        if self.task_log_fetcher:
            self.task_log_fetcher.stop()
        self.log.warning("Airflow task 종료 — ECS 태스크 %s 는 멈추지 않는다(끝까지 돈다)", self.arn)

    # ── 1. 제출 전 확인 ──
    def _tasks_started_by(self, started_by: str) -> list[dict]:
        """이 task instance 가 띄운 ECS 태스크 전부(살아 있는 것·끝난 것). 조회가 불완전하면 예외."""
        arns: list[str] = []
        for status in ("RUNNING", "STOPPED"):
            arns += self.client.list_tasks(cluster=self.cluster, startedBy=started_by,
                                           desiredStatus=status)["taskArns"]
        if not arns:
            return []
        resp = self.client.describe_tasks(cluster=self.cluster, tasks=arns)
        if resp.get("failures"):
            raise RuntimeError(f"DescribeTasks 불완전: {resp['failures']}")
        return resp["tasks"]

    def _read_started_by(self) -> list[dict]:
        last: Exception | None = None
        for n in range(self.READS):
            if n:
                time.sleep(self.PAUSE_SECONDS)
            try:
                return self._tasks_started_by(self._started_by)
            except Exception as exc:
                last = exc
                self.log.warning("ECS 상태 조회 실패 %d/%d: %s", n + 1, self.READS, exc)
        if self._try_number == 1:
            # 첫 시도엔 이 task instance 의 태스크가 있을 수 없다(startedBy 가 run 마다 다르다) — 모르는 것이 없으니
            # 보류(작업 전체 차단)가 아니라 제출하지 않은 실패로 끝낸다. 원장엔 미실행(MISSED)으로 남는다.
            self._no_task()
            raise AirflowFailException(f"ECS 상태 조회 실패 — 제출하지 않았다: {last}")
        raise HoldExecution(HOLD_ECS_STATE_UNKNOWN, f"ECS 상태 조회 실패 — 새 태스크를 띄우지 않는다: {last}")

    def _try_reattach_task(self, started_by: str):
        """provider 가 제출 직전에 부른다(reattach=True). self.arn 을 채우면 provider 는 새로 띄우지 않고 그
        태스크를 기다린다."""
        tasks = self._read_started_by()
        self._known_arns = {t["taskArn"] for t in tasks}
        alive = [t for t in tasks if ecs_verdict(t) == ALIVE]
        if len(alive) > 1:
            raise HoldExecution(HOLD_ECS_STATE_UNKNOWN,
                                f"살아 있는 ECS 태스크가 {len(alive)}개: {[t['taskArn'] for t in alive]}")
        if alive:
            self.arn = alive[0]["taskArn"]
            self.log.warning("실행 중인 ECS 태스크에 재접속: %s (새 업무 시도 없음)", self.arn)
            self._trace("ecs_reattached_arn")
            return
        if tasks:
            latest = max(tasks, key=lambda t: t.get("createdAt") or 0)
            verdict = ecs_verdict(latest, self.partial_exit_codes)
            if verdict in (NOT_RUN, HELD):
                reuse = False       # 업무를 시작하지 않았다 — 새 컨테이너가 원장 게이트를 다시 거친다
            elif verdict == RESULT_UNKNOWN:
                reuse = True        # 판정에서 보류로 이어진다
            elif not self._new_request:
                # 자동 재시도(판정 조회 실패·대기 오류·worker 중단 뒤) — 끝난 업무를 다시 돌리지 않는다. 업무 실패도
                # 재시도하지 않는다는 계약과 같다.
                reuse = True
            else:
                # clear 뒤 첫 시도 = 다시 돌리려는 요청. 수집의 성공만 재사용한다(외부 재호출 없음). 정제·적재·보고는
                # 멱등이고, 옛 결과를 쓰면 바뀐 입력(재수집 raw·보고할 보류)이 반영되지 않는다(로컬 V3).
                reuse = verdict == SUCCESS and self.skip_if_succeeded
            if not reuse:
                self.log.info("앞선 ECS 태스크 %s 는 끝났다(%s) — 새로 띄운다", latest["taskArn"], verdict)
                return
            self.arn = latest["taskArn"]
            self.log.warning("앞선 ECS 태스크 %s 의 결과(%s)로 판정한다 — 새로 띄우지 않는다",
                             self.arn, verdict)
            self._trace("ecs_reused_arn")
            return
        if self._try_number > 1:
            raise HoldExecution(HOLD_ECS_STATE_UNKNOWN,
                                f"시도 {self._try_number}: 앞선 시도의 ECS 태스크가 조회되지 않는다 — 제출하지 "
                                "않았는지, 조회에 아직 안 보이는지, 기록이 만료됐는지 가릴 수 없다")

    def _no_task(self) -> None:
        """이 task instance 에 살아 있는 ECS 태스크가 없다(제출하지 않았거나 모두 업무 미시작으로 끝남)는 표시 —
        하류가 이 스텝의 결말을 "확인됨"으로 읽는 근거다(exit_code·hold 가 없는 종료에 한해)."""
        try:
            self._ti.xcom_push(key="no_ecs_task", value=True)
        except Exception:
            pass

    def _trace(self, key: str) -> None:
        """새 태스크 없이 앞선 태스크를 쓴 시도의 흔적(새 attempt 가 생기지 않으므로 원장 밖의 유일한 흔적)."""
        from airflow.sdk import get_current_context
        try:
            get_current_context()["ti"].xcom_push(key=key, value=self.arn)
        except Exception:       # 컨텍스트 밖(단위 호출) — 로그로 충분하다
            pass

    # ── 2. 제출 ──
    def _start_task(self):
        refusal: Exception | None = None
        for n in range(self.SUBMITS):
            if n:
                time.sleep(self.PAUSE_SECONDS)
            self._submission = n
            try:
                return super()._start_task()
            except EcsOperatorError as exc:
                # ECS 가 이 토큰의 요청을 처리해 배치 거부를 확정했다(failures) — 태스크 없음. 같은 토큰의 앞선
                # 재전송이 태스크를 만들었다면 ECS 는 그 태스크를 돌려준다(failures 가 아니다).
                refusal = exc
            except ClientError as exc:
                error = exc.response.get("Error", {}).get("Code")
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0
                if self.arn:
                    raise                           # 생성은 됐다(뒤 조회 실패) — 판정은 종료 뒤에
                if error == "ThrottlingException":
                    # 마지막 응답만 거부다 — boto 가 먼저 보낸 같은 토큰 요청이 태스크를 만들었을 수 있다.
                    return self._track_submission(exc)
                if 400 <= status < 500:
                    self._no_task()
                    raise AirflowFailException(f"ECS 제출 거부(설정·권한): {error}") from exc
                else:
                    return self._track_submission(exc)
            except Exception as exc:              # 연결 끊김·타임아웃 — 생성 여부를 모른다
                if self.arn:
                    raise
                return self._track_submission(exc)
            self.log.warning("ECS 기동 거부 %d/%d: %s", n + 1, self.SUBMITS, refusal)
        self._no_task()
        raise AirflowFailException(f"ECS 기동 거부 반복 — 태스크 없음: {refusal}")

    def _track_submission(self, exc: Exception) -> None:
        """제출 응답을 잃었다. 다시 제출하지 않고 이 시도가 만든 태스크(앞서 보이지 않던 ARN)를 찾아 붙는다."""
        self.log.warning("ECS 제출 응답 유실 — 다시 제출하지 않고 추적한다: %s", exc)
        for n in range(self.READS):
            time.sleep(self.PAUSE_SECONDS)
            try:
                new = [t for t in self._tasks_started_by(self._started_by)
                       if t["taskArn"] not in self._known_arns]
            except Exception as read_exc:
                self.log.warning("추적 조회 실패 %d/%d: %s", n + 1, self.READS, read_exc)
                continue
            if len(new) == 1:
                self.arn = new[0]["taskArn"]
                self.log.warning("응답을 잃은 제출의 태스크를 찾았다: %s", self.arn)
                return
            if len(new) > 1:
                raise HoldExecution(HOLD_ECS_STATE_UNKNOWN,
                                    f"응답을 잃은 제출 뒤 새 태스크가 {len(new)}개: {[t['taskArn'] for t in new]}")
        raise HoldExecution(HOLD_ECS_STATE_UNKNOWN,
                            f"제출 응답 유실 — 태스크 생성 여부를 확인하지 못했다: {exc}")

    # ── 3. 종료 판정 ──
    def _describe_own(self) -> dict | None:
        """이 시도가 붙은 태스크의 현재 상태. 없거나 확인 불가면 None(단정하지 않는다)."""
        if not self.arn:
            return None
        try:
            tasks = self.client.describe_tasks(cluster=self.cluster, tasks=[self.arn])["tasks"]
        except Exception:
            self.log.exception("ECS 종료 확인 실패 — 판정 보류(재시도)")
            return None
        return tasks[0] if tasks else None
