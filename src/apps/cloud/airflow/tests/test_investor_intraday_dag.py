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


def test_dagbag_parses_the_folder_like_the_dag_processor():
    # 모듈 import 가 아니라 실제 Airflow DagBag 으로 폴더를 파싱한다 — scheduler 가 보는 것과 같은 경로.
    from airflow.dag_processing.dagbag import DagBag
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        bag = DagBag(dag_folder=str(ROOT / "dags"))
    finally:
        socket.socket.connect = original
    assert bag.import_errors == {}
    assert sorted(bag.dag_ids) == ["edge_investor_intraday"]


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
    def __init__(self, try_number=1, max_tries=2, pulled=None):
        self.pushed = {}
        self.dag_id, self.task_id, self.run_id, self.map_index = "edge_investor_intraday", "t", "r", -1
        self.try_number, self.max_tries = try_number, max_tries
        self.pulled = pulled or {}

    def xcom_push(self, key, value):
        self.pushed[key] = value

    def xcom_pull(self, task_ids, key):
        return self.pulled.get((task_ids, key))


def _context(slot, conf=None, try_number=1):
    return {"ti": _Ti(try_number), "logical_date": slot,
            "dag_run": SimpleNamespace(conf=conf or {}, run_after=slot)}


def _started_by():
    from airflow.providers.amazon.aws.utils.identifiers import generate_uuid
    return generate_uuid("edge_investor_intraday", "t", "r", "-1")


def _task(arn, *, exit_code=None, stop_code="EssentialContainerExited", last="STOPPED", created=0):
    container = {"name": "data-pipeline", "lastStatus": last}
    if exit_code is not None:
        container["exitCode"] = exit_code
    task = {"taskArn": arn, "startedBy": _started_by(), "lastStatus": last, "createdAt": created,
            "desiredStatus": "STOPPED" if last == "STOPPED" else "RUNNING", "containers": [container]}
    if last == "STOPPED":
        task["stopCode"] = stop_code
    return task


class FakeEcs:
    """ECS 클라이언트 대역 — 실제 provider execute 가 부른다. 제출(run_task)을 세고 결과를 주입한다.

    on_run: 제출 1회의 결과 목록(차례로 소모) — {"exit": n, "stop": code} 태스크 생성 / "refuse" 배치 거부 확정
    (failures) / "lost" 생성 뒤 응답 유실 / "lost_invisible" 응답 유실 + 조회에 안 보임 / ClientError 인스턴스."""

    def __init__(self, tasks=(), on_run=(), list_errors=0):
        self.tasks = {t["taskArn"]: t for t in tasks}
        self.hidden: set[str] = set()
        self.on_run = list(on_run)
        self.list_errors = list_errors
        self.run_calls: list[dict] = []

    def list_tasks(self, cluster, startedBy, desiredStatus):
        if self.list_errors:
            self.list_errors -= 1
            raise OSError("ecs list unavailable")
        return {"taskArns": [a for a, t in self.tasks.items() if a not in self.hidden
                             and t["startedBy"] == startedBy and t["desiredStatus"] == desiredStatus]}

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [self.tasks[a] for a in tasks if a in self.tasks],
                "failures": [{"arn": a, "reason": "MISSING"} for a in tasks if a not in self.tasks]}

    def run_task(self, **kwargs):
        self.run_calls.append(kwargs)
        action = self.on_run.pop(0)
        if isinstance(action, Exception):
            raise action
        if action == "refuse":
            return {"tasks": [], "failures": [{"reason": "RESOURCE:MEMORY"}]}
        if action == "throttled":           # 마지막 재전송만 거부 — 태스크는 없다
            raise _throttle()
        arn = f"arn:new{len(self.run_calls)}"
        spec = {"exit": 0} if isinstance(action, str) else action
        task = _task(arn, exit_code=spec.get("exit"), stop_code=spec.get("stop", "EssentialContainerExited"),
                     created=100 + len(self.run_calls))
        task["startedBy"] = kwargs["startedBy"]
        self.tasks[arn] = task
        if action == "lost_invisible":
            self.hidden.add(arn)
        if action in ("lost", "lost_invisible"):
            raise ConnectionError("read timeout after RunTask")
        if action == "throttled_after_create":    # 앞선 재전송이 만들고 마지막 응답만 Throttling
            raise _throttle()
        return {"tasks": [task], "failures": []}

    def get_waiter(self, name):
        return SimpleNamespace(wait=lambda **_: None)


def _throttle():
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": "ThrottlingException"},
                        "ResponseMetadata": {"HTTPStatusCode": 400}}, "RunTask")


def _op(ecs, **kwargs):
    from edge_batch import EdgeStep, _IdempotentRunTask
    op = EdgeStep(task_id="t", taskdef_key="bigkinds", command=["normalize-investor-estimate"], **kwargs)
    op.PAUSE_SECONDS = 0
    op.__dict__["client"] = _IdempotentRunTask(ecs, op._client_token)
    return op


def _step(monkeypatch, *, exit_code, raises=None, error=None, **kwargs):
    """첫 시도가 새 태스크 하나를 띄워 exit_code 로 끝나는 경우(실제 provider 경로)."""
    return _op(FakeEcs(on_run=[{"exit": exit_code}]), **kwargs)


NOW = datetime.now(timezone.utc)


def test_partial_exit_lets_downstream_continue(monkeypatch):
    op = _step(monkeypatch, exit_code=2, partial_exit_codes=(2,))
    ctx = _context(NOW)
    op.execute(ctx)
    assert ctx["ti"].pushed["exit_code"] == 2


def test_business_failure_is_not_retried_by_airflow(monkeypatch):
    from airflow.sdk.exceptions import AirflowFailException
    op = _step(monkeypatch, exit_code=1, partial_exit_codes=(2,))
    with pytest.raises(AirflowFailException):
        op.execute(_context(NOW))


def test_duplicate_guards_by_step_kind(monkeypatch):
    # 외부 호출이 있는 수집만 성공 이력 skip. 정제·적재는 실행권만 — 다른 슬롯이 파티션을 갱신한 뒤의
    # 복구는 정제를 다시 돌려야만 되기 때문이다.
    collect = _step(monkeypatch, exit_code=0, skip_if_succeeded=True)
    collect.execute(_context(NOW))
    env = collect.overrides["containerOverrides"][0]["environment"]
    assert {"name": "OPS_EXCLUSIVE_STEP", "value": "1"} in env
    assert {"name": "OPS_SKIP_IF_SUCCEEDED", "value": "1"} in env
    normalize = _step(monkeypatch, exit_code=0)
    normalize.execute(_context(NOW))
    names = [e["name"] for e in normalize.overrides["containerOverrides"][0]["environment"]]
    assert "OPS_EXCLUSIVE_STEP" in names and "OPS_SKIP_IF_SUCCEEDED" not in names


def test_steps_never_defer_and_plan_marks_reprocess(dag_module):
    # defer 하면 재개가 provider execute_complete 로 가서 exit 판정·XCom 을 건너뛴다.
    for task_id in ("plan", "collect", "normalize", "load"):
        assert dag_module.dag.get_task(task_id).deferrable is False
    assert dag_module.dag.get_task("plan").reprocess_env == {"OPS_REPROCESS": "1"}
    assert dag_module.dag.get_task("collect").skip_if_succeeded is True
    assert dag_module.dag.get_task("normalize").skip_if_succeeded is False


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
            return self.codes[task_ids] if key == "exit_code" else None

    run = SimpleNamespace(conf={})
    assert verdict(ti=Ti(0), dag_run=run) == {"collect": 0, "normalize": 0, "load": 0}
    for report in (None, 1, 75):
        with pytest.raises(AirflowFailException, match="판정 보고 실패"):
            verdict(ti=Ti(report), dag_run=run)


def test_every_step_passes_its_airflow_attempt_ref(dag_module):
    # 원장 attempt → Airflow 시도 역추적의 유일한 연결이다.
    for task_id in ("plan", "collect", "normalize", "load", "report"):
        env = {e["name"]: e["value"] for e in
               dag_module.dag.get_task(task_id).overrides["containerOverrides"][0]["environment"]}
        assert env["OPS_ORCHESTRATOR_ATTEMPT_REF"] == (
            "airflow:{{ dag.dag_id }}/{{ run_id }}/{{ task.task_id }}/{{ ti.try_number }}")


def test_status_is_not_reported_when_plan_did_not_succeed(dag_module):
    # 계획이 실패(충돌·재처리 불가·인프라)한 run 은 업무를 안 건드렸다 — 슬롯의 기존 판정을 덮지 않는다.
    class Ti:
        def __init__(self, plan):
            self.codes = {"plan": plan, "collect": 0, "normalize": 0, "load": 0}

        def xcom_pull(self, task_ids, key):
            return self.codes.get(task_ids)

    run = SimpleNamespace(conf={})
    assert dag_module._status(Ti(None), run) == "" and dag_module._status(Ti(1), run) == ""
    assert dag_module._status(Ti(0), run) == "SUCCEEDED"


@pytest.mark.parametrize("now_kst, expected_kst", [
    ("2026-09-28T11:45:00", "2026-09-28T13:25:00"),   # 11:25 슬롯 20분 뒤 활성화 — 지난 슬롯을 돌리지 않는다
    ("2026-09-28T15:00:00", "2026-09-29T09:35:00"),   # 전환 절차의 장 마감 뒤 활성화 — 14:35 재실행 없음
    ("2026-09-27T04:00:00", "2026-09-28T09:35:00"),   # 일요일 활성화
])
def test_activation_does_not_run_an_already_passed_slot(dag_module, monkeypatch, now_kst, expected_kst):
    # 실제 Airflow timetable 로 "처음 켰을 때 다음 run" 을 계산한다(catchup=False, 이전 run 없음).
    import airflow.timetables.trigger as trigger
    from airflow.timetables.base import TimeRestriction
    kst = timezone(timedelta(hours=9))
    now = datetime.fromisoformat(now_kst).replace(tzinfo=kst).astimezone(timezone.utc)
    import pendulum
    monkeypatch.setattr(trigger, "utcnow", lambda: pendulum.instance(now))
    # MultipleCron 의 no-catchup 정렬 키는 utcnow 가 아니라 time.time() 을 쓴다 — 둘 다 고정한다.
    monkeypatch.setattr(trigger.time, "time", lambda: now.timestamp())
    info = dag_module.dag.timetable.next_dagrun_info(
        last_automated_data_interval=None,
        restriction=TimeRestriction(earliest=None, latest=None, catchup=False))
    assert info.logical_date.astimezone(kst).strftime("%Y-%m-%dT%H:%M:%S") == expected_kst


# ── 재시도·재접속·보류 정책(초기 운영): 앞선 시도의 결말을 확인한 뒤에만 새 ECS 태스크를 띄운다 ──
# 보는 것: 결과 판정뿐 아니라 **run_task 호출 수**(= 새로 뜬 컨테이너 수 = 업무 실행 기회)다.

@pytest.mark.parametrize("task, expected", [
    (_task("a", last="RUNNING"), "ALIVE"),
    ({**_task("a", last="RUNNING"), "desiredStatus": "STOPPED"}, "ALIVE"),        # StopTask 요청 뒤 종료 중
    (_task("a", exit_code=0), "SUCCESS"),
    (_task("a", exit_code=0, stop_code="UserInitiated"), "SUCCESS"),              # 끝까지 간 뒤에만 0
    (_task("a", exit_code=2), "PARTIAL"),
    (_task("a", exit_code=1), "FAILED"),
    (_task("a", exit_code=75), "NOT_RUN"),
    (_task("a", exit_code=76), "HELD"),
    (_task("a", stop_code="TaskFailedToStart"), "NOT_RUN"),
    (_task("a"), "RESULT_UNKNOWN"),                                              # STOPPED 인데 exit 없음
    (_task("a", exit_code=143, stop_code="UserInitiated"), "RESULT_UNKNOWN"),    # 외부 종료 — 업무 도중일 수 있다
    (_task("a", exit_code=1, stop_code="SpotInterruption"), "RESULT_UNKNOWN"),
    (_task("a", exit_code=75, stop_code="UserInitiated"), "RESULT_UNKNOWN"),
])
def test_ecs_verdict_never_folds_unknown_into_success_or_not_run(task, expected):
    from edge_batch import ecs_verdict
    assert ecs_verdict(task, (2,)) == expected


@pytest.mark.parametrize("prior, collect, request_kind, expected_calls", [
    ([_task("arn:old", exit_code=75)], False, "retry", 1),                  # 업무 미시작 확인 → 새로 띄움
    ([_task("arn:old", stop_code="TaskFailedToStart")], False, "retry", 1),
    ([_task("arn:old", exit_code=76)], False, "clear", 1),  # 컨테이너 보류 — 새 컨테이너가 원장 게이트를 다시 거친다
    # 자동 재시도(판정 조회 실패·대기 오류·worker 중단 뒤)는 끝난 업무를 반복하지 않는다 — 실패도 재시도 안 한다는 계약.
    ([_task("arn:old", exit_code=1)], True, "retry", 0),
    ([_task("arn:old", exit_code=0)], False, "retry", 0),
    ([_task("arn:old", exit_code=2)], False, "retry", 0),
    ([_task("arn:1", exit_code=75, created=1), _task("arn:2", exit_code=0, created=2)], True, "retry", 0),
    # clear = 다시 돌리려는 요청. 수집 성공만 재사용(외부 재호출 없음). 정제·적재·보고는 새로 — 옛 결과를 쓰면 바뀐
    # 입력(재수집 raw·보고할 보류)이 반영되지 않는다(로컬 V3: report 가 앞 결과를 재사용해 보류를 못 옮겼다).
    ([_task("arn:old", exit_code=0)], True, "clear", 0),
    ([_task("arn:old", exit_code=0)], False, "clear", 1),
    ([_task("arn:old", exit_code=1)], True, "clear", 1),
])
def test_prior_finished_task_is_reused_on_retry_and_rerun_on_clear(prior, collect, request_kind, expected_calls):
    # retries=2(DAG 기본값). 자동 재시도: try 2·max_tries 2. clear 뒤 첫 시도: try 2·max_tries 3(= 1 + retries).
    ecs = FakeEcs(tasks=prior, on_run=[{"exit": 0}])
    ctx = _context(NOW, try_number=2)
    ctx["ti"].max_tries = 3 if request_kind == "clear" else 2
    op = _op(ecs, skip_if_succeeded=collect, retries=2, partial_exit_codes=(2,))
    try:
        op.execute(ctx)
    except Exception:
        pass                # 재사용한 실패는 실패로 끝난다 — 여기서는 새 태스크 수만 본다
    assert len(ecs.run_calls) == expected_calls


def test_retry_reattaches_to_a_running_task_instead_of_starting_another(monkeypatch):
    import airflow.sdk
    ecs = FakeEcs(tasks=[_task("arn:running", last="RUNNING")])
    ctx = _context(NOW, try_number=2)
    monkeypatch.setattr(airflow.sdk, "get_current_context", lambda: ctx)

    def finish(**_):
        ecs.tasks["arn:running"] = _task("arn:running", exit_code=0)
    ecs.get_waiter = lambda name: SimpleNamespace(wait=finish)
    assert _op(ecs).execute(ctx) is None
    assert ecs.run_calls == [] and ctx["ti"].pushed["ecs_reattached_arn"] == "arn:running"


@pytest.mark.parametrize("prior, try_number, kind", [
    ([_task("arn:old", exit_code=143, stop_code="UserInitiated")], 2, "RESULT_UNKNOWN"),   # 강제 종료
    ([_task("arn:old")], 2, "RESULT_UNKNOWN"),                                              # exit 없음
    ([], 2, "ECS_STATE_UNKNOWN"),               # 앞 시도가 있었는데 아무것도 안 보인다 ≠ 제출하지 않았다
    ([_task("arn:a", last="RUNNING"), _task("arn:b", last="RUNNING")], 1, "ECS_STATE_UNKNOWN"),
])
def test_unknown_previous_state_holds_without_starting_a_task(prior, try_number, kind):
    from edge_batch import HoldExecution
    ecs = FakeEcs(tasks=prior, on_run=[{"exit": 0}])
    ctx = _context(NOW, try_number=try_number)
    with pytest.raises(HoldExecution) as held:
        _op(ecs).execute(ctx)
    assert ecs.run_calls == [] and held.value.kind == kind
    assert ctx["ti"].pushed["hold"]["kind"] == kind          # report 가 원장에 옮길 재료


def test_hold_is_not_retried_by_airflow():
    from airflow.sdk.exceptions import AirflowFailException
    from edge_batch import HoldExecution
    assert issubclass(HoldExecution, AirflowFailException)


@pytest.mark.parametrize("list_errors, try_number, outcome", [
    (1, 1, "submitted"),     # 일시 오류는 조회만 다시 한다
    (3, 1, "failed"),        # 첫 시도 — 이 run 의 태스크는 있을 수 없다: 제출하지 않고 실패(작업 전체 보류 아님)
    (3, 2, "held"),          # 앞 시도가 있었다 — 모르는 채로 제출하지 않고 보류
])
def test_status_read_failure_never_submits_blind(list_errors, try_number, outcome):
    from airflow.sdk.exceptions import AirflowFailException
    from edge_batch import HoldExecution
    ecs = FakeEcs(on_run=[{"exit": 0}], list_errors=list_errors)
    ctx = _context(NOW, try_number=try_number)
    if outcome == "submitted":
        _op(ecs).execute(ctx)
        assert len(ecs.run_calls) == 1
        return
    with pytest.raises(AirflowFailException, match="조회 실패") as err:
        _op(ecs).execute(ctx)
    assert ecs.run_calls == []
    assert isinstance(err.value, HoldExecution) == (outcome == "held")
    assert ("hold" in ctx["ti"].pushed) == (outcome == "held")


def test_confirmed_placement_refusal_is_resubmitted_with_a_new_token():
    ecs = FakeEcs(on_run=["refuse", "refuse", {"exit": 0}])
    _op(ecs).execute(_context(NOW))
    tokens = [c["clientToken"] for c in ecs.run_calls]
    assert len(tokens) == 3 and len(set(tokens)) == 3 and all(0 < len(t) <= 64 for t in tokens)


def test_repeated_refusal_fails_without_hold():
    from airflow.sdk.exceptions import AirflowFailException
    from edge_batch import HoldExecution
    ecs = FakeEcs(on_run=["refuse"] * 3)
    with pytest.raises(AirflowFailException) as err:
        _op(ecs).execute(_context(NOW))
    assert not isinstance(err.value, HoldExecution) and len(ecs.run_calls) == 3


def test_lost_submit_response_is_tracked_not_resubmitted():
    ecs = FakeEcs(on_run=["lost"])
    ctx = _context(NOW)
    assert _op(ecs).execute(ctx) is None
    assert len(ecs.run_calls) == 1 and ctx["ti"].pushed["exit_code"] == 0


def test_lost_submit_response_that_cannot_be_traced_holds():
    from edge_batch import HoldExecution
    ecs = FakeEcs(on_run=["lost_invisible", {"exit": 0}])
    with pytest.raises(HoldExecution) as held:
        _op(ecs).execute(_context(NOW))
    assert held.value.kind == "ECS_STATE_UNKNOWN" and len(ecs.run_calls) == 1


def test_rejected_request_is_a_config_failure_not_a_hold():
    from botocore.exceptions import ClientError
    from airflow.sdk.exceptions import AirflowFailException
    from edge_batch import HoldExecution
    denied = ClientError({"Error": {"Code": "AccessDeniedException"},
                          "ResponseMetadata": {"HTTPStatusCode": 400}}, "RunTask")
    ecs = FakeEcs(on_run=[denied])
    with pytest.raises(AirflowFailException) as err:
        _op(ecs).execute(_context(NOW))
    assert not isinstance(err.value, HoldExecution) and len(ecs.run_calls) == 1


@pytest.mark.parametrize("finished, retryable", [
    ({"stop": "TaskFailedToStart"}, True),       # 기동 실패 — 업무 미시작, 다음 시도가 새로 띄운다
    ({"exit": 75}, True),                         # 실행권 대기 초과·원장 불가 — 업무 미시작
])
def test_not_started_work_is_retried(finished, retryable):
    from airflow.sdk.exceptions import AirflowException, AirflowFailException
    with pytest.raises(AirflowException) as err:
        _op(FakeEcs(on_run=[finished])).execute(_context(NOW))
    assert not isinstance(err.value, AirflowFailException)


@pytest.mark.parametrize("finished, kind", [
    ({"exit": 76}, "OPEN_ATTEMPT"),
    ({"exit": 143, "stop": "UserInitiated"}, "RESULT_UNKNOWN"),
])
def test_held_or_killed_container_holds(finished, kind):
    from edge_batch import HoldExecution
    with pytest.raises(HoldExecution) as held:
        _op(FakeEcs(on_run=[finished])).execute(_context(NOW))
    assert held.value.kind == kind


def test_provider_never_stops_a_running_business_task():
    # 대기 오류에 provider 기본값(stop_task_on_failure=True)이 도는 업무 컨테이너를 죽이면 결과 미상이 된다.
    from edge_batch import EdgeStep
    op = EdgeStep(task_id="n", taskdef_key="bigkinds", command=["x"])
    assert op.stop_task_on_failure is False and op.container_name == "data-pipeline" and op.reattach


def test_holds_reach_the_ledger_through_report(dag_module):
    report = dag_module.dag.get_task("report")
    env = {e["name"]: e["value"] for e in report.overrides["containerOverrides"][0]["environment"]}
    assert env["OPS_EXECUTION_HOLDS"] == "{{ edge_holds(ti) }}"

    class Ti:
        holds = {"collect": {"kind": "OPEN_ATTEMPT", "reason": "r"},     # 컨테이너가 이미 원장에 남겼다
                 "normalize": {"kind": "ECS_STATE_UNKNOWN", "reason": "r"},
                 "load": None}

        def xcom_pull(self, task_ids, key):
            return self.holds.get(task_ids) if key == "hold" else 0

    import json
    assert json.loads(dag_module._holds_env(Ti())) == {
        "NORMALIZE_INVESTOR_INTRADAY": {"kind": "ECS_STATE_UNKNOWN", "reason": "r"}}
    verdict = dag_module.dag.get_task("verdict").python_callable
    from airflow.sdk.exceptions import AirflowFailException
    with pytest.raises(AirflowFailException, match="실행 보류"):
        verdict(ti=Ti(), dag_run=SimpleNamespace(conf={}))


@pytest.mark.parametrize("action, outcome", [("throttled_after_create", "attached"), ("throttled", "held")])
def test_throttled_submit_is_tracked_not_resubmitted(action, outcome):
    # Throttling 은 마지막 재전송의 거부일 뿐이다 — 새 토큰으로 다시 내면 앞선 요청이 만든 태스크 옆에 하나 더 뜬다.
    from edge_batch import HoldExecution
    ecs = FakeEcs(on_run=[action, {"exit": 0}])
    if outcome == "attached":
        assert _op(ecs).execute(_context(NOW)) is None
    else:
        with pytest.raises(HoldExecution):
            _op(ecs).execute(_context(NOW))
    assert len(ecs.run_calls) == 1


def test_killing_the_airflow_task_does_not_stop_the_business_container():
    # run failed 표시·dagrun_timeout·worker 종료는 on_kill 을 부른다 — provider 기본은 StopTask 다.
    ecs = FakeEcs()
    stops = []
    ecs.stop_task = lambda **kw: stops.append(kw)
    op = _op(ecs)
    op.arn = "arn:running"
    op.on_kill()
    assert stops == []


@pytest.mark.parametrize("try_number, held", [(1, False), (3, True)])
def test_running_task_on_the_last_try_is_held_not_failed(try_number, held):
    # 대기가 계속 실패하는데 태스크는 돈다 — 마지막 시도에서 "실패"로 닫으면 끝난 것으로 읽힌다.
    from airflow.sdk.exceptions import AirflowException
    from edge_batch import HoldExecution
    ecs = FakeEcs(tasks=[_task("arn:running", last="RUNNING")])
    ecs.get_waiter = lambda name: SimpleNamespace(wait=lambda **_: (_ for _ in ()).throw(RuntimeError("waiter")))
    ctx = _context(NOW, try_number=try_number)
    with pytest.raises(AirflowException) as err:
        _op(ecs).execute(ctx)
    assert isinstance(err.value, HoldExecution) == held and ecs.run_calls == []


def test_step_after_a_held_upstream_does_not_start(dag_module, monkeypatch):
    # 정제는 수집이 부분 실패여도 돈다(trigger rule) — 그러나 수집이 보류면 그 태스크가 raw 를 아직 쓰고 있을 수 있다.
    from airflow.sdk.exceptions import AirflowSkipException
    from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator
    monkeypatch.setattr(EcsRunTaskOperator, "execute",
                        lambda self, ctx: (_ for _ in ()).throw(AssertionError("ECS 제출")))
    hold = {("collect", "hold"): {"kind": "ECS_STATE_UNKNOWN", "reason": "r"}}
    ctx = {"ti": _Ti(pulled=hold), "logical_date": NOW, "dag_run": SimpleNamespace(conf={}, run_after=NOW)}
    with pytest.raises(AirflowSkipException):
        dag_module.dag.get_task("normalize").execute(ctx)
    assert dag_module.dag.get_task("report").stop_on_upstream_hold is False   # 보류를 원장에 옮겨야 한다


def test_plan_hold_is_reported_as_a_hold(dag_module):
    from airflow.sdk.exceptions import AirflowFailException
    verdict = dag_module.dag.get_task("verdict").python_callable

    class Ti:
        def xcom_pull(self, task_ids, key):
            return {"kind": "ECS_STATE_UNKNOWN", "reason": "r"} if (task_ids, key) == ("plan", "hold") else None
    with pytest.raises(AirflowFailException, match="실행 보류"):
        verdict(ti=Ti(), dag_run=SimpleNamespace(conf={}))


@pytest.mark.parametrize("pulled, starts", [
    ({("collect", "exit_code"): 1}, True),              # 업무 실패는 끝난 것 — 정제는 돈다(SFN 과 같은 계약)
    ({("collect", "no_ecs_task"): True}, True),         # 태스크 없음(재처리 no-op·제출 전 실패)
    ({}, False),        # 결말 기록 없음 — 마지막 시도 중 worker 사망·수동 failed 표시(ECS 는 계속 돌 수 있다)
])
def test_downstream_starts_only_after_a_settled_upstream(dag_module, monkeypatch, pulled, starts):
    from airflow.sdk.exceptions import AirflowSkipException
    from airflow.providers.amazon.aws.operators.ecs import EcsRunTaskOperator
    launched = []
    monkeypatch.setattr(EcsRunTaskOperator, "execute", lambda self, ctx: launched.append(1))
    pulled = {("plan", "exit_code"): 0, **pulled}
    op = dag_module.dag.get_task("normalize")
    op.__dict__["client"] = SimpleNamespace(describe_tasks=lambda **_: {"tasks": []})
    ctx = {"ti": _Ti(pulled=pulled), "logical_date": NOW, "dag_run": SimpleNamespace(conf={}, run_after=NOW)}
    if starts:
        with pytest.raises(Exception) as err:       # 대역 ECS 라 판정은 보류로 끝난다 — 시작했는지만 본다
            op.execute(ctx)
        assert not isinstance(err.value, AirflowSkipException) and launched == [1]
    else:
        with pytest.raises(AirflowSkipException, match="결말 미확인"):
            op.execute(ctx)
        assert launched == []


@pytest.mark.parametrize("path", ["noop_reprocess", "same_day_refused", "read_failed_first_try"])
def test_steps_that_start_no_task_say_so(monkeypatch, path):
    # 하류가 "결말 확인"으로 읽는 근거 — 이 표시가 빠지면 재처리 run 의 정제가 수집 no-op 뒤에 영영 skip 된다.
    from airflow.sdk.exceptions import AirflowFailException
    kwargs = {"noop_on_reprocess": True, "same_day_only": True}
    ecs = FakeEcs(list_errors=3 if path == "read_failed_first_try" else 0)
    op = _op(ecs, **kwargs)
    conf = {"reprocess_slot": NOW.isoformat()} if path == "noop_reprocess" else None
    slot = NOW - timedelta(days=3) if path == "same_day_refused" else NOW
    ctx = _context(slot, conf=conf)
    if path == "noop_reprocess":
        op.execute(ctx)
    else:
        with pytest.raises(AirflowFailException):
            op.execute(ctx)
    assert ctx["ti"].pushed.get("no_ecs_task") is True and ecs.run_calls == []


def test_stop_evidence_matches_the_shared_table_the_reconciler_also_uses():
    # 같은 ECS 증거에 Airflow 는 보류, Reconciler 는 업무 실패로 결론 내면 원장과 화면이 어긋난다 — 한 표로 묶는다.
    import json
    from edge_batch import ecs_stop_evidence
    cases = json.loads((ROOT / "tests" / "ecs_stop_cases.json").read_text())["cases"]
    for case in cases:
        assert list(ecs_stop_evidence(case["task"])) == [case["kind"], case["exit"]], case["name"]


class _Pulls:
    def __init__(self, values):
        self.values = values

    def xcom_pull(self, task_ids, key):
        return self.values.get((task_ids, key))


@pytest.mark.parametrize("values, expected_kind", [
    ({("collect", "edge_started"): True}, "ECS_STATE_UNKNOWN"),      # worker 사망·수동 failed — 결말 없음
    ({("collect", "edge_started"): True, ("collect", "ecs_task_arn"): "arn:x"}, "ECS_STATE_UNKNOWN"),
    ({("collect", "edge_started"): True, ("collect", "exit_code"): 1}, None),     # 확정된 업무 실패는 보류가 아니다
    ({("collect", "edge_started"): True, ("collect", "no_ecs_task"): True}, None),
    ({}, None),                                                        # 시작도 안 함(skip·upstream_failed)
    ({("collect", "hold"): {"kind": "RESULT_UNKNOWN", "reason": "r"}}, "RESULT_UNKNOWN"),
])
def test_a_step_that_ended_without_a_conclusion_is_a_hold(values, expected_kind):
    from edge_batch import settlement
    got = settlement(_Pulls(values), "collect")
    assert (got or {}).get("kind") == expected_kind


def test_result_less_step_reaches_verdict_and_ledger_as_a_hold(dag_module):
    import json
    from airflow.sdk.exceptions import AirflowFailException
    pulls = _Pulls({("plan", "edge_started"): True, ("plan", "exit_code"): 0,
                    ("collect", "edge_started"): True, ("collect", "ecs_task_arn"): "arn:x"})
    assert json.loads(dag_module._holds_env(pulls))["INVESTOR_INTRADAY_COLLECTION_KIS"]["kind"] == \
        "ECS_STATE_UNKNOWN"
    verdict = dag_module.dag.get_task("verdict").python_callable
    with pytest.raises(AirflowFailException, match="실행 보류"):
        verdict(ti=pulls, dag_run=SimpleNamespace(conf={}))


def test_every_attempt_marks_its_start_and_a_skipped_step_says_it_ran_nothing(dag_module, monkeypatch):
    from airflow.sdk.exceptions import AirflowSkipException
    hold = {("collect", "hold"): {"kind": "ECS_STATE_UNKNOWN", "reason": "r"}}
    ti = _Ti(pulled=hold)
    ctx = {"ti": ti, "logical_date": NOW, "dag_run": SimpleNamespace(conf={}, run_after=NOW)}
    with pytest.raises(AirflowSkipException):
        dag_module.dag.get_task("normalize").execute(ctx)
    assert ti.pushed.get("edge_started") is True and ti.pushed.get("no_ecs_task") is True


def test_report_names_its_dag_run_and_the_run_lifetime_fits_the_reconciler(dag_module):
    report = dag_module.dag.get_task("report")
    env = {e["name"]: e["value"] for e in report.overrides["containerOverrides"][0]["environment"]}
    assert env["OPS_REPORT_RUN_REF"] == "{{ dag.dag_id }}/{{ run_id }}"
    # 운영 기본값. 주기 Reconciler 의 수명 기준(1800초)보다 짧아야 살아 있는 run 을 보류로 올리지 않는다.
    assert dag_module.dag.dagrun_timeout == timedelta(seconds=1500)


def test_reprocess_slot_without_timezone_is_refused():
    # 오프셋 없는 슬롯을 worker 로컬(UTC)로 읽으면 10:05 KST 가 19:05 KST 가 된다 — 추측하지 않고 거부한다.
    from airflow.sdk.exceptions import AirflowFailException
    from edge_batch import reprocess_slot, run_key
    naive = {"dag_run": SimpleNamespace(conf={"reprocess_slot": "2026-09-22T10:05:00"})}
    with pytest.raises(AirflowFailException, match="시간대"):
        reprocess_slot(naive)
    aware = {"dag_run": SimpleNamespace(conf={"reprocess_slot": "2026-09-22T10:05:00+09:00"})}
    assert run_key("investor-intraday", reprocess_slot(aware)) == "investor-intraday:2026-09-22T10:05"
