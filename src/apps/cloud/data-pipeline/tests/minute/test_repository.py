"""session/window repository 테스트 (ALPHA-662, 계획 §7 2B 해당분).

의도: 이 원장은 실행을 제어한다 — 멱등(재계획 no-op)·경합(claim winner 1)·fence(구
Worker 쓰기 거부)가 깨지면 중복 수집 또는 유실이 조용히 일어난다. 실제 MinuteLedger 를
FakeMinuteDB 위에서 돌려 SQL 경로 그대로 검증한다.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # minutefakes — tests/ 루트의 opsfakes 관례
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.models import KST, plan_session_windows
from data_pipeline.minute.repository import (
    MinuteLedger,
    SessionFinalizedError,
    UniverseConflictError,
)

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
WINDOWS = plan_session_windows(SESSION_DATE)
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)  # 장중 — 앞쪽 window 들이 due


def make_ledger(db):
    return MinuteLedger(db=_DB, connect_fn=db.connect)


def plan(ledger, **overrides):
    args = dict(
        dataset="price_minute",
        source_group="toss",
        session_date=SESSION_DATE,
        universe_version="univ-fixture-v1",
        universe_hash="a" * 64,
        windows=WINDOWS,
    )
    args.update(overrides)
    return ledger.plan_session(**args)


class TestPlanSession:
    def test_replan_is_noop(self):
        # Planner 동시/재기동 2회 → session·window 중복 0 (계획 §7)
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        id1, created1 = plan(ledger)
        id2, created2 = plan(ledger)
        assert created1 is True and created2 is False
        assert id1 == id2  # 결정적 session_id — 재기동이 다른 id 를 만들지 않는다
        assert len(db.sessions) == 1
        assert len(db.windows) == 390

    def test_different_universe_rejected(self):
        # 같은 날짜 non-finalized session 에 다른 universe → 두 번째 생성 거부 (v0.7 10.1)
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        plan(ledger)
        with pytest.raises(UniverseConflictError):
            plan(ledger, universe_version="univ-other", universe_hash="b" * 64)
        assert len(db.sessions) == 1

    def test_replan_refreshes_window_count(self):
        # 재계획이 window 를 더했으면 집계도 실제 행 수를 따라간다 — 아니면 완료 판정이
        # 부족한 기대치로 session 을 조기 완료시킨다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger, windows=WINDOWS[:389])
        assert db.sessions[session_id]["expected_window_count"] == 389
        plan(ledger)  # 전체 390 으로 재계획
        assert db.sessions[session_id]["expected_window_count"] == 390
        assert len(db.windows) == 390

    def test_finalized_session_replan_rejected(self):
        # terminal session 에 DUE window 를 더하면 QC 뒤 유령 작업이 생긴다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        db.sessions[session_id]["phase"] = "FINALIZED"
        with pytest.raises(SessionFinalizedError):
            plan(ledger)

    def test_windows_materialized_upfront(self):
        # 하루치 미리 materialize — 프로세스가 안 떠도 due 시각이 지나면 MISSING 으로
        # 잡을 수 있는 전제. scheduled_at 은 window_end(구간이 닫혀야 bar 가 있다)
        db = FakeMinuteDB()
        make_ledger(db).plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version="v", universe_hash="a" * 64, windows=WINDOWS,
        )
        first = min(db.windows.values(), key=lambda w: w["window_start"])
        assert first["data_status"] == "DUE"
        assert first["scheduled_at"] == first["window_end"]


class TestFence:
    def test_acquire_then_second_worker_blocked(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=60
        )
        assert token == 1
        # lease 가 살아 있는 동안 두 번째 Worker 는 fence 를 못 잡는다
        assert ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=NOW, lease_seconds=60
        ) is None

    def test_expired_lease_taken_over_with_bumped_token(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=60
        )
        later = NOW + timedelta(seconds=61)
        token2 = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=60
        )
        assert token2 == 2  # token 증가가 곧 구 Worker 차단이다

    def test_heartbeat_stale_token_fails(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=1
        )
        later = NOW + timedelta(seconds=2)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=60
        )
        # 구 Worker 의 heartbeat 은 False — 계속 진행하면 안 된다는 신호
        assert ledger.heartbeat(
            session_id=session_id, fence_token=1, now=later, lease_seconds=60
        ) is False
        assert ledger.heartbeat(
            session_id=session_id, fence_token=2, now=later, lease_seconds=60
        ) is True


class TestClaim:
    def _ready(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        return db, ledger, session_id, token

    def test_concurrent_claims_get_distinct_windows(self):
        # 같은 window 의 winner 는 1 — 두 번째 claim 은 다음 window 를 받는다
        db, ledger, session_id, token = self._ready()
        first = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        )
        second = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        )
        assert first["window_start"] != second["window_start"]
        assert first["window_start"] == datetime(2026, 7, 31, 9, 0, tzinfo=KST)

    def test_stale_fence_cannot_claim(self):
        db, ledger, session_id, token = self._ready()
        later = NOW + timedelta(seconds=301)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=300
        )
        assert ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=later, lease_seconds=60,
        ) is None

    def test_lease_expiry_reclaim_same_window(self):
        db, ledger, session_id, token = self._ready()
        first = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        )
        # lease 만료 전엔 같은 window 재청구 불가(다음 window 로 감), 만료 후엔 재청구
        later = NOW + timedelta(seconds=61)
        ledger.heartbeat(session_id=session_id, fence_token=token, now=later, lease_seconds=300)
        reclaimed = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=later, lease_seconds=60,
        )
        assert reclaimed["window_start"] == first["window_start"]
        assert reclaimed["attempt_count"] == 2

    def test_future_windows_not_due(self):
        db, ledger, session_id, token = self._ready()
        opening = datetime(2026, 7, 31, 9, 0, 30, tzinfo=KST)  # 첫 window 마감 전
        assert ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=opening, lease_seconds=60,
        ) is None


class TestRecordOutcome:
    def _claimed(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        claim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        )
        return db, ledger, session_id, token, claim

    def _record(
        self, ledger, session_id, claim, *,
        worker_id="w1", fence_token, status="VALID", checksum="c" * 64, claim_token=None,
    ):
        return ledger.record_window_outcome(
            session_id=session_id, window_start=claim["window_start"],
            worker_id=worker_id, fence_token=fence_token,
            claim_token=claim["claim_token"] if claim_token is None else claim_token,
            data_status=status,
            expected_unit_count=348, succeeded_unit_count=348, failed_unit_count=0,
            record_count=348, checksum=checksum, manifest_uri="memory://m",
            manifest_checksum="d" * 64, missing_units=None,
            stage_timestamps={"collection_started_at": NOW.isoformat()},
        )

    def test_happy_path_records_and_releases(self):
        db, ledger, session_id, token, claim = self._claimed()
        assert self._record(ledger, session_id, claim, fence_token=token) is True
        window = db.windows[(session_id, claim["window_start"])]
        assert window["data_status"] == "VALID"
        assert window["generation"] == 1
        assert window["claimed_by"] is None  # claim 해제

    def test_stale_fence_commit_rejected(self):
        # 구 Worker 가 수집을 끝냈어도 fence 를 뺏겼으면 기록 불가 (계획 §7)
        db, ledger, session_id, token, claim = self._claimed()
        later = NOW + timedelta(seconds=301)
        token2 = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=300
        )
        assert self._record(ledger, session_id, claim, fence_token=token) is False
        window = db.windows[(session_id, claim["window_start"])]
        assert window["data_status"] == "CLAIMED"  # 결과는 오직 새 fence 경로로만
        # 새 Worker 가 만료 claim 을 넘겨받아 기록하는 경로는 살아 있다
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w2", fence_token=token2,
            now=later, lease_seconds=60,
        )
        assert reclaim["window_start"] == claim["window_start"]
        assert self._record(
            ledger, session_id, reclaim, worker_id="w2", fence_token=token2
        ) is True

    def test_non_result_status_rejected(self):
        db, ledger, session_id, token, claim = self._claimed()
        with pytest.raises(ValueError):
            self._record(ledger, session_id, claim, fence_token=token, status="DUE")

    def test_late_write_from_expired_claim_rejected(self):
        # 같은 Worker·같은 fence 라도 만료된 옛 claim 의 늦은 기록은 거부 —
        # claim_token 이 claim 마다 고유해야 잡히는 결함이다
        db, ledger, session_id, token, old_claim = self._claimed()
        later = NOW + timedelta(seconds=61)
        ledger.heartbeat(session_id=session_id, fence_token=token, now=later, lease_seconds=300)
        new_claim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=later, lease_seconds=60,
        )
        assert new_claim["window_start"] == old_claim["window_start"]
        assert new_claim["claim_token"] != old_claim["claim_token"]
        assert self._record(
            ledger, session_id, old_claim, fence_token=token,
            claim_token=old_claim["claim_token"],
        ) is False
        assert self._record(ledger, session_id, new_claim, fence_token=token) is True

    def test_same_checksum_rerun_keeps_generation(self):
        # 값이 같은 재실행은 generation 불변 — "같은 checksum → artifact 재사용" 판정
        # (계획 §8)의 전제. 바뀐 checksum 만 세대를 올린다
        db, ledger, session_id, token, claim = self._claimed()
        assert self._record(ledger, session_id, claim, fence_token=token) is True
        window = db.windows[(session_id, claim["window_start"])]
        assert window["generation"] == 1
        # EOD 명시 재수집 경로를 흉내: window 를 다시 claimable 로 되돌려 재기록
        window["data_status"] = "DUE"
        rerun = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        )
        assert self._record(ledger, session_id, rerun, fence_token=token) is True
        assert window["generation"] == 1  # 같은 checksum — 불변
        window["data_status"] = "DUE"
        corrected = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        )
        assert self._record(
            ledger, session_id, corrected, fence_token=token, checksum="e" * 64
        ) is True
        assert window["generation"] == 2  # correction — 세대 증가
