"""FMP 어댑터 테스트 — 심볼 매핑·수집 메타 부착·오류 격리 (네트워크 없음)."""

import json

from data_pipeline.config import NewsSource
from data_pipeline.sources.fmp import FmpNewsSource, market_for


def _qs(url: str, key: str, default: str = "") -> str:
    return url.split(f"{key}=")[1].split("&")[0] if f"{key}=" in url else default


class FakeClient:
    """URL 별 canned 응답. 실제 HTTP 없이 어댑터 로직만 검증한다.

    page 0 에만 응답을 주고 이후는 빈 배열 — 페이지네이션 종료를 흉내낸다."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses  # {fmp_symbol: body}
        self.requested: list[str] = []

    def get(self, url: str, *, accept: str = "application/json") -> str:
        self.requested.append(url)
        if _qs(url, "page", "0") != "0":
            return "[]"
        return self.responses.get(_qs(url, "symbols"), "[]")


# 심볼 맵은 이제 설정에서 온다 — 테스트도 config 로 주입한다(코드 상수 아님).
_MAP = {"005930": "SSNLF", "105560": "KB", "NVDA": "NVDA", "BRK.B": "BRK-B"}


def _source(
    responses: dict[str, str], api_key: str | None = "k", symbol_map: dict | None = None
) -> FmpNewsSource:
    config = NewsSource(
        base_url="https://fmp.example/stable/news/stock",
        api_key=api_key,
        symbol_map=_MAP if symbol_map is None else symbol_map,
    )
    return FmpNewsSource(config, FakeClient(responses))


def test_disabled_without_api_key():
    # WHY: 키 미주입 상태에서 질의하면 무의미한 401 을 두드린다 — 소스 스스로
    #      비활성을 드러내고 호출부가 skip 처리한다.
    assert _source({}, api_key=None).enabled is False
    assert _source({}, api_key="k").enabled is True


def test_plan_skips_unmapped_symbols():
    # WHY: 설정 심볼맵에 없는 종목(검증 안 된 KR ADR·오타)은 FMP 질의에 오르면 안 된다 —
    #      매핑된 심볼만 계획에 남는다(나머지는 이 소스가 건너뜀).
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
    #      단 깨진 응답도 실패로 기록돼야 한다(전 심볼이 깨진 200 → 조용한 성공 금지).
    ok = json.dumps([{"title": "ok", "publishedDate": "2026-07-01 00:00:00"}])
    source = _source({"SSNLF": "{broken", "NVDA": ok})
    records = list(source.fetch(["005930", "NVDA"]))
    assert [r["our_ticker"] for r in records] == ["NVDA"]
    assert [f["symbol"] for f in source.fetch_failures] == ["SSNLF"]


def test_fetch_records_non_list_response_as_failure():
    # WHY: 200 이지만 배열이 아닌 응답(예: 에러 객체)도 실패다 — 기록 없이 넘기면
    #      전 심볼이 그런 응답을 받아도 런이 '성공(0건)'으로 남는다.
    source = _source({"NVDA": json.dumps({"error": "quota"})})
    records = list(source.fetch(["NVDA"]))
    assert records == []
    assert [f["symbol"] for f in source.fetch_failures] == ["NVDA"]


def test_fetch_skips_malformed_items_and_keeps_good_ones():
    # WHY: list 안에 null/문자열/숫자가 섞여도 dict(item) 예외로 제너레이터가 죽어
    #      남은 정상 item·심볼 수집이 끊기면 안 된다 — 불량 item 은 기록 후 스킵.
    payload = json.dumps([
        {"title": "good", "publishedDate": "2026-07-01 00:00:00"},
        None,
        "쓰레기",
        123,
    ])
    source = _source({"NVDA": payload})
    records = list(source.fetch(["NVDA"]))

    assert [r["title"] for r in records] == ["good"]  # 정상 item 은 살아남음
    assert len(source.fetch_failures) == 3  # null·str·int 3건 기록


def test_fetch_isolates_retry_exhaustion_per_symbol():
    # WHY: 한 심볼의 일시 오류(5xx 재시도 소진)가 남은 심볼 수집을 끊으면
    #      런 하나의 장애가 전체 유니버스 유실로 번진다 — 심볼 단위 격리.
    ok = json.dumps([{"title": "ok", "publishedDate": "2026-07-01 00:00:00"}])

    class FlakyClient(FakeClient):
        def get(self, url, *, accept="application/json"):
            if "SSNLF" in url:
                raise RuntimeError("GET 재시도 소진")
            return super().get(url, accept=accept)

    config = NewsSource(base_url="https://fmp.example/x", api_key="k", symbol_map=_MAP)
    source = FmpNewsSource(config, FlakyClient({"NVDA": ok}))
    records = list(source.fetch(["005930", "NVDA"]))
    assert [r["our_ticker"] for r in records] == ["NVDA"]
    # 격리하되 은폐하지 않는다 — 실패 심볼이 기록돼 스텝이 런 상태에 반영한다.
    assert [f["symbol"] for f in source.fetch_failures] == ["SSNLF"]


def test_fetch_stops_on_stop_fetch():
    # WHY: 4xx/429 는 키·쿼터 문제라 심볼 격리 대상이 아니다 — 즉시 전체 중단해야
    #      무의미한 호출로 쿼터를 더 태우지 않는다.
    import pytest

    from data_pipeline.sources.http import StopFetch

    class BlockedClient(FakeClient):
        def get(self, url, *, accept="application/json"):
            raise StopFetch("HTTP 429")

    config = NewsSource(base_url="https://fmp.example/x", api_key="k", symbol_map=_MAP)
    source = FmpNewsSource(config, BlockedClient({}))
    with pytest.raises(StopFetch):
        list(source.fetch(["NVDA"]))


def test_brk_queries_fmp_dash_symbol():
    # WHY: FMP 는 클래스주를 대시(BRK-B)로 표기한다 — 점 표기로 질의하면
    #      버크셔 뉴스가 조용히 0건이 된다(analysis-engine 데이터 키와도 일치).
    source = _source({})
    assert source.plan(["BRK.B"]) == [("BRK.B", "BRK-B")]


def test_fetch_flags_truncation_at_max_pages(monkeypatch):
    # WHY: 백필 창이 커서 MAX_PAGES 를 초과하면 뒷부분을 조용히 버리면 안 된다 —
    #      절단을 실패로 기록해 런이 partial 로 드러나야 한다(fail loud).
    from data_pipeline.sources import fmp as fmp_mod
    monkeypatch.setattr(fmp_mod, "MAX_PAGES", 3)

    full = json.dumps([{"title": "x", "publishedDate": "2026-06-10 00:00:00",
                        "url": "https://e.com/x"}])  # limit=1 → 매 페이지가 '꽉 참'

    class AlwaysFull:
        def get(self, url, *, accept="application/json"):
            return full

    config = NewsSource(base_url="https://fmp.example/x", api_key="k", symbol_map={"NVDA": "NVDA"})
    source = FmpNewsSource(config, AlwaysFull(), limit=1)
    records = list(source.fetch(["NVDA"], "2026-06-01", "2026-06-30"))

    assert len(records) == 3  # MAX_PAGES(3) × limit(1) 까지만
    assert source.fetch_failures and "MAX_PAGES" in source.fetch_failures[0]["error"]


def test_request_url_carries_window_and_page():
    # WHY: 날짜창·페이지가 URL 에 실려야 FMP 가 창을 좁히고 페이지네이션이 동작한다.
    source = _source({})
    url = source.request_url("AAPL", page=2, from_date="2026-06-01", to_date="2026-06-15")
    assert "symbols=AAPL" in url and "page=2" in url
    assert "from=2026-06-01" in url and "to=2026-06-15" in url


def test_fetch_paginates_until_short_page():
    # WHY: 하루 유입이 limit 을 넘으면 page 0 만 읽어선 뒷부분을 놓친다 — 페이지 끝까지
    #      순회해야 창 안 기사를 다 수집한다(마지막 페이지 = limit 미만에서 종료).
    def item(n):
        return {"title": f"a{n}", "publishedDate": "2026-06-10 00:00:00", "url": f"https://e.com/{n}"}

    class PagingClient:
        def get(self, url, *, accept="application/json"):
            page = _qs(url, "page", "0")
            return {  # limit=2 → page0·page1 꽉 참, page2 부분(1건)에서 종료
                "0": json.dumps([item(1), item(2)]),
                "1": json.dumps([item(3), item(4)]),
                "2": json.dumps([item(5)]),
            }.get(page, "[]")

    config = NewsSource(base_url="https://fmp.example/x", api_key="k", symbol_map={"NVDA": "NVDA"})
    source = FmpNewsSource(config, PagingClient(), limit=2)
    records = list(source.fetch(["NVDA"], "2026-06-01", "2026-06-30"))
    assert [r["title"] for r in records] == ["a1", "a2", "a3", "a4", "a5"]


def test_market_for():
    # WHY: market 파티션 키가 갈리는 지점 — KR 숫자 티커 규약이 바뀌면 경로가 깨진다.
    assert market_for("005930") == "KR"
    assert market_for("BRK.B") == "US"
