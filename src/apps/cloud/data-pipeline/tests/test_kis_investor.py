"""KIS 투자자 수급 어댑터 테스트 — 심볼매핑·수집메타·페이지네이션·EGW00201 재시도·격리 (네트워크 없음).

각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9). 가격 어댑터(kis_price)에서 이식한 로직이
edge 관례 인터페이스(격리·fail-loud·bronze 무변형)를 지키는지 실키 없이 FakeClient 로 잠근다.
차이는 창 파라미터가 FID_INPUT_DATE_1 하나(기준일=창 끝)라는 점이다.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.config import KisInvestorSource as KisInvestorSourceConfig
from data_pipeline.sources import kis_investor
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_investor import KisInvestorSource

_MAP = {"005930": "005930", "000660": "000660"}

KST = timezone(timedelta(hours=9))
# 거래일(월) 의 서빙 해소 시각(15:41 KST) 직후 — OPSQ2001 재시도가 살아 있는 구간.
# 경계 자체를 다루지 않는 기존 테스트들은 여기에 시계를 못박아 실행 시각과 무관하게 만든다.
_RETRYABLE_NOW = datetime(2026, 7, 27, 15, 41, 30, tzinfo=KST)


def _qs(url: str, key: str) -> str:
    return url.split(f"{key}=")[1].split("&")[0] if f"{key}=" in url else ""


def _row(date: str, prsn: str = "-70203") -> dict:
    # headline(개인·외국인·기관계) + 기관세부 일부(연기금=fund). 실측 필드명·zero-pad 문자열.
    return {
        "stck_bsop_date": date,
        "prsn_ntby_qty": prsn, "prsn_ntby_tr_pbmn": "-3190",
        "frgn_ntby_qty": "39367", "frgn_ntby_tr_pbmn": "1713",
        "orgn_ntby_qty": "11941", "orgn_ntby_tr_pbmn": "660",
        "fund_ntby_qty": "5000", "fund_ntby_tr_pbmn": "250",
    }


def _ok(rows: list[dict]) -> str:
    return json.dumps({"rt_cd": "0", "output2": rows})


_EMPTY = json.dumps({"rt_cd": "0", "output2": []})
_RATE = json.dumps({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"})
_BOUNDARY = json.dumps({"rt_cd": "2", "msg_cd": "OPSQ2001", "msg1": "TIME LIMIT 00:00 ~ 15:40"})
_ERR = json.dumps({"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "조회 오류"})
_TOKEN = json.dumps({"access_token": "tok", "access_token_token_expired": "2026-07-07 00:00:00"})


class FakeClient:
    """POST=토큰, GET=심볼별 페이지 응답(리스트를 순서대로 소비). 대기는 기록만 하고 no-op.

    심볼 값이 **호출가능**이면 리스트 대신 `(지금) -> 응답본문` 으로 부른다 — 응답이 시각에
    달린 벤더(OPSQ2001 서빙 블랙아웃)를 모사하기 위함. `clock` 을 주면 `_sleep` 이 그 시계를
    실제로 흐르게 해, 백오프가 해소 시각을 넘기는지를 테스트가 진짜로 검증한다(ALPHA-562).
    """

    def __init__(self, chunk_responses, token_body: str = _TOKEN, clock=None):
        self.chunk_responses = chunk_responses
        self.token_body = token_body
        self.clock = clock
        self.calls: list[str] = []
        self.urls: list[str] = []
        self.sleeps: list[float] = []   # 실제로 기다린 시간 — 헛기다림 검증용(ALPHA-562)
        self._idx: dict[str, int] = defaultdict(int)

    def _sleep(self, secs):
        self.sleeps.append(secs)
        if self.clock is not None:
            self.clock["now"] += timedelta(seconds=secs)

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.calls.append(method)
        if method == "POST":
            return self.token_body
        self.urls.append(url)
        sym = _qs(url, "FID_INPUT_ISCD")
        pages = self.chunk_responses.get(sym, [])
        if callable(pages):
            return pages(self.clock["now"])
        idx = self._idx[sym]
        self._idx[sym] += 1
        return pages[idx] if idx < len(pages) else _EMPTY


def _at(monkeypatch, when):
    """어댑터가 보는 '지금'(KST)을 `when` 으로 못박고, 흐르게 할 수 있는 시계를 돌려준다.

    OPSQ2001 재시도는 서빙 해소 시각(15:41 KST)까지 남은 시간에 좌우되므로(ALPHA-562), 경계
    관련 테스트가 실행 시각에 따라 결과가 갈리지 않게 못박는다. 안 박으면 오전에 돌린 CI 와
    오후에 돌린 CI 가 다른 것을 검증한다. `datetime` 서브클래스로 갈아끼우는 이유는 어댑터가
    `strptime` 도 쓰기 때문 — 아무 객체나 넣으면 페이지네이션이 깨진다.
    """
    state = {"now": when}

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return state["now"]

    monkeypatch.setattr(kis_investor, "datetime", _Clock)
    return state


def _boundary_until(clears, after):
    """`clears` 전에는 OPSQ2001, 이후에는 `after` 페이지를 순서대로 주는 응답기.

    벤더가 특정 시각에 스스로 풀리는 것을 모사한다 — 고정 페이지 리스트로는 "백오프가 해소
    시각을 실제로 넘겼는가"를 검증할 수 없고, 넘기지 못하는 구현도 통과한다(Rule 9).
    """
    pages = list(after)

    def _next(now):
        if now < clears:
            return _BOUNDARY
        return pages.pop(0) if pages else _EMPTY

    return _next


def _source(chunk_responses, *, app_key="k", app_secret="s", symbol_map=None, client=None):
    config = KisInvestorSourceConfig(
        env="prod",
        app_key=app_key,
        app_secret=app_secret,
        symbol_map=_MAP if symbol_map is None else symbol_map,
    )
    return KisInvestorSource(config, client or FakeClient(chunk_responses))


def test_disabled_without_credentials():
    # WHY: 앱키·시크릿이 없으면 무의미한 401 을 두드린다 — 소스 스스로 비활성을 드러내고
    #      호출부(스텝)가 skip 처리한다. 둘 중 하나만 없어도 비활성.
    assert _source({}, app_key=None).enabled is False
    assert _source({}, app_secret=None).enabled is False
    assert _source({}, app_key="k", app_secret="s").enabled is True


def test_universe_from_holdings_opt_in():
    # WHY: 대상이 ETF 가 아니라 그 구성종목이라 유니버스는 가격과 같은 축(holdings 파생)이어야
    #      한다 — 스텝이 이 플래그를 보고 canonical KR holdings 를 targets 에 union 한다.
    assert _source({}).universe_from_holdings is True


def test_plan_maps_six_digit_identity_and_skips_foreign():
    # WHY: KRX 6자리 코드는 KIS 코드와 항등이라 맵 없이도 수집돼야 한다(구성종목이 holdings 에서
    #      파생돼 오므로). 비6자리(US 등)만 제외 — KIS 는 국내 전용이라 US 티커를 질의하면 안 된다.
    plan = _source({}, symbol_map={}).plan(["005930", "NVDA", "000660", "AAPL"])
    assert plan == [("005930", "005930"), ("000660", "000660")]


def test_plan_maps_alphanumeric_short_code_identity():
    # WHY: 신규 상장분 단축코드에는 문자가 섞이고(0093A0 등) KIS 는 그대로 받는다 — 숫자로만
    #      보면 이 종목들이 항등 매핑을 못 받아 조용히 빠진다(ALPHA-463 축). 6자 US/한글은 제외.
    plan = _source({}, symbol_map={}).plan(["0093A0", "0005G0", "NVDA", "ABCDEF", "가나다라마바"])
    assert plan == [("0093A0", "0093A0"), ("0005G0", "0005G0")]


def test_plan_symbol_map_overrides_identity():
    # WHY: symbol_map 은 항등이 아닌 예외의 오버라이드 축으로 남는다 — 맵이 있으면 맵이 이긴다.
    plan = _source({}, symbol_map={"005930": "005935"}).plan(["005930"])
    assert plan == [("005930", "005935")]


def test_fetch_attaches_meta_and_preserves_raw():
    # WHY: raw 존 행은 어느 our_ticker/market/kis_symbol 로 왔는지가 있어야 후속 정규화·재현이
    #      가능하다. output2 원본 필드(순매수 수량·대금)는 무변형 보존해야 한다(bronze).
    src = _source({"005930": [_ok([_row("20260703", prsn="-12345")])]})
    records = list(src.fetch(["005930"]))

    assert len(records) == 1
    rec = records[0]
    assert rec["our_ticker"] == "005930"
    assert rec["market"] == "KR"
    assert rec["kis_symbol"] == "005930"
    assert rec["fetched_at"]
    assert rec["prsn_ntby_qty"] == "-12345"  # 원본 순매수 보존
    assert rec["fund_ntby_qty"] == "5000"  # 연기금 세부도 무변형 보존
    assert rec["stck_bsop_date"] == "20260703"


def test_token_issued_once_across_symbols():
    # WHY: 종목마다 토큰을 발급하면 KIS 분당 한도에 걸린다 — run 당 1회만 발급해야 한다.
    src = _source({"005930": [_ok([_row("20260703")])], "000660": [_ok([_row("20260703")])]})
    list(src.fetch(["005930", "000660"]))
    assert src.client.calls.count("POST") == 1


def test_window_end_is_input_date_1_no_date_2():
    # WHY: 이 엔드포인트는 창 파라미터가 FID_INPUT_DATE_1(기준일=창 끝) 하나뿐이다(일봉과 달리
    #      DATE_2 가 없다). 잘못 배선하면 창이 어긋난다 — 파라미터 계약을 잠근다.
    client = FakeClient({"005930": [_ok([_row("20260703")]), _EMPTY]})
    list(_source({}, client=client).fetch(["005930"], to_date="2026-07-05"))
    assert client.urls
    assert all("FID_INPUT_DATE_1=20260705" in url for url in client.urls[:1])
    assert all("FID_INPUT_DATE_2" not in url for url in client.urls)


def test_pagination_walks_back_and_dedups():
    # WHY: 콜당 ≤30거래일이라 장기 창은 기준일을 뒤로 물려 최신→과거로 페이지네이션한다. 페이지
    #      경계에서 같은 거래일이 겹쳐 와도 raw 는 거래일 기준으로 중복 없이 보존한다.
    src = _source({
        "005930": [
            _ok([_row("20260703"), _row("20260702")]),
            _ok([_row("20260702"), _row("20260701")]),  # 20260702 겹침
        ],
    })
    records = list(src.fetch(["005930"], from_date="2026-07-01"))
    dates = [r["stck_bsop_date"] for r in records]
    assert dates == ["20260701", "20260702", "20260703"]


def test_from_date_filters_rows_below_lower_bound():
    # WHY: 이 엔드포인트는 FID_INPUT_DATE_1(창 끝) 하나만 받아 그 날부터 과거 ~30거래일을 통째로
    #      준다(kis_price 는 시작일 파라미터로 서버가 하한을 거르지만 여긴 못 건다). d1 아래 행까지
    #      저장하면 증분 run 마다 창 밖 파티션이 raw·canonical 에 얹혀 매일 재작성된다 — 요청 창
    #      [d1,d2] 로 좁혀야 한다(edge-review·Codex 공동 지적).
    src = _source({"005930": [_ok([_row("20260703"), _row("20260702"), _row("20260630")])]})
    records = list(src.fetch(["005930"], from_date="2026-07-02"))
    dates = [r["stck_bsop_date"] for r in records]
    assert dates == ["20260702", "20260703"]  # 20260630 은 d1 아래라 제외


def test_egw00201_retried_then_succeeds():
    # WHY: 초당한도(EGW00201)는 HTTP 429 가 아니라 응답 본문으로 온다 — 어댑터가 재시도해야
    #      행을 놓치지 않는다.
    src = _source({"005930": [_RATE, _ok([_row("20260703")]), _EMPTY]})
    records = list(src.fetch(["005930"]))
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]


def test_opsq2001_boundary_retried_then_succeeds(monkeypatch):
    # WHY: EOD 서빙경계(OPSQ2001, "TIME LIMIT 00:00 ~ 15:40")는 장마감 직후 ~1분 블랙아웃 후
    #      자가해소한다(ALPHA-518). 데이터 결손이 아니라 경계 레이스라 재시도로 복구해야 —
    #      첫 스케줄 런이 이걸 부분실패로 판정해 전체 FAILED 났다. 격리로 끝내면 안 된다.
    _at(monkeypatch, _RETRYABLE_NOW)
    src = _source({"005930": [_BOUNDARY, _ok([_row("20260703")]), _EMPTY]})
    records = list(src.fetch(["005930"]))
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]
    assert src.fetch_failures == []  # 재시도로 복구 → 격리 기록 없음


def test_egw_and_opsq_have_independent_retry_budgets(monkeypatch):
    # WHY: 초당한도(EGW00201)와 서빙경계(OPSQ2001)는 성격이 달라 재시도 예산이 독립이어야 —
    #      공유 카운터면 EGW 재시도가 attempt 를 소비해 뒤이은 OPSQ 가 예산 부족으로 조기 격리되고,
    #      경계는 자가해소하는데 EGW 노이즈 때문에 종목을 잃어 이 티켓의 취지가 깨진다(ALPHA-518).
    #      최악 시퀀스(EGW 예산 전량 + OPSQ 예산 전량 소비 뒤 다음 콜에서 성공)를 상수로 구성한다 —
    #      이건 두 가지를 동시에 고정한다: (1) 두 예산의 독립성(공유 카운터면 OPSQ 가 조기 소진돼
    #      격리) (2) 루프 상한이 max 가 아니라 두 예산의 합이라는 것(max 면 마지막 성공 콜 전에 종료).
    _at(monkeypatch, _RETRYABLE_NOW)
    egw_budget = kis_investor.MAX_RATE_RETRY - 1  # EGW 는 rate<MAX-1 이라 MAX-1 회 재시도
    opsq_budget = kis_investor.MAX_BOUNDARY_RETRY  # OPSQ 는 boundary<MAX 라 MAX 회 재시도
    pages = [_RATE] * egw_budget + [_BOUNDARY] * opsq_budget + [_ok([_row("20260703")]), _EMPTY]
    src = _source({"005930": pages})
    records = list(src.fetch(["005930"]))
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]
    assert src.fetch_failures == []


def test_opsq2001_boundary_exhausted_isolated_per_symbol(monkeypatch):
    # WHY: 백오프가 소진되도록 경계가 안 풀리면(비정상) 조용한 성공이 아니라 심볼 단위 실패로
    #      기록돼 런을 partial 로 드러내야 한다(fail-loud) — 다른 심볼은 계속 수집.
    #      예산+1 회 연속 경계면 재시도를 다 쓰고도 성공 못 해 격리된다.
    _at(monkeypatch, _RETRYABLE_NOW)
    boundary_pages = [_BOUNDARY] * (kis_investor.MAX_BOUNDARY_RETRY + 1)
    src = _source({"005930": boundary_pages, "000660": [_ok([_row("20260703")])]})
    records = list(src.fetch(["005930", "000660"]))
    assert [r["our_ticker"] for r in records] == ["000660"]
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


@pytest.mark.parametrize(
    "when, why",
    [
        (datetime(2026, 7, 26, 10, 0, tzinfo=KST), "비거래일(일) 오전 — 실측된 실행 시각"),
        (datetime(2026, 7, 26, 16, 0, tzinfo=KST), "비거래일(일) 해소시각 이후 — 그날 EOD 자체가 없다"),
        (datetime(2026, 7, 27, 15, 39, 44, tzinfo=KST), "거래일이지만 예산 75s 로도 15:40:59 라 15:41 미도달"),
    ],
)
def test_opsq2001_isolated_without_waiting_when_it_cannot_clear(monkeypatch, when, why):
    # WHY: `TIME LIMIT 00:00 ~ 15:40` 은 일시 장애가 아니라 "지금이 서빙 개시 전"이라는 상시
    #      조건이라, 풀릴 수 없는 시점에는 기다려도 절대 안 풀린다. 그런데 예산은 심볼별로
    #      독립이라(ALPHA-518) 헛기다림이 유니버스 크기만큼 곱해진다 — 2026-07-26 비거래일
    #      실행에서 심볼당 75.8초가 실측됐고, 470종이면 ~10시간으로 SFN 6시간 타임아웃을
    #      넘긴다. 그러니 대기 없이(sleeps 비어야 함) 콜 1회로 격리해야 한다.
    #      "격리된다"만 보면 75초를 태우고 격리해도 통과하므로 대기 자체를 못박는다.
    #      비거래일 케이스가 시각 양쪽에 있는 이유: 해소시각만 보고 거래일을 안 보면 같은
    #      결함이 "일요일 16시"로 자리만 옮긴다.
    _at(monkeypatch, when)
    client = FakeClient({"005930": [_BOUNDARY] * 10, "000660": [_ok([_row("20260703")])]})
    src = _source(None, client=client)
    records = list(src.fetch(["005930", "000660"]))

    assert client.sleeps == [], f"{why} — 자가해소 불가 구간에서 기다렸다"
    assert client.urls.count(client.urls[0]) == 1  # 문제 심볼은 재질의 없이 1콜
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]
    assert [r["our_ticker"] for r in records] == ["000660"]  # 다른 심볼은 계속 수집


def test_opsq2001_waits_through_the_real_clearing_time(monkeypatch):
    # WHY: 가드를 "해소시각 이전이면 무조건 포기"로 단순화하면 ALPHA-518 이 고친 레이스가
    #      되살아난다 — 실측(15:40:53~59 실패, 15:41:00 이후 성공)대로 15:40:53 의 경계는
    #      예산 안에 진짜로 풀린다. 벤더가 15:41 에 풀리는 것을 시계와 함께 모사해, 백오프가
    #      **실제로 그 시각을 넘겼을 때만** 통과하게 한다. 고정 페이지 리스트로 첫 대기 직후
    #      성공을 주면 해소시각을 못 넘기는 구현도 초록이라 회귀를 못 잡는다(Rule 9).
    clears = datetime(2026, 7, 27, 15, 41, 0, tzinfo=KST)
    clock = _at(monkeypatch, datetime(2026, 7, 27, 15, 40, 53, tzinfo=KST))
    client = FakeClient(
        {"005930": _boundary_until(clears, [_ok([_row("20260703")])])}, clock=clock
    )
    src = _source(None, client=client)
    records = list(src.fetch(["005930"]))

    assert client.sleeps == [kis_investor.BOUNDARY_BACKOFF_S]  # 15s 한 번으로 15:41:08
    assert clock["now"] >= clears
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]
    assert src.fetch_failures == []


def test_kis_error_code_isolated_per_symbol():
    # WHY: 한 종목의 KIS 오류코드(rt_cd!=0)가 나머지 종목 수집을 죽이면 안 된다 — 격리 후 계속,
    #      단 실패로 기록돼야 한다(조용한 성공 금지).
    src = _source({"005930": [_ERR], "000660": [_ok([_row("20260703")])]})
    records = list(src.fetch(["005930", "000660"]))
    assert [r["our_ticker"] for r in records] == ["000660"]
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_bad_json_isolated_per_symbol():
    # WHY: 한 종목의 깨진 응답이 나머지 수집을 끊으면 안 된다 — 격리 후 계속.
    src = _source({"005930": ["{broken"], "000660": [_ok([_row("20260703")])]})
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
    #      전파해 스텝이 error 로 드러내야 한다.
    src = _source({"005930": [_ok([_row("20260703")])]}, client=FakeClient({}, token_body="{}"))
    with pytest.raises(RuntimeError):
        list(src.fetch(["005930"]))


def test_non_object_response_fails_loud():
    # WHY: KIS 는 항상 객체({rt_cd,...})로 답한다 — 배열·스칼라(스키마 드리프트)를 조용히 넘기면
    #      .get 이 AttributeError 로 죽거나 이상 응답이 묻힌다. 형태를 명시 검사해 심볼 실패로 surface.
    src = _source({"005930": [json.dumps(["not", "an", "object"])]})
    records = list(src.fetch(["005930"]))
    assert records == []
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_malformed_success_missing_output2_fails_loud():
    # WHY: rt_cd=0 인데 output2 누락/비-list 를 정상 빈 페이지로 취급하면 success 0건으로
    #      위장된다 — 빈 list([])와 구분해 fail-loud 해야 한다.
    src = _source({"005930": [json.dumps({"rt_cd": "0"})]})  # output2 키 자체가 없음
    records = list(src.fetch(["005930"]))
    assert records == []
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_output2_single_object_wrapped_not_failed():
    # WHY: 단일 거래일(1일 창·신규상장)은 KIS 가 output2 를 dict 하나로 줄 수 있다(공식 샘플도
    #      list 아니면 단일 객체로 감싼다) — 유효한 1행을 output2 이상으로 실패 처리하면 안 된다.
    page = json.dumps({"rt_cd": "0", "output2": _row("20260703")})  # list 아닌 dict 하나
    src = _source({"005930": [page, _EMPTY]})
    records = list(src.fetch(["005930"]))
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]  # 감싸서 정상 수집
    assert not src.fetch_failures


def test_non_dict_row_isolated_not_crashing_symbol():
    # WHY: output2 배열에 dict 아닌 행(스키마 드리프트)이 섞여도 한 행이 심볼 전체를 끊으면 안
    #      된다 — 기록 후 스킵하고 정상 행은 계속 수집한다(조용히 버리지 않는다, Rule 12).
    page = json.dumps({"rt_cd": "0", "output2": [_row("20260703"), "not-a-dict"]})
    src = _source({"005930": [page, _EMPTY]})
    records = list(src.fetch(["005930"]))
    assert [r["stck_bsop_date"] for r in records] == ["20260703"]  # 정상 행 보존
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]  # 이상 행은 실패로 surface


def test_dateless_rows_preserved_not_dropped():
    # WHY: bronze 는 받은 행을 버리지 않는다 — stck_bsop_date 없는 이상치를 조용히 드롭하면
    #      스키마 드리프트가 묻힌다. 날짜 있는 행은 수집하고, 날짜 없는 dict 행은 원본+provenance 보존.
    dateless = {"prsn_ntby_qty": "1", "frgn_ntby_qty": "2"}  # stck_bsop_date 없음
    src = _source({"005930": [_ok([_row("20260703"), dateless]), _EMPTY]})
    records = list(src.fetch(["005930"]))

    assert "20260703" in [r.get("stck_bsop_date") for r in records]
    preserved = [r for r in records if r.get("stck_bsop_date") is None]
    assert len(preserved) == 1
    assert preserved[0]["market"] == "KR" and preserved[0]["fetched_at"]
    assert not src.fetch_failures  # 정상 dict 행이라 실패가 아님(보존으로 surface)


def test_all_dateless_page_marked_incomplete():
    # WHY: 행은 있는데 날짜 있는 행이 0인 페이지는 페이지네이션을 진전시킬 수 없어 창이 절단될
    #      수 있다 — 이상치는 보존하되 조용한 success 가 아니라 실패로 surface(빈 응답과 구분).
    dateless = {"prsn_ntby_qty": "1"}
    src = _source({"005930": [_ok([dateless])]})
    records = list(src.fetch(["005930"]))
    assert len(records) == 1 and records[0].get("stck_bsop_date") is None
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_max_pages_truncation_is_noted(monkeypatch):
    # WHY: 안전상한(MAX_PAGES)에 걸려 창이 절단되면 조용히 버리지 않고 실패로 기록해 런을
    #      partial 로 드러내야 한다(구간 좁혀 재실행 신호).
    monkeypatch.setattr(kis_investor, "MAX_PAGES", 2)
    src = _source({
        "005930": [_ok([_row("20260703")]), _ok([_row("20260702")]), _ok([_row("20260701")])],
    })
    records = list(src.fetch(["005930"]))  # from_date 없음 → new==0 로만 멈춤
    assert len(records) == 2
    assert any("MAX_PAGES" in f["error"] for f in src.fetch_failures)
