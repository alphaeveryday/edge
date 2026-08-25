"""Wrapper instrumentation + data_status 테스트 (ALPHA-530) — 스펙 §9 시나리오 6·21·22 + D."""

from __future__ import annotations

import os

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.ops import contracts, states, wrapper
from data_pipeline.ops.ledger import Ledger

from opsfakes import FakeOpsDB

_DB = DbConfig(password="x")
d = wrapper.derive_data_status


def _ledger(db):
    return Ledger(db=_DB, connect_fn=db.connect)


def _attempt_id(db):
    return db.attempts[-1]["attempt_id"]


def _seed(
    db, run_id="R", task_key="LOAD_PRICE_DAILY", etid="et1", expected_count=None,
    contract_key=None,
):
    snapshot_id = None
    if expected_count is not None:
        snapshot_id = f"snap-{etid}"
        db.snapshots.append({
            "id": snapshot_id,
            "run_id": run_id,
            "task_key": task_key,
            "expected_entity_count": expected_count,
            "entity_ids": None,
        })
    row = {"expected_task_id": etid, "pipeline_run_id": run_id, "task_key": task_key,
           "plan_status": "DUE", "task_outcome": "PENDING", "data_status": "UNKNOWN",
           "required": True, "missed_at": None, "fulfilled_at": None, "blocked_at": None,
           "outcome_reason": None, "current_attempt_id": None, "completeness": None,
           "records_out": None, "unsupported_records": None, "failed_records": None,
           "entity_resolution_arguments_total": None,
           "entity_resolution_arguments_resolved": None,
           "dataset_contract_key": contract_key,
           "freshness_status": states.FRESHNESS_UNKNOWN if contract_key else None,
           "freshness_reason": states.FRESHNESS_EVIDENCE_MISSING if contract_key else None,
           "actual_as_of_date": None, "collected_at": None, "observed_at": None,
           "freshness_evidence": None,
           "stage": "feature", "dataset": "price_daily", "eligible_at": None,
           "deadline_at": None, "expectation_snapshot_id": snapshot_id}
    db.etasks[(run_id, task_key)] = row
    db.etasks_by_id[etid] = row
    return etid


def test_krx_artifact_records_unverified_freshness_without_monitor_time():
    """WHY: KRX 요청일은 actual-as-of 증거가 아니지만, 저장된 로그의 존재 자체는 수집 시각이다."""
    db = FakeOpsDB()
    _seed(
        db, task_key="ETF_HOLDINGS_COLLECTION_KRX",
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    )

    wrapper.instrument(
        lambda: 0, task_key="ETF_HOLDINGS_COLLECTION_KRX", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/krx",
        observe_data_fn=lambda ec: {
            "artifact_observed": True, "records_out": 10, "failed_records": 0,
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["actual_as_of_date"] is None
    assert row["collected_at"] == "SET"
    assert row["observed_at"] is None
    assert row["freshness_status"] == states.FRESHNESS_UNKNOWN
    assert row["freshness_reason"] == states.FRESHNESS_ACTUAL_AS_OF_UNVERIFIED
    assert row["freshness_evidence"] is None


def test_krx_trd_dd_matching_expected_as_of_never_promotes_fresh():
    """WHY: trd_dd 는 우리가 보낸 요청값이라 expected_as_of 와 같은 달력 규칙에서 나온다 —
    이 일치는 증거가 아니라 동어반복이다. 이걸 actual 로 승격하면 달력 오류 하나로 거짓
    FRESH 가 나온다(완료조건 ②, ALPHA-653: KRX 응답에는 기준일 필드가 없다)."""
    db = FakeOpsDB()
    _seed(
        db, task_key="ETF_HOLDINGS_COLLECTION_KRX",
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    )
    db.etasks_by_id["et1"]["expected_as_of_date"] = "2026-07-24"

    wrapper.instrument(
        lambda: 0, task_key="ETF_HOLDINGS_COLLECTION_KRX", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/krx",
        observe_data_fn=lambda ec: {
            "artifact_observed": True, "records_out": 10, "failed_records": 0,
            # 요청값이 기대 기준일과 정확히 일치하는 관측 신호 — 그래도 승격 금지
            "trd_dd": "2026-07-24", "actual_as_of_date": "2026-07-24",
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["actual_as_of_date"] is None
    assert row["freshness_status"] == states.FRESHNESS_UNKNOWN
    assert row["freshness_reason"] == states.FRESHNESS_ACTUAL_AS_OF_UNVERIFIED


def test_krx_without_artifact_keeps_planner_evidence_missing():
    """WHY: exit code만으로 immutable 산출물이 생겼다고 추정하면 collected_at이 거짓 시각이 된다."""
    db = FakeOpsDB()
    _seed(
        db, task_key="ETF_HOLDINGS_COLLECTION_KRX",
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    )

    wrapper.instrument(
        lambda: 0, task_key="ETF_HOLDINGS_COLLECTION_KRX", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/krx",
    )

    row = db.etasks_by_id["et1"]
    assert row["collected_at"] is None
    assert row["freshness_reason"] == states.FRESHNESS_EVIDENCE_MISSING


def test_kis_nav_uses_latest_vendor_trade_date_as_actual_as_of():
    """WHY: NAV 날짜창에는 여러 거래일이 정상적으로 섞인다. 요청일이 아니라 KIS 응답의
    stck_bsop_date 최댓값이 이 전달 경계가 실제로 확보한 최신 업무 기준일이다."""
    db = FakeOpsDB()
    _seed(db, task_key="NAV_COLLECTION_KIS", contract_key=contracts.ETF_NAV_KIS_DAILY)
    db.etasks_by_id["et1"]["expected_as_of_date"] = "2026-07-24"

    wrapper.instrument(
        lambda: 0, task_key="NAV_COLLECTION_KIS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/nav", observe_data_fn=lambda ec: {
            "artifact_observed": True, "records_out": 2, "failed_records": 0,
            "actual_as_of_values": ["20260723", "20260724"],
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["actual_as_of_date"] == "2026-07-24"
    assert row["freshness_status"] == states.FRESHNESS_FRESH
    assert row["observed_at"] == "SET"
    assert row["freshness_evidence"]["source_field"] == "stck_bsop_date"


def test_kis_nav_invalid_vendor_dates_preserve_unknown():
    """WHY: 형식이 깨진 벤더 날짜를 요청일이나 실행일로 메우면 거짓 FRESH가 된다."""
    db = FakeOpsDB()
    _seed(db, task_key="NAV_COLLECTION_KIS", contract_key=contracts.ETF_NAV_KIS_DAILY)
    db.etasks_by_id["et1"]["expected_as_of_date"] = "2026-07-24"

    wrapper.instrument(
        lambda: 0, task_key="NAV_COLLECTION_KIS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/nav", observe_data_fn=lambda ec: {
            "artifact_observed": True, "records_out": 1, "failed_records": 0,
            # strptime은 6~7자리도 관대하게 받으므로 정확한 8자리 검증이 따로 필요하다.
            "actual_as_of_values": ["202681"],
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["actual_as_of_date"] is None
    assert row["freshness_status"] == states.FRESHNESS_UNKNOWN


def test_kis_nav_partial_date_evidence_preserves_unknown():
    """WHY: 저장 행 하나라도 기준일이 없으면 나머지 정상 날짜만으로 검증 완료할 수 없다."""
    db = FakeOpsDB()
    _seed(db, task_key="NAV_COLLECTION_KIS", contract_key=contracts.ETF_NAV_KIS_DAILY)
    db.etasks_by_id["et1"]["expected_as_of_date"] = "2026-07-24"

    wrapper.instrument(
        lambda: 0, task_key="NAV_COLLECTION_KIS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/nav", observe_data_fn=lambda ec: {
            "artifact_observed": True, "records_out": 2, "failed_records": 0,
            "actual_as_of_values": ["20260724", None],
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["actual_as_of_date"] is None
    assert row["freshness_status"] == states.FRESHNESS_UNKNOWN


def test_unobserved_retry_resets_prior_collection_evidence():
    """WHY: 재시도는 같은 raw 키를 덮어쓴다 — 로그를 못 남기고 죽은 재시도가 앞 시도의
    collected_at 을 물려받으면, 옛 수집 시각이 바뀐 raw 객체의 증거로 남는다(관대한 방향).
    미관측 시도는 EVIDENCE_MISSING 으로 리셋해야 한다."""
    db = FakeOpsDB()
    _seed(
        db, task_key="ETF_HOLDINGS_COLLECTION_KRX",
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    )
    # 앞 시도가 남긴 수집 증거
    prior = db.etasks_by_id["et1"]
    prior["collected_at"] = "SET"
    prior["freshness_reason"] = states.FRESHNESS_ACTUAL_AS_OF_UNVERIFIED

    wrapper.instrument(
        lambda: 1, task_key="ETF_HOLDINGS_COLLECTION_KRX", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/krx",
        observe_data_fn=lambda ec: {"artifact_observed": None},
    )

    row = db.etasks_by_id["et1"]
    assert row["collected_at"] is None
    assert row["freshness_status"] == states.FRESHNESS_UNKNOWN
    assert row["freshness_reason"] == states.FRESHNESS_EVIDENCE_MISSING


def test_exception_attempt_also_resets_prior_collection_evidence():
    """WHY: 예외로 죽은 재시도도 raw 를 이미 덮어썼을 수 있다 — 정상 종료 경로만 리셋하면
    같은 함정이 예외 경로에 한 계층 남는다(리뷰 3라운드)."""
    db = FakeOpsDB()
    _seed(
        db, task_key="ETF_HOLDINGS_COLLECTION_KRX",
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    )
    prior = db.etasks_by_id["et1"]
    prior["collected_at"] = "SET"
    prior["freshness_reason"] = states.FRESHNESS_ACTUAL_AS_OF_UNVERIFIED

    def boom():
        raise RuntimeError("mid-write crash")

    with pytest.raises(RuntimeError):
        wrapper.instrument(
            boom, task_key="ETF_HOLDINGS_COLLECTION_KRX", run_id="R",
            ledger=_ledger(db), ecs_task_arn="arn:task/krx",
        )

    row = db.etasks_by_id["et1"]
    assert row["collected_at"] is None
    assert row["freshness_reason"] == states.FRESHNESS_EVIDENCE_MISSING


def test_uncontracted_artifact_does_not_gain_freshness():
    """WHY: 계약 미연결 작업의 NULL은 UNKNOWN이 아니라 NOT_APPLICABLE이다."""
    db = FakeOpsDB()
    _seed(db)

    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/load",
        observe_data_fn=lambda ec: {"artifact_observed": True},
    )

    row = db.etasks_by_id["et1"]
    assert row["collected_at"] is None
    assert row["freshness_status"] is None


def test_new_krx_artifact_invalidates_previous_monitor_observation():
    """WHY: 재시도의 새 산출물을 Monitor의 옛 observed_at으로 평가된 것처럼 남기면 안 된다."""
    db = FakeOpsDB()
    _seed(
        db, task_key="ETF_HOLDINGS_COLLECTION_KRX",
        contract_key=contracts.ETF_HOLDINGS_KRX_EOD,
    )
    db.etasks_by_id["et1"]["observed_at"] = "OLD_MONITOR_TIME"

    wrapper.instrument(
        lambda: 0, task_key="ETF_HOLDINGS_COLLECTION_KRX", run_id="R",
        ledger=_ledger(db), ecs_task_arn="arn:task/retry",
        observe_data_fn=lambda ec: {"artifact_observed": True},
    )

    assert db.etasks_by_id["et1"]["observed_at"] is None


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
    _seed(db, expected_count=10)
    rc = wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {"records_out": 10, "received_count": 10,
                                    "request_completed": True},
    )
    assert rc == 0
    assert db.etasks_by_id["et1"]["task_outcome"] == states.OUTCOME_FULFILLED
    assert db.etasks_by_id["et1"]["data_status"] == states.DATA_VALID
    assert db.etasks_by_id["et1"]["completeness"] == {
        "expected": 10, "received": 10, "missing": 0,
    }
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
    _seed(db, expected_count=31)
    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {"records_out": 30, "received_count": 30},
    )
    row = db.etasks_by_id["et1"]
    assert row["task_outcome"] == states.OUTCOME_FULFILLED     # 실행 성공(축 분리)
    assert row["data_status"] == states.DATA_INCOMPLETE        # 데이터는 불완전
    assert db.attempts[0]["status"] == states.EXEC_SUCCEEDED   # attempt 실패로 안 바꾼다
    assert row["completeness"] == {"expected": 31, "received": 30, "missing": 1}


def test_ledger_expected_count_overrides_observer_self_report():
    """수집기가 분모도 30으로 줄여 신고해 30/30 만점을 만드는 축소 채점을 막는다."""
    db = FakeOpsDB()
    _seed(db, expected_count=31)
    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 30, "expected_count": 30, "received_count": 30,
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["data_status"] == states.DATA_INCOMPLETE
    assert row["completeness"] == {"expected": 31, "received": 30, "missing": 1}


def test_missing_received_count_stays_unknown_and_clears_old_completeness():
    """수신 신호가 사라진 재시도는 앞 시도의 31/31을 그대로 보이면 안 된다."""
    db = FakeOpsDB()
    _seed(db, expected_count=31)
    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 100, "failed_records": 0, "received_count": 31,
        },
    )
    assert db.etasks_by_id["et1"]["data_status"] == states.DATA_VALID

    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/2",
        observe_data_fn=lambda ec: {"records_out": 100, "failed_records": 0},
    )
    row = db.etasks_by_id["et1"]
    assert row["data_status"] == states.DATA_UNKNOWN
    assert row["completeness"] == {"expected": 31, "received": None, "missing": None}


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


# ── 산출 카운터 저장 (ALPHA-182) ──
def test_counter_stores_only_trustworthy_counts():
    """저장 카운터는 판정과 같은 기준으로 거른다 — 0 으로 메우거나 절단하지 않는다.

    대시보드(ALPHA-514)가 이 값을 "몇 건 처리했나"로 읽는다. 결측을 0 으로 쓰면 '신호 없음'이
    '0건 처리'로 위장되고, 소수를 절단하면 깨진 봉투가 그럴듯한 정수로 위장된다 — 둘 다 원장이
    실제보다 아는 척하는 방향이라 화면만 보고는 못 가른다.
    """
    c = wrapper._counter
    assert c(0) == 0 and c(2736) == 2736
    assert c(3.0) == 3                      # 정수값 float 는 그 정수다
    assert c(3.7) is None                   # 절단 금지 — 건수가 소수면 봉투가 깨진 것
    assert c(None) is None and c("5") is None and c(True) is None
    assert c(-1) is None and c(float("nan")) is None and c(float("inf")) is None
    # BIGINT 범위 밖은 저장을 포기한다 — 넘기면 UPDATE 가 통째로 실패해 같은 문장의
    # task_outcome 까지 롤백되고, 끝난 작업이 PENDING 으로 남아 MISSED 로 오판된다.
    assert c(2**63 - 1) == 2**63 - 1
    assert c(2**63) is None
    assert c(10**400) is None               # float 변환 불가 거대 int — crash 도 저장도 아니다


def test_huge_int_signal_does_not_crash_judgement():
    """거대 int 봉투가 판정 단계를 죽이지 않는다 — 작업이 성공한 뒤 트레이스백으로 뒤집히면
    exit 0 이 크래시가 되고 원장엔 아무 결과도 안 남는다(crash-before-gate)."""
    assert d({"exit_code": 0, "records_out": 10**400}) == states.DATA_UNKNOWN


def test_malformed_counter_is_logged_not_swallowed(caplog):
    """봉투는 멀쩡한데 값만 깨진 경우는 여기 말고 드러날 곳이 없다(Rule 12).

    결측(None)은 리더가 이미 경고하므로 두 번 짖지 않는다 — 경고가 흔해지면 아무도 안 본다.
    """
    with caplog.at_level("WARNING"):
        assert wrapper._counter(-5) is None
    assert any("유효한 건수가 아니다" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert wrapper._counter(None) is None
    assert caplog.records == []


def test_instrument_stores_envelope_counters():
    """봉투 카운터가 원장 행에 남는다 — 없으면 대시보드가 런×작업마다 S3 로그를 뒤져야 한다."""
    db = FakeOpsDB()
    _seed(db, task_key="LOAD_ETF_HOLDINGS")
    wrapper.instrument(
        lambda: 0, task_key="LOAD_ETF_HOLDINGS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 2736, "failed_records": 4,
            "entity_resolution_attempt_id": _attempt_id(db),
            "entity_resolution_arguments_total": 4,
            "entity_resolution_arguments_resolved": 3,
        },
    )
    row = db.etasks_by_id["et1"]
    assert row["records_out"] == 2736 and row["failed_records"] == 4
    assert row["unsupported_records"] is None
    assert row["entity_resolution_arguments_total"] == 4
    assert row["entity_resolution_arguments_resolved"] == 3
    assert db.attempts[0]["entity_resolution_arguments_total"] == 4
    assert db.attempts[0]["entity_resolution_arguments_resolved"] == 3
    # 카운터는 저장 전용 — 판정 축은 종전 규칙 그대로다(실패 있음 → INCOMPLETE).
    assert row["data_status"] == states.DATA_INCOMPLETE
    assert row["task_outcome"] == states.OUTCOME_FULFILLED


def test_non_etf_task_cannot_store_unsupported_records():
    """WHY: ETF 전용 신호가 다른 producer의 동명 키로 원장을 오염시키면 안 된다."""
    db = FakeOpsDB()
    _seed(db, task_key="LOAD_PRICE_DAILY")
    wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 10, "unsupported_records": 42, "failed_records": 0,
        },
    )
    assert db.etasks_by_id["et1"]["unsupported_records"] is None


def test_unsupported_records_is_storage_only_not_incomplete():
    """정상 지원 제외가 있어도 실제 유실이 0이면 데이터는 VALID다."""
    db = FakeOpsDB()
    _seed(db, task_key="LOAD_ETF_HOLDINGS", expected_count=33)
    wrapper.instrument(
        lambda: 0, task_key="LOAD_ETF_HOLDINGS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 958, "unsupported_records": 42, "failed_records": 0,
            "ops_attempt_id": _attempt_id(db),
            "received_count": 33,
        },
    )

    row = db.etasks_by_id["et1"]
    assert row["unsupported_records"] == 42
    assert row["data_status"] == states.DATA_VALID


def test_stale_attempt_cannot_store_unsupported_records():
    """WHY: 같은 run_id의 앞 quality log를 재시도가 읽어도 옛 지원 제외 수치를 쓰면 안 된다."""
    db = FakeOpsDB()
    _seed(db, task_key="LOAD_ETF_HOLDINGS")
    wrapper.instrument(
        lambda: 1, task_key="LOAD_ETF_HOLDINGS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/current",
        observe_data_fn=lambda ec: {
            "records_out": 958, "unsupported_records": 42, "failed_records": 0,
            "ops_attempt_id": "stale-attempt",
        },
    )
    assert db.etasks_by_id["et1"]["unsupported_records"] is None


def test_instrument_scopes_attempt_marker_to_the_wrapped_run(monkeypatch):
    """WHY: 공유 로그의 pair는 이 wrapper attempt임을 증명해야 하고 기존 프로세스 환경도 보존한다."""
    db = FakeOpsDB()
    _seed(db)
    monkeypatch.setenv("OPS_LEDGER_ATTEMPT_ID", "outer-attempt")

    def run():
        assert os.environ["OPS_LEDGER_ATTEMPT_ID"] == _attempt_id(db)
        return 0

    wrapper.instrument(run, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
                       ecs_task_arn="arn:task/1")

    assert os.environ["OPS_LEDGER_ATTEMPT_ID"] == "outer-attempt"


def test_instrument_leaves_counters_null_when_envelope_missing():
    """봉투가 없으면 컬럼도 NULL — 0 으로 메우지 않는다(data_status UNKNOWN 규칙과 동형)."""
    db = FakeOpsDB()
    _seed(db)
    wrapper.instrument(lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R",
                       ledger=_ledger(db), ecs_task_arn="arn:task/1")
    row = db.etasks_by_id["et1"]
    assert row["records_out"] is None and row["failed_records"] is None
    assert row["unsupported_records"] is None
    assert row["entity_resolution_arguments_total"] is None
    assert row["entity_resolution_arguments_resolved"] is None
    assert row["data_status"] == states.DATA_UNKNOWN


def test_instrument_leaves_counters_null_when_envelope_malformed():
    """malformed 카운터는 저장되지 않고, 그것 때문에 계측이 죽지도 않는다(원장 장애 격리)."""
    db = FakeOpsDB()
    _seed(db)
    rc = wrapper.instrument(
        lambda: 0, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": -5, "unsupported_records": "x", "failed_records": "x",
            "entity_resolution_attempt_id": _attempt_id(db),
            "entity_resolution_arguments_total": 2,
            "entity_resolution_arguments_resolved": 3,
        },
    )
    row = db.etasks_by_id["et1"]
    assert rc == 0
    assert row["records_out"] is None and row["failed_records"] is None
    assert row["unsupported_records"] is None
    assert row["entity_resolution_arguments_total"] is None
    assert row["entity_resolution_arguments_resolved"] is None
    assert row["data_status"] == states.DATA_UNKNOWN


def test_incomplete_entity_resolution_pair_is_loud_and_null(caplog):
    """WHY: 한쪽만 남은 수치는 그럴듯한 해소율로 조립할 수 없어 pair 전체를 폐기해야 한다."""
    with caplog.at_level("WARNING"):
        pair = wrapper._entity_resolution_counters({
            "exit_code": 0,
            "entity_resolution_attempt_id": "attempt-1",
            "entity_resolution_arguments_total": 4,
        }, expected_attempt_id="attempt-1")
    assert pair == {
        "entity_resolution_arguments_total": None,
        "entity_resolution_arguments_resolved": None,
    }
    assert "pair가 불완전" in caplog.text


def test_entity_resolution_pair_requires_the_same_attempt(caplog):
    """WHY: 같은 run_id의 겹친 재시도가 공유 로그를 덮어써도 다른 시도의 pair를 승인하지 않는다."""
    with caplog.at_level("WARNING"):
        pair = wrapper._entity_resolution_counters({
            "exit_code": 0,
            "entity_resolution_attempt_id": "attempt-newer",
            "entity_resolution_arguments_total": 4,
            "entity_resolution_arguments_resolved": 3,
        }, expected_attempt_id="attempt-older")
    assert pair == {
        "entity_resolution_arguments_total": None,
        "entity_resolution_arguments_resolved": None,
    }
    assert "attempt 증거가 현재 실행과 다름" in caplog.text


def test_retry_without_envelope_clears_previous_counters():
    """재시도가 봉투를 못 내놓으면 앞 시도의 카운터를 **지운다**.

    안 지우면 최신 판정(FAILED/UNKNOWN) 옆에 앞 시도의 성공 수치가 남아, 대시보드가 옛 건수를
    지금 결과로 읽는다 — 이 레포 계측 결함의 일관된 방향(원장이 관대해지는 쪽)이다.
    """
    db = FakeOpsDB()
    _seed(db, task_key="LOAD_ETF_HOLDINGS")
    wrapper.instrument(
        lambda: 0, task_key="LOAD_ETF_HOLDINGS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 100, "unsupported_records": 42, "failed_records": 0,
            "ops_attempt_id": _attempt_id(db),
            "entity_resolution_attempt_id": _attempt_id(db),
            "entity_resolution_arguments_total": 4,
            "entity_resolution_arguments_resolved": 3,
        },
    )
    assert db.etasks_by_id["et1"]["records_out"] == 100
    assert db.etasks_by_id["et1"]["unsupported_records"] == 42
    assert db.etasks_by_id["et1"]["entity_resolution_arguments_resolved"] == 3

    wrapper.instrument(  # 같은 expected_task 재시도 — 이번엔 봉투가 없다
        lambda: 0, task_key="LOAD_ETF_HOLDINGS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/2",
    )
    row = db.etasks_by_id["et1"]
    assert row["records_out"] is None and row["failed_records"] is None
    assert row["unsupported_records"] is None
    assert row["entity_resolution_arguments_total"] is None
    assert row["entity_resolution_arguments_resolved"] is None
    assert row["data_status"] == states.DATA_UNKNOWN


def test_nonzero_exit_rejects_stale_entity_resolution_pair():
    """WHY: 같은 run_id의 새 로그 저장이 실패하면 observer가 앞 성공 로그의 pair를 읽을 수 있다."""
    db = FakeOpsDB()
    _seed(db)
    wrapper.instrument(
        lambda: 1, task_key="LOAD_PRICE_DAILY", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/failed",
        observe_data_fn=lambda ec: {
            "records_out": 100, "failed_records": 0,
            "entity_resolution_attempt_id": _attempt_id(db),
            "entity_resolution_arguments_total": 4,
            "entity_resolution_arguments_resolved": 3,
        },
    )
    row = db.etasks_by_id["et1"]
    assert row["task_outcome"] == states.OUTCOME_FAILED
    assert row["entity_resolution_arguments_total"] is None
    assert row["entity_resolution_arguments_resolved"] is None


def test_step_exception_clears_counters():
    """예외로 죽은 시도는 산출을 세지 못했다 — '실패했지만 2736건 처리'를 만들지 않는다."""
    db = FakeOpsDB()
    _seed(db, task_key="LOAD_ETF_HOLDINGS")
    wrapper.instrument(
        lambda: 0, task_key="LOAD_ETF_HOLDINGS", run_id="R", ledger=_ledger(db),
        ecs_task_arn="arn:task/1",
        observe_data_fn=lambda ec: {
            "records_out": 2736, "unsupported_records": 42, "failed_records": 0,
            "ops_attempt_id": _attempt_id(db),
            "entity_resolution_attempt_id": _attempt_id(db),
            "entity_resolution_arguments_total": 4,
            "entity_resolution_arguments_resolved": 3,
        },
    )
    assert db.etasks_by_id["et1"]["records_out"] == 2736
    assert db.etasks_by_id["et1"]["unsupported_records"] == 42

    def _boom():
        raise RuntimeError("적재 중 커넥션 끊김")

    with pytest.raises(RuntimeError):
        wrapper.instrument(_boom, task_key="LOAD_ETF_HOLDINGS", run_id="R",
                           ledger=_ledger(db), ecs_task_arn="arn:task/2")
    row = db.etasks_by_id["et1"]
    assert row["task_outcome"] == states.OUTCOME_FAILED
    assert row["records_out"] is None and row["failed_records"] is None
    assert row["unsupported_records"] is None
    assert row["entity_resolution_arguments_total"] is None
    assert row["entity_resolution_arguments_resolved"] is None
