"""근거 데이터 구조 v3 — 근거 조각 1개 = 1행 (`.tmp/근거 포맷에 대한 정리.md` §0~§6 구현).

플랫 5종(가격·구성종목·공시·뉴스·재무및컨센서스)은 설명 문장이 값을 직접 인용한
것이고, 통계검정은 위 다섯을 입력으로 소비하는 파생 근거다 - 같은 3줄 골격(내용·
출처·시각)에 통계검정만 추가정보가 붙는다(§1).

저장은 전부 영문 코드다(§0) - 한글 라벨은 콘솔(`labels.ts`)의 몫이라 여기서 만들지
않는다. 이 모듈은 vocab.py 와 같은 패턴(닫힌 어휘 frozenset + `_need` 검증)을 쓴다 -
같은 파일들이 같은 방식으로 닫혀 있어야 검정 결과를 근거로 옮기는 접합부가 하나만
있으면 된다.

band(§3.6)·series 문법(§3.7)은 스펙에는 있지만 지금 엔진(`tool_baserate` 등)에는
없다 - 이 모듈이 그 빠진 조각(`band_of`)을 새로 놓는다. `RELATED_STOCKS` 는 §3.3 이
"현재 렌더 불가"라고 명시한 방법이라 `series_for` 가 여기서 예외를 낸다(§9-M1).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# §2 "{dataset} 에 창을 붙이지 않는다(`· 08-07 09:00–13:20` 아님) - 시점은 시각
# 줄이 이미 말한다." 시:분–시:분 꼴이 dataset 문자열에 섞이면 그 정보가 두 곳에
# 중복되고, 창이 갱신될 때 한쪽만 고쳐지는 드리프트가 생긴다.
_TIME_WINDOW = re.compile(r"\d{1,2}:\d{2}\s*[-–~]\s*\d{1,2}:\d{2}")


def _josa(word: str, with_jong: str, without_jong: str) -> str:
    """받침에 따라 조사(이/가·은/는·을/를)를 고른다.

    §3.5 의 문형 표는 "{event_title}가 난 종목" 처럼 "가"를 문자 그대로 적어
    뒀지만, 실제 슬롯값(공시·사건 제목)은 태반이 "변경"·"결정"·"인상"처럼 받침으로
    끝난다 - 그대로 이으면 "…변경가 났다" 같은 비문이 나간다(§7 케이스 C 의
    실제 예문은 이미 "인상**이** 있던 날"로 손으로 고쳐 적혀 있어, 표 자체가
    조사 합성을 문자 그대로 하라는 뜻이 아님을 스펙 스스로 보여준다).

    알고리즘은 이 저장소의 다른 서술층(`causal/p8_findings.py::_josa`)과 같다 -
    같은 문제의 같은 해법을 두 번 다르게 짜면 한쪽만 늙는다.
    """
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return with_jong if (ord(ch) - 0xAC00) % 28 else without_jong
        if ch.isalnum():
            return with_jong if ch.isdigit() or ch.lower() in "lmnr" else without_jong
    return without_jong


# §3.5 문형. 슬롯 바로 뒤에 받침-의존 조사(이/가)가 오는 세 자리만 `_josa` 로
# 동적으로 고른다 - "의"·"에"·"까지"·"에 따라"는 받침과 무관해 문자 그대로 둔다.
_TEMPLATE_RENDER = {
    "MATCHED_ATT": lambda s: (
        f"「{s['event_title']}」{_josa(s['event_title'], '이', '가')} 난 종목이 "
        "비슷한 다른 종목보다 더 움직였나"),
    "MARKET_EVENT": lambda s: (
        f"{s['etype']}{_josa(s['etype'], '이', '가')} 있던 날 시장이 평소와 "
        "다르게 움직였나"),
    "TUPLE_PANEL": lambda s: (
        f"{s['trigger']}{_josa(s['trigger'], '이', '가')} 크게 움직인 날, "
        f"{s['channel']}에 민감한 종목이 더 움직였나"),
    "RELATION_PANEL": lambda s: (
        f"{s['src']}의 사건이 {s['rel']} 관계인 {s['dst']}까지 영향을 줬나"),
    "MODERATION": lambda s: f"「{s['event']}」의 영향이 {s['conditions']}에 따라 달라지나",
    "EVENT_TAIL": lambda s: (
        f"「{s['event_title']}」 이후 움직임이 이 종목의 평소와 견줘 큰 편인가"),
}


def render_template(template: str, slots: dict[str, str]) -> str:
    """§3.5 문형 + 슬롯 → 실제 문장. 저장은 코드(§0)지만 렌더는 사람이 읽는 글이다."""
    if template not in _TEMPLATE_RENDER:
        raise EvidenceFormatError(f"template={template!r} 는 §3.5 문형 밖이다")
    return _TEMPLATE_RENDER[template](slots)

# ── 유형 (§1) ──────────────────────────────────────────────────────────
EVIDENCE_TYPES = frozenset({
    "PRICE", "HOLDING", "DISCLOSURE", "NEWS", "FINANCIAL", "STAT_TEST"})
FLAT_TYPES = EVIDENCE_TYPES - {"STAT_TEST"}
# §1 "유형 순서 고정" - 렌더·dedup 정렬의 1차 키.
TYPE_ORDER = ("PRICE", "HOLDING", "DISCLOSURE", "NEWS", "FINANCIAL", "STAT_TEST")

# ── 통계검정 닫힌 어휘 (§3) ─────────────────────────────────────────────
BASES = frozenset({"MARKET", "SECTOR", "IDIO"})                              # §3.2
METHODS = frozenset({                                                        # §3.3
    "SIMILAR_STOCKS", "SIMILAR_DAYS", "SENSITIVE_STOCKS", "RELATED_STOCKS",
    "BY_CONDITION", "VS_USUAL"})
TEMPLATES = frozenset({                                                      # §3.5
    "MATCHED_ATT", "MARKET_EVENT", "TUPLE_PANEL", "RELATION_PANEL",
    "MODERATION", "EVENT_TAIL"})
# template ↔ method 는 1:1 이다(§3.5 표) - 어긋나면 렌더 문형과 방법 라벨이 갈린다.
TEMPLATE_METHOD = {
    "MATCHED_ATT": "SIMILAR_STOCKS", "MARKET_EVENT": "SIMILAR_DAYS",
    "TUPLE_PANEL": "SENSITIVE_STOCKS", "RELATION_PANEL": "RELATED_STOCKS",
    "MODERATION": "BY_CONDITION", "EVENT_TAIL": "VS_USUAL"}
METHOD_TEMPLATE = {v: k for k, v in TEMPLATE_METHOD.items()}
# 템플릿 문형의 슬롯 이름 (§3.5). `slots` 딕셔너리 키는 이 집합과 정확히 같아야 한다 -
# 더 있으면 안 쓰이는 값이 조용히 저장되고, 모자라면 렌더가 빈칸을 낸다.
TEMPLATE_SLOTS = {
    "MATCHED_ATT": frozenset({"event_title"}),
    "MARKET_EVENT": frozenset({"etype"}),
    "TUPLE_PANEL": frozenset({"trigger", "channel"}),
    "RELATION_PANEL": frozenset({"src", "rel", "dst"}),
    "MODERATION": frozenset({"event", "conditions"}),
    "EVENT_TAIL": frozenset({"event_title"}),
}
BANDS = frozenset({"TOP_TAIL", "UPPER", "MIDDLE", "LOWER", "BOTTOM_TAIL"})    # §3.6
UNITS = frozenset({"COUNT", "DAY"})
# "date" 는 §3.1 표가 자격을 안 준다 - 순환이라 빌드 게이트 이전에 이미 걸러진다.
NULL_KINDS = frozenset({"label", "pair"})

# ── 플랫 5종 닫힌 어휘 (§2) ─────────────────────────────────────────────
DISCLOSURE_SOURCES = frozenset({"DART", "KRX", "KIND"})
FIN_METRICS = frozenset({"매출액", "영업이익", "순이익", "EPS", "목표주가"})
FIN_KINDS = frozenset({"실적", "컨센서스"})

# ── series 문법 (§3.7) — 세 슬롯의 어휘 ──────────────────────────────────
SERIES_FREQ = frozenset({"일봉", "5분봉", "1분봉"})
SERIES_MEASURE = frozenset({"수익률", "변화"})
# method 별 series 칸 수(§3.7 표). RELATED_STOCKS 는 없음 = 렌더 불가.
_SERIES_ARITY = {
    "SIMILAR_STOCKS": (2, 2), "SIMILAR_DAYS": (1, 1), "SENSITIVE_STOCKS": (2, 2),
    "VS_USUAL": (1, 1), "BY_CONDITION": (2, None)}    # None = 상한 없음(조건 계열 가변)


class EvidenceFormatError(ValueError):
    """근거 포맷 위반. §0 의 "근거 0인 문장은 빌드 실패" 를 코드로 강제한다."""


class NotRenderable(EvidenceFormatError):
    """§3.3/§9-M1 이 명시한, 현재 렌더 불가한 방법(RELATED_STOCKS)을 만들려는 시도."""


def _need(value: str, vocab: frozenset[str], slot: str) -> str:
    if value not in vocab:
        raise EvidenceFormatError(f"{slot}={value!r} 는 닫힌 어휘 밖이다: {sorted(vocab)}")
    return value


# ── §3.6 band — 스펙에는 있지만 엔진(tool_baserate)엔 없던 조각 ──────────
def band_of(pct_rank: float | None) -> str | None:
    """백분위(0~1, 절댓값 기준)를 5단 band 로 접는다.

    `tool_baserate._base_rate` 가 내는 `pct_rank`(자기 과거 분포 내 순위)를 그대로
    받는다. 경계는 §3.6 표를 그대로 코드화한다 - `>=0.95`·`>=0.75`·`>0.25`·`>0.05`
    순서로 위에서부터 걸러 전 구간을 빈틈없이 덮는다(0.25·0.05 는 아래쪽에 붙는다).
    `None` 이면 놓을 분포 자체가 없다는 뜻이라(§3.6 "값이 없으면 줄이 생략") 그대로
    `None` 을 돌려준다 - 침묵을 만들지 않는다.
    """
    if pct_rank is None:
        return None
    if not 0.0 <= pct_rank <= 1.0:
        raise EvidenceFormatError(f"pct_rank 는 [0,1] 이어야 한다: {pct_rank!r}")
    if pct_rank >= 0.95:
        return "TOP_TAIL"
    if pct_rank >= 0.75:
        return "UPPER"
    if pct_rank > 0.25:
        return "MIDDLE"
    if pct_rank > 0.05:
        return "LOWER"
    return "BOTTOM_TAIL"


# ── §3.7 series 문법 ─────────────────────────────────────────────────────
def series_name(target: str, freq: str, measure: str) -> str:
    """`{대상} {빈도} {측정}` 한 칸. 손으로 적는 칸이 아니라 유도값이라 여기만 거친다."""
    if not target or not target.strip():
        raise EvidenceFormatError("series 대상이 비었다")
    _need(freq, SERIES_FREQ, "series.빈도")
    _need(measure, SERIES_MEASURE, "series.측정")
    return f"{target} {freq} {measure}"


def series_for(method: str, items: list[str]) -> tuple[str, ...]:
    """method 가 요구하는 series 칸 수(§3.7 표)를 강제한다.

    `RELATED_STOCKS` 는 표에 칸이 없다 - "렌더 불가"(§3.3)라 예외로 막는다. 다른
    다섯은 칸 수만 검사한다 - 무엇을 채울지는 호출자(어댑터)가 §3.7 조립 규칙대로
    이미 만든 문자열을 넘긴다(중복 조립하지 않는다 - SSOT 는 `series_name`).
    """
    _need(method, METHODS, "method")
    if method == "RELATED_STOCKS":
        raise NotRenderable(
            "RELATED_STOCKS 는 현재 렌더 불가다(§3.3·§9-M1) - series 를 만들 수 없다")
    lo, hi = _SERIES_ARITY[method]
    n = len(items)
    if n < lo or (hi is not None and n > hi):
        want = f"{lo}개" if hi == lo else f"{lo}개 이상"
        raise EvidenceFormatError(f"{method} 의 series 는 {want}이어야 한다 (받음 {n}개)")
    if not all(s.strip() for s in items):
        raise EvidenceFormatError("series 칸에 빈 문자열이 있다")
    if method in ("SIMILAR_STOCKS", "SENSITIVE_STOCKS") and items[0] == items[1]:
        raise EvidenceFormatError(f"{method} 는 대상이 다른 두 칸이어야 한다: {items!r}")
    return tuple(items)


# ── 행 골격 (§1) ─────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """여섯 유형이 공유하는 3줄 골격 + (통계검정만) 추가정보.

    `ref` 는 정수다(§1) - 문자열이면 사전순이 e_9 > e_10 을 낸다. `detail` 은 플랫
    5종엔 없고(§1 "플랫 5종은 추가정보가 없다") STAT_TEST 에만 채워진다.
    """

    ref: int
    type: str
    content: str
    source: str
    time: str = ""              # 통계검정은 항상 "" - §3.4 "시각 줄 자체를 생략"
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _need(self.type, EVIDENCE_TYPES, "type")
        if self.ref < 0:
            raise EvidenceFormatError(f"ref 는 음이 아닌 정수다: {self.ref}")
        if not self.content.strip() or not self.source.strip():
            raise EvidenceFormatError("근거 0인 문장은 빌드 실패다(§0) - content/source 필수")
        if self.type == "STAT_TEST" and self.time:
            raise EvidenceFormatError("STAT_TEST 는 시각 줄이 없다(§3.4) - time 은 항상 빈 문자열")
        if self.type != "STAT_TEST" and self.detail:
            raise EvidenceFormatError("플랫 5종은 추가정보(detail)가 없다(§1)")

    def sort_key(self) -> tuple[int, int]:
        """§1 "유형 순서 고정 → 같은 유형 내부는 ref 오름차순"."""
        return (TYPE_ORDER.index(self.type), self.ref)


def dedup_rows(rows: list[EvidenceRow]) -> list[EvidenceRow]:
    """§1 "동일 ref 는 카드당 1행" + 정렬. 첫 등장을 대표로 남긴다(안정 정렬)."""
    seen: dict[int, EvidenceRow] = {}
    for r in rows:
        seen.setdefault(r.ref, r)
    return sorted(seen.values(), key=EvidenceRow.sort_key)


# ── 플랫 5종 팩토리 (§2 표를 그대로 문자열로) ─────────────────────────────
def price_row(ref: int, *, dataset: str, vendor: str, as_of: str) -> EvidenceRow:
    if _TIME_WINDOW.search(dataset):
        raise EvidenceFormatError(
            f"dataset 에 시간창을 붙이지 않는다(§2) - 시점은 time 줄이 말한다: {dataset!r}")
    return EvidenceRow(ref, "PRICE", content=dataset, source=vendor, time=as_of)


def holding_row(ref: int, *, as_of_basis: str, vendor: str, as_of: str) -> EvidenceRow:
    return EvidenceRow(ref, "HOLDING", content=f"PDF 구성비중 · {as_of_basis}",
                       source=vendor, time=as_of)


def disclosure_row(ref: int, *, title: str, source_code: str, rcept_no: str,
                   published_at: str) -> EvidenceRow:
    _need(source_code, DISCLOSURE_SOURCES, "공시.source_code")
    return EvidenceRow(ref, "DISCLOSURE", content=f"「{title}」",
                       source=f"{source_code} · {rcept_no}", time=published_at)


def news_row(ref: int, *, title: str, publisher: str, published_at: str) -> EvidenceRow:
    return EvidenceRow(ref, "NEWS", content=f"「{title}」", source=publisher,
                       time=published_at)


def financial_row(ref: int, *, metric: str, kind: str, period: str, vendor: str,
                  as_of: str) -> EvidenceRow:
    _need(metric, FIN_METRICS, "재무.항목")
    _need(kind, FIN_KINDS, "재무.실적|컨센서스")
    return EvidenceRow(ref, "FINANCIAL", content=f"{metric}({kind}) · {period}",
                       source=vendor, time=as_of)


# ── §3.1 통계검정 레코드 ─────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class StatTestRecord:
    """`type` 은 항상 `"STAT_TEST"` 다(§3.1) - 필드로 안 받고 상수로 고정한다."""

    ref: int
    template: str
    basis: str
    slots: dict[str, str]
    method: str
    n: int
    unit: str
    estimate: float                 # 비율로 저장(§3.1) - 렌더가 ×100
    p: float
    series: tuple[str, ...]
    k: int = 1
    band: str | None = None
    null_kind: str = "pair"         # 감사 전용(§3.1) - 카드에 렌더하지 않는다

    def __post_init__(self) -> None:
        _need(self.template, TEMPLATES, "template")
        _need(self.basis, BASES, "basis")
        _need(self.method, METHODS, "method")
        _need(self.unit, UNITS, "unit")
        _need(self.null_kind, NULL_KINDS, "null_kind")
        if TEMPLATE_METHOD[self.template] != self.method:
            raise EvidenceFormatError(
                f"template={self.template!r} 은 method={TEMPLATE_METHOD[self.template]!r} "
                f"전용이다 (받음 {self.method!r}) - §3.5 는 1:1 이다")
        want_slots = TEMPLATE_SLOTS[self.template]
        got_slots = frozenset(self.slots)
        if got_slots != want_slots:
            raise EvidenceFormatError(
                f"{self.template} 의 slots 는 {sorted(want_slots)} 이어야 한다 "
                f"(받음 {sorted(got_slots)})")
        if any(not str(v).strip() for v in self.slots.values()):
            raise EvidenceFormatError("slots 값에 빈 문자열이 있다")
        if self.n < 1:
            raise EvidenceFormatError(f"n 은 1 이상이다: {self.n}")
        if not 0.0 <= self.p <= 1.0:
            raise EvidenceFormatError(f"p 는 [0,1] 이어야 한다: {self.p}")
        if self.k < 1:
            raise EvidenceFormatError(f"k 는 1 이상이다: {self.k}")
        if self.band is not None:
            _need(self.band, BANDS, "band")
        series_for(self.method, list(self.series))     # RELATED_STOCKS 는 여기서 막힌다

    def to_row(self, ref: int | None = None) -> EvidenceRow:
        """§3.4 렌더 골격 3줄 + 추가정보. `content` 는 문형+슬롯(§3.5), 시각은 없다."""
        detail = {"basis": self.basis, "method": self.method, "n": self.n,
                  "unit": self.unit, "estimate": self.estimate, "p": self.p,
                  "band": self.band}
        if self.k > 1:
            detail["k"] = self.k       # §3.4 "k=1 이면 렌더 생략"
        return EvidenceRow(ref if ref is not None else self.ref, "STAT_TEST",
                          content=render_template(self.template, self.slots),
                          source=" · ".join(self.series), time="",
                          detail={k: v for k, v in detail.items() if v is not None})


__all__ = [
    "BANDS", "BASES", "DISCLOSURE_SOURCES", "EVIDENCE_TYPES", "EvidenceFormatError",
    "EvidenceRow", "FIN_KINDS", "FIN_METRICS", "FLAT_TYPES", "METHODS",
    "METHOD_TEMPLATE", "NULL_KINDS", "NotRenderable", "SERIES_FREQ", "SERIES_MEASURE",
    "StatTestRecord", "TEMPLATES", "TEMPLATE_METHOD", "TEMPLATE_SLOTS", "TYPE_ORDER",
    "UNITS", "band_of", "dedup_rows", "disclosure_row", "financial_row", "holding_row",
    "news_row", "price_row", "render_template", "series_for", "series_name",
]
