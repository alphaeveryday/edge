"""ALPHA-1060 T1~T9: 세 레인의 실제 PG rollback/lease 재claim과 변경 응답 복구."""

import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")


class ChangingCollector:
    def __init__(self):
        self.variant = "A"
        self.calls = 0
        self.reverse = False

    def collect(self, request, now):
        from data_pipeline.minute.models import CollectionResult, content_checksum

        self.calls += 1
        ids = list(request.unit_ids)
        partial = self.variant in ("missing", "no_trade")
        received = ids[:-1] if partial else ids
        units = {"received": received, "missing": ids[-1:] if self.variant == "missing" else [],
                 "no_trade": ids[-1:] if self.variant == "no_trade" else [], "invalid": []}
        value = "101" if self.variant == "B" else "100"
        records = tuple({"unit_id": u, "ts": request.window_start, "open": "100", "high": "102",
                         "low": "99", "close": value, "volume": "10"}
                        if request.dataset != "etf_inav_minute" else
                        {"unit_id": u, "ts": request.window_start, "nav": value,
                         "market_price": "100", "premium_pct": "0"} for u in received)
        if self.reverse:
            units = {k: list(reversed(v)) for k, v in reversed(list(units.items()))}
        failed = len(units["missing"])
        return CollectionResult(
            status="INCOMPLETE" if failed else "VALID", expected_count=len(ids),
            succeeded_count=len(ids) - failed, failed_count=failed, retry_count=self.calls,
            artifact_uri="pending://artifact", manifest_checksum=content_checksum(units),
            result_checksum=content_checksum(records), watermark_before=None,
            watermark_after=request.window_end, generation=1,
            stage_timestamps={"collection_started_at": now},
        ), records, units


@pytest.fixture(params=["price_minute", "etf_inav_minute", "sector_index_minute"])
def lane(request, tmp_path):
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect
    from data_pipeline.lake import LocalStorage
    from data_pipeline.minute.commit import MinuteCommitter
    from data_pipeline.minute.models import KST, Universe
    from data_pipeline.minute.repository import MinuteLedger
    from data_pipeline.minute.worker import (
        PriceWorker, WorkerConfig, InavWorker, InavWorkerConfig,
        SectorIndexWorker, SectorIndexWorkerConfig,
    )

    ds = request.param
    db = DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
                  name="edge", user="edge", password="edge", sslmode="disable")
    start = datetime(2026, 9, 7, 9, 0, tzinfo=KST)
    universe = Universe(universe_version="content-test", etf_ids=("500000", "500001"),
                        constituent_ids=("100000",))
    ledger = MinuteLedger(db=db)
    sid, _ = ledger.plan_session(
        dataset=ds, source_group="alpha1060-content-test", session_date=start.date(),
        universe_version=universe.universe_version, universe_hash=universe.universe_hash,
        windows=[(start, start + timedelta(minutes=1))],
    )
    cfg = dict(worker_id="w1", dataset=ds, source="kis", market="KR", session_date="2026-09-07",
               run_id="test", lease_seconds=1, recovery_budget_per_tick=0, artifact_format="content_v2")
    if ds == "price_minute":
        cls, config = PriceWorker, WorkerConfig(**cfg, universe=universe, is_backfill=False,
                                                trigger_schema_version="test", destination="test")
    elif ds == "etf_inav_minute":
        cls, config = InavWorker, InavWorkerConfig(**cfg, universe=universe)
    else:
        cls, config = SectorIndexWorker, SectorIndexWorkerConfig(
            **cfg, unit_ids=("1005", "1007"), expected_version=universe.universe_version,
            expected_hash=universe.universe_hash,
        )
    storage, collector = LocalStorage(tmp_path), ChangingCollector()
    worker = cls(session_id=sid, ledger=ledger, committer=MinuteCommitter(db=db),
                 storage=storage, collector=collector, config=config)
    h = SimpleNamespace(db=db, connect=connect, sid=sid, start=start,
                        now=start + timedelta(minutes=1), worker=worker, ledger=ledger,
                        storage=storage, collector=collector, dataset=ds)
    try:
        yield h
    finally:
        with connect(db) as c:
            c.execute("DELETE FROM dataset_commit_outbox WHERE payload->>'session_id'=%s", (sid,))
            c.execute("DELETE FROM price_window_job WHERE session_id=%s", (sid,))
            c.execute("DELETE FROM minute_window_artifact_commit WHERE session_id=%s", (sid,))
            c.execute("DELETE FROM minute_ingestion_window WHERE session_id=%s", (sid,))
            c.execute("DELETE FROM minute_ingestion_session WHERE session_id=%s", (sid,))


def snapshot(h):
    with h.connect(h.db) as c:
        return {
            "window": c.execute("SELECT * FROM minute_ingestion_window WHERE session_id=%s", (h.sid,)).fetchall(),
            "history": c.execute("SELECT * FROM minute_window_artifact_commit WHERE session_id=%s ORDER BY generation", (h.sid,)).fetchall(),
            "jobs": c.execute("SELECT * FROM price_window_job WHERE session_id=%s ORDER BY generation", (h.sid,)).fetchall(),
            "outbox": c.execute("SELECT * FROM dataset_commit_outbox WHERE payload->>'session_id'=%s ORDER BY generation", (h.sid,)).fetchall(),
        }


def winner(h, generation):
    from data_pipeline.minute.artifact_reader import read_window_artifact
    from data_pipeline.minute.artifacts import sha256_bytes

    with h.connect(h.db) as c:
        row = c.execute("SELECT generation,checksum,manifest_uri,manifest_checksum FROM minute_ingestion_window WHERE session_id=%s", (h.sid,)).fetchone()
        assert row[0] == generation
        history = c.execute("SELECT generation,artifact_uri,artifact_checksum,manifest_uri,manifest_checksum,committed_at FROM minute_window_artifact_commit WHERE session_id=%s ORDER BY generation", (h.sid,)).fetchall()
    data = read_window_artifact(h.storage, dataset=h.dataset, market="KR", session_id=h.sid,
                               window_start=h.start, window_end=h.start + timedelta(minutes=1),
                               generation=row[0], checksum=row[1], manifest_uri=row[2], manifest_checksum=row[3])
    assert history[-1][:1] == (generation,)
    assert history[-1][2:5] == row[1:]
    # 현재뿐 아니라 모든 과거 승자의 바이트를 다시 검증한다.
    for _, uri, checksum, manifest_uri, manifest_checksum, committed_at in history:
        assert sha256_bytes(h.storage.get_bytes(uri)) == checksum
        assert sha256_bytes(h.storage.get_bytes(manifest_uri)) == manifest_checksum
        assert committed_at is not None
    state = snapshot(h)
    expected_jobs = len(history) if h.dataset == "price_minute" else 0
    assert len(state["jobs"]) == len(state["outbox"]) == expected_jobs
    return row, history, data


def reopen_for_correction(h):
    """이미 확정된 입력의 정정만 위한 fixture. crash 복구에는 사용하지 않는다."""
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_window SET data_status='DUE' WHERE session_id=%s", (h.sid,))
    h.now += timedelta(seconds=2)


def claim(h):
    assert h.worker._ensure_fence(h.now)
    return h.ledger.claim_due_window(session_id=h.sid, worker_id=h.worker.config.worker_id,
                                     fence_token=h.worker.fence_token, now=h.now, lease_seconds=1)


@pytest.mark.parametrize("initial_format", ["legacy", "content_v2"])
def test_t1_changed_response_recovers_after_real_sql_rollback(lane, monkeypatch, initial_format):
    h = lane
    h.worker.config.artifact_format = initial_format
    original = h.worker.committer._confirm_window_tx
    injected = []

    def fail_once(cur, **kw):
        generation = original(cur, **kw)
        if not injected:
            injected.append(generation)
            cur.execute("SELECT 1/0")  # window+history UPDATE/INSERT 뒤 실제 PG abort
        return generation

    monkeypatch.setattr(h.worker.committer, "_confirm_window_tx", fail_once)
    h.collector.variant = "missing"
    assert h.worker.tick(h.now) == "WINDOW_FAILED"
    with h.connect(h.db) as c:
        assert c.execute("SELECT data_status,generation,checksum,manifest_uri,attempt_count FROM minute_ingestion_window WHERE session_id=%s", (h.sid,)).fetchone() == ("CLAIMED", 0, None, None, 1)
    failed = snapshot(h)
    assert not failed["history"] and not failed["jobs"] and not failed["outbox"]
    old = {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")}
    assert len(old) == 2 and injected == [1]
    h.worker.config.artifact_format = "content_v2"
    h.collector.variant = "A"
    assert h.worker.tick(h.now + timedelta(seconds=2)) == "PROCESSED"
    with h.connect(h.db) as c:
        assert c.execute("SELECT attempt_count FROM minute_ingestion_window WHERE session_id=%s", (h.sid,)).fetchone() == (2,)
    row, history, _ = winner(h, 1)
    assert len(history) == 1 and row[2] not in old
    assert len(h.storage.list_keys("")) == 4
    assert all(h.storage.get_bytes(k) == value for k, value in old.items())


def test_t2_same_content_and_reordered_units_do_not_duplicate(lane):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    first = winner(h, 1)
    before = snapshot(h)
    objects = h.storage.list_keys("")
    reopen_for_correction(h)
    h.collector.reverse = True
    assert h.worker.tick(h.now) == "PROCESSED"
    assert winner(h, 1) == first
    after = snapshot(h)
    assert {k: after[k] for k in ("history", "jobs", "outbox")} == {k: before[k] for k in ("history", "jobs", "outbox")}
    assert h.storage.list_keys("") == objects


def test_t3_unit_classification_change_has_new_generation_same_artifact(lane):
    h = lane
    h.collector.variant = "missing"
    assert h.worker.tick(h.now) == "PROCESSED"
    first = winner(h, 1)
    reopen_for_correction(h)
    h.collector.variant = "no_trade"
    assert h.worker.tick(h.now) == "PROCESSED"
    second = winner(h, 2)
    assert first[2] == second[2] and first[0][1] == second[0][1]
    assert first[0][2:] != second[0][2:]
    assert second[1][0][1] == second[1][1][1]


def test_t4_a_b_a_reuses_bytes_but_preserves_three_commits(lane):
    h = lane
    results = []
    for generation, variant in enumerate(("A", "B", "A"), 1):
        h.collector.variant = variant
        if generation > 1:
            reopen_for_correction(h)
        assert h.worker.tick(h.now) == "PROCESSED"
        results.append(winner(h, generation))
    assert results[0][2] == results[2][2] != results[1][2]
    history = results[-1][1]
    assert [r[0] for r in history] == [1, 2, 3]
    assert history[0][1] == history[2][1] != history[1][1]
    assert len({r[3] for r in history}) == 3


def test_t5_manifest_put_failure_recovers_with_changed_response(lane, monkeypatch):
    h = lane
    original = h.storage.put_bytes_if_version
    failed = []

    def put(key, data, version):
        if key.endswith("manifest.json") and not failed:
            failed.append(key)
            raise OSError("injected manifest PUT failure")
        return original(key, data, version)

    monkeypatch.setattr(h.storage, "put_bytes_if_version", put)
    assert h.worker.tick(h.now) == "WINDOW_FAILED"
    before = snapshot(h)
    assert not before["history"] and not before["jobs"] and not before["outbox"]
    assert len(h.storage.list_keys("")) == 1
    h.collector.variant = "B"
    assert h.worker.tick(h.now + timedelta(seconds=2)) == "PROCESSED"
    winner(h, 1)
    assert len(h.storage.list_keys("")) == 3


@pytest.mark.parametrize("stale_kind", ["claim", "fence"])
def test_t6_late_candidate_cannot_change_winner(lane, monkeypatch, stale_kind):
    from data_pipeline.minute.commit import CommitRejectedError

    h = lane
    original = h.worker._commit
    paused = []

    def pause(old_claim, **kw):
        paused.append((old_claim, kw))
        raise ConnectionError("pause after S3 PUT, before DB commit")

    monkeypatch.setattr(h.worker, "_commit", pause)
    assert h.worker.tick(h.now) == "WINDOW_FAILED"
    monkeypatch.setattr(h.worker, "_commit", original)
    h.collector.variant = "B"
    later = h.now + timedelta(seconds=2 if stale_kind == "claim" else 301)
    if stale_kind == "fence":
        h.worker = replace(h.worker, config=replace(h.worker.config, worker_id="w2"),
                           fence_token=None, _last_heartbeat=None)
    assert h.worker.tick(later) == "PROCESSED"
    current = winner(h, 1)
    before = snapshot(h)
    with pytest.raises(CommitRejectedError):
        original(paused[0][0], **paused[0][1])
    assert snapshot(h) == before and winner(h, 1) == current


def test_t7_lost_commit_response_does_not_collect_or_publish_again(lane):
    h = lane
    lost = []

    @contextmanager
    def lose_response(db):
        with h.connect(db) as c:
            yield c
        if not lost:
            lost.append(True)
            raise ConnectionError("committed, response lost")

    h.worker.committer.connect_fn = lose_response
    assert h.worker.tick(h.now) == "WINDOW_FAILED"
    current = winner(h, 1)
    before = snapshot(h)
    assert h.worker.tick(h.now + timedelta(seconds=2)) == "IDLE"
    assert h.collector.calls == 1 and snapshot(h) == before
    assert winner(h, 1) == current


def test_t8_insert_constraint_failure_rolls_back_entire_transaction(lane, monkeypatch):
    from data_pipeline.minute.jobs import JobLedger

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    winner(h, 1)
    # 이력 INSERT는 세 레인 모두, 가격 outbox INSERT는 가격에만 존재한다.
    for target in (["history", "outbox"] if h.dataset == "price_minute" else ["history"]):
        reopen_for_correction(h)
        h.collector.variant = "B" if target == "history" else "A"
        current_claim = claim(h)
        before = snapshot(h)
        injected = []
        with monkeypatch.context() as patch:
            if target == "history":
                original = h.worker.committer._insert_artifact_commit_tx

                def fail_history(cur, sid, start, artifact):
                    # 이전 세대 no-op을 실제 SQL로 통과시킨 뒤 새 세대 CHECK를 실패시킨다.
                    if artifact.generation > len(before["history"]):
                        injected.append(True)
                        artifact = replace(artifact, artifact_checksum="invalid")
                    return original(cur, sid, start, artifact)

                patch.setattr(h.worker.committer, "_insert_artifact_commit_tx", fail_history)
            else:
                original = JobLedger._insert_outbox_tx

                def fail_outbox(cur, **kw):
                    injected.append(True)
                    return original(cur, **{**kw, "event_type": None})  # 실제 NOT NULL 위반

                patch.setattr(JobLedger, "_insert_outbox_tx", staticmethod(fail_outbox))
            assert h.worker._process(current_claim, h.now) is False
        assert injected and snapshot(h) == before  # claim 시점의 전체 컬럼과 이력/job/outbox가 동일하다
        # crash는 DUE 갱신 없이 실제 lease 재claim으로 재개한다.
        assert h.worker.tick(h.now + timedelta(seconds=2)) == "PROCESSED"
        winner(h, len(before["history"]) + 1)


def test_t9_legacy_same_semantics_preserves_original_coordinates(lane):
    h = lane
    h.worker.config.artifact_format = "legacy"
    assert h.worker.tick(h.now) == "PROCESSED"
    before = snapshot(h)
    assert not before["history"]
    old_objects = h.storage.list_keys("")
    with h.connect(h.db) as c:
        old = c.execute("SELECT generation,checksum,manifest_uri,manifest_checksum FROM minute_ingestion_window WHERE session_id=%s", (h.sid,)).fetchone()
    h.worker.config.artifact_format = "content_v2"
    reopen_for_correction(h)
    assert h.worker.tick(h.now) == "PROCESSED"
    assert winner(h, 1)[0] == old
    after = snapshot(h)
    assert after["jobs"] == before["jobs"] and after["outbox"] == before["outbox"]
    assert h.storage.list_keys("") == old_objects


def test_legacy_writer_rejected_before_collection_in_v2_session(lane):
    from data_pipeline.minute.repository import ArtifactFormatError

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    before = snapshot(h)
    h.worker.config.artifact_format = "legacy"
    with pytest.raises(ArtifactFormatError, match="legacy"):
        h.worker.tick(h.now + timedelta(seconds=2))
    assert h.collector.calls == 1 and snapshot(h) == before


def test_schema_absent_rejects_before_claim_or_collection(lane):
    from data_pipeline.minute.repository import ArtifactFormatError

    h = lane
    before = snapshot(h)
    original_connect = h.ledger.connect_fn
    with h.connect(h.db) as c:
        # 같은 실제 PG transaction 안에서만 이름을 바꿔 미적용 상태를 재현한다.
        # rollback으로 원상 복귀하며 다른 연결의 스키마를 변경하지 않는다.
        c.execute("ALTER TABLE minute_window_artifact_commit RENAME TO alpha1060_hidden_history")

        @contextmanager
        def borrowed(db):
            yield c

        h.ledger.connect_fn = borrowed
        try:
            with pytest.raises(ArtifactFormatError, match="migration"):
                h.worker.tick(h.now)
        finally:
            c.rollback()
            h.ledger.connect_fn = original_connect
    assert h.collector.calls == 0 and not h.storage.list_keys("")
    assert snapshot(h) == before


def test_conflicting_history_rejects_without_overwriting_any_state(lane):
    from data_pipeline.minute.commit import ArtifactCommitConflictError

    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    with h.connect(h.db) as c:
        # 모순된 선행 이력을 명시적으로 만드는 fixture. 실제 writer에는 UPDATE가 없다.
        c.execute("UPDATE minute_window_artifact_commit SET artifact_uri='canonical/contradiction' WHERE session_id=%s", (h.sid,))
    reopen_for_correction(h)
    current_claim = claim(h)
    before = snapshot(h)
    objects = {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")}
    with pytest.raises(ArtifactCommitConflictError):
        h.worker._process(current_claim, h.now)
    assert snapshot(h) == before
    assert {k: h.storage.get_bytes(k) for k in h.storage.list_keys("")} == objects


@pytest.mark.parametrize("damage", ["missing", "checksum"])
def test_unreadable_current_winner_is_not_treated_as_new_data(lane, damage):
    h = lane
    assert h.worker.tick(h.now) == "PROCESSED"
    uri = winner(h, 1)[0][2]
    if damage == "missing":
        (h.storage.root / uri).unlink()
    else:
        h.storage.put_bytes(uri, b"corrupted")
    reopen_for_correction(h)
    h.collector.variant = "B"
    current_claim = claim(h)
    before = snapshot(h)
    keys = h.storage.list_keys("")
    assert h.worker._process(current_claim, h.now) is False
    assert snapshot(h) == before and h.storage.list_keys("") == keys


def test_inflight_legacy_commit_rejected_after_v2_winner(lane, monkeypatch):
    from data_pipeline.minute.repository import ArtifactFormatError

    h = lane
    h.worker.config.artifact_format = "legacy"
    original = h.worker._commit
    paused = []

    def pause(old_claim, **kw):
        paused.append((old_claim, kw))
        raise ConnectionError("legacy PUT completed, DB pending")

    monkeypatch.setattr(h.worker, "_commit", pause)
    assert h.worker.tick(h.now) == "WINDOW_FAILED"
    monkeypatch.setattr(h.worker, "_commit", original)
    h.worker.config.artifact_format = "content_v2"
    h.collector.variant = "B"
    assert h.worker.tick(h.now + timedelta(seconds=2)) == "PROCESSED"
    winner(h, 1)
    before = snapshot(h)
    with pytest.raises(ArtifactFormatError):
        original(paused[0][0], **paused[0][1])
    assert snapshot(h) == before
