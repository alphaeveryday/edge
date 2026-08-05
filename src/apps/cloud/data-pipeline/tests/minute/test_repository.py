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
from data_pipeline.minute.models import (
    FINAL_WINDOW_SETTLE_SEC,
    KST,
    plan_session_windows,
)
from data_pipeline.minute.repository import (
    MinuteLedger,
    SessionFinalizedError,
    UniverseConflictError,
)

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
WINDOWS = plan_session_windows(SESSION_DATE, universe=None)
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

    def test_close_window_row_is_scheduled_after_the_auction(self):
        """마감 window 행이 **원장에** 늦춰져 들어가는지 — `scheduled_at_for` 배선 검증.

        helper 단위 테스트만 두면 INSERT 가 `scheduled_at=window_end` 로 회귀해도
        전부 통과한다(그러면 미완성 봉 커밋 결함이 그대로 돌아온다). 여기서 원장에
        저장된 값을 직접 본다.
        """
        db = FakeMinuteDB()
        make_ledger(db).plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version="v", universe_hash="a" * 64, windows=WINDOWS,
        )
        rows = sorted(db.windows.values(), key=lambda w: w["window_start"])
        close_row = rows[-1]
        assert close_row["window_end"] == datetime(2026, 7, 31, 15, 30, tzinfo=KST)
        settled = close_row["window_end"] + timedelta(seconds=FINAL_WINDOW_SETTLE_SEC)
        # 단일가 접수 구간(15:20~15:30)에 걸친 10개가 전부 마감 뒤로 — 마감 하나만 밀면
        # 나머지 아홉이 벤더 복제 봉을 그대로 실어 5분봉 거래량을 부풀린다
        assert all(w["scheduled_at"] == settled for w in rows[-10:])
        # 나머지 380개는 그대로 window_end — 장중에 지연을 만들면 안 된다
        assert all(w["scheduled_at"] == w["window_end"] for w in rows[:-10])

    def test_news_session_close_window_is_not_delayed(self):
        """같은 plan_session 을 쓰는 뉴스 세션엔 안 건다 — 종가 단일가는 가격 얘기고,
        1분 지연은 realtime 뉴스 추출·조립을 그만큼 늦춘다(Codex P2)."""
        db = FakeMinuteDB()
        make_ledger(db).plan_session(
            dataset="news_minute", source_group="bigkinds", session_date=SESSION_DATE,
            universe_version="v", universe_hash="a" * 64, windows=WINDOWS,
        )
        assert all(w["scheduled_at"] == w["window_end"] for w in db.windows.values())


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
        # realtime 기본 lane 은 최신 due 부터 — NOW=09:05 에 due 인 최신 window 는 09:04
        assert first["window_start"] == datetime(2026, 7, 31, 9, 4, tzinfo=KST)
        assert second["window_start"] == datetime(2026, 7, 31, 9, 3, tzinfo=KST)

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
            now=NOW, lease_seconds=60, lane="recovery",
        )
        # lease 만료 전엔 같은 window 재청구 불가(다음 window 로 감), 만료 후엔 재청구
        later = NOW + timedelta(seconds=61)
        ledger.heartbeat(session_id=session_id, fence_token=token, now=later, lease_seconds=300)
        reclaimed = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=later, lease_seconds=60, lane="recovery",
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
            now=NOW, lease_seconds=60, lane="recovery",
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
            now=later, lease_seconds=60, lane="recovery",
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
            now=later, lease_seconds=60, lane="recovery",
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
            now=NOW, lease_seconds=60, lane="recovery",
        )
        assert self._record(ledger, session_id, rerun, fence_token=token) is True
        assert window["generation"] == 1  # 같은 checksum — 불변
        window["data_status"] = "DUE"
        corrected = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        assert self._record(
            ledger, session_id, corrected, fence_token=token, checksum="e" * 64
        ) is True
        assert window["generation"] == 2  # correction — 세대 증가


class TestWatermarks:
    """계획 §7: window 3 누락에서 processed=5, contiguous=2 — 두 watermark 는 다른 질문에
    답한다(processed=어디까지 기록됐나 / contiguous=어디까지 구멍 없이 완전한가)."""

    def _session_with_outcomes(self, statuses):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger, windows=WINDOWS[:len(statuses)])
        for (start, end), status in zip(WINDOWS, statuses):
            if status is None:
                continue  # 미처리 hole
            window = db.windows[(session_id, start)]
            window["data_status"] = status
        return db, ledger, session_id

    def test_hole_at_window3_processed5_contiguous2(self):
        db, ledger, session_id = self._session_with_outcomes(
            ["VALID", "VALID", None, "VALID", "VALID"]
        )
        processed, contiguous = ledger.advance_watermarks(session_id=session_id)
        assert processed == WINDOWS[4][1]    # 5번째 window 끝 — hole 넘어 전진
        assert contiguous == WINDOWS[1][1]   # 2번째 window 끝 — hole 에서 멈춤
        session = db.sessions[session_id]
        assert session["processed_through"] == processed
        assert session["contiguous_complete_through"] == contiguous

    def test_correction_fills_hole_and_contiguous_advances(self):
        db, ledger, session_id = self._session_with_outcomes(
            ["VALID", "VALID", "INCOMPLETE", "VALID", "VALID"]
        )
        _, contiguous = ledger.advance_watermarks(session_id=session_id)
        assert contiguous == WINDOWS[1][1]  # INCOMPLETE 도 hole 이다
        db.windows[(session_id, WINDOWS[2][0])]["data_status"] = "VALID"  # correction
        _, contiguous = ledger.advance_watermarks(session_id=session_id)
        assert contiguous == WINDOWS[4][1]  # hole 이 메워지면 끝까지 전진

    def test_empty_session_watermarks_none(self):
        db, ledger, session_id = self._session_with_outcomes([None, None])
        processed, contiguous = ledger.advance_watermarks(session_id=session_id)
        assert processed is None and contiguous is None


class TestLanes:
    def test_realtime_picks_newest_recovery_picks_oldest(self):
        # v0.7 7절 — 장중 지연이 최신 분 처리를 밀지 않게 realtime 은 최신부터,
        # recovery 는 가장 오래된 hole 부터 메운다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        late_now = datetime(2026, 7, 31, 9, 10, tzinfo=KST)  # window 10개 due
        newest = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=late_now, lease_seconds=60, lane="realtime",
        )
        oldest = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=late_now, lease_seconds=60, lane="recovery",
        )
        assert newest["window_start"] == datetime(2026, 7, 31, 9, 9, tzinfo=KST)
        assert oldest["window_start"] == datetime(2026, 7, 31, 9, 0, tzinfo=KST)

    def test_unknown_lane_rejected(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        with pytest.raises(ValueError):
            ledger.claim_due_window(
                session_id=session_id, worker_id="w1", fence_token=1,
                now=NOW, lease_seconds=60, lane="bulk",
            )


class TestDrain:
    def _active(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        return db, ledger, session_id, token

    def test_drain_request_then_ack(self):
        db, ledger, session_id, token = self._active()
        assert ledger.request_drain(session_id=session_id, now=NOW) is True
        assert db.sessions[session_id]["phase"] == "DRAINING"
        assert ledger.request_drain(session_id=session_id, now=NOW) is False  # 멱등 no-op
        assert ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW) is True
        assert db.sessions[session_id]["phase"] == "DRAINED"

    def test_stale_fence_ack_rejected(self):
        # 구 Worker 의 ack 가 통과하면 새 Worker 처리 중에 DRAINED 로 넘어가
        # EOD QC 가 이른 snapshot 을 찍는다
        db, ledger, session_id, token = self._active()
        ledger.request_drain(session_id=session_id, now=NOW)
        later = NOW + timedelta(seconds=301)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=300
        )
        assert ledger.ack_drain(session_id=session_id, fence_token=token, now=later) is False
        assert db.sessions[session_id]["phase"] == "DRAINING"

    def test_ack_without_drain_request_is_noop(self):
        db, ledger, session_id, token = self._active()
        assert ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW) is False


class TestDrainBoundary:
    """DRAINED 는 EOD snapshot 경계다 — 그 뒤의 claim·fence 재획득·기록이 뚫리면
    QC 가 찍은 snapshot 과 원장이 어긋난다."""

    def _drained(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        ledger.request_drain(session_id=session_id, now=NOW)
        ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW)
        return db, ledger, session_id, token

    def test_no_claim_after_drained(self):
        db, ledger, session_id, token = self._drained()
        assert ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        ) is None

    def test_no_fence_reacquire_after_drained(self):
        db, ledger, session_id, token = self._drained()
        later = NOW + timedelta(seconds=301)  # lease 만료 뒤에도
        assert ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=60
        ) is None

    def test_no_new_claim_during_draining_but_inflight_record_ok(self):
        # DRAINING: 새 claim 은 금지, in-flight 기록은 허용 — 이게 뒤집히면 drain 이
        # 수렴하지 않거나 마지막 window 가 유실된다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        claim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        ledger.request_drain(session_id=session_id, now=NOW)
        assert ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60,
        ) is None
        assert ledger.record_window_outcome(
            session_id=session_id, window_start=claim["window_start"],
            worker_id="w1", fence_token=token, claim_token=claim["claim_token"],
            data_status="VALID", expected_unit_count=348, succeeded_unit_count=348,
            failed_unit_count=0, record_count=348, checksum="c" * 64,
            manifest_uri="memory://m", manifest_checksum="d" * 64,
            missing_units=None, stage_timestamps={"collection_started_at": NOW},
        ) is True


class TestDrainRecovery:
    def test_draining_reclaims_expired_orphan_but_not_due(self):
        # 죽은 Worker 의 만료 claim 은 DRAINING 중에도 회수돼야 봉인 유실이 없다 —
        # 단 DUE 신규 claim 은 계속 금지(아니면 drain 이 수렴 안 함)
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=1
        )
        orphan = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=1, lane="recovery",
        )
        later = NOW + timedelta(seconds=2)
        token2 = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w2", now=later, lease_seconds=300
        )
        ledger.request_drain(session_id=session_id, now=later)
        reclaimed = ledger.claim_due_window(
            session_id=session_id, worker_id="w2", fence_token=token2,
            now=later, lease_seconds=60, lane="recovery",
        )
        assert reclaimed["window_start"] == orphan["window_start"]  # 고아 회수
        # 회수분 기록 후엔 더 claim 할 게 없어야 한다 (DUE 는 잠김)
        assert ledger.record_window_outcome(
            session_id=session_id, window_start=reclaimed["window_start"],
            worker_id="w2", fence_token=token2, claim_token=reclaimed["claim_token"],
            data_status="VALID", expected_unit_count=348, succeeded_unit_count=348,
            failed_unit_count=0, record_count=348, checksum="c" * 64,
            manifest_uri="memory://m", manifest_checksum="d" * 64,
            missing_units=None, stage_timestamps={"collection_started_at": later},
        ) is True
        assert ledger.claim_due_window(
            session_id=session_id, worker_id="w2", fence_token=token2,
            now=later, lease_seconds=60, lane="recovery",
        ) is None

    def test_ack_refused_while_claimed_windows_remain(self):
        # CLAIMED 잔존 채 DRAINED 봉인 = in-flight 유실
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        claim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        ledger.request_drain(session_id=session_id, now=NOW)
        assert ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW) is False
        ledger.record_window_outcome(
            session_id=session_id, window_start=claim["window_start"],
            worker_id="w1", fence_token=token, claim_token=claim["claim_token"],
            data_status="VALID", expected_unit_count=348, succeeded_unit_count=348,
            failed_unit_count=0, record_count=348, checksum="c" * 64,
            manifest_uri="memory://m", manifest_checksum="d" * 64,
            missing_units=None, stage_timestamps={"collection_started_at": NOW},
        )
        assert ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW) is True

    def test_heartbeat_stops_after_drained(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        ledger.request_drain(session_id=session_id, now=NOW)
        ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW)
        assert ledger.heartbeat(
            session_id=session_id, fence_token=token, now=NOW, lease_seconds=60
        ) is False  # terminal — Worker 정지 신호

    def test_missing_session_watermark_fails_loud(self):
        db = FakeMinuteDB()
        with pytest.raises(ValueError, match="session"):
            make_ledger(db).advance_watermarks(session_id="msn_ghost")

    def test_unissued_token_zero_rejected(self):
        # 발급된 적 없는 token 0 이 기본값 0 과 일치해 통과하면 fence 없는 호출이
        # PLANNED session 을 DRAINED 로 봉인할 수 있다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        ledger.request_drain(session_id=session_id, now=NOW)
        assert ledger.ack_drain(session_id=session_id, fence_token=0, now=NOW) is False
        assert db.sessions[session_id]["phase"] == "DRAINING"

    def test_drained_session_replan_rejected(self):
        # 재계획이 DRAINED snapshot 경계를 우회해 DUE window 를 삽입하면 안 된다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger, windows=WINDOWS[:10])
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        ledger.request_drain(session_id=session_id, now=NOW)
        ledger.ack_drain(session_id=session_id, fence_token=token, now=NOW)
        with pytest.raises(SessionFinalizedError):
            plan(ledger)


class TestClaimChecksum:
    def test_claim_returns_current_checksum_for_generation_prediction(self):
        # Worker 의 세대 예측 재료 — 첫 claim 은 None, 기록 후 재claim 은 그 checksum
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        session_id, _ = plan(ledger)
        token = ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=300
        )
        claim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        assert claim["checksum"] is None
        ledger.record_window_outcome(
            session_id=session_id, window_start=claim["window_start"],
            worker_id="w1", fence_token=token, claim_token=claim["claim_token"],
            data_status="VALID", expected_unit_count=348, succeeded_unit_count=348,
            failed_unit_count=0, record_count=348, checksum="c" * 64,
            manifest_uri="m", manifest_checksum="d" * 64, missing_units=None,
            stage_timestamps={"collection_started_at": NOW},
        )
        db.windows[(session_id, claim["window_start"])]["data_status"] = "DUE"
        reclaim = ledger.claim_due_window(
            session_id=session_id, worker_id="w1", fence_token=token,
            now=NOW, lease_seconds=60, lane="recovery",
        )
        assert reclaim["checksum"] == "c" * 64
