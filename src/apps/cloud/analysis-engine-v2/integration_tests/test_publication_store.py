"""Verify atomic publication against the migrated local PostgreSQL database."""

import os
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.publication_store import PublicationStore
from edge_analysis_v2.tool_store import ToolStore


NOW = datetime.fromisoformat("2026-09-28T10:00:00+09:00")


@pytest.fixture
def publication():
    dsn = os.environ["V2_TEST_DSN"]
    args = conninfo_to_dict(dsn)
    if args.get("host") not in ("localhost", "127.0.0.1") or args.get("port") != "55439" or args.get("dbname") != "analysis_v2":
        raise ValueError("Requires local analysis_v2 on 55439")
    key = "publication-test-" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        store = PublicationStore(conn, final_tool_names={key})
        store.begin("movement", key, key, NOW)
        audit = ToolStore(conn)
        audit.register_definition(tool_id=key, function_name=key, version="v1", description="Test sum", source_names=["fixture"])
        def evidence(identity=key, kind="movement"):
            audit.save_run(tool_run_id=identity, tool_id=key, analysis_kind=kind, analysis_id=identity,
                           arguments={}, context={}, output={"tool_run_id":identity,"result":{"value":18}},
                           started_at=NOW, finished_at=NOW)
        evidence()
        try:
            yield store, key, evidence
        finally:
            conn.execute("DELETE FROM tool_runs WHERE tool_id=%s", (key,))
            conn.execute("DELETE FROM tool_definitions WHERE tool_id=%s", (key,))
            for table in ["outlook_conclusion_keywords", "outlook_factors", "outlook_items"]:
                conn.execute(f"DELETE FROM {table} WHERE analysis_id IN (SELECT analysis_id FROM outlook_analyses WHERE etf_code=%s)", (key,))
            conn.execute("DELETE FROM movement_items WHERE analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)", (key,))
            conn.execute("DELETE FROM movement_analyses WHERE etf_code=%s", (key,))
            conn.execute("DELETE FROM outlook_analyses WHERE etf_code=%s", (key,))


def response(run):
    return {"new_items":[{"candidate_id":"new", "type":"수급", "title_keyword":"제목", "sentence":"설명",
                          "sentiment":"positive", "tool_run_ids":[run]}],
            "selected_item_ids":["new"], "summary":"요약"}


def test_movement_commit_reuses_original_publication_when_nothing_changed(publication):
    store, key, _ = publication
    first = store.save_movement(key, response(key))
    second = key + "-next"
    store.begin("movement", second, key, NOW + timedelta(minutes=1), key)
    assert store.save_movement(second, {"new_items":[], "selected_item_ids":[], "summary":None}) == first
    rows = store.connection.execute("SELECT published_at FROM movement_analyses WHERE etf_code=%s", (key,)).fetchall()
    assert rows[0] == rows[1]
    assert store.save_movement(key, {}) == first


def test_invalid_selection_rolls_back_new_items_but_keeps_tool_evidence(publication):
    store, key, _ = publication
    invalid = response(key)
    invalid["selected_item_ids"] = ["missing"]
    with pytest.raises(ValueError):
        store.save_movement(key, invalid)
    assert store.connection.execute("SELECT count(*) FROM movement_items WHERE analysis_id=%s", (key,)).fetchone()[0] == 0
    assert ToolStore(store.connection).get_run(key) is not None


def test_previous_day_items_cannot_be_selected_as_today(publication):
    store, key, _ = publication
    store.save_movement(key, response(key))
    selected = store.connection.execute("SELECT selected_item_ids FROM movement_analyses WHERE analysis_id=%s", (key,)).fetchone()[0]
    second = key + "-tomorrow"
    store.begin("movement", second, key, NOW + timedelta(days=1), key)
    with pytest.raises(ValueError, match="other-day"):
        store.save_movement(second, {"new_items":[], "selected_item_ids":selected, "summary":"요약"})
