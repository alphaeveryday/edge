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
# collector 선택은 **오늘인가 지난 거래일인가**로 갈린다(ALPHA-846) — 당일 축 고정용
TODAY = datetime.now(KST).date()
# 시간외(NXT)까지 거래되는 종목이 하나 있는 universe — 세션이 720 window 로 계획된다
UNIVERSE_EXT = Universe(
    universe_version="univ-test-ext-v1",
    etf_ids=("500000",),
    constituent_ids=("100000", "100001"),
    extended_hours_ids=("100000",),
)


def build_worker(db, tmp_path, *, scenario=None, worker_id="w1", windows=3,
                 universe=UNIVERSE, first_window=0, is_backfill=False):
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    planned = plan_session_windows(SESSION_DATE, universe=universe, extended_hours=True)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version=universe.universe_version, universe_hash=universe.universe_hash,
        windows=planned[first_window:first_window + windows],
    )
    worker = PriceWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(root=tmp_path),
        collector=FakePriceCollector(scenario or {"scenario": "normal"}, seed=42),
        config=WorkerConfig(
            worker_id=worker_id, dataset="price_minute", source="toss", market="KR",
            session_date="2026-07-31", universe=universe, run_id="run_t",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            is_backfill=is_backfill,
            # 만료 시나리오를 61초 점프로 검증하는 픽스처라 명시한다 — 운영 기본값은
            # 토스 tick 상한(73초+) 위로 올라갔다(ALPHA-706)
            lease_seconds=60,
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

    def test_worker_config_has_no_default_for_the_backfill_axis(self):
        """`is_backfill` 에 기본값을 두지 않는다 — 빠뜨린 구성 지점은 죽어야 한다.

        기본 False 를 두면 새 구성 지점이 이 축을 빠뜨렸을 때 조용히 실시간으로 판정돼
        과거 봉이 트리거·설명(LLM)을 돌린다. 기본값 있는 옵셔널 인자가 회귀를 숨기는
        형태를 이 트랙에서 이미 밟았다 — 결정을 계약으로 고정한다.
        """
        with pytest.raises(TypeError, match="is_backfill"):
            WorkerConfig(
                worker_id="w1", dataset="price_minute", source="toss", market="KR",
                session_date="2026-07-31", universe=UNIVERSE, run_id="run_t",
                trigger_schema_version="trig-1",
                destination="price-analysis-realtime",
            )

    def test_backfill_session_writes_the_ledger_but_no_realtime_event(self, tmp_path):
        """과거일 백필은 수집·원장은 그대로, **발행만** 안 한다(ALPHA-863).

        outbox 는 곧 `price-analysis-realtime` 이고 그 소비자는 "지금 이 종목이
        움직인다"를 판정한다 — 백필 커밋이 거기로 나가면 며칠 전 봉으로 트리거와
        설명(LLM)이 돈다. 08-08 08-03 재수집에서 실제로 나서 390건을 손으로 DEAD
        격리해 막았고, ALPHA-856(251거래일)이면 그 창이 없다.

        위 happy path 와 **같은 시나리오·같은 window 수**다 — 갈리는 것은 이 축뿐이라
        job 3·artifact 3 을 같이 못박아 "발행만 빠졌다"를 증명한다. 수집까지 멈추면
        백필 자체가 무의미해지므로 그쪽으로 넘어간 회귀도 여기서 죽는다.
        """
        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, is_backfill=True)
        assert run_until_idle(worker, NOW) == ["PROCESSED", "IDLE"]
        assert db.outbox == {}  # Relay 가 집을 것이 없다
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}
        assert len(db.jobs) == 3  # 백필 흔적은 job·window 원장에 그대로 남는다
        keys = worker.storage.list_keys("")
        assert sum(k.endswith("bars.ndjson") for k in keys) == 3

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

    def test_artifact_lives_in_canonical_zone_with_source_column(self, tmp_path):
        # 분봉 canonical 은 S3 단일 정본(ALPHA-701)이고 벤더는 키가 아니라 컬럼이다
        # (ALPHA-705) — 키에 source 가 남으면 소비자가 벤더로 갈라 읽고, 컬럼이 빠지면
        # 벤더 출처가 유실된다
        import json
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        run_until_idle(worker, NOW)
        [artifact_key] = [k for k in worker.storage.list_keys("") if k.endswith("bars.ndjson")]
        assert artifact_key.startswith("canonical/market_data/price_minute/market=KR/")
        assert "source=" not in artifact_key
        rows = worker.storage.get_bytes(artifact_key).decode().splitlines()
        assert rows and all(json.loads(row)["source"] == "toss" for row in rows)


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
        worker._predict_generation = lambda claim, checksum, units, expected: (
            (99,) + original(claim, checksum, units, expected)[1:]
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
        # ack 성공 tick 이 즉시 DRAINED 를 알린다 — 다음 tick 관측에 맡기면 heartbeat
        # 주기 경계에서 DRAINED 세션이 heartbeat 를 거부해 STOPPED 로 샌다(#484 P2)
        assert worker.tick(NOW + timedelta(seconds=30)) == "DRAINED"
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
        assert worker.tick(after_lease) == "DRAINED"  # 고아 회수·처리 후 ack 성공
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
        assert worker.tick(after_lease) == "DRAINED"  # 회수→재실패→반납→ack
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

    def test_artifact_immutability_violation_propagates(self, tmp_path):
        # 같은 세대 key 에 다른 바이트 = 결정성 붕괴 — window 실패로 위장해 영구
        # 재시도하지 않고 크게 죽는다
        from data_pipeline.lake.storage import canonical_price_minute_artifact_key
        from data_pipeline.minute.artifacts import ArtifactImmutabilityError
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        key = canonical_price_minute_artifact_key("KR", "2026-07-31", "0900", 1)
        worker.storage.put_bytes(key, b"corrupted-preexisting")
        with pytest.raises(ArtifactImmutabilityError):
            worker.tick(NOW)


class TestInvalidClassification:
    def test_invalid_units_commit_invalid_outcome(self, tmp_path):
        # collector 가 invalid 로 분류한 unit 은 버려지지 않고 INVALID 결과로 커밋된다
        from data_pipeline.minute.models import CollectionResult

        class InvalidatingCollector:
            def collect(self, request, now):
                result = CollectionResult(
                    status="INVALID", expected_count=3, succeeded_count=2,
                    failed_count=1, retry_count=0, artifact_uri="memory://x",
                    manifest_checksum="a" * 64, result_checksum="b" * 64,
                    watermark_before=None, watermark_after=request.window_end,
                    generation=1,
                    stage_timestamps={"collection_started_at": now},
                )
                records = (
                    {"unit_id": "500000", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
                    {"unit_id": "100001", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
                )
                manifest = {"received": ["100001", "500000"], "no_trade": [],
                            "missing": [], "invalid": ["100000"]}
                return result, records, manifest

        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        worker.collector = InvalidatingCollector()
        assert worker.tick(NOW) == "PROCESSED"
        window = next(iter(db.windows.values()))
        assert window["data_status"] == "INVALID"


class TestTradingHoursUniverse:
    """시간대별 기대 유니버스 (ALPHA-684).

    의도: 15:30 이 거래 마지막인 종목(ETF·지수·비NXT 개별주)을 시간외 window 의 기대
    대상으로 잡으면 그 window 가 영원히 INCOMPLETE 로 남고 매분 재수집된다
    (2026-08-02 dev 실호출 실증 — 19:58 window 에서 069500·001527 이 missing).
    """

    def test_after_hours_window_expects_only_extended_units(self, tmp_path):
        db = FakeMinuteDB()
        # 19:57~19:59 = 720 계획의 마지막 3 window (정규장 밖)
        worker, ledger, session_id = build_worker(
            db, tmp_path, universe=UNIVERSE_EXT, windows=3, first_window=717,
        )
        assert worker.tick(datetime(2026, 7, 31, 20, 0, tzinfo=KST)) == "PROCESSED"
        window = next(w for w in db.windows.values() if w["data_status"] != "DUE")
        # 기대는 시간외 종목 1개뿐 — 3개로 잡으면 나머지 2개가 missing 이라 INCOMPLETE 다
        assert window["expected_unit_count"] == 1
        assert window["data_status"] == "VALID"

    def test_regular_window_expects_whole_universe(self, tmp_path):
        # 같은 universe 라도 정규장 안에서는 전 종목이 기대 대상이다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(
            db, tmp_path, universe=UNIVERSE_EXT, windows=1, first_window=60,  # 09:00
        )
        assert worker.tick(NOW) == "PROCESSED"
        window = next(iter(db.windows.values()))
        assert window["expected_unit_count"] == 3
        assert window["data_status"] == "VALID"

    def test_config_universe_mismatch_stops_processing(self, tmp_path):
        # 원장이 고정한 universe 와 Worker 설정이 갈리면 처리하면 안 된다 — 남의 기대
        # 집합을 내 기준으로 VALID 확정하거나(조용한 누락) 거래시간 밖이 된 window 를
        # 영원히 재청구한다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, universe=UNIVERSE_EXT,
                                                  windows=1, first_window=717)
        worker.config.universe = UNIVERSE  # 배포로 설정만 바뀐 상황
        assert worker.tick(datetime(2026, 7, 31, 20, 0, tzinfo=KST)) == "STOPPED"
        assert all(w["data_status"] == "DUE" for w in db.windows.values())
        # 다음 tick 도 처리하지 않는다 — 설정 불일치는 재시도로 낫지 않는다
        token = db.sessions[session_id]["worker_fencing_token"]
        assert worker.tick(datetime(2026, 7, 31, 20, 1, tzinfo=KST)) == "STOPPED"
        assert all(w["data_status"] == "DUE" for w in db.windows.values())
        # fence 를 쥔 채다 — 반납하고 멈추면 다음 tick 이 재획득해 token 이 매 tick 오른다
        assert db.sessions[session_id]["worker_fencing_token"] == token
        assert worker.fence_token == token

    def test_mismatch_after_stop_still_observes_later_drain(self, tmp_path):
        # 정지를 영구화(stopping)하면 그 뒤 EOD 가 drain 을 걸어도 tick 이 최상단에서
        # 빠져 ack_drain 에 도달하지 못한다 — 그걸 부를 수 있는 건 Worker 뿐이라
        # 세션이 DRAINING 에 영구 고착된다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, universe=UNIVERSE_EXT,
                                                  windows=1, first_window=717)
        start = datetime(2026, 7, 31, 20, 0, tzinfo=KST)
        worker.config.universe = UNIVERSE
        assert worker.tick(start) == "STOPPED"          # ACTIVE + 불일치
        ledger.request_drain(session_id=session_id, now=start)   # 그 **뒤에** drain
        assert worker.tick(start + timedelta(seconds=1)) == "DRAINED"
        assert db.sessions[session_id]["phase"] == "DRAINED"

    def test_universe_mismatch_still_converges_drain(self, tmp_path):
        # 자격이 없어도 drain 은 막지 않는다 — ack_drain 을 부를 수 있는 건 Worker 뿐이라
        # 여기서 멈추면 그 세션이 DRAINING 에 영구 고착되고 EOD 가 시작되지 못한다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, universe=UNIVERSE_EXT,
                                                  windows=1, first_window=717)

        class AlwaysExploding:
            def collect(self, request, now):
                raise RuntimeError("vendor 장기 장애")

        start = datetime(2026, 7, 31, 20, 0, tzinfo=KST)
        worker.collector = AlwaysExploding()
        assert worker.tick(start) == "WINDOW_FAILED"  # window 를 CLAIMED 로 남긴다
        ledger.request_drain(session_id=session_id, now=start)
        worker.config.universe = UNIVERSE  # 배포로 설정만 바뀐 상황
        assert worker.tick(start + timedelta(seconds=61)) == "DRAINED"
        assert db.sessions[session_id]["phase"] == "DRAINED"
        # 처리하지 않고 반납만 했다 — 잔여 판정은 EOD QC 소관
        assert {w["data_status"] for w in db.windows.values()} == {"DUE"}


class TestManifestVocabulary:
    def test_unknown_unit_class_fails_loud(self, tmp_path):
        # 미지 분류를 걸러서 넘기면 manifest 검증이 실행되지 않아, 우리가 이해 못 한
        # 관측이 증거에서 사라진 채 window 가 성공 커밋된다 (Rule 12)
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        inner = FakePriceCollector({"scenario": "normal"}, seed=42)

        class ExtraClassCollector:
            def collect(self, request, now):
                result, records, manifest = inner.collect(request, now)
                return result, records, {**manifest, "deferred": []}

        worker.collector = ExtraClassCollector()
        # 그 window 에 격리된다 — 전파하면 drain 이 release/ack 을 못 거쳐 세션이
        # DRAINING 에 고착되고 교체 Worker 가 같은 window 로 크래시 루프를 돈다
        assert worker.tick(NOW) == "WINDOW_FAILED"
        assert all(w["checksum"] is None for w in db.windows.values())

    def test_result_counts_must_match_manifest_partition(self, tmp_path):
        # 원장 수량(result)과 증거 분할(manifest)이 어긋나면 "missing_units 는 있는데
        # VALID·failed=0" 같은 성공 위장이 커밋된다 — 각자의 validator 는 상대를 모른다
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        inner = FakePriceCollector({"scenario": "normal"}, seed=42)

        class MiscountingCollector:
            def collect(self, request, now):
                result, records, manifest = inner.collect(request, now)
                moved, *rest = manifest["received"]
                return result, records, {**manifest, "received": rest, "missing": [moved]}

        worker.collector = MiscountingCollector()
        assert worker.tick(NOW) == "WINDOW_FAILED"
        assert all(w["checksum"] is None for w in db.windows.values())


class TestPriceWorkerCli:
    """진입점의 fail-loud + bounded 확인 게이트 (ALPHA-706, relay_cli 동형)."""

    def _options(self, **overrides):
        from types import SimpleNamespace
        base = dict(
            client_id="cid", client_secret="secret", source="toss",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            lookback=1, lease_seconds=300, session_lease_seconds=300,
            heartbeat_every_seconds=60, recovery_budget_per_tick=2,
            tick_seconds=0.0,
        )
        return SimpleNamespace(**{**base, **overrides})

    def _settings(self, *, db=None, options=None):
        from types import SimpleNamespace
        return SimpleNamespace(db=db, minute_price_worker=options, storage=None)

    def test_missing_db_fails_loud(self):
        from data_pipeline.minute.worker import price_worker_cli
        with pytest.raises(SystemExit, match="db 설정 없음"):
            price_worker_cli(self._settings(options=self._options()),
                             session_date=None, universe="u.json")

    def test_missing_worker_config_fails_loud(self):
        from data_pipeline.minute.worker import price_worker_cli
        with pytest.raises(SystemExit, match="minute_price_worker 설정 없음"):
            price_worker_cli(self._settings(db=_DB), session_date=None, universe="u.json")

    def test_missing_credentials_fail_loud(self):
        # env 주입 누락은 첫 벤더 호출이 아니라 기동에서 죽어야 배포 시점에 드러난다
        from data_pipeline.minute.worker import price_worker_cli
        with pytest.raises(SystemExit, match="자격증명 없음"):
            price_worker_cli(
                self._settings(db=_DB, options=self._options(client_secret=None)),
                session_date=None, universe="u.json",
            )

    def test_missing_universe_fails_loud(self):
        # universe 없이 뜨면 원장과 다른 기대 집합으로 도는 게 아니라 아예 못 뜬다
        from data_pipeline.minute.worker import price_worker_cli
        with pytest.raises(SystemExit, match="--universe 필요"):
            price_worker_cli(self._settings(db=_DB, options=self._options()),
                             session_date=None, universe=None)

    def test_bad_session_date_fails_loud(self, tmp_path):
        from data_pipeline.minute.worker import price_worker_cli
        with pytest.raises(SystemExit, match="session-date 형식 오류"):
            price_worker_cli(self._settings(db=_DB, options=self._options()),
                             session_date="2026-W01-1", universe="u.json")

    def _universe_file(self, tmp_path):
        import json
        path = tmp_path / "universe.json"
        path.write_text(json.dumps({
            "universe_version": UNIVERSE.universe_version,
            "etf_ids": list(UNIVERSE.etf_ids),
            "constituent_ids": list(UNIVERSE.constituent_ids),
        }), encoding="utf-8")
        return str(path)

    def test_absent_session_fails_loud(self, tmp_path, monkeypatch):
        # 세션 없이 뜨면 fence 획득이 조용히 실패해 빈 폴링만 돈다 — 기동 거부가 맞다
        from data_pipeline.minute.worker import price_worker_cli
        db = FakeMinuteDB()
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteLedger",
                            lambda db=None: MinuteLedger(db=_DB, connect_fn=FakeMinuteDB().connect))
        with pytest.raises(SystemExit, match="세션 없음"):
            price_worker_cli(
                self._settings(db=_DB, options=self._options()),
                session_date="2026-07-31", universe=self._universe_file(tmp_path),
            )

    def test_bounded_run_processes_windows(self, tmp_path, monkeypatch):
        # planner 와 같은 universe 파일 → 결정적 session_id 유도 → window 처리까지
        # 한 번에 확인한다. WINDOW_FAILED 0 이면 exit 0.
        from data_pipeline.minute.worker import price_worker_cli
        db = FakeMinuteDB()
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=UNIVERSE, extended_hours=True)
        ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
            windows=planned[:3],
        )
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteLedger",
                            lambda db=None: MinuteLedger(db=_DB, connect_fn=db_.connect))
        db_ = db
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteCommitter",
                            lambda db=None: MinuteCommitter(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.lake.storage.make_storage",
                            lambda config: LocalStorage(root=tmp_path))
        monkeypatch.setattr("data_pipeline.sources.toss.TossOpenApiClient",
                            lambda client_id, client_secret: object())
        monkeypatch.setattr(
            "data_pipeline.minute.toss_collector.TossPriceCollector",
            lambda client, lookback: FakePriceCollector({"scenario": "normal"}, seed=42),
        )
        code = price_worker_cli(
            self._settings(db=_DB, options=self._options()),
            session_date=SESSION_DATE.isoformat(), universe=self._universe_file(tmp_path),
            max_ticks=2,
        )
        assert code == 0
        assert {w["data_status"] for w in db.windows.values()} == {"VALID"}
        keys = [k for k in LocalStorage(root=tmp_path).list_keys("") if k.endswith("bars.ndjson")]
        assert len(keys) == 3  # canonical 존에 artifact 가 실제로 남았다
        # ⚠️ `SESSION_DATE` 는 **지난 거래일**이라 이 실행은 백필 경로다 — 발행이 없어야
        # 한다(ALPHA-863). 이 한 줄이 CLI 배선(`is_backfill=is_backfill`)을 붙잡는다:
        # 상수 False 로 바꾸면 여기서 죽는다. 수집·적재는 위 세 단언이 그대로 지킨다.
        assert db.outbox == {}


class TestPriceWorkerConfig:
    """설정 검증 — lease 가 tick 최악 소요를 못 덮으면 로드 시점에 죽는다(ALPHA-706)."""

    def _config(self, **overrides):
        from data_pipeline.config.models import MinutePriceWorkerConfig
        base = dict(client_id="c", client_secret="s", trigger_schema_version="trig-1")
        return MinutePriceWorkerConfig(**{**base, **overrides})

    def test_lease_must_cover_worst_tick(self):
        # (1 + budget 3) × 75 = 300 > lease 299 — 뒤쪽 claim 이 처리 중 만료되는 조합.
        # 배포 후 장중에 탈취·commit 거부로 드러나는 대신 로드에서 거부한다
        with pytest.raises(ValueError, match="tick 최악 소요"):
            self._config(lease_seconds=299, recovery_budget_per_tick=3)

    def test_session_lease_must_cover_worst_tick(self):
        with pytest.raises(ValueError, match="fence 가 처리 중 만료"):
            self._config(session_lease_seconds=100, recovery_budget_per_tick=2)

    def test_zero_recovery_budget_rejected(self):
        # budget 0 이면 DRAINING 에서 만료 고아 CLAIMED 를 아무도 회수하지 못해
        # ack_drain 이 영구 거부된다 — 세션이 DRAINING 에 고착
        with pytest.raises(ValueError):
            self._config(recovery_budget_per_tick=0)

    def test_default_is_valid(self):
        cfg = self._config()
        assert (1 + cfg.recovery_budget_per_tick) * 75 <= cfg.lease_seconds

    def test_bounded_all_blocked_is_not_success(self, tmp_path, monkeypatch):
        # 경쟁 fence 에 막혀 한 window 도 못 봤는데 exit 0 이면 확인 게이트가
        # 오배선을 성공으로 판정한다
        from data_pipeline.minute.worker import price_worker_cli
        from types import SimpleNamespace
        db = FakeMinuteDB()
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=UNIVERSE, extended_hours=True)
        session_id, _ = ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
            windows=planned[:1],
        )
        # 다른 Worker 가 fence 를 쥐고 있다(미래까지 유효)
        ledger.acquire_worker_fence(
            session_id=session_id, worker_id="other",
            now=datetime.now(KST) + timedelta(days=1), lease_seconds=3600,
        )
        db_ = db
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteLedger",
                            lambda db=None: MinuteLedger(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteCommitter",
                            lambda db=None: MinuteCommitter(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.lake.storage.make_storage",
                            lambda config: LocalStorage(root=tmp_path))
        monkeypatch.setattr("data_pipeline.sources.toss.TossOpenApiClient",
                            lambda client_id, client_secret: object())
        monkeypatch.setattr(
            "data_pipeline.minute.toss_collector.TossPriceCollector",
            lambda client, lookback: FakePriceCollector({"scenario": "normal"}, seed=42),
        )
        import json as _json
        path = tmp_path / "u.json"
        path.write_text(_json.dumps({
            "universe_version": UNIVERSE.universe_version,
            "etf_ids": list(UNIVERSE.etf_ids),
            "constituent_ids": list(UNIVERSE.constituent_ids),
        }), encoding="utf-8")
        options = SimpleNamespace(
            client_id="cid", client_secret="secret", source="toss",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            lookback=1, lease_seconds=300, session_lease_seconds=300,
            heartbeat_every_seconds=60, recovery_budget_per_tick=2, tick_seconds=0.0,
        )
        settings = SimpleNamespace(db=_DB, minute_price_worker=options, storage=None)
        code = price_worker_cli(
            settings, session_date=SESSION_DATE.isoformat(), universe=str(path),
            max_ticks=2,
        )
        assert code == 1  # 차단만 있고 처리 0 — 성공 위장 금지

    def test_session_lease_must_cover_heartbeat_gap_plus_worst_tick(self):
        # 최악은 "직전 갱신 후 주기 직전에 시작한 tick 이 최악 소요만큼 도는" 경우 —
        # lease 가 (heartbeat 주기 + 최악 tick) 미만이면 처리 중 fence 가 만료된다.
        # ×2 절반 규칙은 이 조합(60 + 150 > 200)을 통과시켰다(라운드 3 반례)
        with pytest.raises(ValueError, match="heartbeat 주기"):
            self._config(session_lease_seconds=200, heartbeat_every_seconds=60,
                         recovery_budget_per_tick=1, lease_seconds=150)

    def test_blank_credentials_rejected_at_startup(self):
        # 공백-only 자격증명은 기동을 통과하면 모든 벤더 인증이 실패한 채 돈다
        from types import SimpleNamespace
        from data_pipeline.minute.worker import price_worker_cli
        options = SimpleNamespace(
            client_id=" ", client_secret="s", source="toss",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            lookback=1, lease_seconds=300, session_lease_seconds=300,
            heartbeat_every_seconds=60, recovery_budget_per_tick=2, tick_seconds=0.0,
        )
        settings = SimpleNamespace(db=_DB, minute_price_worker=options, storage=None)
        with pytest.raises(SystemExit, match="자격증명 없음"):
            price_worker_cli(settings, session_date="2026-07-31", universe="u.json")

    def test_non_price_destination_rejected_at_startup(self):
        # 오타 destination 은 커밋까지 통과하고 Relay 가 전건 DEAD 로 격리한다 —
        # event_id 가 결정적이라 재실행으로 안 고쳐지고 건별 redrive 만 남는다
        from types import SimpleNamespace
        from data_pipeline.minute.worker import price_worker_cli
        options = SimpleNamespace(
            client_id="c", client_secret="s", source="toss",
            trigger_schema_version="trig-1", destination="price-analysis-realtme",
            lookback=1, lease_seconds=300, session_lease_seconds=300,
            heartbeat_every_seconds=60, recovery_budget_per_tick=2, tick_seconds=0.0,
        )
        settings = SimpleNamespace(db=_DB, minute_price_worker=options, storage=None)
        with pytest.raises(SystemExit, match="가격 큐 어휘가 아니다"):
            price_worker_cli(settings, session_date="2026-07-31", universe="u.json")

    def test_drained_exit_does_not_erase_prior_failure(self, monkeypatch, tmp_path):
        # DRAINED 조기 반환이 그전 tick 의 WINDOW_FAILED 를 지우면 실패한 확인
        # 실행이 성공으로 보고된다 — bounded 게이트는 마지막 상태가 아니라 누적으로
        from types import SimpleNamespace
        from data_pipeline.minute.worker import PriceWorker, price_worker_cli
        db = FakeMinuteDB()
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=UNIVERSE, extended_hours=True)
        ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
            windows=planned[:1],
        )
        db_ = db
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteLedger",
                            lambda db=None: MinuteLedger(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteCommitter",
                            lambda db=None: MinuteCommitter(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.lake.storage.make_storage",
                            lambda config: LocalStorage(root=tmp_path))
        monkeypatch.setattr("data_pipeline.sources.toss.TossOpenApiClient",
                            lambda client_id, client_secret: object())
        monkeypatch.setattr(
            "data_pipeline.minute.toss_collector.TossPriceCollector",
            lambda client, lookback: FakePriceCollector({"scenario": "normal"}, seed=42),
        )
        states = iter(["WINDOW_FAILED", "DRAINED"])
        monkeypatch.setattr(PriceWorker, "tick", lambda self, now: next(states))
        import json as _json
        path = tmp_path / "u.json"
        path.write_text(_json.dumps({
            "universe_version": UNIVERSE.universe_version,
            "etf_ids": list(UNIVERSE.etf_ids),
            "constituent_ids": list(UNIVERSE.constituent_ids),
        }), encoding="utf-8")
        options = SimpleNamespace(
            client_id="c", client_secret="s", source="toss",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            lookback=1, lease_seconds=300, session_lease_seconds=300,
            heartbeat_every_seconds=60, recovery_budget_per_tick=2, tick_seconds=0.0,
        )
        settings = SimpleNamespace(db=_DB, minute_price_worker=options, storage=None)
        assert price_worker_cli(
            settings, session_date=SESSION_DATE.isoformat(), universe=str(path),
            max_ticks=5,
        ) == 1

    def test_draining_failure_counts_in_bounded_gate(self, monkeypatch, tmp_path):
        # DRAINING 중의 처리 실패는 반환값에 안 실린다 — 카운터 합산이 없으면
        # ack 후 DRAINED 로 끝난 확인 실행이 실패를 지운 채 exit 0 이 된다
        from types import SimpleNamespace
        from data_pipeline.minute.worker import PriceWorker, price_worker_cli
        db = FakeMinuteDB()
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=UNIVERSE, extended_hours=True)
        ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
            windows=planned[:1],
        )
        db_ = db
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteLedger",
                            lambda db=None: MinuteLedger(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.minute.worker.MinuteCommitter",
                            lambda db=None: MinuteCommitter(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.lake.storage.make_storage",
                            lambda config: LocalStorage(root=tmp_path))
        monkeypatch.setattr("data_pipeline.sources.toss.TossOpenApiClient",
                            lambda client_id, client_secret: object())
        monkeypatch.setattr(
            "data_pipeline.minute.toss_collector.TossPriceCollector",
            lambda client, lookback: FakePriceCollector({"scenario": "normal"}, seed=42),
        )

        def draining_then_drained(self, now):
            self.drain_window_failures = getattr(self, "drain_window_failures", 0) + 1
            if getattr(self, "_t", 0) == 0:
                self._t = 1
                return "DRAINING"
            return "DRAINED"

        monkeypatch.setattr(PriceWorker, "tick", draining_then_drained)
        import json as _json
        path = tmp_path / "u.json"
        path.write_text(_json.dumps({
            "universe_version": UNIVERSE.universe_version,
            "etf_ids": list(UNIVERSE.etf_ids),
            "constituent_ids": list(UNIVERSE.constituent_ids),
        }), encoding="utf-8")
        options = SimpleNamespace(
            client_id="c", client_secret="s", source="toss",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            lookback=1, lease_seconds=300, session_lease_seconds=300,
            heartbeat_every_seconds=60, recovery_budget_per_tick=2, tick_seconds=0.0,
        )
        settings = SimpleNamespace(db=_DB, minute_price_worker=options, storage=None)
        assert price_worker_cli(
            settings, session_date=SESSION_DATE.isoformat(), universe=str(path),
            max_ticks=5,
        ) == 1


class TestFenceRecovery:
    def test_heartbeat_loss_clears_token_for_reacquisition(self, tmp_path):
        # 상실한 token 을 쥔 채 두면 상주 재시도가 같은 stale token 으로 heartbeat 만
        # 반복한다 — 비워야 다음 tick 이 재획득을 시도하고, 경쟁자가 사라지면 인계된다
        db = FakeMinuteDB()
        first, ledger, session_id = build_worker(db, tmp_path, windows=5)
        first.tick(NOW)
        later = NOW + timedelta(seconds=301)
        takeover, _, _ = build_worker(db, tmp_path, worker_id="w2", windows=5)
        takeover.session_id = session_id
        takeover.tick(later)  # fence 교체(token+1)
        assert first.tick(later + timedelta(seconds=1)) == "STOPPED"
        assert first.fence_token is None  # stale token 폐기
        # 경쟁자가 lease 를 반납하면(교체 배포 SIGTERM) 원래 Worker 가 재획득한다 —
        # 남은 window 는 takeover 가 이미 처리해 IDLE 이지만, fence 재획득 자체가 증거다
        takeover.request_stop()
        takeover.tick(later + timedelta(seconds=2))
        assert first.tick(later + timedelta(seconds=3)) == "IDLE"
        assert first.fence_token is not None  # stale 반복이 아니라 재획득했다

    def test_draining_not_ready_counts_as_blocked(self, tmp_path):
        # DRAINING + universe 불일치 — 자격 없음이 카운터로 남아야 bounded 게이트가
        # "자격도 없었는데 성공"으로 판정하지 않는다(반환값은 drain 수렴용 DRAINING 그대로)
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path, windows=1)
        run_until_idle(worker, NOW)
        ledger.request_drain(session_id=session_id, now=NOW)
        worker.config.universe = UNIVERSE_EXT  # 배포로 설정만 바뀐 상황
        # 자격 없음이어도 ack 는 성공한다(잔존 CLAIMED 없음) — DRAINED 로 수렴하되
        # 자격 없음 카운터는 남는다
        assert worker.tick(NOW + timedelta(seconds=1)) == "DRAINED"
        assert getattr(worker, "drain_blocked", 0) >= 1


class TestCollectorSelection:
    """설정 `source` → collector 배선 (ALPHA-735).

    session_id 가 source 로 유도되므로, 여기서 다른 벤더가 끼워지면 **원장이 toss 세션이라
    적힌 자리에 kis 봉이 실린다**. 조용한 폴백 없이 기동에서 죽는지를 고정한다.
    """

    def _config(self, **overrides):
        from data_pipeline.config.models import MinutePriceWorkerConfig
        base = dict(trigger_schema_version="trig-1")
        return MinutePriceWorkerConfig(**{**base, **overrides})

    def test_default_source_is_kis(self):
        # 토스는 초당 5회라 400종/분을 못 맞춘다 — 기본 벤더가 교체된 게 이 티켓이다
        assert self._config().source == "kis"

    def test_kis_source_builds_kis_collector(self):
        from data_pipeline.minute.kis_collector import KisPriceCollector
        from data_pipeline.minute.worker import make_price_collector

        collector, is_backfill = make_price_collector(
            self._config(source="kis", app_key="k", app_secret="s"), session_date=TODAY
        )
        assert isinstance(collector, KisPriceCollector)
        assert is_backfill is False
        # 유량 상한은 간격이다 — 설정이 client 까지 실제로 닿는지 본다(기본 12 req/s)
        assert collector.client.client.min_interval == pytest.approx(0.08)

    def test_past_session_date_builds_historical_kis_client(self):
        """지난 거래일이면 **다른 TR** 이다(ALPHA-846).

        당일 TR 에는 날짜 축이 없어 과거 세션에 물리면 오늘 봉이 오늘 라벨로 돌아오고
        전 window 가 missing 이 된다 — 그때 원장은 "수집했는데 벤더가 안 줬다"로 보인다.
        타입만 보면 상속이라 같은 클래스로도 통과하므로 **TR·날짜 파라미터**까지 본다.
        """
        from data_pipeline.minute.worker import make_price_collector
        from data_pipeline.sources.kis_minute import KisHistoricalMinuteClient

        collector, is_backfill = make_price_collector(
            self._config(source="kis", app_key="k", app_secret="s"),
            session_date=date(2026, 8, 3),
        )
        assert is_backfill is True
        client = collector.client
        assert isinstance(client, KisHistoricalMinuteClient)
        assert client.session_date == date(2026, 8, 3)
        assert client.tr_id == "FHKST03010230"
        assert "FID_INPUT_DATE_1=20260803" in client._url("005930", "153000")

    def test_today_session_date_keeps_the_intraday_client(self):
        # 상주 레인은 그대로여야 한다 — 소급 TR 은 하루를 통째로 캐시하므로 장중에
        # 물리면 그 프로세스가 첫 tick 의 하루를 끝까지 재사용한다(값이 얼어붙는다)
        from data_pipeline.minute.worker import make_price_collector
        from data_pipeline.sources.kis_minute import KisHistoricalMinuteClient

        collector, is_backfill = make_price_collector(
            self._config(source="kis", app_key="k", app_secret="s"), session_date=TODAY
        )
        client = collector.client
        assert is_backfill is False
        assert not isinstance(client, KisHistoricalMinuteClient)
        assert client.tr_id == "FHKST03010200"

    def test_cli_feeds_the_parsed_session_date_to_the_collector(self, monkeypatch):
        """`--session-date` 가 collector 선택까지 **닿는가** — 이 이음매가 TR 을 정한다.

        `make_price_collector` 는 단위로 검증되지만, CLI 가 오늘 날짜를 넘기면 백필이
        당일 TR 로 돌고 오늘 봉이 오늘 라벨로 돌아와 **362종 전건이 missing** 이 된다.
        그때 원장은 "수집은 했는데 벤더가 안 줬다"로 보여 아무도 배선을 의심하지 않는다.

        ⚠️ `SystemExit` 자체는 이 함수의 거의 모든 갈래가 낸다 — 그것만 단언하면 거짓
        초록이다. **대체 함수가 실제로 불렸는지**를 같이 못박는다.
        """
        from types import SimpleNamespace

        from data_pipeline.minute import worker as worker_module

        seen: dict[str, object] = {}

        def capture(options, *, session_date):
            seen["session_date"] = session_date
            raise SystemExit("여기서 멈춘다 — 이 뒤는 DB·S3 가 필요하다")

        from data_pipeline.minute import models as models_module

        monkeypatch.setattr(models_module, "load_universe_uri", lambda _: UNIVERSE)
        monkeypatch.setattr(worker_module, "make_price_collector", capture)
        settings = SimpleNamespace(
            db=DbConfig(password="x"),
            minute_price_worker=self._config(source="kis", app_key="k", app_secret="s"),
        )
        with pytest.raises(SystemExit):
            worker_module.price_worker_cli(
                settings, session_date="2026-08-03", universe="s3://bucket/universe.json"
            )
        assert "session_date" in seen, "make_price_collector 가 불리지 않았다"
        assert seen["session_date"] == date(2026, 8, 3)

    @pytest.mark.parametrize(
        "session_date, expected",
        [(date(2026, 8, 3), True), (TODAY, False)],
        ids=["past-day", "today"],
    )
    def test_cli_wires_the_backfill_verdict_into_the_worker(
        self, monkeypatch, session_date, expected
    ):
        """CLI 가 백필 판정을 **Worker 설정까지** 내려보내는가(ALPHA-863).

        `make_price_collector` 가 옳게 판정하는 것과 그 값이 커밋 경로에 닿는 것은 다른
        사실이다. 이음매가 끊기면(예: `is_backfill=False` 상수) 벤더 선택은 소급인데
        발행은 실시간으로 나가 — 이 티켓이 막으려는 바로 그 상태다.

        **양방향을 다 묻는다**: 과거일에서만 참이면 상수 True 회귀가, 당일에서만 거짓이면
        상수 False 회귀가 남는다. 한쪽만 보는 단언은 갈리는 구간을 안 밟는다.
        """
        from types import SimpleNamespace

        from data_pipeline.minute import models as models_module
        from data_pipeline.minute import worker as worker_module

        seen: dict[str, object] = {}

        def capture(**kwargs):
            seen["config"] = kwargs["config"]
            raise SystemExit("여기서 멈춘다 — 이 뒤는 DB·S3 가 필요하다")

        monkeypatch.setattr(models_module, "load_universe_uri", lambda _: UNIVERSE)
        monkeypatch.setattr(worker_module, "make_price_collector",
                            lambda options, *, session_date: (object(), session_date < TODAY))
        monkeypatch.setattr(worker_module, "MinuteLedger", lambda db=None: SimpleNamespace(
            session_snapshot=lambda **_: {"phase": "ACTIVE"}))
        monkeypatch.setattr(worker_module, "MinuteCommitter", lambda db=None: object())
        monkeypatch.setattr("data_pipeline.lake.storage.make_storage", lambda config: object())
        monkeypatch.setattr(worker_module, "PriceWorker", capture)
        settings = SimpleNamespace(
            db=DbConfig(password="x"), storage=None,
            minute_price_worker=self._config(source="kis", app_key="k", app_secret="s"),
        )
        with pytest.raises(SystemExit):
            worker_module.price_worker_cli(
                settings, session_date=session_date.isoformat(),
                universe="s3://bucket/universe.json",
            )
        assert "config" in seen, "PriceWorker 가 구성되지 않았다"
        assert seen["config"].is_backfill is expected

    def test_backfill_refuses_an_extended_hours_universe_at_startup(self, monkeypatch):
        """시간외 universe 로는 소급 백필이 **구조적으로** 불가하다 — 기동에서 거부한다.

        소급 TR 은 09:00–15:30 만 페이징하는데 시간외 종목이 있으면 세션은 720 window
        로 계획된다. 런타임 거부로 두면 그 330개가 `_process` 의 catch-all 에 window
        실패로 접혀(이 레인은 소스 전역 실패를 전파하지 않는다) 매 tick 재청구·재실패로
        세션이 영영 안 마르고, 상주 진입점은 무한 루프한다.

        ⚠️ `SystemExit` 는 이 함수의 거의 모든 갈래가 낸다 — **문구**를 같이 못박는다.
        """
        from types import SimpleNamespace

        from data_pipeline.minute import worker as worker_module

        from data_pipeline.minute import models as models_module

        monkeypatch.setattr(models_module, "load_universe_uri", lambda _: UNIVERSE_EXT)
        settings = SimpleNamespace(
            db=DbConfig(password="x"),
            minute_price_worker=self._config(source="kis", app_key="k", app_secret="s"),
        )
        with pytest.raises(SystemExit, match="시간외 universe"):
            worker_module.price_worker_cli(
                settings, session_date="2026-08-03", universe="s3://bucket/universe.json"
            )

    def test_extended_hours_refusal_does_not_follow_the_date_to_other_vendors(
        self, monkeypatch
    ):
        """시간외 거부는 **소급 TR 의 사실**이다 — 날짜만 보고 토스까지 막지 않는다.

        백필 판정을 날짜 축으로 통일하면서(ALPHA-863) 이 게이트가 같이 넓어지면, 토스
        과거일 세션이 *"소급 TR 은 09:00–15:30 만 준다"* 는 **거짓 사유**로 기동을
        거부당한다. 토스는 window 끝 시각으로 임의 과거 구간을 받으므로 시간외 window
        가 구조적 결손이 아니다.

        같은 날짜·같은 universe 로 KIS 는 거부되고(위 테스트) 토스는 이 게이트를
        지나간다 — 갈리는 것은 벤더 축뿐이다.
        """
        from types import SimpleNamespace

        from data_pipeline.minute import models as models_module
        from data_pipeline.minute import worker as worker_module

        monkeypatch.setattr(models_module, "load_universe_uri", lambda _: UNIVERSE_EXT)
        monkeypatch.setattr(worker_module, "MinuteLedger", lambda db=None: SimpleNamespace(
            session_snapshot=lambda **_: None))
        settings = SimpleNamespace(
            db=DbConfig(password="x"),
            minute_price_worker=self._config(
                source="toss", client_id="c", client_secret="s"),
        )
        # 게이트를 지나 **세션 조회**까지 가서 죽는다 — 그 문구가 곧 "여기는 안 막혔다"
        with pytest.raises(SystemExit, match="세션 없음"):
            worker_module.price_worker_cli(
                settings, session_date="2026-08-03", universe="s3://bucket/universe.json"
            )

    def test_toss_source_still_builds_toss_collector(self):
        from data_pipeline.minute.toss_collector import TossPriceCollector
        from data_pipeline.minute.worker import make_price_collector

        collector, is_backfill = make_price_collector(
            self._config(source="toss", client_id="c", client_secret="s"), session_date=TODAY
        )
        assert isinstance(collector, TossPriceCollector)
        assert is_backfill is False

    def test_backfill_verdict_is_the_date_not_the_vendor(self):
        """과거일 판정은 **날짜**에서 나온다 — 소급 TR 이 없는 벤더도 백필이다(ALPHA-863).

        토스에는 소급 클라이언트가 없어 collector 타입은 당일과 똑같다. 판정을 벤더
        구현으로 되물으면(옛 `isinstance(getattr(collector, "client", …))`) 여기서 조용히
        False 가 되고, 과거일 토스 백필의 커밋이 오늘의 실시간 판정 큐로 나간다.
        """
        from data_pipeline.minute.toss_collector import TossPriceCollector
        from data_pipeline.minute.worker import make_price_collector

        collector, is_backfill = make_price_collector(
            self._config(source="toss", client_id="c", client_secret="s"),
            session_date=date(2026, 8, 3),
        )
        assert isinstance(collector, TossPriceCollector)  # 벤더 축은 안 갈린다
        assert is_backfill is True  # 날짜 축은 갈린다

    def test_kis_without_app_key_fails_loud(self):
        # 토스 자격증명만 주입된 채 source 만 바뀐 배포 — 첫 벤더 호출이 아니라 기동에서 죽는다
        from data_pipeline.minute.worker import make_price_collector

        with pytest.raises(SystemExit, match="APP_KEY"):
            make_price_collector(
                self._config(source="kis", client_id="c", client_secret="s"),
                session_date=TODAY,
            )

    def test_blank_credentials_fail_loud(self):
        # 공백-only 도 결손이다 — 통과시키면 기동은 되고 모든 인증이 실패해 window 실패만 쌓인다
        from data_pipeline.minute.worker import make_price_collector

        with pytest.raises(SystemExit, match="자격증명 없음"):
            make_price_collector(
                self._config(source="kis", app_key="k", app_secret="  "), session_date=TODAY
            )

    def test_unknown_source_fails_loud(self):
        from data_pipeline.minute.worker import make_price_collector

        with pytest.raises(SystemExit, match="알 수 없는 source"):
            make_price_collector(
                self._config(source="fmp", app_key="k", app_secret="s"), session_date=TODAY
            )

    def test_source_default_matches_terraform_session_group(self):
        """config `source` 와 terraform `minute_session_source_group` 기본값은 같아야 한다.

        session_id = f(dataset, source, date) 라 둘이 갈리면 Worker 가 **존재하지 않는
        세션**을 유도해 기동을 거부한다(그날 수집이 통째로 안 돈다). 배포 전에 잡을 곳은
        여기뿐이라 계약으로 고정한다(test_kis_auth 의 statemachine 검사와 같은 축).
        """
        import re
        root = next(
            (p for p in Path(__file__).resolve().parents
             if (p / "infra/terraform/modules/data-pipeline/variables.tf").exists()),
            None,
        )
        if root is None:
            pytest.skip("variables.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")
        text = (root / "infra/terraform/modules/data-pipeline/variables.tf").read_text()
        block = re.search(
            r'variable\s+"minute_session_source_group"\s*\{[^}]*default\s*=\s*"([^"]+)"', text
        )
        assert block, "minute_session_source_group 기본값을 못 찾았다"
        assert block.group(1) == self._config().source


def test_drain_회수는_설정된_예산만큼_돈다(tmp_path):
    """바닥(`max(1, …)`)만 못박으면 `reclaim_budget = 1` 로 상수화해도 통과한다 — 그러면
    고아 CLAIMED 가 2건 이상인 세션의 drain 이 tick 당 1건씩만 줄어 EOD 정지 기한 대비
    지연이 배로 늘어난다(ALPHA-851 라운드2 지적)."""
    db = FakeMinuteDB()
    worker, ledger, session_id = build_worker(db, tmp_path, windows=3)
    worker.tick(NOW)  # fence 획득

    rows = ledger.session_window_rows(session_id=session_id)
    orphans = [r[0] for r in rows][1:3]
    for window_start in orphans:
        db.windows[(session_id, window_start)].update(
            data_status="CLAIMED", claimed_by="dead", claim_token=99,
            lease_expires_at=NOW - timedelta(seconds=1),
        )
    ledger.request_drain(session_id=session_id, now=NOW)

    # 예산 2 → **한 tick**에 둘 다 회수돼 그 자리에서 DRAINED 로 수렴한다
    assert worker.config.recovery_budget_per_tick == 2
    assert worker.tick(NOW + timedelta(seconds=61)) == "DRAINED"
    assert all(db.windows[(session_id, w)]["data_status"] != "CLAIMED" for w in orphans)
