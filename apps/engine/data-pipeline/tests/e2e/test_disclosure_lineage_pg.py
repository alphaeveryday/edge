"""Real PostgreSQL: 정정공시가 원공시 계보를 찾는 조회 (공급계약 정정 계보).

WHY: 원공시 후보 조회는 (발행사, 제출일) 쌍 배열을 unnest 해 비교한다. 날짜를 문자열로 비교하는
캐스팅이나 배열 바인딩이 실제 드라이버·서버에서 어긋나면 후보가 0건이 되고, 모든 정정공시가 외부
계보(원공시 없음)로 떨어진다 — 가짜 커서로는 잡을 수 없다.
"""
import os
from uuid import uuid4

import pytest

from data_pipeline.steps import assemble_disclosure_events

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="ephemeral PostgreSQL required")


@pytest.fixture
def conn():
    import psycopg
    from psycopg import sql
    schema = "disclosure_lineage_test_" + uuid4().hex
    with psycopg.connect(host=os.environ["E2E_PGHOST"],
                         port=os.environ.get("E2E_PGPORT", "5432"),
                         dbname=os.environ.get("E2E_PGDATABASE", "edge"),
                         user=os.environ.get("E2E_PGUSER", "edge"),
                         password=os.environ.get("E2E_PGPASSWORD", "edge")) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(schema)))
        connection.execute("""
          CREATE TABLE document(document_id text PRIMARY KEY, source_document_id text);
          CREATE TABLE disclosure_document(document_id text PRIMARY KEY, issuer_actor_id text,
              report_date date);
          CREATE TABLE disclosure_fact(fact_id text PRIMARY KEY, document_id text,
              is_current boolean NOT NULL DEFAULT TRUE);
          CREATE TABLE entity(entity_id text PRIMARY KEY, display_name text);
          CREATE TABLE supply_contract_fact(fact_id text PRIMARY KEY, counterparty_raw_name text,
              contract_object_concept_id text, is_correction boolean NOT NULL DEFAULT FALSE,
              contract_date date, correction_target_filed_date date);
        """)
        try:
            yield connection
        finally:
            connection.rollback()


def _original(conn, n, issuer, report_date, rcept_no, counterparty, contract_date="2026-08-08"):
    conn.execute("INSERT INTO document VALUES (%s, %s)", (f"doc{n}", rcept_no))
    conn.execute("INSERT INTO disclosure_document VALUES (%s, %s, %s)", (f"doc{n}", issuer, report_date))
    conn.execute("INSERT INTO disclosure_fact VALUES (%s, %s)", (f"f{n}", f"doc{n}"))
    conn.execute("INSERT INTO supply_contract_fact VALUES (%s, %s, NULL, FALSE, %s, NULL)",
                 (f"f{n}", counterparty, contract_date))


def test_correction_finds_its_original_on_real_postgres(conn):
    _original(conn, 1, "actor_a", "2026-08-09", "R_A1", "고객사A")
    _original(conn, 2, "actor_a", "2026-08-09", "R_A2", "고객사B")   # 같은 날 다른 계약
    _original(conn, 3, "actor_b", "2026-08-09", "R_B1", "고객사A")   # 다른 발행사
    correction = {"rcept_no": "R_C", "is_correction": True, "issuer_actor_id": "actor_a",
                  "correction_target_filed_date": "2026-08-09", "contract_date": "2026-08-08",
                  "counterparty_raw_name": "고객사B(주)", "contract_object_name": None}
    orphan = {"rcept_no": "R_D", "is_correction": True, "issuer_actor_id": "actor_a",
              "correction_target_filed_date": "2013-08-09", "contract_date": "2013-08-08",
              "counterparty_raw_name": "한국수력원자력(주)", "contract_object_name": None}

    assemble_disclosure_events.resolve_lineage(conn, [correction, orphan])

    assert correction["lineage"] == "rcept=R_A2"
    assert orphan["lineage"] == "external=actor_a|2013-08-09|2013-08-08"
