"""ObjectSet LLM surface contract.

These tests encode the safety reason for the surface: model output is untrusted data,
so it may select declared operations but can never provide executable query text or
change the analysis clock.
"""
from __future__ import annotations

import json

import duckdb

from edge_analysis.statics.objectset_tools import ObjectSetRuntime


class _Lake:
    def __init__(self) -> None:
        self.con = duckdb.connect()
        self.bound = {"instrument": "available_at", "actor": None,
                      "equity_profile": "profile_as_of_date"}
        self.con.execute("""
            CREATE VIEW v_instrument AS
            SELECT * FROM (VALUES
              ('B', TIMESTAMP '2026-08-07 10:00:00', 'Beta'),
              ('FUTURE', TIMESTAMP '2026-08-08 10:00:00', 'Future 999'),
              ('MID_FUTURE', TIMESTAMP '2026-08-07 15:00:00', 'Same-day future 999'),
              ('A', TIMESTAMP '2026-08-07 10:00:00', 'Alpha')
            ) t(instrument_id, available_at, display_name)
        """)
        self.con.execute("""
            CREATE VIEW v_actor AS
            SELECT * FROM (VALUES ('C', 'COMPANY', 'Alpha Corp'),
                                  ('D', 'COMPANY', 'Future Corp'),
                                  ('G', 'GOVERNMENT', 'Agency'))
              t(actor_id, actor_type, display_name)
        """)
        self.con.execute("""
            CREATE VIEW v_equity_profile AS
            SELECT * FROM (VALUES
              ('A', 'C', DATE '2026-08-07'),
              ('B', 'D', DATE '2026-08-08')
            ) t(instrument_id, issuer_actor_id, profile_as_of_date)
        """)


def _runtime(*, versions=None) -> ObjectSetRuntime:
    return ObjectSetRuntime(
        _Lake(), as_of="2026-08-07T12:00:00", dataset_versions=versions or {})


def test_tool_contract_has_six_structured_operations_and_no_query_text_slot():
    runtime = _runtime()

    specs = [spec for spec in runtime.tool_specs()
             if spec["name"].startswith("objectset.")]

    assert [s["name"] for s in specs] == [
        "objectset.create", "objectset.filter", "objectset.describe",
        "objectset.list_affordances", "objectset.follow", "objectset.inspect",
    ]
    encoded = json.dumps(specs, ensure_ascii=False).lower()
    assert '"sql"' not in encoded
    assert '"query"' not in encoded
    assert '"view_name"' not in encoded
    create = specs[0]["input_schema"]
    assert set(create["properties"]) == {"kind"}
    assert create["properties"]["kind"]["enum"] == ["COMPANY_ENTITY", "ISSUER"]
    assert create["additionalProperties"] is False


def test_create_pins_clock_and_reports_missing_dataset_snapshot_honestly():
    runtime = _runtime()

    out = runtime.call("objectset.create", {"kind": "ISSUER"})

    assert out["ok"] is True
    assert out["as_of"] == "2026-08-07T12:00:00"
    assert out["dataset"] == {"name": "instrument", "version": None}
    assert out["pit"]["clamp"] == "available_at"
    assert "NO_DATASET_VERSION:instrument" in out["pit"]["gaps"]
    assert out["lineage"][0]["operation"] == "create"


def test_filter_is_immutable_and_inspect_blocks_future_rows_even_if_view_is_bad():
    runtime = _runtime(versions={"instrument": "fixture-v1"})
    created = runtime.call("objectset.create", {"kind": "ISSUER"})
    filtered = runtime.call("objectset.filter", {
        "handle": created["handle"], "field": "instrument_id",
        "operator": "ne", "value": "B",
    })

    assert filtered["handle"] != created["handle"]
    assert len(created["lineage"]) == 1
    assert [step["operation"] for step in filtered["lineage"]] == ["create", "filter"]

    inspected = runtime.call("objectset.inspect", {
        "handle": filtered["handle"], "fields": ["instrument_id", "display_name"],
        "limit": 10,
    })
    assert inspected["objects"] == [{"instrument_id": "A", "display_name": "Alpha"}]
    assert inspected["truncated"] is False
    assert "999" not in json.dumps(inspected)


def test_inspect_orders_by_object_identity_before_applying_limit():
    runtime = _runtime(versions={"instrument": "fixture-v1"})
    created = runtime.call("objectset.create", {"kind": "ISSUER"})

    inspected = runtime.call("objectset.inspect", {
        "handle": created["handle"], "fields": ["instrument_id"], "limit": 2})

    assert inspected["objects"] == [{"instrument_id": "A"}, {"instrument_id": "B"}]
    assert inspected["truncated"] is False


def test_model_cannot_change_clock_or_smuggle_executable_fields():
    runtime = _runtime()

    changed_clock = runtime.call("objectset.create", {
        "kind": "ISSUER", "as_of": "2099-01-01"})
    smuggled = runtime.call("objectset.filter", {
        "handle": "anything", "field": "instrument_id", "operator": "eq",
        "value": "A", "sql": "DROP TABLE x"})

    assert changed_clock == {
        "ok": False,
        "error": {"code": "INVALID_ARGUMENTS", "message": "arguments contain unsupported fields"},
    }
    assert smuggled["ok"] is False
    assert smuggled["error"]["code"] == "INVALID_ARGUMENTS"
    assert "DROP" not in json.dumps(smuggled)


def test_describe_affordances_and_follow_are_handle_scoped():
    runtime = _runtime(versions={"instrument": "i1", "actor": "a1",
                                 "equity_profile": "e1"})
    created = runtime.call("objectset.create", {"kind": "ISSUER"})

    described = runtime.call("objectset.describe", {"handle": created["handle"]})
    menu = runtime.call("objectset.list_affordances", {"handle": created["handle"]})
    followed = runtime.call("objectset.follow", {
        "handle": created["handle"], "relation": "ISSUER"})
    inspected = runtime.call("objectset.inspect", {
        "handle": followed["handle"], "fields": ["actor_id"], "limit": 10})

    assert "display_name" in {field["name"] for field in described["fields"]}
    assert menu["relations"] == [{"name": "ISSUER", "target_kind": "COMPANY_ENTITY"}]
    assert followed["kind"] == "COMPANY_ENTITY"
    assert followed["as_of"] == created["as_of"]
    assert [step["operation"] for step in followed["lineage"]][-1] == "follow"
    assert inspected["objects"] == [{"actor_id": "C"}]


def test_invalid_field_and_relation_fail_as_policy_errors_without_internal_details():
    runtime = _runtime()
    created = runtime.call("objectset.create", {"kind": "ISSUER"})

    bad_field = runtime.call("objectset.filter", {
        "handle": created["handle"], "field": "password", "operator": "eq", "value": "x"})
    bad_relation = runtime.call("objectset.follow", {
        "handle": created["handle"], "relation": "secret_table"})

    assert bad_field["error"]["code"] == "FIELD_NOT_ALLOWED"
    assert bad_relation["error"]["code"] == "RELATION_NOT_ALLOWED"
    assert "SELECT" not in json.dumps([bad_field, bad_relation])


def test_filter_values_and_inspection_payloads_are_bounded():
    runtime = _runtime()
    created = runtime.call("objectset.create", {"kind": "ISSUER"})
    oversized_filter = runtime.call("objectset.filter", {
        "handle": created["handle"], "field": "display_name", "operator": "eq",
        "value": "x" * 501})
    assert oversized_filter["error"]["code"] == "INVALID_ARGUMENTS"
    nested_in = runtime.call("objectset.filter", {
        "handle": created["handle"], "field": "instrument_id", "operator": "in",
        "value": [{"not": "a scalar"}]})
    assert nested_in["error"]["code"] == "INVALID_ARGUMENTS"

    con = duckdb.connect()
    con.execute("CREATE VIEW v_instrument AS SELECT 'A' instrument_id, repeat('x', 40000) note")
    large = ObjectSetRuntime(
        type("Lake", (), {"con": con, "bound": {"instrument": None}})(),
        as_of="2026-08-07T12:00:00")
    handle = large.call("objectset.create", {"kind": "ISSUER"})["handle"]
    oversized_result = large.call("objectset.inspect", {
        "handle": handle, "fields": ["note"], "limit": 1})
    assert oversized_result["error"]["code"] == "RESULT_TOO_LARGE"


def test_binding_can_resolve_the_readonly_rdb_relation_when_auto_view_is_absent():
    """instrument is a hand-surface name, so the statics lake has no v_instrument auto view."""
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS rdb")
    con.execute("CREATE SCHEMA rdb.public")
    con.execute("CREATE TABLE rdb.public.instrument AS SELECT 'A' AS instrument_id")
    lake = type("Lake", (), {"con": con, "bound": {"instrument": None}})()

    runtime = ObjectSetRuntime(lake, as_of="2026-08-07T12:00:00")
    created = runtime.call("objectset.create", {"kind": "ISSUER"})
    inspected = runtime.call("objectset.inspect", {
        "handle": created["handle"], "fields": ["instrument_id"], "limit": 1})

    assert inspected["objects"] == [{"instrument_id": "A"}]
