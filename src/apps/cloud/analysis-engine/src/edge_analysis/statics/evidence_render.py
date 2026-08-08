"""근거 행 → 사람 문장 — 결정론 렌더 참조 구현 (ALPHA-888, §3.4·§4·§10.3).

콘솔(labels.ts)이 나중에 이식할 참조 구현이다 — 산문 경로에는 물리지 않는다.
같은 레코드면 같은 바이트를 낸다: 입력 밖의 상태(시계·난수·로케일)를 읽지 않고,
형식은 §4 공통 규칙을 문자 그대로 코드화한다.

카드 어휘 규율(스펙 독자 기준): 통계 훈련 없이 읽힌다 — `귀무`·`ATT`·`노출`·
`고유층` 같은 말은 여기서 절대 나오지 않는다. 그 계약은 테스트가 지킨다.
"""
from __future__ import annotations

from .evidence_card import EvidenceFormatError, EvidenceRow, StatTestRecord

# ── 코드 ↔ 한글 라벨 (§10.3 계약과 같은 값 — 콘솔 labels.ts 의 원본) ──────
EVIDENCE_TYPE_LABEL = {
    "PRICE": "가격",
    "HOLDING": "구성종목",
    "DISCLOSURE": "공시",
    "NEWS": "뉴스",
    "FINANCIAL": "재무및컨센서스",
    "STAT_TEST": "통계검정",
}

METHOD_LABEL = {
    "SIMILAR_STOCKS": "비슷한종목비교",
    "SIMILAR_DAYS": "비슷한날비교",
    "SENSITIVE_STOCKS": "민감한종목비교",
    "RELATED_STOCKS": "관계있는종목비교",
    "BY_CONDITION": "조건별효과",
    "VS_USUAL": "평소대비",
}

BASIS_LABEL = {
    "MARKET": "시장 전체 움직임",
    "SECTOR": "업종 전체 움직임",
    "IDIO": "이 종목만의 움직임",
}

BAND_LABEL = {
    "TOP_TAIL": "과거보다 훨씬 큼",
    "UPPER": "과거보다 큼",
    "MIDDLE": "과거와 비슷한 수준",
    "LOWER": "과거보다 작음",
    "BOTTOM_TAIL": "과거보다 훨씬 작음",
}

UNIT_LABEL = {"COUNT": "건", "DAY": "일"}

# TUPLE_PANEL 의 {channel} 코드 → 카드 이름 (§3.5).
CHANNEL_LABEL = {
    "Q수량": "판매량",
    "P판가": "판매가격",
    "C원가": "원가",
    "FX환": "환율",
    "R금리신용": "금리·신용",
    "S주식수": "주식수",
    "π확률": "사업확률",
    "K위험": "위험",
}

_PAD = " " * 15         # 둘째 줄부터의 들여쓰기 — [ref] 유형 폭에 맞춘 고정값


def _detail_lines(detail: dict) -> list[str]:
    """§3.4 추가정보 6줄. 빈 조각은 구분자까지 함께 생략한다(§4)."""
    lines = [
        f"기준     {BASIS_LABEL[detail['basis']]}",
        f"방법     {METHOD_LABEL[detail['method']]}",
        f"표본     과거 {detail['n']}{UNIT_LABEL[detail['unit']]}",
        f"차이     평균 {detail['estimate'] * 100:+.2f}%p",
        f"유의확률   p={detail['p']:.4f}"
        + (f" · 가설 {detail['k']}건 보정" if detail.get("k", 1) > 1 else ""),
    ]
    if detail.get("band"):
        lines.append(f"위치     {BAND_LABEL[detail['band']]}")
    return lines


def render_row(row: EvidenceRow) -> str:
    """근거 행 하나 → 카드 텍스트. 같은 행이면 같은 바이트."""
    label = EVIDENCE_TYPE_LABEL.get(row.type)
    if label is None:
        raise EvidenceFormatError(f"type={row.type!r} 는 렌더할 수 없다")
    lines = [f"[{row.ref}] {label}   {row.content}"]
    if row.type == "STAT_TEST":
        lines.append(_PAD + row.source)         # 출처 = series · 이음(§3.4)
        detail = _detail_lines(row.detail)
        for i, line in enumerate(detail):
            branch = "└" if i == len(detail) - 1 else "├"
            lines.append(f"{_PAD}{branch} {line}")
    else:
        lines.append(f"{_PAD}{row.source} / {row.time}")    # 출처 / 시각(§2)
    return "\n".join(lines)


def render_rows(rows: tuple[EvidenceRow, ...] | list[EvidenceRow]) -> str:
    """근거 창 전체 — §1 정렬(유형 순서 → ref)을 다시 강제해 입력 순서 의존을 없앤다."""
    return "\n".join(render_row(row)
                     for row in sorted(rows, key=EvidenceRow.sort_key))


def render_stat_test(record: StatTestRecord) -> str:
    """§3.1 레코드 → 카드 텍스트. `to_row` 를 거쳐 행 렌더와 한 경로다."""
    return render_row(record.to_row())


__all__ = [
    "BAND_LABEL", "BASIS_LABEL", "CHANNEL_LABEL", "EVIDENCE_TYPE_LABEL",
    "METHOD_LABEL", "UNIT_LABEL", "render_row", "render_rows", "render_stat_test",
]
