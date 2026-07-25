"""KisInavSource 어댑터 테스트 — iNAV 고유 계약만 본다.

토큰 1회 발급·단축코드 파생·rt_cd 판정·EGW00201 재시도·malformed 행 격리는 부모
(KisNavSource)의 코드를 그대로 타므로 test_kis_nav 가 이미 덮는다. 여기선 **갈리는 지점**만
검증한다 — 시장코드·tr_id·간격 파라미터·날짜 미전송·간격 각인. 이 넷이 조용히 틀리면
전 종목이 실패하거나(시장코드) 자연키가 충돌한다(간격).
"""

import json
import urllib.parse

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


def _source(responses, interval_sec=DEFAULT_INTERVAL_SEC):
    src = KisInavSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        {"069500": "KR7069500007"},
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
    src = _source({"069500": _ok([LIVE_ROW])}, interval_sec=10)
    list(src.fetch())

    assert src.client.queries[0]["FID_HOUR_CLS_CODE"] == ["10"]
    assert src.window_sec == 300  # 10초 × 30행 = 5분치


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
    src = _source({"069500": _ok([LIVE_ROW])}, interval_sec=30)
    records = list(src.fetch())

    assert records[0]["interval_sec"] == 30


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


@pytest.mark.parametrize("bad", [0, -1])
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
