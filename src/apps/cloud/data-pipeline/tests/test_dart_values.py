"""dart_values 매칭 규약 테스트(ALPHA-547) — 승격은 (동일 발행회사 ∧ |Δ|/dart<0.08 ∧ ±7일
∧ 후보 정확히 1건) 전부를 요구한다. 임계·모호성 규약이 흔들리면 value_source 등급이
오염되므로(잘못된 DART 승격 = 골드 대조 오염), 각 축의 경계에서 의도를 고정한다."""
from datetime import datetime

from data_pipeline.steps import dart_values
from data_pipeline.steps.dart_values import match_candidates, match_dart_values

D = datetime.fromisoformat
_AT = D("2026-07-15T09:00:00+09:00")


def _m(value, issuers=("actor_A",), at=_AT, sid="evt1", ord_=0):
    return (sid, ord_, value, set(issuers), at)


def _f(amount, issuer="actor_A", rcept="R1", at=D("2026-07-14T16:00:00+09:00")):
    return (issuer, amount, rcept, at)


def test_tolerance_boundary_is_strict_under_8pct():
    """7.99% 오차는 승격, 정확히 8.00%는 미승격 — 계약의 '<0.08' 경계가 '<='로 물러지면
    공시와 다른 금액이 DART 출처로 둔갑한다."""
    ok, _ = match_candidates([_m(107_990.0)], [_f(100_000)])
    at_limit, _ = match_candidates([_m(108_000.0)], [_f(100_000)])
    assert [u[0] for u in ok] == ["R1"]
    assert at_limit == []


def test_two_candidates_keep_parsed_and_count_ambiguous():
    """톨러런스 안 공시가 2건이면 어느 쪽인지 단정할 수 없다 — 승격하지 않고 PARSED 를
    보존하며 모호 카운터로 드러낸다(Rule 12: 조용한 오귀속 금지)."""
    updates, ambiguous = match_candidates(
        [_m(100_000.0)], [_f(100_000, rcept="R1"), _f(99_000, rcept="R2")])
    assert updates == []
    assert ambiguous == 1


def test_issuer_and_window_gates():
    """발행회사 불일치·±7일 창 밖 공시는 금액이 같아도 남의 계약이다 — 승격 금지.
    7일 차는 창 안이다(경계 포함)."""
    other_issuer, _ = match_candidates([_m(100_000.0)], [_f(100_000, issuer="actor_B")])
    far, _ = match_candidates(
        [_m(100_000.0, at=D("2026-07-22T09:00:00+09:00"))],
        [_f(100_000, at=D("2026-07-14T09:00:00+09:00"))])  # 8일 차
    near, _ = match_candidates(
        [_m(100_000.0, at=D("2026-07-21T09:00:00+09:00"))],
        [_f(100_000, at=D("2026-07-14T09:00:00+09:00"))])  # 7일 차
    assert other_issuer == []
    assert far == []
    assert len(near) == 1


def test_wrapper_folds_supplier_fanout_before_judging():
    """공급사 2명 조인 곱으로 같은 측정행이 2행으로 와도 (사건, ord) 로 **먼저 접힌다** —
    곱셈이 새면 같은 측정행이 두 번 판정돼 모호 카운터가 2로 부풀거나 승격이 중복 실행된다.
    접은 뒤 규약을 적용하므로 공급사 2명은 모호 1건이고, 공급사 1명은 UPDATE 1건이다
    (UPDATE 는 PARSED 행만 만진다)."""

    class _Cur:
        def __init__(self, conn):
            self._conn = conn
            self._rows: list = []

        def execute(self, sql, params=None):
            self._rows = (self._conn.measures if "FROM event_measure" in sql
                          else self._conn.facts)

        def executemany(self, sql, rows):
            self._conn.updates.append((sql, list(rows)))

        def fetchall(self):
            return self._rows

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        def __init__(self, measures):
            self.measures = measures
            self.facts = [_f(100_000)]
            self.updates: list = []

        def cursor(self):
            return _Cur(self)

    # 공급사 2명 → 접혀서 모호 1건(2건이 아니다), UPDATE 없음.
    fanout = _Conn([("evt1", 0, 100_000.0, "actor_A", _AT),
                    ("evt1", 0, 100_000.0, "actor_B", _AT)])
    assert match_dart_values(fanout, "2026-07-15", "2026-07-15") == (0, 1)
    assert fanout.updates == []

    # 공급사 1명 → 승격 1건, UPDATE 는 PARSED 행만.
    single = _Conn([("evt1", 0, 100_000.0, "actor_A", _AT)])
    assert match_dart_values(single, "2026-07-15", "2026-07-15") == (1, 0)
    [(sql, rows)] = single.updates
    assert rows == [("R1", "evt1", 0)]
    assert "value_source = 'PARSED'" in sql


def test_measure_sql_binds_group_supplier_contract_and_day_scope():
    """SQL 계약 — 순수 함수가 못 지키는 네 좁히기를 쿼리에서 고정한다(Codex #265 P2).

    (1) group_ord 짝: 멀티기업 기사는 (주체↔값)을 group_ord 로 묶는다. 측정행에 group 이 있으면
        공급사도 같은 group 이어야 한다 — 공급사 group 이 NULL 이어도 통과시키면 기형 출력 하나가
        모든 그룹 금액에 붙어 엉뚱한 rcept 번호가 lineage 에 남는다.
    (2) 역할·타입: 대조 대상이 supply_contract_fact 뿐이라, 배당·자기주식 같은 다른 KRW
        금액이 우연히 ±8% 안에 들면 무관한 공시로 출처가 둔갑한다.
    (3) 제출인 측 역할: 공급계약 공시의 제출인은 공급사다. 상장 CUSTOMER 의 issuer 를 후보에
        넣으면 고객사의 무관한 공시로 승격되거나 공급사 공시와 함께 모호로 빠져 승격을 잃는다.
    (4) 창 축: canonical 파티션(published_date=KST)과 같은 축인 event_date 로 잡아야 한다 —
        available_at::date 는 세션 TZ(RDS 기본 UTC)로 해석돼 KST 오전 기사가 조용히 빠진다.
    넷 다 실 DB 없이는 값으로 검증할 수 없어 쿼리 계약을 직접 고정한다(기존 threading 의
    novelty 필터 테스트와 같은 방식)."""
    sql = dart_values._MEASURE_SQL
    assert "(em.group_ord IS NULL OR ea.group_ord = em.group_ord)" in sql
    assert "ea.group_ord IS NULL" not in sql  # 무그룹 공급사는 그룹 금액에 붙지 않는다
    assert "ea.role_code = 'SUPPLIER'" in sql
    assert "em.role_code = 'CONTRACT_VALUE'" in sql
    assert "se.event_type_code = 'COMPANY.CONTRACT.SIGNING'" in sql
    assert "se.event_date >= %s::date" in sql and "se.available_at >=" not in sql


def test_window_boundary_uses_kst_day_not_session_tz():
    """UTC 세션이 돌려준 시각으로도 ±7일 경계는 KST 달력일로 판정한다 — 사건은 KST 오전
    10시(UTC 01시, 같은 날), 공시는 KST 08일 01시(UTC 07일 16시, 전일로 밀림)면 UTC 축으로는
    8일 차라 승격을 잃지만 KST 축으로는 정확히 7일이라 승격해야 한다(Codex #265 P2)."""
    event_at = D("2026-07-15T01:00:00+00:00")   # KST 07-15 10:00
    fact_at = D("2026-07-07T16:00:00+00:00")    # KST 07-08 01:00
    updates, ambiguous = match_candidates(
        [_m(100_000.0, at=event_at)], [_f(100_000, at=fact_at)])
    assert [u[0] for u in updates] == ["R1"] and ambiguous == 0

    # 8일 차(KST)는 여전히 배제 — 경계가 물러지면 무관한 공시가 붙는다.
    too_old, _ = match_candidates(
        [_m(100_000.0, at=event_at)], [_f(100_000, at=D("2026-07-06T16:00:00+00:00"))])
    assert too_old == []


def test_fact_sql_prefilters_on_kst_day():
    """공시 프리필터도 KST 달력일 축이어야 한다 — TIMESTAMPTZ 를 %s::date 와 직접 비교하면
    UTC 세션에서 하한일 오전 9시 이전 공시가 파이썬 판정 전에 잘려, 유효한 금액이 모호로도
    안 잡히고 조용히 PARSED 로 남는다(Codex #265 P2)."""
    sql = dart_values._FACT_SQL
    assert "(df.available_at AT TIME ZONE 'Asia/Seoul')::date" in sql
    assert "df.available_at >=" not in sql


def test_multiple_suppliers_keep_parsed_even_with_one_candidate():
    """공급사 후보가 여럿이면(무그룹 측정행 × 다중 공급사, 컨소시엄) 근처 공시가 1건뿐이어도
    승격하지 않는다 — 그 금액이 그 제출인의 계약이라는 근거가 없어, 승격하면 남의 rcept 를
    lineage 로 박는 조작이다. 손실은 모호 카운터로 드러난다(Codex #265 P2, Rule 12)."""
    updates, ambiguous = match_candidates(
        [_m(100_000.0, issuers=("actor_A", "actor_B"))], [_f(100_000, issuer="actor_A")])
    assert updates == [] and ambiguous == 1

    # 공급사가 유일하면 종전대로 승격한다 — 규약이 과하게 조여지면 승격이 전멸한다.
    ok, _ = match_candidates([_m(100_000.0, issuers=("actor_A",))], [_f(100_000)])
    assert [u[0] for u in ok] == ["R1"]
