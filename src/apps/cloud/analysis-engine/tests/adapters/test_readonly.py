"""읽기전용 질의 가드 — 통과시켜선 안 되는 것을 통과시키지 않는지.

가드는 안전의 마지막 층이 아니다(권한과 읽기전용 트랜잭션이 앞에 있다). 하지만 가드가
헐거우면 그 두 층에 의존하고 있다는 사실이 가려지고, 언젠가 권한을 넓히는 GRANT 가
섞였을 때 아무도 못 막는다. 그래서 가드는 가드로서 검사한다.
"""
from __future__ import annotations

import io

import pytest

from edge_analysis.adapters.readonly import emit, guard
from edge_analysis.config import PipelineError


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select 1",
    "  SELECT 1  ",
    "SELECT 1;",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "TABLE instrument",
    "EXPLAIN SELECT 1",
])
def test_read_queries_pass(sql):
    assert guard(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM instrument",
    "UPDATE instrument SET ticker='x'",
    "INSERT INTO instrument VALUES (1)",
    "DROP TABLE instrument",
    "TRUNCATE instrument",
    "GRANT ALL ON instrument TO agent_ro",
    "CREATE TABLE t(x int)",
])
def test_write_verbs_are_refused(sql):
    """쓰기 동사는 서버가 거부하기 전에 가드가 읽히는 메시지로 되돌린다."""
    with pytest.raises(PipelineError, match="read-only path accepts"):
        guard(sql)


def test_comment_prefix_cannot_disguise_a_write():
    """`/* ... */ DELETE` 는 SELECT 로 시작하지 않는다 - 주석을 벗기고 판정해야 한다."""
    with pytest.raises(PipelineError, match="read-only path accepts"):
        guard("/* harmless */ DELETE FROM instrument")
    with pytest.raises(PipelineError, match="read-only path accepts"):
        guard("-- harmless\nDELETE FROM instrument")


def test_statement_stacking_is_refused():
    """두 번째 문장에 쓰기를 숨길 수 있으므로 문장은 하나여야 한다."""
    with pytest.raises(PipelineError, match="multiple statements"):
        guard("SELECT 1; DELETE FROM instrument")


def test_semicolon_inside_a_literal_is_not_a_statement_break():
    """리터럴 안 세미콜론을 문장 구분으로 오해하면 정상 질의가 거부된다."""
    assert guard("SELECT * FROM instrument WHERE ticker = 'a;b'")


def test_empty_query_is_refused():
    with pytest.raises(PipelineError, match="empty query"):
        guard("   ")


def test_emit_writes_one_json_object_per_row():
    """CloudWatch 는 줄 단위로 이벤트를 자른다 - 한 행이 한 줄이어야 조회가 가능하다."""
    out = io.StringIO()
    emit(["a", "b"], [(1, "x"), (2, "y")], stream=out)
    assert out.getvalue().splitlines() == ['{"a": 1, "b": "x"}', '{"a": 2, "b": "y"}']


def test_emit_stringifies_types_json_does_not_know():
    """date·Decimal 을 만나면 죽지 말고 문자열로 떨어뜨린다 - 원장 컬럼 대부분이 그렇다."""
    from datetime import date
    from decimal import Decimal

    out = io.StringIO()
    emit(["d", "n"], [(date(2026, 7, 29), Decimal("1.5"))], stream=out)
    assert out.getvalue().strip() == '{"d": "2026-07-29", "n": "1.5"}'
