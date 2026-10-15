"""Verify atomic publication against the migrated local PostgreSQL database."""

import os
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.storage.publications import PublicationStore
from edge_analysis_v2.storage.tool_runs import ToolStore
from edge_analysis_v2.analysis.body_editor import BodyEditor
from edge_analysis_v2.contracts.publication_validation import FACTORS


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
            conn.execute("DELETE FROM tool_runs WHERE tool_id IN (%s,%s)", (key, key + "-news-source"))
            conn.execute("DELETE FROM tool_definitions WHERE tool_id IN (%s,%s)", (key, key + "-news-source"))
            for table in ["outlook_conclusion_keywords", "outlook_factors", "outlook_items"]:
                conn.execute(f"DELETE FROM {table} WHERE analysis_id IN (SELECT analysis_id FROM outlook_analyses WHERE etf_code=%s)", (key,))
            conn.execute("DELETE FROM movement_items WHERE analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)", (key,))
            conn.execute("DELETE FROM movement_analyses WHERE etf_code=%s", (key,))
            conn.execute("DELETE FROM outlook_analyses WHERE etf_code=%s", (key,))


def response(run):
    return {"new_items":[{"candidate_id":"new", "type":"수급", "title_keyword":"제목", "sentence":"설명",
                          "sentiment":"positive", "tool_run_ids":[run]}],
            "selected_item_ids":["new"], "summary":"요약"}


@pytest.mark.parametrize('source', ['synthetic', 'unknown'])
def test_real_publication_rejects_other_source_evidence(publication, source):
    """A valid old tool run is not evidence for a different data source."""
    store, key, _ = publication
    store.save_movement(key, response(key))
    store.connection.execute('UPDATE movement_analyses SET data_source=%s WHERE analysis_id=%s', (source, key))
    real = key + '-real'
    store.begin('movement', real, key, NOW + timedelta(minutes=1), data_source='database')
    with pytest.raises(ValueError):
        store.save_movement(real, response(key))


def test_movement_commit_reuses_original_publication_when_nothing_changed(publication):
    store, key, _ = publication
    first = store.save_movement(key, response(key))
    second = key + "-next"
    store.begin("movement", second, key, NOW + timedelta(minutes=1), key)
    assert store.save_movement(second, {"new_items":[], "selected_item_ids":[], "summary":None}) == first
    rows = store.connection.execute("SELECT published_at FROM movement_analyses WHERE etf_code=%s", (key,)).fetchall()
    assert rows[0] == rows[1]
    assert store.save_movement(key, {}) == first


def test_dashboard_exposes_original_item_and_publication_times(publication, monkeypatch):
    from contextlib import nullcontext
    from edge_analysis_v2.dashboard import server as cloud_review

    store, key, _ = publication
    store.save_movement(key, response(key))
    second = key + '-later'
    store.begin('movement', second, key, NOW + timedelta(minutes=30), key)
    store.save_movement(second, {'new_items': [], 'selected_item_ids': [], 'summary': None})
    monkeypatch.setattr(cloud_review, 'connect_results', lambda _: nullcontext(store.connection))
    first = cloud_review.read_screen(None, 'movement', key, 'all')
    later = cloud_review.read_screen(None, 'movement', second, 'all')
    assert later['items'] == first['items']
    assert datetime.fromisoformat(later['items'][0]['source_as_of']) == NOW
    assert later['items'][0]['item_id'] and later['items'][0]['added_at']
    assert later['publication']['published_at'] == first['publication']['published_at']
    assert later['publication']['analysis_at'] != first['publication']['analysis_at']
    from edge_analysis_v2.storage.inspection import read_storage
    saved = read_storage(store.connection, 'movement', second)['tables']
    assert saved['movement_analyses'][0]['analysis_id'] == second
    assert saved['movement_items'][0]['analysis_id'] == key
    assert saved['movement_items'][0]['item_id'] == later['items'][0]['item_id']
    assert saved['tool_runs'][0]['movement_analysis_id'] == key


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


def features(run):
    return {"outlook":{"direction":"상승"}, "summary_card":{"title":"요약 제목", "summary":"요약"},
            "factors":[{"type":factor, "sticker":"상승", "sentence":"설명"} for factor in FACTORS],
            "conclusion":{"title":"결론", "supports":[{"label":"도움", "tool_run_ids":[run]}],
                          "burdens":[], "sentence":"판단"}}


def test_outlook_all_features_are_assembled_from_committed_rows(publication):
    store, key, evidence = publication
    identity = key + "-outlook"
    store.begin("outlook", identity, key, NOW)
    evidence(identity, "outlook")
    editor = BodyEditor(None, NOW)
    body = editor.write("본문", [{"id":"topic", "title_keyword":"이유", "sentences":["불릿"], "sentiment":"positive", "tool_run_ids":[identity]}])
    result = store.save_outlook(identity, features(identity), body)
    assert result["detail"]["items"][0]["sentences"][0] == {"sentence":"불릿", "is_updated":False}
    assert result["detail"]["items"][0]["sentiment"] == "positive"
    assert result["detail"]["updates"]["items"] == []
    assert store.get_outlook(identity) == result
    assert store.save_outlook(identity, {}, {}) == result


def test_outlook_news_source_links_are_assembled_from_final_evidence(publication):
    store, key, evidence = publication
    identity = key + "-outlook-source"
    store.begin("outlook", identity, key, NOW)
    evidence(identity, "outlook")
    source_tool = key + "-news-source"
    audit = ToolStore(store.connection)
    audit.register_definition(tool_id=source_tool, function_name="get_issue_evidence", version=key,
                              description="Read final news evidence", source_names=["news"])
    source_run = identity + "-news-run"
    audit.save_run(tool_run_id=source_run, tool_id=source_tool, analysis_kind="outlook", analysis_id=identity,
                   arguments={"news_ids": ["article-1"], "include_body": False}, context={},
                   output={"tool_run_id": source_run, "result": {"news": [
                       {"news_id": "article-1", "title": "계약 발표", "source_uri": "https://news.example.com/article/1"}]}},
                   started_at=NOW, finished_at=NOW)
    body = BodyEditor(None, NOW).write("본문", [{"id":"topic", "title_keyword":"이유", "sentences":["불릿"],
                                                  "sentiment":"positive", "tool_run_ids":[source_run]}])
    store.final_tool_names = store.final_tool_names | {"get_issue_evidence"}
    result = store.save_outlook(identity, features(identity), body)
    assert result["detail"]["items"][0]["source_links"] == [
        {"title": "계약 발표", "url": "https://news.example.com/article/1"}]
    from edge_analysis_v2.dashboard.server import assemble_screen
    screen = assemble_screen(store.connection, "outlook", identity, "all")
    assert screen["detail"]["items"][0]["source_links"] == result["detail"]["items"][0]["source_links"]


def test_reading_before_migration_keeps_existing_dashboard_available(publication):
    store, key, evidence = publication
    identity = key + "-legacy"
    store.begin("outlook", identity, key, NOW)
    evidence(identity, "outlook")
    body = BodyEditor(None, NOW).write("본문", [{"id": "topic", "title_keyword": "이유",
        "sentences": ["불릿"], "sentiment": "positive", "tool_run_ids": [identity]}])
    store.save_outlook(identity, features(identity), body)
    # Shadow only this connection's table to represent the not-yet-migrated reader.
    store.connection.execute("CREATE TEMP TABLE outlook_items AS SELECT * FROM public.outlook_items")
    try:
        store.connection.execute("ALTER TABLE pg_temp.outlook_items DROP COLUMN sentiment, DROP COLUMN source_links")
        result = store.get_outlook(identity)
        assert result["detail"]["items"][0]["sentiment"] is None
        assert result["detail"]["items"][0]["source_links"] == []
    finally:
        store.connection.execute("DROP TABLE pg_temp.outlook_items")


def test_missing_factor_rejects_entire_publication_instead_of_inventing_neutral(publication):
    store, key, evidence = publication
    identity = key + "-outlook"
    store.begin("outlook", identity, key, NOW)
    evidence(identity, "outlook")
    incomplete = features(identity)
    incomplete["factors"].pop()
    body = BodyEditor(None, NOW).write("본문", [])
    with pytest.raises(ValueError, match="five"):
        store.save_outlook(identity, incomplete, body)
    assert store.get_outlook(identity) is None
    assert store.connection.execute("SELECT count(*) FROM outlook_factors WHERE analysis_id=%s", (identity,)).fetchone()[0] == 0


def test_outlook_changed_topic_and_daily_updates_share_new_analysis(publication):
    store, key, evidence = publication
    identity = key + "-outlook"
    store.begin("outlook", identity, key, NOW)
    evidence(identity, "outlook")
    editor = BodyEditor(None, NOW)
    body = editor.write("본문", [{"id":"topic", "title_keyword":"이유", "sentences":["불릿"], "sentiment":"positive", "tool_run_ids":[identity]}])
    first = store.save_outlook(identity, features(identity), body)
    second = identity + "-next"
    store.begin("outlook", second, key, NOW + timedelta(minutes=1), identity)
    evidence(second, "outlook")
    editor = BodyEditor(first["detail"], NOW)
    body = editor.apply([{"action":"update", "id":"topic", "sentences":["새 불릿"], "sentiment":"negative", "updated_sentence_numbers":[1], "tool_run_ids":[second]}])
    result = store.save_outlook(second, features(second), body)
    assert result["detail"]["updates"]["items"][0]["sentence"] == "새 불릿"
    assert result["detail"]["items"][0]["sentiment"] == "negative"
    assert result["detail"]["updates"]["items"][0]["sentiment"] == "negative"
    assert store.get_outlook(identity) == first


@pytest.mark.parametrize("problem", ["missing", "ineligible", "future"])
def test_unverifiable_evidence_cannot_publish_a_sentence(publication, problem):
    store, key, _ = publication
    if problem == "missing":
        invalid = response("not-stored")
        target = key
    elif problem == "ineligible":
        store.final_tool_names = frozenset()
        invalid, target = response(key), key
    else:
        target = key + "-earlier"
        store.begin("movement", target, key, NOW - timedelta(minutes=1))
        invalid = response(key)
    with pytest.raises(ValueError, match="Evidence"):
        store.save_movement(target, invalid)
    assert store.get_movement(target) is None


def test_a_late_older_result_cannot_change_the_newer_stored_publication(publication):
    store, key, evidence = publication
    newer = key + "-newer"
    store.begin("movement", newer, key, NOW + timedelta(minutes=1))
    evidence(newer)
    newest = store.save_movement(newer, response(newer))
    store.save_movement(key, response(key))
    assert store.get_movement(newer) == newest
    latest = store.connection.execute("SELECT analysis_id FROM movement_analyses WHERE etf_code=%s AND status='completed' ORDER BY analysis_at DESC LIMIT 1", (key,)).fetchone()[0]
    assert latest == newer


def test_retired_factor_evidence_survives_only_in_completed_publications(publication):
    store, key, _ = publication
    store.save_movement(key, response(key))
    store.connection.execute("UPDATE tool_definitions SET function_name='get_factor_metrics' WHERE tool_id=%s", (key,))
    second = key + '-next'
    store.begin('movement', second, key, NOW + timedelta(minutes=1), key)
    store.save_movement(second, response(key))
    store.connection.execute("UPDATE movement_analyses SET status='running' WHERE analysis_id=%s", (key,))
    with pytest.raises(ValueError, match='final-eligible'):
        store.save_movement(key, response(key))
