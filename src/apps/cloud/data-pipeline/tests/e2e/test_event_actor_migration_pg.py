"""Real PostgreSQL checks: no lost participant evidence or split event history."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="ephemeral PostgreSQL required")
MIGRATION = (Path(__file__).parents[5] / "libs/schema/migrations-cloud"
             / "V202610030010__event_participants_reference_issuers.sql")


@pytest.fixture
def conn():
    import psycopg
    from psycopg import sql
    schema = "actor_test_" + uuid4().hex
    with psycopg.connect(host=os.environ["E2E_PGHOST"],
                         port=os.environ.get("E2E_PGPORT", "5432"),
                         dbname=os.environ.get("E2E_PGDATABASE", "edge"),
                         user=os.environ.get("E2E_PGUSER", "edge"),
                         password=os.environ.get("E2E_PGPASSWORD", "edge")) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        connection.execute("""
          CREATE TABLE entity(entity_id text PRIMARY KEY);
          CREATE TABLE company_profile(actor_id text PRIMARY KEY REFERENCES entity);
          CREATE TABLE equity_profile(instrument_id text PRIMARY KEY REFERENCES entity,
              issuer_actor_id text NOT NULL REFERENCES entity);
          CREATE TABLE source_event(source_event_id text PRIMARY KEY, event_type_code text,
              available_at timestamptz, event_date date);
          CREATE TABLE event_argument(event_argument_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
              source_event_id text REFERENCES source_event, role_code text,
              entity_id text REFERENCES entity, mention_text text, group_ord int,
              UNIQUE(source_event_id,role_code,entity_id));
          CREATE TABLE event_thread(thread_id text PRIMARY KEY, thread_key text UNIQUE,
              event_type_code text, current_stage text, opened_at timestamptz, last_state_at timestamptz);
          CREATE TABLE event_thread_link(source_event_id text PRIMARY KEY REFERENCES source_event,
              thread_id text REFERENCES event_thread, source_class text, novelty_status text,
              link_type text, evaluated_at timestamptz, unknown_reason text);
          CREATE TABLE thread_discovery_snapshot(source_event_id text PRIMARY KEY REFERENCES source_event,
              thread_id text REFERENCES event_thread, prior_event_count int,
              days_since_previous_stage int, is_novel boolean, unknown_reason text, evaluated_at timestamptz);
          CREATE TABLE event_measure(source_event_id text REFERENCES source_event,
              measure_ord int, value numeric, value_source text, unit text, role_code text, group_ord int);
          INSERT INTO entity VALUES ('share'),('preferred'),('actor'),('buyer'),('concept');
          INSERT INTO company_profile VALUES ('actor'),('buyer');
          INSERT INTO equity_profile VALUES ('share','actor'),('preferred','actor');
          INSERT INTO source_event VALUES ('event','COMPANY.CONTRACT.SIGNING','2026-10-02T09:00Z','2026-10-02');
          INSERT INTO event_argument(source_event_id,role_code,entity_id,mention_text,group_ord)
            VALUES ('event','SUPPLIER','share','Company',0),
                   ('event','CUSTOMER','buyer','Buyer',0),
                   ('event','CONTRACT_OBJECT','concept','Chip',0),
                   ('event','CUSTOMER',NULL,'Unnamed',1);
          INSERT INTO event_thread VALUES ('historical',
            'event_type_id=COMPANY.CONTRACT.SIGNING||required:SUPPLIER=share||required:CUSTOMER=buyer||required:CONTRACT_OBJECT=concept');
          INSERT INTO event_thread_link VALUES ('event','historical');
          INSERT INTO event_measure VALUES ('event',0,100,'PARSED','KRW','CONTRACT_VALUE',0);
        """)
        # Roll back the complete test schema, including transactional DDL.
        try:
            yield connection
        finally:
            connection.rollback()


def test_migration_preserves_record_identity_evidence_and_thread_references(conn):
    before = conn.execute("SELECT * FROM event_argument ORDER BY event_argument_id").fetchall()
    conn.execute(MIGRATION.read_text(encoding="utf8"))
    after = conn.execute("SELECT * FROM event_argument ORDER BY event_argument_id").fetchall()
    assert after == [before[0][:3] + ("actor",) + before[0][4:], *before[1:]]
    assert conn.execute("SELECT source_event_id,thread_id FROM event_thread_link").fetchall() == [("event", "historical")]
    thread_id, key = conn.execute("SELECT thread_id,thread_key FROM event_thread").fetchone()
    assert thread_id == "historical" and "SUPPLIER=actor||" in key
    assert conn.execute("SELECT count(*) FROM event_argument a JOIN equity_profile e ON e.instrument_id=a.entity_id").fetchone() == (0,)


def test_migration_maps_and_sorts_multi_party_keys_without_substring_replacement(conn):
    conn.execute("INSERT INTO event_thread VALUES ('multi', %s)",
                 ('event_type_id=X||required:ISSUER=["preferred", "share", "buyer"]||required:PRODUCT=share_suffix',))
    conn.execute(MIGRATION.read_text(encoding="utf8"))
    assert conn.execute("SELECT thread_key FROM event_thread WHERE thread_id='multi'").fetchone() == (
        'event_type_id=X||required:ISSUER=["actor", "buyer"]||required:PRODUCT=share_suffix',)


def test_conflicting_participant_evidence_aborts_the_whole_migration(conn):
    import psycopg
    conn.execute("INSERT INTO event_argument(source_event_id,role_code,entity_id,mention_text) VALUES ('event','SUPPLIER','preferred','Other mention')")
    with pytest.raises(psycopg.errors.RaiseException, match="participant collision"):
        with conn.transaction():
            conn.execute(MIGRATION.read_text(encoding="utf8"))
    assert conn.execute("SELECT count(*) FROM event_argument WHERE entity_id IN ('share','preferred')").fetchone() == (2,)
    assert "SUPPLIER=share||" in conn.execute("SELECT thread_key FROM event_thread").fetchone()[0]


def test_colliding_thread_keys_do_not_silently_merge_histories(conn):
    import psycopg
    conn.execute("INSERT INTO event_thread VALUES ('other',%s)",
                 ('event_type_id=COMPANY.CONTRACT.SIGNING||required:SUPPLIER=actor||required:CUSTOMER=buyer||required:CONTRACT_OBJECT=concept',))
    with pytest.raises(psycopg.errors.RaiseException, match="thread collision"):
        with conn.transaction():
            conn.execute(MIGRATION.read_text(encoding="utf8"))
    assert conn.execute("SELECT entity_id FROM event_argument WHERE role_code='SUPPLIER'").fetchone() == ("share",)


def test_legacy_writer_is_rejected_instead_of_reintroducing_equity_participants(conn):
    import psycopg
    conn.execute(MIGRATION.read_text(encoding="utf8"))
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE event_argument SET entity_id='share' WHERE role_code='SUPPLIER'")
    assert conn.execute("SELECT entity_id FROM event_argument WHERE role_code='SUPPLIER'").fetchone() == ("actor",)


def test_dart_measure_lookup_survives_actor_cutover(conn):
    from data_pipeline.steps.dart_values import _MEASURE_SQL
    before = conn.execute(_MEASURE_SQL, ("2026-10-02", "2026-10-02")).fetchall()
    assert len(before) == 1
    conn.execute(MIGRATION.read_text(encoding="utf8"))
    assert conn.execute(_MEASURE_SQL, ("2026-10-02", "2026-10-02")).fetchall() == before


def test_new_event_continues_existing_history_after_real_database_migration(conn):
    from data_pipeline.steps.assemble_events import thread_events
    conn.execute(MIGRATION.read_text(encoding="utf8"))
    conn.execute("INSERT INTO source_event VALUES ('next','COMPANY.CONTRACT.SIGNING','2026-10-03T09:00Z','2026-10-03')")
    assert thread_events(conn, [{
        "source_event_id": "next", "event_type_code": "COMPANY.CONTRACT.SIGNING",
        "available_at": "2026-10-03T09:00:00Z",
        "role_values": {"SUPPLIER": "share", "CUSTOMER": "buyer", "CONTRACT_OBJECT": "concept"},
    }]) == 0
    assert conn.execute("SELECT count(*) FROM event_thread").fetchone() == (1,)
    assert conn.execute("SELECT thread_id,novelty_status FROM event_thread_link WHERE source_event_id='next'").fetchone() == (
        "historical", "DUPLICATE_REBROADCAST")
    assert conn.execute("SELECT prior_event_count FROM thread_discovery_snapshot WHERE source_event_id='next'").fetchone() == (1,)
