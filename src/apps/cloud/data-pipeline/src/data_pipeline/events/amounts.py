"""결정적 KR 금액/단위 파서 — value·unit 은 코드가 소유한다 (ALPHA-545, v4 이식).

이식원은 실험실(event-ontology repo) normalize/amounts.py 다 — 문법·판정 규칙 무변경.
정직 규칙: 추정 금지, 단위 패밀리 간 환산 금지(``2배``→PCT, ``5년``→일수, USD→KRW 없음).
문법 밖 표기는 value=None + ``parse_flag="no_number"`` 로 돌려줘, 호출부가 숫자를 지어내는
대신 UNRESOLVED 로 기록하게 한다. LLM 은 surface(원문 표기)만 내고 산술은 여기서만 한다
(Rule 5 — 분류·추출은 모델, 결정론 변환은 코드).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

FLAG_OK = "ok"
FLAG_APPROX_OR_RANGE = "approx_or_range"
FLAG_NO_NUMBER = "no_number"
FLAG_CALENDAR_YEAR = "calendar_year"

# '2028년 만기' 는 만기일(역년)이지 기간이 아니다 — 재무 계약에 1900~2999'년짜리 기간'은
# 없으므로 4자리 역년대 YEARS 는 날짜로 읽고 거부한다. 통과시키면 DURATION_DAYS 계열
# 수량이 2028년짜리 기간으로 적재돼 만기·기간 분석이 수천 배 오염된다.
_CALENDAR_YEAR_MIN, _CALENDAR_YEAR_MAX = 1900, 2999

# event_measure.basis CHECK 어휘 — 추출 계약(llm-extract-v4)과 동일.
BASIS_VALUES = frozenset({"TOTAL", "ANNUAL", "UNKNOWN"})

# 큰 자리(조억만)는 그룹 경계, 작은 자리(천백십)는 그룹 안 가수 배율이다 —
# "1천200억" = (1천+200)×억. 세그먼트 독립 합산은 고차 자리를 마지막 세그먼트에만 곱한다.
_SMALL_PLACES = {"천": 10**3, "백": 10**2, "십": 10}
_BIG_PLACES = {"조": 10**12, "억": 10**8, "만": 10**4}
# 만 이상의 자릿수 표기는 KR 뉴스 관례상 단위 없이도 원화로 읽는다.
_IMPLIED_KRW_THRESHOLD = 10**4
_SEGMENT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)([조억만천백십]*)")
_RANGE_SPLIT_RE = re.compile(r"[~∼]")
_APPROX_RE = re.compile(r"약|가량|안팎|이상|이하|최대|최소|넘|육박|(?<=\d)여")

# 순서 중요: 가장 길고 구체적인 토큰 먼저.
_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], str, bool | None], ...] = (
    (re.compile(r"^(달러|USD|불(?![가-힣]))"), "USD", True),
    (re.compile(r"^(원|₩)"), "KRW", True),
    (re.compile(r"^(%p|%포인트|퍼센트포인트|퍼센트|포인트|프로|%)"), "PCT", None),
    (re.compile(r"^개월"), "MONTHS", None),
    (re.compile(r"^(년|해(?![가-힣]))"), "YEARS", None),
    (re.compile(r"^일(?![0-9가-힣])"), "DAYS", None),
    (re.compile(r"^(주|건|명|개|대|척|곳|가구|회|기(?![가-힣])|좌)"), "COUNT", None),
)


@dataclass(frozen=True)
class AmountParse:
    value: float | None
    unit: str | None
    parse_flag: str
    currency_marked: bool | None  # True: 명시 토큰 · False: 관례상 KRW · None: 비통화


_NO_NUMBER = AmountParse(None, None, FLAG_NO_NUMBER, None)


@dataclass(frozen=True)
class _Run:
    value: float
    unit: str | None
    currency_marked: bool | None
    had_unit_token: bool
    had_place: bool
    first_place_product: float


def _segment_value(digits: str, places: str) -> tuple[float, float, float]:
    """→ (그룹 내 가수, 큰 자리 곱, 세그먼트 전체 자리곱)."""
    number = float(digits.replace(",", ""))
    small = big = 1.0
    for char in places:
        if char in _SMALL_PLACES:
            small *= _SMALL_PLACES[char]
        else:
            big *= _BIG_PLACES[char]
    return number * small, big, small * big


def _unit_after(text: str, end: int) -> tuple[str | None, bool | None]:
    rest = text[end:].lstrip()
    for pattern, unit, marked in _UNIT_PATTERNS:
        if pattern.match(rest):
            return unit, marked
    return None, None


def _runs(text: str) -> list[_Run]:
    matches = list(_SEGMENT_RE.finditer(text))
    grouped: list[list[re.Match[str]]] = []
    for match in matches:
        if grouped and text[grouped[-1][-1].end() : match.start()].strip() == "":
            grouped[-1].append(match)
        else:
            grouped.append([match])

    runs: list[_Run] = []
    for group in grouped:
        total = 0.0
        pending = 0.0  # 아직 큰 자리(조억만)를 못 만난 가수
        first_place_product = 1.0
        had_place = False
        for index, match in enumerate(group):
            mantissa, big, product = _segment_value(match.group(1), match.group(2))
            pending += mantissa
            if big > 1.0:
                total += pending * big
                pending = 0.0
            if index == 0:
                first_place_product = product
            if product >= _IMPLIED_KRW_THRESHOLD:
                had_place = True
        total += pending
        start, end = group[0].start(), group[-1].end()
        unit, marked = _unit_after(text, end)
        if unit is None and text[:start].rstrip().endswith("$"):
            unit, marked = "USD", True
        if start > 0 and text[start - 1] in "-−":
            total = -total
        currency_marked = marked
        if unit is None and had_place:
            unit, currency_marked = "KRW", False
        runs.append(_Run(total, unit, currency_marked, marked is not None, had_place, first_place_product))
    return runs


def _parse_single(text: str, *, allow_bare: bool = False) -> _Run | None:
    runs = _runs(text)
    candidates = [run for run in runs if run.unit is not None]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and allow_bare and len(runs) == 1:
        return runs[0]
    return None  # 0개 또는 애매(무관한 금액 여럿): 거부한다 — 절대 추측하지 않는다


def _is_calendar_year(value: float | None, unit: str | None) -> bool:
    """4자리 역년대 YEARS = 날짜 표기('2028년 만기') — 기간으로 읽지 않는다."""
    return (unit == "YEARS" and value is not None and float(value).is_integer()
            and _CALENDAR_YEAR_MIN <= value <= _CALENDAR_YEAR_MAX)


def parse_amount(surface: str | None) -> AmountParse:
    if not surface or not surface.strip():
        return _NO_NUMBER
    text = unicodedata.normalize("NFKC", surface).strip()
    approx = bool(_APPROX_RE.search(text))

    parts = _RANGE_SPLIT_RE.split(text)
    if len(parts) == 2 and all(any(ch.isdigit() for ch in part) for part in parts):
        left = _parse_single(parts[0], allow_bare=True)
        right = _parse_single(parts[1])
        if left is not None and right is not None:
            left_value = left.value
            if left.unit is None:
                # "3~4조원": 좌측 경계는 우측 경계의 자릿수·단위를 물려받는다
                left_value *= right.first_place_product
            midpoint = (left_value + right.value) / 2.0
            if _is_calendar_year(midpoint, right.unit):
                return AmountParse(None, None, FLAG_CALENDAR_YEAR, None)
            return AmountParse(midpoint, right.unit, FLAG_APPROX_OR_RANGE, right.currency_marked)
        return _NO_NUMBER

    run = _parse_single(text)
    if run is None:
        return _NO_NUMBER
    if _is_calendar_year(run.value, run.unit):
        return AmountParse(None, None, FLAG_CALENDAR_YEAR, None)
    flag = FLAG_APPROX_OR_RANGE if approx else FLAG_OK
    return AmountParse(run.value, run.unit, flag, run.currency_marked)


def parse_basis(surface: str | None) -> str:
    """원문이 명시(총/연간)할 때만 basis — 그 외는 UNKNOWN(지어내지 않는다)."""
    if not surface:
        return "UNKNOWN"
    text = unicodedata.normalize("NFKC", surface)
    if "총" in text:
        return "TOTAL"
    if "연간" in text:
        return "ANNUAL"
    return "UNKNOWN"
