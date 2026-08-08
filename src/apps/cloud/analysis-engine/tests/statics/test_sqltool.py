"""sqltool 계약 검증 — 반례가 실제로 거부되는가 (Rule 9).

각 테스트가 지키는 사업 규칙:
  · 쓰기·다중문이 새면 읽기 전용 계약이 무너진다 (표기 우회 포함)
  · allowlist 밖 참조가 새면 클램프 뷰의 PIT 보장이 우회된다
  · 절단이 침묵하면 잘린 표본이 전체로 읽힌다
  · trace 에 안 남으면 질의 감사가 사라진다
"""
from __future__ import annotations

import duckdb
import pytest

from edge_analysis.observability import collect_trace
from edge_analysis.statics import sqltool


class FakeLake:
    """CausalLake 의 sqltool 이 만지는 면만: con · bound."""

    def __init__(self) -> None:
        self.con = duckdb.connect()
        self.con.execute(
            "CREATE TABLE _price AS SELECT i AS n, 'tk' || (i % 7) AS ticker, "
            "repeat('x', 50) AS pad FROM range(300) t(i)")
        self.con.execute("CREATE VIEW v_price_daily AS SELECT * FROM _price")
        self.con.execute(
            "CREATE VIEW v_instrument AS SELECT 'tk1' AS ticker, '반도체' AS sector")
        self.con.execute("CREATE VIEW bars_5m AS SELECT 1 AS n")
        self.con.execute("CREATE TABLE secret AS SELECT 42 AS leak")
        # bind_day 산출과 같은 모양: 표 → 클램프 열 (None = 시점 불변 차원)
        self.bound = {"price_daily": "available_at", "instrument": None}


@pytest.fixture()
def lake() -> FakeLake:
    return FakeLake()


# ── 정상 왕복 ─────────────────────────────────────────────────────────


def test_select_roundtrip(lake):
    out = sqltool.run(lake, "SELECT count(*) AS c FROM v_price_daily")
    assert out["ok"] and out["columns"] == ["c"] and out["rows"] == [[300]]
    assert out["truncated"] == "" and out["pit_unbound"] == []


def test_with_cte_and_case_insensitive(lake):
    out = sqltool.run(
        lake, "WITH mine AS (SELECT n FROM V_PRICE_DAILY WHERE n < 5)\n"
              "SELECT count(*) AS c FROM mine")
    assert out["ok"] and out["rows"] == [[5]]


# ── 쓰기·다중문·우회 표기 거부 ───────────────────────────────────────


@pytest.mark.parametrize("bad", [
    "SELECT 1; DROP TABLE _price",          # 다중문에 실린 DDL
    "/* 주석 */ DELETE FROM v_price_daily",  # 주석 뒤 DML
    "  \n-- x\nUPDATE _price SET n = 0",     # 선행 잡음 뒤 DML
    "InSeRt INTO _price VALUES (1,'a','b')",  # 대소문자 우회
    "PRAGMA database_list",
    "ATTACH ':memory:' AS other",
    "COPY _price TO 'out.csv'",
    "",                                      # 빈 질의
])
def test_non_select_rejected(lake, bad):
    out = sqltool.run(lake, bad)
    assert out["ok"] is False and out["reason"]
    # 거부가 말뿐이면 안 된다 — 원본 표가 살아 있고 값이 안 바뀌었어야 한다.
    assert lake.con.execute("SELECT count(*) FROM _price").fetchone()[0] == 300
    assert lake.con.execute("SELECT min(n) FROM _price").fetchone()[0] == 0


# ── allowlist ────────────────────────────────────────────────────────


def test_outside_allowlist_rejected(lake):
    out = sqltool.run(lake, "SELECT * FROM secret")
    assert out["ok"] is False and "secret" in out["reason"]


def test_outside_allowlist_inside_subquery_rejected(lake):
    """문자열 grep 이 아니라 파스 트리 검사임을 서브쿼리가 증명한다."""
    out = sqltool.run(
        lake, "SELECT * FROM v_price_daily WHERE n IN (SELECT leak FROM secret)")
    assert out["ok"] is False and "secret" in out["reason"]


def test_table_function_rejected(lake):
    out = sqltool.run(lake, "SELECT * FROM read_parquet('s3://x/y.parquet')")
    assert out["ok"] is False and "read_parquet" in out["reason"]


def test_cte_name_is_not_a_table_reference(lake):
    out = sqltool.run(
        lake, "WITH secret_like AS (SELECT 1 AS a) SELECT * FROM secret_like")
    assert out["ok"] is True


# ── 상한 절단은 명시된다 ─────────────────────────────────────────────


def test_row_cap_truncation_is_explicit(lake):
    out = sqltool.run(lake, "SELECT n FROM v_price_daily", row_cap=10)
    assert out["ok"] and out["row_count"] == 10
    assert "10" in out["truncated"]         # 조용한 절단 금지


def test_byte_cap_truncation_is_explicit(lake):
    out = sqltool.run(lake, "SELECT pad FROM v_price_daily", byte_cap=300)
    assert out["ok"] and out["row_count"] < 300
    assert "바이트" in out["truncated"]


def test_under_cap_is_not_marked(lake):
    out = sqltool.run(lake, "SELECT n FROM v_price_daily WHERE n < 3")
    assert out["ok"] and out["truncated"] == ""


# ── PIT 비보장 플래그 ────────────────────────────────────────────────


def test_unclamped_dimension_flagged(lake):
    out = sqltool.run(
        lake, "SELECT p.n FROM v_price_daily p JOIN v_instrument i "
              "ON i.ticker = p.ticker")
    assert out["ok"] and out["pit_unbound"] == ["v_instrument"]


def test_whitelist_view_flagged(lake):
    out = sqltool.run(lake, "SELECT * FROM bars_5m")
    assert out["ok"] and out["pit_unbound"] == ["bars_5m"]


# ── 오류 표면과 감사 trace ───────────────────────────────────────────


def test_execution_error_is_deterministic_surface(lake):
    out = sqltool.run(lake, "SELECT no_such_col FROM v_price_daily")
    assert out["ok"] is False and out["reason"].startswith("실행 실패")


def test_trace_records_query_and_rejection(lake):
    with collect_trace() as tr:
        sqltool.run(lake, "SELECT count(*) FROM v_price_daily")
        sqltool.run(lake, "DROP TABLE _price")
    events = {e["event"]: e for e in tr}
    assert "v_price_daily" in events["sqltool.query"]["sql"]
    assert events["sqltool.query"]["rows"] == 1
    assert events["sqltool.query"]["truncated"] is False
    assert "DROP TABLE" in events["sqltool.rejected"]["sql"]


# ── LLM 툴 스키마 어댑터 ─────────────────────────────────────────────


def test_tool_spec_shape_and_callable(lake):
    spec = sqltool.tool_spec(lake)
    assert spec["name"] == "sql"
    assert spec["input_schema"]["required"] == ["sql"]
    assert "v_price_daily" in spec["description"]      # 허용 관계가 설명에 실린다
    assert "v_instrument" in spec["description"]
    out = spec["call"]("SELECT count(*) AS c FROM v_price_daily")
    assert out["ok"] and out["rows"] == [[300]]
    assert spec["call"]("DELETE FROM v_price_daily")["ok"] is False
