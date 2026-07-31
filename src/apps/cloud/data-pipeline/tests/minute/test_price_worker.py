"""Price Worker loop 테스트 (ALPHA-667, 계획 §9 Worker loop 해당분).

의도: 루프가 죽거나 헛돌면 장중 수집이 조용히 멈춘다 — fence 상실 즉시 정지,
window 실패 격리(다음 window 진행), drain 수렴, 재시작 복구를 tick 단위(가상
시계)로 고정한다. collector 는 주입 계약 — 토스 adapter 는 실측 후 별도.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage
from data_pipeline.minute.commit import MinuteCommitter
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


NOW = datetime(2026, 7, 31, 9, 10, tzinfo=KST)  # 3개 window 전부 due


class TestHappyPath:
    def test_processes_all_windows_then_idle(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        states = [worker.tick(NOW + timedelta(seconds=i)) for i in range(4)]
        assert states == ["PROCESSED", "PROCESSED", "PROCESSED", "IDLE"]
        statuses = {w["data_status"] for w in db.windows.values()}
        assert statuses == {"VALID"}
        assert len(db.jobs) == 3 and len(db.outbox) == 3
        # artifact + manifest 가 window 마다 저장됐다
        keys = worker.storage.list_keys("")
        assert sum(k.endswith("bars.ndjson") for k in keys) == 3
        assert sum(k.endswith("manifest.json") for k in keys) == 3

    def test_realtime_first_newest_window(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        worker.tick(NOW)
        # 최신 window(09:02~09:03)가 먼저 처리된다 — 장중 지연이 최신 분을 밀지 않게
        done = [w for w in db.windows.values() if w["data_status"] == "VALID"]
        assert done[0]["window_start"] == datetime(2026, 7, 31, 9, 2, tzinfo=KST)

    def test_rerun_same_data_is_noop(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        while worker.tick(NOW) == "PROCESSED":
            pass
        outbox_before = dict(db.outbox)
        # EOD 명시 재수집 흉내 — 같은 데이터 재처리
        for window in db.windows.values():
            window["data_status"] = "DUE"
        while worker.tick(NOW + timedelta(minutes=1)) == "PROCESSED":
            pass
        assert {w["generation"] for w in db.windows.values()} == {1}  # 세대 불변
        assert db.outbox == outbox_before  # 재발행 0


class TestFailureIsolation:
    def test_partial_missing_commits_incomplete_and_continues(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(
            db, tmp_path, scenario={"scenario": "x", "missing_unit_ids": ["100000"]}
        )
        states = [worker.tick(NOW + timedelta(seconds=i)) for i in range(3)]
        assert states == ["PROCESSED"] * 3  # 일부 unit 실패가 window 진행을 막지 않는다
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
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"  # 다음 window 진행
        # 실패한 window 는 lease 만료 후 재청구돼 처리된다
        later = NOW + timedelta(seconds=61)
        while worker.tick(later) in ("PROCESSED",):
            pass
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}


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
        first, ledger, session_id = build_worker(db, tmp_path)
        first.tick(NOW)  # 1개 처리 후 "죽음"
        later = NOW + timedelta(seconds=301)  # session lease 만료
        replacement, _, _ = build_worker(db, tmp_path, worker_id="w2")
        replacement.session_id = session_id
        states = [replacement.tick(later + timedelta(seconds=i)) for i in range(3)]
        assert states == ["PROCESSED", "PROCESSED", "IDLE"]
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}

    def test_fence_loss_stops_on_heartbeat(self, tmp_path):
        db = FakeMinuteDB()
        first, ledger, session_id = build_worker(db, tmp_path)
        first.tick(NOW)
        later = NOW + timedelta(seconds=301)
        takeover, _, _ = build_worker(db, tmp_path, worker_id="w2")
        takeover.session_id = session_id
        takeover.tick(later)  # fence 교체(token+1)
        # 구 Worker 의 다음 heartbeat 주기 tick — 즉시 정지해야 한다
        assert first.tick(later + timedelta(seconds=1)) == "STOPPED"


class TestDrainAndStop:
    def test_draining_acks_then_drained(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        while worker.tick(NOW) == "PROCESSED":
            pass
        ledger.request_drain(session_id=session_id, now=NOW)
        assert worker.tick(NOW + timedelta(seconds=1)) == "DRAINING"  # ack 수행
        assert db.sessions[session_id]["phase"] == "DRAINED"
        assert worker.tick(NOW + timedelta(seconds=2)) == "DRAINED"

    def test_sigterm_stops_without_new_claim(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        worker.tick(NOW)
        worker.request_stop()
        assert worker.tick(NOW + timedelta(seconds=1)) == "STOPPED"
        # 처리 안 된 window 는 그대로 남는다(다음 Worker 가 이어감) — 유실 아님
        remaining = [w for w in db.windows.values() if w["data_status"] == "DUE"]
        assert len(remaining) == 2


class TestCorrection:
    def test_late_correction_new_generation_and_event(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        assert worker.tick(NOW) == "PROCESSED"
        assert len(db.outbox) == 1
        db.windows[next(iter(db.windows))]["data_status"] = "DUE"  # EOD 재수집 지시 흉내
        corrected = FakePriceCollector(
            {"scenario": "corr", "generation": 2,
             "correction": {"unit_ids": ["100000"], "close_delta": 7}},
            seed=42,
        )
        worker.collector = corrected
        assert worker.tick(NOW + timedelta(minutes=1)) == "PROCESSED"
        window = next(iter(db.windows.values()))
        assert window["generation"] == 2
        assert len(db.outbox) == 2  # correction event 정확히 1개 추가
