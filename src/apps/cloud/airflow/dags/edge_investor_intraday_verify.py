"""장중 수급 DAG 의 격리 검증판 — 실제 AWS 에서 EdgeStep 의 제출·추적·보류 경로를 확인한다(ALPHA-1119).

운영 DAG 와 **같은 그래프·같은 EdgeStep**(`edge_investor_intraday.build_dag`)이다. 다른 것은 셋뿐이다.
- 대상: 격리 검증 클러스터·태스크 정의·보안그룹·로그 그룹(`EDGE_VERIFY_*`, terraform modules/airflow/verify.tf).
  검증 태스크는 배포된 업무 이미지 + shim 으로 돈다 — 원장은 검증 전용 DB, 레이크는 검증 전용 버킷,
  수집은 저장된 응답 재생(KIS 호출 없음).
- 일정 없음(수동 trigger 만), SNS 통보 없음, dagrun_timeout 900초(시간 초과 경로를 25분 안에 재현).
- 장애 주입: dag_run.conf `faults` 로만 켠다. 없으면 운영 EdgeStep 과 똑같이 돈다.

장애 주입(conf 예) — 값은 그 장애를 걸 try 번호 목록:
    {"faults": {"collect": {"runtask_response_lost": [1],          # RunTask 는 성공, 응답만 버린다
                            "list_tasks_error": [2],                 # 그 try 의 ListTasks 전부 실패
                            "list_tasks_error_after_submit": [1],    # 제출 뒤 추적 조회만 실패
                            "container": {"1": {"sleep_in_step": 300}}}}}   # 컨테이너 shim 장애(env)
여기서 확인하는 것은 "모의 장애 주입"이다 — 실제 AWS 에서 관측한 것(ECS 태스크 수·exitCode·stopCode·원장
행)과 구분해 기록한다(README "실제 AWS 검증").

`EDGE_VERIFY_CLUSTER` 가 없으면 이 DAG 는 등록되지 않는다(검증 자원이 없는 환경). 업무 클러스터와 같으면
파싱에서 실패한다 — 검증 태스크가 업무 Reconciler 의 sweep 대상(worker 클러스터)에 섞이면 안 된다.
"""

from __future__ import annotations

import json
import os
from functools import cached_property

from botocore.exceptions import ClientError

from edge_batch import CLUSTER, EdgeStep, _IdempotentRunTask
from edge_investor_intraday import build_dag

VERIFY_CLUSTER = os.environ.get("EDGE_VERIFY_CLUSTER", "")
FAULTS = ("runtask_response_lost", "list_tasks_error", "list_tasks_error_after_submit")


class _FaultyClient:
    """ECS 클라이언트 앞에서 주입한 장애만 일으킨다. 나머지는 실제 AWS 호출 그대로."""

    def __init__(self, inner, step: "VerifyStep"):
        self._inner = inner
        self._step = step

    def _armed(self, fault: str) -> bool:
        return self._step._try_number in self._step.faults.get(fault, ())

    def run_task(self, **kwargs):
        response = self._inner.run_task(**kwargs)
        self._step._submitted = True
        if self._armed("runtask_response_lost"):
            # 태스크는 실제로 만들어졌다 — 응답만 잃은 상황(연결 끊김)을 흉내 낸다.
            raise ConnectionError("verify: RunTask 응답 유실 주입(태스크는 생성됨)")
        return response

    def list_tasks(self, **kwargs):
        if self._armed("list_tasks_error") or (self._step._submitted and self._armed("list_tasks_error_after_submit")):
            raise ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "verify: 주입"},
                               "ResponseMetadata": {"HTTPStatusCode": 503}}, "ListTasks")
        return self._inner.list_tasks(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class VerifyStep(EdgeStep):
    """검증 전용 EdgeStep — conf 의 장애 주입만 더한다. 판정·보류 규칙은 EdgeStep 그대로다."""

    def _execute(self, context):
        spec = ((context["dag_run"].conf or {}).get("faults") or {}).get(self.task_id) or {}
        self.faults = {k: tuple(spec.get(k) or ()) for k in FAULTS}
        self._submitted = False
        container = (spec.get("container") or {}).get(str(context["ti"].try_number))
        if container:
            # 컨테이너 쪽 장애(verify/shim.py) — 이 try 가 띄우는 컨테이너에만 간다.
            self.overrides["containerOverrides"][0]["environment"].append(
                {"name": "VERIFY_FAULT", "value": json.dumps(container)})
        return super()._execute(context)

    @cached_property
    def client(self):
        return _FaultyClient(_IdempotentRunTask(self.hook.conn, self._client_token), self)


if VERIFY_CLUSTER:
    if VERIFY_CLUSTER == CLUSTER:
        raise RuntimeError("EDGE_VERIFY_CLUSTER 가 업무 클러스터와 같다 — 검증 태스크를 업무 클러스터에 띄우지 않는다")
    dag = build_dag(
        "edge_investor_intraday_verify", schedule=None, step=VerifyStep, notify=False,
        dagrun_timeout_seconds=900,
        ecs_target={"cluster": VERIFY_CLUSTER,
                    "taskdef_prefix": os.environ["EDGE_VERIFY_TASKDEF_PREFIX"],
                    "security_groups": [s for s in os.environ["EDGE_VERIFY_SECURITY_GROUPS"].split(",") if s],
                    "log_group": os.environ.get("EDGE_VERIFY_LOG_GROUP") or None},
    )
