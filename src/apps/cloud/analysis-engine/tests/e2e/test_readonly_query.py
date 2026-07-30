"""읽기전용 질의 경로 — 실제 Postgres 에서.

이 파일이 존재하는 이유가 ALPHA-622 의 요점이다. 가짜 커서는 SQL 을 파싱하지 않으므로
"읽기전용이다"라는 주장을 검증할 수 없다. 그래서 여기서는 **가드를 우회해서** 커서로
직접 쓰기를 던진다. 막히는 주체가 애플리케이션이 아니라 서버여야 한다.

인증(IAM 토큰)은 여기서 검사할 수 없다 - 로컬 Postgres 에 ``rds_iam`` 이 없다. 이 파일이
검사하는 것은 **인가**(agent_ro 가 무엇을 할 수 있는가)와 **읽기전용 트랜잭션**이다.
토큰 발급은 클라우드에서만 성립하고, 그 경계를 테스트가 흐리게 만들지 않는다.
"""
from __future__ import annotations

import os
import secrets

import psycopg2
import pytest

from edge_analysis.adapters.readonly import connect_readonly, run_query
from edge_analysis.config import _load_pg

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

_MASTER = {
    "host": os.environ.get("E2E_PGHOST", "127.0.0.1"),
    "port": int(os.environ.get("E2E_PGPORT", "5432")),
    "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
    "user": os.environ.get("E2E_PGUSER", "edge"),
    "password": os.environ.get("E2E_PGPASSWORD", "edge"),
}


@pytest.fixture
def ro_conn(monkeypatch):
    """``agent_ro`` 로 붙은 읽기전용 커넥션.

    마스터로 비밀번호를 하나 심는다. 클라우드에서는 IAM 토큰이 그 자리를 채우므로 비밀번호
    경로는 로컬 전용이다 - 매 실행 무작위로 만들어 픽스처 밖으로 새지 않게 한다.
    """
    password = secrets.token_urlsafe(16)
    master = psycopg2.connect(**_MASTER)
    master.autocommit = True
    with master.cursor() as cur:
        cur.execute("ALTER ROLE agent_ro PASSWORD %s", (password,))

    for key, value in _MASTER.items():
        monkeypatch.setenv({"dbname": "PGDATABASE"}.get(key, f"PG{key.upper()}"), str(value))
    monkeypatch.setenv("PGUSER", "agent_ro")
    monkeypatch.setenv("PGPASSWORD", password)
    monkeypatch.setenv("PGSSLMODE", "disable")  # 로컬 Postgres 는 TLS 가 꺼져 있다

    conn = connect_readonly(_load_pg(), timeout_ms=2000)
    try:
        yield conn
    finally:
        conn.close()
        with master.cursor() as cur:
            cur.execute("ALTER ROLE agent_ro PASSWORD NULL")
        master.close()


def test_reading_the_ledger_works(ro_conn):
    """경로가 실제로 산다 - 마이그레이션이 만든 테이블을 agent_ro 가 읽는다."""
    columns, rows = run_query(ro_conn, "SELECT count(*) AS n FROM instrument")
    assert columns == ["n"]
    assert rows[0][0] >= 0


@pytest.mark.parametrize("sql", [
    # entity_type 을 넣지 않는다 - GENERATED 컬럼이라 재작성 단계에서 먼저 거부되고,
    # 그러면 읽기전용 규칙이 발동할 기회가 없어 이 테스트가 검사하려는 층을 못 건드린다.
    "INSERT INTO instrument (instrument_id) VALUES ('x')",
    "UPDATE instrument SET ticker = 'zz'",
    "DELETE FROM instrument",
    "CREATE TABLE _should_not_exist (x int)",
    "DROP TABLE instrument",
])
def test_writes_are_refused_by_the_server_not_the_guard(ro_conn, sql):
    """**가드를 우회해도** 막힌다.

    이게 이 경로의 안전 주장 전부다. 정규식이 뚫리는 날에도 원장은 안전해야 한다.
    """
    with pytest.raises(psycopg2.Error) as caught:
        with ro_conn.cursor() as cur:
            cur.execute(sql)
    message = str(caught.value).lower()
    assert "read-only transaction" in message or "permission denied" in message \
        or "must be owner" in message, message
    ro_conn.rollback()


def test_row_cap_truncates_a_runaway_result(ro_conn):
    """상한이 없으면 한 번의 실수가 로그를 수 GB 로 만든다."""
    _, rows = run_query(ro_conn, "SELECT * FROM generate_series(1, 100)", row_cap=5)
    assert len(rows) == 5


def test_statement_timeout_kills_a_hanging_query(ro_conn):
    """폭주 질의가 태스크를 붙잡으면 다음 질의가 영원히 대기한다."""
    with pytest.raises(psycopg2.errors.QueryCanceled):
        run_query(ro_conn, "SELECT pg_sleep(30)")
    ro_conn.rollback()
