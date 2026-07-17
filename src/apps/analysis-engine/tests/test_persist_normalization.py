"""persist_normalization 의 document_id 자연키 해소 테스트 (ALPHA-409).

실 DB 없이 돈다 — 가짜 커넥션이 실행된 SQL·배치를 기록한다. 검사하는 WHY:
document 는 이제 이중 writer 다(load-documents=ULID, 여기=결정적 해시). 종속 행
(news_document·document_entity·document_assertion)이 **실제 DB 행의 ID** 가 아니라
자기 후보 해시 ID 로 FK 를 걸면, 로더가 먼저 적재한 기사에서 persist 트랜잭션
전체가 fk_document_assertion_document 위반으로 죽는다 — analyze 페이즈 적색.
"""

from datetime import date
from types import SimpleNamespace

from edge_analysis.daily_pipeline import _stable_id, persist_normalization


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        self._conn.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self._conn.resolved_rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, resolved_rows):
        self.executed: list = []
        self.value_batches: list = []
        self.resolved_rows = resolved_rows
        self.committed = False

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        self.committed = True


def _fake_execute_values(cur, sql, rows):
    cur._conn.value_batches.append((" ".join(sql.split()), list(rows)))


def _batch(conn, table: str) -> list:
    for sql, rows in conn.value_batches:
        if sql.upper().startswith(f"INSERT INTO {table.upper()} "):
            return rows
    raise AssertionError(f"{table} 배치가 실행되지 않았다")


_ROWS = [{"article_id": "a1", "title": "삼성전자 수주", "published_at": "2026-07-16T09:00:00+09:00"}]
_CLS = {
    "a1": {
        "article_id": "a1",
        "event_type_code": "SUPPLY_CONTRACT",
        "predicate_code": "WIN",
        "primary_ticker": "005930",
        "entity_id": "ent_X",
        "role_code": "ISSUER",
        "lifecycle_stage": None,
        "confidence": 0.9,
    }
}
_SETTINGS = SimpleNamespace(trade_date=date(2026, 7, 16))


def _run(monkeypatch, resolved_rows):
    monkeypatch.setattr("psycopg2.extras.execute_values", _fake_execute_values)
    conn = _FakeConn(resolved_rows)
    created = persist_normalization(conn, _ROWS, _CLS, {"005930": "ent_X"}, _SETTINGS)
    return conn, created


def test_dependents_use_resolved_id_when_loader_wrote_first(monkeypatch):
    """load-documents 가 먼저 적재한 기사(ULID 행 존재) — document INSERT 는 skip 되고,
    종속 행은 후보 해시가 아니라 **그 ULID** 로 걸려야 FK 가 산다(ALPHA-409의 핵심)."""
    loader_id = "doc_01LOADERULID"
    conn, created = _run(monkeypatch, resolved_rows=[("a1", loader_id)])

    candidate = _stable_id("doc", "bigkinds", "a1")
    assert _batch(conn, "news_document") == [(loader_id,)]
    assert _batch(conn, "document_entity")[0][0] == loader_id
    assertion_row = _batch(conn, "document_assertion")[0]
    assert assertion_row[1] == loader_id
    assert candidate not in {assertion_row[1], _batch(conn, "document_entity")[0][0]}, \
        "종속 행이 DB 에 없는 후보 해시 ID 를 참조한다 — FK 위반 경로"
    # assertion_id 도 해소된 ID 에서 파생돼야 재실행이 같은 ID 로 수렴한다(멱등).
    assert assertion_row[0] == _stable_id("asrt", loader_id, "SUPPLY_CONTRACT", "WIN")
    assert conn.committed


def test_behavior_unchanged_when_engine_wrote_first(monkeypatch):
    """분석엔진이 먼저였던 기존 데이터 — 해소 결과가 자기 해시 ID 라 픽스 전과 같은 ID 로
    수렴해야 한다(기존 행과의 멱등이 깨지면 재실행이 중복 계보를 만든다)."""
    candidate = _stable_id("doc", "bigkinds", "a1")
    conn, created = _run(monkeypatch, resolved_rows=[("a1", candidate)])

    assert _batch(conn, "news_document") == [(candidate,)]
    row = _batch(conn, "document_assertion")[0]
    assert row[0] == _stable_id("asrt", candidate, "SUPPLY_CONTRACT", "WIN")
    assert row[1] == candidate


def test_resolution_query_targets_natural_key(monkeypatch):
    """해소 SELECT 는 자연키(source_code, source_document_id)로 실제 행을 읽어야 한다 —
    후보 ID 로 읽으면(WHERE document_id = …) 로더 행을 영영 못 찾는다."""
    conn, _ = _run(monkeypatch, resolved_rows=[("a1", "doc_X")])
    selects = [(sql, p) for sql, p in conn.executed if sql.upper().startswith("SELECT")]
    assert len(selects) == 1
    sql, params = selects[0]
    assert "source_code" in sql and "source_document_id" in sql
    assert params == ("bigkinds", ["a1"])
