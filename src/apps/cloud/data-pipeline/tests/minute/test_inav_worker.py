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

    ⚠️ **술어가 아니라 가드를 잡는다.** 앞선 판(순수 함수만 직접 호출)은 CLI 의 호출부를
    통째로 우회시켜도(`if False:`) 전 스위트가 통과했다 — 고치려던 결함("가드가 소비자
    한쪽에만 있다")이 한 층 위에서 그대로 재현된 것이다. 그래서 여기서는 **CLI 를 돌려**
    수집 경로에 도달하지 않는 것까지 단언한다.
    """

    def _settings(self):
        class Src:
            app_key, app_secret = "k", "s"

        class Section:
            source = Src()

        class KrxSource:
            etf_map = {"069500": "KR7069500007"}

        class Krx:
            source = KrxSource()

        class Settings:
            db = DbConfig(password="x")
            kis_nav = Section()
            krx_etf = Krx()
            storage = None

        return Settings()

    def _run(self, monkeypatch, tmp_path, *, skip_reason, session_date, max_ticks=3):
        """CLI 를 실제로 돌린다. 게이트를 지나면 원장에 닿으므로, 원장 생성이 곧
        '수집 경로에 도달했다'는 신호다 — 거기서 터뜨려 도달을 검출한다."""
        import data_pipeline.minute.worker as module
        import data_pipeline.sources.kis_inav as kis_inav

        class StubSource:
            def __init__(self, *a, **k):
                self.interval_sec = 60

            @property
            def skip_reason(self):
                return skip_reason

        reached = []

        def _ledger(**kwargs):
            reached.append(1)
            raise AssertionError("게이트를 지나 수집 경로에 도달했다")

        monkeypatch.setattr(kis_inav, "KisInavSource", StubSource)
        monkeypatch.setattr(module, "MinuteLedger", _ledger)
        universe_file = tmp_path / "u.json"
        universe_file.write_text(UNIVERSE.model_dump_json(), encoding="utf-8")
        code = module.inav_worker_cli(
            self._settings(), session_date=session_date,
            universe=str(universe_file), max_ticks=max_ticks,
        )
        return code, reached

    def test_오늘이_아니면_기동을_거부한다(self, monkeypatch, tmp_path):
        """운영자 입력 오류다 — 환경 skip 의 exit 0 을 물려주면 날짜를 훑는 래퍼가
        **아무것도 안 쓰고 전건 초록**으로 끝난다."""
        import pytest

        yesterday = (datetime.now(KST).date() - timedelta(days=1)).isoformat()
        with pytest.raises(SystemExit, match="오늘"):
            self._run(monkeypatch, tmp_path, skip_reason=None, session_date=yesterday)

    def test_휴장일은_수집_경로에_닿기_전에_멈춘다(self, monkeypatch, tmp_path):
        """KIS 는 휴장일에 빈 응답이 아니라 **직전 거래일 행을 그대로** 준다(실측) —
        라벨이 그날 window 와 1:1로 맞아 세션 전체가 전일 NAV 로 굳는다."""
        today = datetime.now(KST).date().isoformat()
        code, reached = self._run(
            monkeypatch, tmp_path, skip_reason="non-trading day (KST %s)" % today,
            session_date=today,
        )

        assert reached == []          # 원장에 닿지도 않았다
        assert code == 1              # bounded 는 확인 게이트다 — 아래 테스트가 짝이다

    def test_상주_모드의_휴장일_skip_만_정상_종료다(self, monkeypatch, tmp_path):
        """`--max-ticks` 없는 상주 실행은 스케줄러가 휴장일마다 정상으로 지나가야 하지만,
        bounded 실행은 "돌렸는데 한 window 도 못 봤다"를 성공으로 보고하면 안 된다 —
        README 의 확인 명령이 휴장일마다 초록으로 통과한다."""
        today = datetime.now(KST).date().isoformat()
        code, _ = self._run(
            monkeypatch, tmp_path, skip_reason="non-trading day",
            session_date=today, max_ticks=None,
        )
        assert code == 0

    def test_bounded_는_개장_전에_기다리지_않는다(self, monkeypatch, tmp_path):
        """확인 명령(`--max-ticks 3`)은 즉답이어야 한다 — 개장 전이라고 기다리면
        README 의 확인이 장 시작까지 멈춰 선다. 그리고 exit 1 이라야 "한 window 도
        못 봤다"가 초록으로 안 지나간다."""
        today = datetime.now(KST).date().isoformat()
        code, reached = self._run(
            monkeypatch, tmp_path,
            skip_reason="before market open (KST 07:45 < 09:00)", session_date=today,
        )
        assert reached == []
        assert code == 1

    def test_상주_모드는_개장_전에_종료하지_않고_기다린다(self, monkeypatch, tmp_path):
        """⭐ start-minute-session 은 07:45 에 올리는데(가격 레인 시간외 첫 window 08:00)
        iNAV 하한은 09:00 이다. 여기서 종료하면 ECS 가 desired 1 을 유지해 개장까지
        ~75분 재기동 루프를 돈다 — ECS 백오프가 첫 정상 기동을 09:00 뒤로 밀 수 있고
        iNAV window 는 소급이 불가라 그만큼 영구 결손이다.

        기다린 **뒤에는 실제로 수집 경로로 들어가야** 한다 — 그냥 안 죽고 도는 것은
        재기동 루프와 증상만 다르고 결과가 같다."""
        import data_pipeline.minute.worker as module
        import data_pipeline.sources.kis_inav as kis_inav

        today = datetime.now(KST).date().isoformat()
        reasons = ["before market open (KST 07:45 < 09:00)",
                   "before market open (KST 08:30 < 09:00)",
                   None]

        class StubSource:
            def __init__(self, *a, **k):
                self.interval_sec = 60

            @property
            def skip_reason(self):
                return reasons.pop(0) if len(reasons) > 1 else reasons[0]

        slept = []
        reached = []

        def _ledger(**kwargs):
            reached.append(1)
            raise AssertionError("게이트를 지나 수집 경로에 도달했다")

        monkeypatch.setattr(kis_inav, "KisInavSource", StubSource)
        monkeypatch.setattr(module, "MinuteLedger", _ledger)
        import time as _time
        monkeypatch.setattr(_time, "sleep", lambda s: slept.append(s))
        universe_file = tmp_path / "u.json"
        universe_file.write_text(UNIVERSE.model_dump_json(), encoding="utf-8")

        import pytest
        with pytest.raises(AssertionError, match="수집 경로에 도달"):
            module.inav_worker_cli(
                self._settings(), session_date=today,
                universe=str(universe_file), max_ticks=None,
            )

        assert reached == [1], "개장 뒤엔 대기를 끝내고 수집으로 들어가야 한다"
        assert slept == [20, 20], "개장 전 관측마다 tick_seconds 만큼 잔다(종료가 아니라)"

    def test_대기_중_비거래일로_바뀌면_종료한다(self, monkeypatch, tmp_path):
        """자정을 넘겨 날짜가 바뀌는 경우다 — 기다려도 안 열리는 사유로 바뀌면 빠져나와야
        한다. 접두어만 보고 무조건 계속 자면 그 컨테이너는 영원히 안 죽는다."""
        import data_pipeline.minute.worker as module
        import data_pipeline.sources.kis_inav as kis_inav

        today = datetime.now(KST).date().isoformat()
        reasons = ["before market open (KST 23:59 < 09:00)", "non-trading day (KST ...)"]

        class StubSource:
            def __init__(self, *a, **k):
                self.interval_sec = 60

            @property
            def skip_reason(self):
                return reasons.pop(0) if len(reasons) > 1 else reasons[0]

        monkeypatch.setattr(kis_inav, "KisInavSource", StubSource)
        monkeypatch.setattr(module, "MinuteLedger",
                            lambda **k: (_ for _ in ()).throw(AssertionError("도달하면 안 된다")))
        import time as _time
        monkeypatch.setattr(_time, "sleep", lambda s: None)
        universe_file = tmp_path / "u.json"
        universe_file.write_text(UNIVERSE.model_dump_json(), encoding="utf-8")

        code = module.inav_worker_cli(
            self._settings(), session_date=today,
            universe=str(universe_file), max_ticks=None,
        )
        assert code == 0
