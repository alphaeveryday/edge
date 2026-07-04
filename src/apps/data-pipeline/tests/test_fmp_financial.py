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


def test_fetch_attaches_provenance_us_only():
    # WHY: bronze 는 원본 행을 보존하고 수집 provenance(our_ticker·market·fmp_symbol·
    #      statement_type·period_type)만 덧붙인다. canonical 이 statement_type·period_type
    #      으로 문서·주기를 가르므로 이 둘은 꼭 붙어야 한다. 매핑 없는 KR 은 제외.
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
    # 원본 필드는 손대지 않고 그대로 보존(무변형) — canonical 이 date·fillingDate 로 정체성 추출.
    assert r["date"] == "2025-01-31" and r["fillingDate"] == "2025-02-26"
    assert r["netIncome"] == 100


def test_row_missing_date_is_preserved():
    # WHY: bronze 는 하나도 못 버린다 — date/fillingDate 가 없는 행도(품질 판정은 후속
    #      canonical 소관) 그대로 낸다. 정체성 부재를 raw 에서 드롭하면 원본을 잃는다.
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
    assert len(annual_income) == 2  # 둘 다 보존(버리지 않음)
    assert not src.fetch_failures  # 정체성 부재는 실패가 아님(canonical 이 판정)


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
