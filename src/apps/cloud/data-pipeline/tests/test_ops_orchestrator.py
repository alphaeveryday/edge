"""실행 주체(orchestrator) 경계 테스트 — 레인별 SFN→Airflow 단계 이관.

지키려는 것:
- 같은 슬롯을 두 주체가 계획하면 같은 run_id 로 수집·적재가 두 번 돈다 → 뒤에 온 쪽은 실행하지 않는다.
- Airflow 주체 런은 SFN 을 시작·조회하지 않는다(없는 실행을 LAUNCH_UNCONFIRMED 로 오판하지 않게).
- Airflow 재시도가 이미 성공한 업무를 다시 부르면 외부 호출·쓰기를 반복하지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.ops import catalog, entry, states, wrapper
from data_pipeline.ops.ledger import Ledger
from data_pipeline.ops.planner import plan_run
from data_pipeline.ops.reconciler import reconcile_run

from opsfakes import FakeEcs, FakeOpsDB, FakeSfn

_DB = DbConfig(password="x")
_LANE = catalog.INVESTOR_INTRADAY_PIPELINE_TYPE
_ARN = "arn:aws:states:ap-northeast-2:123456789012:stateMachine:edge-dev-data-pipeline-investor-intraday"
# 2026-09-22 = 화요일(거래일). 00:35 UTC = KST 09:35(첫 슬롯).
_SLOT = datetime(2026, 9, 22, 0, 35, tzinfo=timezone.utc)
_REF = "edge_investor_intraday/scheduled__2026-09-22T00:35:00+00:00"


class _NoSfn:
    """Airflow 주체 경로가 SFN 을 한 번이라도 부르면 실패시키는 더블."""

    def __getattr__(self, name):
        raise AssertionError(f"Airflow 주체 런이 SFN.{name} 을 불렀다")


def _ledger(db):
    return Ledger(db=_DB, connect_fn=db.connect)


def _plan_airflow(db, ref=_REF):
    return plan_run(_ledger(db), state_machine_arn=None, scheduled_time=_SLOT,
                    pipeline_type=_LANE, sfn_client=_NoSfn(),
                    orchestrator=states.ORCHESTRATOR_AIRFLOW, orchestrator_run_ref=ref)


def test_airflow_plan_records_expectations_without_starting_sfn():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    assert result.launch_status == states.LAUNCH_LAUNCHED and result.created
    run = db.runs[result.run_key]
    # 같은 슬롯이면 SFN 경로와 같은 run_id — 원장·레이크 경로가 주체에 따라 갈리지 않는다.
    assert run["pipeline_run_id"] == plan_run(
        _ledger(FakeOpsDB()), state_machine_arn=_ARN, scheduled_time=_SLOT,
        pipeline_type=_LANE, sfn_client=FakeSfn()).pipeline_run_id
    assert run["orchestrator"] == states.ORCHESTRATOR_AIRFLOW
    assert run["orchestrator_run_ref"] == _REF
    assert run["expected_execution_arn"] is None and run["sfn_execution_arn"] is None
    assert len(db.etasks) == len(catalog.entries(_LANE)) == 3


def test_airflow_retry_of_same_slot_converges_to_one_run():
    db = FakeOpsDB()
    first, again = _plan_airflow(db), _plan_airflow(db)
    assert again.created is False and again.launch_status == states.LAUNCH_LAUNCHED
    assert again.pipeline_run_id == first.pipeline_run_id and len(db.runs) == 1
    assert not db.open_issues(states.ISSUE_LAUNCH_CONFLICT)


def test_sfn_replan_of_airflow_owned_slot_is_refused_without_starting():
    # 전환 날 EventBridge 스케줄을 끄기 전에 SFN 경로가 같은 슬롯을 다시 계획하는 경우.
    db = FakeOpsDB()
    _plan_airflow(db)
    sfn = FakeSfn()
    result = plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SLOT,
                      pipeline_type=_LANE, sfn_client=sfn)
    assert result.launch_status == states.LAUNCH_CONFLICT and result.conflict
    assert sfn.start_calls == []
    run = db.runs[result.run_key]
    assert run["launch_status"] == states.LAUNCH_LAUNCHED   # 소유자의 사실은 덮지 않는다
    assert run["orchestrator"] == states.ORCHESTRATOR_AIRFLOW
    assert len(db.open_issues(states.ISSUE_LAUNCH_CONFLICT)) == 1


def test_airflow_plan_of_sfn_owned_slot_is_refused():
    # 롤백 뒤 SFN 이 실행한 슬롯을 Airflow 가 뒤늦게(catchup·수동 trigger) 다시 요청하는 경우.
    db = FakeOpsDB()
    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SLOT,
             pipeline_type=_LANE, sfn_client=FakeSfn())
    result = _plan_airflow(db)
    assert result.launch_status == states.LAUNCH_CONFLICT
    assert db.runs[result.run_key]["orchestrator"] == states.ORCHESTRATOR_SFN


def _record(db, run_id, task_key, *, arn, exit_code, records_out=10):
    return wrapper.instrument(lambda: exit_code, task_key=task_key, run_id=run_id,
                              ledger=_ledger(db), ecs_task_arn=arn,
                              observe_data_fn=lambda _: {"records_out": records_out})


def test_reconcile_airflow_run_uses_ledger_attempts_and_defers_missed_to_hard_deadline():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/collect", exit_code=0)
    ecs = FakeEcs(tasks={"arn:ecs/collect": {"lastStatus": "STOPPED", "exitCode": 0}})

    # 작업 deadline(슬롯+10~13분)은 지났지만 런 hard deadline(6h) 전 — 도는 중으로 본다.
    summary = reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                            ecs_client=ecs, now=_SLOT + timedelta(minutes=30))
    assert summary["evidence_ok"] and summary["missed"] == []
    assert not db.open_issues(states.ISSUE_LAUNCH_UNCONFIRMED)
    normalize = db.etasks[(run_id, "NORMALIZE_INVESTOR_INTRADAY")]
    assert normalize["eligible_at"] is not None     # 원장 attempt 가 선행 완료 증거가 됐다

    # hard deadline 뒤에도 정제·적재 attempt 가 없으면 그때 MISSED/BLOCKED 로 드러난다.
    late = reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                         ecs_client=ecs, now=_SLOT + timedelta(hours=7))
    assert "NORMALIZE_INVESTOR_INTRADAY" in late["missed"]


def _fulfilled(db, *, exit_code):
    result = _plan_airflow(db)
    _record(db, result.pipeline_run_id, "NORMALIZE_INVESTOR_INTRADAY",
            arn="arn:ecs/first", exit_code=exit_code)
    return result.pipeline_run_id


def test_skip_if_succeeded_does_not_repeat_a_finished_task(monkeypatch):
    # ECS 는 exit 0 으로 끝났는데 Airflow 가 응답을 잃고 재시도해 새 태스크가 뜬 경우.
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    monkeypatch.setenv("OPS_SKIP_IF_SUCCEEDED", "1")
    calls = []
    rc = wrapper.instrument(lambda: calls.append(1) or 0, task_key="NORMALIZE_INVESTOR_INTRADAY",
                            run_id=run_id, ledger=_ledger(db), ecs_task_arn="arn:ecs/retry")
    assert rc == 0 and calls == []
    assert [a["arn"] for a in db.attempts] == ["arn:ecs/first"]   # 가짜 attempt 없음


def test_skip_if_succeeded_still_reruns_partial_and_forced_runs(monkeypatch):
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=2)       # exit 2 도 FULFILLED 지만 복구 재시도는 정당하다
    monkeypatch.setenv("OPS_SKIP_IF_SUCCEEDED", "1")
    calls = []
    wrapper.instrument(lambda: calls.append(1) or 0, task_key="NORMALIZE_INVESTOR_INTRADAY",
                       run_id=run_id, ledger=_ledger(db), ecs_task_arn="arn:ecs/retry")
    assert calls == [1]

    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    monkeypatch.delenv("OPS_SKIP_IF_SUCCEEDED")  # 재처리 run = env 없음 = 의도한 재처리
    wrapper.instrument(lambda: calls.append(2) or 0, task_key="NORMALIZE_INVESTOR_INTRADAY",
                       run_id=run_id, ledger=_ledger(db), ecs_task_arn="arn:ecs/force")
    assert calls == [1, 2]


@pytest.mark.parametrize("env, message", [
    ({}, "OPS_ORCHESTRATOR_RUN_REF"),
    ({"OPS_ORCHESTRATOR_RUN_REF": _REF}, "OPS_SCHEDULED_TIME"),
    ({"OPS_ORCHESTRATOR_RUN_REF": _REF, "OPS_SCHEDULED_TIME": "not-a-time"}, "OPS_SCHEDULED_TIME"),
])
def test_airflow_plan_cli_refuses_without_slot_identity(monkeypatch, env, message):
    # 슬롯 시각이 없으면 지금 시각이 슬롯이 되어 재시도마다 다른 run_id 가 생긴다.
    for key in ("OPS_ORCHESTRATOR_RUN_REF", "OPS_SCHEDULED_TIME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPS_PIPELINE_TYPE", _LANE)
    monkeypatch.setenv("OPS_ORCHESTRATOR", states.ORCHESTRATOR_AIRFLOW)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit, match=message):
        entry.plan_run_cli(object())


# ── 실행권(StepLock)과 원장 장애 — Airflow 경로(OPS_EXCLUSIVE_STEP)만 fail-closed ──
def _exclusive(monkeypatch, *, skip=True):
    monkeypatch.setenv("OPS_EXCLUSIVE_STEP", "1")
    monkeypatch.setenv("OPS_STEP_LOCK_WAIT_SECONDS", "0")
    if skip:
        monkeypatch.setenv("OPS_SKIP_IF_SUCCEEDED", "1")
    else:
        monkeypatch.delenv("OPS_SKIP_IF_SUCCEEDED", raising=False)


def _run(db, run_id, calls, arn="arn:ecs/next"):
    return wrapper.instrument(lambda: calls.append(arn) or 0, task_key="NORMALIZE_INVESTOR_INTRADAY",
                              run_id=run_id, ledger=_ledger(db), ecs_task_arn=arn)


def test_retry_after_lost_response_does_not_run_when_ledger_is_down(monkeypatch):
    # 업무 성공 → 응답 유실 → 재시도 → 원장 조회 실패. 종전(fail-open)이면 업무가 다시 돈다.
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    _exclusive(monkeypatch)
    db.fail = True
    calls = []
    assert _run(db, run_id, calls) == wrapper.STEP_NOT_RUN_EXIT and calls == []


def test_unreadable_history_inside_lock_does_not_run(monkeypatch):
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    _exclusive(monkeypatch)
    monkeypatch.setattr(Ledger, "attempts_for", lambda self, etid: (_ for _ in ()).throw(OSError("down")))
    calls = []
    assert _run(db, run_id, calls) == wrapper.STEP_NOT_RUN_EXIT and calls == []


def test_step_held_by_another_execution_is_not_run(monkeypatch):
    # 같은 작업이 아직 실행 중(lock 보유) — 성공 이력이 아직 없어 skip 확인만으로는 못 막는 경우.
    db = FakeOpsDB(advisory_grants=False)
    run_id = _plan_airflow(db).pipeline_run_id
    _exclusive(monkeypatch)
    calls = []
    assert _run(db, run_id, calls) == wrapper.STEP_NOT_RUN_EXIT and calls == []
    assert db.attempts == []                     # 실행하지 않은 시도는 원장에 남기지 않는다


def test_reprocess_runs_again_under_the_lock(monkeypatch):
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    _exclusive(monkeypatch, skip=False)          # 재처리 run: 실행권은 잡되 성공 이력 skip 없음
    calls = []
    assert _run(db, run_id, calls) == 0 and calls == ["arn:ecs/next"]


def test_sfn_path_keeps_running_through_ledger_outage(monkeypatch):
    # env 없는 기존 경로는 종전 계약(원장 장애가 작업을 막지 않는다, 스펙 §3.4) 그대로.
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    for key in ("OPS_EXCLUSIVE_STEP", "OPS_SKIP_IF_SUCCEEDED"):
        monkeypatch.delenv(key, raising=False)
    db.fail = True
    calls = []
    assert _run(db, run_id, calls) == 0 and calls == ["arn:ecs/next"]


# ── Airflow 주체 런의 대조: 부분 실패·실행 중·실행 불명 ──
def _start_only(db, run_id, task_key, arn):
    """wrapper 가 attempt 시작만 남기고 끝을 못 남긴 상태(실행 중이거나 죽었거나)."""
    ledger = _ledger(db)
    etid = ledger.find_expected_task(run_id=run_id, task_key=task_key)["expected_task_id"]
    ledger.record_attempt_start(expected_task_id=etid, ecs_task_arn=arn)


class _EcsDown:
    def describe_tasks(self, **_):
        raise OSError("ecs unreachable")


def test_reconcile_airflow_partial_normalize_counts_as_fulfilled_and_unblocks_load():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c", exit_code=0)
    _record(db, run_id, "NORMALIZE_INVESTOR_INTRADAY", arn="arn:ecs/n", exit_code=2)
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(),
                  now=_SLOT + timedelta(minutes=30))
    assert db.etasks[(run_id, "NORMALIZE_INVESTOR_INTRADAY")]["task_outcome"] == states.OUTCOME_FULFILLED
    assert db.etasks[(run_id, "LOAD_INVESTOR_INTRADAY")]["eligible_at"] is not None


def test_reconcile_airflow_running_attempt_is_not_judged_before_its_stall_threshold():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _start_only(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", "arn:ecs/running")
    ecs = FakeEcs(tasks={"arn:ecs/running": {"lastStatus": "RUNNING"}})
    summary = reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                            ecs_client=ecs, now=_SLOT + timedelta(minutes=5))
    task = db.etasks[(run_id, "INVESTOR_INTRADAY_COLLECTION_KIS")]
    assert task["task_outcome"] == states.OUTCOME_PENDING
    assert summary["missed"] == [] and summary["failed"] == []


def test_reconcile_airflow_unknown_execution_is_not_turned_into_failure_or_missed():
    # ECS 상태를 못 읽는 attempt — 실패로도 미실행으로도 단정하지 않는다(증거 없음 ≠ 미실행).
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _start_only(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", "arn:ecs/unknown")
    summary = reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                            ecs_client=_EcsDown(), now=_SLOT + timedelta(hours=7))
    task = db.etasks[(run_id, "INVESTOR_INTRADAY_COLLECTION_KIS")]
    assert task["task_outcome"] == states.OUTCOME_PENDING
    assert "INVESTOR_INTRADAY_COLLECTION_KIS" not in summary["missed"] + summary["failed"]


def test_exclusive_without_ledger_is_not_run(monkeypatch):
    # task-def 에 DB env 가 빠진 배선 누락 — 조용히 가드 없이 돌면 중복 호출이 열린다.
    _exclusive(monkeypatch)
    calls = []
    rc = wrapper.instrument(lambda: calls.append(1) or 0, task_key="NORMALIZE_INVESTOR_INTRADAY",
                            run_id="run_x", ledger=None, ecs_task_arn="arn:ecs/x")
    assert rc == wrapper.STEP_NOT_RUN_EXIT and calls == []


def test_exclusive_run_without_recorded_start_is_not_run(monkeypatch):
    # 시작 기록 없는 실행은 다음 재시도가 볼 수 없다(오래된 성공을 믿고 skip·Reconciler 오판).
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _exclusive(monkeypatch)
    monkeypatch.delenv("OPS_ECS_TASK_ARN", raising=False)
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    calls = []
    rc = wrapper.instrument(lambda: calls.append(1) or 0, task_key="NORMALIZE_INVESTOR_INTRADAY",
                            run_id=run_id, ledger=_ledger(db), ecs_task_arn=None)
    assert rc == wrapper.STEP_NOT_RUN_EXIT and calls == [] and db.attempts == []


def test_exclusive_unplanned_run_is_not_run(monkeypatch):
    db = FakeOpsDB()
    _plan_airflow(db)
    _exclusive(monkeypatch)
    calls = []
    assert _run(db, "run_not_planned", calls) == wrapper.STEP_NOT_RUN_EXIT and calls == []


def test_success_skip_yields_when_upstream_reran_after_it(monkeypatch):
    # 수집 실패 → 빈 입력 정제 exit 0 → 수집 복구(clear). 정제 skip 이면 복구한 raw 가 적재되지 않는다.
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c1", exit_code=1)
    _record(db, run_id, "NORMALIZE_INVESTOR_INTRADAY", arn="arn:ecs/n1", exit_code=0)
    _exclusive(monkeypatch)
    calls = []
    assert _run(db, run_id, calls, arn="arn:ecs/n-retry") == 0 and calls == []   # 입력 불변 → skip
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c2", exit_code=0)
    assert _run(db, run_id, calls, arn="arn:ecs/n2") == 0 and calls == ["arn:ecs/n2"]


# ── 재처리 입력 확인·Airflow 런 상태 투영 ──
def test_reprocess_requires_an_existing_slot_with_collected_raw(monkeypatch):
    # 없는 슬롯(오타)·수집 실패 슬롯을 재처리하면 빈 입력 정제·적재가 "성공"으로 끝난다.
    db = FakeOpsDB()
    ledger = _ledger(db)
    key = _plan_airflow(db).run_key
    assert ledger.reprocess_ready("investor-intraday:2026-09-22T09:36") == (False, "계획된 run 없음 또는 raw 단계 없음")
    ready, reason = ledger.reprocess_ready(key)
    assert not ready and "raw 단계 미완료" in reason
    _record(db, db.runs[key]["pipeline_run_id"], "INVESTOR_INTRADAY_COLLECTION_KIS",
            arn="arn:ecs/c", exit_code=0)
    assert ledger.reprocess_ready(key) == (True, "ok")


def _finish(db, run_id, exits):
    for task_key, (arn, code) in exits.items():
        _record(db, run_id, task_key, arn=arn, exit_code=code)


@pytest.mark.parametrize("normalize_exit, expected", [(0, states.ORCH_SUCCEEDED), (2, states.ORCH_FAILED)])
def test_reconciler_projects_airflow_run_status_like_the_dag_verdict(normalize_exit, expected):
    # 비워 두면 콘솔 R02 가 정상 완료 런을 미귀결로 올린다. exit 2 는 원장 FULFILLED 여도 런 실패다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    ecs = FakeEcs()
    # 결론 안 난 증거로는 투영하지 않는다(RUNNING 도 쓰지 않는다 — 비어 있어야 R02 가 마감 뒤 드러낸다).
    assert reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=ecs,
                         now=_SLOT + timedelta(minutes=5))["orchestration"] is None
    _finish(db, run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", normalize_exit),
                         "LOAD_INVESTOR_INTRADAY": ("arn:ecs/l", 0)})
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=ecs,
                  now=_SLOT + timedelta(minutes=30))
    assert db.runs[result.run_key]["orchestration_status"] == expected


def test_unconcluded_airflow_run_stays_unresolved_after_hard_deadline():
    # 증거가 없으면 종료로 단정하지 않는다 — NULL 로 남아 콘솔 R02(마감 초과 미귀결)가 드러낸다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    summary = reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                            ecs_client=FakeEcs(), now=_SLOT + timedelta(hours=7))
    assert summary["orchestration"] is None
    assert db.runs[result.run_key]["orchestration_status"] is None


def test_transient_ecs_read_failure_is_not_turned_into_a_terminal_status():
    # 앞 두 작업은 성공, 적재 attempt 는 시작만 기록됐고 ECS 조회가 일시 실패 — FAILED 로 닫지 않는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _finish(db, run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", 0)})
    _start_only(db, run_id, "LOAD_INVESTOR_INTRADAY", "arn:ecs/l")
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=_EcsDown(),
                  now=_SLOT + timedelta(hours=7))
    assert db.runs[result.run_key]["orchestration_status"] is None
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),        # 조회 회복
                  ecs_client=FakeEcs(tasks={"arn:ecs/l": {"lastStatus": "STOPPED", "exitCode": 0}}),
                  now=_SLOT + timedelta(hours=7, minutes=15))
    assert db.runs[result.run_key]["orchestration_status"] == states.ORCH_SUCCEEDED


def test_rerun_after_a_report_is_projected_when_the_dag_could_not_report():
    # 보고(FAILED) 뒤 clear 로 다시 돌아 성공했지만 DAG 가 보고 전에 죽었다 — 새 업무 시도가 이긴다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _finish(db, run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", 1)})
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(),
              now=_SLOT + timedelta(hours=1))
    reconcile_run(_ledger(db), reported_status=states.ORCH_FAILED, **kw)
    _finish(db, run_id, {"NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n2", 0),
                         "LOAD_INVESTOR_INTRADAY": ("arn:ecs/l", 0)})
    reconcile_run(_ledger(db), **kw)
    assert db.runs[result.run_key]["orchestration_status"] == states.ORCH_SUCCEEDED


def test_skip_is_traceable_but_not_business_evidence(monkeypatch):
    # 중복 skip 도 어느 Airflow 시도·ECS 태스크였는지 남긴다. 다음 판단의 "최신 시도"가 되면 안 된다.
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    monkeypatch.setenv("OPS_ORCHESTRATOR_ATTEMPT_REF", "airflow:edge_investor_intraday/r1/collect/1")
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c1", exit_code=0)
    _exclusive(monkeypatch)
    for try_number, arn in ((2, "arn:ecs/c2"), (3, "arn:ecs/c3")):
        monkeypatch.setenv("OPS_ORCHESTRATOR_ATTEMPT_REF",
                           f"airflow:edge_investor_intraday/r1/collect/{try_number}")
        monkeypatch.setenv("OPS_ECS_TASK_ARN", arn)
        calls = []
        rc = wrapper.instrument(lambda: calls.append(1) or 0, task_key="INVESTOR_INTRADAY_COLLECTION_KIS",
                                run_id=run_id, ledger=_ledger(db), ecs_task_arn=arn)
        assert rc == 0 and calls == []           # 두 번째 skip 도 여전히 skip(첫 skip 행에 안 흔들린다)
    # skip 행은 한 번에 완료 상태로 쓴다 — RUNNING 으로 남으면 전환 절차의 종료 확인을 영구히 막는다.
    assert all(a["status"] == states.EXEC_SUCCEEDED for a in db.attempts)
    rows = [(a["arn"], a["source"], a["orchestrator_attempt_ref"], a["exit_code"]) for a in db.attempts]
    assert rows == [
        ("arn:ecs/c1", states.SOURCE_WRAPPER, "airflow:edge_investor_intraday/r1/collect/1", 0),
        ("arn:ecs/c2", states.SOURCE_DUPLICATE_SKIP, "airflow:edge_investor_intraday/r1/collect/2", 0),
        ("arn:ecs/c3", states.SOURCE_DUPLICATE_SKIP, "airflow:edge_investor_intraday/r1/collect/3", 0),
    ]


def test_zero_row_success_does_not_block_recollection(monkeypatch):
    # 거래일에 소스 비활성·자격증명 결측으로 수집이 skip(exit 0, 0건)된 뒤 설정을 고쳐 다시 부르는 경우.
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c0", exit_code=0, records_out=0)
    # 0건 수집 슬롯은 재처리할 입력도 없다.
    assert _ledger(db).reprocess_ready("investor-intraday:2026-09-22T09:35")[0] is False
    _exclusive(monkeypatch)
    calls = []
    rc = wrapper.instrument(lambda: calls.append(1) or 0, task_key="INVESTOR_INTRADAY_COLLECTION_KIS",
                            run_id=run_id, ledger=_ledger(db), ecs_task_arn="arn:ecs/c1")
    assert rc == 0 and calls == [1]


def test_reported_airflow_verdict_is_not_overwritten_by_projection():
    # 재처리 run 이 새 attempt 없이 실패(기동 실패 재시도 소진)하면 원장엔 앞 세대 성공만 남는다.
    # DAG 가 보고한 FAILED 를 주기 대조의 투영(SUCCEEDED)이 덮으면 안 된다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _finish(db, result.pipeline_run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", 0),
                                         "LOAD_INVESTOR_INTRADAY": ("arn:ecs/l", 0)})
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(),
              now=_SLOT + timedelta(hours=2))
    reconcile_run(_ledger(db), reported_status=states.ORCH_FAILED, **kw)
    reconcile_run(_ledger(db), **kw)                                   # 주기 대조(보고 없음)
    assert db.runs[result.run_key]["orchestration_status"] == states.ORCH_FAILED
    reconcile_run(_ledger(db), reported_status=states.ORCH_SUCCEEDED, **kw)   # 이후 성공 재처리 보고
    assert db.runs[result.run_key]["orchestration_status"] == states.ORCH_SUCCEEDED


def test_status_report_losing_the_reconcile_lock_is_retried_not_dropped(monkeypatch):
    # 주기 대조가 락을 쥔 동안 온 보고를 0 으로 끝내면 판정이 유실된다(확정값은 투영이 안 덮는다).
    db = FakeOpsDB(advisory_grants=False)
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: _ledger(db))
    monkeypatch.setenv("OPS_RUN_KEY", "investor-intraday:2026-09-22T09:35")
    monkeypatch.setenv("OPS_ORCHESTRATION_STATUS", states.ORCH_FAILED)
    assert entry.reconcile_cli(object()) == wrapper.STEP_NOT_RUN_EXIT
    monkeypatch.delenv("OPS_ORCHESTRATION_STATUS")          # 보고 없는 주기 실행은 종전대로 skip(0)
    assert entry.reconcile_cli(object()) == 0


def test_success_skip_requires_count_and_exit_from_the_same_attempt(monkeypatch):
    # attempt 종료(exit 0)는 기록됐는데 outcome 갱신(건수·current_attempt_id)이 실패한 상태.
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c1", exit_code=0)
    ledger = _ledger(db)
    monkeypatch.setattr(Ledger, "update_task_outcome", lambda self, *a, **k: None)
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c2", exit_code=0, records_out=0)
    monkeypatch.undo()
    _exclusive(monkeypatch)
    calls = []
    rc = wrapper.instrument(lambda: calls.append(1) or 0, task_key="INVESTOR_INTRADAY_COLLECTION_KIS",
                            run_id=run_id, ledger=ledger, ecs_task_arn="arn:ecs/c3")
    assert rc == 0 and calls == [1]       # 앞 시도의 건수로 최신 시도를 skip 하지 않는다



def test_new_unconcluded_attempt_reopens_a_previously_settled_slot():
    # 성공으로 보고된 슬롯을 재처리하던 중 종료 증거·보고가 유실 — 앞 SUCCEEDED 가 남으면 R02 가 못 본다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _finish(db, run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", 0),
                         "LOAD_INVESTOR_INTRADAY": ("arn:ecs/l", 0)})
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), now=_SLOT + timedelta(hours=1))
    reconcile_run(_ledger(db), reported_status=states.ORCH_SUCCEEDED, ecs_client=FakeEcs(), **kw)
    _start_only(db, run_id, "NORMALIZE_INVESTOR_INTRADAY", "arn:ecs/n-reprocess")
    reconcile_run(_ledger(db), ecs_client=_EcsDown(), **kw)
    assert db.runs[result.run_key]["orchestration_status"] is None



def test_projection_does_not_mix_generations():
    # 보고(FAILED) 뒤 재처리가 정제만 다시 성공시키고 적재·보고 전에 끊겼다. 적재의 최신 시도는 앞 세대의
    # 성공이다 — 이를 결론으로 쓰면 새 정제 결과가 적재되지 않았는데 SUCCEEDED 가 된다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _finish(db, run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", 2),
                         "LOAD_INVESTOR_INTRADAY": ("arn:ecs/l", 0)})
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(),
              now=_SLOT + timedelta(hours=1))
    reconcile_run(_ledger(db), reported_status=states.ORCH_FAILED, **kw)
    _finish(db, run_id, {"NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n2", 0)})
    reconcile_run(_ledger(db), **kw)
    assert db.runs[result.run_key]["orchestration_status"] is None      # 미귀결 — 성공으로 단정 안 함


def test_skip_row_after_a_report_does_not_reopen_it(monkeypatch):
    # 보고 뒤 운영자가 collect 만 clear 해 중복 skip 행이 생겼다 — 업무 시도가 아니므로 보고를 유지한다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _finish(db, run_id, {"INVESTOR_INTRADAY_COLLECTION_KIS": ("arn:ecs/c", 0),
                         "NORMALIZE_INVESTOR_INTRADAY": ("arn:ecs/n", 1)})
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(),
              now=_SLOT + timedelta(hours=1))
    reconcile_run(_ledger(db), reported_status=states.ORCH_FAILED, **kw)
    _exclusive(monkeypatch)
    monkeypatch.setenv("OPS_ECS_TASK_ARN", "arn:ecs/c-again")
    wrapper.instrument(lambda: 0, task_key="INVESTOR_INTRADAY_COLLECTION_KIS", run_id=run_id,
                       ledger=_ledger(db), ecs_task_arn="arn:ecs/c-again")
    assert db.attempts[-1]["source"] == states.SOURCE_DUPLICATE_SKIP
    reconcile_run(_ledger(db), **kw)
    assert db.runs[result.run_key]["orchestration_status"] == states.ORCH_FAILED


# ── 실행 상태 불명 시 보류(ALPHA-1088 초기 운영 정책) ──
# lock 은 잡은 연결이 끊기면 풀린다. 그래서 "lock 을 얻었다"는 "앞 실행이 끝났다"가 아니다 — 원장에 종료가
# 확인되지 않은 같은 작업의 시도가 있으면 업무를 시작하지 않고 보류한다. 보류는 시간이 지나도 풀리지 않고,
# 그 시도가 스스로 끝을 기록하거나 Reconciler 가 ECS STOPPED 를 확인해야 풀린다.
_NORMALIZE = "NORMALIZE_INVESTOR_INTRADAY"


def _rec_key(run_id, task_key, kind):
    from data_pipeline.ops.reconciler import run_hold_key
    return run_hold_key(run_id, task_key, kind)


def _plan_slot(db, slot, ref):
    return plan_run(_ledger(db), state_machine_arn=None, scheduled_time=slot, pipeline_type=_LANE,
                    sfn_client=_NoSfn(), orchestrator=states.ORCHESTRATOR_AIRFLOW,
                    orchestrator_run_ref=ref)


def test_lock_alone_does_not_start_work_while_an_earlier_attempt_is_unconfirmed(monkeypatch):
    # A(09:35 슬롯 정제)가 시작 기록만 남긴 채 lock 연결을 잃었다 — B(10:05 슬롯)는 lock 을 얻는다.
    # 두 슬롯은 같은 거래일 canonical 파티션을 병합하므로 run 이 달라도 겹치면 안 된다.
    db = FakeOpsDB()
    a_run = _plan_airflow(db).pipeline_run_id
    _start_only(db, a_run, _NORMALIZE, "arn:ecs/a")
    b_run = _plan_slot(db, _SLOT + timedelta(minutes=30), "b").pipeline_run_id
    _exclusive(monkeypatch, skip=False)
    calls = []
    assert _run(db, b_run, calls, arn="arn:ecs/b") == wrapper.STEP_HELD_EXIT
    assert calls == []                                             # 업무 실행 0
    assert [a["arn"] for a in db.attempts] == ["arn:ecs/a"]        # B 는 시도 행도 남기지 않는다
    b_task = db.etasks[(b_run, _NORMALIZE)]
    assert (b_task["task_outcome"], b_task["outcome_reason"]) == (states.OUTCOME_FAILED,
                                                                   states.REASON_EXECUTION_HOLD)
    [issue] = db.open_issues(states.ISSUE_EXECUTION_HOLD)
    assert issue["evidence"]["kind"] == states.HOLD_OPEN_ATTEMPT
    assert [b["ecs_task_arn"] for b in issue["evidence"]["blocking"]] == ["arn:ecs/a"]


def test_hold_is_released_by_ecs_stop_evidence_not_by_elapsed_time(monkeypatch):
    db = FakeOpsDB()
    result = _plan_airflow(db)
    a_run = result.pipeline_run_id
    _start_only(db, a_run, _NORMALIZE, "arn:ecs/a")
    _exclusive(monkeypatch, skip=False)
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn())
    # 7시간 뒤라도 ECS 를 못 읽거나 아직 돌면 닫지 않는다 — 보류 유지.
    for ecs in (_EcsDown(), FakeEcs(tasks={"arn:ecs/a": {"lastStatus": "RUNNING"}})):
        reconcile_run(_ledger(db), ecs_client=ecs, now=_SLOT + timedelta(hours=7), **kw)
        assert _run(db, a_run, [], arn="arn:ecs/b") == wrapper.STEP_HELD_EXIT
    # 강제 종료로 exit code 없이 STOPPED — 종료가 확인됐으니 시도는 닫히고(결과는 미확정) 보류가 풀린다.
    reconcile_run(_ledger(db), ecs_client=FakeEcs(tasks={"arn:ecs/a": {"lastStatus": "STOPPED"}}),
                  now=_SLOT + timedelta(hours=7), **kw)
    a = db.attempts[0]
    assert (a["status"], a["exit_code"]) == (states.EXEC_FAILED, None)
    calls = []
    assert _run(db, a_run, calls, arn="arn:ecs/b") == 0 and calls == ["arn:ecs/b"]
    # 컨테이너 보류 기록은 닫히고, ECS 가 결과 미상 종료로 닫은 시도는 RESULT_UNKNOWN 으로 남는다(새 실행은 안 막는다).
    assert [i["evidence"]["kind"] for i in db.open_issues(states.ISSUE_EXECUTION_HOLD)] == [states.HOLD_RESULT_UNKNOWN]


@pytest.mark.parametrize("ecs_task, exit_code, reason", [
    ({"lastStatus": "STOPPED"}, None, "stopped_result_unknown"),                         # exit 없음
    ({"lastStatus": "STOPPED", "exitCode": 137, "stopCode": "UserInitiated"}, 137,
     "stopped_result_unknown"),                                                          # 외부 종료
    ({"lastStatus": "STOPPED", "exitCode": 1, "stopCode": "EssentialContainerExited"}, 1,
     "attempt_failed"),                                                                  # 업무 실패
    ({"lastStatus": "STOPPED", "exitCode": 2, "stopCode": "UserInitiated"}, 2,
     "stopped_result_unknown"),         # 외부 종료의 2 를 "부분 성공(산출 있음)"으로 읽지 않는다
])
def test_stop_is_closed_but_an_external_stop_is_not_a_business_result(ecs_task, exit_code, reason):
    # 종료 확인(시도 닫힘)과 업무 결과는 다른 축이다 — 강제 종료를 업무 실패 사유로 적지 않고, 런을 결론 내지 않는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _record(db, run_id, "INVESTOR_INTRADAY_COLLECTION_KIS", arn="arn:ecs/c", exit_code=0)
    _start_only(db, run_id, _NORMALIZE, "arn:ecs/n")
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                  ecs_client=FakeEcs(tasks={"arn:ecs/n": ecs_task}), now=_SLOT + timedelta(hours=1))
    task = db.etasks[(run_id, _NORMALIZE)]
    assert (task["task_outcome"], task["outcome_reason"]) == (states.OUTCOME_FAILED, reason)
    assert (db.attempts[-1]["status"], db.attempts[-1]["exit_code"]) == (states.EXEC_FAILED, exit_code)
    if reason != "attempt_failed":
        assert db.runs[result.run_key]["orchestration_status"] is None


def test_start_record_is_committed_on_the_lock_session(monkeypatch):
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _exclusive(monkeypatch, skip=False)
    calls = []
    assert _run(db, run_id, calls) == 0 and calls == ["arn:ecs/next"]
    assert db.commits == 1 and db.attempts[0]["arn"] == "arn:ecs/next"


@pytest.mark.parametrize("method", ["blocking", "start_attempt"])
def test_lock_session_failure_does_not_start_work(monkeypatch, method):
    # 판단 재료를 lock 세션에서 못 읽거나(blocking) 시작 기록을 lock 세션에 못 남기면(연결 상실) 실행하지 않는다.
    from data_pipeline.ops.ledger import StepLock
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _exclusive(monkeypatch, skip=False)
    monkeypatch.setattr(StepLock, method, lambda self, **_: (_ for _ in ()).throw(OSError("closed")))
    calls = []
    assert _run(db, run_id, calls) == wrapper.STEP_NOT_RUN_EXIT and calls == []


def test_sfn_path_is_not_gated_by_open_attempts(monkeypatch):
    # 코드 보호 범위의 경계를 고정한다: env 없는 SFN·수동 경로는 종전 그대로 돈다. 두 실행 주체의 겹침은
    # 전환 절차(한 주체만 켬)가 막는다 — 이 테스트가 깨지면 README 의 보호 범위 표를 같이 고쳐야 한다.
    db = FakeOpsDB()
    run_id = _plan_airflow(db).pipeline_run_id
    _start_only(db, run_id, _NORMALIZE, "arn:ecs/a")
    for key in ("OPS_EXCLUSIVE_STEP", "OPS_SKIP_IF_SUCCEEDED"):
        monkeypatch.delenv(key, raising=False)
    calls = []
    assert _run(db, run_id, calls, arn="arn:ecs/b") == 0 and calls == ["arn:ecs/b"]


def test_airflow_ecs_hold_blocks_reprocess_until_the_operator_resolves_it(monkeypatch):
    # Airflow 가 ECS 태스크 생성·종료를 확인 못 해 보류 → report 가 원장에 남긴다. 그 태스크는 원장에 시도가
    # 없을 수 있다(컨테이너가 wrapper 전에 있다) — 재처리·수동 trigger 가 새 run 으로 우회하지 못해야 한다.
    from data_pipeline.ops import reconciler
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    reconciler.record_execution_holds(_ledger(db), run_key=result.run_key, holds={
        _NORMALIZE: {"kind": states.HOLD_ECS_STATE_UNKNOWN, "reason": "제출 응답 유실"},
        "LOAD_INVESTOR_INTRADAY": {"kind": states.HOLD_RESULT_UNKNOWN, "reason": "강제 종료"}})
    task = db.etasks[(run_id, _NORMALIZE)]
    assert (task["task_outcome"], task["outcome_reason"]) == (states.OUTCOME_FAILED,
                                                               states.REASON_EXECUTION_HOLD)
    # MISSED(미실행)로 바뀌지 않는다 — 실행 여부를 모르는 것이지 안 돈 것이 아니다.
    late = reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                         ecs_client=FakeEcs(), now=_SLOT + timedelta(hours=7))
    assert _NORMALIZE not in late["missed"]
    _exclusive(monkeypatch, skip=False)
    other = _plan_slot(db, _SLOT + timedelta(minutes=30), "reprocess").pipeline_run_id
    assert _run(db, other, [], arn="arn:ecs/b") == wrapper.STEP_HELD_EXIT
    # 결과 미확정(RESULT_UNKNOWN)은 종료가 확인된 것이라 새 실행을 막지 않는다.
    load = wrapper.instrument(lambda: 0, task_key="LOAD_INVESTOR_INTRADAY", run_id=other,
                              ledger=_ledger(db), ecs_task_arn="arn:ecs/l")
    assert load == 0
    # 운영자가 종료를 확인하고 해제한다(삭제가 아니라 RESOLVED 전이).
    _ledger(db).resolve_issue(_rec_key(run_id, _NORMALIZE, states.HOLD_ECS_STATE_UNKNOWN),
                              resolution_reason="operator_confirmed_stopped", resolution_source="operator")
    calls = []
    assert _run(db, other, calls, arn="arn:ecs/c") == 0 and calls == ["arn:ecs/c"]


def test_hold_does_not_overwrite_a_settled_outcome(monkeypatch):
    # 이미 성공한 슬롯의 재처리가 보류됐다 — 슬롯의 데이터는 그대로이므로 FULFILLED 를 FAILED 로 덮지 않는다.
    db = FakeOpsDB()
    run_id = _fulfilled(db, exit_code=0)
    other = _plan_slot(db, _SLOT + timedelta(minutes=30), "b").pipeline_run_id
    _start_only(db, other, _NORMALIZE, "arn:ecs/running")
    _exclusive(monkeypatch, skip=False)
    assert _run(db, run_id, [], arn="arn:ecs/x") == wrapper.STEP_HELD_EXIT
    assert db.etasks[(run_id, _NORMALIZE)]["task_outcome"] == states.OUTCOME_FULFILLED
    assert db.open_issues(states.ISSUE_EXECUTION_HOLD)


@pytest.mark.parametrize("raw", [
    "not json",
    '{"UNKNOWN_TASK": {"kind": "ECS_STATE_UNKNOWN"}}',
    '{"NORMALIZE_INVESTOR_INTRADAY": {"kind": "OPEN_ATTEMPT"}}',     # 컨테이너 보류는 report 경로가 아니다
    '{"NORMALIZE_INVESTOR_INTRADAY": "ECS_STATE_UNKNOWN"}',
])
def test_malformed_hold_report_fails_loud(monkeypatch, raw):
    # 보류가 원장에 안 남으면 재처리가 그 작업을 우회한다 — 버리지 않고 report 를 실패시킨다(verdict 가 드러낸다).
    db = FakeOpsDB()
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: _ledger(db))
    monkeypatch.setenv("OPS_RUN_KEY", "investor-intraday:2026-09-22T09:35")
    monkeypatch.setenv("OPS_EXECUTION_HOLDS", raw)
    with pytest.raises(SystemExit):
        entry.reconcile_cli(object())


def test_hold_report_is_recorded_before_the_run_is_reconciled(monkeypatch):
    from data_pipeline.ops import reconciler
    db = FakeOpsDB()
    result = _plan_airflow(db)
    seen = []
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: _ledger(db))
    monkeypatch.setattr(reconciler, "reconcile_run",
                        lambda ledger, **kw: seen.append(len(db.open_issues(states.ISSUE_EXECUTION_HOLD))) or {})
    monkeypatch.setenv("OPS_RUN_KEY", result.run_key)
    monkeypatch.setenv("OPS_EXECUTION_HOLDS", '{"NORMALIZE_INVESTOR_INTRADAY": '
                                              '{"kind": "ECS_STATE_UNKNOWN", "reason": "r"}}')
    assert entry.reconcile_cli(object()) == 0 and seen == [1]


# ── DAG 의 마지막 task·callback 없이도 미확정 실행을 원장에 남긴다(시간 초과·worker 사망) ──
from data_pipeline.ops import reconciler as _rec  # noqa: E402


class _SweepEcs:
    """ListTasks·DescribeTasks 대역 — 태스크 {arn: {lastStatus, exitCode?, stopCode?, command, createdAt}}."""

    def __init__(self, tasks=None, list_error=False):
        self.tasks = tasks or {}
        self.list_error = list_error

    def list_tasks(self, **kw):
        if self.list_error:
            raise OSError("ecs list unavailable")
        return {"taskArns": [a for a, t in self.tasks.items() if t.get("lastStatus") != "STOPPED"]}

    def describe_tasks(self, **kw):
        out = []
        for arn in kw["tasks"]:
            t = self.tasks.get(arn)
            if t is None:
                continue
            c = {k: t[k] for k in ("exitCode",) if k in t}
            task = {"taskArn": arn, "lastStatus": t.get("lastStatus", "RUNNING"), "containers": [c],
                    "createdAt": t.get("createdAt"),
                    "overrides": {"containerOverrides": [{"command": t.get("command", [])}]}}
            if "stopCode" in t:
                task["stopCode"] = t["stopCode"]
            out.append(task)
        return {"tasks": out, "failures": []}


def _age(db, arn, started):
    next(a for a in db.attempts if a["arn"] == arn)["started_at"] = started


def test_unfinished_attempt_past_dag_lifetime_is_recorded_and_released_by_ecs_evidence():
    # DAG 시간 초과로 report 가 돌지 못했다 — 끝나지 않은 시도가 수명(1800초)을 넘으면 원장이 스스로 보류로 남기고,
    # ECS 종료 증거로 닫히면 그 기록도 닫는다(시간이 지났다고 닫지 않는다).
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _start_only(db, run_id, _NORMALIZE, "arn:ecs/n")
    _age(db, "arn:ecs/n", _SLOT)
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), now=_SLOT + timedelta(minutes=40))
    ecs = _SweepEcs({"arn:ecs/n": {"lastStatus": "RUNNING"}})
    reconcile_run(_ledger(db), ecs_client=ecs, **kw)
    [issue] = db.open_issues(states.ISSUE_EXECUTION_HOLD)
    assert issue["dedupe_key"].endswith(":OPEN_ATTEMPT") and issue["evidence"]["kind"] == states.HOLD_OPEN_ATTEMPT
    ecs.tasks["arn:ecs/n"] = {"lastStatus": "STOPPED", "exitCode": 0, "stopCode": "EssentialContainerExited"}
    reconcile_run(_ledger(db), ecs_client=ecs, **kw)
    assert not db.open_issues(states.ISSUE_EXECUTION_HOLD)
    assert db.attempts[-1]["status"] == states.EXEC_SUCCEEDED


def test_young_unfinished_attempt_is_not_recorded_as_a_hold():
    # DAG run 이 아직 살아 있을 수 있다(수명 안) — 도는 중인 정상 실행을 보류로 올리지 않는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _start_only(db, result.pipeline_run_id, _NORMALIZE, "arn:ecs/n")
    _age(db, "arn:ecs/n", _SLOT)
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(),
                  ecs_client=_SweepEcs({"arn:ecs/n": {"lastStatus": "RUNNING"}}), now=_SLOT + timedelta(minutes=10))
    assert not db.open_issues(states.ISSUE_EXECUTION_HOLD)


@pytest.mark.parametrize("ecs_task", [
    {"lastStatus": "STOPPED", "exitCode": 137},        # stopCode 없음 + 신호 — exit 가 원장에 남는다
    {"lastStatus": "STOPPED"},                         # exit 없음 — 다음 대조도 ECS 를 다시 본다
])
def test_killed_attempt_is_recorded_once_as_result_unknown(ecs_task):
    # 매 주기 같은 사실로 재발 카운트를 올리지 않는다 — 이번 대조가 닫은 시도만 기록한다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _start_only(db, result.pipeline_run_id, _NORMALIZE, "arn:ecs/n")
    ecs = _SweepEcs({"arn:ecs/n": ecs_task})
    for _ in range(2):
        reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=ecs,
                      now=_SLOT + timedelta(minutes=40))
    [issue] = db.open_issues(states.ISSUE_EXECUTION_HOLD)
    assert issue["evidence"]["kind"] == states.HOLD_RESULT_UNKNOWN and issue["occurrence_count"] == 1
    assert db.attempts[-1]["exit_code"] == ecs_task.get("exitCode")


def test_sweep_finds_an_untracked_ecs_task_after_the_dag_is_gone_and_the_gate_honours_it(monkeypatch):
    # 시간 초과 당시 PENDING 이던 태스크는 원장에 흔적이 없다 — 주기 점검이 ECS 에서 찾아 ECS_STATE_UNKNOWN 으로
    # 남기고, 같은 작업의 다른 run·그 태스크 자신이 늦게 떠도 업무를 시작하지 않는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    now = _SLOT + timedelta(minutes=45)
    cmd = ["normalize-investor-estimate", "--run-id", run_id, "--input-run-id", run_id]
    ecs = _SweepEcs({
        "arn:ecs/orphan": {"lastStatus": "PENDING", "command": cmd, "createdAt": _SLOT},
        "arn:ecs/young": {"lastStatus": "PENDING", "command": cmd, "createdAt": now - timedelta(minutes=2)},
        "arn:ecs/other-lane": {"lastStatus": "RUNNING", "command": ["load-price-daily"], "createdAt": _SLOT},
    })
    summary = _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=ecs, cluster_arn="c", now=now)
    assert [u["ecs_task_arn"] for u in summary["unrecorded"]] == ["arn:ecs/orphan"]
    [issue] = db.open_issues(states.ISSUE_EXECUTION_HOLD)
    assert issue["evidence"]["kind"] == states.HOLD_ECS_STATE_UNKNOWN
    _exclusive(monkeypatch, skip=False)
    other = _plan_slot(db, _SLOT + timedelta(minutes=30), "b").pipeline_run_id
    for rid, arn in ((other, "arn:ecs/b"), (run_id, "arn:ecs/orphan")):
        calls = []
        assert _run(db, rid, calls, arn=arn) == wrapper.STEP_HELD_EXIT and calls == []


def test_sweep_reconciles_old_slot_runs_and_does_not_guess_when_ecs_listing_fails():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _start_only(db, result.pipeline_run_id, _NORMALIZE, "arn:ecs/n")
    ecs = _SweepEcs({"arn:ecs/n": {"lastStatus": "STOPPED", "exitCode": 0, "stopCode": "EssentialContainerExited"}},
                    list_error=True)
    # 주기 슬롯 대조는 최근 예정일만 본다 — 이 런은 하루 전 슬롯이어도 끝나지 않은 시도가 있으면 훑는다.
    summary = _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=ecs, cluster_arn="c", now=_SLOT + timedelta(days=1))
    assert summary["reconciled"] == [result.run_key] and db.attempts[-1]["status"] == states.EXEC_SUCCEEDED
    assert summary["ecs_listing"] == "failed" and not db.open_issues(states.ISSUE_EXECUTION_HOLD)


def test_sfn_run_tasks_are_not_swept():
    # 원장 밖 태스크 보류는 Airflow 런만 — SFN 은 자기 실행을 기다리고 시도를 원장에 남긴다.
    db = FakeOpsDB()
    result = plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SLOT, pipeline_type=_LANE,
                      sfn_client=FakeSfn())
    cmd = ["normalize-investor-estimate", "--run-id", result.pipeline_run_id]
    ecs = _SweepEcs({"arn:ecs/x": {"lastStatus": "RUNNING", "command": cmd, "createdAt": _SLOT}})
    assert _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=ecs, cluster_arn="c",
                                   now=_SLOT + timedelta(hours=1))["unrecorded"] == []


def _attempt_by(db, run_id, task_key, arn, dag_run):
    _start_only(db, run_id, task_key, arn)
    next(a for a in db.attempts if a["arn"] == arn)["orchestrator_attempt_ref"] = \
        f"airflow:edge_investor_intraday/{dag_run}/normalize/1"


def test_late_report_of_an_older_dag_run_does_not_overwrite_the_newer_state():
    # 재처리 run B 가 업무를 다시 돌리는 중 옛 run A 의 report(재시도)가 늦게 도착했다 — B 의 미귀결을 덮지 않는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    _attempt_by(db, run_id, _NORMALIZE, "arn:ecs/a", "A")
    _attempt_by(db, run_id, _NORMALIZE, "arn:ecs/b", "B")
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(), now=_SLOT + timedelta(minutes=30))
    summary = reconcile_run(_ledger(db), reported_status=states.ORCH_SUCCEEDED,
                            reported_ref="edge_investor_intraday/A", **kw)
    assert summary["stale_report"]["reported"] == "edge_investor_intraday/A"
    assert db.runs[result.run_key]["orchestration_status"] is None
    reconcile_run(_ledger(db), reported_status=states.ORCH_FAILED, reported_ref="edge_investor_intraday/B", **kw)
    assert db.runs[result.run_key]["orchestration_status"] == states.ORCH_FAILED


def test_periodic_reconcile_sweeps_even_without_due_slots(monkeypatch):
    monkeypatch.setattr(entry.aws, "ecs_client", lambda: _SweepEcs())   # CI 엔 AWS 리전이 없다 — 실제 클라이언트 금지
    # 야간·휴장일에도 돈다 — 시간 초과된 run 의 미확정 실행은 슬롯 일정과 무관하다.
    db = FakeOpsDB()
    swept = []
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: _ledger(db))
    monkeypatch.setattr(entry, "_due_slots", lambda _now: [])
    monkeypatch.setattr(_rec, "sweep_airflow_runs", lambda ledger, **kw: swept.append(kw) or {})
    monkeypatch.delenv("OPS_RUN_KEY", raising=False)
    monkeypatch.setenv("OPS_CLUSTER_ARN", "c")
    assert entry.reconcile_cli(object()) == 0
    assert len(swept) == 1 and swept[0]["cluster_arn"] == "c"


def test_hold_report_losing_the_reconcile_lock_is_retried(monkeypatch):
    db = FakeOpsDB(advisory_grants=False)
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: _ledger(db))
    monkeypatch.setenv("OPS_RUN_KEY", "investor-intraday:2026-09-22T09:35")
    monkeypatch.delenv("OPS_ORCHESTRATION_STATUS", raising=False)
    monkeypatch.setenv("OPS_EXECUTION_HOLDS", '{"NORMALIZE_INVESTOR_INTRADAY": {"kind": "ECS_STATE_UNKNOWN"}}')
    assert entry.reconcile_cli(object()) == wrapper.STEP_NOT_RUN_EXIT


def test_a_later_hold_of_another_kind_does_not_lift_the_gate(monkeypatch):
    # 주기 점검이 원장 밖 태스크를 ECS_STATE_UNKNOWN 으로 남긴 뒤, 같은 런의 재처리 report 가 그 작업을 RESULT_UNKNOWN
    # 으로 보고했다. 한 키를 쓰면 evidence 가 덮여 게이트(kind=ECS_STATE_UNKNOWN)가 풀린다 — 종류별 키로 막는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    run_id = result.pipeline_run_id
    cmd = ["normalize-investor-estimate", "--run-id", run_id]
    _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=_SweepEcs({"arn:ecs/o": {"lastStatus": "PENDING", "command": cmd,
                                                                      "createdAt": _SLOT}}),
                            cluster_arn="c", now=_SLOT + timedelta(minutes=45))
    _rec.record_execution_holds(_ledger(db), run_key=result.run_key, holds={
        _NORMALIZE: {"kind": states.HOLD_RESULT_UNKNOWN, "reason": "killed"}})
    kinds = sorted(i["evidence"]["kind"] for i in db.open_issues(states.ISSUE_EXECUTION_HOLD))
    assert kinds == [states.HOLD_ECS_STATE_UNKNOWN, states.HOLD_RESULT_UNKNOWN]
    _exclusive(monkeypatch, skip=False)
    assert _run(db, run_id, [], arn="arn:ecs/x") == wrapper.STEP_HELD_EXIT


def test_report_and_reconciler_record_one_result_unknown_for_one_killed_container():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _start_only(db, result.pipeline_run_id, _NORMALIZE, "arn:ecs/n")
    _rec.record_execution_holds(_ledger(db), run_key=result.run_key, holds={
        _NORMALIZE: {"kind": states.HOLD_RESULT_UNKNOWN, "reason": "killed"}})
    reconcile_run(_ledger(db), run_key=result.run_key, sfn_client=_NoSfn(), now=_SLOT + timedelta(minutes=40),
                  ecs_client=_SweepEcs({"arn:ecs/n": {"lastStatus": "STOPPED", "exitCode": 137}}))
    assert len(db.open_issues(states.ISSUE_EXECUTION_HOLD)) == 1


def test_unfinished_record_is_closed_even_when_the_attempt_ended_by_itself():
    # 수명을 넘긴 시도가 기록된 뒤 컨테이너가 스스로 끝을 기록했다(원장 RUNNING 없음). 과거 슬롯 런이라 슬롯 대조가
    # 안 본다 — 열린 기록이 있는 런도 점검이 훑어 닫는다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _start_only(db, result.pipeline_run_id, _NORMALIZE, "arn:ecs/n")
    _age(db, "arn:ecs/n", _SLOT)
    ecs = _SweepEcs({"arn:ecs/n": {"lastStatus": "RUNNING"}})
    _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=ecs, cluster_arn=None, now=_SLOT + timedelta(minutes=40))
    assert db.open_issues(states.ISSUE_EXECUTION_HOLD)
    a = db.attempts[-1]
    a["status"], a["exit_code"] = states.EXEC_SUCCEEDED, 0          # wrapper 가 스스로 끝을 기록
    summary = _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=ecs, cluster_arn=None, now=_SLOT + timedelta(days=1))
    assert summary["reconciled"] == [result.run_key] and not db.open_issues(states.ISSUE_EXECUTION_HOLD)


def test_sweep_skips_runs_the_slot_reconcile_already_saw():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _start_only(db, result.pipeline_run_id, _NORMALIZE, "arn:ecs/n")
    summary = _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=_SweepEcs(), cluster_arn=None, now=_SLOT,
                                      skip_run_keys=frozenset({result.run_key}))
    assert summary["reconciled"] == []


class _PagedEcs(_SweepEcs):
    """ListTasks 를 2쪽으로 나누고, DescribeTasks 는 100개를 넘으면 실제 ECS 처럼 거부한다."""

    def __init__(self, tasks):
        super().__init__(tasks)
        self.describe_sizes = []

    def list_tasks(self, **kw):
        arns = sorted(self.tasks)
        half = len(arns) // 2
        if kw.get("nextToken") == "p2":
            return {"taskArns": arns[half:]}
        return {"taskArns": arns[:half], "nextToken": "p2"}

    def describe_tasks(self, **kw):
        if len(kw["tasks"]) > 100:
            raise ValueError("DescribeTasks accepts at most 100 tasks")
        self.describe_sizes.append(len(kw["tasks"]))
        return super().describe_tasks(**kw)


def test_sweep_reads_every_page_and_describes_in_chunks_of_100():
    db = FakeOpsDB()
    result = _plan_airflow(db)
    cmd = ["normalize-investor-estimate", "--run-id", result.pipeline_run_id]
    tasks = {f"arn:ecs/other{i:03d}": {"lastStatus": "RUNNING", "command": ["load-price-daily"], "createdAt": _SLOT}
             for i in range(230)}
    tasks["arn:ecs/zz-orphan"] = {"lastStatus": "RUNNING", "command": cmd, "createdAt": _SLOT}   # 둘째 쪽 끝
    ecs = _PagedEcs(tasks)
    summary = _rec.sweep_airflow_runs(_ledger(db), sfn_client=_NoSfn(), ecs=ecs, cluster_arn="c", now=_SLOT + timedelta(hours=1))
    assert [u["ecs_task_arn"] for u in summary["unrecorded"]] == ["arn:ecs/zz-orphan"]
    assert sorted(ecs.describe_sizes) == [31, 100, 100]


def test_sweep_failure_fails_the_periodic_run_after_slot_reconciliation(monkeypatch):
    monkeypatch.setattr(entry.aws, "ecs_client", lambda: _SweepEcs())   # CI 엔 AWS 리전이 없다 — 실제 클라이언트 금지
    # 슬롯 대조는 끝난 뒤다 — 원장 밖 실행을 확인하지 못한 것을 exit 0 으로 숨기지 않는다.
    db = FakeOpsDB()
    reconciled = []
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: _ledger(db))
    monkeypatch.setattr(entry, "_due_slots", lambda _now: [("investor-intraday:2026-09-22T09:35", False)])
    monkeypatch.setattr(_rec, "reconcile_run", lambda ledger, **kw: reconciled.append(kw["run_key"]) or {})
    monkeypatch.setattr(_rec, "sweep_airflow_runs", lambda ledger, **kw: {"errors": ["ecs listing failed"]})
    monkeypatch.delenv("OPS_RUN_KEY", raising=False)
    assert entry.reconcile_cli(object()) == 1
    assert reconciled == ["investor-intraday:2026-09-22T09:35"]


def test_report_of_a_newer_run_that_did_no_business_does_not_replace_the_slot_verdict():
    # 새 재처리 run 이 업무를 하나도 못 돌렸다(76·75·제출 전 실패) — 슬롯 데이터는 앞 run 의 결과 그대로다.
    db = FakeOpsDB()
    result = _plan_airflow(db)
    _record(db, result.pipeline_run_id, _NORMALIZE, arn="arn:ecs/a", exit_code=0)
    next(a for a in db.attempts if a["arn"] == "arn:ecs/a")["orchestrator_attempt_ref"] = \
        "airflow:edge_investor_intraday/A/normalize/1"
    kw = dict(run_key=result.run_key, sfn_client=_NoSfn(), ecs_client=FakeEcs(), now=_SLOT + timedelta(minutes=30))
    reconcile_run(_ledger(db), reported_status=states.ORCH_SUCCEEDED, reported_ref="edge_investor_intraday/A", **kw)
    summary = reconcile_run(_ledger(db), reported_status=states.ORCH_FAILED,
                            reported_ref="edge_investor_intraday/B", **kw)
    assert "stale_report" in summary and db.runs[result.run_key]["orchestration_status"] == states.ORCH_SUCCEEDED
