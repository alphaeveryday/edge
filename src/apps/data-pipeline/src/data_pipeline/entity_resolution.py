"""엔티티 해소 — assertion argument 텍스트 → instrument entity_id (ALPHA-375).

tag-news 의 argument `text` 는 기사 원문 표현("삼성전자"·"005930")이고,
`assertion_argument.entity_id` 는 NOT NULL + FK 라 해소 없이는 한 건도 못 넣는다.

**규칙은 코드가 답한다(Rule 5) — LLM 재호출 금지.** 완전일치 축 3개:
  (a) 티커(6자리, `instrument.ticker`)      → instrument_id
  (b) 회사 정식명(발행사 entity display_name) → 그 회사 보통주 instrument_id
  (c) 종목/ETF display_name                  → instrument_id

**해소 결과는 항상 instrument 엔티티다** — 다운스트림 `event_argument ⋈ instrument`
조인과 분석엔진 entity_index(ticker→instrument_id) 관례에 맞춘다. 회사명이 와도
회사(actor)가 아니라 그 발행사의 주식으로 해소한다.

동명 충돌(한 키가 서로 다른 엔티티 2개)은 **미해소(ambiguous)** 다 — 아무거나 고르면
그 순간 조용히 틀린다. 별칭·유사도 매칭은 완전일치 해소율 실측 후 별건(티켓 범위).
"""

from __future__ import annotations

from dataclasses import dataclass

# 충돌 표식 — dict 값이 None 이면 "그 키는 두 엔티티가 다퉜다"는 뜻이다.
_AMBIGUOUS = None

RESOLVED = "resolved"
UNRESOLVED = "unresolved"
AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ResolutionIndex:
    """정규화 키 → instrument entity_id (None = 동명 충돌)."""

    by_key: dict[str, str | None]


def _normalize(text: str) -> str:
    """완전일치 전 최소 정규화 — 앞뒤·내부 연속 공백만 접는다. 그 이상(법인 접미사
    제거·대소문자 등)은 별칭 축이라 해소율 실측 후 판단한다."""
    return " ".join(text.split())


def load_resolution_index(conn) -> ResolutionIndex:
    """엔티티 마스터 1쿼리로 해소 인덱스를 만든다.

    키 공간: instrument.ticker · 발행사 entity.display_name · instrument entity.display_name.
    같은 키가 서로 다른 instrument_id 로 두 번 오면 충돌 표식으로 바꾼다 — 나중 행이
    조용히 이기게 두면 어느 종목으로 해소됐는지가 적재 순서에 달리게 된다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.instrument_id, i.ticker, ie.display_name, ae.display_name"
            " FROM instrument i"
            " JOIN entity ie ON ie.entity_id = i.instrument_id"
            " LEFT JOIN equity_profile ep ON ep.instrument_id = i.instrument_id"
            " LEFT JOIN entity ae ON ae.entity_id = ep.issuer_actor_id"
        )
        rows = cur.fetchall()

    by_key: dict[str, str | None] = {}
    for instrument_id, ticker, instrument_name, issuer_name in rows:
        for raw in (ticker, instrument_name, issuer_name):
            if not raw:
                continue
            key = _normalize(str(raw))
            if not key:
                continue
            if key not in by_key:
                by_key[key] = str(instrument_id)
            elif by_key[key] != str(instrument_id):
                by_key[key] = _AMBIGUOUS
    return ResolutionIndex(by_key=by_key)


def resolve(index: ResolutionIndex, text: object) -> tuple[str | None, str]:
    """argument 텍스트 1건 해소 — (entity_id | None, 사유).

    사유는 호출부(로더)가 quality log 에 분포로 남긴다(Rule 12 — 미해소는 침묵하지
    않는다). 비문자열·공백뿐 텍스트는 unresolved 다.
    """
    if not isinstance(text, str):
        return None, UNRESOLVED
    key = _normalize(text)
    if not key or key not in index.by_key:
        return None, UNRESOLVED
    entity_id = index.by_key[key]
    if entity_id is _AMBIGUOUS:
        return None, AMBIGUOUS
    return entity_id, RESOLVED
