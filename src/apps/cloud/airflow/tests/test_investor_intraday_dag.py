"""장중 수급 DAG 계약 테스트 — Airflow 3.3.2 이미지 안에서 돈다(README 실행법).

SFN 경로와 갈라지면 안 되는 것(슬롯·명령·run_id)을 terraform 원본과 대조하고, 실행 관리가 업무
의미를 바꾸는 지점(exit code 해석·당일 수집·재처리)을 고정한다.
"""

from __future__ import annotations

import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT.parents[3] / "infra/terraform/modules/data-pipeline"
sys.path.insert(0, str(ROOT / "dags"))


@pytest.fixture(scope="module")
def dag_module():
    # 파싱은 외부 호출을 하면 안 된다 — import 동안 소켓 연결을 막는다.
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        import edge_investor_intraday as module
    finally:
        socket.socket.connect = original
    return module


def test_pipeline_run_id_matches_production_run_ids():
    # 실제 dev 레이크 raw 경로의 run_id(2026-09-22 슬롯)와 같아야 원장·레이크가 같은 런을 가리킨다.
    from edge_batch import pipeline_run_id
    kst = timezone(timedelta(hours=9))
    assert pipeline_run_id("investor-intraday", datetime(2026, 9, 22, 9, 35, tzinfo=kst)) \
        == "run_c52a16e3d7f98774b0fe384f5d"
    assert pipeline_run_id("investor-intraday", datetime(2026, 9, 22, 5, 35, tzinfo=timezone.utc)) \
        == "run_2eed4fa22525b9d4caa51ea0d6"


def test_slots_match_terraform_schedule(dag_module):
    tf = (TF / "variables.tf").read_text()
    block = tf[tf.index('variable "investor_intraday_schedule_expressions"'):]
    block = block[:block.index("\n}\n")]
    crons = re.findall(r'cron\((\d+) (\d+) \? \* MON-FRI \*\)', block)
    assert sorted(f"{m} {h} * * 1-5" for m, h in crons) == sorted(dag_module.CRONS)


def test_commands_match_sfn_definition(dag_module):
    tf = (TF / "statemachine.tf").read_text()
    for task_id, state in (("collect", "CollectKisInvestorEstimate"),
                           ("normalize", "NormalizeInvestorEstimate"),
                           ("load", "LoadInvestorIntraday")):
        expr = re.search(rf'state\s+= "{state}"\s+taskdef_key\s+= "(\w+)"\s+command_expr = "States\.Array\((.*)\)"',
                         tf).groups()
        taskdef, args = expr[0], [a.strip().strip("'") for a in expr[1].split(",")]
        op = dag_module.dag.get_task(task_id)
        assert op.task_definition.endswith(f"-{taskdef}")
        command = op.overrides["containerOverrides"][0]["command"]
        assert [("$.run_id" if "edge_run_id" in c else c) for c in command] == args


def test_scheduling_guards(dag_module):
    dag = dag_module.dag
    assert dag.catchup is False                       # 활성화 시 과거 슬롯 소급 금지
    assert dag.max_active_runs == 1                   # 같은 거래일 파티션 병합 직렬화
    assert dag.dagrun_timeout == timedelta(seconds=1500)
    assert str(dag.timetable.__class__.__name__) == "MultipleCronTriggerTimetable"
    assert dag.get_task("normalize").trigger_rule == "all_done_min_one_success"   # raw 실패여도 정제
    assert dag.get_task("verdict").trigger_rule == "all_done"


class _Ti:
    def __init__(self):
        self.pushed = {}

    def xcom_push(self, key, value):
        self.pushed[key] = value


def _context(slot, conf=None):
    return {"ti": _Ti(), "logical_date": slot, "dag_run": SimpleNamespace(conf=conf or {}, run_after=slot)}


def _step(monkeypatch, *, exit_code, raises, error=None, **kwargs):
    from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator
    from airflow.sdk.exceptions import AirflowException
    from edge_batch import EdgeStep
    op = EdgeStep(task_id="t", taskdef_key="bigkinds", command=["normalize-investor-estimate"], **kwargs)
    op.arn = "arn:task"
    op.__dict__["client"] = SimpleNamespace(describe_tasks=lambda **_: {"tasks": [
        {"lastStatus": "STOPPED", "containers": [{"exitCode": exit_code}]}]})

    def fake_execute(self, context):
        if raises:
            raise (error or AirflowException("container not in success state"))
    monkeypatch.setattr(EcsRunTaskOperator, "execute", fake_execute)
    return op


NOW = datetime.now(timezone.utc)


def test_partial_exit_lets_downstream_continue(monkeypatch):
    op = _step(monkeypatch, exit_code=2, raises=True, partial_exit_codes=(2,))
    ctx = _context(NOW)
    op.execute(ctx)
    assert ctx["ti"].pushed["exit_code"] == 2


def test_business_failure_is_not_retried_by_airflow(monkeypatch):
    from airflow.sdk.exceptions import AirflowFailException
    op = _step(monkeypatch, exit_code=1, raises=True, partial_exit_codes=(2,))
    with pytest.raises(AirflowFailException):
        op.execute(_context(NOW))


def test_unknown_exit_is_retryable_infra_failure(monkeypatch):
    from airflow.sdk.exceptions import AirflowException, AirflowFailException
    op = _step(monkeypatch, exit_code=None, raises=True)
    with pytest.raises(AirflowException) as err:
        op.execute(_context(NOW))
    assert not isinstance(err.value, AirflowFailException)


def test_duplicate_guards_by_step_kind(monkeypatch):
    # 외부 호출이 있는 수집만 성공 이력 skip. 정제·적재는 실행권만 — 다른 슬롯이 파티션을 갱신한 뒤의
    # 복구는 정제를 다시 돌려야만 되기 때문이다.
    collect = _step(monkeypatch, exit_code=0, raises=False, skip_if_succeeded=True)
    collect.execute(_context(NOW))
    env = collect.overrides["containerOverrides"][0]["environment"]
    assert {"name": "OPS_EXCLUSIVE_STEP", "value": "1"} in env
    assert {"name": "OPS_SKIP_IF_SUCCEEDED", "value": "1"} in env
    normalize = _step(monkeypatch, exit_code=0, raises=False)
    normalize.execute(_context(NOW))
    names = [e["name"] for e in normalize.overrides["containerOverrides"][0]["environment"]]
    assert "OPS_EXCLUSIVE_STEP" in names and "OPS_SKIP_IF_SUCCEEDED" not in names


def test_normal_return_without_exit_code_is_retried(monkeypatch):
    # provider 는 컨테이너 정보가 빈 응답엔 예외 없이 반환한다 — exit 를 못 봤으면 성공이 아니다.
    from airflow.sdk.exceptions import AirflowException, AirflowFailException
    op = _step(monkeypatch, exit_code=None, raises=False)
    with pytest.raises(AirflowException) as err:
        op.execute(_context(NOW))
    assert not isinstance(err.value, AirflowFailException)


def test_non_airflow_exception_is_still_judged_by_exit_code(monkeypatch):
    # 대기 중 WaiterError 등 — 컨테이너가 exit 2 로 끝났으면 부분 성공으로 하류를 진행한다.
    op = _step(monkeypatch, exit_code=2, raises=True, error=RuntimeError("waiter"),
               partial_exit_codes=(2,))
    ctx = _context(NOW)
    assert op.execute(ctx) is None and ctx["ti"].pushed["exit_code"] == 2


def test_steps_never_defer_and_plan_marks_reprocess(dag_module):
    # defer 하면 재개가 provider execute_complete 로 가서 exit 판정·XCom 을 건너뛴다.
    for task_id in ("plan", "collect", "normalize", "load"):
        assert dag_module.dag.get_task(task_id).deferrable is False
    assert dag_module.dag.get_task("plan").reprocess_env == {"OPS_REPROCESS": "1"}
    assert dag_module.dag.get_task("collect").skip_if_succeeded is True
    assert dag_module.dag.get_task("normalize").skip_if_succeeded is False


def test_step_not_run_is_retried_not_failed(monkeypatch):
    # 75 = 실행권·원장 확인 불가로 업무를 실행하지 않았다. 업무 실패(재시도 없음)로 닫으면 안 된다.
    from airflow.sdk.exceptions import AirflowException, AirflowFailException
    op = _step(monkeypatch, exit_code=75, raises=True, partial_exit_codes=(2,))
    with pytest.raises(AirflowException) as err:
        op.execute(_context(NOW))
    assert not isinstance(err.value, AirflowFailException)


def test_past_slot_collection_is_refused_and_reprocess_does_not_collect(monkeypatch):
    from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator
    from airflow.sdk.exceptions import AirflowFailException
    past = NOW - timedelta(days=3)
    op = _step(monkeypatch, exit_code=0, raises=False, same_day_only=True, noop_on_reprocess=True)
    with pytest.raises(AirflowFailException, match="소급 수집 불가"):
        op.execute(_context(past))
    # 재처리 run: skip 이면 하류가 전파로 멈춘다 → 성공 no-op, ECS 실행 없음.
    launched = []
    monkeypatch.setattr(EcsRunTaskOperator, "execute", lambda self, ctx: launched.append(1))
    ctx = _context(NOW, conf={"reprocess_slot": past.isoformat()})
    assert op.execute(ctx) is None and launched == [] and "exit_code" not in ctx["ti"].pushed


def test_reprocess_runs_without_duplicate_guard(monkeypatch):
    op = _step(monkeypatch, exit_code=0, raises=False)
    op.execute(_context(NOW, conf={"reprocess_slot": (NOW - timedelta(days=3)).isoformat()}))
    names = [e["name"] for e in op.overrides["containerOverrides"][0]["environment"]]
    assert "OPS_SKIP_IF_SUCCEEDED" not in names and "OPS_EXCLUSIVE_STEP" in names


def test_exit_zero_after_provider_exception_is_success(monkeypatch):
    # provider 가 대기·로그 경로에서 예외를 냈어도 컨테이너가 exit 0 이면 업무 성공이다.
    op = _step(monkeypatch, exit_code=0, raises=True, partial_exit_codes=(2,))
    ctx = _context(NOW)
    assert op.execute(ctx) is None and ctx["ti"].pushed["exit_code"] == 0


def test_report_precedes_verdict_so_the_leaf_decides_the_run(dag_module):
    # DAG run 상태는 leaf task 가 정한다 — 보고(reconcile) 성공이 런 실패를 가리면 안 된다.
    dag = dag_module.dag
    assert [t.task_id for t in dag.leaves] == ["verdict"]
    report = dag.get_task("report")
    assert report.trigger_rule == "all_done" and "verdict" in report.downstream_task_ids
    env = {e["name"]: e["value"] for e in report.overrides["containerOverrides"][0]["environment"]}
    assert env["OPS_RUN_KEY"] == "{{ edge_run_key(logical_date, dag_run) }}"
    assert env["OPS_ORCHESTRATION_STATUS"] == "{{ edge_run_status(ti, dag_run) }}"


def test_run_status_matches_verdict_semantics():
    from edge_batch import run_status
    assert run_status({"collect": 0, "normalize": 0, "load": 0}) == "SUCCEEDED"
    assert run_status({"collect": 0, "normalize": 2, "load": 0}) == "FAILED"     # 부분 성공은 런 실패
    assert run_status({"collect": None, "normalize": 0, "load": 0}) == "FAILED"  # 미실행·미확인
    assert run_status({}) == "FAILED"


def test_log_prefix_follows_task_definition(monkeypatch):
    monkeypatch.setattr("edge_batch.LOG_GROUP", "/ecs/x")
    from edge_batch import EdgeStep
    assert EdgeStep(task_id="p", taskdef_key="ops", command=["plan-run"]).awslogs_stream_prefix \
        == "ops/data-pipeline"
    assert EdgeStep(task_id="c", taskdef_key="kis", command=["x"]).awslogs_stream_prefix \
        == "raw-ingest/data-pipeline"


def test_verdict_fails_when_the_status_report_did_not_land(dag_module):
    # 업무 3스텝이 0 이어도 원장 보고(report)가 실패하면 런을 성공으로 닫지 않는다.
    from airflow.sdk.exceptions import AirflowFailException
    verdict = dag_module.dag.get_task("verdict").python_callable

    class Ti:
        def __init__(self, report):
            self.codes = {"collect": 0, "normalize": 0, "load": 0, "report": report}

        def xcom_pull(self, task_ids, key):
            return self.codes[task_ids]

    run = SimpleNamespace(conf={})
    assert verdict(ti=Ti(0), dag_run=run) == {"collect": 0, "normalize": 0, "load": 0}
    for report in (None, 1, 75):
        with pytest.raises(AirflowFailException, match="판정 보고 실패"):
            verdict(ti=Ti(report), dag_run=run)
