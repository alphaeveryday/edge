"""국내 공시 원문을 DART 에서 받는다 (ALPHA-1457).

WHY: KIND 공시 뷰어는 AWS 출구 IP 를 막아 운영에서 원문 읽기가 전부 실패했다(모델이 본문 없이 제목·보도로 판정).
DART 는 같은 공시를 다른 접수번호로 싣는다 — 제목·제출인으로 찾는데, 잘못 고르면 다른 공시의 본문이 증거가 된다.
인증키는 주소에 실리므로 오류 문구에 새면 세션 기록·로그로 퍼진다.
"""
import io
import zipfile

import pytest

from edge_truth_research import dart, tools
from edge_truth_research.dart import Dart, DartError


def _item(no, title, filer):
    return {"rcept_no": no, "report_nm": title, "flr_nm": filer}


def test_match_finds_the_exchange_filing_despite_spacing_and_a_truncated_dart_title():
    items = [_item("20261023900540", "투자판단관련주요경영사항(임상시험결과)              (전이성 거", "퓨쳐켐"),
             _item("20261023000111", "주식등의대량보유상황보고서(일반)", "퓨쳐켐")]
    assert dart.match(items, "투자판단 관련 주요경영사항(임상시험 결과)(전이성 거세저항성 전립선암 환자 대상)", "퓨쳐켐") == "20261023900540"


def test_match_uses_the_filer_for_same_day_same_title_and_refuses_to_guess_otherwise():
    items = [_item("1", "임원ㆍ주요주주특정증권등소유상황보고서", "서경헌"), _item("2", "임원ㆍ주요주주특정증권등소유상황보고서", "홍길동")]
    assert dart.match(items, "임원ㆍ주요주주특정증권등소유상황보고서", "홍길동") == "2"
    assert dart.match(items, "임원ㆍ주요주주특정증권등소유상황보고서", "다른사람") is None  # 고르지 않는다
    assert dart.match(items, "주요사항보고서(유상증자결정)", "홍길동") is None


def test_corp_codes_never_borrow_the_code_of_an_unlisted_corp_listed_before():
    # 비상장사(종목코드 빈칸) 뒤의 상장사가 앞 회사 고유번호를 받으면 다른 회사의 공시 목록을 읽는다
    xml = ("<result><list><corp_code>00000001</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>"
           "<list><corp_code>00000002</corp_code><corp_name>상장</corp_name><stock_code>123456</stock_code></list></result>")
    assert dart.parse_corp_codes(xml) == {"123456": "00000002"}


def _zip(html: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("doc.xml", html)
    return buf.getvalue()


def test_document_reads_the_matched_filing_and_errors_never_carry_the_key(tmp_path, monkeypatch):
    calls = []

    def fake(self, path, **params):
        calls.append((path, params))
        if path == "corpCode.xml":
            return _zip("<list><corp_code>01053540</corp_code><corp_name>퓨쳐켐</corp_name><stock_code>220100</stock_code></list>")
        if path == "list.json":
            return b'{"status": "000", "list": [{"rcept_no": "20261023900540", "report_nm": "\\uc784\\uc0c1\\uc2dc\\ud5d8\\uacb0\\uacfc", "flr_nm": "x"}]}'
        return _zip("<html><style>.x{}</style><body>ORR 38.9%</body></html>") if params["rcept_no"] == "20261023900540" else b""
    monkeypatch.setattr(Dart, "_get", fake)
    d = Dart("SECRET-KEY", cache=tmp_path / "corp.json")
    assert d.document("220100", "20261023000540", "임상시험 결과", "퓨쳐켐") == "ORR 38.9%"
    assert calls[1] == ("list.json", {"corp_code": "01053540", "bgn_de": "20261023", "end_de": "20261023", "page_count": "100"})
    assert d.corp_code("220100") == "01053540" and len([c for c in calls if c[0] == "corpCode.xml"]) == 1  # 표는 한 번만 받는다

    monkeypatch.setattr(Dart, "_get", lambda self, path, **p: (fake(self, path, **p) if path != "document.xml"
                                                               else b"<result><status>013</status><message>no</message></result>"))
    with pytest.raises(DartError, match="원문 오류: no") as error:
        d.document("220100", "20261023000540", "임상시험 결과", "퓨쳐켐")
    assert "SECRET-KEY" not in str(error.value)


def test_http_errors_hide_the_url_with_the_key(monkeypatch):
    import urllib.error
    import urllib.request

    def boom(url, timeout):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(DartError) as error:
        Dart("SECRET-KEY")._get("list.json")
    assert str(error.value) == "DART HTTP 403" and error.value.__cause__ is None


def test_company_filings_go_to_dart_and_market_notices_or_keyless_runs_go_to_kind(tmp_path, monkeypatch):
    from edge_truth_agent.workspace import Workspace
    ws = Workspace.prepare(tmp_path / "ws", mode="research", case=None, inbox=[], index=[], today="2026-10-23",
                           now="2026-10-23T15:35:00+09:00", mover={"code": "220100", "name": "퓨쳐켐", "market": "KOSDAQ", "filings": []})
    seen = []
    monkeypatch.setattr(tools.Kind, "document", lambda self, acpt: seen.append(("kind", acpt)) or "kind")
    monkeypatch.setattr(tools.Dart, "document", lambda self, code, acpt, title, filer: seen.append(("dart", code, acpt)) or "dart")
    company = {"acptno": "20261023000540", "title": "임상시험 결과", "submitter": "퓨쳐켐"}
    notice = {"acptno": "20261023001040", "title": "공매도 과열종목 지정", "submitter": "코스닥시장본부"}
    monkeypatch.setenv("DART_API_KEY", "k")
    assert tools._kr_document(ws, company["acptno"], company) == "dart"
    assert tools._kr_document(ws, notice["acptno"], notice) == "kind"  # 시장조치는 DART 에 없다
    monkeypatch.delenv("DART_API_KEY")
    assert tools._kr_document(ws, company["acptno"], company) == "kind"  # 키 없는 로컬 실행
    assert seen == [("dart", "220100", "20261023000540"), ("kind", "20261023001040"), ("kind", "20261023000540")]
