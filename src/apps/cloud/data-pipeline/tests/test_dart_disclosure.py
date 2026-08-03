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
    """list_pages: {page_no: payload_dict}. corp_rows: corpCode.xml 매핑(enrich 경로 전용).

    ⚠️ list.json 은 **corp_code 없이** 불린다 — 종목별 질의를 걷어냈다. 그래서 이 fake 는
    페이지 번호만 키로 쓰고, corp_code 파라미터가 되살아나면 아래 단언이 그걸 잡는다.
    """

    def __init__(self, list_pages=None, corp_rows=None, documents=None):
        self.list_pages = list_pages or {}
        self.corp_rows = corp_rows if corp_rows is not None else [("005930", "00126380", "삼성전자")]
        self.documents = documents or {}
        self.list_urls: list[str] = []
        self.corpcode_calls = 0

    def request(self, method, url, *, headers=None, data=None, decode=True):
        if "/corpCode.xml" in url:
            self.corpcode_calls += 1
            return _corpcode_zip(self.corp_rows)
        if "/list.json" in url:
            assert "corp_code=" not in url, f"공시목록에 corp_code 가 실렸다: {url}"
            self.list_urls.append(url)
            page = int(_param(url, "page_no"))
            payload = self.list_pages.get(page, {"status": "013"})
            return json.dumps(payload, ensure_ascii=False)
        if "/document.xml" in url:
            rcept_no = _param(url, "rcept_no")
            return self.documents.get(rcept_no, _doc_zip(rcept_no))
        raise AssertionError(f"unexpected url: {url}")


def _page(rows, *, status="000", total_page=1, page_no=1) -> dict:
    # ⚠️ page_no 는 **요청한 페이지를 그대로 에코**한다(실 API 실측 2026-08-03). 픽스처가 모든
    # 페이지에 1 을 박아두면 어댑터의 에코 검증이 픽스처에서만 오작동해, 그 가드를 지우는
    # 회귀가 초록으로 통과한다(test-fixture-must-mirror-prod-config).
    return {"status": status, "message": "정상", "page_no": page_no, "page_count": 100,
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
    client = FakeClient(list_pages={1: _page([
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


def test_foreign_rows_filtered_without_touching_corpcode(tmp_path):
    # WHY: 시장 전체 목록에는 우리가 수집하지 않는 회사의 행이 하루 수백 건 섞여 온다 —
    #      유니버스 밖은 **정상적으로 버리는 것**이지 실패가 아니다. 그리고 종목→corp_code
    #      해소가 수집 경로에서 사라졌다는 것도 여기서 고정한다: corpCode.xml 을 부르면
    #      유니버스 크기만큼 미매핑 실패가 되살아나 원장이 매 런 INCOMPLETE 로 묶인다.
    client = FakeClient(list_pages={1: _page([
        _row("단일판매ㆍ공급계약체결", rcept_no="MINE"),
        _row("단일판매ㆍ공급계약체결", rcept_no="THEIRS", stock_code="000660"),  # 유니버스 밖
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["MINE"]
    assert source.fetch_failures == []  # 남의 회사 행은 결측이 아니다
    assert client.corpcode_calls == 0


def test_foreign_row_defect_is_not_our_failed_record(tmp_path):
    # WHY: 필드 게이트를 유니버스 필터보다 **앞에** 두면, 우리가 수집하지도 않는 회사의
    #      malformed 행이 우리 런의 failed_records 로 올라가 원장이 없는 결측을 센다.
    #      종목별 질의 시절엔 남의 행을 볼 일이 없어 없던 경로다.
    client = FakeClient(list_pages={1: _page([
        {"stock_code": "000660", "report_nm": 12345, "rcept_no": "X"},  # 유니버스 밖 + 깨진 필드
        _row("사업보고서", rcept_no="OK"),
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["OK"]
    assert source.fetch_failures == []


def test_row_repeated_across_shifted_pages_collapses(tmp_path):
    # WHY: 목록은 수집 중에도 자란다(접수 피크 16시) — 새 공시가 끼어들면 페이지 경계가 밀려
    #      같은 행이 두 페이지에 걸쳐 나온다. 접지 않으면 한 런이 같은 문서를 두 번 내려받고
    #      raw 에도 중복 행이 앉는다. 후속 canonical dedup 은 이미 쓴 대역폭을 돌려주지 않는다.
    dup = _row("단일판매ㆍ공급계약체결", rcept_no="DUP")
    client = FakeClient(list_pages={
        1: _page([dup], total_page=2),
        2: _page([dup, _row("사업보고서", rcept_no="NEW")], total_page=2, page_no=2),
    })
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["DUP", "NEW"]


def test_window_scanned_once_regardless_of_universe_size(tmp_path):
    # WHY: 이 PR 의 존재 이유다 — 호출 수가 유니버스 크기에 비례하면(311 종 = 311 콜, 간격
    #      1.0s ⇒ ~311초) 잦은 실행이 불가능하다. 창당 페이지 수에만 비례해야 한다.
    client = FakeClient(list_pages={1: _page([_row("공급계약", rcept_no="A1")])})
    source = _source(tmp_path, client, api_key="k", symbol_map={})

    list(source.fetch([f"{i:06d}" for i in range(1, 51)]))  # 50 종

    assert len(client.list_urls) == 1


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
    client = FakeClient(list_pages={1: _page([], status="020")})
    source = _source(tmp_path, client, api_key="k")

    with pytest.raises(StopFetch):
        list(source.fetch(["005930"]))


def test_malformed_row_isolated_others_yielded(tmp_path):
    # WHY(각도 H): list[] 에 비객체 행이 섞여도 그 행만 격리하고 정상 대상 행은 계속 내야 한다 —
    #      malformed 입력이 게이트를 뚫거나 런을 죽이면 안 된다.
    client = FakeClient(list_pages={1: _page([
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
    client = FakeClient(list_pages={1: _page([
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
    client = FakeClient(list_pages={1: _page([
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
    client = FakeClient(list_pages={1: {
        "status": "000", "message": "정상", "page_no": 1,
        "list": [_row("공급계약", rcept_no="P1")],
        "total_page": "??",  # 이상값
    }})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))
    assert [r["rcept_no"] for r in records] == ["P1"]  # 1페이지분은 보존
    assert any("total_page" in f["error"] for f in source.fetch_failures)


def test_target_with_missing_rcept_no_noted_not_yielded(tmp_path):
    # WHY(각도 H): 대상 유형인데 rcept_no 가 비면(문서키 결측) 본문을 못 받고 정체성도 못 잡는다 —
    #      빈 문서키로 통과시키지 말고 격리 기록한다(coerce-to-passing 방지).
    client = FakeClient(list_pages={1: _page([
        _row("단일판매ㆍ공급계약체결", rcept_no=""),
    ])})
    source = _source(tmp_path, client, api_key="k")

    assert list(source.fetch(["005930"])) == []
    assert len(source.fetch_failures) == 1
    assert "rcept_no 결측" in source.fetch_failures[0]["error"]


def test_pagination_follows_total_page(tmp_path):
    # WHY: 한 corp·창의 공시가 여러 페이지면 total_page 까지 순회해 누락이 없어야 한다.
    client = FakeClient(list_pages={
        1: _page([_row("공급계약체결", rcept_no="P1")], total_page=2),
        2: _page([_row("사업보고서", rcept_no="P2")], total_page=2, page_no=2),
    })
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))
    assert {r["rcept_no"] for r in records} == {"P1", "P2"}


@pytest.mark.parametrize("echoed", [1, "1"])
def test_repeated_page_echo_noted_not_silent_loss(tmp_path, echoed):
    # WHY(각도 H): 캐시·벤더 이상으로 page 2 요청에 1페이지가 그대로 돌아오면, rcept_no dedup
    #      이 반복 행을 걷어내고 루프는 total_page 까지 정상 완주해 **뒷페이지 전량이 빠진
    #      success 런**이 된다. dedup 이 증상을 지우므로 이 확인이 없으면 사후에도 복원되지
    #      않는다 — 실 API 는 요청 page_no 를 그대로 에코한다(실측 2026-08-03).
    #      ⚠️ 문자열 에코("1")도 함께 고정한다 — 타입으로 가드를 끄면(`isinstance(int)` 일 때만
    #      대조) 벤더가 문자열로 바꾸는 순간 가드가 조용히 죽는다.
    page1 = _page([_row("공급계약", rcept_no="P1")], total_page=3, page_no=echoed)
    client = FakeClient(list_pages={1: page1, 2: page1, 3: page1})  # 2·3 도 page_no=1
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["P1"]  # 1페이지분은 보존
    assert any("page_no" in f["error"] for f in source.fetch_failures)


@pytest.mark.parametrize("bad_total_page", [False, 0, -3, 1.9, "많음", None, "MISSING"])
def test_malformed_total_page_noted_not_coerced_to_one(tmp_path, bad_total_page):
    # WHY(각도 H — coerce-to-passing): `max(1, int(raw))` 는 False·0·-3·1.9 를 전부 "1페이지"
    #      라는 통과값으로 바꾼다. 창 전체가 한 순회인 지금 1페이지 오판은 100행만 남기고
    #      나머지를 통째로 버리면서 실패 기록조차 남기지 않는다.
    #      **결측(None·키 부재)도 통과시키지 않는다**: 종전 "없으면 단일 페이지로 본다"는 상한이
    #      corp 당이던 시절엔 대체로 맞았지만(한 회사의 한 창은 실제로 1페이지), 지금은
    #      total_count=1800 인 응답에서 이 필드만 빠져도 1,700행을 조용히 버린다.
    payload = {
        "status": "000", "message": "정상", "page_no": 1,
        "list": [_row("공급계약", rcept_no="P1")],
    }
    if bad_total_page != "MISSING":  # "MISSING" = 키 자체가 없는 경우
        payload["total_page"] = bad_total_page
    client = FakeClient(list_pages={1: payload})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["P1"]  # 받은 1페이지분은 보존
    assert any("total_page" in f["error"] for f in source.fetch_failures)


def test_missing_page_no_noted_not_skipping_the_guard(tmp_path):
    # WHY(각도 H): 결측을 통과시키면 **필드가 빠지는 순간 가드가 죽는다** — page_no 없이 같은
    #      1페이지가 반복돼도 dedup 이 중복을 지우고 total_page 까지 완주해 success 로 끝난다.
    #      page_no 는 응답 계약에 있는 필드다(실측) — 없다는 건 응답을 못 믿는다는 뜻이다.
    page = {"status": "000", "message": "정상", "total_page": 3,
            "list": [_row("공급계약", rcept_no="P1")]}  # page_no 키 없음
    client = FakeClient(list_pages={1: page, 2: page, 3: page})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["P1"]
    assert any("page_no" in f["error"] for f in source.fetch_failures)


def test_late_013_is_a_failure_not_an_empty_window(tmp_path):
    # WHY(Rule 12): 1페이지가 total_page=3 으로 여러 페이지의 실재를 이미 신고했다. 그 뒤의
    #      013 은 "이 창은 비었다"가 아니라 목록 변동·캐시 불일치이고 남은 페이지는 안 읽힌
    #      것이다. 1페이지 013(정상 빈 창)과 같이 묶으면 그 유실이 success 로 남는다.
    client = FakeClient(list_pages={
        1: _page([_row("공급계약", rcept_no="P1")], total_page=3),
        2: {"status": "013"},
    })
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["P1"]
    assert any("013" in f["error"] for f in source.fetch_failures)


def test_total_page_smaller_than_current_page_noted(tmp_path):
    # WHY(각도 H): 공유 파서(`_as_page_number`)는 "페이지 번호로 읽히는가"만 본다 — 두 필드의
    #      의미까지는 못 지킨다. page=2 응답의 total_page=1 은 파서엔 유효하지만 모순이고,
    #      `page >= total_page` 가 참이 돼 **조용한 정상 종료**가 된다.
    client = FakeClient(list_pages={
        1: _page([_row("공급계약", rcept_no="P1")], total_page=3),
        2: _page([_row("사업보고서", rcept_no="P2")], total_page=1, page_no=2),  # 모순
    })
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["P1", "P2"]  # 받은 만큼은 보존
    assert any("total_page=1" in f["error"] for f in source.fetch_failures)


def test_non_string_stock_code_noted_not_folded_into_foreign(tmp_path):
    # WHY(각도 H — unchecked-field): stock_code 가 비문자열로 오면 **누구 것인지 판정할 수
    #      없다** — 우리 종목일 수도 있다. 유니버스 밖(정상 스킵)으로 접어 버리면 그 유실이
    #      영영 안 보인다. 판정 불가와 유니버스 밖은 다른 사건이다(Rule 12).
    client = FakeClient(list_pages={1: _page([
        {"stock_code": 5930, "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": "Z1"},
        _row("사업보고서", rcept_no="OK"),
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["OK"]
    assert any("stock_code 비문자열" in f["error"] for f in source.fetch_failures)


def test_blank_stock_code_is_normal_not_a_failure(tmp_path):
    # WHY: 비상장·펀드 신고자는 단축코드가 없다 — 하루 수백 건이다. 이걸 실패로 세면 원장이
    #      매 런 없는 결측을 수백 건 보고, INCOMPLETE 가 상시가 되어 신호가 죽는다.
    client = FakeClient(list_pages={1: _page([
        _row("단일판매ㆍ공급계약체결", rcept_no="FUND", stock_code=" "),
        _row("사업보고서", rcept_no="OK"),
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert [r["rcept_no"] for r in records] == ["OK"]
    assert source.fetch_failures == []


def test_vendor_stock_code_preserved_verbatim_in_raw(tmp_path):
    # WHY(레이크 규약): raw 는 벤더 원본을 무변형 보존한다(bronze). 매칭용으로 strip 한 값을
    #      되쓰면 벤더 이상(패딩 등)을 raw 에서 재현·감사할 수 없다. 우리 축은 our_ticker 가
    #      이미 담고 있고, 정규화된 stock_code 의 소비자는 현재 0건이다.
    client = FakeClient(list_pages={1: _page([
        _row("공급계약", rcept_no="PAD", stock_code=" 005930 "),
    ])})
    source = _source(tmp_path, client, api_key="k")

    records = list(source.fetch(["005930"]))

    assert len(records) == 1  # 패딩돼도 매칭은 된다
    assert records[0]["stock_code"] == " 005930 "  # 그러나 원본은 그대로다
    assert records[0]["our_ticker"] == "005930"


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
