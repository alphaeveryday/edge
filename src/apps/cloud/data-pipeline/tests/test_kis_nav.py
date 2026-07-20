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
