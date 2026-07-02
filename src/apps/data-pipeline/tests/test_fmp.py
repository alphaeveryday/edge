"""FMP 어댑터 테스트 — 심볼 매핑·수집 메타 부착·오류 격리 (네트워크 없음)."""

import json

from data_pipeline.config import NewsSource
from data_pipeline.sources.fmp import FmpNewsSource, market_for


class FakeClient:
    """URL 별 canned 응답. 실제 HTTP 없이 어댑터 로직만 검증한다."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses  # {fmp_symbol: body}
        self.requested: list[str] = []

    def get(self, url: str, *, accept: str = "application/json") -> str:
        symbol = url.split("symbols=")[1].split("&")[0]
        self.requested.append(symbol)
        return self.responses.get(symbol, "[]")


def _source(responses: dict[str, str], api_key: str | None = "k") -> FmpNewsSource:
    config = NewsSource(base_url="https://fmp.example/stable/news/stock", api_key=api_key)
    return FmpNewsSource(config, FakeClient(responses))


def test_disabled_without_api_key():
    # WHY: 키 미주입 상태에서 질의하면 무의미한 401 을 두드린다 — 소스 스스로
    #      비활성을 드러내고 호출부가 skip 처리한다.
    assert _source({}, api_key=None).enabled is False
    assert _source({}, api_key="k").enabled is True


def test_plan_skips_unmapped_and_unverified_symbols():
    # WHY: 검증 안 된 KR ADR 매핑(None)으로 질의하면 엉뚱한 종목 뉴스가 수집된다 —
    #      매핑된 심볼만 질의 계획에 올라야 한다.
    source = _source({})
    plan = source.plan(["005930", "000660", "NVDA", "UNKNOWN"])
    assert plan == [("005930", "SSNLF"), ("NVDA", "NVDA")]


def test_fetch_attaches_collection_meta():
    # WHY: raw 존 항목은 어느 our_ticker/market 질의로 왔는지가 있어야
    #      Step2 정규화(mention·파티션)와 재현 검증이 가능하다.
    item = {"symbol": "SSNLF", "title": "t", "url": "https://e.com/a",
            "publishedDate": "2026-07-01 09:00:00", "site": "Reuters", "text": "body"}
    source = _source({"SSNLF": json.dumps([item])})
    records = list(source.fetch(["005930"]))

    assert len(records) == 1
    rec = records[0]
    assert rec["our_ticker"] == "005930"
    assert rec["market"] == "KR"
    assert rec["fetched_at"]  # ISO 수집시각
    assert rec["title"] == "t"  # 원본 필드 보존(선별은 Step2)


def test_fetch_isolates_bad_json_per_symbol():
    # WHY: 한 심볼의 깨진 응답이 나머지 심볼 수집까지 죽이면 안 된다 — 격리 후 계속.
    ok = json.dumps([{"title": "ok", "publishedDate": "2026-07-01 00:00:00"}])
    source = _source({"SSNLF": "{broken", "NVDA": ok})
    records = list(source.fetch(["005930", "NVDA"]))
    assert [r["our_ticker"] for r in records] == ["NVDA"]


def test_market_for():
    # WHY: market 파티션 키가 갈리는 지점 — KR 숫자 티커 규약이 바뀌면 경로가 깨진다.
    assert market_for("005930") == "KR"
    assert market_for("BRK.B") == "US"
