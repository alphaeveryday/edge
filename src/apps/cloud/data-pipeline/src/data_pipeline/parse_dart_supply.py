"""DART 단일판매ㆍ공급계약 공시 본문 파서 (euc-kr HTML → 공급계약 필드).

⚠️ 출처(이식): 이 파일의 파싱 로직은 팀원 **정준영**의 검증 프로토타입(alphamale)
`src/alphamale/filings/dart/supply.py`(parse_supply·parse_krw_amount·parse_ratio_pct·
날짜/계약상대방 헬퍼)를 edge 프로덕션 파이프라인으로 **이식**한 것이다. jy 는 별 저장소라
의존성으로 import 하지 않고 코드를 반입했다. graph 투영(rows_to_edges·slugify_counterparty·
theme 링킹)은 edge 범위 밖(analysis-engine 소관)이라 **이식하지 않았다** — 이 모듈은
본문 str → 원자 필드 dict 로 끝나는 **순수 파서**다(rcept_no·corp_code·ticker·report_date 는
raw 메타에서 정제 스텝이 조인한다).

본문은 OpenDART document.xml ZIP 내부의 euc-kr HTML(xforms 테이블)이다 — `extract_document_html`
이 ZIP 에서 본문 멤버를 골라 euc-kr 로 디코딩한다(jy `decode_document_bytes` 이식). 파싱은
`parse_supply(html) -> dict` 하나다.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

_NULL_MARKERS = frozenset({"", "-", "--", "—", "―", "n/a", "na", "nan", "none", "null", "미정", "unknown"})
_WITHHELD_COUNTERPARTY_MARKERS = frozenset({"-", "--", "—", "―", "unknown", "비공개", "공시유보"})
_WITHHELD_COUNTERPARTY_FRAGMENTS = ("경영상비밀유지", "비밀유지", "공시유보", "비공개")
_AMOUNT_UNIT_FACTORS = {
    "천억원": 10**11,
    "천억": 10**11,
    "조원": 10**12,
    "조": 10**12,
    "억원": 10**8,
    "억": 10**8,
    "백만원": 10**6,
    "백만": 10**6,
    "만원": 10**4,
    "만": 10**4,
    "원": 1,
}
_AMOUNT_UNIT_PATTERN = re.compile(
    r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(천억원|천억|조원|조|억원|억|백만원|백만|만원|만|원)"
)


def _normalize_text(text: object | None) -> str:
    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _canonical_label(text: object | None) -> str:
    value = _normalize_text(text)
    value = re.sub(r"^\d+[.)]\s*", "", value)
    value = re.sub(r"^[-※•·]+\s*", "", value)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", value)


def _is_nullish(text: object | None) -> bool:
    value = _normalize_text(text)
    return not value or value.lower() in _NULL_MARKERS


def _counterparty_fields(text: object | None) -> tuple[str | None, str | None, bool]:
    """계약상대방 원문 → (정규화값, 원문, 유보여부). 비밀유지·공시유보·비공개·결측은 withheld."""
    raw = _normalize_text(text)
    if _is_nullish(raw):
        return None, raw or None, True
    compact = re.sub(r"\s+", "", raw).lower()
    if compact in _WITHHELD_COUNTERPARTY_MARKERS or any(fragment in compact for fragment in _WITHHELD_COUNTERPARTY_FRAGMENTS):
        return None, raw, True
    return raw, raw, False


def parse_krw_amount(text: object | None) -> int | None:
    """한국식 금액 표기(조/천억/억/백만/만/원)를 KRW 정수로 환산. 못 읽으면 None."""
    value = _normalize_text(text)
    if _is_nullish(value):
        return None

    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1]
    value = value.replace("△", "-")

    total = 0.0
    matched = False
    for number_text, unit in _AMOUNT_UNIT_PATTERN.findall(value):
        matched = True
        total += float(number_text.replace(",", "")) * _AMOUNT_UNIT_FACTORS[unit]
    if matched:
        if negative:
            total = -abs(total)
        return int(round(total))

    compact = value.replace(",", "").replace(" ", "")
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", compact):
        return int(round(float(compact)))

    match = re.search(r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)", value)
    if match is None:
        return None
    return int(round(float(match.group(1).replace(",", ""))))


def parse_ratio_pct(text: object | None) -> float | None:
    """텍스트에서 비율(%) 수치 하나를 뽑는다 — 없으면 None."""
    value = _normalize_text(text)
    if _is_nullish(value):
        return None
    match = re.search(r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)", value)
    return float(match.group(1).replace(",", "")) if match else None


def parse_date_value(text: object | None) -> date | None:
    """텍스트에서 날짜 하나 — 구분자형 우선, 8자리 컴팩트 폴백, 실패는 None."""
    value = _normalize_text(text)
    if _is_nullish(value):
        return None

    # 구분자 문자군의 '-' 는 반드시 끝에 둔다 — `[./-년]` 처럼 가운데 두면 `/`~`년` 범위가 되어
    # 숫자까지 매치해 컴팩트 날짜('20240429')를 잘못 파싱한다('20240429'→2024-04-09). 끝의 '-' 는
    # 리터럴이라 구분자('2024-04-29')만 잡고, 컴팩트 날짜는 아래 8자리 폴백으로 흘러간다(Codex P2).
    match = re.search(r"(\d{4})\s*[./년-]\s*(\d{1,2})\s*[./월-]\s*(\d{1,2})", value)
    if match is not None:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def parse_date_range(text: object | None) -> tuple[date | None, date | None]:
    """텍스트의 날짜 최대 둘을 (첫째, 둘째) 로 — 부족한 칸은 None.

    구분자형 표기(./년월- 구분) 앞 둘을 먼저 검사하고, 유효한 것이 **하나도
    없을 때만** 8자리 컴팩트 표기 폴백을 본다(두 형식을 섞어 세지 않는다).
    각 단계 모두 앞의 두 표기만 보며, 무효 표기는 건너뛰어 자리가 당겨진다.
    """
    value = _normalize_text(text)
    # '-' 는 문자군 끝(리터럴) — parse_date_value 와 동일 이유(컴팩트 날짜 오파싱 방지, Codex P2).
    matches = re.findall(r"(\d{4})\s*[./년-]\s*(\d{1,2})\s*[./월-]\s*(\d{1,2})", value)
    dates: list[date] = []
    for year_text, month_text, day_text in matches[:2]:
        try:
            dates.append(date(int(year_text), int(month_text), int(day_text)))
        except ValueError:
            continue

    if not dates:
        for digits in re.findall(r"\d{8}", re.sub(r"\D", "", value))[:2]:
            try:
                dates.append(date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])))
            except ValueError:
                continue

    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], None
    return None, None


def _table_rows(soup: BeautifulSoup) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [_normalize_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _table_score(rows: list[list[str]]) -> int:
    keys = (
        "계약상대방",
        "계약상대",
        "계약금액총액",
        "확정계약금액",
        "계약금액",
        "최근매출액",
        "매출액대비",
        "계약기간",
        "시작일",
        "종료일",
        "체결계약명",
        "판매공급계약내용",
    )
    score = 0
    for row in rows:
        joined = "".join(_canonical_label(cell) for cell in row)
        score += sum(1 for key in keys if key in joined)
    return score


def _row_pairs(rows: list[list[str]]) -> list[tuple[str, str, list[str]]]:
    pairs: list[tuple[str, str, list[str]]] = []
    for row in rows:
        if len(row) == 2:
            pairs.append((row[0], row[1], row))
            continue
        for index in range(len(row) - 1):
            pairs.append((row[index], row[index + 1], row))
    return pairs


def _find_value(rows: list[list[str]], *keys: str) -> str | None:
    wanted = tuple(_canonical_label(key) for key in keys)
    for label, value, row in _row_pairs(rows):
        label_key = _canonical_label(label)
        if not any(key in label_key for key in wanted):
            continue
        row_head = _canonical_label(row[0]) if row else ""
        if row_head.endswith("계약내역") and label == row[0]:
            continue
        if row_head.endswith("계약기간") and label == row[0]:
            # 계약기간이 시작일/종료일 서브라벨을 거느린 그룹 헤더면 그 헤더-서브라벨 쌍을 건너뛴다
            # (서브라벨을 기간 값으로 오인 방지). 단, 2컬럼 `계약기간 | 2024.01.02 ~ …` 처럼 값 칸이
            # 실제 날짜면 건너뛰지 않는다 — 안 그러면 그 기간이 통째 유실된다(Codex P2).
            value_key = _canonical_label(value)
            if not value_key or value_key in ("시작일", "종료일"):
                continue
        return _normalize_text(value)
    return None


def _find_first_nonnull(rows: list[list[str]], *keys: str) -> str | None:
    """키를 **우선순위 순으로 하나씩** 시도해 첫 non-nullish 값을 반환한다.

    `_find_value` 는 여러 키를 한 번에 받아 '테이블 순서상 첫 매치'를 주므로, 조건부 계약처럼
    특정 라벨(예: `확정 계약금액`='-')이 `계약금액 총액` 위에 먼저 오면 nullish 를 집어 실제
    총액을 놓친다. 이 헬퍼는 넘긴 키 우선순위(총액 우선)를 존중하고 nullish 매치는 건너뛴다
    (이식원 jy `parse_supply` 대비 edge 개선 — 조건부 계약 금액 유실 방지, Codex P2).
    """
    for key in keys:
        value = _find_value(rows, key)
        if value is not None and not _is_nullish(value):
            return value
    return None


def _find_period(rows: list[list[str]]) -> tuple[date | None, date | None]:
    start = parse_date_value(_find_value(rows, "시작일"))
    end = parse_date_value(_find_value(rows, "종료일"))
    if start or end:
        return start, end
    return parse_date_range(_find_value(rows, "계약기간"))


def _extract_corp_name(soup: BeautifulSoup) -> str | None:
    title = _normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    if "/" in title:
        corp_name = title.split("/", 1)[0].strip()
        return corp_name or None
    return None


def _confidence(corp_name: str | None, counterparty: str | None, amount_krw: int | None,
                ratio_pct: float | None, start: date | None, end: date | None, object_text: str | None) -> str:
    if corp_name and counterparty and object_text and (amount_krw is not None or ratio_pct is not None) and (start or end):
        return "full"
    return "partial"


def parse_supply(html_or_xml_text: str) -> dict[str, Any]:
    """단일판매ㆍ공급계약 공시 본문 하나를 원자 필드 dict 로 파싱한다.

    출력: corp_name·counterparty(_raw·_withheld)·amount_krw·ratio_pct·start·end·object·
    confidence(full=핵심필드 다 있음/partial)·evidence(amount_text·ratio_text 원문). 순수
    함수 — rcept_no·corp_code·ticker·report_date 는 raw 메타에서 정제 스텝이 조인한다.
    """
    soup = BeautifulSoup(html_or_xml_text, "html.parser")
    tables = _table_rows(soup)
    rows = max(tables, key=_table_score, default=[])

    corp_name = _extract_corp_name(soup)
    counterparty_raw = _find_value(rows, "계약상대방", "계약상대")
    counterparty, counterparty_raw, counterparty_withheld = _counterparty_fields(counterparty_raw)
    object_text = _find_value(rows, "체결계약명", "판매ㆍ공급계약 내용", "판매ㆍ공급계약내용", "세부내용")
    # 총액을 우선하고 nullish(조건부 계약의 '-' 등)는 건너뛴다 — 특정 라벨이 총액 위에 먼저
    # 와도 실제 계약금액(총액)을 놓치지 않게(_find_first_nonnull 참고, Codex P2).
    amount_text = _find_first_nonnull(rows, "계약금액 총액(원)", "계약금액 총액", "확정 계약금액", "계약금액(원)", "계약금액")
    ratio_text = _find_value(rows, "매출액대비(%)", "매출액대비", "매출액 대비(%)", "매출액 대비")
    start, end = _find_period(rows)
    amount_krw = parse_krw_amount(amount_text)
    ratio_pct = parse_ratio_pct(ratio_text)

    return {
        "corp_name": corp_name,
        "counterparty": counterparty,
        "counterparty_raw": counterparty_raw,
        "counterparty_withheld": counterparty_withheld,
        "amount_krw": amount_krw,
        "ratio_pct": ratio_pct,
        "start": start,
        "end": end,
        "object": object_text,
        "confidence": _confidence(corp_name, counterparty, amount_krw, ratio_pct, start, end, object_text),
        "evidence": {
            "amount_text": amount_text,
            "ratio_text": ratio_text,
        },
    }


# ── 본문 ZIP 추출·디코딩 (jy fetch.decode_document_bytes·_select_primary_member 이식) ──
def _select_primary_member(names: list[str], archive: zipfile.ZipFile) -> str:
    """ZIP 멤버 중 본문 문서를 고른다 — xml > htm/html/txt > 기타, 동급이면 큰 파일 우선."""
    def rank(name: str) -> tuple[int, int, str]:
        """정렬 키 — (형식 등급, -크기, 이름)."""
        lowered = name.lower()
        if lowered.endswith(".xml"):
            bucket = 0
        elif lowered.endswith((".htm", ".html", ".txt")):
            bucket = 1
        else:
            bucket = 2
        return (bucket, -archive.getinfo(name).file_size, name)

    return sorted(names, key=rank)[0]


def _decode_document_bytes(data: bytes) -> str:
    """공시 본문 bytes → str. 실측상 euc-kr 이나, utf-8→cp949(euc-kr 상위집합) 폴백으로 방어."""
    for encoding in ("utf-8", "cp949"):
        try:
            return data.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def extract_document_html(zip_bytes: bytes) -> str:
    """raw 로 저장된 document.xml ZIP bytes → 본문 HTML str(euc-kr 디코딩).

    OpenDART document.xml 응답은 ZIP 이고 내부에 `{rcept_no}.xml`(실제 euc-kr HTML) 1개가
    들어 있다. 본문 멤버를 골라 디코딩한다. 빈 ZIP·비-ZIP 은 ValueError(정제 스텝이 사유로 격리)."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if not names:
                raise ValueError("공시서류 ZIP 이 비었음")
            member = _select_primary_member(names, archive)
            data = archive.read(member)
    except zipfile.BadZipFile as exc:
        raise ValueError("공시서류 본문이 ZIP 이 아님") from exc
    return _decode_document_bytes(data)


__all__ = [
    "parse_supply",
    "parse_krw_amount",
    "parse_ratio_pct",
    "parse_date_value",
    "parse_date_range",
    "extract_document_html",
]
