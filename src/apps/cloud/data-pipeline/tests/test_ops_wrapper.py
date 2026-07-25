"""Wrapper instrumentation + data_status 테스트 (ALPHA-530) — 스펙 §9 시나리오 6·21·22 + D."""

from __future__ import annotations

import pytest

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
    # VALID 는 완전성 확인(expected+received) 이 있어야 한다 — 없으면 정직하게 UNKNOWN.
    assert d({"exit_code": 0, "records_out": 5}) == states.DATA_UNKNOWN
    assert d({"exit_code": 0, "records_out": 5, "expected_count": 5,
              "received_count": 5}) == states.DATA_VALID
    assert d({"exit_code": 0, "records_out": 30, "expected_count": 31,
              "received_count": 30}) == states.DATA_INCOMPLETE               # 종목 누락
    assert d({"exit_code": 0, "records_out": 5, "failed_records": 2}) == states.DATA_INCOMPLETE
    # 음수·NaN·비수치 records_out, 비수치 failed → VALID 승격/ crash 없이 UNKNOWN.
    assert d({"exit_code": 0, "records_out": -1}) == states.DATA_UNKNOWN
    assert d({"exit_code": 0, "records_out": float("nan")}) == states.DATA_UNKNOWN
    assert d({"exit_code": 0, "records_out": 5, "failed_records": "x"}) == states.DATA_UNKNOWN


def test_bool_exit_code_is_not_success():
    """exit_code=False 는 성공 0 이 아니다 — False==0 우회를 막는다(edge-review H)."""
    assert d({"exit_code": False, "records_out": 5, "expected_count": 5,
              "received_count": 5}) == states.DATA_UNKNOWN


# ── instrument ──
def test_instrument_records_attempt_and_fulfilled():
    db = FakeOpsDB()
    _seed(db)
    rc = wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {"records_out": 10, "expected_count": 10,
                                    "received_count": 10, "request_completed": True},
    )
    assert rc == 0
    assert db.etasks_by_id["et1"]["task_outcome"] == states.OUTCOME_FULFILLED
    assert db.etasks_by_id["et1"]["data_status"] == states.DATA_VALID
    assert len(db.attempts) == 1 and db.attempts[0]["status"] == states.EXEC_SUCCEEDED


def test_step_exception_closes_the_attempt_instead_of_leaving_it_running():
    # WHY: attempt 를 RUNNING 으로 남기면 Reconciler 가 **이미 끝난 실행을 STALLED** 로 오판하고,
    #      STALLED 는 resolve 경로가 없어 영구 OPEN 으로 쌓인다. 계측이 dispatch 전체를 감싸면서
    #      인자 검증 SystemExit 까지 이 경로로 들어온다(SystemExit 은 Exception 이 아니다).
    #      예외 자체는 삼키지 않고 그대로 전파해야 한다 — 계측이 흐름을 바꾸면 안 된다.
    db = FakeOpsDB()
    _seed(db)

    def _boom():
        raise SystemExit("KIS 가격은 --from 없이 --to 만 지정할 수 없다")

    with pytest.raises(SystemExit):
        wrapper.instrument(_boom, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
                           ecs_task_arn="arn:task/1")
    assert len(db.attempts) == 1
    assert db.attempts[0]["status"] == states.EXEC_FAILED     # RUNNING 으로 안 남는다
    assert db.etasks_by_id["et1"]["task_outcome"] == states.OUTCOME_FAILED
    assert db.etasks_by_id["et1"]["data_status"] == states.DATA_UNKNOWN


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


def test_instrument_skips_attempt_for_skipped_task():
    """SKIPPED 작업(비거래일 등)은 SFN 이 그날 돌아 컨테이너가 떠도 attempt 를 안 만든다."""
    db = FakeOpsDB()
    _seed(db)
    db.etasks_by_id["et1"]["plan_status"] = "SKIPPED"
    rc = wrapper.instrument(lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R",
                            ledger=_ledger(db), ecs_task_arn="arn:task/1")
    assert rc == 0
    assert db.attempts == []
    assert db.etasks_by_id["et1"]["task_outcome"] == "PENDING"  # 안 건드림


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
