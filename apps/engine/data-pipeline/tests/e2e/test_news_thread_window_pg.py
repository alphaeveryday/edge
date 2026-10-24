"""Real PostgreSQL: news threads continue the latest segment across runs (ALPHA-508).

WHY: the segment lookup is a prefix match on event_thread.thread_key. A comparison that depends on
the database collation (range bounds with punctuation) can silently miss the existing segment, and
every run would then open a new thread — each report becomes FIRST_IN_THREAD and the follow-up
signal disappears. The fake cursor cannot catch that; only the real database can.
"""
import os
from uuid import uuid4

import pytest

from data_pipeline.steps import assemble_events

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="ephemeral PostgreSQL required")


@pytest.fixture
def conn():
    import psycopg
    from psycopg import sql
    schema = "news_thread_test_" + uuid4().hex
    with psycopg.connect(host=os.environ["E2E_PGHOST"],
                         port=os.environ.get("E2E_PGPORT", "5432"),
                         dbname=os.environ.get("E2E_PGDATABASE", "edge"),
                         user=os.environ.get("E2E_PGUSER", "edge"),
                         password=os.environ.get("E2E_PGPASSWORD", "edge")) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        connection.execute("""
          CREATE TABLE equity_profile(instrument_id text PRIMARY KEY, issuer_actor_id text NOT NULL);
          CREATE TABLE event_thread(thread_id text PRIMARY KEY, thread_key text NOT NULL UNIQUE,
              event_type_code text NOT NULL, current_stage text, opened_at timestamptz,
              last_state_at timestamptz);
          CREATE TABLE event_thread_link(source_event_id text PRIMARY KEY, thread_id text,
              source_class text, novelty_status text, link_type text, evaluated_at timestamptz,
              unknown_reason text);
          CREATE TABLE thread_discovery_snapshot(source_event_id text PRIMARY KEY, thread_id text,
              prior_event_count int, days_since_previous_stage int, is_novel boolean,
              unknown_reason text, evaluated_at timestamptz);
        """)
        try:
            yield connection
        finally:
            connection.rollback()


def _event(sid: str, at: str) -> dict:
    return {"source_event_id": sid, "event_type_code": "COMPANY.CONTRACT.SIGNING",
            "available_at": at, "role_values": {"SUPPLIER": "inst_sup"},
            "lifecycle_stage": None, "predicate_code": None}


def test_later_runs_continue_the_segment_inside_the_window_and_open_one_after(conn):
    assemble_events.thread_events(conn, [_event("e1", "2026-07-01T09:00:00+09:00")])
    assemble_events.thread_events(conn, [_event("e2", "2026-07-03T09:00:00+09:00")])   # 다음 런, 2일 뒤
    assemble_events.thread_events(conn, [_event("e3", "2027-03-01T09:00:00+09:00")])   # 창 밖

    links = dict(conn.execute(
        "SELECT source_event_id, thread_id || ' ' || novelty_status FROM event_thread_link").fetchall())
    t1, n1 = links["e1"].split()
    t2, n2 = links["e2"].split()
    t3, n3 = links["e3"].split()
    assert t1 == t2 and (n1, n2) == ("FIRST_IN_THREAD", "FOLLOW_UP_STAGE")
    assert t3 != t1 and n3 == "FIRST_IN_THREAD"
    assert conn.execute("SELECT count(*) FROM event_thread").fetchone() == (2,)
