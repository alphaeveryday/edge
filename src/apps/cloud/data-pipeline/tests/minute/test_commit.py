"""fenced commit transaction 테스트 (ALPHA-666, 계획 §8 후반부).

의도: canonical/window/job/outbox 가 한 트랜잭션이 아니면 부분 확정이 생긴다 —
event 없는 canonical(분석 누락) 또는 canonical 없는 event(유령 분석). 멱등성
(같은 checksum 재실행 → outbox 0 / correction → 1)이 깨지면 중복 분석이 조용히 돈다.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage, raw_price_minute_artifact_key
from data_pipeline.minute.artifacts import put_immutable, serialize_records
from data_pipeline.minute.commit import (
    CommitRejectedError,
    GenerationMismatchError,
    MinuteCommitter,
    find_orphan_artifacts,
)
from data_pipeline.minute.models import KST, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)
RECORDS = (
    {"unit_id": "100000", "open": 1000, "high": 1010, "low": 995, "close": 1005, "volume": 1},
)


class FakeCanonicalWriter:
    """자연키 멱등 upsert 를 흉내내는 canonical 경계 fake — 같은 cursor 트랜잭션 전제."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}
        self.calls = 0

    def upsert_tx(self, cur, *, dataset, window_start, records):
        self.calls += 1
        for record in records:
            self.rows[(dataset, window_start, record["unit_id"])] = record
        return len(records)


def ready_session():
    db = FakeMinuteDB()
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version="v1", universe_hash="a" * 64,
        windows=plan_session_windows(SESSION_DATE)[:10],
    )
    token = ledger.acquire_worker_fence(
        session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
    )
    claim = ledger.claim_due_window(
        session_id=session_id, worker_id="w1", fence_token=token,
        now=NOW, lease_seconds=60, lane="recovery",
    )
    return db, ledger, session_id, token, claim


def commit_kwargs(session_id, claim, token, *, checksum="c" * 64):
    return dict(
        session_id=session_id, window_start=claim["window_start"],
        worker_id="w1", fence_token=token, claim_token=claim["claim_token"],
        data_status="VALID", expected_unit_count=1, succeeded_unit_count=1,
        failed_unit_count=0, record_count=1, checksum=checksum,
        manifest_uri="operations_archive/m.json", manifest_checksum="d" * 64,
        missing_units=None, stage_timestamps={"collection_started_at": NOW},
        records=RECORDS, dataset="price_minute",
        trigger_schema_version="trig-1", destination="price-analysis-realtime",
        artifact_generation=1,
    )


class TestCommitPriceWindow:
    def test_happy_path_commits_all_in_one_transaction(self):
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        writer = FakeCanonicalWriter()
        before = db.connect_calls
        generation = committer.commit_price_window(
            canonical_writer=writer, **commit_kwargs(session_id, claim, token)
        )
        assert db.connect_calls == before + 1  # 전부 한 트랜잭션(=connect 1회)
        assert generation == 1
        assert len(writer.rows) == 1
        window = db.windows[(session_id, claim["window_start"])]
        assert window["data_status"] == "VALID" and window["generation"] == 1
        assert len(db.jobs) == 1
        [(_, job_id)] = db.jobs.keys()
        assert f"PriceWindowCommitted:{job_id}:0" in db.outbox

    def test_rerun_same_checksum_no_new_outbox(self):
        # 계획 §8: 재실행 같은 checksum → generation 불변, outbox 재발행 없음
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        writer = FakeCanonicalWriter()
        committer.commit_price_window(
            canonical_writer=writer, **commit_kwargs(session_id, claim, token)
        )
        # EOD 명시 재수집 흉내 — 다시 claim 해 같은 checksum 으로 재commit
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        generation = committer.commit_price_window(
            canonical_writer=writer, **commit_kwargs(session_id, reclaim, token)
        )
        assert generation == 1  # 불변
        assert len(db.jobs) == 1 and len(db.outbox) == 1  # 중복 0

    def test_correction_bumps_generation_and_emits_one_event(self):
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        writer = FakeCanonicalWriter()
        committer.commit_price_window(
            canonical_writer=writer, **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token, checksum="e" * 64)
        kwargs["artifact_generation"] = 2  # Worker 는 checksum 변화를 보고 세대를 예상한다
        generation = committer.commit_price_window(canonical_writer=writer, **kwargs)
        assert generation == 2
        assert len(db.jobs) == 2 and len(db.outbox) == 2  # correction event 정확히 1개 추가

    def test_stale_fence_commits_nothing(self):
        # 계획 §8: stale Worker 는 artifact 가 남아도 canonical/outbox commit 불가
        db, ledger, session_id, token, claim = ready_session()
        later = NOW + timedelta(seconds=301)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=300
        )
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        writer = FakeCanonicalWriter()
        with pytest.raises(CommitRejectedError):
            committer.commit_price_window(
                canonical_writer=writer, **commit_kwargs(session_id, claim, token)
            )
        assert writer.rows == {} and db.jobs == {} and db.outbox == {}
        assert db.windows[(session_id, claim["window_start"])]["checksum"] is None

    def test_stale_claim_commits_nothing(self):
        db, ledger, session_id, token, claim = ready_session()
        later = NOW + timedelta(seconds=61)
        ledger.heartbeat(session_id=session_id, fence_token=token, now=later, lease_seconds=300)
        reclaim = ledger.claim_due_window(  # 같은 window 재청구 — 옛 claim 무효화
            session_id=session_id, worker_id="w1", fence_token=token,
            now=later, lease_seconds=60, lane="recovery",
        )
        assert reclaim["claim_token"] != claim["claim_token"]
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        writer = FakeCanonicalWriter()
        with pytest.raises(CommitRejectedError):
            committer.commit_price_window(
                canonical_writer=writer, **commit_kwargs(session_id, claim, token)
            )
        assert writer.rows == {} and db.outbox == {}

    def test_db_commit_then_kill_leaves_outbox_new(self):
        # 계획 §8: DB commit 뒤 process kill → outbox NEW 유지 (Relay 가 나중에 발행)
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            canonical_writer=FakeCanonicalWriter(), **commit_kwargs(session_id, claim, token)
        )
        [event] = db.outbox.values()
        assert event["status"] == "NEW" and event["published_at"] is None


class TestOrphanDetection:
    def test_s3_success_db_failure_detected_as_orphan(self, tmp_path):
        # 계획 §8: S3 성공/DB 실패 → orphan 검출. S3 실패→DB 0 은 순서상 자명하다
        # (commit 은 PUT 뒤에만 호출되고, PUT 실패는 commit 자체가 없다)
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        committed_key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        orphan_key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0901", 1)
        put_immutable(storage, committed_key, serialize_records(list(RECORDS)))
        put_immutable(storage, orphan_key, serialize_records(list(RECORDS)))
        # 09:00 만 DB commit — 09:01 은 PUT 후 죽은 시나리오
        MinuteCommitter(db=_DB, connect_fn=db.connect).commit_price_window(
            canonical_writer=FakeCanonicalWriter(), **commit_kwargs(session_id, claim, token)
        )
        orphans = find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            source="toss", market="KR", session_date="2026-07-31",
        )
        assert orphans == [orphan_key]

    def test_rerun_after_crash_clears_orphan(self, tmp_path):
        # 재claim 실행이 같은 key 를 재사용해 commit 하면 orphan 이 사라진다
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        put_immutable(storage, key, serialize_records(list(RECORDS)))
        assert find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            source="toss", market="KR", session_date="2026-07-31",
        ) == [key]
        MinuteCommitter(db=_DB, connect_fn=db.connect).commit_price_window(
            canonical_writer=FakeCanonicalWriter(), **commit_kwargs(session_id, claim, token)
        )
        assert find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            source="toss", market="KR", session_date="2026-07-31",
        ) == []


class TestGenerationGuard:
    def test_artifact_generation_mismatch_rejected(self):
        # Worker 가 세대 2 로 PUT 했는데 checksum 이 같아 DB 는 1 을 확정 — 어긋난 채
        # 진행하면 manifest_uri/job 세대가 갈려 정상 artifact 가 orphan 으로 오인된다
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        writer = FakeCanonicalWriter()
        committer.commit_price_window(
            canonical_writer=writer, **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token)  # 같은 checksum
        kwargs["artifact_generation"] = 2  # 그런데 세대 2 로 PUT 했다고 주장
        with pytest.raises(GenerationMismatchError):
            committer.commit_price_window(canonical_writer=writer, **kwargs)
        assert len(db.outbox) == 1  # 새 event 없음

    def test_result_status_vocabulary_enforced_in_tx_path(self):
        # _tx 직접 경로도 원장 축 값(DUE/MISSING)을 결과로 위장할 수 없다
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        kwargs = commit_kwargs(session_id, claim, token)
        kwargs["data_status"] = "DUE"
        with pytest.raises(ValueError, match="수집 결과 어휘"):
            committer.commit_price_window(
                canonical_writer=FakeCanonicalWriter(), **kwargs
            )

    def test_midway_failure_propagates_before_job_outbox(self):
        # canonical 실패는 삼켜지지 않고 전파된다 — job/outbox 는 그 뒤라 미생성.
        # (실DB 는 rollback 으로 canonical 도 사라진다 — fake 는 트랜잭션이 없어
        # 그 부분은 검증 못 하는 천장. CI ephemeral DB/스테이징 실측 소관)
        db, ledger, session_id, token, claim = ready_session()

        class ExplodingWriter:
            def upsert_tx(self, cur, *, dataset, window_start, records):
                raise RuntimeError("canonical 장애")

        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        with pytest.raises(RuntimeError, match="canonical 장애"):
            committer.commit_price_window(
                canonical_writer=ExplodingWriter(), **commit_kwargs(session_id, claim, token)
            )
        assert db.jobs == {} and db.outbox == {}


class TestOrphanGenerations:
    def test_prior_generation_artifact_is_not_orphan(self, tmp_path):
        # correction 후 세대 1 artifact 는 immutable 정상 이력 — orphan 이 아니다
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        gen1 = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        gen2 = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 2)
        gen3 = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 3)
        for key in (gen1, gen2, gen3):
            put_immutable(storage, key, serialize_records(list(RECORDS)))
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            canonical_writer=FakeCanonicalWriter(), **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token, checksum="e" * 64)
        kwargs["artifact_generation"] = 2
        committer.commit_price_window(canonical_writer=FakeCanonicalWriter(), **kwargs)
        orphans = find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            source="toss", market="KR", session_date="2026-07-31",
        )
        assert orphans == [gen3]  # 커밋 세대(2)보다 높은 것만 orphan
