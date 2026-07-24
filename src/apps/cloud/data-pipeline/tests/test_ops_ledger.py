"""Ledger 멱등·복구 테스트 (ALPHA-530) — 스펙 §9 시나리오 6·8·14·17·18·9(ARN 없음)."""

from __future__ import annotations

from data_pipeline.config import DbConfig
from data_pipeline.ops import states
from data_pipeline.ops.ledger import Ledger

from opsfakes import FakeOpsDB

_DB = DbConfig(password="x")


def _ledger(db, **kw):
    return Ledger(db=_DB, connect_fn=db.connect, **kw)


def test_pipeline_run_idempotent_on_run_key():
    db = FakeOpsDB()
    ledger = _ledger(db)
    args = dict(run_key="etf-daily:2026-07-24", execution_name="etf-daily-2026-07-24",
                pipeline_type="etf-daily", schedule_slot="s", trading_date="2026-07-24",
                hard_deadline_at=None, catalog_version="v", catalog_content_hash="h",
                image_digest=None, input_hash="ih", expected_execution_arn="arn:x")
    id1, c1 = ledger.create_pipeline_run(**args)
    id2, c2 = ledger.create_pipeline_run(**args)
    assert c1 is True and c2 is False and id1 == id2
    assert len(db.runs) == 1


def test_attempt_requires_ecs_arn_no_phantom_row():
    """시나리오 9(원장 측) — ECS ARN 없는 시도는 attempt 행을 만들지 않는다."""
    db = FakeOpsDB()
    assert _ledger(db).record_attempt_start(expected_task_id="et1", ecs_task_arn="") is None
    assert db.attempts == []


def test_attempt_idempotent_by_ecs_arn():
    """시나리오 8 — 같은 (expected_task, ECS ARN) 재기록은 attempt 1개."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    a1 = ledger.record_attempt_start(expected_task_id="et1", ecs_task_arn="arn:task/1")
    a2 = ledger.record_attempt_start(expected_task_id="et1", ecs_task_arn="arn:task/1")
    assert a1 == a2
    assert len([a for a in db.attempts if a["etid"] == "et1"]) == 1


def test_backfill_is_idempotent():
    """시나리오 14(원장 측) — backfill 이 기존 attempt 를 중복 생성하지 않는다."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    ledger.record_attempt_start(expected_task_id="et1", ecs_task_arn="arn:task/1")
    bid = ledger.backfill_attempt(expected_task_id="et1", ecs_task_arn="arn:task/1",
                                  execution_status=states.EXEC_SUCCEEDED)
    assert len([a for a in db.attempts if a["etid"] == "et1"]) == 1
    assert bid is not None


def test_issue_open_dedupe_and_occurrence_count():
    """시나리오 17 — 열린 이슈 중복 방지 + occurrence_count 증가."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    i1, c1 = ledger.open_or_bump_issue(issue_type=states.ISSUE_MISSED, dedupe_key="d1")
    i2, c2 = ledger.open_or_bump_issue(issue_type=states.ISSUE_MISSED, dedupe_key="d1")
    assert c1 is True and c2 is False and i1 == i2
    assert len(db.open_issues()) == 1
    assert db.issues[0]["occurrence_count"] == 2


def test_issue_reopens_after_resolve():
    """시나리오 18 — 해결 후 동일 문제 재발 시 새 이슈."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    i1, _ = ledger.open_or_bump_issue(issue_type=states.ISSUE_MISSED, dedupe_key="d1")
    assert ledger.resolve_issue("d1", resolution_reason="fixed", resolution_source="test") is True
    i2, c2 = ledger.open_or_bump_issue(issue_type=states.ISSUE_MISSED, dedupe_key="d1")
    assert c2 is True and i2 != i1
    assert len(db.open_issues()) == 1
    assert len(db.issues) == 2  # 하나는 RESOLVED, 하나는 새 OPEN


def test_attempt_start_bounded_backoff_returns_none_on_persistent_failure():
    """시나리오 6(메커니즘) — 원장 DB 가 계속 실패해도 예외 없이 None(본 작업 진행)."""
    clock = iter([0.0, 100.0, 200.0])

    def raising(_db):
        raise RuntimeError("db down")

    ledger = Ledger(db=_DB, connect_fn=raising, sleep_fn=lambda _s: None,
                    clock_fn=lambda: next(clock))
    result = ledger.record_attempt_start(expected_task_id="et1", ecs_task_arn="arn:task/1")
    assert result is None  # 던지지 않고 None — 호출부(wrapper)가 본 작업을 계속한다
