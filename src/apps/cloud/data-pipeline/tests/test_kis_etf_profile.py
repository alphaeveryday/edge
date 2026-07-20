"""KisEtfProfileSource 어댑터 테스트 — ETF 마스터 재료 수집 (ALPHA-462).

NAV 어댑터와 같은 관례 인터페이스를 지켜 ingest_raw_etf 스텝을 재사용하므로, 스텝의 fail-loud
상태 로직은 test_ingest_raw_etf 가 이미 덮는다 — 여기선 프로필 고유(단일 output 객체·
provenance·격리)만 본다.
"""

import json
import urllib.parse

import pytest

from data_pipeline.config import KisNavSource as KisNavSourceConfig
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.kis_etf_profile import KisEtfProfileSource

# 라이브 실측 응답(069500, 2026-07-20). pdno 는 패딩된 내부 코드라 티커가 아니다.
LIVE_OUTPUT = {
    "pdno": "00000A069500", "prdt_abrv_name": "KODEX 200",
    "prdt_name": "삼성 KODEX200 증권상장지수투자신탁[주식]",
    "prdt_eng_abrv_name": "KODEX 200", "prdt_clsf_name": "ETF",
    "std_pdno": "KR7069500007", "ivst_prdt_type_cd_name": "주식",
}


class FakeAuth:
    def __init__(self):
        self.calls = 0

    def token(self):
        self.calls += 1
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
        payload = self.responses.get(params["PDNO"][0], {"rt_cd": "0", "output": {}})
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)

    def _sleep(self, seconds):
        pass


def _source(responses, etf_map=None):
    src = KisEtfProfileSource(
        KisNavSourceConfig(app_key="k", app_secret="s"),
        etf_map if etf_map is not None else {"069500": "KR7069500007"},
        FakeClient(responses),
    )
    src.auth = FakeAuth()
    return src


def _ok(output):
    return {"rt_cd": "0", "output": output}


def test_상품정보를_provenance_와_함께_원본_보존한다():
    # WHY: 이 raw 가 ETF 마스터(entity.display_name NOT NULL)의 유일한 이름 출처다. 필드가
    #      유실되면 마스터를 만들 수 없고, 31종 중 30종이 마트에 못 들어가는 상태가 유지된다.
    src = _source({"069500": _ok(LIVE_OUTPUT)})
    [record] = list(src.fetch())

    assert src.client.queries[0]["PDNO"] == ["069500"]
    assert src.client.queries[0]["PRDT_TYPE_CD"] == ["300"]
    assert src.client.headers[0]["tr_id"] == "CTPF1604R"
    for key, value in LIVE_OUTPUT.items():
        assert record[key] == value        # bronze 무변형
    assert record["our_etf_id"] == "069500"
    assert record["market"] == "KR"
    assert record["kis_symbol"] == "069500"
    assert record["fetched_at"]


def test_토큰은_run_당_1회만_발급한다():
    # WHY: ETF 마다 발급하면 KIS 가 분당 한도로 막는다(ALPHA-458).
    src = _source(
        {"069500": _ok(LIVE_OUTPUT), "091160": _ok({**LIVE_OUTPUT, "prdt_abrv_name": "KODEX 반도체"})},
        etf_map={"069500": "KR7069500007", "091160": "KR7091160005"},
    )
    list(src.fetch())
    assert src.auth.calls == 1 and src.planned_etfs == 2


def test_문자_섞인_신형_단축코드도_질의한다():
    # WHY: 신규 상장분은 코드에 문자가 섞인다(0093A0). 숫자로만 거르면 유니버스가 조용히 샌다.
    src = _source({"0093A0": _ok({**LIVE_OUTPUT, "prdt_abrv_name": "RISE AI반도체TOP10"})},
                  etf_map={"0093A0": "KR70093A0000"})
    [record] = list(src.fetch())
    assert record["prdt_abrv_name"] == "RISE AI반도체TOP10"


def test_빈_output_은_격리되고_남은_ETF_는_계속_수집한다():
    # WHY: 빈 응답을 통과시키면 이름 없는 마스터를 만들려다 NOT NULL 위반으로 적재가 죽는다.
    src = _source(
        {"069500": _ok({}), "091160": _ok(LIVE_OUTPUT)},
        etf_map={"069500": "KR7069500007", "091160": "KR7091160005"},
    )
    records = list(src.fetch())

    assert [r["our_etf_id"] for r in records] == ["091160"]
    assert "empty output" in src.fetch_failures[0]["error"]


def test_output_이_객체가_아니면_fail_loud():
    # WHY: 이 엔드포인트의 output 은 배열이 아니라 **객체 하나**다(NAV 와 다르다).
    #      배열이 오면 스키마 드리프트이므로 조용한 빈 결과로 위장시키지 않는다.
    src = _source({"069500": {"rt_cd": "0", "output": [LIVE_OUTPUT]}})
    list(src.fetch())
    assert "output 이상" in src.fetch_failures[0]["error"]


def test_StopFetch_는_소스_전체를_중단한다():
    src = _source({"069500": StopFetch("401", status=401)})
    with pytest.raises(StopFetch):
        list(src.fetch())


def test_자격증명_없으면_비활성():
    src = KisEtfProfileSource(
        KisNavSourceConfig(app_key=None, app_secret=None), {"069500": "KR7069500007"},
        FakeClient({}),
    )
    assert src.enabled is False
