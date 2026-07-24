"""dart_values 매칭 규약 테스트(ALPHA-547) — 승격은 (동일 발행회사 ∧ |Δ|/dart<0.08 ∧ ±7일
∧ 후보 정확히 1건) 전부를 요구한다. 임계·모호성 규약이 흔들리면 value_source 등급이
오염되므로(잘못된 DART 승격 = 골드 대조 오염), 각 축의 경계에서 의도를 고정한다."""
from datetime import datetime

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


def test_wrapper_groups_participant_fanout_and_updates_once():
    """참여자 2명 조인 곱으로 같은 측정행이 2행으로 와도 (사건, ord) 로 접혀 UPDATE 는
    1건만 나간다 — 곱셈이 새면 승격이 중복 실행된다. UPDATE 는 PARSED 행만 만진다."""

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
        def __init__(self):
            self.measures = [("evt1", 0, 100_000.0, "actor_A", _AT),
                             ("evt1", 0, 100_000.0, "actor_B", _AT)]
            self.facts = [_f(100_000)]
            self.updates: list = []

        def cursor(self):
            return _Cur(self)

    conn = _Conn()
    assert match_dart_values(conn, "2026-07-15", "2026-07-15") == (1, 0)
    [(sql, rows)] = conn.updates
    assert rows == [("R1", "evt1", 0)]
    assert "value_source = 'PARSED'" in sql
