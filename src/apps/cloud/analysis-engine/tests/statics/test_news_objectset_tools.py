"""News ObjectSet contract: PIT-safe hierarchy and ontology-backed argument menus."""
from __future__ import annotations

import json

import duckdb

from edge_analysis.observability import collect_trace
from edge_analysis.statics.objectset_tools import NewsScope, ObjectSetRuntime


class _NewsLake:
    def __init__(self) -> None:
        self.con = duckdb.connect()
        self.bound = {
            "event_thread": "opened_at",
            "event_thread_link": "evaluated_at",
            "source_event": "available_at",
            "event_argument": None,
            "event_evidence": None,
            "instrument": "available_at",
            "etf_holding_snapshot": "trade_date",
            "document_assertion": "available_at",
            "document": "available_at",
        }
        self.con.execute("""
            CREATE VIEW v_event_thread AS SELECT * FROM (VALUES
              ('thr_1', 'COMPANY.CONTRACT.SIGNING', TIMESTAMP '2026-08-07 09:50:00'),
              ('thr_delayed', 'COMPANY.CONTRACT.SIGNING', TIMESTAMP '2026-08-07 09:00:00'),
              ('thr_rejected', 'COMPANY.CONTRACT.SIGNING', TIMESTAMP '2026-08-07 09:00:00'),
              ('thr_exact', 'COMPANY.CONTRACT.SIGNING', TIMESTAMP '2026-08-07 09:00:00'),
              ('thr_next', 'COMPANY.CONTRACT.SIGNING', TIMESTAMP '2026-08-07 09:00:00'),
              ('thr_future', 'COMPANY.CONTRACT.SIGNING', TIMESTAMP '2026-08-07 15:00:00')
            ) t(thread_id, event_type_code, opened_at)
        """)
        self.con.execute("""
            CREATE VIEW v_event_thread_link AS SELECT * FROM (VALUES
              ('evt_1', 'thr_1', 'FIRST_IN_THREAD', TIMESTAMP '2026-08-07 10:00:00'),
              ('evt_old', 'thr_1', 'FOLLOW_UP_STAGE', TIMESTAMP '2026-08-05 10:00:00'),
              ('evt_delayed', 'thr_delayed', 'FIRST_IN_THREAD', TIMESTAMP '2026-08-07 15:00:00'),
              ('evt_rejected', 'thr_rejected', 'FIRST_IN_THREAD', TIMESTAMP '2026-08-07 11:00:00'),
              ('evt_exact', 'thr_exact', 'FIRST_IN_THREAD', TIMESTAMP '2026-08-07 12:05:00'),
              ('evt_next', 'thr_next', 'FIRST_IN_THREAD', TIMESTAMP '2026-08-07 12:05:01'),
              ('evt_future', 'thr_1', 'FOLLOW_UP_STAGE', TIMESTAMP '2026-08-07 15:00:00')
            ) t(source_event_id, thread_id, novelty_status, evaluated_at)
        """)
        self.con.execute("""
            CREATE VIEW v_source_event AS SELECT * FROM (VALUES
              ('evt_1', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'ACTIVE', DATE '2026-08-07', TIMESTAMP '2026-08-07 10:00:00'),
              ('evt_delayed', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'ACTIVE', DATE '2026-08-07', TIMESTAMP '2026-08-07 09:10:00'),
              ('evt_rejected', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'REJECTED', DATE '2026-08-07', TIMESTAMP '2026-08-07 11:00:00'),
              ('evt_exact', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'ACTIVE', DATE '2026-08-07', TIMESTAMP '2026-08-07 12:05:00'),
              ('evt_next', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'ACTIVE', DATE '2026-08-07', TIMESTAMP '2026-08-07 12:05:01'),
              ('evt_old', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'ACTIVE', DATE '2026-08-05', TIMESTAMP '2026-08-05 10:00:00'),
              ('evt_future', 'NEWS', 'COMPANY.CONTRACT.SIGNING', 'ACTIVE', DATE '2026-08-07', TIMESTAMP '2026-08-07 15:00:00')
            ) t(source_event_id, source_class, event_type_code, event_status, event_date, available_at)
        """)
        self.con.execute("""
            CREATE VIEW v_event_argument AS SELECT * FROM (VALUES
              (1, 'evt_1', 'SUPPLIER', 'subject', 'Alpha', 'COMPANY_ENTITY', 'A_INS', 0.9),
              (2, 'evt_1', 'CONTRACT_OBJECT', 'object', 'next battery',
               'PRODUCT_OR_CONCEPT', NULL, 0.7),
              (3, 'evt_future', 'CUSTOMER', 'object', 'Future Buyer',
               'COMPANY_ENTITY', 'B', 0.8),
              (4, 'evt_old', 'SUPPLIER', 'subject', 'Alpha', 'COMPANY_ENTITY', 'A_INS', 0.9),
              (5, 'evt_rejected', 'SUPPLIER', 'subject', 'Alpha', 'COMPANY_ENTITY', 'A_INS', 0.9)
              ,(6, 'evt_1', 'CUSTOMER', 'object', 'Future Buyer', 'COMPANY_ENTITY', 'B', 0.8)
            ) t(event_argument_id, source_event_id, role_code, slot, mention_text,
                entity_kind, entity_id, confidence)
        """)
        self.con.execute("""
            CREATE VIEW v_event_evidence AS SELECT * FROM (VALUES
              ('ev_1', 'evt_1', 'assert_1', 'TITLE', 'Alpha signs battery contract', 0.95),
              ('ev_late_doc', 'evt_1', 'assert_late', 'BODY', 'late enrichment', 0.8),
              ('ev_old', 'evt_old', 'assert_old', 'TITLE', 'old contract', 0.95),
              ('ev_rejected', 'evt_rejected', 'assert_rejected', 'TITLE', 'bad extraction', 0.9),
              ('ev_exact', 'evt_exact', 'assert_exact', 'TITLE', 'exact cutoff', 0.9),
              ('ev_next', 'evt_next', 'assert_next', 'TITLE', 'next second', 0.9),
              ('ev_future', 'evt_future', 'assert_future', 'TITLE', 'future follow-up', 0.95)
            ) t(evidence_id, source_event_id, assertion_id, evidence_type, evidence_text, link_confidence)
        """)
        self.con.execute("""
            CREATE VIEW v_instrument AS SELECT * FROM (VALUES
              ('A_INS', TIMESTAMP '2026-08-07 09:00:00', 'Alpha'),
              ('B', TIMESTAMP '2026-08-07 15:00:00', 'Future Buyer')
            ) t(instrument_id, available_at, display_name)
        """)
        self.con.execute("""
            CREATE VIEW v_etf_holding_snapshot AS SELECT * FROM (VALUES
              ('ETF', DATE '2026-08-06', 'A_INS', TIMESTAMP '2026-08-06 18:00:00'),
              ('ETF', DATE '2026-08-06', 'A_INS', TIMESTAMP '2026-08-06 18:00:00'),
              ('ETF', DATE '2026-08-07', 'B', TIMESTAMP '2026-08-07 15:00:00')
            ) t(etf_instrument_id, trade_date, constituent_instrument_id, available_at)
        """)
        self.con.execute("""
            CREATE VIEW v_document_assertion AS SELECT * FROM (VALUES
              ('assert_1', 'doc_1', TIMESTAMP '2026-08-07 10:00:00'),
              ('assert_late', 'doc_late', TIMESTAMP '2026-08-07 15:00:00'),
              ('assert_old', 'doc_old', TIMESTAMP '2026-08-05 10:00:00'),
              ('assert_rejected', 'doc_rejected', TIMESTAMP '2026-08-07 11:00:00'),
              ('assert_exact', 'doc_exact', TIMESTAMP '2026-08-07 12:05:00'),
              ('assert_next', 'doc_next', TIMESTAMP '2026-08-07 12:05:01'),
              ('assert_future', 'doc_future', TIMESTAMP '2026-08-07 15:00:00')
            ) t(assertion_id, document_id, available_at)
        """)
        self.con.execute("""
            CREATE VIEW v_document AS SELECT * FROM (VALUES
              ('doc_1', TIMESTAMP '2026-08-07 10:00:00'),
              ('doc_late', TIMESTAMP '2026-08-07 15:00:00'),
              ('doc_old', TIMESTAMP '2026-08-05 10:00:00'),
              ('doc_rejected', TIMESTAMP '2026-08-07 11:00:00'),
              ('doc_exact', TIMESTAMP '2026-08-07 12:05:00'),
              ('doc_next', TIMESTAMP '2026-08-07 12:05:01'),
              ('doc_future', TIMESTAMP '2026-08-07 15:00:00')
            ) t(document_id, available_at)
        """)
        self.con.execute("""
            CREATE VIEW v_actor AS SELECT * FROM (VALUES
              ('A_ACT', 'COMPANY', 'Alpha'), ('B_ACT', 'COMPANY', 'Future Buyer')
            ) t(actor_id, actor_type, display_name)
        """)


def _runtime() -> ObjectSetRuntime:
    versions = {
        "event_thread": "t1", "event_thread_link": "tl1", "source_event": "se1",
        "event_argument": "ea1", "event_evidence": "ee1", "instrument": "i1",
    }
    return ObjectSetRuntime(
        _NewsLake(), as_of="2026-08-07T12:00:00", dataset_versions=versions)


def _call(runtime: ObjectSetRuntime, name: str, arguments: dict) -> dict:
    out = runtime.call(name, arguments)
    assert out.get("ok") is True, out
    return out


def test_news_contract_advertises_hierarchy_and_schema_without_executable_fields():
    specs = _runtime().tool_specs()
    news = [spec for spec in specs if spec["name"].startswith("news.")]

    assert [spec["name"] for spec in news] == [
        "news.find_threads", "news.get_thread", "news.list_events",
        "news.get_event_arguments", "news.describe_event_schema",
        "news.follow_argument", "news.get_event_evidence",
    ]
    encoded = json.dumps(news, ensure_ascii=False).lower()
    assert '"sql"' not in encoded
    assert '"query"' not in encoded
    assert '"view_name"' not in encoded
    assert all(spec["input_schema"]["additionalProperties"] is False for spec in news)


def test_news_cutoff_includes_exact_instant_and_excludes_the_next_second():
    runtime = ObjectSetRuntime(_NewsLake(), as_of="2026-08-07T12:05:00")
    threads = _call(runtime, "news.find_threads", {"limit": 10})["threads"]

    ids = {row["thread_id"] for row in threads}
    assert "thr_exact" in ids
    assert "thr_next" not in ids


def test_thread_to_event_to_argument_and_evidence_keeps_one_intraday_clock():
    runtime = _runtime()
    threads = _call(runtime, "news.find_threads", {
        "event_type_code": "COMPANY.CONTRACT.SIGNING", "limit": 10})
    thread = _call(runtime, "news.get_thread", {"thread_id": "thr_1"})
    delayed = _call(runtime, "news.get_thread", {"thread_id": "thr_delayed"})
    events = _call(runtime, "news.list_events", {"handle": thread["handle"], "limit": 10})
    arguments = _call(runtime, "news.get_event_arguments", {
        "handle": events["handle"], "limit": 10})
    evidence = _call(runtime, "news.get_event_evidence", {
        "handle": events["handle"], "limit": 10})

    assert threads["threads"] == [{
        "thread_id": "thr_1", "event_type_code": "COMPANY.CONTRACT.SIGNING",
        "opened_at": "2026-08-07T09:50:00",
    }]
    assert delayed["thread"] is None  # opened_at만 이르면 안 되고 link 평가도 도달해야 한다.
    assert events["events"] == [{
        "source_event_id": "evt_1", "event_type_code": "COMPANY.CONTRACT.SIGNING",
        "available_at": "2026-08-07T10:00:00",
    }, {
        "source_event_id": "evt_old", "event_type_code": "COMPANY.CONTRACT.SIGNING",
        "available_at": "2026-08-05T10:00:00",
    }]
    assert [row["resolved"] for row in arguments["arguments"]] == [True, False, True, True]
    assert arguments["arguments"][1]["mention_text"] == "next battery"
    assert arguments["arguments"][1]["entity_id"] is None
    assert [row["evidence_id"] for row in evidence["evidence"]] == ["ev_1", "ev_old"]
    assert {out["as_of"] for out in (threads, thread, events, arguments, evidence)} == {
        "2026-08-07T12:00:00"}
    encoded = json.dumps([threads, events, arguments, evidence]).lower()
    assert "evt_future" not in encoded and "ev_future" not in encoded
    assert '"sql"' not in encoded and '"query"' not in encoded
    assert '"view_name"' not in encoded


def test_event_schema_is_derived_from_ontology_roles_and_measure_units():
    schema = _call(_runtime(), "news.describe_event_schema", {
        "event_type_code": "COMPANY.CONTRACT.SIGNING"})

    assert schema["event_type_code"] == "COMPANY.CONTRACT.SIGNING"
    assert schema["dataset"] == {"name": "ontology", "version": schema["ontology_version"]}
    by_role = {row["role_code"]: row for row in schema["arguments"]}
    assert by_role["SUPPLIER"] == {
        "role_code": "SUPPLIER", "cardinality": "ONE_OR_MORE",
        "object_kind": "COMPANY_ENTITY", "slot": "subject",
    }
    assert by_role["CUSTOMER"]["cardinality"] == "ZERO_OR_MORE"
    assert by_role["CONTRACT_OBJECT"]["object_kind"] == "PRODUCT_OR_CONCEPT"
    measures = {row["role_code"]: row for row in schema["measures"]}
    assert measures["CONTRACT_VALUE"]["unit_family"] == "CURRENCY"
    assert measures["CONTRACT_VALUE"]["cardinality"] == "ONE_OR_MORE"
    assert measures["CONTRACT_VALUE"]["basis"] == ["TOTAL", "ANNUAL"]
    assert measures["CONTRACT_DURATION"]["unit_family"] == "DURATION_DAYS"


def test_follow_argument_resolves_an_entity_or_returns_unresolved_surface():
    runtime = _runtime()
    thread = _call(runtime, "news.get_thread", {"thread_id": "thr_1"})
    events = _call(runtime, "news.list_events", {"handle": thread["handle"]})
    arguments = _call(runtime, "news.get_event_arguments", {"handle": events["handle"]})

    resolved = _call(runtime, "news.follow_argument", {
        "handle": arguments["handle"], "event_argument_id": 1})
    unresolved = _call(runtime, "news.follow_argument", {
        "handle": arguments["handle"], "event_argument_id": 2})
    unavailable = _call(runtime, "news.follow_argument", {
        "handle": arguments["handle"], "event_argument_id": 6})

    assert resolved["resolved"] is True
    assert resolved["kind"] == "ISSUER"
    assert resolved["objects"] == [{"instrument_id": "A_INS", "display_name": "Alpha"}]
    assert unresolved["as_of"] == "2026-08-07T12:00:00"
    assert unresolved["resolved"] is False
    assert unresolved["reason"] == "UNRESOLVED_ARGUMENT"
    assert unresolved["objects"] == []
    assert unresolved["dataset"] == {"name": "event_argument", "version": "ea1"}
    assert unresolved["argument"] == {
        "event_argument_id": 2, "source_event_id": "evt_1",
        "role_code": "CONTRACT_OBJECT",
        "object_kind": "PRODUCT_OR_CONCEPT", "mention_text": "next battery",
        "entity_id": None,
    }
    assert unavailable["resolved"] is False
    assert unavailable["reason"] == "TARGET_NOT_AVAILABLE"
    assert unavailable["objects"] == []
    assert unavailable["argument"] == {
        "event_argument_id": 6, "source_event_id": "evt_1", "role_code": "CUSTOMER",
        "object_kind": "COMPANY_ENTITY", "mention_text": "Future Buyer", "entity_id": "B",
    }


def test_news_calls_reject_wrong_handle_types_unknown_schema_and_smuggled_fields():
    runtime = _runtime()
    issuer = _call(runtime, "objectset.create", {"kind": "ISSUER"})

    wrong = runtime.call("news.list_events", {"handle": issuer["handle"]})
    unknown = runtime.call("news.describe_event_schema", {"event_type_code": "SECRET.TYPE"})
    smuggled = runtime.call("news.find_threads", {
        "sql": "DROP TABLE source_event", "limit": 10})
    internal = runtime.call("objectset.create", {"kind": "NEWS_EVENT"})

    assert wrong["error"]["code"] == "HANDLE_KIND_MISMATCH"
    assert unknown["error"]["code"] == "EVENT_TYPE_NOT_ALLOWED"
    assert smuggled["error"]["code"] == "INVALID_ARGUMENTS"
    assert internal["error"]["code"] == "KIND_NOT_ALLOWED"
    encoded = json.dumps(smuggled).lower()
    assert "drop" not in encoded
    assert "sql" not in encoded and "query" not in encoded and "view_name" not in encoded


def test_scoped_news_discovers_constituent_when_direct_etf_has_zero_and_clamps_pit():
    runtime = ObjectSetRuntime(
        _NewsLake(), as_of="2026-08-07T12:00:00",
        news_scope=NewsScope("ETF", "2026-08-06"),
    )

    with collect_trace() as trace:
        threads = _call(runtime, "news.find_threads", {"limit": 20})
        events = _call(runtime, "news.list_events", {"handle": threads["handle"], "limit": 20})
        evidence = _call(runtime, "news.get_event_evidence", {
            "handle": events["handle"], "limit": 20})

    assert [row["thread_id"] for row in threads["threads"]] == ["thr_1"]
    assert [row["source_event_id"] for row in events["events"]] == ["evt_1"]
    assert [row["evidence_id"] for row in evidence["evidence"]] == ["ev_1"]
    assert threads["scope_counts"] == {
        "candidate_entities": 2, "candidate_events": 3,
        "candidate_threads": 2,
        "pit_filtered_events": 1, "final_threads": 1, "delivered_threads": 1,
    }
    [scope_trace] = [row for row in trace if row.get("event") == "news.scope"]
    assert scope_trace["relationship_lineage"] == [
        "etf_holding_snapshot", "event_argument", "source_event",
        "event_evidence", "document_assertion", "document", "event_thread_link",
    ]
