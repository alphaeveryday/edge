"""DART 값 승격(ALPHA-547) — 조립이 파싱한 금액을 공시 정규화 사실과 대조해 출처를 승격한다.

경계: ``event_measure`` 의 INSERT writer 는 조립(assemble)뿐이고, 이 모듈은
``value_source``·``dart_rcept_no`` 두 컬럼만 UPDATE 한다(ALPHA-538 과 같은 컬럼 소유 분리).

매칭 규약(v4 DART 레인): 공시 제출인과 같은 발행회사(공급계약 공시의 제출인은 **공급사**이므로
사건의 ``SUPPLIER`` 참여자를 ``equity_profile`` 로 issuer actor 에 접지) · 상대오차
``|value − dart| / dart < 0.08`` · 공시 available_at 이 사건 available_at ±7일(KST 달력일).
승격은 **공급사 후보가 유일**하고 **후보 공시가 정확히 1건**일 때만 한다 — 둘 중 하나라도
여럿이면 금액을 어느 제출인에 귀속할지 단정할 수 없으므로 PARSED 를 보존한 채 카운터로
드러낸다(Rule 12 — 조용한 오귀속보다 미승격이 싸다).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

TOLERANCE = 0.08
WINDOW_DAYS = 7
_KST = timezone(timedelta(hours=9))


def _kst_day(value: datetime):
    """KST 달력일. 세션 TZ(RDS 기본 UTC)로 렌더된 시각을 그대로 ``.date()`` 하면 KST 오전이
    전일로 밀려 ±7일 경계가 하루 어긋난다 — 사건은 밀리고 공시는 안 밀리는 짝에서 정확히
    7일인데 8일로 읽혀 승격을 잃는다(Codex #265 P2). naive 는 이미 KST 로 본다."""
    return (value if value.tzinfo is None else value.astimezone(_KST)).date()


# 사건의 SUPPLIER 참여 instrument 를 equity_profile 로 발행회사 actor 에 접지한 PARSED KRW
# 측정행. 공급사가 여럿이면 행이 곱해진다 — (사건, ord) 로 묶어 issuer 집합을 만든다.
#
# 세 가지 좁히기가 정합성에 필수다(Codex #265 P2):
# 1) group_ord 짝 — 멀티기업 기사는 (주체↔값)을 group_ord 로 묶는다. 조인을 안 좁히면
#    group 0 금액이 group 1 발행회사의 공시로 승격돼 엉뚱한 rcept 번호가 붙는다. **측정행에
#    group 이 있으면 공급사도 같은 group 이어야 한다** — 공급사 쪽 group 이 NULL(기형 LLM
#    출력)이면 모든 그룹의 금액에 붙어 엉뚱한 공급사 공시로 승격된다. 측정행 자체가 무그룹
#    (단일 그룹 기사)일 때만 그 역할 전원을 본다.
# 2) 역할·타입 — 대조 대상이 supply_contract_fact(공급계약 금액)뿐이므로 측정행도 같은 의미로
#    좁힌다. 배당·자기주식 등 다른 KRW 금액이 우연히 ±8% 안에 들면 무관한 공시로 상표가 바뀐다.
# 3) 제출인 측 역할 — supply_contract_fact 는 **제출 회사**(단일판매·공급계약 체결 공시의
#    제출인 = 공급사) 기준이다. 상장 CUSTOMER 의 issuer 를 후보에 넣으면 그 고객사의 무관한
#    공급계약 공시로 승격되거나(남의 rcept) 공급사 공시와 함께 모호로 빠져 승격을 잃는다.
_MEASURE_SQL = (
    "SELECT em.source_event_id, em.measure_ord, em.value, ep.issuer_actor_id, se.available_at"
    " FROM event_measure em"
    " JOIN source_event se ON se.source_event_id = em.source_event_id"
    " JOIN event_argument ea ON ea.source_event_id = em.source_event_id"
    " AND ea.role_code = 'SUPPLIER'"
    " AND (em.group_ord IS NULL OR ea.group_ord = em.group_ord)"
    " JOIN equity_profile ep ON ep.instrument_id = ea.entity_id"
    " WHERE em.value_source = 'PARSED' AND em.unit = 'KRW' AND em.value IS NOT NULL"
    " AND em.role_code = 'CONTRACT_VALUE'"
    " AND se.event_type_code = 'COMPANY.CONTRACT.SIGNING'"
    # 창은 canonical 파티션 일자(published_date=KST)와 같은 축인 se.event_date(DATE)로 잡는다.
    # available_at(TIMESTAMPTZ)을 %s::date 와 비교하면 세션 TZ 로 해석돼(RDS 기본 UTC) KST
    # 오전 기사가 전일로 밀려, 조립은 됐는데 승격 대상에서 조용히 빠진다(Codex #265 P2).
    " AND se.event_date >= %s::date AND se.event_date <= %s::date"
)

# 대조 대상 공급계약 공시 사실 — 창은 사건 창의 ±WINDOW_DAYS 를 SQL 에서 미리 좁힌다.
# 축은 **KST 달력일**로 고정한다: TIMESTAMPTZ 를 %s::date 와 직접 비교하면 세션 TZ 로
# 해석돼(UTC 세션) KST 하한일 오전 9시 이전 공시가 파이썬 판정 전에 잘려나가, 유효한 금액이
# 모호로도 안 잡히고 조용히 PARSED 로 남는다(Codex #265 P2).
_FACT_SQL = (
    "SELECT dd.issuer_actor_id, scf.contract_amount_krw, d.source_document_id, df.available_at"
    " FROM supply_contract_fact scf"
    " JOIN disclosure_fact df ON df.fact_id = scf.fact_id"
    " JOIN disclosure_document dd ON dd.document_id = df.document_id"
    " JOIN document d ON d.document_id = df.document_id"
    " WHERE df.is_current AND scf.contract_amount_krw IS NOT NULL"
    " AND scf.contract_amount_krw > 0"
    f" AND (df.available_at AT TIME ZONE 'Asia/Seoul')::date >= %s::date - {WINDOW_DAYS}"
    f" AND (df.available_at AT TIME ZONE 'Asia/Seoul')::date <= %s::date + {WINDOW_DAYS}"
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
            if abs((_kst_day(available_at) - _kst_day(fact_at)).days) > window_days:
                continue
            candidates[rcept_no] = (issuer, amount)
        # 공급사가 여럿이면(무그룹 측정행 × 다중 공급사, 또는 같은 그룹의 컨소시엄) 금액을
        # 특정 제출인에 귀속할 근거가 없다 — 근처 공시 1건을 가진 쪽으로 승격하면 조작이다.
        if len(candidates) == 1 and len(issuers) == 1:
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
