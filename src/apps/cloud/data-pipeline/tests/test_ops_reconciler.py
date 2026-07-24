"""Reconciler 증거규칙 테스트 (ALPHA-530) — 스펙 §9 시나리오 7·9·10·11·12·13·14·15·16·19·20."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.ops import states
from data_pipeline.ops.ledger import Ledger
from data_pipeline.ops.reconciler import detect_planner_missing, reconcile_run

from opsfakes import FakeEcs, FakeOpsDB, FakeSfn

_DB = DbConfig(password="x")
_RID = "run_X"
_RUN_KEY = "etf-daily:2026-07-24"
_NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
_PAST = "2026-07-24T01:00:00+00:00"
_OLD = "2026-07-24T00:00:00+00:00"
_FUTURE = "2026-07-24T20:00:00+00:00"


def _ledger(db):
    return Ledger(db=_DB, connect_fn=db.connect)


def _seed(db, tasks, *, hard_deadline=None):
    db.runs[_RUN_KEY] = db.runs_by_id[_RID] = {
        "pipeline_run_id": _RID, "run_key": _RUN_KEY, "execution_name": "etf-daily-2026-07-24",
        "expected_execution_arn": "arn:exec", "sfn_execution_arn": None,
        "launch_status": "LAUNCHED", "orchestration_status": None,
        "hard_deadline_at": hard_deadline, "trading_date": "2026-07-24"}
    for t in tasks:
        row = {"pipeline_run_id": _RID, "stage": "raw", "plan_status": "DUE",
               "task_outcome": "PENDING", "data_status": "UNKNOWN", "required": True,
               "eligible_at": None, "deadline_at": None, "missed_at": None,
               "fulfilled_at": None, "blocked_at": None, "outcome_reason": None,
               "current_attempt_id": None, "completeness": None, **t}
        db.etasks[(_RID, t["task_key"])] = db.etasks_by_id[t["expected_task_id"]] = row


def _entered(state, *, arn=None, succeeded=False, submit_failed=False):
    events = [{"type": "TaskStateEntered", "stateEnteredEventDetails": {"name": state}}]
    if arn:
        events.append({"type": "TaskSubmitted",
                       "taskSubmittedEventDetails": {"output": json.dumps({"TaskArn": arn})}})
    if succeeded:
        events.append({"type": "TaskSucceeded", "taskSucceededEventDetails": {"output": "{}"}})
    if submit_failed:
        events.append({"type": "TaskSubmitFailed", "taskSubmitFailedEventDetails": {}})
    return events


def _reconcile(db, *, history=None, ecs=None):
    return reconcile_run(
        _ledger(db), run_key=_RUN_KEY, now=_NOW,
        sfn_client=FakeSfn(history=history or [], describe={"status": "RUNNING"}),
        ecs_client=ecs or FakeEcs())


def test_missed_when_state_not_entered_eligible_and_past_deadline():
    """시나리오 10 — 미진입 + eligible + deadline 초과 → MISSED."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    _reconcile(db)
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_MISSED
    assert db.etasks_by_id["e1"]["missed_at"] == "SET"
    assert len(db.open_issues(states.ISSUE_MISSED)) == 1


def test_blocked_not_missed_when_never_eligible_past_hard_deadline():
    """시나리오 11 — 끝까지 eligible 하지 못함(upstream 미완) → MISSED 아니라 BLOCKED."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "LOAD_PRICE_DAILY", "expected_task_id": "e1", "stage": "feature",
                "eligible_at": None, "deadline_at": _PAST}], hard_deadline=_PAST)
    _reconcile(db)
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_BLOCKED
    assert db.etasks_by_id["e1"]["blocked_at"] == "SET"
    assert len(db.open_issues(states.ISSUE_MISSED)) == 0


def test_running_over_time_is_stalled_execution_status_preserved():
    """시나리오 12 — RUNNING 시간 초과: STALLED(health)만, execution_status 유지."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    db.attempts.append({"attempt_id": "a1", "etid": "e1", "arn": "arn:task/kis",
                        "status": states.EXEC_RUNNING, "exit_code": None, "source": "WRAPPER",
                        "started_at": _OLD})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "RUNNING"}}))
    assert db.attempts[0]["status"] == states.EXEC_RUNNING     # 뒤집지 않는다
    assert len(db.open_issues(states.ISSUE_STALLED)) == 1


def test_confirmed_ecs_stopped_transitions_running_attempt():
    """시나리오 13·7 — ECS STOPPED 증거가 있을 때만 상태 확정(종료 UPDATE 누락 복구)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    db.attempts.append({"attempt_id": "a1", "etid": "e1", "arn": "arn:task/kis",
                        "status": states.EXEC_RUNNING, "exit_code": None, "source": "WRAPPER",
                        "started_at": _OLD})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "STOPPED", "exitCode": 0}}))
    assert db.attempts[0]["status"] == states.EXEC_SUCCEEDED


def test_ledger_gap_backfills_attempt_and_opens_issue():
    """시나리오 14 — ECS ARN 있고 attempt 없음 → backfill + LEDGER_GAP."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "RUNNING"}}))
    assert any(a["etid"] == "e1" and a["arn"] == "arn:task/kis" for a in db.attempts)
    assert len(db.open_issues(states.ISSUE_LEDGER_GAP)) == 1


def test_evidence_lost_when_entered_but_no_ecs_task_confirmed():
    """시나리오 15 — 진입했으나 ECS 생성 확인 불가 → MISSED 단정 안 함, EVIDENCE_LOST."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    _reconcile(db, history=_entered("CollectKisPrice"))  # arn 없음
    assert db.etasks_by_id["e1"]["task_outcome"] != states.OUTCOME_MISSED
    assert len(db.open_issues(states.ISSUE_EVIDENCE_LOST)) == 1


def test_failed_to_start_no_phantom_attempt():
    """시나리오 9 — RunTask submit 실패 + ARN 없음 → outcome FAILED/FAILED_TO_START, attempt 없음."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_entered("CollectKisPrice", submit_failed=True))
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_FAILED
    assert db.etasks_by_id["e1"]["outcome_reason"] == states.REASON_FAILED_TO_START
    assert [a for a in db.attempts if a["etid"] == "e1"] == []


def test_missed_unlatches_to_fulfilled_on_late_success():
    """시나리오 16 — MISSED 후 늦은 성공 → FULFILLED, missed_at 보존, MISSED 이슈 RESOLVED."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "task_outcome": states.OUTCOME_MISSED, "missed_at": "SET", "eligible_at": _OLD}])
    db.issues.append({"issue_id": "iss1", "issue_type": states.ISSUE_MISSED,
                      "dedupe_key": f"missed:{_RID}:PRICE_COLLECTION_KIS", "status": "OPEN",
                      "occurrence_count": 1, "scope": "task", "scope_key": "e1", "evidence": None})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis", succeeded=True))
    row = db.etasks_by_id["e1"]
    assert row["task_outcome"] == states.OUTCOME_FULFILLED
    assert row["missed_at"] == "SET"                            # 보존
    assert db.issues[0]["status"] == "RESOLVED"


def test_hard_deadline_terminates_eligible_pending_as_missed():
    """시나리오 20 — hard deadline 뒤 eligible-but-PENDING 은 MISSED 로 종결(무한 PENDING 금지)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _FUTURE}], hard_deadline=_PAST)
    _reconcile(db)
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_MISSED


def test_planner_missing_when_slot_has_no_run():
    """시나리오 19 — schedule 상 있어야 할 run_key 부재 → PLANNER_MISSING."""
    db = FakeOpsDB()
    missing = detect_planner_missing(_ledger(db), expected_run_keys=["etf-daily:2026-07-25"])
    assert missing == ["etf-daily:2026-07-25"]
    assert len(db.open_issues(states.ISSUE_PLANNER_MISSING)) == 1
