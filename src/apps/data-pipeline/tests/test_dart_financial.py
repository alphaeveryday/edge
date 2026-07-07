"""OpenDART 재무 어댑터 테스트 — corp_code 매핑·raw 보존·status 처리 (네트워크 없음).

각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9). DART 는 ZIP
corpCode 와 JSON 재무 응답을 함께 쓰므로 FakeClient 로 두 운반 형태를 모두 잠근다.
"""

import io
import json
import zipfile

import pytest

from data_pipeline.config import DartFinancialSource as DartFinancialSourceConfig
from data_pipeline.sources.dart_financial import DartFinancialSource
from data_pipeline.sources.http import StopFetch

_MAP = {"005930": "005930", "000660": "000660"}


def _corp_zip(entries: list[tuple[str, str, str]]) -> bytes:
    rows = "".join(
        f"<list><corp_code>{corp_code}</corp_code><corp_name>{name}</corp_name>"
        f"<stock_code>{stock_code}</stock_code></list>"
        for stock_code, corp_code, name in entries
    )
    raw = f"<?xml version='1.0' encoding='UTF-8'?><result>{rows}</result>".encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", raw)
    return buf.getvalue()


def _ok(rows: list[dict]) -> str:
    return json.dumps({"status": "000", "message": "정상", "list": rows}, ensure_ascii=False)


def _status(code: str, message: str = "msg") -> str:
    return json.dumps({"status": code, "message": message}, ensure_ascii=False)


def _status_xml(code: str, message: str = "msg") -> bytes:
    return f"<result><status>{code}</status><message>{message}</message></result>".encode()


class FakeClient:
    def __init__(self, responses, corp_body=None):
        self.responses = responses  # {(corp_code, year, reprt): body}
        self.corp_body = corp_body or _corp_zip([("005930", "00126380", "삼성전자")])
        self.urls: list[str] = []

    def request(self, method, url, *, headers=None, data=None, decode=True):
        self.urls.append(url)
        if "corpCode.xml" in url:
            return self.corp_body
        corp_code = url.split("corp_code=")[1].split("&")[0]
        year = url.split("bsns_year=")[1].split("&")[0]
        reprt = url.split("reprt_code=")[1].split("&")[0]
        return self.responses.get((corp_code, year, reprt), _status("013", "조회 데이터 없음"))


def _source(responses, *, corp_body=None, api_key="k", symbol_map=None, years=None, reprt_codes=None):
    config = DartFinancialSourceConfig(
        api_key=api_key,
        symbol_map=_MAP if symbol_map is None else symbol_map,
        years=["2025"] if years is None else years,
        reprt_codes=["11011"] if reprt_codes is None else reprt_codes,
    )
    return DartFinancialSource(config, FakeClient(responses, corp_body=corp_body))


def test_fetch_attaches_provenance_and_preserves_raw():
    # WHY: raw 는 DART list[] 원본 필드를 보존하고, 후속 canonical 이 재현할 수 있게
    #      our_ticker·market·stock_code·corp_code·보고서 파라미터 provenance 만 붙여야 한다.
    row = {
        "rcept_no": "20260310000123",
        "account_nm": "자산총계",
        "thstrm_amount": "566900000000000",
    }
    src = _source({("00126380", "2025", "11011"): _ok([row])})
    records = list(src.fetch(["005930"]))

    assert len(records) == 1
    rec = records[0]
    assert rec["our_ticker"] == "005930"
    assert rec["market"] == "KR"
    assert rec["stock_code"] == "005930"
    assert rec["corp_code"] == "00126380"
    assert rec["corp_name"] == "삼성전자"
    assert rec["bsns_year"] == "2025"
    assert rec["reprt_code"] == "11011"
    assert rec["fetched_at"]
    assert rec["account_nm"] == "자산총계"
    assert rec["thstrm_amount"] == "566900000000000"


def test_corp_code_loaded_once_across_symbols():
    # WHY: corpCode.xml 은 전체 ZIP 이라 종목마다 다시 받으면 불필요하게 DART 를 두드린다.
    #      run 내에서는 한 번만 받아 메모리 캐시해야 한다.
    corp_body = _corp_zip([
        ("005930", "00126380", "삼성전자"),
        ("000660", "00164779", "SK하이닉스"),
    ])
    responses = {
        ("00126380", "2025", "11011"): _ok([{"account_nm": "자산총계"}]),
        ("00164779", "2025", "11011"): _ok([{"account_nm": "자산총계"}]),
    }
    src = _source(responses, corp_body=corp_body)
    records = list(src.fetch(["005930", "000660"]))

    assert len(records) == 2
    assert sum("corpCode.xml" in url for url in src.client.urls) == 1


def test_status_013_is_no_data_not_target_failure():
    # WHY: DART 013 은 특정 종목·연도·보고서의 데이터 없음이다. 이를 실패로 세면
    #      정상적인 미공시 기간이 partial 로 오염된다. 전량 0건 여부는 step 이 별도 가드한다.
    src = _source({("00126380", "2025", "11011"): _status("013", "조회 데이터 없음")})
    assert list(src.fetch(["005930"])) == []
    assert src.fetch_failures == []


def test_key_or_quota_status_aborts_whole_source():
    # WHY: 키 오류·일한도·IP 제한은 특정 종목 문제가 아니라 소스 전체 문제다. 심볼 격리로
    #      계속 두드리면 안 되고 StopFetch 로 전체 중단해야 한다.
    src = _source({}, corp_body=_status_xml("010", "미등록 키"))
    with pytest.raises(StopFetch):
        list(src.fetch(["005930"]))


def test_expired_key_status_901_aborts_whole_source():
    # WHY: OpenDART 901 은 만료/차단된 계정성 키 오류라 종목별 실패로 계속 호출하면
    #      요청만 소모하고 원인을 흐린다. 키/IP/쿼터와 같이 source-wide 중단이어야 한다.
    src = _source({}, corp_body=_corp_zip([("005930", "00126380", "삼성전자")]))
    src.client.responses[("00126380", "2025", "11011")] = _status("901", "사용자 계정 만료")
    with pytest.raises(StopFetch):
        list(src.fetch(["005930"]))


def test_missing_corp_code_isolated_per_symbol():
    # WHY: 설정 맵에는 있는데 corpCode.xml 에 없는 종목은 그 종목만 실패로 기록하고,
    #      매핑 가능한 나머지 종목 수집은 계속해야 한다.
    src = _source({
        ("00126380", "2025", "11011"): _ok([{"account_nm": "자산총계"}]),
    })
    records = list(src.fetch(["005930", "000660"]))

    assert [r["our_ticker"] for r in records] == ["005930"]
    assert [f["symbol"] for f in src.fetch_failures] == ["000660"]


def test_malformed_success_missing_list_fails_loud():
    # WHY: status=000 인데 list 가 없으면 malformed success 다. 정상 빈 응답처럼 넘기면
    #      success 0건으로 위장되므로 대상 실패로 surface 해야 한다.
    src = _source({("00126380", "2025", "11011"): json.dumps({"status": "000"})})
    records = list(src.fetch(["005930"]))

    assert records == []
    assert [f["symbol"] for f in src.fetch_failures] == ["005930"]


def test_disabled_without_api_key():
    # WHY: 키 미주입이면 이 소스는 비활성(로컬/CI) — 스텝이 skip 으로 드러낸다.
    src = _source({}, api_key=None)
    assert src.enabled is False
