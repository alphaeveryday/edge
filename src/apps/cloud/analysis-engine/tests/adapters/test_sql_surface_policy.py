"""LLM 조회 표면은 분석 입력과 분석 산출물을 섞지 않는다."""
from __future__ import annotations

from edge_analysis.adapters.sql_surface import auto_views_sql
from edge_analysis.statics.duck import BACKFILL_SETS, CausalLake
from edge_analysis.statics.sqltool import allowed_relations
from edge_analysis.statics.tools import Catalog


OUTPUT_TABLES = {
    "analysis_evidence_bundle",
    "explanation_evidence_row",
    "explanation_result",
    "explanation_run",
    "explanation_run_event_evidence",
    "explanation_run_event_price_observation",
    "hypothesis_trial",
}


def test_analysis_outputs_never_become_automatic_llm_views():
    """WHY: 산출물을 다시 읽으면 자기 응답을 근거로 삼는 순환 증거가 생긴다."""
    cols = {name: ["created_at"] for name in OUTPUT_TABLES}
    cols["source_event"] = ["source_event_id", "available_at"]

    plan = auto_views_sql(
        cols,
        as_of="TIMESTAMP '2026-08-07 23:59:59'",
        trade_date="DATE '2026-08-07'",
        prefix="rdb.public.",
    )

    names = {name for name, _clamp, _ddl in plan}
    assert names == {"source_event"}


def test_sql_allowlist_rejects_outputs_even_if_a_caller_marks_them_bound():
    """WHY: 잘못 구성된 lake.bound 하나가 출력 격리 정책을 우회하면 안 된다."""
    class Lake:
        bound = {"source_event": "available_at", **{
            name: "created_at" for name in OUTPUT_TABLES
        }}

    allowed = allowed_relations(Lake())

    assert "v_source_event" in allowed
    assert not ({f"v_{name}" for name in OUTPUT_TABLES} & set(allowed))


def test_catalog_peek_does_not_disclose_output_schema_or_execute_it():
    """WHY: 뷰가 없어도 컬럼을 알려주면 LLM 표면에는 산출물이 존재하게 된다."""
    class Lake:
        cols = {"explanation_result": ["explanation_text", "confidence_level"]}
        effective = {}

        def sql(self, _query):
            raise AssertionError("금지 관계는 실행 경계에 도달하면 안 된다")

    out = Catalog(Lake(), "305720", "instrument-1", "2026-08-07").peek(
        "v_explanation_result")

    assert "조회할 수 없는 분석 산출물" in out
    assert "explanation_text" not in out


def test_coverage_denominator_excludes_analysis_outputs():
    """WHY: 의도적으로 닫은 산출물을 '미도달 입력'으로 보고하면 운영자가 다시 열게 된다."""
    lake = CausalLake.__new__(CausalLake)
    lake.rows = {"source_event": 10, "explanation_result": 50}
    lake.bound = {"source_event": "available_at"}
    lake.unbound = {}
    lake.day = ""
    lake.effective = {}
    lake.exists = {name: 0 for name in BACKFILL_SETS}
    lake.exists["bars_5m"] = False
    lake.backfill_notes = {}
    lake.s3 = {}
    lake.deferred = {}

    out = lake.coverage()

    assert "바인딩 1/1 = 100%" in out
    assert "explanation_result" not in out
