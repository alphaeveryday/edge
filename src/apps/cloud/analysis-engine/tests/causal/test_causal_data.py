"""인과 데이터 어댑터 테스트 — PIT 강제와 정렬 보존.

이 두 가지가 깨지면 조용히 틀린다. PIT 누락은 결과를 **좋아지게** 만들어 사후 탐지가
안 되고, 정렬 어긋남은 x·y 가 다른 단위를 가리키는데도 길이만 맞으면 통과한다.
그래서 여기서 고정한다.
"""

from datetime import date

import numpy as np
import pytest

from edge_analysis.adapters.causal_data import CausalData
from edge_analysis.config import PipelineError


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        # SAVEPOINT 계열은 기록하지 않는다. 트랜잭션 오염 방지용 제어문이라 검사 대상이
        # 아니고, 기록에 섞이면 모든 테스트가 `executed[-1]` 로 질의를 못 집는다.
        if flat.split(None, 1)[0].upper() in ("SAVEPOINT", "RELEASE", "ROLLBACK"):
            return
        self._conn.executed.append((flat, params))

    def fetchall(self):
        return self._conn.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows=()):
        self.executed: list[tuple[str, object]] = []
        self.rows = list(rows)

    def cursor(self):
        return _FakeCursor(self)


PAIRS = [("I_A", date(2026, 7, 29)), ("I_B", date(2026, 7, 29)), ("I_C", date(2026, 7, 28))]
AS_OF = "2026-07-29T15:30:00"


def test_predicate_rejects_pit_bypass_and_statement_break():
    cd = CausalData(_FakeConn())

    for bad in ("available_at > '2030-01-01'", "1=1; DROP TABLE instrument",
                "1=1 -- comment", "1=1 UNION SELECT 1"):
        with pytest.raises(PipelineError):
            cd.cohort(bad, as_of=AS_OF)


def test_empty_predicate_is_rejected_with_column_help():
    cd = CausalData(_FakeConn())

    with pytest.raises(PipelineError, match="industry_name"):
        cd.cohort("   ", as_of=AS_OF)


def test_as_of_injects_pit_clause_that_the_predicate_cannot_write():
    conn = _FakeConn()

    CausalData(conn).cohort("industry_name = 'Biotechnology'", as_of=AS_OF)

    sql, params = conn.executed[-1]
    assert "c.available_at <= %s" in sql
    assert AS_OF in params


def test_cohort_requires_as_of():
    """PIT 는 선택사항이 아니다 - 선택으로 두면 잊고, 잊으면 미래를 본다."""
    cd = CausalData(_FakeConn())

    with pytest.raises(TypeError):
        cd.cohort("industry_name = 'Biotechnology'")
    with pytest.raises(PipelineError, match="as_of"):
        cd.cohort("industry_name = 'Biotechnology'", as_of="")


def test_aligned_columns_preserve_input_order_and_mark_missing_as_nan():
    """DB 가 다른 순서로 줘도, 일부를 안 줘도 입력 순서가 유지돼야 한다."""
    conn = _FakeConn([("I_C", date(2026, 7, 28), 0.03),
                      ("I_A", date(2026, 7, 29), -0.01)])

    out = CausalData(conn).ar(PAIRS)

    assert out[0] == pytest.approx(-0.01)
    assert np.isnan(out[1])
    assert out[2] == pytest.approx(0.03)
    assert len(out) == len(PAIRS)


def test_empty_pairs_returns_empty_array_without_querying():
    conn = _FakeConn()

    assert len(CausalData(conn).ar([])) == 0
    assert conn.executed == []


def test_window_uses_trading_day_ranks_not_calendar_days():
    """20거래일은 20거래일이다 - 달력일로 자르면 연휴가 표본을 깎는다."""
    conn = _FakeConn()

    CausalData(conn).mom(PAIRS, days=20, lag=1)

    sql, params = conn.executed[-1]
    assert "row_number() OVER (ORDER BY trade_date)" in sql
    assert "c2.rn BETWEEN k.rn - %s AND k.rn - %s" in sql
    assert 20 in params and 1 in params


def test_bad_window_is_rejected():
    cd = CausalData(_FakeConn())

    with pytest.raises(PipelineError):
        cd.mom(PAIRS, days=0)
    with pytest.raises(PipelineError):
        cd.vol(PAIRS, lag=-1)


def test_weight_share_is_normalised_over_the_snapshot():
    conn = _FakeConn([("I_A", 0.30), ("I_B", 0.20), ("I_D", 0.50)])

    w = CausalData(conn).weight("ETF_1", date(2026, 7, 29), ["I_A", "I_B"])

    assert w["share"] == pytest.approx(0.5)
    assert w["n_hold"] == 3


def test_weight_reports_none_share_when_snapshot_is_empty():
    """비중이 없으면 0 으로 대체하지 않는다 - 결측과 0 은 다르다."""
    cd = CausalData(_FakeConn([]))

    assert cd.weight("ETF_1", date(2026, 7, 29), ["I_A"])["share"] is None


def test_required_effect_is_the_free_arithmetic_gate():
    """무게 5.2% 로 잔차 13.36% 를 설명하려면 +257% 가 필요하다 - 통계 없이 죽는다."""
    cd = CausalData(_FakeConn())

    assert cd.required_effect(0.1336, 0.052) == pytest.approx(2.569, abs=1e-3)
    assert cd.required_effect(0.1336, None) is None
    assert cd.required_effect(0.1336, 0.0) is None


def test_universe_excludes_treated_pairs():
    conn = _FakeConn([("I_A", date(2026, 7, 29)), ("I_B", date(2026, 7, 29))])

    out = CausalData(conn).universe("industry_name = 'Biotechnology'",
                                   [date(2026, 7, 29)],
                                   exclude=[("I_A", date(2026, 7, 29))])

    assert out == [("I_B", date(2026, 7, 29))]


@pytest.mark.parametrize("call", [
    lambda cd, p: cd.cohort(p, as_of=AS_OF),
    lambda cd, p: cd.universe(p, [date(2026, 7, 29)]),
])
def test_like_predicate_survives_parameter_binding(call):
    """`LIKE '%...%'` 술어가 psycopg2 보간을 통과해야 한다.

    이게 코호트의 주된 사용법이다 - 이 파일이 검사하는 `_guard` 의 에러 메시지도
    `industry_name LIKE '%Semiconductor%'` 를 예로 든다. 그런데 술어는 f-string 으로
    SQL 에 박히고 그 SQL 은 파라미터와 함께 execute 되므로, `%` 를 두 배로 만들지 않으면
    psycopg2 가 그것을 플레이스홀더로 읽어 `IndexError` 로 죽는다.

    실험은 DuckDB paramstyle 이라 이 경로를 안 밟았고, 가짜 커서는 SQL 을 파싱하지 않아
    잡지 못했다. 클라우드 Postgres 에서 처음 드러났다(ALPHA-622).
    """
    conn = _FakeConn()

    call(CausalData(conn), "industry_name LIKE '%Semiconductor%'")

    sql, params = conn.executed[-1]
    assert "'%%Semiconductor%%'" in sql, "술어의 % 가 이스케이프되지 않았다"
    # psycopg2 의 클라이언트측 바인딩은 `sql % params` 와 같은 규칙을 쓴다. 이스케이프가
    # 빠지면 여기서 IndexError 가 난다 - 운영에서 나는 것과 같은 예외다.
    sql % tuple("x" for _ in params)


def test_universe_casts_the_date_array():
    """날짜 배열은 ``::date[]`` 로 캐스팅해야 한다.

    ISO 문자열 리스트를 넘기면 psycopg2 가 ``text[]`` 로 어댑트하고 Postgres 에는
    ``date = text`` 연산자가 없다. 캐스팅이 빠지면 모든 대조군 질의가 UndefinedFunction 으로
    죽고, 대조군이 없으면 인과 설계가 성립하지 못한다 - 실제로 클라우드에서 그랬다.
    DuckDB(실험)는 이 비교를 암묵 캐스팅으로 통과시켜 드러나지 않았다.
    """
    conn = _FakeConn()

    CausalData(conn).universe("industry_name = 'X'", [date(2026, 7, 29)])

    sql, _ = conn.executed[-1]
    assert "ANY(%s::date[])" in sql
