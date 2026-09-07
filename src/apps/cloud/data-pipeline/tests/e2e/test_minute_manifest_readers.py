"""공통 lake fixture + 실 PostgreSQL 좌표를 가격 소비자·롤업·분석 엔진이 함께 읽는다."""

import io
import os
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")


@pytest.fixture(params=["legacy", "content_v2", "mixed"])
def committed_lake(request, tmp_path):
    import psycopg
    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage, minute_content_artifact_key, minute_content_manifest_key
    from data_pipeline.lake.storage import canonical_price_minute_artifact_key, minute_window_manifest_key
    from data_pipeline.minute.artifacts import (
        build_window_manifest, build_content_window_manifest, serialize_records,
        serialize_manifest, sha256_bytes,
    )
    from data_pipeline.minute.models import KST
    from data_pipeline.minute.repository import MinuteLedger

    db = DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
                  name="edge", user="edge", password="edge", sslmode="disable")
    ledger = MinuteLedger(db=db)
    storage = LocalStorage(tmp_path)
    start = datetime(2026, 9, 7, 9, 0, tzinfo=KST)
    planned = [(start + timedelta(minutes=i), start + timedelta(minutes=i + 1)) for i in range(5)]
    sid, _ = ledger.plan_session(
        dataset="price_minute", source_group="alpha1060-reader-test", session_date=start.date(),
        universe_version="fixture", universe_hash="a" * 64, windows=planned,
    )
    coordinates = []
    with psycopg.connect(host=db.host, port=db.port, dbname=db.name, user=db.user,
                         password=db.password) as conn:
        try:
            for i, (window_start, window_end) in enumerate(planned):
                v2 = request.param == "content_v2" or request.param == "mixed" and i % 2 == 1
                hhmm = window_start.strftime("%H%M")
                body = serialize_records([{
                    "unit_id": "500000", "ts": window_start, "source": "kis",
                    "open": "100", "high": str(101 + i), "low": "99",
                    "close": str(100 + i), "volume": "10",
                }])
                checksum = sha256_bytes(body)
                key = (minute_content_artifact_key("price_minute", "KR", "2026-09-07", sid, hhmm, checksum)
                       if v2 else canonical_price_minute_artifact_key("KR", "2026-09-07", hhmm, 1))
                builder = build_content_window_manifest if v2 else build_window_manifest
                manifest = builder(
                    dataset="price_minute", session_id=sid, window_start=window_start,
                    window_end=window_end, generation=1, expected_unit_ids=["500000"],
                    units={"received": ["500000"]}, artifact_key=key, artifact_checksum=checksum,
                )
                manifest_body = serialize_manifest(manifest)
                manifest_checksum = sha256_bytes(manifest_body)
                uri = (minute_content_manifest_key("price_minute", "KR", "2026-09-07", sid, hhmm, 1, manifest_checksum)
                       if v2 else minute_window_manifest_key("price_minute", "kis", "KR", "2026-09-07", hhmm, 1))
                storage.put_bytes(key, body)
                storage.put_bytes(uri, manifest_body)
                conn.execute(
                    "UPDATE minute_ingestion_window SET data_status='VALID', generation=1, checksum=%s,"
                    " manifest_uri=%s, manifest_checksum=%s WHERE session_id=%s AND window_start=%s",
                    (checksum, uri, manifest_checksum, sid, window_start),
                )
                coordinates.append((window_start, window_end, checksum, uri, manifest_checksum))
            conn.commit()
            # 같은 generation 의 다른 후보는 DB 가 가리키지 않으므로 소비하지 않는다.
            storage.put_bytes(minute_content_artifact_key(
                "price_minute", "KR", "2026-09-07", sid, "0900", "f" * 64), b"uncommitted")
            yield db, ledger, storage, conn, sid, coordinates
        finally:
            conn.rollback()
            conn.execute("DELETE FROM minute_ingestion_window WHERE session_id=%s", (sid,))
            conn.execute("DELETE FROM minute_ingestion_session WHERE session_id=%s", (sid,))
            conn.commit()


def _handler(db, storage):
    from data_pipeline.minute.jobs import JobLedger
    from data_pipeline.minute.price_consumer import PriceTriggerHandler

    return PriceTriggerHandler(
        db=db, storage=storage, jobs=JobLedger(db=db), etf_ids=frozenset({"500000"}),
        universe_version="fixture", universe_hash="a" * 64, abs_threshold=Decimal("0.01"),
        revert_threshold=Decimal("0.005"), detection_policy_version="fixture", destination="fixture",
    )


class S3:
    def __init__(self, storage):
        self.storage = storage
        self.requested = []

    def get_object(self, *, Bucket, Key):
        from botocore.exceptions import ClientError

        self.requested.append(Key)
        try:
            return {"Body": io.BytesIO(self.storage.get_bytes(Key))}
        except FileNotFoundError as error:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject") from error


def test_three_readers_agree_on_db_winners(committed_lake):
    import pyarrow.parquet as pq
    from data_pipeline.minute.rollup import rollup_session
    from edge_analysis.adapters.eventstore import EventStore
    from edge_analysis.adapters.lake import LakeReader

    db, ledger, storage, conn, sid, coordinates = committed_lake
    handler = _handler(db, storage)
    for i, (start, end, checksum, uri, manifest_checksum) in enumerate(coordinates):
        # 소비자가 실제 실행하는 SELECT 에서 manifest 좌표가 빠져도 깨져야 한다.
        assert handler._window_checksum(sid, start) == (1, checksum, uri, manifest_checksum, end)
        rows = handler._artifact_rows(
            "2026-09-07", start, 1, expected_checksum=checksum, session_id=sid,
            window_end=end, manifest_uri=uri, manifest_checksum=manifest_checksum,
        )
        assert [(r["unit_id"], r["close"]) for r in rows] == [("500000", str(100 + i))]
    assert handler._first_window(sid, "2026-09-07")[4:6] == coordinates[0][3:5]

    s3 = S3(storage)
    windows = EventStore(conn).fetch_committed_minute_windows(sid, coordinates[0][0], coordinates[-1][1])
    bars = LakeReader(s3, "fixture").load_committed_minute_bars("KR", windows)
    assert [b.close for b in bars] == [Decimal(100 + i) for i in range(5)]
    assert len(s3.requested) == 10  # DB가 가리킨 manifest+artifact 각 5건, 후보 LIST 없음
    output = rollup_session(storage, ledger, dataset="price_minute", session_id=sid,
                            market="KR", session_date="2026-09-07")
    rows = pq.read_table(io.BytesIO(storage.get_bytes(output))).to_pylist()
    assert len(rows) == 1
    assert rows[0]["close"] == 104 and rows[0]["volume"] == 50


@pytest.mark.parametrize("damage", ["manifest_missing", "manifest_hash", "identity", "units", "artifact_hash", "artifact_missing"])
def test_corrupt_winner_fails_all_readers_and_preserves_rollup(committed_lake, damage):
    import json
    from data_pipeline.lake.storage import canonical_price_minute_artifact_key
    from data_pipeline.minute.artifacts import serialize_manifest, sha256_bytes
    from data_pipeline.minute.artifact_reader import ArtifactReadError
    from data_pipeline.minute.consumer import TransientJobError
    from data_pipeline.minute.rollup import rollup_session
    from edge_analysis.adapters.eventstore import EventStore
    from edge_analysis.adapters.lake import LakeReader
    from edge_analysis.config import PipelineError, ReturnsNotReadyError

    db, ledger, storage, conn, sid, coordinates = committed_lake
    output = rollup_session(storage, ledger, dataset="price_minute", session_id=sid,
                            market="KR", session_date="2026-09-07")
    before = storage.get_bytes(output)
    start, end, checksum, uri, manifest_checksum = coordinates[1]
    manifest = json.loads(storage.get_bytes(uri))
    artifact_key = manifest["artifact_key"]
    # v2의 canonical generation 경로에 정상 파일을 둬도 손상 fallback 으로 읽으면 안 된다.
    legacy_key = canonical_price_minute_artifact_key("KR", "2026-09-07", "0901", 1)
    if legacy_key != artifact_key:
        storage.put_bytes(legacy_key, storage.get_bytes(artifact_key))
    if damage == "manifest_missing":
        storage.delete_keys([uri])
    elif damage == "manifest_hash":
        storage.put_bytes(uri, b"tampered")
    elif damage in ("identity", "units"):
        if damage == "identity":
            manifest["session_id"] = "another-session"
        else:
            manifest["units"]["missing"] = ["500000"]
        body = serialize_manifest(manifest)
        storage.put_bytes(uri, body)
        manifest_checksum = sha256_bytes(body)
        conn.execute("UPDATE minute_ingestion_window SET manifest_checksum=%s WHERE session_id=%s AND window_start=%s",
                     (manifest_checksum, sid, start))
        conn.commit()
    elif damage == "artifact_hash":
        storage.put_bytes(artifact_key, b"tampered")
    else:
        storage.delete_keys([artifact_key])
    with pytest.raises(TransientJobError):
        _handler(db, storage)._artifact_rows(
            "2026-09-07", start, 1, expected_checksum=checksum, session_id=sid,
            window_end=end, manifest_uri=uri, manifest_checksum=manifest_checksum,
        )
    windows = EventStore(conn).fetch_committed_minute_windows(sid, coordinates[0][0], coordinates[-1][1])
    with pytest.raises((PipelineError, ReturnsNotReadyError)):
        LakeReader(S3(storage), "fixture").load_committed_minute_bars("KR", windows)
    with pytest.raises(ArtifactReadError):
        rollup_session(storage, ledger, dataset="price_minute", session_id=sid,
                       market="KR", session_date="2026-09-07")
    assert storage.get_bytes(output) == before


def test_legacy_fallback_requires_absent_db_manifest_coordinates(committed_lake):
    import json
    from data_pipeline.lake.storage import canonical_price_minute_artifact_key
    from data_pipeline.minute.rollup import rollup_session
    from edge_analysis.adapters.eventstore import EventStore
    from edge_analysis.adapters.lake import LakeReader

    db, ledger, storage, conn, sid, coordinates = committed_lake
    for start, end, checksum, uri, manifest_checksum in coordinates:
        key = json.loads(storage.get_bytes(uri))["artifact_key"]
        legacy_key = canonical_price_minute_artifact_key("KR", "2026-09-07", start.strftime("%H%M"), 1)
        storage.put_bytes(legacy_key, storage.get_bytes(key))
    conn.execute("UPDATE minute_ingestion_window SET manifest_uri=NULL, manifest_checksum=NULL WHERE session_id=%s", (sid,))
    conn.commit()
    windows = EventStore(conn).fetch_committed_minute_windows(sid, coordinates[0][0], coordinates[-1][1])
    s3 = S3(storage)
    assert len(LakeReader(s3, "fixture").load_committed_minute_bars("KR", windows)) == 5
    assert len(s3.requested) == 5
    start, end, checksum, _, _ = coordinates[0]
    assert len(_handler(db, storage)._artifact_rows(
        "2026-09-07", start, 1, expected_checksum=checksum, session_id=sid,
        window_end=end, manifest_uri=None, manifest_checksum=None,
    )) == 1
    assert rollup_session(storage, ledger, dataset="price_minute", session_id=sid,
                          market="KR", session_date="2026-09-07") is not None
