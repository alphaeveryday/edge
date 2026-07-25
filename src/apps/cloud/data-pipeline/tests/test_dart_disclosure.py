"""DartDisclosureSource 어댑터 테스트 — 공시목록 필터·provenance·status·문서 원본.

공시목록(list.json)은 전 유형을 주므로 report_nm 부분일치로 대상만 낸다. 실측 표기(가운뎃점
ㆍ·꼬리 공백·[기재정정] 접두)와 malformed 입력에서 게이트가 뚫리지 않는지(각도 H)를 검증한다.
각 테스트는 '왜 이 동작이 중요한가'를 주석으로 남긴다(AGENTS Rule 9).
"""

import io
import json
import zipfile

import pytest

from data_pipeline.config import load_settings
from data_pipeline.sources.dart_disclosure import DartDisclosureSource
from data_pipeline.sources.http import StopFetch

CONFIG = """
[news.sources.fmp]
base_url = "https://fmp.example/stable/news/stock"

[price.source]
base_url = "https://fmp.example/stable/historical-price-eod/full"

[dart_disclosure.source]
base_url = "https://dart.example/api"

[dart_disclosure.source.symbol_map]
"005930" = "005930"

[targets]
symbols = ["005930"]
"""


def _corpcode_zip(rows) -> bytes:
    """rows: [(stock_code, corp_code, corp_name)] → corpCode.xml ZIP bytes."""
    xml = "<result>" + "".join(
        f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name>"
        f"<stock_code>{s}</stock_code></list>"
        for s, c, n in rows
    ) + "</result>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


def _doc_zip(rcept_no: str, body: bytes = b"<html>euc-kr body</html>") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{rcept_no}.xml", body)
    return buf.getvalue()


def _param(url: str, key: str) -> str:
    return url.split(f"{key}=")[1].split("&")[0]


class FakeClient:
    """list_pages: {(corp_code, page_no): payload_dict}. corp_rows: corpCode.xml 매핑."""

    def __init__(self, list_pages=None, corp_rows=None, documents=None):
        self.list_pages = list_pages or {}
        self.corp_rows = corp_rows if corp_rows is not None else [("005930", "00126380", "삼성전자")]
        self.documents = documents or {}

    def request(self, method, url, *, headers=None, data=None, decode=True):
        if "/corpCode.xml" in url:
            return _corpcode_zip(self.corp_rows)
        if "/list.json" in url:
            corp_code = _param(url, "corp_code")
            page = int(_param(url, "page_no"))
            payload = self.list_pages.get((corp_code, page), {"status": "013"})
            return json.dumps(payload, ensure_ascii=False)
        if "/document.xml" in url:
            rcept_no = _param(url, "rcept_no")
            return self.documents.get(rcept_no, _doc_zip(rcept_no))
        raise AssertionError(f"unexpected url: {url}")


def _page(rows, *, status="000", total_page=1) -> dict:
    return {"status": status, "message": "정상", "page_no": 1, "page_count": 100,
            "total_count": len(rows), "total_page": total_page, "list": rows}


def _row(report_nm, rcept_no="20260710800910", **over) -> dict:
    base = {
        "corp_code": "00126380", "corp_name": "삼성전자", "stock_code": "005930",
        "corp_cls": "Y", "report_nm": report_nm, "rcept_no": rcept_no,
        "flr_nm": "삼성전자", "rcept_dt": "20260710", "rm": "유",
    }
    base.update(over)
    return base


def _source(tmp_path, client, **override):
    path = tmp_path / "sources.toml"
    path.write_text(CONFIG, encoding="utf-8")
    config = load_settings(path).dart_disclosure.source
    if override:
        config = config.model_copy(update=override)
    return DartDisclosureSource(config, client)


def test_filters_by_report_name_and_attaches_provenance(tmp_path):
    # WHY: 공시목록은 전 유형을 준다 — 대상 유형(공급계약·사업보고서)만 내고, 무관 유형은
    #      버려야 후속이 본문을 무의미하게 재수집하지 않는다. 그리고 list.json 이 안 주는
    #      source_url 은 rcept_no 로 구성해 붙여야(파생 provenance) 다운스트림이 원문을 연다.
    client = FakeClient(list_pages={("00126380", 1): _page([
        _row("단일판매ㆍ공급계약체결              ", rcept_no="A1"),  # 대상(공급계약) + 꼬리공백·ㆍ
        _row("주주총회소집결의", rcept_no="B2"),                     # 비대상
        _row("[기재정정]사업보고서", rcept_no="C3"),                 # 대상(사업보고서, 정정본)
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["A1", "C3"]  # 대상만, 비대상 제외
    r = records[0]
    assert r["market"] == "KR" and r["our_ticker"] == "005930"
    assert r["stock_code"] == "005930"
    assert r["source_url"] == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=A1"
    assert "fetched_at" in r
    assert source.fetch_failures == []


def test_unmapped_corp_code_noted_not_crash(tmp_path):
    # WHY: corpCode.xml 에 종목이 없으면(상장폐지·매핑 공백) 그 대상만 격리해 기록하고 나머지는
    #      계속한다 — 한 종목 결측이 런을 죽이면 안 된다(fail loud, 격리≠은폐).
    # corpCode.xml 자체는 정상이지만(다른 회사 존재) 우리 종목(005930)이 없다 — 그 대상만 격리.
    client = FakeClient(corp_rows=[("000660", "00164779", "SK하이닉스")])
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert records == []
    assert len(source.fetch_failures) == 1
    assert "corp_code 없음" in source.fetch_failures[0]["error"]
    # kind=unmapped — holdings 유니버스에는 DART 신고자가 아닌 종목이 상수로 섞여 매 런 같은
    # 수가 걸린다. 재시도로 낫지 않는 구조적 결측이라 스텝이 런을 죽이지 않게 구분한다
    # (ALPHA-477). 계측에서는 빠지지 않는다 — 아래 스텝 테스트가 그걸 고정한다.
    assert source.fetch_failures[0]["kind"] == "unmapped"


def test_kr_short_code_plans_by_identity_without_symbol_map(tmp_path):
    # WHY: symbol_map 이 곧 수집 유니버스이던 시절엔 손으로 적은 9 종만 통과해, holdings 로
    #      넓힌 유니버스(309 구성종목)가 plan() 에서 도로 잘려나갔다(ALPHA-477). KRX 단축코드는
    #      corpCode.xml 의 stock_code 와 같은 값이라 항등이 기본이어야 한다. 문자 섞인 신형
    #      단축코드(0093A0)도 대상이다 — isdigit 로 보면 그게 조용히 빠진다(ALPHA-463).
    client = FakeClient()
    source = _source(tmp_path, client, api_key="k", symbol_map={})

    assert source.plan(["005930", "0093A0"]) == [("005930", "005930"), ("0093A0", "0093A0")]


def test_non_kr_symbol_excluded_from_plan(tmp_path):
    # WHY: 항등 폴백이 US 심볼까지 주워담으면 국내 전용 API 인 OpenDART 에 엉뚱한 질의가 간다.
    #      targets 에는 US 9 종이 섞여 있으므로 형태 판정(krx_short_code)이 경계를 지켜야 한다.
    client = FakeClient()
    source = _source(tmp_path, client, api_key="k", symbol_map={})

    assert source.plan(["AAPL", "NVDA"]) == []
    assert list(source.fetch(["AAPL"])) == []
    assert source.planned_symbols == 0


def test_symbol_map_overrides_identity(tmp_path):
    # WHY: symbol_map 은 삭제된 게 아니라 '항등이 아닌 예외'의 오버라이드 축으로 남는다
    #      (수급 소스와 동일 정책). 오버라이드가 항등을 이기지 못하면 예외 종목을 표현할 길이 없다.
    client = FakeClient()
    source = _source(tmp_path, client, api_key="k", symbol_map={"005930": "999999"})

    assert source.plan(["005930"]) == [("005930", "999999")]


def test_status_013_is_empty_window_no_failure(tmp_path):
    # WHY: 조회 데이터 없음(013)은 그 corp·기간에 공시가 없다는 정상 빈 창이다(뉴스형) —
    #      실패로 기록하면 정상 상태를 오탐한다.
    client = FakeClient(list_pages={})  # 기본 013
    source = _source(tmp_path, client, api_key="k")

    assert list(source.fetch(["005930"])) == []
    assert source.fetch_failures == []


def test_stop_status_raises_stopfetch(tmp_path):
    # WHY: 쿼터 초과(020)·키 오류 등은 특정 종목 문제가 아니라 소스 전체 문제라 즉시 중단해야
    #      한다 — 재시도로 두드리거나 조용히 넘기면 안 된다.
    client = FakeClient(list_pages={("00126380", 1): _page([], status="020")})
    source = _source(tmp_path, client, api_key="k")

    with pytest.raises(StopFetch):
        list(source.fetch(["005930"]))


def test_malformed_row_isolated_others_yielded(tmp_path):
    # WHY(각도 H): list[] 에 비객체 행이 섞여도 그 행만 격리하고 정상 대상 행은 계속 내야 한다 —
    #      malformed 입력이 게이트를 뚫거나 런을 죽이면 안 된다.
    client = FakeClient(list_pages={("00126380", 1): _page([
        "not-a-dict",
        _row("단일판매ㆍ공급계약체결", rcept_no="A1"),
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["A1"]
    assert len(source.fetch_failures) == 1
    assert "malformed row" in source.fetch_failures[0]["error"]


def test_non_string_report_nm_isolated_not_crash(tmp_path):
    # WHY(각도 H — crash-before-gate): 행 타입만 보고 필드 타입을 안 보면, 비문자열 report_nm
    #      (malformed 응답의 숫자 등)이 .strip() 에서 터져 그 corp 전체를 죽이고 뒷페이지를
    #      버린다 — 행 단위로 격리해 정상 행은 계속 나와야 한다.
    client = FakeClient(list_pages={("00126380", 1): _page([
        _row("공급계약체결", rcept_no="A1"),  # report_nm 을 숫자로 덮어씀 ↓
        {"report_nm": 12345, "rcept_no": "N2", "stock_code": "005930"},
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))
    assert [r["rcept_no"] for r in records] == ["A1"]  # 정상 행 보존
    assert any("report_nm 비문자열" in f["error"] for f in source.fetch_failures)


def test_non_string_rcept_no_isolated_not_crash(tmp_path):
    # WHY(각도 H — crash-before-gate/unchecked-field): rcept_no 가 문서키인데 비문자열(숫자)로
    #      오면 .strip() 에서 터진다 — 격리 기록하고 정상 행은 계속. 빈 문서키로 통과시키지도 않는다.
    client = FakeClient(list_pages={("00126380", 1): _page([
        {"report_nm": "단일판매ㆍ공급계약체결", "rcept_no": 20260710800910, "stock_code": "005930"},
        _row("사업보고서", rcept_no="OK9"),
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))
    assert [r["rcept_no"] for r in records] == ["OK9"]
    assert any("rcept_no 결측/비문자열" in f["error"] for f in source.fetch_failures)


def test_unparseable_total_page_noted_not_silent_truncation(tmp_path):
    # WHY(각도 H/Rule 12): total_page 가 present 인데 파싱 불가면 몇 페이지인지 몰라 조용히
    #      1페이지에서 멈추면 목록 절단이 은폐된다 — 감사(fetch_failure)로 드러내야 한다.
    client = FakeClient(list_pages={("00126380", 1): {
        "status": "000", "message": "정상", "list": [_row("공급계약", rcept_no="P1")],
        "total_page": "??",  # 이상값
    }})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))
    assert [r["rcept_no"] for r in records] == ["P1"]  # 1페이지분은 보존
    assert any("total_page 파싱 불가" in f["error"] for f in source.fetch_failures)


def test_target_with_missing_rcept_no_noted_not_yielded(tmp_path):
    # WHY(각도 H): 대상 유형인데 rcept_no 가 비면(문서키 결측) 본문을 못 받고 정체성도 못 잡는다 —
    #      빈 문서키로 통과시키지 말고 격리 기록한다(coerce-to-passing 방지).
    client = FakeClient(list_pages={("00126380", 1): _page([
        _row("단일판매ㆍ공급계약체결", rcept_no=""),
    ])})
    source = _source(tmp_path, client, api_key="k")

    assert list(source.fetch(["005930"])) == []
    assert len(source.fetch_failures) == 1
    assert "rcept_no 결측" in source.fetch_failures[0]["error"]


def test_pagination_follows_total_page(tmp_path):
    # WHY: 한 corp·창의 공시가 여러 페이지면 total_page 까지 순회해 누락이 없어야 한다.
    client = FakeClient(list_pages={
        ("00126380", 1): _page([_row("공급계약체결", rcept_no="P1")], total_page=2),
        ("00126380", 2): _page([_row("사업보고서", rcept_no="P2")], total_page=2),
    })
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))
    assert {r["rcept_no"] for r in records} == {"P1", "P2"}


def test_fetch_document_returns_zip_bytes(tmp_path):
    # WHY: 본문은 무변형 ZIP bytes 로 보존한다(euc-kr HTML) — 디코딩·파싱은 후속 소관.
    client = FakeClient()
    source = _source(tmp_path, client, api_key="k")

    body = source.fetch_document("20260710800910")
    assert body[:2] == b"PK"


def test_fetch_document_error_xml_raises(tmp_path):
    # WHY(각도 H): document.xml 이 ZIP 이 아니라 에러 XML(잘못된 rcept_no·쿼터)이면 그걸
    #      본문으로 저장하면 안 된다 — status 로 판정해 StopFetch/ValueError 로 드러낸다.
    err_xml = b"<result><status>020</status><message>quota</message></result>"
    client = FakeClient(documents={"X9": err_xml})
    source = _source(tmp_path, client, api_key="k")

    with pytest.raises(StopFetch):
        source.fetch_document("X9")


def test_disabled_without_api_key(tmp_path):
    # WHY: 키 미주입이면 enabled=False — 스텝이 조용한 성공 대신 skip 으로 드러낼 수 있게.
    source = _source(tmp_path, FakeClient(), api_key=None)
    assert source.enabled is False


def test_config_defaults_report_filters(tmp_path):
    # WHY: sources.toml 미지정 시 기본 대상 유형(공급계약·사업보고서)이 적용돼야 한다.
    source = _source(tmp_path, FakeClient(), api_key="k")
    assert source.report_name_filters == ["공급계약", "사업보고서"]
