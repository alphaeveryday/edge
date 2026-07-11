"""공급계약 fact 게이트 — blocking(정체성·시간축) vs 경고(값 이상 표면화) 경계 검증.

각도 H: malformed 로 파싱된 fact 가 사유 없이 통과하지 않게, 결측·범위밖을 각각 사유로
수집하는지(coerce-to-passing 방지). 뉴스 게이트와 동형 — 사유 전부 수집(첫 실패에서 안 멈춤).
"""

from __future__ import annotations

import pytest

from data_pipeline.quality import BLOCKING_REASONS_DISCLOSURE
from data_pipeline.quality.disclosure import validate_supply_fact

MAX_REPORT_DATE = "2026-06-25"


def _valid_fact(**overrides) -> dict:
    fact = {
        "rcept_no": "20260623900750",
        "report_date": "2026-06-23",
        "counterparty": "한화에어로스페이스(주)",
        "counterparty_withheld": False,
        "amount_krw": 17_899_464_000,
        "ratio_pct": 92.33,
        "contract_start": "2024-04-29",
        "contract_end": "2029-02-10",
    }
    fact.update(overrides)
    return fact


def test_valid_fact_has_no_reasons() -> None:
    assert validate_supply_fact(_valid_fact(), max_report_date=MAX_REPORT_DATE) == []


def test_missing_rcept_no_is_blocking() -> None:
    reasons = validate_supply_fact(_valid_fact(rcept_no=None), max_report_date=MAX_REPORT_DATE)
    assert "missing_rcept_no" in reasons
    assert "missing_rcept_no" in BLOCKING_REASONS_DISCLOSURE


@pytest.mark.parametrize("report_date", [None, "", "   "])
def test_missing_report_date_is_blocking(report_date) -> None:
    reasons = validate_supply_fact(_valid_fact(report_date=report_date), max_report_date=MAX_REPORT_DATE)
    assert "missing_report_date" in reasons
    assert "missing_report_date" in BLOCKING_REASONS_DISCLOSURE


@pytest.mark.parametrize("report_date", ["2099-12-31", "1999-01-01"])
def test_out_of_range_report_date_is_blocking(report_date) -> None:
    """달력유효-쓰레기 날짜(far-future/past)는 bad_report_date 로 막는다(passed 위장 방지)."""
    reasons = validate_supply_fact(_valid_fact(report_date=report_date), max_report_date=MAX_REPORT_DATE)
    assert "bad_report_date" in reasons
    assert "bad_report_date" in BLOCKING_REASONS_DISCLOSURE


def test_empty_parse_is_blocking() -> None:
    """계약을 하나도 못 뽑은 빈 파싱(테이블 없음 등)은 canonical 가치가 없어 막는다."""
    empty = {
        "rcept_no": "20260623900750",
        "report_date": "2026-06-23",
        "counterparty": None,
        "counterparty_withheld": False,
        "amount_krw": None,
        "ratio_pct": None,
        "contract_start": None,
        "contract_end": None,
    }
    reasons = validate_supply_fact(empty, max_report_date=MAX_REPORT_DATE)
    assert "empty_parse" in reasons
    assert "empty_parse" in BLOCKING_REASONS_DISCLOSURE


def test_withheld_counterparty_is_warning_not_blocking() -> None:
    """계약상대방 유보는 정상 관행 — 통과시키되 경고로 드러낸다. empty_parse 로 치지 않는다."""
    fact = _valid_fact(counterparty=None, counterparty_withheld=True)
    reasons = validate_supply_fact(fact, max_report_date=MAX_REPORT_DATE)
    assert "withheld_counterparty" in reasons
    assert "empty_parse" not in reasons
    assert not (set(reasons) & BLOCKING_REASONS_DISCLOSURE)


def test_missing_amount_and_ratio_is_warning() -> None:
    fact = _valid_fact(amount_krw=None, ratio_pct=None)
    reasons = validate_supply_fact(fact, max_report_date=MAX_REPORT_DATE)
    assert "missing_amount_and_ratio" in reasons
    assert not (set(reasons) & BLOCKING_REASONS_DISCLOSURE)


@pytest.mark.parametrize("ratio_pct", [214.5, 0, -5.0])
def test_ratio_out_of_range_is_warning(ratio_pct) -> None:
    """0 이하·150% 초과 비율은 파싱 이상 신호로 표면화(경고) — fact 자체는 유효해 통과."""
    reasons = validate_supply_fact(_valid_fact(ratio_pct=ratio_pct), max_report_date=MAX_REPORT_DATE)
    assert "ratio_out_of_range" in reasons
    assert not (set(reasons) & BLOCKING_REASONS_DISCLOSURE)


@pytest.mark.parametrize("amount_krw", [0, -100])
def test_amount_non_positive_is_warning(amount_krw) -> None:
    fact = _valid_fact(amount_krw=amount_krw)
    reasons = validate_supply_fact(fact, max_report_date=MAX_REPORT_DATE)
    assert "amount_non_positive" in reasons
    assert not (set(reasons) & BLOCKING_REASONS_DISCLOSURE)


def test_amount_over_int64_is_blocking() -> None:
    """각도 H: int64 초과 금액(단위 곱으로 만든 초대형 값)은 canonical 적재 시 OverflowError 라
    표현 불가 — passed 로 인증하지 않고 blocking 으로 막는다(배치 kill 방지)."""
    fact = _valid_fact(amount_krw=10**19)  # > int64 max(9.22e18)
    reasons = validate_supply_fact(fact, max_report_date=MAX_REPORT_DATE)
    assert "amount_out_of_range" in reasons
    assert "amount_out_of_range" in BLOCKING_REASONS_DISCLOSURE
    assert "amount_non_positive" not in reasons  # 상한 위반은 ≤0 경고와 배타


def test_ratio_non_finite_is_blocking() -> None:
    """각도 H: inf/nan 비율은 float64 canonical 오염원 — blocking 으로 막는다.
    NaN 비교는 전부 False 라 범위 검사를 조용히 통과하므로 finite 검사를 먼저 둔다."""
    for bad in (float("inf"), float("-inf"), float("nan")):
        reasons = validate_supply_fact(_valid_fact(ratio_pct=bad), max_report_date=MAX_REPORT_DATE)
        assert "ratio_not_finite" in reasons, bad
        assert "ratio_out_of_range" not in reasons  # 비유한은 범위 경고와 배타
    assert "ratio_not_finite" in BLOCKING_REASONS_DISCLOSURE


def test_collects_all_reasons_not_first() -> None:
    """사유는 전부 수집한다(첫 실패에서 안 멈춤) — blocking + 경고가 함께 나온다(Rule 12)."""
    fact = _valid_fact(rcept_no=None, ratio_pct=999.0, amount_krw=-1)
    reasons = validate_supply_fact(fact, max_report_date=MAX_REPORT_DATE)
    assert {"missing_rcept_no", "ratio_out_of_range", "amount_non_positive"} <= set(reasons)


def test_boolean_ratio_does_not_crash_or_flag() -> None:
    """각도 H: bool 은 int 하위형이라 조용히 수치로 통과할 수 있다 — 비교 대상에서 제외한다."""
    reasons = validate_supply_fact(_valid_fact(ratio_pct=True), max_report_date=MAX_REPORT_DATE)
    assert "ratio_out_of_range" not in reasons
