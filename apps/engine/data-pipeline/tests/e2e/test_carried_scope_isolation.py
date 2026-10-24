"""회수 범위 적재 격리 E2E — 실 Postgres 의 savepoint 의미 검증 (ALPHA-1053).

WHY: 단위 테스트는 `_FakeConn.transaction()` 이라는 **제어 흐름 대역** 위에서 돈다. 그건
"회수 그룹의 예외를 우리가 삼키는가"만 증명하고, **Postgres 가 실제로 그 그룹의 쓰기만
되돌리는가**는 증명하지 못한다 — 중첩 `transaction()` 이 savepoint 가 아니라면(psycopg 계약이
바뀌거나 우리가 최상위에서 열었다면) 첫 오류에 트랜잭션 전체가 abort 되고, 자기 범위 행도
같이 사라지는데 단위 테스트는 그걸 못 본다. ALPHA-1052 에서 "격리했다"를 문 하나만 짚은
테스트로 믿었다가 3라운드째 뒤집힌 이력이 있어, 이 주장만은 실물 위에서 확인한다.

방아쇠는 **길이 초과**다(`event_type_code VARCHAR(120)`). 어휘 밖 코드는 FK 도 CHECK 도 없어
그냥 들어가므로 제약 위반이 못 된다 — 실제로 남는 두 방아쇠(길이 초과·데드락) 중 결정적으로
재현 가능한 쪽을 쓴다.

실행 조건: ``E2E_PGHOST`` + Flyway(cloud 세트) 적용된 ephemeral Postgres. CI e2e job 전용.
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

PUBLISHED_OWN = "2026-07-16"
PUBLISHED_CARRIED = "2026-07-15"
OWN_ARTICLE = "e2e-iso-own"
CARRIED_ARTICLE = "e2e-iso-carried"
OWN_EVENT = "COMPANY.CAPITAL.DIVIDEND_DECISION"
# VARCHAR(120) 초과 — Postgres 가 22001(string_data_right_truncation)로 거절한다.
OVERLONG_EVENT = "X" * 200
SAMSUNG = "삼성전자"

_COLUMNS = ("article_id", "published_at", "title", "input_fingerprint", "doc_class",
            "status", "assertions", "reasons", "ontology_version", "tagger_version",
            "tagged_at")


def _pg_kwargs() -> dict:
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _assertion(event_type: str) -> dict:
    return {
        "event_type_code": event_type, "predicate_code": "DECLARE",
        "arguments": [{"role_code": "ISSUER", "text": SAMSUNG, "entity_id": None}],
        "confidence": 0.9, "completeness": "complete",
    }


def _feature_row(article_id: str, published_date: str, event_type: str) -> dict:
    return {
        "article_id": article_id,
        "published_at": f"{published_date}T09:00:00+09:00",
        "title": f"{SAMSUNG} 배당 결정", "input_fingerprint": "fp", "doc_class": "EVENT",
        "status": "ok", "assertions": json.dumps([_assertion(event_type)]),
        "reasons": "[]", "ontology_version": "ont-1", "tagger_version": "tagging-v1",
        "tagged_at": "2026-07-15T02:00:00+00:00",
    }


def _write_feature(storage, published_date: str, rows: list[dict]) -> str:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data_pipeline.lake import feature_news_assertions_partition

    schema = pa.schema([(c, pa.string()) for c in _COLUMNS])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), buf)
    key = f"{feature_news_assertions_partition('ko', published_date)}/part-00000.parquet"
    storage.put_bytes(key, buf.getvalue())
    return key


def _write_manifest(storage, run_id: str, published_date: str, key: str,
                    article_ids: list[str]) -> None:
    from data_pipeline.lake import feature_run_manifest_key

    storage.put_bytes(feature_run_manifest_key("news_assertions", run_id), json.dumps({
        "run_id": run_id, "producer": "tag_news", "feature_written": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "feature_partitions": [{
            "language": "ko", "published_date": published_date,
            "key": key, "article_ids": sorted(article_ids),
        }],
    }, ensure_ascii=False).encode("utf-8"))


def _seed_documents(conn) -> None:
    """이 스텝이 자연키로 해소하는 document 행 — FK·available_at 의 전제다.

    ⚠️ **먼저 앞선 실행의 주장을 지운다.** 안 지우면 이 테스트는 아무것도 증명하지 못한다 —
    격리가 없어 전부 롤백돼도 이전 실행이 남긴 행이 "자기 범위가 실렸다"를 만족시켜 초록이
    된다. 실제로 그랬다(2026-08-28, savepoint 를 뺀 변이가 통과). 적재는 `ON CONFLICT DO
    NOTHING` 이라 두 번째 실행이 아무 행도 안 만들어도 티가 안 나는 구조다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM assertion_argument WHERE assertion_id IN"
            " (SELECT assertion_id FROM document_assertion WHERE document_id = ANY(%s))",
            ([f"doc_{OWN_ARTICLE}", f"doc_{CARRIED_ARTICLE}"],),
        )
        cur.execute("DELETE FROM document_assertion WHERE document_id = ANY(%s)",
                    ([f"doc_{OWN_ARTICLE}", f"doc_{CARRIED_ARTICLE}"],))
        for article_id, published_date in ((OWN_ARTICLE, PUBLISHED_OWN),
                                           (CARRIED_ARTICLE, PUBLISHED_CARRIED)):
            cur.execute(
                "INSERT INTO document (document_id, document_type, source_code,"
                " source_document_id, title, language_code, published_at, available_at)"
                " VALUES (%s,'NEWS','bigkinds',%s,%s,'ko',%s,%s)"
                " ON CONFLICT (source_code, source_document_id) DO NOTHING",
                (f"doc_{article_id}", article_id, f"{SAMSUNG} 배당 결정",
                 f"{published_date}T09:00:00+09:00", f"{published_date}T09:00:00+09:00"),
            )
            cur.execute(
                "INSERT INTO news_document (document_id) VALUES (%s)"
                " ON CONFLICT (document_id) DO NOTHING",
                (f"doc_{article_id}",),
            )
    conn.commit()


def test_a_poisoned_carried_scope_rolls_back_alone_on_real_postgres(tmp_path):
    """회수 범위의 제약 위반이 **그 범위만** 되돌리고, 자기 범위는 커밋된다.

    격리가 없으면(중첩 트랜잭션이 savepoint 가 아니면) 첫 오류에 트랜잭션 전체가 abort 되어
    `exit 1` + 자기 범위 행도 0건이 된다 — 그게 ALPHA-1053 이 고치는 그 모양이다.
    """
    import psycopg2

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage, run_manifest_consumed_key
    from data_pipeline.steps import load_assertions

    pg = _pg_kwargs()
    conn = psycopg2.connect(**pg)
    try:
        _seed_documents(conn)

        storage = LocalStorage(tmp_path / "lake")
        carried_key = _write_feature(storage, PUBLISHED_CARRIED, [
            _feature_row(CARRIED_ARTICLE, PUBLISHED_CARRIED, OVERLONG_EVENT)])
        own_key = _write_feature(storage, PUBLISHED_OWN, [
            _feature_row(OWN_ARTICLE, PUBLISHED_OWN, OWN_EVENT)])
        _write_manifest(storage, "e2e-iso-T0", PUBLISHED_CARRIED, carried_key,
                        [CARRIED_ARTICLE])          # 미소비 — 회수 대상
        _write_manifest(storage, "e2e-iso-T1", PUBLISHED_OWN, own_key, [OWN_ARTICLE])

        assert load_assertions.run(
            storage, "e2e-iso-load",
            db=DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                        user=pg["user"], password=pg["password"], sslmode="disable"),
            input_run_id="e2e-iso-T1",
        ) == 0, "회수 범위의 실패가 이번 런을 죽였다 — 격리가 적재 단계에 없다"

        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_type_code FROM document_assertion WHERE document_id = %s",
                (f"doc_{OWN_ARTICLE}",))
            assert [r[0] for r in cur.fetchall()] == [OWN_EVENT], "자기 범위가 함께 롤백됐다"
            cur.execute(
                "SELECT count(*) FROM document_assertion WHERE document_id = %s",
                (f"doc_{CARRIED_ARTICLE}",))
            assert cur.fetchone()[0] == 0, "터진 회수 범위가 적재됐다"

        # 소비 마커: 자기 범위만 닫히고, 오염된 회수 범위는 다음 런이 다시 집는다
        assert storage.list_keys(run_manifest_consumed_key(
            "feature", "news_assertions", "e2e-iso-T1", "load_assertions"))
        assert storage.list_keys(run_manifest_consumed_key(
            "feature", "news_assertions", "e2e-iso-T0", "load_assertions")) == []
    finally:
        conn.close()
