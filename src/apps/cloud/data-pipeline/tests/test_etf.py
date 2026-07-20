"""FmpEtfSource 어댑터 테스트 — 메타 부착·원본 보존·plan·StopFetch 전파·격리."""

import json

import pytest

from data_pipeline.config import EtfSource
from data_pipeline.sources.etf import FmpEtfSource
from data_pipeline.sources.http import StopFetch


class FakeClient:
    def __init__(self, responses):
        self.responses = responses  # {fmp_symbol: <payload>}

    def get(self, url, *, accept="application/json"):
        symbol = url.split("symbol=")[1].split("&")[0]
        payload = self.responses.get(symbol, [])
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)


def _source(responses, etf_map=None):
    config = EtfSource(
        base_url="https://fmp.example/stable/etf/holdings",
        api_key="k",
        etf_map=etf_map if etf_map is not None else {"SPY": "SPY"},
    )
    return FmpEtfSource(config, FakeClient(responses))


def test_fetch_attaches_meta_and_preserves_original():
    # WHY: raw 는 원본 필드를 무변형 보존하고 수집 메타(our_etf_id/market/fetched_at)만
    #      덧붙인다 — 특히 벤더 기준일(updatedAt)이 그대로 남아야 canonical 이 쓴다.
    src = _source({"SPY": [{"asset": "NVDA", "weightPercentage": 7.5, "updatedAt": "2026-07-11 09:07:03"}]})
    rows = list(src.fetch())

    assert len(rows) == 1
    row = rows[0]
    assert row["our_etf_id"] == "SPY" and row["market"] == "US" and "fetched_at" in row
    # 원본 필드 무변형 보존.
    assert row["asset"] == "NVDA" and row["weightPercentage"] == 7.5
    assert row["updatedAt"] == "2026-07-11 09:07:03"


def test_plan_maps_and_sets_planned_count():
    # WHY: etf_map 이 곧 수집 유니버스 — plan 은 (our_etf_id, fmp_symbol) 로 매핑하고,
    #      planned_etfs 를 세워 스텝이 '매핑 0개'를 skip 으로 드러내게 한다.
    src = _source({"SPY": [{"asset": "NVDA"}], "QQQ": [{"asset": "MSFT"}]},
                  etf_map={"SPY": "SPY", "QQQ": "QQQ"})
    list(src.fetch())
    assert src.planned_etfs == 2
    assert src.plan() == [("QQQ", "QQQ"), ("SPY", "SPY")]  # 정렬됨(재현성)


def test_empty_holdings_isolated_as_failure():
    # WHY: ETF 는 정의상 구성종목이 있으므로 빈 holdings 는 정상이 아니다 — 종목의
    #      '뉴스 없음'과 달리 fail-loud 하게 ETF 단위 실패로 격리한다(런은 partial/error).
    src = _source({"SPY": [{"asset": "NVDA"}], "QQQ": []}, etf_map={"SPY": "SPY", "QQQ": "QQQ"})
    rows = list(src.fetch())

    assert len(rows) == 1  # SPY 만 수집
    assert len(src.fetch_failures) == 1
    assert src.fetch_failures[0]["our_etf_id"] == "QQQ"
    assert "empty holdings" in src.fetch_failures[0]["error"]


def test_stopfetch_aborts_whole_source():
    # WHY: 4xx/429 는 키·쿼터 문제라 ETF 단위 격리 대상이 아니다 — 소스 전체를 중단해야
    #      쿼터를 더 태우지 않는다(격리하면 남은 ETF 로 계속 4xx 를 맞는다).
    src = _source({"SPY": StopFetch("429 rate limit")})
    with pytest.raises(StopFetch):
        list(src.fetch())


def test_disabled_without_api_key():
    # WHY: 키는 env 로만 주입 — 없으면 이 소스는 비활성(스텝이 skip 으로 드러냄).
    config = EtfSource(base_url="https://fmp.example/stable/etf/holdings", etf_map={"SPY": "SPY"})
    assert FmpEtfSource(config, FakeClient({})).enabled is False
