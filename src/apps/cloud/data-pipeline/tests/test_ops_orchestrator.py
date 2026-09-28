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
