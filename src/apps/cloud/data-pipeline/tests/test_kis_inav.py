"""KisInavSource 어댑터 테스트 — iNAV 고유 계약만 본다.

토큰 1회 발급·단축코드 파생·rt_cd 판정·EGW00201 재시도·malformed 행 격리는 부모
(KisNavSource)의 코드를 그대로 타므로 test_kis_nav 가 이미 덮는다. 여기선 **갈리는 지점**만
검증한다 — 시장코드·tr_id·간격 파라미터·날짜 미전송·간격 각인. 이 넷이 조용히 틀리면
전 종목이 실패하거나(시장코드) 자연키가 충돌한다(간격).
"""

import json
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from data_pipeline.config import KisNavSource as KisNavSourceConfig
from data_pipeline.sources.kis_inav import DEFAULT_INTERVAL_SEC, KisInavSource

# 라이브 실측 행(2026-07-25, 069500, cls=60). 필드명·문자열 타입을 그대로 고정한다.
# 일별 응답과 달리 **날짜 필드가 없다** — bsop_hour(HHMMSS)뿐인 것이 이 API 의 성질이다.
LIVE_ROW = {
    "bsop_hour": "153000", "nav": "106243.76", "nav_prdy_vrss_sign": "5",
    "nav_prdy_vrss": "-7131.27", "nav_prdy_ctrt": "-6.71", "nav_vrss_prpr": "121.24",
    "dprt": "0.11", "stck_prpr": "106365", "prdy_vrss": "-6865", "prdy_vrss_sign": "5",
    "prdy_ctrt": "-6.06", "acml_vol": "15610871", "cntg_vol": "51584",
}


class FakeAuth:
    def token(self):
        return "TOKEN"


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.queries = []
        self.headers = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.queries.append(params)
        self.headers.append(headers or {})
        return json.dumps(self.responses.get(params["FID_INPUT_ISCD"][0], {"rt_cd": "0", "output": []}))

    def _sleep(self, seconds):
        pass


def _source(responses, interval_sec=DEFAULT_INTERVAL_SEC, etf_map=None):
    src = KisInavSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        etf_map or {"069500": "KR7069500007"},
        FakeClient(responses),
        interval_sec=interval_sec,
    )
    src.auth = FakeAuth()
    return src


def _ok(rows):
    return {"rt_cd": "0", "output": rows}


def test_시장코드는_E_다_일별의_J_가_아니다():
    """`J` 를 보내면 KIS 가 rt_cd=2 로 전건 튕긴다(실측) — 일별 어댑터에서 상속만 하고
    이 값을 안 갈면 수집이 통째로 죽는다. 값으로 못박는다."""
    src = _source({"069500": _ok([LIVE_ROW])})
    list(src.fetch())

    assert src.client.queries[0]["FID_COND_MRKT_DIV_CODE"] == ["E"]
    assert src.client.headers[0]["tr_id"] == "FHPST02440100"


def test_간격이_KIS_파라미터로_전달된다():
    """간격이 곧 조회 창(간격×30)이라 배선이 끊기면 폴링 주기와 창이 어긋나 갭이 난다.
    iNAV 는 소급이 안 돼 그 갭이 영구 유실이다."""
    src = _source({"069500": _ok([LIVE_ROW])}, interval_sec=180)
    list(src.fetch())

    assert src.client.queries[0]["FID_HOUR_CLS_CODE"] == ["180"]
    assert src.window_sec == 5400  # 180초(3분) × 30행 = 90분치


def test_날짜_파라미터를_싣지_않는다():
    """이 API 는 날짜·시각 지정을 무시한다(실측: FID_INPUT_HOUR_1 을 바꿔도 응답 동일).
    무시되는 파라미터를 실으면 코드를 읽는 쪽이 소급 조회가 되는 줄 착각한다."""
    src = _source({"069500": _ok([LIVE_ROW])})
    list(src.fetch())

    params = src.client.queries[0]
    assert "FID_INPUT_DATE_1" not in params
    assert "FID_INPUT_DATE_2" not in params
    assert "FID_INPUT_HOUR_1" not in params


def test_간격이_행에_각인된다():
    """같은 bsop_hour 라벨이라도 간격이 다르면 값이 다르다(실측) — 이 필드가 없으면
    후속 canonical 의 자연키가 간격을 바꾸는 순간 조용히 덮어쓴다."""
    src = _source({"069500": _ok([LIVE_ROW])}, interval_sec=180)
    records = list(src.fetch())

    assert records[0]["interval_sec"] == 180


def test_원본_행을_무변형_보존하고_provenance_를_붙인다():
    """bronze 규약 — 필드 선별은 canonical 소관이다. dprt(괴리율)·거래량도 그대로 남는다."""
    src = _source({"069500": _ok([LIVE_ROW])})
    records = list(src.fetch())

    assert len(records) == 1
    for key, value in LIVE_ROW.items():
        assert records[0][key] == value
    assert records[0]["our_etf_id"] == "069500"
    assert records[0]["market"] == "KR"
    assert records[0]["kis_symbol"] == "069500"
    # 응답에 날짜가 없어 거래일을 수집 시각으로 붙여야 한다 — 휴장일 유령 as-of 를
    # 나중에 교정할 유일한 근거라 반드시 남아야 한다.
    assert records[0]["fetched_at"]


@pytest.mark.parametrize("bad", [0, -1, 1, 30, 59])
def test_간격이_1_미만이면_생성에서_막는다(bad):
    """KIS 가 0·음수에 무엇을 주는지 확인된 바 없다 — 미확인 값을 조용히 흘리지 않는다."""
    with pytest.raises(ValueError, match="interval_sec"):
        KisInavSource(
            KisNavSourceConfig(app_key="k", app_secret="s"),
            {"069500": "KR7069500007"},
            FakeClient({}),
            interval_sec=bad,
        )


def test_일별_어댑터의_질의는_그대로다():
    """훅 추출이 일별 NAV 의 질의를 바꾸지 않았는지 — 회귀 방지."""
    from data_pipeline.sources.kis_nav import KisNavSource

    src = KisNavSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        {"069500": "KR7069500007"},
        FakeClient({"069500": _ok([{"nav": "1"}])}),
        "2026-06-01",
        "2026-07-17",
    )
    src.auth = FakeAuth()
    records = list(src.fetch())

    params = src.client.queries[0]
    assert params["FID_COND_MRKT_DIV_CODE"] == ["J"]
    assert params["FID_INPUT_DATE_1"] == ["20260601"]
    assert params["FID_INPUT_DATE_2"] == ["20260717"]
    assert src.client.headers[0]["tr_id"] == "FHPST02440200"
    assert "interval_sec" not in records[0]  # 일별에는 간격 개념이 없다


def test_필수_필드_결측_행은_격리되고_나머지는_수집된다():
    """bsop_hour 없이는 시각 축을, nav 없이는 값을 못 만든다. 그런 행을 그대로 저장하면
    collection_log 는 success 인데 다운스트림이 못 쓴다 — 수집 실패의 성공 위장이다(Rule 12)."""
    broken = {k: v for k, v in LIVE_ROW.items() if k != "bsop_hour"}
    src = _source({"069500": _ok([broken, LIVE_ROW])})
    records = list(src.fetch())

    assert [r["bsop_hour"] for r in records] == ["153000"]  # 성한 행은 계속 수집
    assert len(src.fetch_failures) == 1
    assert "bsop_hour" in src.fetch_failures[0]["error"]
    assert src.fetch_failures[0]["our_etf_id"] == "069500"


def test_빈_문자열도_결측으로_본다():
    """KIS 는 값을 문자열로 준다 — 키는 있는데 빈 문자열이면 없는 것과 같다."""
    src = _source({"069500": _ok([{**LIVE_ROW, "nav": ""}])})
    list(src.fetch())

    assert "nav" in src.fetch_failures[0]["error"]


def test_일별_어댑터는_필드_결측을_거르지_않는다():
    """bronze 무변형 — 일별 NAV 는 형태만 본다. iNAV 의 필수 필드 규칙이 부모로 새면
    기존 수집이 조용히 좁아진다(Rule 3)."""
    from data_pipeline.sources.kis_nav import KisNavSource

    src = KisNavSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        {"069500": "KR7069500007"},
        FakeClient({"069500": _ok([{"nav": "1"}])}),
    )
    src.auth = FakeAuth()
    records = list(src.fetch())

    assert len(records) == 1
    assert src.fetch_failures == []


@pytest.mark.parametrize("nav", [0, "0", 0.0, "0.00"])
def test_값이_0_인_행은_결측이_아니다(nav):
    """falsy 판정으로 결측을 가리면 nav=0 인 멀쩡한 원본이 격리돼 사라진다. iNAV 는
    소급 조회가 안 돼 그 유실이 영구적이다 — 결측은 값이 아니라 존재 여부로만 본다."""
    src = _source({"069500": _ok([{**LIVE_ROW, "nav": nav}])})
    records = list(src.fetch())

    assert len(records) == 1
    assert src.fetch_failures == []


@pytest.mark.parametrize("nav", [None, "", "   "])
def test_없거나_공백뿐인_값은_결측이다(nav):
    src = _source({"069500": _ok([{**LIVE_ROW, "nav": nav}])})
    list(src.fetch())

    assert "nav" in src.fetch_failures[0]["error"]


# ── 기준일 가드(ALPHA-557) ────────────────────────────────────────────────
# WHY: 응답에 날짜가 없어 거래일을 수집 시각으로 붙이는데, KIS 는 오늘 데이터가 없어도
#      직전 거래일 값을 준다. 두 성질이 겹치면 옛 값이 오늘 파티션에 앉는다(유령 as-of).
#      2026-07-25 토요일 로컬 실행이 7/24 데이터 930행을 적재한 것이 실측 증거다.

KST = timezone(timedelta(hours=9))


def _at(monkeypatch, when: datetime):
    """어댑터가 보는 '지금'(KST)을 고정한다."""
    import data_pipeline.sources.kis_inav as mod

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # ⚠️ tz 를 무시하면 안 된다 — 무시하는 순간 `datetime.now(KST)` 를
            # `datetime.now()` 로 바꾸는 회귀가 테스트를 통과하고, 운영(UTC 컨테이너)
            # 에서만 naive−aware TypeError 로 터진다. 그 예외는 관측 격리가 삼켜
            # 로그에 "행 관측 실패" 만 남는다(수집은 살아 있어 아무도 안 본다).
            return when if tz is not None else when.replace(tzinfo=None)

    monkeypatch.setattr(mod, "datetime", _Clock)


@pytest.mark.parametrize(
    "when, expected",
    [
        (datetime(2026, 7, 25, 14, 0, tzinfo=KST), "non-trading day"),   # 토
        (datetime(2026, 7, 26, 14, 0, tzinfo=KST), "non-trading day"),   # 일
        (datetime(2026, 7, 27, 8, 59, tzinfo=KST), "before market open"),  # 월, 개장 1분 전
    ],
)
def test_오늘_데이터가_없는_시점은_수집하지_않는다(monkeypatch, when, expected):
    """막지 않으면 옛 값이 오늘 날짜로 적재되고, 소급이 안 돼 교정 말고는 되돌릴 수 없다."""
    _at(monkeypatch, when)
    src = _source({"069500": _ok([LIVE_ROW])})

    assert expected in src.skip_reason


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 7, 27, 9, 0, tzinfo=KST),    # 개장 정각 — 경계는 통과쪽
        datetime(2026, 7, 27, 14, 0, tzinfo=KST),   # 장중
        datetime(2026, 7, 27, 15, 45, tzinfo=KST),  # 장 마감 후 — 오늘 종가 구간이라 라벨이 맞다
        datetime(2026, 7, 27, 23, 59, tzinfo=KST),  # 거래일 자정 직전
    ],
)
def test_거래일_개장_이후는_수집한다(monkeypatch, when):
    """장 마감 뒤까지 막으면 종가 구간(15:20~15:30 갱신분)을 영영 못 받는다 — 소급이 없다."""
    _at(monkeypatch, when)
    src = _source({"069500": _ok([LIVE_ROW])})

    assert src.skip_reason is None


def test_평일_공휴일은_휴장일로_본다(monkeypatch):
    """주말만 거르면 평일 공휴일에 유령 as-of 가 그대로 들어온다. 휴장일 집합은 Planner·KRX
    어댑터와 같은 env(OPS_KR_HOLIDAYS)를 써야 한다 — 복제하면 갈라진다."""
    monkeypatch.setenv("OPS_KR_HOLIDAYS", "2026-07-27")
    _at(monkeypatch, datetime(2026, 7, 27, 14, 0, tzinfo=KST))
    src = _source({"069500": _ok([LIVE_ROW])})

    assert "non-trading day" in src.skip_reason


# ── 응답 집합 관측 (ALPHA-845) ─────────────────────────────────────────
# 이 API 로 1분 레인을 먹일 수 있는지가 **최신 행이 얼마나 낡았나**에 걸려 있는데 한 번도
# 재지 않았다. 지연이 창 폭(간격×30)에 가까우면 REST 폴링으로는 장중 실시간이 성립하지
# 않고 웹소켓(H0STNAV0)이 유일한 경로가 된다. 관측 자체가 이 조각의 산출물이라 로그를
# 계약으로 고정한다 — 값이 틀리는 것도, 관측이 사라지는 것도 여기서 걸려야 한다.


def _row(stamp: str, **over) -> dict:
    """실측 행에서 시각 라벨만 갈아끼운다(나머지는 단위 가드가 볼 수 있게 정합 유지)."""
    return {**LIVE_ROW, "bsop_hour": stamp, **over}


def _window(latest: str = "153000") -> list[dict]:
    """실응답 형상의 축소판 — **요청 간격(60초) 격자**여야 한다. 임의 간격으로 두면
    간격 가드가 정당하게 물어, 그 경고가 모든 테스트에 상시로 껴 신호가 죽는다.

    **최신 행을 가운데 둔다** — 끝에 두면 `rows[0]` 회귀가, 앞에 두면 `rows[-1]` 회귀가
    통과한다."""
    stamps = ["152700", "152800", latest, "152900", "152600"]
    return [_row(s) for s in stamps]


def test_최신_행_기준_벤더_지연을_남긴다(monkeypatch, caplog):
    """수신시각 − 최신 bsop_hour. 이 수치가 REST/웹소켓 갈림길을 정한다.

    부모(`_fetch_etf`)가 훅을 부르는 것까지 함께 검증한다 — 훅 호출이 빠지면 어댑터에
    코드가 남아 있어도 관측은 0건이고, 그건 "지연이 0" 과 로그에서 구분되지 않는다.
    최신이 아닌 행을 고르는 회귀(`rows[0]`·`min`)는 지연 값이 1860초로 벌어져 걸린다.
    """
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))  # 최신 15:30:00 → 60초
    src = _source({"069500": _ok(_window())})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "iNAV 벤더 지연" in caplog.text
    assert "지연=60초" in caplog.text
    # 창과 간격은 **다른 값**이다(1800 = 60×30). 서로 바꿔 쓰는 회귀를 값으로 못박는다 —
    # 판정 기준이 "지연이 창 폭에 가까운가" 라서 창을 60초로 읽으면 결론이 뒤집힌다.
    assert "창=1800초 간격=60초" in caplog.text


def test_자리수_잘린_라벨이_최신을_탈취하지_못한다(monkeypatch, caplog):
    """`"9300"` 은 사전순으로 `"153000"` 보다 크고 strptime 이 09:30:00 으로 **관대하게**
    받는다. 거르지 않으면 지연 60초 표본이 21660초로 찍히고, 그 값은 창 폭을 훌쩍 넘어
    "REST 로는 실시간 불가" 라는 결론을 오염 한 줄로 만들어낸다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([*_window(), _row("9300")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "21660" not in caplog.text
    assert "형식 이탈 1/6 행" in caplog.text  # 조용히 버리지 않는다
    (rec,) = _records(caplog, "벤더 지연")
    assert rec.args[4] == 60.0
    # `행=` 은 **쓸 수 있는 행** 수다 — 받은 행(6)을 쓰면 지연을 해석하는 분모가 거짓이 된다.
    assert rec.args[7] == 5


@pytest.mark.parametrize("stamp", ["240000", "153060", "15:30:00", "abc123", " 153000"])
def test_시각이_아닌_6자리도_라벨로_받지_않는다(monkeypatch, caplog, stamp):
    """자리수만 보면 `240000`·`153060` 이 통과한다 — 실제 시각인지까지 한 곳에서 판정해야
    호출부가 파싱 실패를 다시 다루지 않는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_row(stamp)])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "지연 관측 불가" in caplog.text
    assert "iNAV 벤더 지연" not in caplog.text


def test_관측이_예외를_던져도_수집은_계속된다(monkeypatch, caplog):
    """관측은 곁다리지만 **수집은 아니다.** 훅을 감싸지 않으면 예외가 ETF 단위 격리에
    잡혀 그 ETF 의 행이 통째로 버려지고, 실패 사유 칸에 관측 메시지가 수집 실패인 것처럼
    박힌다. iNAV 는 소급 조회가 불가라 그 유실이 영구적이다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})
    monkeypatch.setattr(
        type(src), "_note_rows",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("관측 폭발")),
    )

    with caplog.at_level("INFO"):
        rows = list(src.fetch())

    assert len(rows) == 5           # 행이 살아 나온다
    assert src.fetch_failures == []  # 수집 실패로 기록되지 않는다
    assert "관측 실패" in caplog.text  # 조용히 넘어가지도 않는다


def test_라벨이_미래면_사실만_적고_원인을_단정하지_않는다(monkeypatch, caplog):
    """응답에 날짜가 없어 이 콜로는 전일 오염을 판정할 수 없다. 부호는 양방향으로 틀린다 —
    구간 끝 라벨이면 미래가 정상이고(오탐), 15:30 이후 실행에선 전일 잔값이 **양수**로
    위장한다(미탐). 못 하는 판정을 하는 척하지 않는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 9, 10, tzinfo=KST))  # 최신 15:30 → 미래
    src = _source({"069500": _ok(_window())})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "라벨이 수신시각보다 미래다" in caplog.text
    assert "전일 오염" not in caplog.text  # 단정 금지


def test_지연이_양수면_미래_문구가_붙지_않는다(monkeypatch, caplog):
    """부재 단언이 없으면 문구를 무조건 붙이는 회귀가 통과한다 — 그러면 ETF 마다·폴링마다
    경고가 떠 진짜 신호가 자기 소음에 묻힌다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "미래" not in caplog.text


def test_괴리율이_비율로_오면_단위_드리프트를_경고한다(monkeypatch, caplog):
    """`dprt` 는 퍼센트다(실측). 벤더가 비율로 바꾸면 canonical `premium_pct` 가 100배
    틀린 값을 싣는다 — 이 조각이 막으려는 사고가 그것이다. 값을 나열만 하면 드리프트가
    나도 로그 모양이 같아 아무도 못 본다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    ratio = float(LIVE_ROW["stck_prpr"]) / float(LIVE_ROW["nav"]) - 1.0
    src = _source({"069500": _ok([_row("153000", dprt=f"{ratio:.6f}")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "괴리 단위 드리프트 의심" in caplog.text


def test_퍼센트로_오는_실측_행은_조용히_통과한다(monkeypatch, caplog):
    """확정된 사실을 ETF·폴링마다 되풀이하지 않는다 — 가드는 어긋날 때만 말한다.

    이 통과 자체가 단위 판정이다: `stck_prpr/nav − 1` = 0.00114115 이고 `dprt` = 0.11 이라
    **퍼센트 가설(×100 = 0.11411)** 과만 맞는다. 비율 가설이면 위 테스트처럼 경고가 뜬다.
    """
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "드리프트" not in caplog.text


@pytest.mark.parametrize("nav", ["nan", "inf", "-inf", "-1", "0"])
def test_유한하지_않거나_양수가_아닌_nav_는_대조에서_뺀다(monkeypatch, caplog, nav):
    """`float()` 는 `"nan"`·`"inf"` 를 예외 없이 통과시킨다. 그대로 두면 오염 표본이
    그럴듯한 수치(-100.0000%)로 대조에 섞여 단위 판정을 흔든다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_row("153000", nav=nav)])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "괴리 단위 대조 불가" in caplog.text
    assert "드리프트" not in caplog.text


def test_시각_라벨이_깨져도_단위_가드는_돈다(monkeypatch, caplog):
    """두 축은 서로의 실패로 죽지 않는다 — return 을 공유하면 라벨 한 줄 때문에 그 ETF
    표본이 단위 판정에서 통째로 빠진다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    ratio = float(LIVE_ROW["stck_prpr"]) / float(LIVE_ROW["nav"]) - 1.0
    src = _source({"069500": _ok([_row("abc", dprt=f"{ratio:.6f}")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "지연 관측 불가" in caplog.text          # 시각 축은 실패했는데
    assert "괴리 단위 드리프트 의심" in caplog.text  # 단위 축은 살아서 물었다


def _records(caplog, needle: str) -> list:
    """그 문구를 낸 로그 레코드들 — 값 검증은 문자열이 아니라 `args` 로 한다.

    `caplog.text` 는 전 종목 로그가 섞인 **단일 문자열**이라 "값 하나가 들어 있는가"만
    보면 다른 ETF 의 로그로도 통과한다. 이 조각은 관측 자체가 산출물이라, 경고가 떴다는
    1비트가 아니라 **그 줄이 나르는 수치**(카운트·표본·식별자)까지 묶어야 한다.
    """
    return [r for r in caplog.records if needle in r.getMessage()]


def test_빈_라벨은_결측_판정이_먼저_거른다(monkeypatch, caplog):
    """`""` 는 `_time_stamp` 가 아니라 `_row_defect`(`_is_blank`)가 격리한다 — 초록의
    이유가 다르다. 같은 파라미터에 묶어 두면 결측 판정을 손댈 때 이유 없이 빨개진다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_row("")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert "필수 필드 결측" in caplog.text  # 격리 단계에서 걸렸다
    assert not _records(caplog, "형식 이탈")  # 관측 단계까지 오지도 않았다


def test_격리된_행은_관측_표본에_다시_들어오지_않는다(monkeypatch, caplog):
    """훅은 `_row_defect` 를 **통과한 행**을 받는다. 원본 output 을 넘기면 이미 격리된
    행이 형식 이탈로 재계상되고 단위 대조 표본에도 섞인다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    bad = {"bsop_hour": "쓰레기"}  # nav 결측 → 부모가 격리한다
    src = _source({"069500": _ok([_row("153000"), bad])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert not _records(caplog, "형식 이탈")  # 격리된 행은 관측이 보지 않는다
    (lag,) = _records(caplog, "벤더 지연")
    assert lag.args[7] == 1  # 행=1 (받은 행 2가 아니다)


def test_지연_로그가_나르는_수치를_전부_묶는다(monkeypatch, caplog):
    """문자열 부분매칭은 "값 하나가 들어 있는가"만 본다 — 자리가 뒤바뀌어도 통과한다.
    창·간격·행수·식별자를 args 로 함께 단언해야 왜곡형 회귀가 죽는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "벤더 지연")
    assert rec.levelno == logging.INFO  # 1분 레인에 붙으면 매 폴링이라 WARN 이면 안 된다
    symbol, etf_id, stamp, _recv, lag, window, interval, n, session, suffix = rec.args
    assert (symbol, etf_id, stamp) == ("069500", "069500", "153000")
    assert (lag, window, interval, n) == (60.0, 1800, 60, 5)
    assert session == "마감후"  # 15:31 은 마감(15:30) 뒤다
    assert suffix == ""


def test_ETF_마다_관측한다(monkeypatch, caplog):
    """픽스처가 1종이면 훅을 ETF 루프 밖으로 옮겨 첫 종목만 관측하는 회귀가 통과한다.
    실 유니버스는 33종이다."""
    _at(monkeypatch, datetime(2026, 7, 27, 11, 0, tzinfo=KST))  # 장중
    src = _source(
        {"069500": _ok([_row("105900")]), "091160": _ok([_row("104900")])},
        etf_map={"069500": "KR7069500007", "091160": "KR7091160002"},
    )

    with caplog.at_level("INFO"):
        list(src.fetch())

    hits = _records(caplog, "벤더 지연")
    assert {r.args[0]: r.args[4] for r in hits} == {"069500": 60.0, "091160": 660.0}
    assert {r.args[8] for r in hits} == {"장중"}  # 분기가 상수면 여기서 죽는다


def test_라벨이_조금_미래여도_사실을_적는다(monkeypatch, caplog):
    """구간 끝 라벨이면 최신 행이 **몇십 초** 미래인 것이 정상이다 — 도크스트링이 오탐
    근거로 든 바로 그 구간이라, 임계를 몰래 넓히는 회귀를 여기서 막는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 30, 5, tzinfo=KST))
    src = _source({"069500": _ok([_row("153100")])})  # −55초

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "벤더 지연")
    assert rec.args[4] == -55.0
    assert "미래" in rec.args[9]


def _priced(stamp: str, nav: float, price: float, dprt: str) -> dict:
    return _row(stamp, nav=str(nav), stck_prpr=str(price), dprt=dprt)


def test_일부_행만_어긋나도_드리프트를_잡는다(monkeypatch, caplog):
    """벤더가 일부 시각 구간만 단위를 바꾸는 것이 가장 흔한 드리프트 형태다. 전 행을 보지
    않거나(첫 행만) "전부 어긋날 때만" 경고하면 그 형태가 통째로 조용하다.

    표본은 **첫 불일치**여야 한다 — 마지막을 담으면 언제 틀어지기 시작했는지 못 읽는다.
    """
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    ok1, ok2 = _priced("150000", 100000, 105000, "5.0"), _priced("153000", 100000, 105000, "5.0")
    bad1 = _priced("151000", 100000, 105000, "0.05")   # 비율로 옴
    bad2 = _priced("152000", 100000, 105000, "0.05")
    src = _source({"069500": _ok([ok1, bad1, ok2, bad2])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "드리프트 의심")
    assert rec.levelno == logging.WARNING  # 알람이 붙을 축이다
    symbol, etf_id, mismatched, checked, at, dprt, expected = rec.args
    assert (symbol, etf_id) == ("069500", "069500")
    assert (mismatched, checked) == (2, 4)   # 카운트·자리 뒤바뀜을 함께 막는다
    assert at == "151000"                    # 마지막이 아니라 첫 불일치
    assert (dprt, round(expected, 4)) == (0.05, 5.0)  # 어느 쪽이 관측이고 어느 쪽이 가설인지


def test_반올림_오차는_드리프트가_아니다(monkeypatch, caplog):
    """괴리가 크면 벤더 내부 정밀도 차이가 비례해 는다 — 상대 오차를 안 두면(0 으로
    좁히면) 정상 표본이 매번 드리프트로 잡혀 진짜 신호가 묻힌다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_priced("153000", 100000, 105000, "5.08")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert not _records(caplog, "드리프트 의심")


def test_상대_오차가_10퍼센트까지_벌어지면_드리프트다(monkeypatch, caplog):
    """반대 방향 경계 — 허용 오차를 넓히는 회귀는 가드를 조용히 죽인다. 관대해지는 쪽으로
    틀리는 것이 이 레포가 반복해 데인 방향이라 양쪽을 다 못박는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_priced("153000", 100000, 105000, "5.5")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert _records(caplog, "드리프트 의심")


def test_괴리가_작을_때도_어긋나면_잡는다(monkeypatch, caplog):
    """실측 표본의 괴리는 0.11% 다 — 절대 오차를 넓히면 **그 구간이 통째로 무방비**가 된다.
    상대 오차는 큰 괴리에서만 구속하므로 작은 값 쪽을 따로 못박는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    # 퍼센트 가설은 0.11411 인데 0.05 로 온다 — 반올림으로는 설명 안 되는 차이다.
    src = _source({"069500": _ok([_row("153000", dprt="0.05")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert _records(caplog, "드리프트 의심")


def test_마감_후_폴링은_구간이_표시된다(monkeypatch, caplog):
    """마감 후 지연은 창 폭의 십수 배로 나오는 게 **정상**이다(15:30 값을 23:50 에 받으면
    30,000초). 마커가 없으면 하루치를 모아 보는 쪽에서 그 값이 "REST 불가"의 증거처럼
    섞인다 — 도크스트링이 경계한 미탐과 모양이 같은데 이쪽은 정상 동작이다."""
    _at(monkeypatch, datetime(2026, 7, 27, 23, 50, tzinfo=KST))
    src = _source({"069500": _ok([_row("153000")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "벤더 지연")
    assert rec.args[8] == "마감후"
    assert rec.args[4] == 30000.0


def test_라벨_범위가_창_폭을_넘으면_혼재를_지목한다(monkeypatch, caplog):
    """이 API 는 오늘 데이터가 없으면 전일 행을 반복한다. 전일 15:30 과 당일 09:05 가 섞이면
    사전순 `max` 가 전일을 "최신"으로 집어 지연 0초가 −22,800초로 찍힌다 — 산출물이 그
    수치 하나라 결론이 직접 뒤집힌다. 날짜 필드가 없어 직접 확인은 못 하지만 **범위는
    확인된다**: 응답 창은 계약상 간격×30 이라, 그걸 넘으면 한 창의 행이 아니다."""
    _at(monkeypatch, datetime(2026, 7, 27, 9, 10, tzinfo=KST))
    src = _source({"069500": _ok([_row("152900"), _row("153000"), _row("090500")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "라벨 범위가 창 폭을 넘는다")
    assert rec.levelno == logging.WARNING
    assert rec.args[2:6] == ("090500", "153000", 23100, 1800)


def test_한_창_안의_행은_범위_경고가_없다(monkeypatch, caplog):
    """정상 응답(창 30분 안)에서 경고가 뜨면 진짜 혼재가 소음에 묻힌다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})  # 150100~152500, 폭 1440초 < 1800

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert not _records(caplog, "라벨 범위가 창 폭을 넘는다")


def test_대조_표본이_모자라면_드러낸다(monkeypatch, caplog):
    """29행이 못 쓰는데 남은 1행이 맞으면 지금까진 로그가 0줄이었다 — 표본이 1/30 인데
    판정은 "정상"으로 보인다. 시각 축은 같은 상황을 "형식 이탈 29/30" 으로 드러낸다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    rows = [_row("150000", dprt=None), _row("151000", nav="nan"), _row("153000")]
    src = _source({"069500": _ok(rows)})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "표본 부족")
    # 사유를 가른다 — "필드가 안 왔다"(벤더 계약 변화)와 "값이 이상하다"(데이터 오염)는
    # 처방이 정반대다. 한 문장으로 뭉개면 둘이 같아 보인다.
    assert rec.args[2:6] == (1, 3, 1, 1)
    assert not _records(caplog, "드리프트 의심")


def test_대조_불가_사유를_뭉개지_않는다(monkeypatch, caplog):
    """전건 못 쓸 때도 결측·비수치와 비유한·비양수를 나눠 센다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_row("150000", dprt=None), _row("153000", nav="inf")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "대조 불가")
    assert rec.args[2:5] == (2, 1, 1)  # 행 2 · 결측 1 · 비유한 1


def test_소스_전역_중단은_관측_격리가_삼키지_않는다(monkeypatch):
    """`StopFetch` 는 키·쿼터 문제라 ETF 격리 대상이 아니다(`fetch()` 가 명시적으로 뺀다).
    관측 격리가 그 계약 **아래층**에서 먼저 먹으면 규약이 두 곳으로 갈려, 소스 전체가
    멈춰야 할 자리에서 남은 ETF 를 계속 두드린다."""
    from data_pipeline.sources.http import StopFetch

    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})
    monkeypatch.setattr(
        type(src), "_note_rows",
        lambda *a, **k: (_ for _ in ()).throw(StopFetch("429 quota")),
    )

    with pytest.raises(StopFetch):
        list(src.fetch())


def test_창을_조금만_넘어도_혼재로_본다(monkeypatch, caplog):
    """임계는 창 폭 **그 자체**다. 배수로 느슨하게 두면 그 사이 구간이 무방비인데, 창이
    간격×30 이라는 건 계약이라 1초만 넘어도 한 창의 행이 아니다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    # 40분(2400초) 폭 — 창 1800초를 넘지만 배수 완화(3600)에는 안 걸린다.
    src = _source({"069500": _ok([_row("145000"), _row("153000")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "라벨 범위가 창 폭을 넘는다")
    assert rec.args[4] == 2400


def test_절사_표기_표본을_드리프트로_잡지_않는다(monkeypatch, caplog):
    """허용 오차를 **못 조이는** 이유를 못박는다.

    `dprt` 2자리 표기가 반올림인지 절사인지가 미실측이다 — 실측 표본(0.114115 → "0.11")은
    두 가설이 같은 값을 내 구분되지 않는다. 절사면 오차 상한이 0.00999 라, 반올림 상한
    (0.005)에 맞춰 조이면 **정상 표본의 40%가 드리프트로 잡힌다**(스윕 실측). 소음이 가드를
    끄게 만들면 결국 관대해지는 쪽으로 착지한다.
    """
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    # 괴리 0.1199% 가 절사로 "0.11" 이 된 행 — 잔차 0.0099.
    src = _source({"069500": _ok([_priced("153000", 100000, 100119.9, "0.11")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert not _records(caplog, "드리프트 의심")


def test_계약과_다른_행_수를_드러낸다(monkeypatch, caplog):
    """`ROWS_PER_CALL` 은 창 계산에만 쓰이고 응답과 대조된 적이 없다. 20행이 오면 실창은
    1200초인데 1800 으로 찍혀 판정(지연÷창)이 1.5배 관대해지고, **같은 배수로 혼재 임계도
    죽는다** — 두 축이 상수 하나에 걸려 같은 방향으로 틀린다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok([_row(f"1{h:02d}000") for h in range(20, 40)])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "행 수가 계약과 다르다")
    assert rec.levelno == logging.WARNING
    assert rec.args[2:4] == (20, 30)


def test_요청과_다른_표본_간격을_드러낸다(monkeypatch, caplog):
    """`interval_sec` 은 우리가 **보낸 요청값**이지 측정값이 아니다. 벤더가
    `FID_HOUR_CLS_CODE` 를 무시해 성기게 주면 범위 가드에도 안 걸려 완전 침묵하고,
    거짓 `interval_sec` 이 raw 행에 각인된다 — 그 필드는 자연키의 일부다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    # 요청 60초인데 실제로는 300초 격자로 온다.
    stamps = ["1500%02d" % 0, "150500", "151000", "151500", "152000"]
    src = _source({"069500": _ok([_row(t) for t in stamps])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "표본 간격이 요청과 다르다")
    assert rec.args[2:4] == (60, 300)


def test_장중에_창_폭을_넘는_지연은_경고다(monkeypatch, caplog):
    """전량 오염(전일 30행 통째)은 범위 가드에 안 걸린다 — 범위가 정상 창 안이라서다.
    그때 INFO 로 두면 **부분 혼재는 WARN 인데 전량 오염이 조용해** WARN 만 보는 쪽에서
    가장 나쁜 경우가 통째로 사라진다. 원인은 못 가르지만 이상이라는 것은 말할 수 있다."""
    _at(monkeypatch, datetime(2026, 7, 27, 9, 10, tzinfo=KST))
    src = _source({"069500": _ok([_row("152900"), _row("153000")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "벤더 지연")
    assert rec.levelno == logging.WARNING
    assert rec.args[8] == "장중"


def test_개장_전_라벨을_관측으로_남긴다(monkeypatch, caplog):
    """창이 뒤로 뻗으면 개장 이전 시각을 요구한다(09:10 실행의 창은 08:40 까지). KIS 가
    그 구간을 무엇으로 채우는지는 **이 조각이 재려는 열린 질문**이라 오류로 단정하지
    않는다 — 다만 최신 행만 찍으면 그 사실이 로그에서 사라진다."""
    _at(monkeypatch, datetime(2026, 7, 27, 9, 10, tzinfo=KST))
    src = _source({"069500": _ok([_row("084000"), _row("085000"), _row("090500")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "개장 전 라벨")
    assert rec.levelno == logging.INFO  # 오류가 아니다
    assert rec.args[0:2] == (2, 3)
    assert rec.args[4] == "084000"


def test_한_축이_던져도_다른_축은_돈다(monkeypatch, caplog):
    """"두 축은 서로의 실패로 죽지 않는다"를 `return` 만으로 지키면 **예외**에는 안 걸린다.
    앞 축이 던지면 뒤 축이 통째로 안 도는데, 단위 가드는 정상일 때 조용해서 "경고 없음"과
    구분되지 않는다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    ratio = float(LIVE_ROW["stck_prpr"]) / float(LIVE_ROW["nav"]) - 1.0
    src = _source({"069500": _ok([_row("153000", dprt=f"{ratio:.6f}")])})
    monkeypatch.setattr(
        type(src), "_note_lag",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("지연 축 폭발")),
    )

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert _records(caplog, "지연 관측 실패")       # 죽은 축은 죽었다고 남기고
    assert _records(caplog, "드리프트 의심")        # 살아 있는 축은 돈다


def test_대조_불가_시_빠진_필드_이름을_남긴다(monkeypatch, caplog):
    """벤더가 `dprt` 를 개명하면 단위 가드가 통째로 사라지는데 런은 초록이다(그 필드는
    행 식별 요건이 아니라 격리도 안 된다). 최소한 무엇이 빠졌는지는 말해야 한다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    rows = [{k: v for k, v in _row("153000").items() if k != "dprt"}]
    src = _source({"069500": _ok(rows)})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "대조 불가")
    assert rec.args[5] == "dprt"


def test_요청대로_온_간격은_조용하다(monkeypatch, caplog):
    """어긋날 때만 말하는 가드다 — 정상에도 뜨면 ETF·폴링마다 경고가 나 진짜 신호가 묻힌다."""
    _at(monkeypatch, datetime(2026, 7, 27, 15, 31, tzinfo=KST))
    src = _source({"069500": _ok(_window())})  # 150100~152500, 요청과 같은 60초 격자

    with caplog.at_level("INFO"):
        list(src.fetch())

    assert not _records(caplog, "표본 간격이 요청과 다르다")


def test_개장_전_폴링은_구간이_따로_표시된다(monkeypatch, caplog):
    """개장 전과 마감 후는 의미가 정반대다 — 개장 전 = 전일 값 확정, 마감 후 = 오늘 값 정상.
    한 라벨로 접으면 08:50 폴링의 `지연=-23400초` 가 마감 후 정상값처럼 읽힌다.

    배선된 경로는 `skip_reason` 이 09:00 이전을 막지만, 수동 CLI·백필은 스텝을 우회한다.
    """
    _at(monkeypatch, datetime(2026, 7, 27, 8, 50, tzinfo=KST))
    src = _source({"069500": _ok([_row("153000")])})

    with caplog.at_level("INFO"):
        list(src.fetch())

    (rec,) = _records(caplog, "벤더 지연")
    assert rec.args[8] == "개장전"
    assert rec.levelno == logging.INFO  # 장중이 아니라 격상 대상이 아니다
