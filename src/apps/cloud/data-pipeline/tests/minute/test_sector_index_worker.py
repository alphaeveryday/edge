"""업종지수 Worker 테스트 (ALPHA-887).

의도: **이 PR 의 완료 조건이 "canonical artifact 가 실제로 떨어진다"** 는 것이고, 그건
tick 을 돌려봐야만 확인된다. 루프 골격(fence·drain·lane)은 `test_price_worker` 가 이미
덮으므로 여기서는 **가격·iNAV 와 갈리는 셋**만 본다 — 기대 집합의 출처(universe 가 아니라
config), universe 세대 표기, artifact 키.
"""

from __future__ import annotations

import json
import sys

import pytest
from datetime import date, datetime, timedelta
from pathlib import Path

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import (
    LocalStorage,
    canonical_sector_index_minute_artifact_key,
)
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.models import KST, config_set_identity, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.states import DATASET_SECTOR_INDEX_MINUTE
from data_pipeline.minute.worker import SectorIndexWorker, SectorIndexWorkerConfig

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB  # noqa: E402

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 8, 10)
NOW = datetime(2026, 8, 10, 9, 10, tzinfo=KST)
# KOSPI 2 + KOSDAQ 1. 대역이 섞여 있어야 "1xxx 만 돈다" 류의 구현이 걸린다.
INDEX_MAP = {"1005": "0005", "1007": "0007", "2118": "1118"}
UNIT_IDS = tuple(sorted(INDEX_MAP))
EXPECTED_VERSION, EXPECTED_HASH = config_set_identity(INDEX_MAP)


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
        # ⚠️ `session_cli` 가 이 dataset 에 적는 값 그대로다 — config 의 index_map 에서
        # 유도한 정체성이다. Worker 의 `_universe_version()`·`_session_ready()` 가 이것과
        # 갈리면 요청과 원장이 다른 기대 집합을 말한다.
        universe_version=EXPECTED_VERSION, universe_hash=EXPECTED_HASH,
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
            unit_ids=UNIT_IDS, expected_version=EXPECTED_VERSION,
            expected_hash=EXPECTED_HASH, run_id="run_t", lease_seconds=60,
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


def test_기대집합_세대는_planner_가_적은_것과_같다(tmp_path):
    """공유 골격은 `config.universe.universe_version` 을 읽는데 이 config 엔 universe 가
    없다. 훅으로 덮되 **값이 planner 것과 같아야** 한다 — 갈리면 요청과 원장이 다른
    기대 집합을 말하고 그 불일치는 아무도 안 본다.
    """
    db = FakeMinuteDB()
    worker, ledger, session_id, collector = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert collector.seen_universe_versions == [EXPECTED_VERSION]
    snapshot = ledger.session_snapshot(session_id=session_id)
    assert snapshot["universe_version"] == EXPECTED_VERSION


def test_장중에_index_map_이_바뀌면_처리를_거부한다(tmp_path):
    """🔴 오전 45종 → 오후 44종 재배포. 대조가 없으면 남은 window 를 **정상 VALID 로**
    이어 채워, 한 세션 안에서 기대 집합이 갈리는데 원장에 아무 신호가 없다(Codex P2).

    `index_map` 은 이 dataset 의 기대 집합 정본이고 이미지 배포가 곧 반영이라, 장중
    재배포가 **의도된 갱신 경로**다 — 그래서 이 경로는 가정이 아니라 실재한다.
    """
    db = FakeMinuteDB()
    worker, _, _, collector = build_worker(db, tmp_path, windows=1)
    # 한 종목이 빠진 이미지로 교체된 상황
    shrunk = {k: v for k, v in INDEX_MAP.items() if k != "1007"}
    version, digest = config_set_identity(shrunk)
    worker.config.unit_ids = tuple(sorted(shrunk))
    worker.config.expected_version, worker.config.expected_hash = version, digest

    states = run_ticks(worker, NOW)

    # 한 window 도 처리하지 않는다 — 조용히 44종으로 확정하지 않는다
    assert "PROCESSED" not in states
    assert collector.seen_unit_ids == []


def test_KIS_코드만_바뀐_재배포도_잡는다(tmp_path):
    """종목 수가 같아도 **질의 대상**이 바뀌면 다른 기대 집합이다.

    45종 그대로인데 KIS 코드 한 줄이 바뀐 재배포는 같은 unit 에 **남의 지수**를 싣는다 —
    이 표에서 가장 조용한 오류다. 키만 해싱하면 이게 통과한다.
    """
    db = FakeMinuteDB()
    worker, _, _, collector = build_worker(db, tmp_path, windows=1)
    swapped = {**INDEX_MAP, "1005": "0006"}   # 종목 수 동일, 질의 코드만 다름
    version, digest = config_set_identity(swapped)
    worker.config.expected_version, worker.config.expected_hash = version, digest

    states = run_ticks(worker, NOW)

    assert "PROCESSED" not in states
    assert collector.seen_unit_ids == []


def test_복구_예산이_0_이다(tmp_path):
    """🔴 **채택된 방침이다 — 미착수가 아니다.** 이 소스는 소급이 아예 불가라(소급 TR 은
    일봉으로 degrade) 놓친 분은 놓친 채로 두고 결손은 원장이 드러낸다. 켜면 iNAV 와 같은
    지평 문제를 만난다 — 페이지 밖 window 가 계속 최고령이라 매 tick 같은 것을 집는다.
    """
    db = FakeMinuteDB()
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    assert worker.config.recovery_budget_per_tick == 0


def test_drain_중_처리한_window_가_카운터에_남는다(tmp_path):
    """🔴 `tick()` 은 회수·처리에 성공해도 **`DRAINED` 하나만** 돌려준다 — 그 사실이
    카운터에 안 남으면 bounded 확인 게이트가 "회수할 게 없었다"와 구분을 못 한다.

    ⚠️ 이 테스트는 CLI 스텁이 아니라 **진짜 drain 루프**를 돈다. `TestBoundedGate` 는
    `tick` 을 통째로 대체하므로 이 카운터가 실제로 증가하는지 못 잰다 — 스텁이
    테스트하려는 경로 자체를 대체한 자리다(리뷰 라운드 3).
    """
    from datetime import timedelta

    db = FakeMinuteDB()
    worker, ledger, session_id, _ = build_worker(db, tmp_path, windows=2)
    worker.tick(NOW)  # fence 획득 + 첫 window 처리

    # 죽은 attempt 가 남긴 고아 CLAIMED — lease 는 이미 만료됐다
    window_start = ledger.session_window_rows(session_id=session_id)[1][0]
    db.windows[(session_id, window_start)].update(
        data_status="CLAIMED", claimed_by="dead-worker", claim_token=99,
        lease_expires_at=NOW - timedelta(seconds=1))
    ledger.request_drain(session_id=session_id, now=NOW)

    states = [worker.tick(NOW + timedelta(seconds=60 + i)) for i in range(4)]

    assert "DRAINED" in states, f"drain 이 수렴하지 않았다: {states}"
    # 회수해서 **처리까지** 했다는 사실이 남아야 한다
    assert getattr(worker, "drain_processed", 0) >= 1


class TestCli:
    """기동 가드 — 설정·날짜 결손은 첫 벤더 호출이 아니라 **기동에서** 죽어야 배포
    시점에 드러난다(`test_price_worker.TestCli` 와 같은 형태)."""

    def _settings(self, *, db=_DB, sector_index=..., kis_nav=...):
        from types import SimpleNamespace
        from data_pipeline.config.models import MinuteSectorIndexConfig
        if sector_index is ...:
            sector_index = MinuteSectorIndexConfig(index_map=INDEX_MAP)
        if kis_nav is ...:
            kis_nav = SimpleNamespace(
                source=SimpleNamespace(app_key="k", app_secret="s"))
        return SimpleNamespace(db=db, minute_sector_index=sector_index,
                               kis_nav=kis_nav, storage=None)

    def _cli(self, **kwargs):
        from data_pipeline.minute.worker import sector_index_worker_cli
        return sector_index_worker_cli(**kwargs)

    def test_missing_db_fails_loud(self):
        with pytest.raises(SystemExit, match="db 설정 없음"):
            self._cli(settings=self._settings(db=None), session_date=None)

    def test_missing_index_map_fails_loud(self):
        """config 가 기대 집합의 정본이다 — 미설정으로 돌면 unit 0종이라 매 window 가
        빈 성공으로 확정된다. 그건 "받을 게 없다"가 아니라 배선 누락이다."""
        with pytest.raises(SystemExit, match=r"minute_sector_index.index_map. 설정 없음"):
            self._cli(settings=self._settings(sector_index=None), session_date=None)

    def test_missing_credentials_fail_loud(self):
        from types import SimpleNamespace
        with pytest.raises(SystemExit, match="자격증명 없음"):
            self._cli(
                settings=self._settings(kis_nav=SimpleNamespace(
                    source=SimpleNamespace(app_key="k", app_secret=None))),
                session_date=None)

    def test_bad_session_date_fails_loud(self):
        with pytest.raises(SystemExit, match="session-date 형식 오류"):
            self._cli(settings=self._settings(), session_date="2026-W01-1")

    def test_past_session_date_is_rejected(self):
        """🔴 이 TR 은 날짜 질의가 불가하다 — 과거일로 돌리면 45종 전건 missing 이 그
        날짜 원장에 굳고, 소급이 없어 채울 방법이 없다."""
        with pytest.raises(SystemExit, match="오늘.*이 아니다"):
            self._cli(settings=self._settings(), session_date="2020-01-02")


class TestBoundedGate:
    """`--max-ticks` 는 **확인 게이트**다 — 아무것도 못 봤으면 성공이 아니다."""

    def _run_cli(self, monkeypatch, tmp_path, states, *, session_identity=None,
                 resident=False):
        """CLI 를 끝까지 몬다 — 판정식을 테스트가 복제하면 코드가 아니라 사본을 잰다."""
        from datetime import datetime as real_datetime
        from types import SimpleNamespace

        import data_pipeline.lake.storage as storage_mod
        import data_pipeline.minute.worker as mod
        from data_pipeline.config.models import MinuteSectorIndexConfig

        db = FakeMinuteDB()
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=None, extended_hours=False)
        ledger.plan_session(
            dataset=DATASET_SECTOR_INDEX_MINUTE, source_group="kis",
            session_date=SESSION_DATE,
            universe_version=(session_identity or (EXPECTED_VERSION, EXPECTED_HASH))[0],
            universe_hash=(session_identity or (EXPECTED_VERSION, EXPECTED_HASH))[1],
            windows=planned[:1],
        )
        monkeypatch.setattr(mod, "MinuteLedger", lambda **kw: ledger)
        monkeypatch.setattr(mod, "MinuteCommitter",
                            lambda **kw: MinuteCommitter(db=_DB, connect_fn=db.connect))
        monkeypatch.setattr(storage_mod, "make_storage",
                            lambda cfg: LocalStorage(root=tmp_path))
        # 벤더 클라이언트는 생성만 하고 안 부른다(tick 을 대체하므로)
        monkeypatch.setattr(mod, "KST", mod.KST)
        # 상태 문자열 또는 (상태, 설정할 drain 카운터) 튜플을 받는다 — DRAINED 는
        # "회수할 게 없었다"와 "회수·처리 성공"을 **같은 문자열로** 내므로, 그 구분을
        # 스텁이 못 하면 테스트가 두 경우를 같은 것으로 승인한다(리뷰 라운드 3).
        remaining = list(states)

        def fake_tick(self, now):
            if not remaining:
                return "IDLE"
            item = remaining.pop(0)
            if isinstance(item, tuple):
                state, counters = item
                for name, value in counters.items():
                    setattr(self, name, value)
                return state
            return item

        monkeypatch.setattr(mod.SectorIndexWorker, "tick", fake_tick)
        # 오늘 날짜 게이트를 통과시킨다 — 세션 날짜와 "오늘"을 같은 날로 고정
        class FrozenDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 8, 10, 9, 10, tzinfo=tz)
        monkeypatch.setattr(mod, "datetime", FrozenDatetime)

        settings = SimpleNamespace(
            db=_DB,
            minute_sector_index=MinuteSectorIndexConfig(index_map=INDEX_MAP),
            kis_nav=SimpleNamespace(source=SimpleNamespace(app_key="k", app_secret="s")),
            storage=None,
        )
        return mod.sector_index_worker_cli(
            settings, session_date=SESSION_DATE.isoformat(),
            max_ticks=None if resident else len(states), tick_seconds=0,
        )

    def test_전_tick_이_IDLE_이면_성공이_아니다(self, monkeypatch, tmp_path):
        """🔴 개장 전 실행처럼 due window 가 아예 없으면 전 tick 이 IDLE 이고 HTTP 호출도
        artifact 도 0 건이다. `blocked and not processed` 로만 판정하면 그때 exit 0 이라,
        README 의 `--max-ticks 3` 확인 명령이 **매일 초록으로** 통과한다(Codex 리뷰 P2).

        iNAV 는 어댑터의 `skip_reason` 이 이 자리를 지키는데 이 어댑터엔 그 축이 없다.
        """
        assert self._run_cli(monkeypatch, tmp_path, ["IDLE", "IDLE"]) == 1

    def test_첫_tick_이_DRAINED_여도_무처리면_성공이_아니다(self, monkeypatch, tmp_path):
        """🔴 판정식을 **한 곳만** 고쳤던 자리다(리뷰 라운드 2).

        DRAINING 세션에 회수할 claim 이 없으면 첫 tick 이 drain 을 ack 하고 DRAINED 를
        낸다. 그 분기는 조기 반환이라 아래 게이트를 통째로 우회하는데, 거기 옛 판정식이
        남아 있으면 **아무것도 안 하고 exit 0** 이다.
        """
        assert self._run_cli(monkeypatch, tmp_path, ["DRAINED"]) == 1

    def test_drain_중_실제로_처리했으면_성공이다(self, monkeypatch, tmp_path):
        """🔴 라운드 2 수정이 만든 **반대 방향 오판정**(리뷰 라운드 3).

        DRAINING 세션에 만료된 CLAIMED 가 있으면 그 tick 이 window 를 처리하고 같은 tick
        에서 ack 까지 성공한 뒤 `DRAINED` **하나만** 반환한다. CLI 는 `PROCESSED` 상태만
        세므로 processed 가 0 인 채여서, "아무것도 안 했으면 실패" 규칙이 **실제로 일한
        실행을 실패로** 판정했다. drain 성공 카운터를 합산해 가른다.
        """
        assert self._run_cli(
            monkeypatch, tmp_path, [("DRAINED", {"drain_processed": 3})]) == 0

    def test_DRAINING_중_max_ticks_가_소진돼도_처리분은_센다(self, monkeypatch, tmp_path):
        """DRAINED 에 닿기 전에 tick 예산이 끝나는 경로 — 조기 반환을 안 타고 **말미
        판정식**으로 떨어진다. 거기서도 drain 처리분을 합산해야 실제로 일한 실행을
        실패로 안 낸다(합산이 두 자리라 한쪽만 고치기 쉽다).
        """
        assert self._run_cli(
            monkeypatch, tmp_path, [("DRAINING", {"drain_processed": 2})]) == 0

    def test_상주_모드의_DRAINED_는_정상_종료다(self, monkeypatch, tmp_path):
        """반대 방향도 잰다 — 상주 서비스가 그날 정상적으로 마르는 것을 실패로 보고하면
        매일 알람이 운다. 확인 게이트 규칙은 **bounded 에만** 걸린다."""
        assert self._run_cli(monkeypatch, tmp_path, ["DRAINED"], resident=True) == 0

    def test_실제로_처리했으면_성공이다(self, monkeypatch, tmp_path):
        """게이트가 늘 1 을 내면 확인 명령이 영영 안 통과한다 — 반대 방향도 잰다."""
        assert self._run_cli(monkeypatch, tmp_path, ["PROCESSED", "IDLE"]) == 0

    def test_처리했지만_window_가_실패했으면_성공이_아니다(self, monkeypatch, tmp_path):
        assert self._run_cli(monkeypatch, tmp_path, ["WINDOW_FAILED"]) == 1

    def test_세션이_다른_index_map_으로_고정돼_있으면_기동에서_죽는다(self, monkeypatch, tmp_path):
        """🔴 tick 마다 `_session_ready` 가 거부하면 IDLE 만 돌아 운영자는 "돌고는 있는데
        아무것도 안 들어온다"만 본다 — 사유를 **기동에서 한 번 크게** 낸다(Rule 12).

        `_session_ready` 대조만으로는 부족하다는 것이 이 테스트의 요지다: 그 경로는
        조용하고, 이 경로는 시끄럽다.
        """
        stale = config_set_identity({k: v for k, v in INDEX_MAP.items() if k != "1007"})
        with pytest.raises(SystemExit, match="기대 집합"):
            self._run_cli(monkeypatch, tmp_path, ["IDLE"], session_identity=stale)
