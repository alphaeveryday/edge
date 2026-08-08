"""fenced commit transaction 테스트 (ALPHA-666, 계획 §8 후반부).

의도: window/job/outbox 가 한 트랜잭션이 아니면 부분 확정이 생긴다 — event 없는
window 확정(분석 누락) 또는 window 확정 없는 event(유령 분석). 멱등성(같은 checksum
재실행 → outbox 0 / correction → 1)이 깨지면 중복 분석이 조용히 돈다.
가격 canonical 은 S3 artifact 라 이 트랜잭션 밖이다(ALPHA-701) — DB canonical 은 뉴스만.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage, canonical_price_minute_artifact_key
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


def ready_session():
    db = FakeMinuteDB()
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version="v1", universe_hash="a" * 64,
        windows=plan_session_windows(SESSION_DATE, universe=None, extended_hours=True)[:10],
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
        trigger_schema_version="trig-1", destination="price-analysis-realtime",
        artifact_generation=1, emit_outbox=True,
    )


class TestCommitPriceWindow:
    def test_emit_outbox_has_no_default(self):
        """`emit_outbox` 에 기본값을 두지 않는다 — 빠뜨린 호출부는 죽어야 한다.

        docstring 이 이 계약을 선언하는데 잡는 장치가 `WorkerConfig` 쪽에만 있었다.
        기본 True 가 붙으면 두 번째 커밋 경로(EOD 재커밋·복구 툴)가 인자를 빠뜨렸을 때
        백필이 조용히 실시간으로 발행되고, 게이트도 테스트도 아무 신호를 안 낸다.
        """
        import inspect

        parameter = inspect.signature(
            MinuteCommitter.commit_price_window
        ).parameters["emit_outbox"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    def test_emit_outbox_false_skips_only_the_event(self):
        """`emit_outbox=False` 는 발행 event 만 뺀다 — window·job 은 그대로다(ALPHA-863).

        같은 트랜잭션이라 가드를 잘못 걸면 job 까지 사라져 백필이 무엇을 수집했는지
        아무 데도 안 남는다. **셋을 따로 묻는다**: outbox 는 0, job 은 1, window 는 확정.
        """
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        before = db.connect_calls
        generation = committer.commit_price_window(
            **{**commit_kwargs(session_id, claim, token), "emit_outbox": False}
        )
        assert generation == 1
        assert db.outbox == {}
        assert len(db.jobs) == 1
        assert db.windows[(session_id, claim["window_start"])]["data_status"] == "VALID"
        assert db.connect_calls == before + 1  # 여전히 한 트랜잭션

    def test_happy_path_commits_all_in_one_transaction(self):
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        before = db.connect_calls
        generation = committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        assert db.connect_calls == before + 1  # 전부 한 트랜잭션(=connect 1회)
        assert generation == 1
        window = db.windows[(session_id, claim["window_start"])]
        assert window["data_status"] == "VALID" and window["generation"] == 1
        assert len(db.jobs) == 1
        [(_, job_id)] = db.jobs.keys()
        assert f"PriceWindowCommitted:{job_id}:0" in db.outbox

    def test_rerun_same_checksum_no_new_outbox(self):
        # 계획 §8: 재실행 같은 checksum → generation 불변, outbox 재발행 없음
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        # EOD 명시 재수집 흉내 — 다시 claim 해 같은 checksum 으로 재commit
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        generation = committer.commit_price_window(
            **commit_kwargs(session_id, reclaim, token)
        )
        assert generation == 1  # 불변
        assert len(db.jobs) == 1 and len(db.outbox) == 1  # 중복 0

    def test_correction_bumps_generation_and_emits_one_event(self):
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token, checksum="e" * 64)
        kwargs["artifact_generation"] = 2  # Worker 는 checksum 변화를 보고 세대를 예상한다
        generation = committer.commit_price_window(**kwargs)
        assert generation == 2
        assert len(db.jobs) == 2 and len(db.outbox) == 2  # correction event 정확히 1개 추가

    def test_stale_fence_commits_nothing(self):
        # 계획 §8: stale Worker 는 artifact 가 남아도 window/outbox commit 불가
        db, ledger, session_id, token, claim = ready_session()
        later = NOW + timedelta(seconds=301)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=300
        )
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        with pytest.raises(CommitRejectedError):
            committer.commit_price_window(
                **commit_kwargs(session_id, claim, token)
            )
        assert db.jobs == {} and db.outbox == {}
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
        with pytest.raises(CommitRejectedError):
            committer.commit_price_window(
                **commit_kwargs(session_id, claim, token)
            )
        assert db.jobs == {} and db.outbox == {}

    def test_db_commit_then_kill_leaves_outbox_new(self):
        # 계획 §8: DB commit 뒤 process kill → outbox NEW 유지 (Relay 가 나중에 발행)
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        [event] = db.outbox.values()
        assert event["status"] == "NEW" and event["published_at"] is None


class TestOrphanDetection:
    def test_s3_success_db_failure_detected_as_orphan(self, tmp_path):
        # 계획 §8: S3 성공/DB 실패 → orphan 검출. S3 실패→DB 0 은 순서상 자명하다
        # (commit 은 PUT 뒤에만 호출되고, PUT 실패는 commit 자체가 없다)
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        committed_key = canonical_price_minute_artifact_key("KR", "2026-07-31", "0900", 1)
        orphan_key = canonical_price_minute_artifact_key("KR", "2026-07-31", "0901", 1)
        put_immutable(storage, committed_key, serialize_records(list(RECORDS)))
        put_immutable(storage, orphan_key, serialize_records(list(RECORDS)))
        # 09:00 만 DB commit — 09:01 은 PUT 후 죽은 시나리오
        MinuteCommitter(db=_DB, connect_fn=db.connect).commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        orphans = find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            market="KR", session_date="2026-07-31",
        )
        assert orphans == [orphan_key]

    def test_rerun_after_crash_clears_orphan(self, tmp_path):
        # 재claim 실행이 같은 key 를 재사용해 commit 하면 orphan 이 사라진다
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        key = canonical_price_minute_artifact_key("KR", "2026-07-31", "0900", 1)
        put_immutable(storage, key, serialize_records(list(RECORDS)))
        assert find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            market="KR", session_date="2026-07-31",
        ) == [key]
        MinuteCommitter(db=_DB, connect_fn=db.connect).commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        assert find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            market="KR", session_date="2026-07-31",
        ) == []


class TestGenerationGuard:
    def test_artifact_generation_mismatch_rejected(self):
        # Worker 가 세대 2 로 PUT 했는데 checksum 이 같아 DB 는 1 을 확정 — 어긋난 채
        # 진행하면 manifest_uri/job 세대가 갈려 정상 artifact 가 orphan 으로 오인된다.
        # (예외 시 window 갱신의 rollback 은 실DB 트랜잭션 소관 — fake 는 트랜잭션이
        # 없어 여기선 outbox 미발행만 단언한다. CI ephemeral DB/스테이징 실측 천장)
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token)  # 같은 checksum
        kwargs["artifact_generation"] = 2  # 그런데 세대 2 로 PUT 했다고 주장
        with pytest.raises(GenerationMismatchError):
            committer.commit_price_window(**kwargs)
        assert len(db.outbox) == 1  # 새 event 없음

    def test_result_status_vocabulary_enforced_in_tx_path(self):
        # _tx 직접 경로도 원장 축 값(DUE/MISSING)을 결과로 위장할 수 없다
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        kwargs = commit_kwargs(session_id, claim, token)
        kwargs["data_status"] = "DUE"
        with pytest.raises(ValueError, match="수집 결과 어휘"):
            committer.commit_price_window(**kwargs)


class TestOrphanGenerations:
    def test_prior_generation_artifact_is_not_orphan(self, tmp_path):
        # correction 후 세대 1 artifact 는 immutable 정상 이력 — orphan 이 아니다
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        gen1 = canonical_price_minute_artifact_key("KR", "2026-07-31", "0900", 1)
        gen2 = canonical_price_minute_artifact_key("KR", "2026-07-31", "0900", 2)
        gen3 = canonical_price_minute_artifact_key("KR", "2026-07-31", "0900", 3)
        for key in (gen1, gen2, gen3):
            put_immutable(storage, key, serialize_records(list(RECORDS)))
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token, checksum="e" * 64)
        kwargs["artifact_generation"] = 2
        committer.commit_price_window(**kwargs)
        orphans = find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            market="KR", session_date="2026-07-31",
        )
        assert orphans == [gen3]  # 커밋 세대(2)보다 높은 것만 orphan

    def test_malformed_key_listed_not_fatal(self, tmp_path):
        # 형식 밖 키 하나가 스캔을 죽이면 다른 orphan 이 안 보인다 — 나열로 일관 처리
        db, ledger, session_id, token, claim = ready_session()
        storage = LocalStorage(root=tmp_path)
        bad = ("canonical/market_data/price_minute/market=KR/session_date=2026-07-31"
               "/window=0900/generation=abc/bars.ndjson")
        storage.put_bytes(bad, b"junk")
        orphans = find_orphan_artifacts(
            db=_DB, connect_fn=db.connect, storage=storage, session_id=session_id,
            market="KR", session_date="2026-07-31",
        )
        assert orphans == [bad]

    def test_classification_only_correction_bumps_generation(self):
        # records 는 같고 manifest(분류)만 바뀐 정정 — 세대가 안 오르면 같은 manifest
        # key 에 다른 바이트를 PUT 해야 해 불변 계약과 충돌한다
        db, ledger, session_id, token, claim = ready_session()
        committer = MinuteCommitter(db=_DB, connect_fn=db.connect)
        committer.commit_price_window(
            **commit_kwargs(session_id, claim, token)
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        kwargs = commit_kwargs(session_id, reclaim, token)  # records checksum 동일
        kwargs["manifest_checksum"] = "f" * 64              # 분류만 변경
        kwargs["artifact_generation"] = 2
        generation = committer.commit_price_window(**kwargs)
        assert generation == 2
