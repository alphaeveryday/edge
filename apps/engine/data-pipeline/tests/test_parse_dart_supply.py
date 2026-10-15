"""공급계약 파서 회귀 — jy(정준영) 프로토타입 검증 픽스처를 edge 로 이식해 값 고정.

파서 로직은 jy `filings/dart/supply.py` 이식이라, jy 가 검증한 실제 공시 3건으로 fact 값을
고정한다(이식 중 값이 어긋나면 실패). + withheld/malformed 방어(각도 H) + euc-kr ZIP 추출.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from data_pipeline import parse_dart_supply as PS

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "disclosure"

# jy 가 검증한 실제 공시 3건(라이브 실측) — 이식 후에도 동일 값이어야 한다.
REAL_FIXTURES = {
    "fiber": {
        "path": FIXTURES_DIR / "supply_fiberpro_20260623900750.html",
        "corp_name": "파이버프로",
        "counterparty": "한화에어로스페이스(주)",
        "amount_krw": 17_899_464_000,
        "ratio_pct": 92.33,
        "start": date(2024, 4, 29),
        "end": date(2029, 2, 10),
        "object": "OO급 관성측정기 수출용 소요자재 공급계약",
    },
    "taeyoung": {
        "path": FIXTURES_DIR / "supply_taeyoung_20260623800740.html",
        "corp_name": "태영건설",
        "counterparty": "국가철도공단",
        "amount_krw": 112_757_480_000,
        "ratio_pct": 4.94,
        "start": date(2021, 6, 29),
        "end": date(2027, 11, 30),
        "object": "호남고속철도2단계(고막원~목포) 제5공구 건설공사",
    },
    "namkwang": {
        "path": FIXTURES_DIR / "supply_namkwang_20260623800716.html",
        "corp_name": "남광토건",
        "counterparty": "한국토지주택공사",
        "amount_krw": 45_868_044_800,
        "ratio_pct": 19.42,
        "start": date(2020, 12, 28),
        "end": date(2026, 6, 24),
        "object": "포항블루밸리 국가산업단지 2단계 조성공사 1공구",
    },
}


@pytest.mark.parametrize("fixture_key", ["fiber", "taeyoung", "namkwang"])
def test_parse_supply_real_fixture(fixture_key: str) -> None:
    expected = REAL_FIXTURES[fixture_key]
    parsed = PS.parse_supply(expected["path"].read_text(encoding="utf-8"))

    assert parsed["corp_name"] == expected["corp_name"]
    assert parsed["counterparty"] == expected["counterparty"]
    assert parsed["counterparty_raw"] == expected["counterparty"]
    assert parsed["counterparty_withheld"] is False
    assert parsed["amount_krw"] == expected["amount_krw"]
    assert parsed["ratio_pct"] == pytest.approx(expected["ratio_pct"])
    assert parsed["start"] == expected["start"]
    assert parsed["end"] == expected["end"]
    assert parsed["object"] == expected["object"]
    assert parsed["confidence"] == "full"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234백만원", 1_234_000_000),
        ("12억", 1_200_000_000),
        ("1,234,567,890원", 1_234_567_890),
        ("1억 2,500만원", 125_000_000),
    ],
)
def test_parse_krw_amount_handles_korean_units(text: str, expected: int) -> None:
    assert PS.parse_krw_amount(text) == expected


def test_parse_date_range_handles_inline_period() -> None:
    assert PS.parse_date_range("계약기간 2024.04.29 ~ 2029.02.10") == (date(2024, 4, 29), date(2029, 2, 10))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("20240429", date(2024, 4, 29)),   # 컴팩트 YYYYMMDD — 구분자 문자군에 안 걸려 폴백 파싱
        ("2024.04.29", date(2024, 4, 29)),  # 점 구분자
        ("2024-04-29", date(2024, 4, 29)),  # 하이픈 구분자(리터럴)
        ("2024년 04월 29일", date(2024, 4, 29)),
    ],
)
def test_parse_date_value_handles_compact_and_separated(text: str, expected: date) -> None:
    """구분자 문자군의 '-' 를 끝(리터럴)에 둬, 컴팩트 날짜가 '20240429'→2024-04-09 로 오파싱되지
    않고 8자리 폴백으로 정확히 파싱되게 한다(Codex P2 — 범위 '/'~'년' 이 숫자를 먹던 버그)."""
    assert PS.parse_date_value(text) == expected


def test_parse_date_range_handles_compact_dates() -> None:
    assert PS.parse_date_range("20240429 ~ 20290210") == (date(2024, 4, 29), date(2029, 2, 10))


def test_parse_supply_inline_period_row_not_dropped() -> None:
    """2컬럼 `계약기간 | 날짜` 행(시작일/종료일 서브라벨 없음)의 기간이 유실되지 않아야 한다 —
    그룹 헤더 스킵 가드가 실제 날짜 값 칸까지 건너뛰던 문제(Codex P2)."""
    html = """
    <html><head><title>인라인/공급</title></head><body>
      <table>
        <tr><td>계약상대방</td><td>발주처(주)</td></tr>
        <tr><td>계약기간</td><td>2024.01.02 ~ 2025.03.04</td></tr>
      </table>
    </body></html>
    """
    parsed = PS.parse_supply(html)
    assert parsed["start"] == date(2024, 1, 2)
    assert parsed["end"] == date(2025, 3, 4)


def test_parse_supply_detects_withheld_counterparty() -> None:
    withheld = """
    <html>
      <head><title>비밀테스트/단일판매ㆍ공급계약체결</title></head>
      <body>
        <table>
          <tr><td>계약상대방</td><td>경영상 비밀유지 요청에 따른 공시유보</td></tr>
          <tr><td>체결계약명</td><td>샘플 공급계약</td></tr>
          <tr><td>계약금액</td><td>1,200,000,000원</td></tr>
          <tr><td>매출액 대비</td><td>12.5</td></tr>
          <tr><td>계약기간</td><td>2024.01.02 ~ 2025.03.04</td></tr>
        </table>
      </body>
    </html>
    """
    parsed = PS.parse_supply(withheld)
    assert parsed["corp_name"] == "비밀테스트"
    assert parsed["counterparty"] is None
    assert parsed["counterparty_raw"] == "경영상 비밀유지 요청에 따른 공시유보"
    assert parsed["counterparty_withheld"] is True
    assert parsed["confidence"] == "partial"


@pytest.mark.parametrize("counterparty_text", ["-", "unknown", "비공개", "공시유보"])
def test_parse_supply_marks_common_withheld_markers(counterparty_text: str) -> None:
    html = f"""
    <html>
      <head><title>마커테스트/단일판매ㆍ공급계약체결</title></head>
      <body>
        <table>
          <tr><td>계약상대방</td><td>{counterparty_text}</td></tr>
          <tr><td>체결계약명</td><td>샘플 공급계약</td></tr>
          <tr><td>매출액 대비</td><td>12.5</td></tr>
          <tr><td>계약기간</td><td>2024.01.02 ~ 2025.03.04</td></tr>
        </table>
      </body>
    </html>
    """
    parsed = PS.parse_supply(html)
    assert parsed["counterparty"] is None
    assert parsed["counterparty_raw"] == counterparty_text
    assert parsed["counterparty_withheld"] is True


def test_parse_supply_malformed_input_returns_partial_not_exception() -> None:
    """각도 H: 테이블은 있으나 라벨 결측·비수치인 malformed 본문이 crash 없이 partial 로 나온다."""
    malformed = """
    <html>
      <head><title>테스트/단일판매ㆍ공급계약체결</title></head>
      <body>
        <table>
          <tr><td>3. 계약상대</td><td></td></tr>
          <tr><td>5. 계약기간</td><td>미정</td></tr>
        </table>
      </body>
    </html>
    """
    parsed = PS.parse_supply(malformed)
    assert parsed["counterparty"] is None
    assert parsed["counterparty_raw"] is None
    assert parsed["counterparty_withheld"] is True
    assert parsed["amount_krw"] is None
    assert parsed["ratio_pct"] is None
    assert parsed["start"] is None
    assert parsed["end"] is None
    assert parsed["confidence"] == "partial"


def test_parse_supply_conditional_contract_prefers_total_over_nullish_component() -> None:
    """조건부 계약: 확정 계약금액='-'가 총액 위에 먼저 와도 실제 총액을 집어야 한다(Codex P2).
    _find_value 의 '테이블 순서 첫 매치'가 nullish 를 집던 유실을 우선순위+skip 으로 방지."""
    conditional = """
    <html>
      <head><title>조건부테스트/단일판매ㆍ공급계약체결</title></head>
      <body>
        <table>
          <tr><td>계약상대방</td><td>발주처(주)</td></tr>
          <tr><td>체결계약명</td><td>조건부 공급계약</td></tr>
          <tr><td>확정 계약금액</td><td>-</td></tr>
          <tr><td>조건부 계약금액</td><td>-</td></tr>
          <tr><td>계약금액 총액(원)</td><td>112,757,480,000</td></tr>
          <tr><td>매출액 대비</td><td>4.94</td></tr>
          <tr><td>계약기간</td><td>2024.01.02 ~ 2025.03.04</td></tr>
        </table>
      </body>
    </html>
    """
    parsed = PS.parse_supply(conditional)
    assert parsed["amount_krw"] == 112_757_480_000  # 총액 — nullish 확정금액을 건너뜀


def test_parse_supply_no_table_returns_partial() -> None:
    """각도 H: 테이블이 아예 없는 본문도 crash 없이 전부 None/partial 로 나온다."""
    parsed = PS.parse_supply("<html><head><title>없음/공급</title></head><body><p>본문</p></body></html>")
    assert parsed["counterparty"] is None
    assert parsed["amount_krw"] is None
    assert parsed["confidence"] == "partial"


def test_parse_supply_uses_detail_row_as_contract_object() -> None:
    """공식 공급계약 양식의 ``- 세부내용``도 계약 식별에 필요한 대상이다."""
    html = """
    <html>
      <head><title>디아이/단일판매ㆍ공급계약체결</title></head>
      <body>
        <table>
          <tr><td>1. 판매ㆍ공급계약 구분</td><td>기타 판매ㆍ공급계약</td></tr>
          <tr><td>- 세부내용</td><td>반도체 검사장비 공급계약</td></tr>
          <tr><td>3. 계약상대</td><td>삼성전자(주)</td></tr>
          <tr><td>계약금액(원)</td><td>10,000,000,000</td></tr>
          <tr><td>매출액대비(%)</td><td>4.5</td></tr>
          <tr><td>계약기간</td><td>2026.07.30 ~ 2027.07.29</td></tr>
        </table>
      </body>
    </html>
    """

    parsed = PS.parse_supply(html)

    assert parsed["object"] == "반도체 검사장비 공급계약"
    assert parsed["confidence"] == "full"


# ── 본문 ZIP 추출·euc-kr 디코딩 ──────────────────────────
def _zip_with(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(name, data)
    return buf.getvalue()


def test_extract_document_html_decodes_euckr() -> None:
    """실측: 본문은 euc-kr HTML. ZIP 에서 골라 euc-kr(cp949 폴백)로 디코딩한다."""
    html = "<html><head><title>한글/공급계약</title></head><body>계약상대방</body></html>"
    zip_bytes = _zip_with("20260101000001.xml", html.encode("euc-kr"))
    assert PS.extract_document_html(zip_bytes) == html


def test_extract_document_html_prefers_xml_member() -> None:
    """xml 멤버가 htm/기타보다 우선 선택된다(본문 선택 랭킹)."""
    zip_bytes = _zip_with_multi({
        "readme.txt": b"noise",
        "20260101000002.xml": "본문XML".encode("euc-kr"),
    })
    assert PS.extract_document_html(zip_bytes) == "본문XML"


def _zip_with_multi(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buf.getvalue()


def test_extract_document_html_non_zip_raises_valueerror() -> None:
    """각도 H: 비-ZIP bytes(에러 XML 등)는 ValueError — 정제 스텝이 사유로 격리."""
    with pytest.raises(ValueError):
        PS.extract_document_html(b"<result><status>013</status></result>")


def test_extract_document_html_empty_zip_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        PS.extract_document_html(_zip_with_multi({}))
