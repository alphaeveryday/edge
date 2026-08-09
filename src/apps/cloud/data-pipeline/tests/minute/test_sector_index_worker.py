"""업종지수 Worker 테스트 (ALPHA-887).

의도: **이 PR 의 완료 조건이 "canonical artifact 가 실제로 떨어진다"** 는 것이고, 그건
tick 을 돌려봐야만 확인된다. 루프 골격(fence·drain·lane)은 `test_price_worker` 가 이미
덮으므로 여기서는 **가격·iNAV 와 갈리는 셋**만 본다 — 기대 집합의 출처(universe 가 아니라
config), universe 세대 표기, artifact 키.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import (
    LocalStorage,
    canonical_sector_index_minute_artifact_key,
)
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.models import KST, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.states import DATASET_SECTOR_INDEX_MINUTE
from data_pipeline.minute.worker import SectorIndexWorker, SectorIndexWorkerConfig

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB  # noqa: E402

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 8, 10)
NOW = datetime(2026, 8, 10, 9, 10, tzinfo=KST)
# KOSPI 2 + KOSDAQ 1. 대역이 섞여 있어야 "1xxx 만 돈다" 류의 구현이 걸린다.
UNIT_IDS = ("1005", "1007", "2118")


class StubCollector:
    """요청받은 unit 전부를 received 로 낸다 — 기대 집합이 무엇이었는지 기록한다."""

    def __init__(self):
        self.seen_unit_ids: list[tuple[str, ...]] = []
        self.seen_universe_versions: list[str] = []

    def collect(self, request, now):
        from data_pipeline.minute.models import CollectionResult, content_checksum

        self.seen_unit_ids.append(request.unit_ids)
        self.seen_universe_versions.append(request.universe_version)
        records = tuple(
            {"unit_id": unit, "ts": request.window_start, "open": "5040",
             "high": "5050", "low": "5039", "close": "5047.39", "volume": "80"}
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
    # 격자는 390 이고 **universe 가 없다** — planner 가 이 dataset 을 그렇게 계획한다
    planned = plan_session_windows(SESSION_DATE, universe=None, extended_hours=False)
    session_id, _ = ledger.plan_session(
        dataset=DATASET_SECTOR_INDEX_MINUTE, source_group="kis",
        session_date=SESSION_DATE,
        # ⚠️ `session_cli` 가 universe 없는 dataset 에 적는 값 그대로다. Worker 의
        # `_universe_version()` 이 이것과 갈리면 요청과 원장이 다른 세대를 말한다.
        universe_version="none", universe_hash="none",
        windows=planned[:windows],
    )
    collector = StubCollector()
    worker = SectorIndexWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(root=tmp_path),
        collector=collector,
        config=SectorIndexWorkerConfig(
            worker_id="sw1", dataset=DATASET_SECTOR_INDEX_MINUTE, source="kis",
            market="KR", session_date=SESSION_DATE.isoformat(),
            unit_ids=UNIT_IDS, run_id="run_t", lease_seconds=60,
        ),
    )
    return worker, ledger, session_id, collector


def run_ticks(worker, start, count=8):
    return [worker.tick(start + timedelta(seconds=i)) for i in range(count)]


def test_canonical_artifact_가_떨어진다(tmp_path):
    """**이 PR 의 완료 조건이다.** 어휘·키·수집기·확정 경계가 다 있어도 tick 이 이어
    붙지 않으면 S3 에는 아무것도 없다 — 그 상태가 원장에서는 정상으로 보인다."""
    db = FakeMinuteDB()
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    key = canonical_sector_index_minute_artifact_key("KR", "2026-08-10", "0900", 1)
    body = LocalStorage(root=tmp_path).get_bytes(key).decode("utf-8")
    rows = [json.loads(line) for line in body.splitlines()]
    # canonical 의 unit_id 는 **KRX 업종코드**다 — KIS 지수코드가 새면 일봉
    # `sector_index` 와 어떤 조인에도 안 걸린다(어댑터 도크스트링 6번).
    assert [r["unit_id"] for r in rows] == ["1005", "1007", "2118"]
    assert all(r["source"] == "kis" for r in rows)


def test_기대_집합은_config_가_준다_universe_가_아니라(tmp_path):
    """이 dataset 의 unit 은 universe.json 에 **한 줄도 없다**(ETF 명부에도 구성종목에도).

    `_expected_units` 를 `universe.units_at()` 로 두면 45종이 전부 기대에서 빠져 매
    window 가 빈 성공(VALID_EMPTY)으로 확정된다 — 그건 "받을 게 없다"가 아니라 배선
    누락인데, 원장에는 정상으로 보인다.
    """
    db = FakeMinuteDB()
    worker, _, _, collector = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert collector.seen_unit_ids == [UNIT_IDS]


def test_universe_세대는_planner_가_적은_none_과_같다(tmp_path):
    """공유 골격은 `config.universe.universe_version` 을 읽는데 이 config 엔 universe 가
    없다. 훅으로 덮되 **값이 planner 것과 같아야** 한다 — 갈리면 요청과 원장이 다른
    세대를 말하고 그 불일치는 아무도 안 본다.
    """
    db = FakeMinuteDB()
    worker, ledger, session_id, collector = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert collector.seen_universe_versions == ["none"]
    assert ledger.session_snapshot(session_id=session_id)["universe_version"] == "none"


def test_복구_예산이_0_이다(tmp_path):
    """🔴 **채택된 방침이다 — 미착수가 아니다.** 이 소스는 소급이 아예 불가라(소급 TR 은
    일봉으로 degrade) 놓친 분은 놓친 채로 두고 결손은 원장이 드러낸다. 켜면 iNAV 와 같은
    지평 문제를 만난다 — 페이지 밖 window 가 계속 최고령이라 매 tick 같은 것을 집는다.
    """
    db = FakeMinuteDB()
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    assert worker.config.recovery_budget_per_tick == 0
