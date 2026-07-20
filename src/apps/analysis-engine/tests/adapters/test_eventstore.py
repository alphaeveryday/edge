"""Tests for the Event Store repository's trigger consumption.

The engine consumes the pipeline-written price_movement_trigger. It must not
resurrect the double-writer (ADR-0005) by inserting a trigger, and observation/
route lineage ids must derive from the *consumed* trigger id, not a locally
recomputed candidate, or the lineage points at a row that is not in the DB.
"""

from datetime import date

from edge_analysis.adapters.eventstore import EventStore
from edge_analysis.domain.models import Decomposition, Member
from edge_analysis.observability import stable_id


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._row = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if flat.startswith("SELECT price_movement_trigger_id"):
            self._row = self._conn.trigger_row

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, trigger_row=None):
        self.executed = []
        self.value_batches = []
        self.trigger_row = trigger_row
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True


_DECOMP = Decomposition(
    members=[Member("005930", "삼성전자", 0.6, 0.05, 0.03, 1)],
    proxy_ret=0.05, covered_weight=0.6, total_weight=1.0, coverage=0.6,
    top1=1.0, top3=1.0, advancing=1, total_priced=1, n_constituents=1,
)


def test_missing_trigger_row_means_normal_variation():
    conn = _FakeConn(trigger_row=None)

    assert EventStore(conn).fetch_price_trigger("inst_ETF", date(2026, 7, 16)) is None

    # Natural key + latest detected_at, since transitional duplicates may exist.
    sql, params = conn.executed[0]
    assert "ORDER BY detected_at DESC" in sql
    assert params == ("inst_ETF", "2026-07-16")


def test_lineage_derives_from_the_consumed_trigger_id(monkeypatch):
    import psycopg2.extras
    monkeypatch.setattr(
        psycopg2.extras, "execute_values",
        lambda cur, sql, rows: cur._conn.value_batches.append(list(rows)),
    )
    conn = _FakeConn(trigger_row=("pmt_01PIPELINEULID", 0.05, "abs|0.0500|>=0.03", True, False))
    store = EventStore(conn)

    gate = store.fetch_price_trigger("inst_ETF", date(2026, 7, 16))
    assert gate.trigger_id == "pmt_01PIPELINEULID"
    assert gate.abs_gate is True

    ids = store.persist_observation_route(
        gate.trigger_id, _DECOMP, "CONCENTRATED", True, {"005930": "ent_X"})
    assert ids["obs_id"] == stable_id("cob", "pmt_01PIPELINEULID")
    assert ids["route_id"] == stable_id("rte", ids["obs_id"])

    trigger_inserts = [s for s, _ in conn.executed
                       if s.upper().startswith("INSERT INTO PRICE_MOVEMENT_TRIGGER")]
    assert trigger_inserts == []  # double-writer must stay dead
    obs_inserts = [p for s, p in conn.executed
                   if s.upper().startswith("INSERT INTO ETF_CONTRIBUTION_OBSERVATION")]
    assert obs_inserts[0][1] == "pmt_01PIPELINEULID"  # obs FK points at consumed row
