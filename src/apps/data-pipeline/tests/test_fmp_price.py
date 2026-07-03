"""FMP 가격 어댑터 테스트 — 심볼 매핑·수집 메타 부착·오류 격리 (네트워크 없음)."""

import json

import pytest

from data_pipeline.config import PriceSource
from data_pipeline.sources.fmp_price import FmpPriceSource
from data_pipeline.sources.http import StopFetch

# 심볼맵은 FMP 벤더 단위라 뉴스와 공유한다 — 테스트도 배선처럼 별도 인자로 주입한다.
_MAP = {"005930": "SSNLF", "105560": "KB", "NVDA": "NVDA", "BRK.B": "BRK-B"}


def _qs(url: str, key: str, default: str = "") -> str:
    return url.split(f"{key}=")[1].split("&")[0] if f"{key}=" in url else default


class FakeClient:
    """symbol 별 canned 응답. 실제 HTTP 없이 어댑터 로직만 검증한다."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses  # {fmp_symbol: body}
        self.requested: list[str] = []

    def get(self, url: str, *, accept: str = "application/json") -> str:
        self.requested.append(url)
        return self.responses.get(_qs(url, "symbol"), "[]")


def _bar(date: str, close: float = 10.0) -> dict:
    return {"date": date, "open": 9.0, "high": 11.0, "low": 8.5, "close": close, "volume": 100}


def _source(
    responses: dict[str, str], api_key: str | None = "k", symbol_map: dict | None = None
) -> FmpPriceSource:
    config = PriceSource(
        base_url="https://fmp.example/stable/historical-price-eod/full", api_key=api_key
    )
    return FmpPriceSource(config, FakeClient(responses), _MAP if symbol_map is None else symbol_map)


def test_disabled_without_api_key():
    # WHY: 키 미주입 상태에서 질의하면 무의미한 401 을 두드린다 — 소스 스스로 비활성을
    #      드러내고 호출부가 skip 처리한다.
    assert _source({}, api_key=None).enabled is False
    assert _source({}, api_key="k").enabled is True


def test_plan_skips_unmapped_symbols():
    # WHY: 설정 심볼맵에 없는 종목(검증 안 된 KR ADR·오타)은 FMP 질의에 오르면 안 된다 —
    #      매핑된 심볼만 계획에 남는다(나머지는 이 소스가 건너뜀).
    plan = _source({}).plan(["005930", "000660", "NVDA", "UNKNOWN"])
    assert plan == [("005930", "SSNLF"), ("NVDA", "NVDA")]


def test_fetch_attaches_collection_meta():
    # WHY: raw 존 항목은 어느 our_ticker/market/fmp_symbol 질의로 왔는지가 있어야
    #      후속 정규화(mart 적재)와 재현 검증이 가능하다. 원본 필드는 보존한다.
    source = _source({"NVDA": json.dumps([_bar("2026-07-01", close=125.0)])})
    records = list(source.fetch(["NVDA"]))

    assert len(records) == 1
    rec = records[0]
    assert rec["our_ticker"] == "NVDA"
    assert rec["market"] == "US"
    assert rec["fmp_symbol"] == "NVDA"
    assert rec["fetched_at"]  # ISO 수집시각
    assert rec["close"] == 125.0  # 원본 OHLCV 보존(선별은 후속)
    assert rec["date"] == "2026-07-01"


def test_fetch_accepts_historical_object_shape():
    # WHY: FMP EOD 는 평평한 배열 또는 {symbol, historical:[...]} 두 형태로 온다 —
    #      객체 형태에서도 봉을 놓치면 안 된다(형태 차이로 조용한 0건 금지).
    body = json.dumps({"symbol": "AAPL", "historical": [_bar("2026-07-01"), _bar("2026-06-30")]})
    source = _source({"AAPL": body}, symbol_map={"AAPL": "AAPL"})
    records = list(source.fetch(["AAPL"]))
    assert [r["date"] for r in records] == ["2026-07-01", "2026-06-30"]


def test_fetch_isolates_bad_json_per_symbol():
    # WHY: 한 심볼의 깨진 응답이 나머지 심볼 수집까지 죽이면 안 된다 — 격리 후 계속.
    #      단 깨진 응답도 실패로 기록돼야 한다(조용한 성공 금지).
    source = _source({"SSNLF": "{broken", "NVDA": json.dumps([_bar("2026-07-01")])})
    records = list(source.fetch(["005930", "NVDA"]))
    assert [r["our_ticker"] for r in records] == ["NVDA"]
    assert [f["symbol"] for f in source.fetch_failures] == ["SSNLF"]


def test_fetch_records_non_list_response_as_failure():
    # WHY: 200 이지만 배열도 historical 객체도 아닌 응답(예: 에러 문자열)은 실패다 —
    #      기록 없이 넘기면 전 심볼이 그런 응답이어도 런이 '성공(0건)'으로 남는다.
    source = _source({"NVDA": json.dumps("rate limited")})
    records = list(source.fetch(["NVDA"]))
    assert records == []
    assert [f["symbol"] for f in source.fetch_failures] == ["NVDA"]


def test_fetch_skips_malformed_rows_and_keeps_good_ones():
    # WHY: 배열 안에 null/문자열/숫자가 섞여도 dict(row) 예외로 제너레이터가 죽어
    #      남은 정상 봉·심볼 수집이 끊기면 안 된다 — 불량 행은 기록 후 스킵.
    payload = json.dumps([_bar("2026-07-01"), None, "쓰레기", 123])
    source = _source({"NVDA": payload})
    records = list(source.fetch(["NVDA"]))

    assert [r["date"] for r in records] == ["2026-07-01"]  # 정상 봉은 살아남음
    assert len(source.fetch_failures) == 3  # null·str·int 3건 기록


def test_fetch_isolates_retry_exhaustion_per_symbol():
    # WHY: 한 심볼의 일시 오류(5xx 재시도 소진)가 남은 심볼 수집을 끊으면 런 하나의
    #      장애가 전체 유니버스 유실로 번진다 — 심볼 단위 격리.
    class FlakyClient(FakeClient):
        def get(self, url, *, accept="application/json"):
            if "SSNLF" in url:
                raise RuntimeError("GET 재시도 소진")
            return super().get(url, accept=accept)

    config = PriceSource(base_url="https://fmp.example/x", api_key="k")
    source = FmpPriceSource(config, FlakyClient({"NVDA": json.dumps([_bar("2026-07-01")])}), _MAP)
    records = list(source.fetch(["005930", "NVDA"]))
    assert [r["our_ticker"] for r in records] == ["NVDA"]
    assert [f["symbol"] for f in source.fetch_failures] == ["SSNLF"]


def test_fetch_stops_on_stop_fetch():
    # WHY: 4xx/429 는 키·쿼터 문제라 심볼 격리 대상이 아니다 — 즉시 전체 중단해야
    #      무의미한 호출로 쿼터를 더 태우지 않는다.
    class BlockedClient(FakeClient):
        def get(self, url, *, accept="application/json"):
            raise StopFetch("HTTP 429")

    config = PriceSource(base_url="https://fmp.example/x", api_key="k")
    source = FmpPriceSource(config, BlockedClient({}), _MAP)
    with pytest.raises(StopFetch):
        list(source.fetch(["NVDA"]))


def test_request_url_carries_window():
    # WHY: 날짜창이 URL 에 실려야 FMP 가 구간을 좁힌다. EOD 는 심볼 단수(symbol=) 파라미터.
    url = _source({}).request_url("AAPL", from_date="2026-06-01", to_date="2026-06-15")
    assert "symbol=AAPL" in url and "symbols=" not in url
    assert "from=2026-06-01" in url and "to=2026-06-15" in url


def test_brk_queries_fmp_dash_symbol():
    # WHY: FMP 는 클래스주를 대시(BRK-B)로 표기한다 — 점 표기로 질의하면 조용한 0건.
    assert _source({}).plan(["BRK.B"]) == [("BRK.B", "BRK-B")]
