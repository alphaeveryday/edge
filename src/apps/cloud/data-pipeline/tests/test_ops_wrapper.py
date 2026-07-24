"""Wrapper instrumentation + data_status 테스트 (ALPHA-530) — 스펙 §9 시나리오 6·21·22 + D."""

from __future__ import annotations

from data_pipeline.config import DbConfig
from data_pipeline.ops import states, wrapper
from data_pipeline.ops.ledger import Ledger

from opsfakes import FakeOpsDB

_DB = DbConfig(password="x")
d = wrapper.derive_data_status


def _ledger(db):
    return Ledger(db=_DB, connect_fn=db.connect)


def _seed(db, run_id="R", task_key="LOAD_PRICE_DAILY", etid="et1"):
    row = {"expected_task_id": etid, "pipeline_run_id": run_id, "task_key": task_key,
           "plan_status": "DUE", "task_outcome": "PENDING", "data_status": "UNKNOWN",
           "required": True, "missed_at": None, "fulfilled_at": None, "blocked_at": None,
           "outcome_reason": None, "current_attempt_id": None, "completeness": None,
           "stage": "feature", "dataset": "price_daily", "eligible_at": None,
           "deadline_at": None}
    db.etasks[(run_id, task_key)] = row
    db.etasks_by_id[etid] = row
    return etid


# ── derive_data_status (스펙 §3.3·§6) ──
def test_exit0_alone_is_not_valid():
    """시나리오 21 — 성공 exit code 만으로 VALID 를 기록하지 않는다."""
    assert d({"exit_code": 0}) == states.DATA_UNKNOWN


def test_zero_count_alone_is_not_valid_empty():
    """시나리오 22 — 명시적 0건 응답만으로 VALID_EMPTY 를 기록하지 않는다."""
    assert d({"exit_code": 0, "records_out": 0}) == states.DATA_UNKNOWN
    # 4증명(요청완료·계약허용·거래일무모순)이 모두 있을 때만 VALID_EMPTY.
    assert d({"exit_code": 0, "records_out": 0, "request_completed": True,
              "empty_allowed": True, "trading_day": True}) == states.DATA_VALID_EMPTY


def test_data_status_rules():
    assert d({"exit_code": 1, "records_out": 5}) == states.DATA_UNKNOWN       # 실패→단정 안 함
    assert d({"exit_code": 0, "records_out": 5}) == states.DATA_VALID
    assert d({"exit_code": 0, "records_out": 30, "expected_count": 31,
              "received_count": 30}) == states.DATA_INCOMPLETE               # 종목 누락
    assert d({"exit_code": 0, "records_out": 5, "failed_records": 2}) == states.DATA_INCOMPLETE


# ── instrument ──
def test_instrument_records_attempt_and_fulfilled():
    db = FakeOpsDB()
    _seed(db)
    rc = wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {"records_out": 10, "request_completed": True},
    )
    assert rc == 0
    assert db.etasks_by_id["et1"]["task_outcome"] == states.OUTCOME_FULFILLED
    assert db.etasks_by_id["et1"]["data_status"] == states.DATA_VALID
    assert len(db.attempts) == 1 and db.attempts[0]["status"] == states.EXEC_SUCCEEDED


def test_instrument_incomplete_data_keeps_outcome_fulfilled():
    """시나리오 D — 종목 누락(INCOMPLETE)이어도 실행 성공이면 attempt/outcome 은 실패 아님."""
    db = FakeOpsDB()
    _seed(db)
    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {"records_out": 30, "expected_count": 31, "received_count": 30},
    )
    row = db.etasks_by_id["et1"]
    assert row["task_outcome"] == states.OUTCOME_FULFILLED     # 실행 성공(축 분리)
    assert row["data_status"] == states.DATA_INCOMPLETE        # 데이터는 불완전
    assert db.attempts[0]["status"] == states.EXEC_SUCCEEDED   # attempt 실패로 안 바꾼다


def test_instrument_passthrough_when_no_ledger():
    assert wrapper.instrument(lambda: 7, task_key="X", run_id="R", ledger=None) == 7


def test_instrument_passthrough_when_unregistered():
    db = FakeOpsDB()
    assert wrapper.instrument(lambda: 0, task_key="X", run_id="R", ledger=_ledger(db)) == 0
    assert db.attempts == []


def test_instrument_continues_when_ledger_down():
    """시나리오 6(엔드투엔드) — 원장 DB 장애에도 본 작업이 실행되고 exit code 가 보존된다."""
    db = FakeOpsDB()
    db.fail = True
    ran = []
    rc = wrapper.instrument(
        lambda: (ran.append(1), 0)[1], task_key="LOAD_PRICE_DAILY", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/1",
    )
    assert rc == 0 and ran == [1]
