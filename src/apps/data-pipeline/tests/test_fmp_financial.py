"""fmp_financial 어댑터 테스트 — 공시 정체성 필드 부착·US 매핑·대상 단위 격리.

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9).
"""

import json

import pytest

from data_pipeline.config import FinancialSource
from data_pipeline.sources.fmp_financial import FmpFinancialSource
from data_pipeline.sources.http import StopFetch

# 재무는 US 거래소-로컬 심볼만 매핑(가격과 같은 정책). 005930(KR)은 미매핑 → 이 소스 제외.
_MAP = {"NVDA": "NVDA"}


class FakeClient:
    """responses: {(fmp_symbol, endpoint, period): [rows]}. 미지정 대상은 빈 배열."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, str, str]] = []

    def get(self, url: str, *, accept: str = "application/json") -> str:
        endpoint = url.split("?")[0].rsplit("/", 1)[1]
        symbol = url.split("symbol=")[1].split("&")[0]
        period = url.split("period=")[1].split("&")[0]
        self.calls.append((symbol, endpoint, period))
        return json.dumps(self.responses.get((symbol, endpoint, period), []))


def _row(date: str, filing: str, period: str = "FY", **vals) -> dict:
    return {"date": date, "fillingDate": filing, "period": period, **vals}


def _source(responses, api_key="k", symbol_map=None):
    config = FinancialSource(
        base_url="https://fmp.example/stable", api_key=api_key,
        symbol_map=_MAP if symbol_map is None else symbol_map,
    )
    return FmpFinancialSource(config, FakeClient(responses))


def test_fetch_attaches_identity_fields_us_only():
    # WHY: 스텝이 공시 정체성으로 raw 키를 만든다 — 어댑터가 statement_type·period_type·
    #      fiscal_period_end·filing_date 를 각 행에 붙여야 키를 만들 수 있다. 매핑 없는 KR 은 제외.
    responses = {
        ("NVDA", "income-statement", "annual"): [_row("2025-01-31", "2025-02-26", period="FY", netIncome=100)],
        ("NVDA", "income-statement", "quarter"): [_row("2025-01-31", "2025-02-26", period="Q4", netIncome=30)],
    }
    src = _source(responses)
    records = list(src.fetch(["NVDA", "005930"]))  # 005930 미매핑 → 제외

    assert src.planned_symbols == 1  # NVDA 만 매핑됨
    income = [r for r in records if r["statement_type"] == "income_statement"]
    assert {r["period_type"] for r in income} == {"annual", "quarter"}  # 두 주기 모두 수집
    r = next(r for r in income if r["period_type"] == "annual")
    assert r["our_ticker"] == "NVDA" and r["market"] == "US" and r["fmp_symbol"] == "NVDA"
    assert r["fiscal_period_end"] == "2025-01-31" and r["filing_date"] == "2025-02-26"
    assert r["netIncome"] == 100  # 원본 필드 보존(무변형)


def test_row_without_identity_is_isolated_not_yielded():
    # WHY: 공시 정체성(date/fillingDate)이 없으면 raw 키를 못 만든다 — 조용히 버리지 않고
    #      대상 단위 실패로 남겨 운영이 인지하게 한다(fail loud).
    responses = {
        ("NVDA", "income-statement", "annual"): [
            {"period": "FY", "netIncome": 1},          # date·fillingDate 없음
            _row("2025-01-31", "2025-02-26"),          # 정상
        ],
    }
    src = _source(responses)
    annual_income = [
        r for r in src.fetch(["NVDA"])
        if r["statement_type"] == "income_statement" and r["period_type"] == "annual"
    ]
    assert len(annual_income) == 1  # 정상 행만 나온다
    assert any("공시 정체성" in f["error"] for f in src.fetch_failures)


def test_error_object_response_is_isolated():
    # WHY: FMP 200 에러 객체({"Error Message": ...})를 0행으로 조용히 넘기면 전 대상이
    #      실패해도 success(0건)로 위장한다 — 대상 단위 실패로 올린다.
    class ErrClient:
        def get(self, url, *, accept="application/json"):
            return json.dumps({"Error Message": "quota exceeded"})

    config = FinancialSource(base_url="https://fmp.example/stable", api_key="k", symbol_map=_MAP)
    src = FmpFinancialSource(config, ErrClient())
    records = list(src.fetch(["NVDA"]))

    assert records == []
    assert len(src.fetch_failures) == 6  # 3문서 × 2주기 모두 실패로 기록


def test_stopfetch_aborts_whole_source():
    # WHY: 4xx/429 는 키·쿼터 문제라 대상 격리가 아니라 소스 전체 중단(StopFetch)이 맞다.
    class StopClient:
        def get(self, url, *, accept="application/json"):
            raise StopFetch("HTTP 401: 수집 중단")

    config = FinancialSource(base_url="https://fmp.example/stable", api_key="k", symbol_map=_MAP)
    src = FmpFinancialSource(config, StopClient())
    with pytest.raises(StopFetch):
        list(src.fetch(["NVDA"]))


def test_disabled_without_api_key():
    # WHY: 키 미주입이면 이 소스는 비활성(스텁 환경) — 스텝이 skip 으로 드러낸다.
    config = FinancialSource(base_url="https://fmp.example/stable", api_key=None, symbol_map=_MAP)
    src = FmpFinancialSource(config, FakeClient({}))
    assert src.enabled is False
