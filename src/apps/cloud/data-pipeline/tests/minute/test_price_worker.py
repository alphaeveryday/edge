"""Price Worker loop 테스트 (ALPHA-667, 계획 §9 Worker loop 해당분).

의도: 루프가 죽거나 헛돌면 장중 수집이 조용히 멈춘다 — fence 상실 즉시 정지,
window 실패 격리(다음 window 진행), 두 lane 동시 소진(realtime 최신 + recovery
backlog budget), drain 수렴, 재시작 복구를 tick 단위(가상 시계)로 고정한다.
collector 는 주입 계약 — 토스 adapter 는 실측 후 별도.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage
from data_pipeline.minute.artifacts import sha256_bytes
from data_pipeline.minute.commit import GenerationMismatchError, MinuteCommitter
from data_pipeline.minute.fake_collector import FakePriceCollector
from data_pipeline.minute.models import KST, Universe, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.worker import PriceWorker, WorkerConfig

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
UNIVERSE = Universe(
    universe_version="univ-test-v1",
    etf_ids=("500000",),
    constituent_ids=("100000", "100001"),
)
NOW = datetime(2026, 7, 31, 9, 10, tzinfo=KST)  # 앞쪽 window 들이 전부 due


class RecordingCanonicalWriter:
    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    def upsert_tx(self, cur, *, dataset, window_start, records):
        for record in records:
            self.rows[(dataset, window_start, record["unit_id"])] = record
        return len(records)


def build_worker(db, tmp_path, *, scenario=None, worker_id="w1", windows=3):
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
        windows=plan_session_windows(SESSION_DATE)[:windows],
    )
    worker = PriceWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(root=tmp_path),
        collector=FakePriceCollector(scenario or {"scenario": "normal"}, seed=42),
        canonical_writer=RecordingCanonicalWriter(),
        config=WorkerConfig(
            worker_id=worker_id, dataset="price_minute", source="toss", market="KR",
            session_date="2026-07-31", universe=UNIVERSE, run_id="run_t",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
        ),
    )
    return worker, ledger, session_id


def run_until_idle(worker, start, limit=20):
    states = []
    for i in range(limit):
        state = worker.tick(start + timedelta(seconds=i))
        states.append(state)
        if state != "PROCESSED":
            return states
    raise AssertionError(f"IDLE 에 도달하지 못했다: {states}")


class TestHappyPath:
    def test_processes_all_windows_then_idle(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        # tick 당 최대 realtime 1 + recovery budget 2 = 3 — 한 tick 에 전부 처리
        assert run_until_idle(worker, NOW) == ["PROCESSED", "IDLE"]
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}
        assert len(db.jobs) == 3 and len(db.outbox) == 3
        keys = worker.storage.list_keys("")
        assert sum(k.endswith("bars.ndjson") for k in keys) == 3
        assert sum(k.endswith("manifest.json") for k in keys) == 3

    def test_lanes_realtime_newest_plus_recovery_oldest(self, tmp_path):
        # 한 tick = 최신 1(realtime) + 최고령 budget(recovery) — 최신 분이 backlog 에
        # 밀리지 않으면서 hole 도 전진한다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=5)
        assert worker.tick(NOW) == "PROCESSED"
        by_start = {
            w["window_start"].strftime("%H%M"): w["data_status"]
            for w in db.windows.values()
        }
        assert by_start["0904"] == "VALID"   # realtime — 최신
        assert by_start["0900"] == "VALID"   # recovery 1 — 최고령
        assert by_start["0901"] == "VALID"   # recovery 2
        assert by_start["0902"] == "DUE" and by_start["0903"] == "DUE"

    def test_rerun_same_data_is_noop(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        run_until_idle(worker, NOW)
        outbox_before = dict(db.outbox)
        for window in db.windows.values():
            window["data_status"] = "DUE"  # EOD 명시 재수집 흉내
        run_until_idle(worker, NOW + timedelta(minutes=1))
        assert {w["generation"] for w in db.windows.values()} == {1}  # 세대 불변
        assert db.outbox == outbox_before  # 재발행 0

    def test_manifest_checksum_matches_stored_bytes(self, tmp_path):
        # manifest 의 artifact_checksum 은 저장된 bars.ndjson 바이트의 sha256 이어야
        # 한다 — 소비자가 재해시로 검증하는 값이다
        import json
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        run_until_idle(worker, NOW)
        keys = worker.storage.list_keys("")
        [artifact_key] = [k for k in keys if k.endswith("bars.ndjson")]
        [manifest_key] = [k for k in keys if k.endswith("manifest.json")]
        manifest = json.loads(worker.storage.get_bytes(manifest_key))
        assert manifest["artifact_key"] == artifact_key
        assert manifest["artifact_checksum"] == sha256_bytes(
            worker.storage.get_bytes(artifact_key)
        )


class TestFailureIsolation:
    def test_partial_missing_commits_incomplete_and_continues(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(
            db, tmp_path, scenario={"scenario": "x", "missing_unit_ids": ["100000"]}
        )
        run_until_idle(worker, NOW)
        assert {w["data_status"] for w in db.windows.values()} == {"INCOMPLETE"}
        assert all(w["missing_units"] == ["100000"] for w in db.windows.values())

    def test_collector_crash_isolated_to_window(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)

        class ExplodingCollector:
            def __init__(self):
                self.calls = 0

            def collect(self, request, now):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("vendor 500")
                return FakePriceCollector({"scenario": "normal"}, seed=42).collect(request, now)

        worker.collector = ExplodingCollector()
        assert worker.tick(NOW) == "WINDOW_FAILED"  # 크게 기록, 루프는 산다
        # 실패한 window(첫 호출=realtime 최신)는 lease 만료 후 재청구돼 처리된다
        later = NOW + timedelta(seconds=61)
        while worker.tick(later) == "PROCESSED":
            pass
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}

    def test_generation_mismatch_propagates(self, tmp_path):
        # 결정적 예측의 불변식 위반은 window 실패로 위장하지 않고 크게 죽는다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        original = worker._predict_generation
        worker._predict_generation = lambda claim, checksum, units: (
            (99,) + original(claim, checksum, units)[1:]
        )
        with pytest.raises(GenerationMismatchError):
            worker.tick(NOW)


class TestFenceLifecycle:
    def test_duplicate_worker_stopped(self, tmp_path):
        db = FakeMinuteDB()
        first, ledger, session_id = build_worker(db, tmp_path)
        assert first.tick(NOW) == "PROCESSED"
        second, _, _ = build_worker(db, tmp_path, worker_id="w2")
        second.session_id = session_id
        assert second.tick(NOW) == "STOPPED"  # lease 가 살아 있는 동안 fence 획득 불가

    def test_restart_recovers_with_new_fence(self, tmp_path):
        db = FakeMinuteDB()
        first, ledger, session_id = build_worker(db, tmp_path, windows=5)
        first.tick(NOW)  # 3개 처리 후 "죽음"
        later = NOW + timedelta(seconds=301)  # session lease 만료
        replacement, _, _ = build_worker(db, tmp_path, worker_id="w2", windows=5)
        replacement.session_id = session_id
        assert run_until_idle(replacement, later) == ["PROCESSED", "IDLE"]
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}

    def test_fence_loss_stops_on_heartbeat(self, tmp_path):
        db = FakeMinuteDB()
        first, ledger, session_id = build_worker(db, tmp_path, windows=5)
        first.tick(NOW)
        later = NOW + timedelta(seconds=301)
        takeover, _, _ = build_worker(db, tmp_path, worker_id="w2", windows=5)
        takeover.session_id = session_id
        takeover.tick(later)  # fence 교체(token+1)
        # 구 Worker 의 다음 heartbeat 주기 tick — 즉시 정지해야 한다
        assert first.tick(later + timedelta(seconds=1)) == "STOPPED"


class TestDrainAndStop:
    def test_draining_acks_then_drained(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        run_until_idle(worker, NOW)
        ledger.request_drain(session_id=session_id, now=NOW)
        assert worker.tick(NOW + timedelta(seconds=30)) == "DRAINING"  # ack 수행
        assert db.sessions[session_id]["phase"] == "DRAINED"
        assert worker.tick(NOW + timedelta(seconds=31)) == "DRAINED"

    def test_sigterm_stops_without_new_claim(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=5)
        worker.tick(NOW)  # 3개 처리
        worker.request_stop()
        assert worker.tick(NOW + timedelta(seconds=1)) == "STOPPED"
        # 처리 안 된 window 는 그대로 남는다(다음 Worker 가 이어감) — 유실 아님
        remaining = [w for w in db.windows.values() if w["data_status"] == "DUE"]
        assert len(remaining) == 2


class TestCorrection:
    def test_late_correction_new_generation_and_event(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        run_until_idle(worker, NOW)
        assert len(db.outbox) == 1
        db.windows[next(iter(db.windows))]["data_status"] = "DUE"  # EOD 재수집 지시 흉내
        worker.collector = FakePriceCollector(
            {"scenario": "corr", "generation": 2,
             "correction": {"unit_ids": ["100000"], "close_delta": 7}},
            seed=42,
        )
        assert worker.tick(NOW + timedelta(minutes=1)) == "PROCESSED"
        window = next(iter(db.windows.values()))
        assert window["generation"] == 2
        assert len(db.outbox) == 2  # correction event 정확히 1개 추가

    def test_drain_converges_after_failed_window(self, tmp_path):
        # 실패로 CLAIMED 로 남은 window 가 있어도 drain 이 수렴해야 한다 —
        # DRAINING 중 만료 고아 회수 → 처리 → ack
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)

        class OnceExploding:
            def __init__(self):
                self.calls = 0

            def collect(self, request, now):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("vendor 500")
                return FakePriceCollector({"scenario": "normal"}, seed=42).collect(request, now)

        worker.collector = OnceExploding()
        assert worker.tick(NOW) == "WINDOW_FAILED"  # window 는 CLAIMED 로 잔존
        ledger.request_drain(session_id=session_id, now=NOW)
        early = worker.tick(NOW + timedelta(seconds=1))
        assert early == "DRAINING"
        assert db.sessions[session_id]["phase"] == "DRAINING"  # lease 미만료 — ack 거부
        after_lease = NOW + timedelta(seconds=61)
        assert worker.tick(after_lease) == "DRAINING"  # 고아 회수·처리 후 ack 성공
        assert db.sessions[session_id]["phase"] == "DRAINED"
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}

    def test_drain_converges_even_with_persistent_failure(self, tmp_path):
        # 벤더 장애가 지속돼도 drain 은 수렴해야 한다 — 실패 claim 을 DUE 로 반납,
        # 잔여 판정은 EOD QC 소관
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)

        class AlwaysExploding:
            def collect(self, request, now):
                raise RuntimeError("vendor 장기 장애")

        worker.collector = AlwaysExploding()
        assert worker.tick(NOW) == "WINDOW_FAILED"
        ledger.request_drain(session_id=session_id, now=NOW)
        after_lease = NOW + timedelta(seconds=61)
        assert worker.tick(after_lease) == "DRAINING"  # 회수→재실패→반납→ack
        assert db.sessions[session_id]["phase"] == "DRAINED"
        assert {w["data_status"] for w in db.windows.values()} == {"DUE"}  # QC 대상


class TestGracefulHandoff:
    def test_sigterm_releases_lease_for_immediate_takeover(self, tmp_path):
        db = FakeMinuteDB()
        first, ledger, session_id = build_worker(db, tmp_path, windows=5)
        first.tick(NOW)  # 3개 처리
        first.request_stop()
        assert first.tick(NOW + timedelta(seconds=1)) == "STOPPED"
        # 교체 Worker 가 lease 만료를 기다리지 않고 즉시 인계한다
        replacement, _, _ = build_worker(db, tmp_path, worker_id="w2", windows=5)
        replacement.session_id = session_id
        assert replacement.tick(NOW + timedelta(seconds=2)) == "PROCESSED"
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}


class TestWatermarkWiring:
    def test_processing_advances_session_watermarks(self, tmp_path):
        # 커밋만 하고 watermark 를 안 밀면 downstream 이 세션 진행을 관측하지 못한다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        run_until_idle(worker, NOW)
        session = db.sessions[session_id]
        last_end = max(w["window_end"] for w in db.windows.values())
        assert session["processed_through"] == last_end
        assert session["contiguous_complete_through"] == last_end
