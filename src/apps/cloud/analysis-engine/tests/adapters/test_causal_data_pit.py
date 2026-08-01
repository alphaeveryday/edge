"""타입 사전의 PIT — '과거'가 미래를 보면 산술 게이트가 선견으로 판정한다.

에이전트 층 감사 5라운드의 수술 검증. 종전 prior/type_population 은 날짜
클램프가 없어 셀 이후 사건의 수익률이 '타입 과거 최대'에 섞였다 - 오늘
죽었어야 할 후보가 다음 달 극단값 덕에 살아남고, 같은 셀 재실행이 다른
게이트 판정을 낸다(레지스트리의 재현 원칙 위반). 계약: 두 함수 모두
`event_date < trade_date` 와 `available_at <= as_of` 를 SQL 에 박고,
시점 인자는 필수다(선택으로 두면 잊는다 - cohort 전례).
"""
from datetime import date

import pytest

from edge_analysis.adapters.causal_data import CausalData


class _Cursor:
    def __init__(self, sink):
        self._sink = sink

    def execute(self, sql, params=None):
        if "SAVEPOINT" in sql or "RELEASE" in sql or "ROLLBACK" in sql:
            return
        self._sink.append((sql, list(params or ())))

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self):
        self.captured: list[tuple[str, list]] = []

    def cursor(self):
        return _Cursor(self.captured)


def test_prior_clamps_to_cell_and_orders_params():
    conn = _Conn()
    CausalData(conn).prior("COMPANY.EARNINGS.RESULT",
                           as_of="2026-06-01T15:30:00+09:00",
                           trade_date=date(2026, 6, 1), need=0.02)
    sql, params = conn.captured[0]
    assert "se.event_date < %s" in sql and "se.available_at <= %s" in sql
    assert "2026-06-01" in params and "2026-06-01T15:30:00+09:00" in params
    # 클램프 파라미터가 need 보다 앞이다 - 순서가 밀리면 임계가 날짜로 바인딩된다.
    assert params.index("2026-06-01") < params.index(0.02)


def test_type_population_clamps_the_same_way():
    conn = _Conn()
    CausalData(conn).type_population("COMPANY.EARNINGS.RESULT",
                                     as_of="2026-06-01T15:30:00+09:00",
                                     trade_date=date(2026, 6, 1))
    sql, params = conn.captured[0]
    assert "se.event_date < %s" in sql and "se.available_at <= %s" in sql


def test_pit_arguments_are_mandatory():
    with pytest.raises(TypeError):
        CausalData(_Conn()).prior("COMPANY.EARNINGS.RESULT")   # 시점 없이는 사전 없음
