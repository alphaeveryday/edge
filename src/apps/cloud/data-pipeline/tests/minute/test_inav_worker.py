"""iNAV Worker 테스트 (ALPHA-851).

의도: **이 티켓의 완료 조건이 "canonical artifact 가 실제로 떨어진다"** 는 것이고,
그건 tick 을 돌려봐야만 확인된다. 루프 골격(fence·drain·lane)은 `test_price_worker`
가 이미 덮으므로 여기서는 **가격과 갈리는 넷**만 본다 — 기대 집합·artifact 키·확정
경계(발행 없음)·recovery 0.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import (
    LocalStorage,
    canonical_etf_inav_minute_artifact_key,
)
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.models import KST, Universe, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.states import DATASET_ETF_INAV_MINUTE
from data_pipeline.minute.worker import InavWorker, InavWorkerConfig

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB  # noqa: E402

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 8, 10)
NOW = datetime(2026, 8, 10, 9, 10, tzinfo=KST)
# ETF 2 + 참조 계열 1 + 구성종목 2. **구성종목에는 NAV 가 없다** — 그 둘이 기대 집합에서
# 빠지는 것이 이 dataset 의 핵심 차이라, 픽스처가 그걸 구분할 수 있어야 한다.
UNIVERSE = Universe(
    universe_version="univ-inav-v1",
    etf_ids=("069500", "091160"),
    constituent_ids=("005930", "000660"),
    sector_etf_ids=("395160",),
)


class StubInavCollector:
    """요청받은 unit 전부를 received 로 낸다 — 기대 집합이 무엇이었는지 기록한다."""

    def __init__(self):
        self.seen_unit_ids: list[tuple[str, ...]] = []

    def collect(self, request, now):
        from data_pipeline.minute.models import CollectionResult, content_checksum

        self.seen_unit_ids.append(request.unit_ids)
        records = tuple(
            {"unit_id": unit, "ts": request.window_start, "nav": "100.0",
             "market_price": "101", "premium_pct": "1.00"}
            for unit in sorted(request.unit_ids)
        )
        manifest = {"received": sorted(request.unit_ids), "no_trade": [],
                    "missing": [], "invalid": []}
        result = CollectionResult(
            status="VALID", expected_count=len(request.unit_ids),
            succeeded_count=len(request.unit_ids), failed_count=0, retry_count=0,
            artifact_uri="pending://artifact",
            manifest_checksum=content_checksum(manifest),
            result_checksum=content_checksum([request.dataset, records]),
            watermark_before=None, watermark_after=request.window_end, generation=1,
            stage_timestamps={"collection_started_at": now},
        )
        return result, records, manifest


def build_worker(db, tmp_path, *, windows=3):
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    # 격자는 390 이다 — iNAV 는 시간외를 계획하지 않는다(어댑터 하한 09:00)
    planned = plan_session_windows(SESSION_DATE, universe=UNIVERSE, extended_hours=False)
    session_id, _ = ledger.plan_session(
        dataset=DATASET_ETF_INAV_MINUTE, source_group="kis", session_date=SESSION_DATE,
        universe_version=UNIVERSE.universe_version, universe_hash=UNIVERSE.universe_hash,
        windows=planned[:windows],
    )
    collector = StubInavCollector()
    worker = InavWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(root=tmp_path),
        collector=collector,
        config=InavWorkerConfig(
            worker_id="iw1", dataset=DATASET_ETF_INAV_MINUTE, source="kis",
            market="KR", session_date=SESSION_DATE.isoformat(),
            universe=UNIVERSE, run_id="run_t", lease_seconds=60,
        ),
    )
    return worker, ledger, session_id, collector


def run_ticks(worker, start, count=8):
    return [worker.tick(start + timedelta(seconds=i)) for i in range(count)]


def test_canonical_artifact_가_떨어진다(tmp_path):
    """**851 의 완료 조건이다.** 어휘·키·수집기·확정 경계가 다 있어도 tick 이 이어 붙지
    않으면 S3 에는 아무것도 없다 — 그 상태가 원장에서는 정상으로 보인다."""
    db = FakeMinuteDB()
    worker, ledger, session_id, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    key = canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0900", 1)
    body = LocalStorage(root=tmp_path).get_bytes(key).decode("utf-8")
    rows = [json.loads(line) for line in body.splitlines()]
    # 기대 집합(ETF 3종)만큼, 벤더 축은 레코드 컬럼으로
    assert [r["unit_id"] for r in rows] == ["069500", "091160", "395160"]
    assert all(r["source"] == "kis" for r in rows)
    window = db.windows[(session_id, worker.ledger.session_window_rows(
        session_id=session_id)[0][0])]
    assert window["data_status"] == "VALID" and window["generation"] == 1


def test_구성종목은_기대_집합에서_빠진다(tmp_path):
    """구성종목에는 NAV 가 없다. `units_at` 을 그대로 쓰면 5종을 기대해 매 window 가
    INCOMPLETE 이고, 소급이 불가한 이 소스에서 그 결손은 영구다."""
    db = FakeMinuteDB()
    worker, _, _, collector = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert collector.seen_unit_ids
    for requested in collector.seen_unit_ids:
        assert set(requested) == {"069500", "091160", "395160"}
        assert "005930" not in requested and "000660" not in requested


def test_job_도_outbox_도_만들지_않는다(tmp_path):
    """iNAV 는 하위 소비자가 없다. 발행하면 봉을 기대하는 소비자가 NAV 를 받아
    설명이 발화된다."""
    db = FakeMinuteDB()
    worker, _, _, _ = build_worker(db, tmp_path, windows=2)

    run_ticks(worker, NOW)

    assert db.jobs == {} and db.outbox == {}


def test_recovery_lane_은_기본으로_돌지_않는다(tmp_path):
    """**채택된 방침이다**(2026-08-08) — iNAV 는 추정 NAV 라 분 단위 완전성 요구가 낮아
    복구를 두지 않는다. 놓친 분은 놓친 채로 두고 결손은 원장이 드러낸다.

    이 단언이 있는 이유: 기본값이 조용히 2 로 돌아오면(공유 `WorkerConfig` 를 다시 쓰게
    되는 등) recovery 가 최고령 due 부터 집는데, 창(30분) 밖 window 는 못 채우면서 계속
    최고령이라 매 tick 같은 것을 집어 앱키 전역 쿼터만 태우고 최신 분을 민다."""
    db = FakeMinuteDB()
    worker, _, _, collector = build_worker(db, tmp_path, windows=3)

    assert worker.config.recovery_budget_per_tick == 0
    states = run_ticks(worker, NOW, count=3)

    # tick 당 realtime 1건씩만 — recovery 가 돌면 첫 tick 에 여러 건이 처리된다
    assert states[:3] == ["PROCESSED", "PROCESSED", "PROCESSED"]
    assert len(collector.seen_unit_ids) == 3


def test_재실행은_같은_키에_같은_바이트다(tmp_path):
    """같은 window 재수집이 같은 checksum → generation 불변 → 같은 키. 값이 같은데
    바이트가 흔들리면 `ArtifactImmutabilityError` 로 그 window 가 영영 막힌다."""
    db = FakeMinuteDB()
    worker, ledger, session_id, _ = build_worker(db, tmp_path, windows=1)
    run_ticks(worker, NOW)
    key = canonical_etf_inav_minute_artifact_key("KR", "2026-08-10", "0900", 1)
    first = LocalStorage(root=tmp_path).get_bytes(key)

    # window 를 DUE 로 되돌려 재청구시킨다(정상 운영의 lease 만료 재시도와 같은 자리)
    window_start = ledger.session_window_rows(session_id=session_id)[0][0]
    db.windows[(session_id, window_start)]["data_status"] = "DUE"
    run_ticks(worker, NOW + timedelta(seconds=30))

    assert LocalStorage(root=tmp_path).get_bytes(key) == first
    assert db.windows[(session_id, window_start)]["generation"] == 1


def test_drain_은_recovery_예산이_0_이어도_수렴한다(tmp_path):
    """🔴 이 둘은 **다른 일인데 노브를 공유했다**(리뷰 지적). backlog 를 안 쫓는 것과
    drain 중 고아 CLAIMED 회수는 별개인데, `tick()` 이 같은 `recovery_budget_per_tick`
    으로 두 루프를 돌린다.

    0 이면 회수 루프가 0회 돌고, `ack_drain` 은 CLAIMED 잔존 시 거부하므로 세션이
    **DRAINING 에 영구 고착**된다 — EOD 가 그 dataset 에서 영영 시작되지 않고 상주
    진입점은 sleep 루프를 무한히 돈다. 도달 경로는 평범하다: ACTIVE 에서 실패한 window
    는 claim 이 release 되지 않아 lease(기본 300초) 동안 CLAIMED 인데, 그 사이 EOD 가
    drain 을 걸면 그대로 봉인된다.
    """
    db = FakeMinuteDB()
    worker, ledger, session_id, _ = build_worker(db, tmp_path, windows=2)
    worker.tick(NOW)  # fence 획득 + 첫 window 처리

    # 죽은 attempt 가 남긴 고아 CLAIMED — lease 는 이미 만료됐다
    window_start = ledger.session_window_rows(session_id=session_id)[1][0]
    row = db.windows[(session_id, window_start)]
    row.update(data_status="CLAIMED", claimed_by="dead-worker", claim_token=99,
               lease_expires_at=NOW - timedelta(seconds=1))
    ledger.request_drain(session_id=session_id, now=NOW)

    states = [worker.tick(NOW + timedelta(seconds=60 + i)) for i in range(4)]

    assert "DRAINED" in states, f"drain 이 수렴하지 않았다: {states}"
    assert db.windows[(session_id, window_start)]["data_status"] != "CLAIMED"


class TestRunGate:
    """틀린 날짜·휴장일에 돌면 **지금 값이 그 날짜의 불변 artifact 로 굳는다** — 벤더
    응답에 날짜가 없고(`bsop_hour` = HHMMSS) 소급 질의 경로도 없어서, 라벨이 어느 날짜의
    window 와도 1:1로 맞는다. 이 소스는 재수집이 불가라 되돌릴 방법이 없다.

    벽시계를 안 읽는 순수 함수로 빼서 가상 시계 없이 못박는다.
    """

    def test_오늘이_아니면_막는다(self):
        from data_pipeline.minute.worker import inav_run_blocked

        today = date(2026, 8, 10)
        assert inav_run_blocked(date(2026, 8, 7), today, None) is not None   # 과거
        assert inav_run_blocked(date(2026, 8, 11), today, None) is not None  # 미래
        assert inav_run_blocked(today, today, None) is None

    def test_휴장일_개장전_사유는_그대로_전달된다(self):
        """raw 스텝은 이미 `skip_reason` 으로 막는데 canonical 경로는 그 가드를 안 지났다
        — KIS 는 휴장일에 빈 응답이 아니라 **직전 거래일 행을 그대로** 준다(실측)."""
        from data_pipeline.minute.worker import inav_run_blocked

        today = date(2026, 8, 10)
        assert inav_run_blocked(today, today, "non-trading day (KST 2026-08-10)") == (
            "non-trading day (KST 2026-08-10)"
        )
        assert inav_run_blocked(today, today, "before market open (KST 08:50 < 09:00)") is not None
