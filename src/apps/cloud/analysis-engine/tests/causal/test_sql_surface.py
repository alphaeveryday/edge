"""자유 SQL 표면 — **시점·문장 종류·행수를 코드가 정한다. 모델은 못 넓힌다.**

고정하는 불변식:
  · 기반 테이블 직접 접근은 거부되고, 대신 쓸 뷰 이름이 사유에 들어간다
  · 세미콜론·주석·DDL·파일 읽기는 형태 검사에서 죽는다 (SELECT 하나만)
  · 시점 클램프는 **뷰 안에** 있다 - 모델 질의가 우회할 문법이 없다
  · LIKE 의 `%` 는 이스케이프된다 (psycopg2 플레이스홀더 충돌)
  · 실패하면 세이브포인트로 되돌린다 - 한 질의가 트랜잭션을 죽이지 않는다
  · 거부도 문자열로 돌아가고 원장에 남는다 - **실패도 관측이다**

가짜 커서로 검사한다. 실제 DB 는 붙지 않으므로 검사 대상은 "무엇을 보냈는가"다.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from edge_analysis.adapters.sql_surface import MAX_ROWS, SqlSurface
from edge_analysis.config import PipelineError

AS_OF = "2026-07-16T15:40:00+09:00"
TRADE_DATE = date(2026, 7, 16)
OK = "SELECT event_type_code, count(*) FROM v_event GROUP BY 1"


class _Cursor:
    """보낸 sql·params 를 전부 기록하는 가짜 커서. `boom` 이 들어간 질의만 터진다."""

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log
        self.description = [("event_type_code",), ("count",)]

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        self.log.append((sql, params))
        if "boom" in sql:
            raise RuntimeError('relation "boom" does not exist')

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [("BUYBACK", 3)]


class _Conn:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self.log)


def _surface() -> tuple[SqlSurface, _Conn]:
    conn = _Conn()
    return SqlSurface(conn, as_of=AS_OF, trade_date=TRADE_DATE), conn


def _sent(conn: _Conn) -> tuple[str, Any]:
    """본 질의 하나. 세이브포인트·타임아웃은 걷어낸다."""
    return next((s, p) for s, p in conn.log if s.lstrip().startswith("WITH"))


# --------------------------------------------------------------------------- #
# 형태 검사 — 통과 못 하면 관측이 아니라 거부다
# --------------------------------------------------------------------------- #
def test_a_base_table_read_is_refused_and_names_the_view_that_replaces_it():
    """기반 테이블 직접 접근은 **시점 클램프를 우회하는 유일한 경로**다. 사유에 대안 뷰를
    안 적으면 모델은 거부를 "그 자료가 없다"로 읽고 가설을 통째로 갈아탄다.
    """
    s, _ = _surface()

    with pytest.raises(PipelineError, match="price_daily") as e:
        s.query("SELECT * FROM price_daily WHERE instrument_id = 'inst_1'")

    assert "v_daily" in str(e.value), "무엇을 대신 쓰라는지가 빠졌다"


@pytest.mark.parametrize("bad", [
    "SELECT 1 FROM v_event; SELECT 2 FROM v_event",   # 두 문장 - 뒤 문장이 검사를 안 받는다
    "SELECT 1 FROM v_event -- 뒤를 주석으로 지운다",
    "SELECT 1 FROM v_event /* 블록 주석 */",
    "DROP TABLE source_event",
    "SELECT pg_read_file('/etc/passwd')",
    "WITH x AS (SELECT 1) SELECT * FROM x",           # SELECT 로 시작하지 않는다
    "   ",
])
def test_a_statement_that_is_not_a_bare_select_is_refused(bad):
    """검사는 문자열 하나에 걸린다 - 이어 붙이기·주석으로 뒤를 가리기·읽기 아닌 문장이
    통과하면 그 뒤의 모든 보증(시점·행수·읽기 전용)이 함께 무너진다.
    """
    s, conn = _surface()

    with pytest.raises(PipelineError):
        s.query(bad)

    assert conn.log == [], "거부된 질의가 커서까지 갔다"


# --------------------------------------------------------------------------- #
# 시점 — 클램프는 뷰 안에 있다
# --------------------------------------------------------------------------- #
def test_the_point_in_time_clamp_lives_in_the_view_and_travels_as_a_parameter():
    """모델 질의에 PIT 절을 요구하면 빼먹은 한 번이 미래를 본 근거가 된다. 클램프를 뷰에
    박고 시점을 파라미터로 넘기면 모델이 우회할 문법 자체가 없다.
    """
    s, conn = _surface()

    s.query(OK)
    sql, params = _sent(conn)

    assert "available_at <= %(as_of)s" in sql
    assert params == {"as_of": AS_OF, "trade_date": TRADE_DATE}
    assert f"{OK}\n) _q LIMIT" in sql, "모델 질의를 감싸지 않으면 행수 상한이 안 걸린다"


def test_the_row_cap_cannot_be_raised_by_the_caller():
    """행수 상한은 폭주 방어다. 호출자가 늘릴 수 있으면 한 질의가 컨텍스트를 다 먹는다."""
    s, conn = _surface()

    s.query(OK, limit=MAX_ROWS * 10)

    assert f"LIMIT {MAX_ROWS}" in _sent(conn)[0]


def test_a_like_pattern_survives_because_the_percent_is_escaped():
    """`as_of` 가 항상 파라미터로 붙으므로 보간이 언제나 일어난다 - `%` 를 그대로 두면
    psycopg2 가 자기 플레이스홀더로 읽고 질의가 IndexError 로 죽는다.
    """
    s, conn = _surface()

    s.query("SELECT title FROM v_event WHERE title LIKE '%자사주%'")

    assert "LIKE '%%자사주%%'" in _sent(conn)[0]


# --------------------------------------------------------------------------- #
# 실패 — 되먹임으로 돌리되 트랜잭션은 살린다
# --------------------------------------------------------------------------- #
def test_a_failed_query_rolls_back_to_the_savepoint():
    """실패한 질의가 트랜잭션을 오염시키면 그 뒤 모든 조회가 죽는다 - 되먹임 루프가 한 번의
    오타로 끝난다. 되돌린 뒤에 예외를 올려야 다음 질의가 산다.
    """
    s, conn = _surface()

    with pytest.raises(RuntimeError):
        s.query("SELECT * FROM boom")

    assert any("ROLLBACK TO SAVEPOINT" in sql for sql, _ in conn.log)
    assert conn.log[0][0] == "SAVEPOINT causal_sql"


def test_ask_returns_the_refusal_as_a_string_because_a_failure_is_an_observation():
    """`ask` 는 모델에게 돌려줄 문자열을 만든다. 여기서 예외가 새면 셀 전체가 죽고, 모델은
    질의를 고쳐 쓸 기회를 못 얻는다 - 거부는 대화의 일부다.
    """
    s, _ = _surface()

    assert s.ask("DELETE FROM source_event").startswith("거부:")
    assert s.ask("SELECT * FROM boom").startswith("오류:")
    assert "count" in s.ask(OK), "성공 응답은 헤더와 함께 표로 나가야 한다"


def test_the_ledger_keeps_the_successes_and_the_execution_failures_alike():
    """보고된 하나가 아니라 시도 전부가 남아야 무엇을 물어봤는지 재구성된다. 실패만 빠지면
    "안 물어봤다"와 "물어봤는데 안 됐다"가 같은 모양이 된다.
    """
    s, _ = _surface()

    s.query(OK)
    with pytest.raises(RuntimeError):
        s.query("SELECT * FROM boom")

    assert [c["rows"] for c in s.ledger.calls] == [1, 0]
    assert not s.ledger.calls[0]["error"] and "boom" in s.ledger.calls[1]["error"]
    assert s.ledger.queries == [OK, "SELECT * FROM boom"]
