"""KisNavSource 어댑터 테스트 — 단축코드 파생·창 파라미터·원본 보존·격리·fail-loud.

KrxEtfSource/FmpEtfSource 와 같은 관례 인터페이스를 지켜 ingest_raw_etf 스텝을 그대로
재사용하므로, 스텝의 fail-loud 상태 로직은 test_ingest_raw_etf(벤더 무관)가 이미 덮는다 —
여기선 NAV 고유(토큰 1회·ISIN→6자리 단축코드·날짜창·단일 output 배열·빈 output 격리)만 본다.
"""

import json
import urllib.parse

import pytest

from data_pipeline.config import KisNavSource as KisNavSourceConfig
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_nav import KisNavSource

# 라이브 실측 응답 행(2026-07-20, 069500). 필드명·문자열 타입을 그대로 고정한다.
LIVE_ROW = {
    "stck_bsop_date": "20260716", "stck_clpr": "109000", "prdy_vrss": "-7735",
    "prdy_vrss_sign": "5", "prdy_ctrt": "-6.63", "acml_vol": "20103895", "cntg_vol": "",
    "dprt": "0.23", "nav_vrss_prpr": "253.67", "nav": "108746.33",
    "nav_prdy_vrss_sign": "5", "nav_prdy_vrss": "-8398.85", "nav_prdy_ctrt": "-7.17",
}


class FakeAuth:
    """토큰 발급을 건너뛰고 고정 토큰을 준다(run 당 1회 규약 검증용)."""

    def __init__(self):
        self.calls = 0

    def token(self):
        self.calls += 1
        return "TOKEN"


class FakeClient:
    def __init__(self, responses):
        self.responses = responses  # {kis_symbol: <payload dict | Exception>}
        self.queries = []  # 요청 쿼리스트링(창·tr_id 검증용)
        self.headers = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        assert method == "GET"
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.queries.append(params)
        self.headers.append(headers or {})
        payload = self.responses.get(params["FID_INPUT_ISCD"][0], {"rt_cd": "0", "output": []})
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)

    def _sleep(self, seconds):  # EGW00201 재시도 경로에서 호출 — 테스트는 즉시 통과
        pass


def _source(responses, etf_map=None, from_date=None, to_date=None):
    config = KisNavSourceConfig(app_key="k", app_secret="s")
    src = KisNavSource(
        config,
        etf_map if etf_map is not None else {"069500": "KR7069500007"},
        FakeClient(responses),
        from_date,
        to_date,
    )
    src.auth = FakeAuth()
    return src


def _ok(rows):
    return {"rt_cd": "0", "output": rows}


def test_isin_을_6자리_단축코드로_질의하고_provenance_를_붙인다():
    """KIS 는 ISIN 이 아니라 단축코드로 질의한다 — 잘못 보내면 전 종목이 조용히 실패한다."""
    src = _source({"069500": _ok([LIVE_ROW])})
    records = list(src.fetch())

    assert src.client.queries[0]["FID_INPUT_ISCD"] == ["069500"]  # ISIN 아님
    assert src.client.headers[0]["tr_id"] == "FHPST02440200"
    assert len(records) == 1
    record = records[0]
    # bronze 무변형: 원본 필드가 하나도 유실·변형되지 않아야 한다(nav 외 dprt·종가 포함).
    for key, value in LIVE_ROW.items():
        assert record[key] == value
    assert record["our_etf_id"] == "069500"
    assert record["market"] == "KR"
    assert record["kis_symbol"] == "069500"
    assert record["fetched_at"]


def test_날짜창이_KIS_파라미터로_전달된다():
    """창 배선이 끊기면 백필이 조용히 당일치만 받아온다 — 값으로 확인한다."""
    src = _source({"069500": _ok([LIVE_ROW])}, from_date="2026-06-01", to_date="2026-07-17")
    list(src.fetch())

    params = src.client.queries[0]
    assert params["FID_INPUT_DATE_1"] == ["20260601"]
    assert params["FID_INPUT_DATE_2"] == ["20260717"]


def test_토큰은_run_당_1회만_발급한다():
    """ETF 마다 발급하면 KIS 가 분당 한도로 막는다(kis_price 와 같은 규약)."""
    src = _source(
        {"069500": _ok([LIVE_ROW]), "091160": _ok([LIVE_ROW])},
        etf_map={"069500": "KR7069500007", "091160": "KR7091160005"},
    )
    list(src.fetch())

    assert src.auth.calls == 1
    assert src.planned_etfs == 2


def test_빈_output_은_ETF_단위로_격리되고_남은_ETF_는_계속_수집한다():
    """빈 응답을 success 0건으로 삼키면 수집 실패가 묻힌다 — partial 로 드러나야 한다."""
    src = _source(
        {"069500": _ok([]), "091160": _ok([LIVE_ROW])},
        etf_map={"069500": "KR7069500007", "091160": "KR7091160005"},
    )
    records = list(src.fetch())

    assert [r["kis_symbol"] for r in records] == ["091160"]  # 나머지는 계속 수집
    assert len(src.fetch_failures) == 1
    assert src.fetch_failures[0]["symbol"] == "069500"
    assert "empty output" in src.fetch_failures[0]["error"]


def test_rt_cd_오류는_ETF_단위_실패로_기록된다():
    src = _source({"069500": {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "잘못된 종목"}})
    records = list(src.fetch())

    assert records == []
    assert "EGW00123" in src.fetch_failures[0]["error"]


def test_rt_cd_0_인데_output_이_비_list_면_fail_loud():
    """스키마 드리프트를 정상 빈 페이지로 위장시키지 않는다."""
    src = _source({"069500": {"rt_cd": "0", "output": {"nav": "1"}}})
    list(src.fetch())

    assert "output 이상" in src.fetch_failures[0]["error"]


def test_StopFetch_는_소스_전체를_중단한다():
    """4xx/429 는 키·쿼터 문제라 남은 ETF 를 두드려봐야 소용없다."""
    src = _source(
        {"069500": StopFetch("401"), "091160": _ok([LIVE_ROW])},
        etf_map={"069500": "KR7069500007", "091160": "KR7091160005"},
    )
    with pytest.raises(StopFetch):
        list(src.fetch())


def test_문자_섞인_신형_단축코드도_질의한다():
    """KRX 신규 상장분은 코드에 문자가 섞인다(0093A0) — 숫자로만 거르면 유니버스가 조용히 샌다."""
    src = _source({"0093A0": _ok([LIVE_ROW])}, etf_map={"0093A0": "KR70093A0000"})
    records = list(src.fetch())

    assert src.client.queries[0]["FID_INPUT_ISCD"] == ["0093A0"]
    assert len(records) == 1
    assert src.fetch_failures == []


def test_단축코드_파생_실패는_질의하지_않고_실패로_남긴다():
    """비정형 표준코드로 엉뚱한 KIS 질의를 쌓지 않는다."""
    src = _source({}, etf_map={"BAD": "XX"})
    records = list(src.fetch())

    assert records == []
    assert src.client.queries == []  # 질의 자체를 안 한다
    assert "단축코드 파생 실패" in src.fetch_failures[0]["error"]


def test_자격증명_없으면_비활성():
    config = KisNavSourceConfig(app_key=None, app_secret=None)
    src = KisNavSource(config, {"069500": "KR7069500007"}, FakeClient({}))
    assert src.enabled is False


# ── 봉투 형상 위반의 **축 분류** (ALPHA-851 라운드2) ─────────────────
# 예외 타입이 곧 재시도 여부다. 타입을 되돌려도(ValueError) 메시지가 같아 기존 단언은
# 전부 통과했다 — 그래서 **타입 자체**를 raise 지점에서 못박는다.


def test_rt_cd_0_인데_output_이_비_list_면_스키마_드리프트_축이다():
    """본문이 유효 JSON 이고 `rt_cd="0"` 까지 있으면 그 응답은 **KIS 가 준 것**이다 —
    프록시가 끼운 오류 페이지가 아니다. 거기서 `output` 이 list 가 아니면 스키마
    드리프트로 읽는다(재시도로 안 풀린다). 이 타입이 1분 레인의 invalid 판정을 만든다."""
    from data_pipeline.sources.kis_nav import KisNavShapeError

    src = _source({"069500": {"rt_cd": "0", "output": {"nav": "1"}}})
    with pytest.raises(KisNavShapeError):
        src._fetch_etf("069500", "069500", "", "", "TOKEN")


def test_본문이_객체가_아니면_전송_사고_축으로_남는다():
    """잘린 응답·프록시 오류 페이지가 압도적이라 재시도 축이다 — `kis_minute` 이 같은
    조건을 `KisUnitError` 로 돌리는 것과 같은 판단(그쪽은 테스트로 고정돼 있다).
    이걸 드리프트 축으로 올리면 **몇 초짜리 전송 사고가 window 를 INVALID 로 굳힌다**."""
    from data_pipeline.sources.kis_nav import KisNavShapeError

    src = _source({"069500": [1, 2, 3]})  # dict 가 아닌 본문
    with pytest.raises(ValueError) as caught:
        src._fetch_etf("069500", "069500", "", "", "TOKEN")
    assert not isinstance(caught.value, KisNavShapeError)


def test_유량_재시도가_실제로_세어진다():
    """유량은 **앱키 전역**이라 iNAV 폴링이 1분 가격 레인을 굶길 수 있다. 이 카운터가
    0 으로 굳으면 그 압력이 window 결과에서 통째로 사라진다(`retry_count` 를 지워도
    전 스위트가 통과했다)."""
    rate = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"}
    src = _source({"069500": rate})

    with pytest.raises(ValueError):
        src._fetch_etf("069500", "069500", "", "", "TOKEN")

    # 예산 5회 중 마지막은 재시도하지 않고 raise 한다 → 증가분은 4
    assert src.retry_count == 4
