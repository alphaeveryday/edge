"""격리 검증 DAG(edge_investor_intraday_verify) — 실제 AWS 검증이 **운영 DAG 와 같은 경로**를 타면서 업무 자원에
닿지 않는지, 그리고 주입한 장애가 EdgeStep 의 판정을 바꾸지 않고 상황만 만드는지 고정한다(ALPHA-1119)."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

from test_investor_intraday_dag import NOW, FakeEcs, _context

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dags"))

VERIFY_ENV = {
    "EDGE_VERIFY_CLUSTER": "arn:aws:ecs:ap-northeast-2:1:cluster/edge-dev-airflow",
    "EDGE_VERIFY_TASKDEF_PREFIX": "edge-dev-airflow-verify",
    "EDGE_VERIFY_SECURITY_GROUPS": "sg-verify",
    "EDGE_VERIFY_LOG_GROUP": "/ecs/edge-dev-airflow-verify",
}
STEPS = ("plan", "collect", "normalize", "load", "report")


def _bag(monkeypatch, env):
    from airflow.dag_processing.dagbag import DagBag
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        return DagBag(dag_folder=str(ROOT / "dags"))
    finally:
        socket.socket.connect = original


def test_verify_dag_runs_the_same_graph_against_isolated_resources_only(monkeypatch):
    import edge_batch
    bag = _bag(monkeypatch, VERIFY_ENV)
    assert bag.import_errors == {}
    prod, verify = bag.dags["edge_investor_intraday"], bag.dags["edge_investor_intraday_verify"]
    # 각 DAG 는 자기 파일에서만 등록된다(검증 파일의 import 가 운영 DAG 를 끌고 오지 않는다).
    assert prod.fileloc.endswith("/edge_investor_intraday.py") and verify.fileloc.endswith("_verify.py")
    # 같은 그래프 — 검증이 운영과 다른 경로를 타면 검증 결과가 운영을 말해 주지 않는다.
    assert {t: sorted(prod.get_task(t).downstream_task_ids) for t in prod.task_ids} == \
           {t: sorted(verify.get_task(t).downstream_task_ids) for t in verify.task_ids}
    for step in STEPS:
        v, p = verify.get_task(step), prod.get_task(step)
        assert type(v).__name__ == "VerifyStep" and type(p).__name__ == "EdgeStep"
        assert v.overrides["containerOverrides"][0]["command"] == p.overrides["containerOverrides"][0]["command"]
        # 업무 자원에 닿지 않는다: 클러스터·태스크 정의·보안그룹·로그 그룹이 모두 검증용.
        assert v.cluster == VERIFY_ENV["EDGE_VERIFY_CLUSTER"]
        assert v.task_definition.startswith("edge-dev-airflow-verify-")
        assert v.network_configuration["awsvpcConfiguration"]["securityGroups"] == ["sg-verify"]
        assert v.awslogs_group == "/ecs/edge-dev-airflow-verify"
        # 운영 DAG 는 그대로 배포 환경값을 쓴다.
        assert p.cluster == edge_batch.CLUSTER and p.task_definition.startswith(edge_batch.TASKDEF_PREFIX + "-")
    report_env = {e["name"]: e["value"] for e in verify.get_task("report").overrides["containerOverrides"][0]["environment"]}
    assert report_env["OPS_CLUSTER_ARN"] == VERIFY_ENV["EDGE_VERIFY_CLUSTER"]   # 검증 원장의 대조도 검증 클러스터
    # 수동 trigger 만, 알림 없음(운영 SNS 로 검증 실패가 가지 않게), 시간 초과는 sweep 수명(1200초)보다 짧다.
    assert type(verify.timetable).__name__ == "NullTimetable"
    assert not verify.on_failure_callback
    assert verify.dagrun_timeout.total_seconds() == 900


def test_verify_dag_absent_without_env_and_refuses_the_business_cluster(monkeypatch):
    for k in VERIFY_ENV:
        monkeypatch.delenv(k, raising=False)
    assert sorted(_bag(monkeypatch, {}).dag_ids) == ["edge_investor_intraday", "edge_source_daily"]
    import edge_batch
    monkeypatch.setattr(edge_batch, "CLUSTER", VERIFY_ENV["EDGE_VERIFY_CLUSTER"])
    bag = _bag(monkeypatch, VERIFY_ENV)
    assert "edge_investor_intraday_verify" not in bag.dag_ids
    assert any("업무 클러스터" in str(e) for e in bag.import_errors.values())


def _verify_step(ecs, faults):
    from edge_batch import _IdempotentRunTask
    from edge_investor_intraday_verify import VerifyStep, _FaultyClient
    op = VerifyStep(task_id="t", taskdef_key="bigkinds", command=["normalize-investor-estimate"],
                    cluster="c", taskdef_prefix="p")
    op.PAUSE_SECONDS = 0
    op.__dict__["client"] = _FaultyClient(_IdempotentRunTask(ecs, op._client_token), op)
    return op, _context(NOW, conf={"faults": {"t": faults}})


def test_injected_response_loss_is_tracked_without_a_second_task():
    # RunTask 는 실제로 태스크를 만들고 응답만 잃는다 — EdgeStep 이 다시 제출하지 않고 그 태스크에 붙어야 한다.
    ecs = FakeEcs(on_run=[{"exit": 0}])
    op, ctx = _verify_step(ecs, {"runtask_response_lost": [1]})
    op.execute(ctx)
    assert len(ecs.run_calls) == 1 and ctx["ti"].pushed["exit_code"] == 0


def test_injected_lookup_failure_after_lost_response_holds():
    # 응답을 잃고 추적 조회도 실패 — 새 태스크를 띄우지 않고 보류(ECS_STATE_UNKNOWN).
    from edge_batch import HoldExecution
    ecs = FakeEcs(on_run=[{"exit": 0}])
    op, ctx = _verify_step(ecs, {"runtask_response_lost": [1], "list_tasks_error_after_submit": [1]})
    with pytest.raises(HoldExecution) as held:
        op.execute(ctx)
    assert held.value.kind == "ECS_STATE_UNKNOWN" and len(ecs.run_calls) == 1


def test_faults_apply_only_to_their_try_and_container_fault_goes_by_env():
    ecs = FakeEcs(on_run=[{"exit": 0}])
    op, ctx = _verify_step(ecs, {"list_tasks_error": [2], "container": {"1": {"sleep_in_step": 5}}})
    op.execute(ctx)                                 # try 1: 조회 장애 없음 → 정상 제출
    env = op.overrides["containerOverrides"][0]["environment"]
    assert {"name": "VERIFY_FAULT", "value": '{"sleep_in_step": 5}'} in env
    assert len(ecs.run_calls) == 1
