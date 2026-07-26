"""KIS 일봉 어댑터 테스트 — 심볼매핑·수집메타·페이지네이션·EGW00201 재시도·격리 (네트워크 없음).

각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9). 프로브에서 이식한 로직이 edge 관례
인터페이스(격리·fail-loud·bronze 무변형)를 지키는지, 실키 없이 FakeClient 로 잠근다.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta

import pytest

from data_pipeline.config import KisPriceSource
from data_pipeline.sources import kis_price
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_price import KisDailyPriceSource

_MAP = {"005930": "005930", "000660": "000660"}


def _qs(url: str, key: str) -> str:
    return url.split(f"{key}=")[1].split("&")[0] if f"{key}=" in url else ""


def _bar(date: str, close: str = "70000") -> dict:
    return {
        "stck_bsop_date": date,
        "stck_oprc": "69000",
        "stck_hgpr": "71000",
        "stck_lwpr": "68000",
        "stck_clpr": close,
        "acml_vol": "1000",
        "acml_tr_pbmn": "5000",
    }


def _ok(bars: list[dict]) -> str:
    return json.dumps({"rt_cd": "0", "output2": bars})


def _full_page(last: str, n: int = kis_price.PAGE_SIZE) -> list[dict]:
    """`last` 부터 하루씩 과거로 n 개 봉(최신→과거) — 운영과 같은 꽉 찬 페이지."""
    d = datetime.strptime(last, "%Y%m%d")
    return [_bar((d - timedelta(days=i)).strftime("%Y%m%d")) for i in range(n)]


_EMPTY = json.dumps({"rt_cd": "0", "output2": []})
_RATE = json.dumps({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
_ERR = json.dumps({"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "조회 오류"})
_TOKEN = json.dumps({"access_token": "tok", "access_token_token_expired": "2026-07-07 00:00:00"})


class FakeClient:
    """POST=토큰, GET=심볼별 페이지 응답(리스트를 순서대로 소비). 대기는 no-op."""

    _sleep = staticmethod(lambda secs: None)

    def __init__(self, chunk_responses: dict[str, list[str]], token_body: str = _TOKEN):
        self.chunk_responses = chunk_responses
        self.token_body = token_body
        self.calls: list[str] = []  # method 기록
        self.urls: list[str] = []  # GET 질의 URL 기록(파라미터 계약 검증용)
        self._idx: dict[str, int] = defaultdict(int)

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls.append(method)
        if method == "POST":
            return self.token_body
        self.urls.append(url)
        sym = _qs(url, "FID_INPUT_ISCD")
        pages = self.chunk_responses.get(sym, [])
        idx = self._idx[sym]
        self._idx[sym] += 1
        return pages[idx] if idx < len(pages) else _EMPTY


def _source(chunk_responses, *, app_key="k", app_secret="s", symbol_map=None, client=None):
    config = KisPriceSource(
        env="prod",
        app_key=app_key,
        app_secret=app_secret,
        symbol_map=_MAP if symbol_map is None else symbol_map,
    )
    return KisDailyPriceSource(config, client or FakeClient(chunk_responses))


def test_disabled_without_credentials():
    # WHY: 앱키·시크릿이 없으면 무의미한 401 을 두드린다 — 소스 스스로 비활성을 드러내고
    #      호출부(스텝)가 skip 처리한다. 둘 중 하나만 없어도 비활성.
    assert _source({}, app_key=None).enabled is False
    assert _source({}, app_secret=None).enabled is False
    assert _source({}, app_key="k", app_secret="s").enabled is True


def test_plan_maps_six_digit_identity_and_skips_foreign():
    # WHY: KRX 6자리 코드는 KIS 코드와 **항등**이라 맵 없이도 수집돼야 한다(ALPHA-419 —
    #      유니버스가 holdings 에서 파생되면 맵에 없는 구성종목이 대상으로 온다). 비6자리
    #      (US 등)만 제외한다 — KIS 는 국내 전용이라 US 티커를 질의하면 안 된다(FMP 가 커버).
    plan = _source({}, symbol_map={}).plan(["005930", "NVDA", "000660", "AAPL"])
    assert plan == [("005930", "005930"), ("000660", "000660")]


def test_plan_maps_alphanumeric_short_code_identity():
    # WHY: 신규 상장분 단축코드에는 문자가 섞이고(0093A0 등) KIS 는 그대로 받는다. 숫자로만
    #      보면 이 종목들이 항등 매핑을 못 받아 "매핑 없음"으로 건너뛰어져, 유니버스를
    #      고쳐도 KIS 질의까지 못 간다(ALPHA-463 — 유니버스 파생과 같은 축의 결함).
    plan = _source({}, symbol_map={}).plan(["0093A0", "0005G0", "NVDA", "ABCDEF", "가나다라마바"])
    # 넓히되 새지 않는다: 6자 US 심볼·한글 6자는 국내 전용 API 로 가면 안 된다 —
    # 질의가 실패하면 런이 매번 partial 이 되고, 응답하면 market=KR 로 오적재된다.
    assert plan == [("0093A0", "0093A0"), ("0005G0", "0005G0")]


def test_plan_symbol_map_overrides_identity():
    # WHY: symbol_map 은 항등이 아닌 예외의 오버라이드 축으로 남는다 — 맵이 있으면 맵이 이긴다.
    plan = _source({}, symbol_map={"005930": "005935"}).plan(["005930"])
    assert plan == [("005930", "005935")]


def test_fetch_attaches_meta_and_preserves_raw():
    # WHY: raw 존 행은 어느 our_ticker/market/kis_symbol 로 왔는지가 있어야 후속 정규화·재현이
    #      가능하다. 그리고 output2 원본 필드(stck_clpr 등)는 무변형 보존해야 한다(bronze).
    src = _source({"005930": [_ok([_bar("20260703", close="71500")])]})
    records = list(src.fetch(["005930"]))

    assert len(records) == 1
    rec = records[0]
    assert rec["our_ticker"] == "005930"
    assert rec["market"] == "KR"
    assert rec["kis_symbol"] == "005930"
    assert rec["fetched_at"]
    assert rec["stck_clpr"] == "71500"  # 원본 OHLCV 보존
    assert rec["stck_bsop_date"] == "20260703"


def test_token_issued_once_across_symbols():
    # WHY: 종목마다 토큰을 발급하면 KIS 분당 한도에 걸린다 — run 당 1회만 발급해야 한다.
    src = _source({"005930": [_ok([_bar("20260703")])], "000660": [_ok([_bar("20260703")])]})
    list(src.fetch(["005930", "000660"]))
    assert src.client.calls.count("POST") == 1  # 두 종목에 토큰 발급은 단 한 번


def test_pagination_walks_back_and_dedups():
    # WHY: 하루 100건 한도라 다년 창은 최신→과거로 페이지네이션해야 한다. 페이지 경계에서
    #      같은 거래일이 겹쳐 와도 raw 는 거래일 기준으로 중복 없이 보존한다.
    src = _source({
        "005930": [
            _ok([_bar("20260703"), _bar("20260702")]),
            _ok([_bar("20260702"), _bar("20260701")]),  # 20260702 겹침
        ],
    })
    records = list(src.fetch(["005930"], from_date="2026-07-01"))
    dates = [r["stck_bsop_date"] for r in records]
    assert dates == ["20260701", "20260702", "20260703"]  # 정렬·중복제거


def test_non_trading_lower_bound_stops_on_first_page():
    # WHY: 종료조건 `earliest <= d1` 은 d1 이 비거래일이면 **절대 성립하지 않는다** — 서버가
    #      FID_INPUT_DATE_1(=시작일)로 하한을 걸어 창 안 첫 거래일만 주므로 earliest(월)가
    #      d1(일)보다 항상 크다. 그래서 빈 페이지를 한 번 더 받아야 멈췄고, 가격 소급이 5일
    #      고정이라 목·금 런은 매번 종목당 콜이 2배였다(2026-07-24 실런: price 1.58s vs
    #      investor 0.78s/종목). 판정 하한을 거래일로 스냅해야 첫 페이지에서 멈춘다.
    src = _source({"005930": [_ok([_bar("20260721"), _bar("20260720")]), _EMPTY]})
    records = list(src.fetch(["005930"], from_date="2026-07-19"))  # 7/19 = 일요일
    assert [r["stck_bsop_date"] for r in records] == ["20260720", "20260721"]
    assert src.client.calls.count("GET") == 1  # 빈 두 번째 콜을 치지 않는다


def test_holiday_set_lets_run_stop_a_page_earlier(monkeypatch):
    # WHY: 스냅이 주말만 알면 **평일 공휴일**엔 가드가 조용히 퇴화한다 — tasks.tf 가 kis 에
    #      OPS_KR_HOLIDAYS 를 주입하는 이유와 같은 축(ALPHA-557). 그 주입이 사라지면 이
    #      테스트가 깨져 드러난다. 7/20(월)이 휴장이면 판정 하한은 7/21(화)라 첫 페이지에서 멈춘다.
    monkeypatch.setenv("OPS_KR_HOLIDAYS", "2026-07-20")
    src = _source({"005930": [_ok([_bar("20260721")]), _EMPTY]})
    list(src.fetch(["005930"], from_date="2026-07-19"))
    assert src.client.calls.count("GET") == 1


def test_over_inclusive_holiday_does_not_narrow_request_window(monkeypatch):
    # WHY: kr_holidays 는 수동 갱신이라 **실제 거래일이 잘못 등재**될 수 있다. 스냅한 값을
    #      요청 하한으로 보내면 그 날 봉이 창 밖으로 밀려 조용히 유실되고 collection_log 는
    #      success 로 남는다(Rule 12 — 유실이 성공으로 위장). 그래서 스냅은 **종료 판정에만**
    #      쓰고 요청에는 원본 d1 을 보낸다. 여기서 7/20 은 실제로 거래일인데 잘못 등재됐다.
    monkeypatch.setenv("OPS_KR_HOLIDAYS", "2026-07-20")
    src = _source({"005930": [_ok([_bar("20260721"), _bar("20260720")]), _EMPTY]})
    records = list(src.fetch(["005930"], from_date="2026-07-19"))
    assert _qs(src.client.urls[0], "FID_INPUT_DATE_1") == "20260719"  # 요청 하한은 원본
    assert "20260720" in [r["stck_bsop_date"] for r in records]  # 잘못 등재된 날의 봉도 보존


def test_full_page_does_not_trust_calendar_at_boundary(monkeypatch):
    # WHY: 잘못 등재된 휴장일이 **페이지 경계에 정확히 걸리면** 달력만 믿는 종료가 그 날 봉을
    #      잘라먹는다 — 첫 페이지 earliest 가 곧 d1_stop 이라 종료하는데, 실제 거래일인 d1 의
    #      봉은 다음 페이지에 있다. 유실인데 fetch_failures 도 없어 collection_log 는 success
    #      로 남는다(Rule 12). 꽉 찬 페이지에선 아래에 더 있을 수 있으므로 달력을 믿지 않는다.
    monkeypatch.setenv("OPS_KR_HOLIDAYS", "2026-07-20")  # 실제로는 거래일인데 잘못 등재
    src = _source({
        "005930": [
            _ok(_full_page("20261028")),   # 꽉 찬 100행, earliest = 20260721(= d1_stop)
            _ok([_bar("20260720")]),       # d1 당일 봉은 여기 있다
        ],
    })
    records = list(src.fetch(["005930"], from_date="2026-07-20"))
    assert src.client.calls.count("GET") == 2  # 꽉 찬 페이지에서 멈추지 않는다
    assert "20260720" in [r["stck_bsop_date"] for r in records]  # 경계 봉 유실 없음


def test_egw00201_retried_then_succeeds():
    # WHY: 초당한도(EGW00201)는 HTTP 429 가 아니라 응답 본문으로 온다 — 운반 계층이 못 잡으니
    #      어댑터가 재시도해야 봉을 놓치지 않는다.
    src = _source({"005930": [_RATE, _ok([_bar("20260703")]), _EMPTY]})
    records = list(src.fetch(["005930"]))
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]


def test_kis_error_code_isolated_per_symbol():
    # WHY: 한 종목의 KIS 오류코드(rt_cd!=0)가 나머지 종목 수집을 죽이면 안 된다 — 격리 후 계속,
    #      단 실패로 기록돼야 한다(조용한 성공 금지).
    src = _source({"005930": [_ERR], "000660": [_ok([_bar("20260703")])]})
    records = list(src.fetch(["005930", "000660"]))
    assert [r["our_ticker"] for r in records] == ["000660"]
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_bad_json_isolated_per_symbol():
    # WHY: 한 종목의 깨진 응답이 나머지 수집을 끊으면 안 된다 — 격리 후 계속.
    src = _source({"005930": ["{broken"], "000660": [_ok([_bar("20260703")])]})
    records = list(src.fetch(["005930", "000660"]))
    assert [r["our_ticker"] for r in records] == ["000660"]
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_stop_fetch_propagates():
    # WHY: 4xx/429 는 키·쿼터 문제라 심볼 격리 대상이 아니다 — 즉시 전체 중단해야 한다.
    class BlockedClient(FakeClient):
        def request(self, method, url, *, headers=None, data=None, decode=True):
            if method == "POST":
                return self.token_body
            raise StopFetch("HTTP 429")

    src = _source({}, client=BlockedClient({"005930": []}))
    with pytest.raises(StopFetch):
        list(src.fetch(["005930"]))


def test_token_failure_is_not_isolated():
    # WHY: 토큰 발급 실패는 소스 전체 문제(키)라 심볼 단위로 삼키면 안 된다 — fetch 밖으로
    #      전파해 스텝이 error 로 드러내야 한다(전 종목이 조용히 0건이 되지 않게).
    src = _source({"005930": [_ok([_bar("20260703")])]}, client=FakeClient({}, token_body="{}"))
    with pytest.raises(RuntimeError):
        list(src.fetch(["005930"]))


def test_requests_original_unadjusted_prices():
    # WHY: bronze 는 원본(미조정) 봉을 보존해야 한다 — KIS FID_ORG_ADJ_PRC=1(원주가)이어야
    #      후속 canonical 이 조정을 재현할 수 있다. 0(수정주가)이면 조정 시점마다 값이 바뀌어
    #      원본 복원이 불가능하다(무변형 원칙 위반). 이 파라미터 계약을 잠근다.
    client = FakeClient({"005930": [_ok([_bar("20260703")]), _EMPTY]})
    list(_source({}, client=client).fetch(["005930"]))
    assert client.urls  # GET 이 실제로 나갔고
    assert all("FID_ORG_ADJ_PRC=1" in url for url in client.urls)  # 전부 원주가로 질의


def test_non_object_response_fails_loud():
    # WHY: KIS 는 항상 객체({rt_cd,...})로 답한다 — 배열·스칼라(스키마 드리프트)를 조용히
    #      넘기면 .get 이 AttributeError 로 죽거나 이상 응답이 묻힌다. 형태를 명시 검사해
    #      심볼 단위 실패로 surface(FMP 어댑터의 응답 형태 검사와 동형).
    src = _source({"005930": [json.dumps(["not", "an", "object"])]})
    records = list(src.fetch(["005930"]))
    assert records == []
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_malformed_success_missing_output2_fails_loud():
    # WHY: rt_cd=0 인데 output2 누락/비-list(malformed success·스키마 드리프트)를 정상 빈
    #      페이지로 취급하면 success 0건으로 위장된다 — 빈 list([])와 구분해 fail-loud 해야
    #      한다(빈 [] 는 정상 종료지만, 키 누락은 이상 신호라 심볼 실패로 surface).
    src = _source({"005930": [json.dumps({"rt_cd": "0"})]})  # output2 키 자체가 없음
    records = list(src.fetch(["005930"]))
    assert records == []
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_dateless_rows_preserved_not_dropped():
    # WHY: bronze 는 받은 행을 버리지 않는다(FMP 가격이 date 없는 dict 행도 보존하는 것과 동형).
    #      stck_bsop_date 없는 이상치를 조용히 드롭하면 스키마 드리프트가 묻힌다 — 날짜 있는
    #      봉은 정상 수집하고, 날짜 없는 행은 원본+provenance 로 raw 에 보존돼야 한다.
    dateless = {"stck_oprc": "100", "stck_clpr": "110"}  # stck_bsop_date 없음
    src = _source({"005930": [_ok([_bar("20260703"), dateless]), _EMPTY]})
    records = list(src.fetch(["005930"]))

    assert "20260703" in [r.get("stck_bsop_date") for r in records]  # 정상 봉 수집
    preserved = [r for r in records if r.get("stck_bsop_date") is None]
    assert len(preserved) == 1  # 날짜 없는 이상치도 보존(드롭 아님)
    assert preserved[0]["market"] == "KR" and preserved[0]["fetched_at"]  # provenance 부착
    assert not src.fetch_failures  # 정상 dict 행이라 실패가 아님(보존으로 surface)


def test_all_dateless_page_marked_incomplete():
    # WHY: 행은 있는데 날짜 있는 행이 0인 페이지는 페이지네이션을 진전시킬 수 없어 창이
    #      절단될 수 있다 — 이상치는 보존하되 조용한 success 가 아니라 실패로 surface 해야 한다
    #      (빈 응답 raw_chunk==[] 은 정상 종료라 이와 구분한다).
    dateless = {"stck_oprc": "100", "stck_clpr": "110"}
    src = _source({"005930": [_ok([dateless])]})  # 첫 페이지가 전부 날짜 없음
    records = list(src.fetch(["005930"]))
    assert len(records) == 1 and records[0].get("stck_bsop_date") is None  # 이상치 보존
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]  # 그러나 실패로 surface


def test_max_pages_truncation_is_noted(monkeypatch):
    # WHY: 안전상한(MAX_PAGES)에 걸려 창이 절단되면 조용히 버리지 않고 실패로 기록해 런을
    #      partial 로 드러내야 한다(구간 좁혀 재실행 신호).
    monkeypatch.setattr(kis_price, "MAX_PAGES", 2)
    # 매 페이지가 계속 새 과거 봉을 줘서 창 하한(없음)에 못 닿음 → 절단.
    src = _source({
        "005930": [_ok([_bar("20260703")]), _ok([_bar("20260702")]), _ok([_bar("20260701")])],
    })
    records = list(src.fetch(["005930"]))  # from_date 없음 → new==0 로만 멈춤
    assert len(records) == 2  # 2페이지분은 수집
    assert any("MAX_PAGES" in f["error"] for f in src.fetch_failures)
