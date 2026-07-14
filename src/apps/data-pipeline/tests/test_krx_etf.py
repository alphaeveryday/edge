"""KrxEtfSource 어댑터 테스트 — 메타 부착·원본 보존(대시 포함)·plan·StopFetch 전파·격리.

FmpEtfSource(US)와 같은 관례 인터페이스를 지켜 ingest_raw_etf 스텝을 그대로 재사용하므로,
스텝의 fail-loud 상태 로직은 test_ingest_raw_etf(벤더 무관)가 이미 덮는다 — 여기선 KRX
고유(로그인 1회·POST getJsonData·JSESSIONID 쿠키·ISIN 질의·해외기초 대시 보존)만 검증한다.
"""

import json
import urllib.parse

import pytest

from data_pipeline.config import KrxEtfSource as KrxEtfSourceConfig
from data_pipeline.sources.http import StopFetch
from data_pipeline.sources.krx_etf import KrxEtfSource, _short_code


class FakeAuth:
    """로그인을 건너뛰고 고정 JSESSIONID 를 준다(어댑터를 실제 KRX 로그인과 분리)."""

    def __init__(self, jsessionid="SESS123"):
        self.jsessionid = jsessionid
        self.calls = 0

    def session(self):
        self.calls += 1  # run 당 1회 규약 검증용
        return self.jsessionid


class FakeClient:
    def __init__(self, responses):
        self.responses = responses  # {isin: <payload dict | Exception>}
        self.cookies = []  # 요청마다 보낸 Cookie 헤더(쿠키 부착 검증용)

    def request(self, method, url, *, headers=None, data=None, decode=True):
        assert method == "POST"
        self.cookies.append((headers or {}).get("Cookie"))
        params = urllib.parse.parse_qs(data.decode("utf-8"))
        isin = params["isuCd"][0]
        payload = self.responses.get(isin, {"output": []})
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)


def _source(responses, etf_map=None, auth=None):
    config = KrxEtfSourceConfig(
        mbr_id="id", pw="pw",
        etf_map=etf_map if etf_map is not None else {"069500": "KR7069500007"},
    )
    src = KrxEtfSource(config, FakeClient(responses))
    src.auth = auth or FakeAuth()
    return src


def test_short_code_derives_6digit_from_isin():
    # WHY: getJsonData 는 isuCd2(6자리 단축코드)도 받는다 — 표준코드 12자리 [3:9]가 단축코드.
    #      라이브 실측 요청 본문과 일치시켜야 응답이 재현된다.
    assert _short_code("KR7069500007") == "069500"
    assert _short_code("KR7360750004") == "360750"


def test_fetch_attaches_meta_and_preserves_original():
    # WHY: raw 는 원본 필드를 무변형 보존하고 수집 메타(our_etf_id/market/isin/trd_dd/
    #      fetched_at)만 덧붙인다 — 특히 우리가 지정한 기준일(trd_dd)이 as-of 로 남아야 한다.
    src = _source({"KR7069500007": {"output": [
        {"COMPST_ISU_CD": "005930", "COMPST_ISU_NM": "삼성전자", "COMPST_RTO": "30.5"}]}})
    rows = list(src.fetch())

    assert len(rows) == 1
    row = rows[0]
    assert row["our_etf_id"] == "069500" and row["market"] == "KR"
    assert row["isin"] == "KR7069500007" and "trd_dd" in row and "fetched_at" in row
    # 원본 필드 무변형 보존.
    assert row["COMPST_ISU_CD"] == "005930" and row["COMPST_RTO"] == "30.5"


def test_foreign_underlying_dash_preserved():
    # WHY: 해외기초 ETF(TIGER美S&P500)는 비중·금액이 `-`(대시)로 온다 — bronze 무변형이라
    #      조용히 버리거나 0 으로 강제하지 않고 그대로 보존한다(정규화는 후속 canonical 소관).
    src = _source({"KR7360750004": {"output": [
        {"COMPST_ISU_CD": "AAPL", "COMPST_RTO": "-", "VALU_AMT": "-", "COMPST_ISU_CU1_SHRS": "12"}]}},
        etf_map={"360750": "KR7360750004"})
    [row] = list(src.fetch())
    assert row["COMPST_RTO"] == "-" and row["VALU_AMT"] == "-"
    assert row["COMPST_ISU_CU1_SHRS"] == "12"


def test_login_once_and_cookie_attached():
    # WHY: 로그인은 run 당 1회(ETF마다 로그인 금지, KIS 토큰 규약과 동형)이고, 그 세션
    #      JSESSIONID 가 매 getJsonData 요청의 Cookie 헤더로 붙어야 게이트를 통과한다.
    auth = FakeAuth("SESS999")
    src = _source({"KR7069500007": {"output": [{"COMPST_ISU_CD": "A"}]},
                   "KR7360750004": {"output": [{"COMPST_ISU_CD": "B"}]}},
                  etf_map={"069500": "KR7069500007", "360750": "KR7360750004"}, auth=auth)
    list(src.fetch())
    assert auth.calls == 1  # 2개 ETF 인데 로그인은 1회
    assert src.client.cookies == ["JSESSIONID=SESS999", "JSESSIONID=SESS999"]


def test_plan_maps_and_sets_planned_count():
    # WHY: etf_map 이 곧 수집 유니버스 — plan 은 (our_etf_id, isin) 로 정렬 매핑하고,
    #      planned_etfs 를 세워 스텝이 '매핑 0개'를 skip 으로 드러내게 한다.
    src = _source({"KR7069500007": {"output": [{"x": 1}]}, "KR7360750004": {"output": [{"x": 1}]}},
                  etf_map={"069500": "KR7069500007", "360750": "KR7360750004"})
    list(src.fetch())
    assert src.planned_etfs == 2
    assert src.plan() == [("069500", "KR7069500007"), ("360750", "KR7360750004")]


def test_empty_output_isolated_as_failure():
    # WHY: ETF 는 정의상 구성종목이 있으므로 빈 output(비영업일·미게시·잘못된 ISIN)은
    #      정상이 아니다 — fail-loud 하게 ETF 단위 실패로 격리한다(런은 partial/error).
    src = _source({"KR7069500007": {"output": [{"x": 1}]}, "KR7360750004": {"output": []}},
                  etf_map={"069500": "KR7069500007", "360750": "KR7360750004"})
    rows = list(src.fetch())
    assert len(rows) == 1  # KODEX200 만
    assert len(src.fetch_failures) == 1
    assert src.fetch_failures[0]["our_etf_id"] == "360750"
    assert "empty output" in src.fetch_failures[0]["error"]


def test_non_object_output_is_failure():
    # WHY: 200 인데 output 이 없거나 비-list(오류 응답·스키마 드리프트)면 조용한 0행 처리
    #      금지 — ETF 실패로 올려 fail loud(US 어댑터의 '비배열 응답' 처리와 동형).
    src = _source({"KR7069500007": {"output": "nope"}})
    list(src.fetch())
    assert len(src.fetch_failures) == 1
    assert "output 이상" in src.fetch_failures[0]["error"]


def test_malformed_row_skipped_others_preserved():
    # WHY: output 배열에 dict 아닌 행(null·문자열)이 섞여도 한 행이 남은 수집을 끊지
    #      않는다 — 불량 행은 기록 후 스킵하고 정상 행은 보존한다.
    src = _source({"KR7069500007": {"output": [{"COMPST_ISU_CD": "A"}, None, "junk"]}})
    rows = list(src.fetch())
    assert len(rows) == 1 and len(src.fetch_failures) == 2


def test_stopfetch_aborts_whole_source():
    # WHY: 4xx/429(미로그인 400 LOGOUT 포함)는 세션·쿼터 문제라 ETF 단위 격리 대상이
    #      아니다 — 소스 전체를 중단해야 한다.
    src = _source({"KR7069500007": StopFetch("400 LOGOUT")})
    with pytest.raises(StopFetch):
        list(src.fetch())


def test_disabled_without_credentials():
    # WHY: 자격증명은 env 로만 주입 — 없으면 이 소스는 비활성(스텝이 skip 으로 드러냄).
    config = KrxEtfSourceConfig(etf_map={"069500": "KR7069500007"})
    assert KrxEtfSource(config, FakeClient({})).enabled is False
