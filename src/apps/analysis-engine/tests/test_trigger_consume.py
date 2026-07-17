"""트리거 소비 전환 테스트 (ALPHA-411 PR B).

검사하는 WHY: 엔진이 트리거를 직접 쓰면 이중 writer(ADR-0005 위반)가 되살아나고,
obs/route ID 를 소비한 행이 아닌 자기 후보 ID 에서 파생하면 계보가 DB 에 없는
트리거에 매달린다.
"""

from datetime import date

from edge_analysis.daily_pipeline import (
    _stable_id,
    fetch_price_trigger,
    persist_observation_route,
)


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._one = None

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._conn.executed.append((flat, params))
        if flat.startswith("SELECT price_movement_trigger_id"):
            self._one = self._conn.trigger_row

    def fetchone(self):
        return self._one

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, trigger_row=None):
        self.executed: list = []
        self.value_batches: list = []
        self.trigger_row = trigger_row
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True


_DECOMP = {
    "members": [{"ticker": "005930", "weight": 0.6, "ret": 0.05, "contribution": 0.03, "rank": 1}],
    "proxy_ret": 0.05, "advancing": 1, "total_priced": 1, "top3": 1.0, "top1": 1.0,
    "coverage": 0.6,
}


def test_no_trigger_row_means_normal_variation():
    """행이 없으면 None — 엔진이 자체 게이트로 대체 판정하면 단일 writer 계약이 깨진다."""
    conn = _FakeConn(trigger_row=None)
    assert fetch_price_trigger(conn, "inst_ETF", date(2026, 7, 16)) is None
    # 조회는 (etf, trade_date) 자연키 + 최신 detected_at 우선이어야 한다(이행기 다중 행).
    sql, params = conn.executed[0]
    assert "ORDER BY detected_at DESC" in sql
    assert params == ("inst_ETF", "2026-07-16")


def test_lineage_derives_from_consumed_pipeline_trigger_id(monkeypatch):
    """obs/route ID 는 **소비한**(파이프라인 ULID) trigger_id 에서 파생돼야 한다 — 자기
    후보 해시에서 파생하면 계보가 DB 에 없는 트리거를 가리킨다."""
    import psycopg2.extras

    def _fake_execute_values(cur, sql, rows):
        cur._conn.value_batches.append((" ".join(sql.split()), list(rows)))

    monkeypatch.setattr(psycopg2.extras, "execute_values", _fake_execute_values)

    conn = _FakeConn(trigger_row=("pmt_01PIPELINEULID", 0.05, "abs|0.0500|>=0.03", True, False))
    gate = fetch_price_trigger(conn, "inst_ETF", date(2026, 7, 16))
    assert gate["trigger_id"] == "pmt_01PIPELINEULID"
    assert gate["abs_gate"] is True

    ids = persist_observation_route(conn, gate["trigger_id"], _DECOMP, "CONCENTRATED", True,
                                    {"005930": "ent_X"})
    assert ids["obs_id"] == _stable_id("cob", "pmt_01PIPELINEULID")
    assert ids["route_id"] == _stable_id("rte", ids["obs_id"])
    # 트리거 INSERT 는 있어선 안 된다 — 이중 writer 부활 금지.
    trigger_inserts = [s for s, _ in conn.executed
                      if s.upper().startswith("INSERT INTO PRICE_MOVEMENT_TRIGGER")]
    assert trigger_inserts == []
    obs_inserts = [p for s, p in conn.executed
                   if s.upper().startswith("INSERT INTO ETF_CONTRIBUTION_OBSERVATION")]
    assert obs_inserts[0][1] == "pmt_01PIPELINEULID"  # obs 가 소비한 행을 FK 로 가리킨다
