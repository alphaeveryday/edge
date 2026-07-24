"""DART 값 승격(ALPHA-547) — 조립이 파싱한 금액을 공시 정규화 사실과 대조해 출처를 승격한다.

경계: ``event_measure`` 의 INSERT writer 는 조립(assemble)뿐이고, 이 모듈은
``value_source``·``dart_rcept_no`` 두 컬럼만 UPDATE 한다(ALPHA-538 과 같은 컬럼 소유 분리).

매칭 규약(v4 DART 레인): 같은 발행회사(``equity_profile`` 브리지 — 사건 참여 instrument →
issuer actor) · 상대오차 ``|value − dart| / dart < 0.08`` · 공시 ``available_at`` 이 사건
available_at ±7일. 후보 공시가 **정확히 1건**일 때만 승격하고, 모호(2건 이상)하면 PARSED 를
보존한 채 카운터로 드러낸다(Rule 12 — 조용한 오귀속보다 미승격이 싸다).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TOLERANCE = 0.08
WINDOW_DAYS = 7

# 사건 참여 instrument 를 equity_profile 로 발행회사 actor 에 접지한 PARSED KRW 측정행.
# 참여자 여러 명이면 행이 곱해진다 — 파이썬에서 (사건, ord) 로 묶어 issuer 집합을 만든다.
_MEASURE_SQL = (
    "SELECT em.source_event_id, em.measure_ord, em.value, ep.issuer_actor_id, se.available_at"
    " FROM event_measure em"
    " JOIN source_event se ON se.source_event_id = em.source_event_id"
    " JOIN event_argument ea ON ea.source_event_id = em.source_event_id"
    " JOIN equity_profile ep ON ep.instrument_id = ea.entity_id"
    " WHERE em.value_source = 'PARSED' AND em.unit = 'KRW' AND em.value IS NOT NULL"
    " AND se.available_at >= %s::date"
    " AND se.available_at < %s::date + interval '1 day'"
)

# 대조 대상 공급계약 공시 사실 — 창은 사건 창의 ±WINDOW_DAYS 를 SQL 에서 미리 좁힌다.
_FACT_SQL = (
    "SELECT dd.issuer_actor_id, scf.contract_amount_krw, d.source_document_id, df.available_at"
    " FROM supply_contract_fact scf"
    " JOIN disclosure_fact df ON df.fact_id = scf.fact_id"
    " JOIN disclosure_document dd ON dd.document_id = df.document_id"
    " JOIN document d ON d.document_id = df.document_id"
    " WHERE scf.contract_amount_krw IS NOT NULL AND scf.contract_amount_krw > 0"
    " AND df.available_at >= %s::date - interval '7 days'"
    " AND df.available_at < %s::date + interval '8 days'"
)

_UPDATE_SQL = (
    "UPDATE event_measure SET value_source = 'DART', dart_rcept_no = %s"
    " WHERE source_event_id = %s AND measure_ord = %s AND value_source = 'PARSED'"
)


def match_candidates(measures, facts, *, tolerance: float = TOLERANCE,
                     window_days: int = WINDOW_DAYS):
    """측정행별 승격 판정 — (UPDATE 파라미터 목록, 모호 건수). 순수 함수(테스트 표면).

    measures: (source_event_id, measure_ord, value, issuer_actor_ids: set, available_at)
    facts:    (issuer_actor_id, contract_amount_krw, rcept_no, available_at)
    """
    updates: list[tuple[str, str, int]] = []
    ambiguous = 0
    for source_event_id, measure_ord, value, issuers, available_at in measures:
        candidates: dict[str, tuple] = {}
        for issuer, amount, rcept_no, fact_at in facts:
            if issuer not in issuers:
                continue
            if abs(float(value) - float(amount)) / float(amount) >= tolerance:
                continue
            if abs((available_at.date() - fact_at.date()).days) > window_days:
                continue
            candidates[rcept_no] = (issuer, amount)
        if len(candidates) == 1:
            updates.append((next(iter(candidates)), source_event_id, measure_ord))
        elif candidates:
            ambiguous += 1
    return updates, ambiguous


def match_dart_values(conn, from_date: str, to_date: str) -> tuple[int, int]:
    """창 안 PARSED KRW 측정행을 공시와 대조해 승격 UPDATE — (승격 수, 모호 수)."""
    with conn.cursor() as cur:
        cur.execute(_MEASURE_SQL, (from_date, to_date))
        measure_rows = cur.fetchall()
        cur.execute(_FACT_SQL, (from_date, to_date))
        facts = cur.fetchall()

    grouped: dict[tuple, list] = {}
    for source_event_id, measure_ord, value, issuer, available_at in measure_rows:
        entry = grouped.setdefault((source_event_id, measure_ord),
                                   [source_event_id, measure_ord, value, set(), available_at])
        entry[3].add(issuer)

    updates, ambiguous = match_candidates([tuple(v) for v in grouped.values()], facts)
    if updates:
        with conn.cursor() as cur:
            cur.executemany(_UPDATE_SQL, updates)
    if ambiguous:
        logger.warning("DART 매칭 모호 %d건 — PARSED 보존(오귀속 방지)", ambiguous)
    return len(updates), ambiguous
