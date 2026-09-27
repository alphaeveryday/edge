"""Exercise real joins locally; SQLite does not establish PostgreSQL compatibility."""
import sqlite3
from types import SimpleNamespace

import pytest

from edge_analysis_v2.thread_repository import load_thread_summary

AT = "2026-09-21T08:30:00+09:00"
START = "2026-09-21T00:00:00+09:00"


class Connection:
    """Adapt parameter markers only; execute joins instead of mocking rows."""
    read_only = True
    isolation_level = SimpleNamespace(name="REPEATABLE_READ")

    def __init__(self):
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, args):
        self.result = self.raw.execute(query.replace("%s", "?"), args)

    def fetchall(self):
        return [dict(row) for row in self.result.fetchall()]


@pytest.fixture
def db():
    connection = Connection()
    connection.raw.executescript("""
        CREATE TABLE document(document_id, document_type, title, published_at, available_at);
        CREATE TABLE news_document(document_id, lead_text, lead_observed_at);
        CREATE TABLE document_entity(document_id, entity_id);
        CREATE TABLE document_assertion(assertion_id, document_id, available_at);
        CREATE TABLE event_evidence(source_event_id, assertion_id);
        CREATE TABLE source_event(source_event_id, source_class, event_status, available_at,
                                  lifecycle_stage, event_type_code, predicate_code);
        CREATE TABLE event_thread_link(source_event_id, thread_id, source_class, evaluated_at, novelty_status);
    """)
    yield connection
    connection.raw.close()


def article(db, number, novelty="FIRST_IN_THREAD", target="sixth-stock"):
    identity = str(number)
    db.raw.execute("INSERT INTO document VALUES (?, 'NEWS', ?, ?, ?)", (identity, "증설 착수 " + identity, AT, AT))
    db.raw.execute("INSERT INTO document_entity VALUES (?, ?)", (identity, target))
    db.raw.execute("INSERT INTO document_assertion VALUES (?, ?, ?)", (identity, identity, AT))
    db.raw.execute("INSERT INTO event_evidence VALUES (?, ?)", (identity, identity))
    db.raw.execute("INSERT INTO source_event VALUES (?, 'NEWS', 'ACTIVE', ?, 'ONGOING', 'CAPACITY', 'EXPAND')", (identity, AT))
    db.raw.execute("INSERT INTO event_thread_link VALUES (?, 'thread', 'NEWS', ?, ?)", (identity, AT, novelty))


def load(db):
    return load_thread_summary(db, "thread", ["sixth-stock"], start_at=START, analysis_at=AT)


def test_all_rows_contribute_to_count_without_top_five_or_300_article_cap(db):
    article(db, 0)
    article(db, 1, "CORRECTION")
    for number in range(2, 307):
        article(db, number, "DUPLICATE_REBROADCAST")
    db.raw.execute("INSERT INTO event_evidence VALUES ('2', '2')")
    article(db, "unrelated", target="other")
    result = load(db)
    assert result["duplicate_count"] == 305
    assert {e["novelty_status"] for e in result["stages"][0]["events"]} == {"FIRST_IN_THREAD", "CORRECTION"}


@pytest.mark.parametrize("table,column", [
    ("document", "published_at"), ("document", "available_at"),
    ("document_assertion", "available_at"), ("source_event", "available_at"),
    ("event_thread_link", "evaluated_at"),
])
def test_every_dated_link_is_required_by_cutoff(db, table, column):
    article(db, 0)
    assert load(db) is not None  # Exact cutoff is inclusive.
    db.raw.execute(f"UPDATE {table} SET {column} = ?", ("2026-09-21T08:30:01+09:00",))
    assert load(db) is None


def test_invalid_scope_and_write_connection_are_rejected(db):
    with pytest.raises(ValueError, match="constituent"):
        load_thread_summary(db, "thread", [], start_at=START, analysis_at=AT)
    db.read_only = False
    with pytest.raises(ValueError, match="read-only"):
        load(db)
