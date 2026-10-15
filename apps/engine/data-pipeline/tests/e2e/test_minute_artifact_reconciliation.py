"""ALPHA-1060 T10~T12: 실제 worker 후보와 PG 확정 이력으로 대사·격리 경계를 검증."""

from contextlib import contextmanager
from datetime import timedelta
import json
import os

import pytest

from test_minute_content_recovery import lane, reopen_for_correction, snapshot, winner
from data_pipeline.minute.artifacts import (
    build_content_window_manifest, put_immutable, serialize_manifest, sha256_bytes,
)
from data_pipeline.lake.storage import minute_content_artifact_key, minute_content_manifest_key
from data_pipeline.minute.reconciliation import reconcile_minute_artifacts

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")


def scan(h, **kw):
    return reconcile_minute_artifacts(ledger=h.ledger, storage=h.storage, session_id=h.sid, **kw)


def close(h, phase="DRAINED"):
    # 격리 phase 경계 fixture다. crash 복구/reclaim을 흉내 내는 조작이 아니다.
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_session SET phase=%s WHERE session_id=%s", (phase, h.sid))


def full_plan(h):
    from data_pipeline.minute.models import plan_session_windows

    cfg = h.worker.config
    h.ledger.plan_session(dataset=h.dataset, source_group="alpha1060-content-test",
                          session_date=h.start.date(),
                          universe_version=cfg.universe.universe_version if hasattr(cfg, "universe") else cfg.expected_version,
                          universe_hash=cfg.universe.universe_hash if hasattr(cfg, "universe") else cfg.expected_hash,
                          windows=plan_session_windows(h.start.date(), universe=None, extended_hours=False))


def candidate(h, *, generation=2, artifact_only=False):
    data = b'{"candidate":"not committed"}\n'
    checksum = sha256_bytes(data)
    key = minute_content_artifact_key(h.dataset, "KR", "2026-09-07", h.sid, "0900", checksum)
    put_immutable(h.storage, key, data)
    if artifact_only:
        return [key]
    manifest = build_content_window_manifest(
        dataset=h.dataset, session_id=h.sid, window_start=h.start,
        window_end=h.start + timedelta(minutes=1), generation=generation,
        expected_unit_ids=["unit"], units={"received": ["unit"]},
        artifact_key=key, artifact_checksum=checksum,
    )
    body = serialize_manifest(manifest)
    uri = minute_content_manifest_key(h.dataset, "KR", "2026-09-07", h.sid, "0900",
                                      generation, sha256_bytes(body))
    put_immutable(h.storage, uri, body)
    return [key, uri]


def test_t10_same_generation_loser_and_past_winner_are_distinct(lane):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    first = winner(h, 1)[1][0]
    reopen_for_correction(h)
    h.collector.variant = "B"
    assert h.worker.tick(h.now) == "PROCESSED"
    winner(h, 2)
    losing = candidate(h, generation=2)
    close(h)
    before = snapshot(h)
    objects = {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")}
    report = scan(h, quarantine=True, actor="test-operator", reason="closed session reconciliation")
    assert report["ok"] and report["scan_complete"] and not report["provisional"]
    assert report["quarantine_complete"]
    assert [r["generation"] for r in report["committed_current"]] == [2]
    assert [r["generation"] for r in report["committed_history"]] == [1]
    assert report["committed_history"][0]["artifact_uri"] == first[1]
    assert {r["uri"] for r in report["uncommitted_candidate"]} == set(losing)
    assert len(report["quarantine_records"]) == 2
    for uri in report["quarantine_records"]:
        record = json.loads(h.storage.get_bytes(uri))
        assert record["object_uri"] in losing
        assert record["object_checksum"] == sha256_bytes(objects[record["object_uri"]])
        assert record["actor"] == "test-operator" and record["reason"] == "closed session reconciliation"
    assert snapshot(h) == before  # 대사는 업무 window/history/job/outbox를 쓰지 않는다.
    assert all(h.storage.get_bytes(k) == v for k, v in objects.items())


def test_t11_active_session_is_provisional_and_quarantine_writes_nothing(lane):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    candidate(h)
    keys = h.storage.list_keys("")
    report = scan(h)
    assert report["ok"] and report["provisional"] and report["scan_complete"]
    denied = scan(h, quarantine=True, actor="operator", reason="too early")
    assert not denied["ok"] and denied["errors"] and not denied["quarantine_complete"]
    assert denied["quarantine_records"] == [] and h.storage.list_keys("") == keys


@pytest.mark.parametrize("failure", ["list", "database"])
def test_t11_query_failure_cannot_report_complete_or_quarantine(lane, monkeypatch, failure):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    candidate(h)
    close(h)
    keys = h.storage.list_keys("")
    if failure == "list":
        original = h.storage.list_keys
        calls = []

        def fail_second(prefix):
            calls.append(prefix)
            if len(calls) == 2:
                raise OSError("LIST denied after first prefix")
            return original(prefix)

        monkeypatch.setattr(h.storage, "list_keys", fail_second)
    else:
        @contextmanager
        def broken(db):
            with h.connect(db) as c:
                c.execute("SELECT 1/0")  # 실제 DB 조회 실패
                yield c

        monkeypatch.setattr(h.ledger, "connect_fn", broken)
    result = scan(h, quarantine=True, actor="operator", reason="must fail")
    assert not result["ok"] and not result["scan_complete"] and result["errors"]
    assert result["quarantine_records"] == []
    monkeypatch.undo()
    assert h.storage.list_keys("") == keys


def test_t12_artifact_only_and_unverified_legacy_are_preserved_idempotently(lane):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    partial = candidate(h, artifact_only=True)
    prefix = f"canonical/market_data/{h.dataset}/market=KR/session_date=2026-09-07"
    legacy = prefix + "/window=0900/generation=1/unknown.ndjson"
    h.storage.put_bytes(legacy, b"unverifiable legacy")
    other = minute_content_artifact_key(h.dataset, "KR", "2026-09-07", "another-session", "0900", "a"*64)
    h.storage.put_bytes(other, b"belongs to another session")
    close(h)
    first = scan(h, quarantine=True, actor="operator", reason="closed")
    all_bytes = {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")}
    second = scan(h, quarantine=True, actor="operator", reason="closed")
    assert first == second and first["ok"]
    assert [r["uri"] for r in first["uncommitted_candidate"]] == partial
    assert [r["uri"] for r in first["legacy_unverified"]] == [legacy]
    assert {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")} == all_bytes
    assert not any(other in json.dumps(r) for r in first.values())


def test_a_b_a_history_does_not_quarantine_reused_artifact(lane):
    h = lane
    for variant in ("A", "B", "A"):
        if variant != "A" or h.collector.calls:
            reopen_for_correction(h)
        h.collector.variant = variant
        assert h.worker.tick(h.now) == "PROCESSED"
    close(h)
    result = scan(h, quarantine=True, actor="operator", reason="closed")
    assert result["ok"] and result["uncommitted_candidate"] == []
    assert len(result["committed_current"]) == 1 and len(result["committed_history"]) == 2
    assert result["quarantine_records"] == []


@pytest.mark.parametrize("damage", ["manifest", "artifact", "history_missing", "history_conflict", "candidate"])
def test_integrity_errors_preserve_every_object_and_fail_loud(lane, damage):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    ref = winner(h, 1)[1][0]
    candidate(h)
    close(h)
    if damage == "manifest":
        h.storage.put_bytes(ref[3], b"corrupted manifest")
    elif damage == "artifact":
        h.storage.put_bytes(ref[1], b"corrupted artifact")
    elif damage == "history_missing":
        with h.connect(h.db) as c:
            c.execute("DELETE FROM minute_window_artifact_commit WHERE session_id=%s", (h.sid,))
    elif damage == "history_conflict":
        with h.connect(h.db) as c:
            c.execute("UPDATE minute_window_artifact_commit SET artifact_uri='wrong' WHERE session_id=%s", (h.sid,))
    else:
        h.storage.put_bytes(candidate(h)[0], b"corrupted candidate")
    objects = {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")}
    result = scan(h, quarantine=True, actor="operator", reason="must preserve")
    assert not result["ok"] and result["integrity_error"]
    assert result["quarantine_records"] == []
    assert {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")} == objects


def test_legacy_current_without_manifest_and_older_generation_are_not_candidates(lane):
    h = lane
    h.worker.config.artifact_format = "legacy"
    assert h.worker.tick(h.now) == "PROCESSED"
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_window SET manifest_uri=NULL, manifest_checksum=NULL WHERE session_id=%s", (h.sid,))
    close(h)
    report = scan(h)
    assert report["ok"] and len(report["committed_current"]) == 1
    assert report["committed_history"] == [] and report["uncommitted_candidate"] == []
    assert len(report["legacy_unverified"]) == 1  # 직접 참조가 끊긴 구형 manifest도 보존.


def test_eod_refuses_to_finalize_when_artifact_reconciliation_fails(lane):
    from data_pipeline.minute.eod import SessionQc

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    ref = winner(h, 1)[1][0]
    # 정상 하루 계획으로 확장해 EOD 실패 원인이 대사임을 분리한다.
    full_plan(h)
    close(h)
    original = h.storage.get_bytes(ref[3])
    h.storage.put_bytes(ref[3], b"broken")
    qc = SessionQc(ledger=h.ledger, storage=h.storage)
    bad = qc.run(session_id=h.sid, now=h.start + timedelta(hours=10))
    assert not bad["ok"] and bad["phase"] == "FAILED"
    assert bad["artifact_reconciliation"]["integrity_error"]
    assert any("artifact 대사 실패" in s for s in bad["violations"])

    assert len(bad["violations"]) == 1  # 계획 자체는 정상이다.
    h.storage.put_bytes(ref[3], original)
    good = qc.run(session_id=h.sid, now=h.start + timedelta(hours=10))
    assert good["ok"] and good["phase"] == "FINALIZED"
    assert good["artifact_reconciliation"]["scan_complete"]
    json.dumps(good)  # 실제 qc CLI의 JSON 계약: datetime 객체가 새 보고서에 새면 실패한다.
    h.storage.put_bytes(ref[3], b"broken again")
    reused = qc.run(session_id=h.sid, now=h.start + timedelta(hours=10))
    assert not reused["ok"] and reused["reused"] and reused["phase"] == "FINALIZED"


def test_losing_classification_manifest_cannot_quarantine_shared_winner_bytes(lane):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    ref = winner(h, 1)[1][0]
    manifest = json.loads(h.storage.get_bytes(ref[3]))
    manifest["units"]["missing"] = manifest["units"]["received"]
    manifest["units"]["received"] = []
    data = serialize_manifest(manifest)
    key = minute_content_manifest_key(h.dataset, "KR", "2026-09-07", h.sid, "0900", 1, sha256_bytes(data))
    put_immutable(h.storage, key, data)
    close(h)
    result = scan(h, quarantine=True, actor="operator", reason="classification loser")
    assert result["ok"]
    assert [r["uri"] for r in result["uncommitted_candidate"]] == [key]
    assert result["committed_current"][0]["artifact_uri"] == ref[1]


def test_partial_quarantine_write_retries_without_overwriting_records(lane, monkeypatch):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    candidate(h)
    close(h)
    original = h.storage.put_bytes_if_version
    calls = []

    def fail_second(key, data, version):
        calls.append(key)
        if len(calls) == 2:
            raise OSError("quarantine write failed")
        return original(key, data, version)

    monkeypatch.setattr(h.storage, "put_bytes_if_version", fail_second)
    first = scan(h, quarantine=True, actor="operator", reason="closed")
    assert not first["ok"] and first["scan_complete"] and not first["quarantine_complete"]
    assert len(first["quarantine_records"]) == 1 and first["errors"]
    saved = h.storage.get_bytes(first["quarantine_records"][0])
    monkeypatch.undo()
    second = scan(h, quarantine=True, actor="operator", reason="closed")
    assert second["ok"] and len(second["quarantine_records"]) == 2
    assert h.storage.get_bytes(first["quarantine_records"][0]) == saved


def test_preflight_reads_actual_migration_and_open_session_blockers(lane):
    from data_pipeline.minute.artifact_preflight import _database

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    active = _database(h.ledger)
    assert active["schema"] and not active["sessions_closed"]
    assert any(s["session_id"] == h.sid for s in active["session_blockers"])
    close(h)
    assert not any(s["session_id"] == h.sid for s in _database(h.ledger)["session_blockers"])


@pytest.mark.parametrize("failure", ["current_manifest", "current_artifact", "candidate", "list"])
def test_s3_operational_failure_is_not_an_integrity_verdict(lane, monkeypatch, failure):
    from botocore.exceptions import ClientError

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    ref = winner(h, 1)[1][0]
    losing = candidate(h)
    close(h)
    if failure == "list":
        monkeypatch.setattr(h.storage, "list_keys", lambda prefix: (_ for _ in ()).throw(OSError("LIST timeout")))
    else:
        key = ref[3] if failure == "current_manifest" else ref[1] if failure == "current_artifact" else losing[0]
        original = h.storage.get_bytes

        def denied(uri):
            if uri == key:
                raise ClientError({"Error": {"Code": "AccessDenied", "Message": "test permission failure"}}, "GetObject")
            return original(uri)

        monkeypatch.setattr(h.storage, "get_bytes", denied)
    result = scan(h, quarantine=True, actor="operator", reason="cannot inspect")
    assert not result["ok"] and not result["scan_complete"]
    assert result["errors"] and not result["integrity_error"]
    assert result["quarantine_records"] == []


@pytest.mark.parametrize("failure", ["get", "list"])
def test_eod_infrastructure_failure_keeps_qc_retryable_and_returns_exit_two(lane, monkeypatch, failure):
    from types import SimpleNamespace
    from data_pipeline.minute import eod

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    full_plan(h)
    close(h)
    name = "get_bytes" if failure == "get" else "list_keys"
    monkeypatch.setattr(h.storage, name, lambda key: (_ for _ in ()).throw(TimeoutError("S3 timeout")))
    monkeypatch.setattr("data_pipeline.lake.make_storage", lambda cfg: h.storage)
    monkeypatch.setattr(eod, "MinuteLedger", lambda db: h.ledger)
    assert eod.qc_session_cli(SimpleNamespace(db=h.db, storage=object()), session_id=h.sid) == 2
    assert h.ledger.session_snapshot(session_id=h.sid)["phase"] == "QC_RUNNING"
