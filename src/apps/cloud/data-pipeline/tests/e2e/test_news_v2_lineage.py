"""News assembly must retain Actor participation and reach the supported v2 tools.

Uses the full cloud migration set on ephemeral PostgreSQL. Only classification is
faked; persistence, source selection and tool reads use their real implementations.
Replaces the retired v1 assembly-to-explanation golden path (ALPHA-1164).
"""
import io
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="ephemeral PostgreSQL required")

ETF = "inst_01KXJB6W2EFJF0AGPMWG967ZSZ"
SHARE = "inst_01KXJB6W2EFQRP1D5TBRF0EBEK"
EVENT_TYPE = "COMPANY.CAPITAL.DIVIDEND_DECISION"
DAY = datetime.now(timezone.utc).date().isoformat()


def test_assembled_actor_event_reaches_v2_news_tools(tmp_path):
    import psycopg
    import pyarrow as pa
    import pyarrow.parquet as pq
    from psycopg.rows import dict_row
    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage, canonical_news_articles_partition
    from data_pipeline.steps import assemble_events
    from edge_analysis_v2.sources.database import DatabaseTools, load_source

    pg = dict(host=os.environ["E2E_PGHOST"], port=int(os.environ.get("E2E_PGPORT", "5432")),
              dbname=os.environ.get("E2E_PGDATABASE", "edge"), user=os.environ.get("E2E_PGUSER", "edge"),
              password=os.environ.get("E2E_PGPASSWORD", "edge"), connect_timeout=5)
    if pg["host"] not in ("localhost", "127.0.0.1"):
        raise ValueError("This fixture requires isolated local PostgreSQL, never a shared database")
    article_id = "actor-v2-" + uuid4().hex
    title = "삼성전자 배당 결정"
    storage = LocalStorage(tmp_path / "lake")
    rows = [{"article_id": article_id, "source_vendor": "bigkinds", "market": "KR",
             "title": title, "publisher": "매일경제", "published_at": DAY + "T09:00:00+09:00",
             "mentions": json.dumps([{"market": "KR", "ticker": "005930"}])}]
    columns = ("article_id", "source_vendor", "market", "title", "url", "normalized_url",
               "normalized_url_hash", "published_at", "publisher", "lead_text", "mentions", "fetched_at")
    content = io.BytesIO()
    pq.write_table(pa.table({c: [r.get(c) for r in rows] for c in columns}), content)
    storage.put_bytes(f"{canonical_news_articles_partition('ko', DAY)}/part-00000.parquet", content.getvalue())

    def classify(_system, user):
        if "event_type_code" in json.loads(user):
            return json.dumps({"items": [{"id": article_id, "predicate": "DECLARE", "stage": None,
                                         "arguments": [], "measures": [], "confidence": "H"}]})
        return json.dumps({"items": [{"id": article_id, "doc_class": "EVENT", "event_type_code": EVENT_TYPE,
                                     "primary_ticker": "005930", "confidence": 0.9}]})

    with psycopg.connect(**pg, autocommit=True) as seed:
        # Like the other full-schema E2Es, this fixture owns an ephemeral database.
        seed.execute("TRUNCATE document, source_event, event_thread, etf_holding_snapshot, etf_holding_snapshot_status CASCADE")
        actor = seed.execute("SELECT issuer_actor_id FROM equity_profile WHERE instrument_id=%s", (SHARE,)).fetchone()[0]
        assert actor != SHARE
        seed.execute("INSERT INTO etf_profile(instrument_id,etf_type) VALUES (%s,'SECTOR') ON CONFLICT DO NOTHING", (ETF,))
        seed.execute("INSERT INTO etf_holding_snapshot VALUES (%s,%s,%s,1,%s,'actor-v2-e2e')", (ETF, SHARE, DAY, DAY))
        seed.execute("INSERT INTO etf_holding_snapshot_status VALUES (%s,%s,1,1,'actor-v2-e2e',%s)", (ETF, DAY, DAY))

        config = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"], user=pg["user"],
                          password=pg["password"], sslmode="disable")
        assert assemble_events.run(storage, article_id, db=config, complete_fn=classify, from_date=DAY, to_date=DAY) == 0
        event_id, document_id, thread_id = seed.execute("""SELECT e.source_event_id,a.document_id,l.thread_id
            FROM document_assertion a JOIN event_evidence e USING(assertion_id)
            JOIN event_thread_link l USING(source_event_id) JOIN document d USING(document_id)
            WHERE d.source_document_id=%s""", (article_id,)).fetchone()
        assert thread_id is not None
        assert seed.execute("SELECT role_code,entity_id FROM event_argument WHERE source_event_id=%s", (event_id,)).fetchall() == [("ISSUER", actor)]
        assert seed.execute("SELECT entity_id FROM document_entity WHERE document_id=%s", (document_id,)).fetchall() == [(SHARE,)]
        assert seed.execute("SELECT thread_key FROM event_thread WHERE thread_id=%s", (thread_id,)).fetchone() == (
            f"event_type_id={EVENT_TYPE}||required:ISSUER={actor}",)

        # Reprocessing the same source must not split identity or history.
        assert assemble_events.run(storage, article_id + "-retry", db=config, complete_fn=classify, from_date=DAY, to_date=DAY) == 0
        assert seed.execute("SELECT source_event_id,thread_id FROM event_thread_link WHERE source_event_id=%s", (event_id,)).fetchall() == [(event_id, thread_id)]

        with psycopg.connect(**pg, row_factory=dict_row) as reader:
            reader.read_only = True
            reader.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            # Execution time is later than the thread's actual evaluation timestamp.
            cutoff = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            tools = DatabaseTools(load_source(reader, "091160", cutoff))
            result = tools.call("search_news_threads", {})["result"]
            assert [t["thread_id"] for t in result["threads"]] == [thread_id]
            found = [e for t in result["threads"] for s in t["stages"] for e in s["events"]]
            assert [(e["event_id"], e["news_id"]) for e in found] == [(event_id, document_id)]
            assert tools.call("get_issue_evidence", {"news_ids": [document_id], "include_body": False})["result"] == {
                "news": [{"news_id": document_id, "title": title}]}
            # Assembly did not fetch a body; the tool must not invent one.
            assert tools.call("get_issue_evidence", {"news_ids": [document_id], "include_body": True})["result"]["news"] == [
                {"news_id": document_id, "title": title, "body": None, "body_kind": "excerpt"}]

        # The supported minute writer must share the batch identity and replay gate.
        from data_pipeline.minute.event_assembly import NewsEventAssembler
        assembler = NewsEventAssembler(db=config)
        minute_article = {"title": "삼성전자, 분기 배당 확대 결정", "published_at": DAY + "T00:00:00+00:00", "language_code": "ko"}
        assertion = {"event_type_code": EVENT_TYPE, "predicate_code": "DECLARE",
                     "arguments": [{"role_code": "ISSUER", "text": "삼성전자", "entity_id": None}],
                     "confidence": 0.9, "completeness": "complete", "missing_required_roles": []}
        extraction = {"status": "ok", "assertions": [assertion, dict(assertion)]}
        args = dict(source_code="bigkinds", article_id=article_id + "-minute", article=minute_article, result=extraction)
        assert assembler.assemble(**args) == {"assembled": 1, "unresolved_primary": 0}
        minute_rows = seed.execute("""SELECT se.source_event_id,a.document_id,l.thread_id,ea.entity_id
            FROM document_assertion a JOIN event_evidence e USING(assertion_id)
            JOIN source_event se USING(source_event_id) JOIN event_thread_link l USING(source_event_id)
            JOIN event_argument ea USING(source_event_id) JOIN document d USING(document_id)
            WHERE d.source_document_id=%s AND ea.role_code='ISSUER'""", (article_id + "-minute",)).fetchall()
        assert len(minute_rows) == 1
        minute_event, minute_doc, minute_thread, minute_actor = minute_rows[0]
        assert (minute_thread, minute_actor) == (thread_id, actor)
        assert assembler.assemble(**args)["skipped"] == "already_assembled"
        with psycopg.connect(**pg, row_factory=dict_row) as reader:
            reader.read_only = True
            reader.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            tools = DatabaseTools(load_source(reader, "091160", (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()))
            threads = tools.call("search_news_threads", {})["result"]["threads"]
            assert [t["thread_id"] for t in threads] == [thread_id]
            assert {(e["event_id"], e["news_id"]) for t in threads for s in t["stages"] for e in s["events"]} == {
                (event_id, document_id), (minute_event, minute_doc)}
