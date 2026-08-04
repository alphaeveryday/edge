"""가격 트리거 판정 handler 테스트 (ALPHA-708 / v2 = ALPHA-745, 2026-08-04 확정).

의도: 판정기는 LLM 0 이고 출력이 곧 분석 파이프라인의 입력이다 — 여기서 고정하는 계약:

- 기준선 = **전일 종가**. 결손 종목만 세션 시가 폴백(minute_session_open, v1 기계 그대로)
- 판정식 v2: 기준선 ±revert_threshold 안이면 발화 금지 + 노출 회수, 밖이면
  |close / anchor − 1| ≥ abs_threshold 에서 발화하고 앵커 ← 발화가
- 쿨다운은 **없다** — 재발화 축이 시간이 아니라 가격(앵커)이고, 시간 축으로 되돌리면
  "내려왔다 다시 오르는" 구간이 통째로 먹힌다
- 회수는 구간당 1회 — 앵커 상태가 마커라 복귀가 몇 window 이어져도 사건은 하나다
- 멱등 = UNIQUE(entity, session, window) + DO NOTHING — 같은 window 재판정은 무해
- 트리거 행 + 앵커 + outbox 는 **한 트랜잭션** — 한쪽만 남으면 유령 트리거/무설명
  발화이거나, 앵커만 움직여 재발화가 영영 막힌다
- 시가 폴백의 해소 규칙(v1 유지): 첫 window 가 미커밋이면 transient(시간이 풀어 준다),
  커밋됐는데 레코드가 없으면 **사유와 함께 MISSING 확정**(조용한 건너뛰기 금지)

픽스처는 실제 Worker 파이프라인(수집→artifact PUT→fenced commit→job/outbox)을 그대로
돌려 만든다 — payload 형상·artifact 경로를 손으로 합성하면 생산자와 어긋나도 초록이다.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.consumer import PermanentJobError, TransientJobError
from data_pipeline.minute.jobs import JobLedger
from data_pipeline.minute.models import (
    KST,
    CollectionResult,
    Universe,
    plan_session_windows,
)
from data_pipeline.minute.price_consumer import (
    EXPOSURE_EVENT_TYPE,
    TRIGGER_EVENT_TYPE,
    PriceTriggerHandler,
)
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.worker import PriceWorker, WorkerConfig

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 9, 10, tzinfo=KST)
UNIVERSE = Universe(
    universe_version="univ-test-v1",
    etf_ids=("500000", "500001"),
    constituent_ids=("100000",),
)
THRESHOLD = Decimal("0.05")
REVERT = Decimal("0.01")
POLICY = "intraday-anchor-v2"
DESTINATION = "price-explanation-realtime"


class ScriptedCollector:
    """window 별로 지정한 (open, close) 를 내는 collector — 판정 입력을 제어한다.

    prices: {unit_id: [(open, close), ...]} — index = **window 순번**(09:00 부터 분).
    호출 순서가 아니다: Worker 는 realtime lane(최신)을 먼저 수집하므로 호출 순서로
    매기면 시나리오가 뒤집힌 채 초록이 된다. None/부재는 그 window 에서 missing 이다.
    """

    def __init__(self, prices: dict[str, list]):
        self.prices = prices

    def collect(self, request, now):
        start = request.window_start.astimezone(KST)
        index = start.hour * 60 + start.minute - (9 * 60)  # 09:00 = window 0
        records = []
        received, missing = [], []
        for unit_id in sorted(request.unit_ids):
            series = self.prices.get(unit_id, [])
            # 음수 index(09:00 이전 window)는 시리즈 밖 = missing 이다 — 상한만 보면
            # 파이썬 음수 인덱싱이 IndexError(2칸 시리즈) 또는 꼬리 읽기(긴 시리즈)로
            # 새서, 시간외 회귀 테스트가 검증 전에 깨지거나 엉뚱한 값으로 초록이 된다
            if not 0 <= index < len(series) or series[index] is None:
                missing.append(unit_id)
                continue
            open_price, close_price = series[index]
            received.append(unit_id)
            records.append({
                "unit_id": unit_id, "ts": request.window_start.isoformat(),
                "open": str(open_price), "high": str(max(open_price, close_price)),
                "low": str(min(open_price, close_price)), "close": str(close_price),
                "volume": "10",
            })
        status = "VALID" if not missing else "INCOMPLETE"
        result = CollectionResult(
            status=status, expected_count=len(request.unit_ids),
            succeeded_count=len(received), failed_count=len(missing),
            retry_count=0, artifact_uri="memory://x",
            manifest_checksum="a" * 64, result_checksum="b" * 64,
            watermark_before=None, watermark_after=request.window_end,
            generation=1,
            stage_timestamps={"collection_started_at": now},
        )
        manifest = {"received": received, "no_trade": [], "missing": missing,
                    "invalid": []}
        return result, tuple(records), manifest


def build_pipeline(db, tmp_path, *, prices, windows=2, universe=UNIVERSE,
                   recovery_budget=2):
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    planned = plan_session_windows(SESSION_DATE, universe=universe)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version=universe.universe_version, universe_hash=universe.universe_hash,
        windows=planned[:windows],
    )
    worker = PriceWorker(
        session_id=session_id, ledger=ledger,
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(root=tmp_path),
        collector=ScriptedCollector(prices),
        config=WorkerConfig(
            worker_id="w1", dataset="price_minute", source="toss", market="KR",
            session_date="2026-07-31", universe=universe, run_id="run_t",
            trigger_schema_version="trig-1", destination="price-analysis-realtime",
            lease_seconds=60, recovery_budget_per_tick=recovery_budget,
        ),
    )
    return worker, ledger, session_id


def build_handler(db, tmp_path, **overrides):
    base = dict(
        db=_DB, storage=LocalStorage(root=tmp_path),
        jobs=JobLedger(db=_DB, connect_fn=db.connect),
        etf_ids=frozenset(UNIVERSE.etf_ids),
        universe_version=UNIVERSE.universe_version,
        universe_hash=UNIVERSE.universe_hash, abs_threshold=THRESHOLD,
        revert_threshold=REVERT,
        detection_policy_version=POLICY, destination=DESTINATION,
        connect_fn=db.connect,
    )
    return PriceTriggerHandler(**{**base, **overrides})


def trigger_events(db):
    return [e for e in db.outbox.values() if e["event_type"] == TRIGGER_EVENT_TYPE]


def revert_events(db):
    return [e for e in db.outbox.values() if e["event_type"] == EXPOSURE_EVENT_TYPE]


def claim_then_run(handler, event, *, payload=None):
    """kernel _prepare 와 같은 순서(claim→handler) — persist 의 attempt fence 전제.

    handler 를 claim 없이 직접 부르면 fence(#485 봇 P1)가 CLAIM_SUPERSEDED 로 막는다 —
    그게 바로 계약이므로 테스트도 같은 경로를 밟는다.
    """
    claim = handler.jobs.claim_job(
        kind="price", job_id=event["payload"]["job_id"], redrive_generation=0,
        worker_id="c-test", now=datetime.now(KST), lease_seconds=600,
    )
    assert isinstance(claim, dict), f"claim 실패: {claim!r}"
    return handler(job_id=event["payload"]["job_id"],
                   payload=payload if payload is not None else event["payload"],
                   attempt=claim["attempt_count"], redrive_generation=0)


def price_job_events(db):
    """Worker 가 만든 price job outbox event — SQS 로 나갈 바로 그 payload."""
    return sorted(
        (e for e in db.outbox.values() if e["event_type"] == "PriceWindowCommitted"),
        key=lambda e: e["payload"]["window_start"],
    )


class TestJudgement:
    def test_fire_inserts_trigger_and_outbox_in_one_tx(self, tmp_path):
        # window 0: 시가 100 확정. window 1: close 110 → +10% ≥ 5% → 발화
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (108, 110)],
                    "500001": [(200, 200), (200, 201)],
                    "100000": [(50, 50), (50, 50)]},
        )
        assert worker.tick(NOW) == "PROCESSED"
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        before = db.connect_calls
        checksum = claim_then_run(handler, second)
        assert len(checksum) == 64
        [trigger] = db.triggers.values()
        assert trigger["entity_id"] == "500000"
        assert trigger["detection_policy_version"] == POLICY
        event = db.outbox[f"{TRIGGER_EVENT_TYPE}:{trigger['trigger_id']}:0"]
        assert event["destination"] == DESTINATION
        assert event["payload"]["entity_id"] == "500000"
        # 500001(+0.5%)·100000(ETF 아님)은 발화하지 않는다
        assert len(db.triggers) == 1

    def test_trigger_and_event_share_one_transaction(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 220)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        # 시가 원장을 먼저 확정해 두면(첫 job 실행) 판정 쓰기 tx 만 남는다
        first = price_job_events(db)[0]
        claim_then_run(handler, first)
        before = db.connect_calls
        claim_then_run(handler, second)
        # connect = 트랜잭션이다(fake 계약). claim 1 + identity 1 + universe 대조 1
        # + window checksum 1 + 시가 select 1(전일 종가는 세션 캐시라 0) + 앵커 select 1
        # + **쓰기 1** — 트리거 2건·앵커 2건·event 2건이 마지막 connect 하나에 있다.
        # 쓰기가 쪼개지면 부분 확정이 가능해진다.
        assert db.connect_calls == before + 7
        assert len(db.triggers) == 2
        assert sum(e["event_type"] == TRIGGER_EVENT_TYPE for e in db.outbox.values()) == 2

    def test_below_threshold_no_trigger(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 104)],   # +4% < 5%
                    "500001": [(200, 200), (200, 205)],   # +2.5%
                    "100000": [(50, 50), (50, 100)]},     # ETF 아님 — 대상 밖
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        assert db.triggers == {}
        assert not any(e["event_type"] == TRIGGER_EVENT_TYPE for e in db.outbox.values())

    def test_negative_move_also_fires(self, tmp_path):
        # 절대값 판정 — 하락도 발화한다
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 94)],    # -6%
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        assert len(db.triggers) == 1


class TestAnchorV2:
    """판정식 v2 계약 (ALPHA-745) — 기준선=전일 종가, 재발화=가변 앵커, 회수 사건.

    v1 대비 뒤집힌 것만 여기 모은다: 갭 개장이 첫 window 에 발화한다 / 재발화가 시간이
    아니라 가격에 걸린다 / 복귀가 사건이 된다 / 멱등 축이 window 다.
    """

    def _run(self, db, tmp_path, prices, *, prev_closes, windows=2, upto=None):
        db.prev_closes.update(prev_closes)
        # recovery 예산은 계획 window 수에 맞춘다 — 부족하면 마지막 window 가 아예
        # 커밋되지 않아, 시나리오의 마지막 판정이 **실행되지 않은 채** 초록이 된다
        worker, _, session_id = build_pipeline(db, tmp_path, prices=prices,
                                               windows=windows,
                                               recovery_budget=windows - 1)
        assert worker.tick(NOW) == "PROCESSED"
        handler = build_handler(db, tmp_path)
        events = price_job_events(db)
        assert len(events) == windows, f"계획 {windows} window 중 {len(events)} 만 커밋됐다"
        for event in events[:upto]:
            claim_then_run(handler, event)
        return handler, session_id, events

    def test_gap_open_fires_on_first_window(self, tmp_path):
        # v1 은 세션 시가가 기준선이라 **갭 개장이 통째로 안 보였다** — 전일 100 이
        # 106 에 열리면 그 자체가 설명 대상인데 시가 대비 0% 라 영영 발화하지 않았다.
        # v2 는 첫 window 에서 바로 발화한다.
        db = FakeMinuteDB()
        _, session_id, _ = self._run(
            db, tmp_path,
            prices={"500000": [(106, 106), (106, 106)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            upto=1,
        )
        [trigger] = db.triggers.values()
        assert trigger["entity_id"] == "500000"
        # 기준선이 트리거 행의 open_price 다 — 엔진 관측치가 전일 대비 축이 되는 배선
        assert Decimal(str(trigger["open_price"])) == 100
        assert Decimal(str(trigger["anchor_price"])) == 100  # 첫 발화의 앵커=기준선
        # 전일 종가가 있으면 시가 원장을 아예 타지 않는다(폴백 전용)
        assert db.session_opens == {}
        assert Decimal(str(db.trigger_anchors[(session_id, "500000")]["anchor_price"])) == 106

    def test_refire_is_anchor_relative_not_time_gated(self, tmp_path):
        # 같은 2h 안이라 v1 쿨다운이면 두 번째가 죽는다 — v2 는 **직전 발화가 대비**
        # 다시 5% 움직였으므로 발화해야 한다(추세가 이어지는데 침묵하지 않는다)
        db = FakeMinuteDB()
        _, session_id, events = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110), (100, 116)],
                    "500001": [(200, 200), (200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            windows=3,
        )
        # 세 window 는 전부 같은 2h 버킷이다 — 시간 축이면 1발이 정답이 된다
        starts = [datetime.fromisoformat(str(e["payload"]["window_start"]))
                  for e in events]
        assert (max(starts) - min(starts)) < timedelta(hours=2)
        assert len(db.triggers) == 2 and len(trigger_events(db)) == 2
        second = max(db.triggers.values(), key=lambda r: r["seq"])
        assert Decimal(str(second["anchor_price"])) == 110  # 직전 발화가 기준
        assert Decimal(str(db.trigger_anchors[(session_id, "500000")]["anchor_price"])) == 116

    def test_revert_emits_once_and_resets_anchor(self, tmp_path):
        # 노출 회수는 상태 전이지 반복 신호가 아니다 — 복귀 구간이 몇 window 이어져도
        # 사건은 1건이어야 소비자가 "내렸다"를 중복 처리하지 않는다
        db = FakeMinuteDB()
        _, session_id, _ = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110), (100, 100.5), (100, 100.2)],
                    "500001": [(200, 200), (200, 200), (200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50), (50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            windows=4,
        )
        assert len(db.triggers) == 1  # 발화는 window 1 한 번
        [revert] = revert_events(db)
        assert revert["destination"] == DESTINATION
        assert revert["payload"]["entity_id"] == "500000"
        assert Decimal(revert["payload"]["prev_close"]) == 100
        assert Decimal(revert["payload"]["open_change"]) <= REVERT
        # 앵커가 기준선으로 돌아왔다 — 이 상태가 곧 "회수됨" 마커다
        assert Decimal(str(db.trigger_anchors[(session_id, "500000")]["anchor_price"])) == 100

    def test_no_exposure_means_no_revert_event(self, tmp_path):
        # 발화한 적 없는 종목이 기준선 근처에 있는 건 **정상**이다 — 매분 회수 사건이
        # 나가면 설명 큐가 무의미한 상태 전이로 덮인다
        db = FakeMinuteDB()
        self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 100.3)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
        )
        assert db.triggers == {} and revert_events(db) == []
        assert db.trigger_anchors == {}

    def test_reset_then_refire(self, tmp_path):
        # v1 의 진짜 공백: 발화 → 복귀 → **재상승**이 앵커가 발화가에 얼어 있어 다시
        # 발화하지 못했다. 회수 리셋이 기준선을 되돌려 새 국면이 다시 발화한다
        db = FakeMinuteDB()
        self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110), (100, 100.5), (100, 106)],
                    "500001": [(200, 200), (200, 200), (200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50), (50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            windows=4,
        )
        assert len(db.triggers) == 2 and len(revert_events(db)) == 1
        last = max(db.triggers.values(), key=lambda r: r["seq"])
        assert Decimal(str(last["close_price"])) == Decimal("106")
        # 리셋된 앵커(=기준선 100) 대비 판정이었다
        assert Decimal(str(last["anchor_price"])) == 100

    def test_same_window_rejudge_does_not_double_fire(self, tmp_path):
        # SQS 는 at-least-once 다. 뒤 window 가 앵커를 올린 뒤 앞 window 가 재배달되면
        # 앵커 대비로는 다시 임계를 넘는다 — window 유니크가 없으면 같은 분에 대해
        # 설명이 두 번 나간다
        db = FakeMinuteDB()
        handler, session_id, events = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110), (100, 121)],
                    "500001": [(200, 200), (200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            windows=3,
        )
        assert len(db.triggers) == 2
        anchor_before = db.trigger_anchors[(session_id, "500000")]["anchor_price"]
        # 재배달 = 같은 claim(attempt 그대로)의 두 번째 실행 — attempt fence 는 통과하고
        # window 유니크만이 막는다
        redelivered = events[1]["payload"]
        handler(job_id=redelivered["job_id"], payload=redelivered,
                attempt=db.jobs[("price", redelivered["job_id"])]["attempt_count"],
                redrive_generation=0)
        assert len(db.triggers) == 2 and len(trigger_events(db)) == 2
        # 앵커도 되돌아가지 않는다 — 트리거 INSERT 가 막히면 앵커 이동도 없다
        assert db.trigger_anchors[(session_id, "500000")]["anchor_price"] == anchor_before

    def test_stale_revert_does_not_undo_newer_fire(self, tmp_path):
        # SQS 는 순서를 보장하지 않는다. 회수했던 window 가 **더 뒤 window 의 발화 뒤에**
        # 재배달되면, 조건부 UPDATE 만으로는 앵커가 기준선으로 되돌아간다 — 그런데 그
        # 회수의 event_id 는 그 window 로 결정적이라 이미 나간 사건과 충돌해 outbox 는
        # DO NOTHING 이다. 결과는 "앵커는 회수됐는데 하류는 노출 그대로"다.
        db = FakeMinuteDB()
        handler, session_id, events = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110), (100, 100.5), (100, 106)],
                    "500001": [(200, 200), (200, 200), (200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50), (50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            windows=4,
        )
        assert len(db.triggers) == 2 and len(revert_events(db)) == 1
        anchor_before = db.trigger_anchors[(session_id, "500000")]["anchor_price"]
        assert Decimal(str(anchor_before)) == 106  # 마지막 발화가
        stale = events[2]["payload"]  # 회수했던 window 의 재배달
        handler(job_id=stale["job_id"], payload=stale,
                attempt=db.jobs[("price", stale["job_id"])]["attempt_count"],
                redrive_generation=0)
        assert db.trigger_anchors[(session_id, "500000")]["anchor_price"] == anchor_before
        assert len(revert_events(db)) == 1

    def test_high_precision_prev_close_reverts_once(self, tmp_path):
        # price_daily.close_price 는 NUMERIC(24,8), 앵커는 NUMERIC(24,6) 이다. 기준선을
        # 저장 스케일로 맞추지 않으면 `anchor_price <> 기준선` 이 영구히 참이 돼
        # 복귀 구간 매 window 마다 회수 사건이 재발행된다(구간당 1회 계약 위반)
        db = FakeMinuteDB()
        base = Decimal("100.12345678")
        db.prev_closes["500001"] = Decimal("200")
        _, session_id, _ = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110), (100, 100.5), (100, 100.2)],
                    "500001": [(200, 200), (200, 200), (200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50), (50, 50), (50, 50)]},
            prev_closes={"500000": base},
            windows=4,
        )
        assert len(revert_events(db)) == 1
        anchor = db.trigger_anchors[(session_id, "500000")]["anchor_price"]
        assert anchor == base.quantize(Decimal("0.000001"))

    def test_missing_prev_close_falls_back_to_session_open(self, tmp_path):
        # 신규 상장 등 전일 종가가 없는 종목만 v1 기계(세션 시가)로 판정한다 —
        # 전 종목을 폴백으로 돌리면 v2 가 조용히 무효가 된다
        db = FakeMinuteDB()
        _, session_id, _ = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            prev_closes={"500001": Decimal("200")},  # 500000 만 결손
        )
        [trigger] = db.triggers.values()
        assert trigger["entity_id"] == "500000"
        assert Decimal(str(trigger["open_price"])) == 100  # 세션 시가가 기준선
        # 시가 해소는 **결손분에만** 걸렸다
        assert set(db.session_opens) == {(session_id, "500000")}

    def test_prev_close_unblocks_uncommitted_first_window(self, tmp_path):
        # v1 은 시가가 기준선이라 첫 window 미커밋이 판정을 막았다(transient). v2 는
        # 기준선이 전일 종가라 realtime lane 이 backlog 착지를 기다릴 이유가 없다
        db = FakeMinuteDB()
        db.prev_closes.update({"500000": Decimal("100"), "500001": Decimal("200")})
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 110), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            recovery_budget=0,  # 최신 window 만 처리 — 첫 window 미커밋
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        [event] = price_job_events(db)
        assert len(claim_then_run(handler, event)) == 64
        assert len(db.triggers) == 1
        assert db.session_opens == {}  # 시가 원장을 탈 이유가 없다

    def test_prev_close_read_once_per_session(self, tmp_path):
        # 세션 중 price_daily 는 안 바뀐다 — window 마다 363종을 다시 읽으면 매분
        # 판정에 쓸데없는 왕복이 붙는다
        db = FakeMinuteDB()
        handler, _, events = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("100"), "500001": Decimal("200")},
            upto=1,
        )
        cached = dict(handler._prev_close_cache)
        claim_then_run(handler, events[1])
        assert handler._prev_close_cache == cached and len(cached) == 1

    def test_non_positive_prev_close_falls_back(self, tmp_path):
        # 계약 위반 기준선(0)으로 판정하면 change_rate 가 무한이 돼 전 종목이 발화한다
        db = FakeMinuteDB()
        _, session_id, _ = self._run(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            prev_closes={"500000": Decimal("0"), "500001": Decimal("200")},
        )
        assert set(db.session_opens) == {(session_id, "500000")}
        [trigger] = db.triggers.values()
        assert Decimal(str(trigger["open_price"])) == 100  # 시가 폴백

    def test_non_positive_revert_threshold_rejected(self, tmp_path):
        # 0 이면 "정확히 기준선"일 때만 회수가 돼 노출이 사실상 영구 유지된다
        with pytest.raises(ValueError, match="revert_threshold"):
            build_handler(FakeMinuteDB(), tmp_path, revert_threshold=Decimal("0"))


class TestOpenLedger:
    def test_open_is_first_window_open_and_immutable(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 101), (999, 110)],  # 시가는 첫 window open=100
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        open_row = db.session_opens[(session_id, "500000")]
        assert open_row["status"] == "OPEN"
        assert Decimal(str(open_row["open_price"])) == 100  # 999(둘째 open)가 아니다
        # 110/100 - 1 = +10% → 발화 (시가가 둘째 window open 이었다면 미발화)
        assert len(db.triggers) == 1

    def test_missing_first_record_decides_missing_with_reason(self, tmp_path):
        # 500001 은 첫 window 에 레코드가 없다(INCOMPLETE) — 시가 MISSING 확정 + 사유,
        # 이후 window 에서 큰 변동이 와도 조용히가 아니라 **기록된 채** 건너뛴다
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 100)],
                    "500001": [None, (200, 260)],        # +30% 이지만 시가가 없다
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        row = db.session_opens[(session_id, "500001")]
        assert row["status"] == "MISSING" and row["reason"]
        assert db.triggers == {}

    def test_uncommitted_first_window_is_transient(self, tmp_path):
        # recovery budget 0 → realtime lane 이 최신(둘째) window 만 처리 — 첫 window
        # 미커밋. 시가를 지금 MISSING 으로 확정하면 시간 문제가 영구 결손이 된다
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 110), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
            recovery_budget=0,
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        [event] = price_job_events(db)
        with pytest.raises(TransientJobError, match="미커밋"):
            claim_then_run(handler, event)
        assert db.session_opens == {} and db.triggers == {}


class TestContracts:
    def _pipeline(self, db, tmp_path):
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        return session_id

    def test_payload_job_mismatch_is_permanent(self, tmp_path):
        db = FakeMinuteDB()
        self._pipeline(db, tmp_path)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        tampered = dict(second["payload"], generation=99)
        with pytest.raises(PermanentJobError, match="다른 window"):
            claim_then_run(handler, second, payload=tampered)

    def test_malformed_payload_is_transient(self, tmp_path):
        db = FakeMinuteDB()
        self._pipeline(db, tmp_path)
        handler = build_handler(db, tmp_path)
        with pytest.raises(TransientJobError, match="payload"):
            handler(job_id="j", payload=[], attempt=1, redrive_generation=0)

    def test_missing_artifact_is_transient(self, tmp_path):
        db = FakeMinuteDB()
        self._pipeline(db, tmp_path)
        # 다른(빈) 스토리지를 보는 handler — artifact 만 없다
        handler = build_handler(db, tmp_path / "empty")
        second = price_job_events(db)[1]
        with pytest.raises(TransientJobError, match="artifact"):
            claim_then_run(handler, second)

    def test_empty_etf_set_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="etf_ids"):
            build_handler(FakeMinuteDB(), tmp_path, etf_ids=frozenset())

    def test_non_positive_threshold_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="양수"):
            build_handler(FakeMinuteDB(), tmp_path, abs_threshold=Decimal("0"))


class TestAdversarialInputs:
    """리뷰 라운드 1이 확인한 반례 고정 (coerce-to-passing·행 격리·전 종목 확정)."""

    def test_opens_decided_for_all_etfs_even_absent_ones(self, tmp_path):
        # 500001 이 어느 artifact 에도 안 실려도 시가 원장에는 MISSING+사유가 남는다 —
        # 현재 window 등장 종목으로 좁히면 하루 종일 이유 없는 공백이 된다
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 100)],
                    "500001": [None, None],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        row = db.session_opens[(session_id, "500001")]
        assert row["status"] == "MISSING" and row["reason"]

    def test_non_positive_close_does_not_fire(self, tmp_path):
        # close 0 은 change_rate 1 로 임계를 통과한다 — 계약 위반 가격은 발화가 아니라
        # 판정 오류다(로그·결과에 남고 트리거는 없다)
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 0)],
                    "500001": [(200, 200), (200, -5)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        assert db.triggers == {}

    def test_non_positive_first_open_decides_missing(self, tmp_path):
        # 첫 window open=0 을 OPEN 으로 불변 확정하면 그 ETF 는 하루 종일 판정
        # 오류로만 돌고 복구 경로가 없다 — MISSING+사유로 확정한다
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(0, 100), (100, 200)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim_then_run(handler, second)
        row = db.session_opens[(session_id, "500000")]
        assert row["status"] == "MISSING" and "계약 위반" in row["reason"]
        assert db.triggers == {}

    def test_malformed_record_isolated_to_entity(self, tmp_path):
        # 한 종목의 close 결손이 job 전체를 죽이면 다른 정상 ETF 판정까지 사라진다 —
        # 행 단위로 격리하고 정상 종목은 발화한다
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 220)],
                    "100000": [(50, 50), (50, 50)]},
        )
        # 500001 레코드에 close 가 없는 채 **정당하게 커밋**된 상황을 만든다 —
        # 사후 바이트 변조는 checksum 검증이 잡는 다른 결함이다
        class CloseStripping(ScriptedCollector):
            def collect(self, request, now):
                result, records, manifest = super().collect(request, now)
                stripped = tuple(
                    {k: v for k, v in r.items() if k != "close"}
                    if r["unit_id"] == "500001" else r
                    for r in records
                )
                return result, stripped, manifest

        worker.collector = CloseStripping(worker.collector.prices)
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        checksum = claim_then_run(handler, second)
        assert len(checksum) == 64  # job 은 산다
        [trigger] = db.triggers.values()
        assert trigger["entity_id"] == "500000"  # 정상 종목은 발화했다

    def test_numeric_session_id_rejected(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 100)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        tampered = dict(second["payload"], session_id=123)
        with pytest.raises(TransientJobError, match="session_id"):
            claim_then_run(handler, second, payload=tampered)

    def test_stale_generation_does_not_persist_trigger(self, tmp_path):
        # 실행 중 window 가 정정(gen+1)되면 gen-1 발화를 커밋하지 않는다 — 커밋되면
        # gen-2 판정이 cooldown UNIQUE 에 막혀 정정 전 결과가 정본이 된다
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        # claim 은 정상(그 시점 세대 일치) — **실행 중** window 가 정정된 상황.
        # claim 전에 정정되면 kernel 의 stale 검사가 DEAD('STALE') 로 걸러낸다
        claim = handler.jobs.claim_job(
            kind="price", job_id=second["payload"]["job_id"], redrive_generation=0,
            worker_id="c-test", now=datetime.now(KST), lease_seconds=600,
        )
        assert isinstance(claim, dict)
        window_start = datetime.fromisoformat(str(second["payload"]["window_start"]))
        for (sid, ws), row in db.windows.items():
            if sid == session_id and ws == window_start:
                row["generation"] = 2
        with pytest.raises(TransientJobError, match="정정"):
            handler(job_id=second["payload"]["job_id"], payload=second["payload"],
                    attempt=claim["attempt_count"], redrive_generation=0)
        assert db.triggers == {}
        assert not any(e["event_type"] == TRIGGER_EVENT_TYPE for e in db.outbox.values())

    def test_extended_session_open_comes_from_regular_first_window(self, tmp_path):
        # 시간외 선언 세션은 08:00 부터 window 가 있다 — 세션 첫 window 를 쓰면 정규장
        # 전용 ETF 전부가 08:00 부재로 MISSING 영구 확정된다(#485 봇 P1). 시가 기준은
        # **정규장 첫 window(09:00)** 다.
        universe_ext = Universe(
            universe_version="univ-ext-v1",
            etf_ids=("500000", "500001"),
            constituent_ids=("100000",),
            extended_hours_ids=("100000",),
        )
        db = FakeMinuteDB()
        # 720 계획의 앞부분: 08:00(시간외, 개별주만) + 09:00·09:01(정규장)
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=universe_ext)
        chosen = [planned[0]] + [w for w in planned
                                 if w[0].astimezone(KST).hour == 9][:2]
        session_id, _ = ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version=universe_ext.universe_version,
            universe_hash=universe_ext.universe_hash, windows=chosen,
        )
        worker = PriceWorker(
            session_id=session_id, ledger=ledger,
            committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
            storage=LocalStorage(root=tmp_path),
            collector=ScriptedCollector({
                # index 는 09:00 기준 분 — 08:00 window 는 index -60 이라 시리즈 밖
                # (missing)이고, 시간외 기대는 100000 뿐이라 INCOMPLETE 로 커밋된다
                "500000": [(100, 100), (100, 110)],
                "500001": [(200, 200), (200, 200)],
                "100000": [(50, 50), (50, 50)],
            }),
            config=WorkerConfig(
                worker_id="w1", dataset="price_minute", source="toss", market="KR",
                session_date="2026-07-31", universe=universe_ext, run_id="run_t",
                trigger_schema_version="trig-1",
                destination="price-analysis-realtime", lease_seconds=60,
                recovery_budget_per_tick=3,
            ),
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path,
                                universe_version=universe_ext.universe_version,
                                universe_hash=universe_ext.universe_hash)
        events = price_job_events(db)
        # 09:01 window 판정 — 시가는 09:00 open(100)이고, 08:00 부재는 무관하다
        target = [e for e in events if "T00:01" in str(e["payload"]["window_start"])][0]
        claim_then_run(handler, target)
        row = db.session_opens[(session_id, "500000")]
        assert row["status"] == "OPEN"
        assert Decimal(str(row["open_price"])) == 100
        [trigger] = db.triggers.values()  # 110/100 → +10% 발화
        assert trigger["entity_id"] == "500000"

    def test_open_not_frozen_from_superseded_first_window(self, monkeypatch, tmp_path):
        # 첫 window artifact 를 읽은 뒤 INSERT 전에 그 window 가 정정(gen+1)되면
        # 낡은 시가를 불변 동결하지 않고 재시도한다(#485 봇 P2)
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        first_start = min(ws for (sid, ws) in db.windows if sid == session_id)

        original = handler._artifact_rows

        def corrupting_read(session_date, window_start, generation, **kwargs):
            rows = original(session_date, window_start, generation, **kwargs)
            if window_start == first_start:
                # artifact 읽기 직후·INSERT 전에 첫 window 가 정정된 상황
                db.windows[(session_id, first_start)]["generation"] = 2
            return rows

        monkeypatch.setattr(handler, "_artifact_rows", corrupting_read)
        with pytest.raises(TransientJobError, match="첫 window"):
            claim_then_run(handler, second)
        assert db.session_opens == {}  # 낡은 시가가 동결되지 않았다

    def test_superseded_claim_cannot_persist_trigger(self, tmp_path):
        # lease 상실 뒤 다른 Consumer 가 재claim(attempt+1)한 job — 낡은 attempt 의
        # 발화가 UNIQUE 를 선점하면 새 attempt(새 설정)의 판정이 영구히 막힌다
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim = handler.jobs.claim_job(
            kind="price", job_id=second["payload"]["job_id"], redrive_generation=0,
            worker_id="c-old", now=datetime.now(KST), lease_seconds=600,
        )
        # 낡은 attempt 가 아직 도는 사이 재claim 이 attempt 를 올린 상황
        db.jobs[("price", second["payload"]["job_id"])]["attempt_count"] += 1
        with pytest.raises(TransientJobError, match="내 것이 아니다"):
            handler(job_id=second["payload"]["job_id"], payload=second["payload"],
                    attempt=claim["attempt_count"], redrive_generation=0)
        assert db.triggers == {}

    def test_artifact_checksum_mismatch_is_transient(self, tmp_path):
        # 원장 checksum 과 다른 바이트로 판정하면 잘못된 canonical(동시 PUT 경합,
        # ALPHA-704)이 그대로 발화한다 — 재해시 검증이 소비자 계약이다
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        storage = LocalStorage(root=tmp_path)
        [key] = [k for k in storage.list_keys("")
                 if k.endswith("bars.ndjson") and "window=0901" in k]
        (Path(tmp_path) / key).write_bytes(b'{"unit_id": "500000", "close": "999"}\n')
        with pytest.raises(TransientJobError, match="checksum"):
            claim_then_run(handler, second)
        assert db.triggers == {}

    def test_extended_hours_etf_rejected_at_startup(self, tmp_path):
        # 시간외 거래 ETF 의 시가는 09:00 축이 아니다 — 지원 없이 받으면 조용히 틀린
        # 축으로 판정하므로 기동에서 거부한다(#485 봇 P2, 실측상 ETF 는 전부 정규장)
        with pytest.raises(ValueError, match="시간외 거래 ETF"):
            build_handler(FakeMinuteDB(), tmp_path,
                          extended_hours_ids=frozenset({"500000"}))

    def test_regular_window_all_etfs_missing_still_records_reason(self, tmp_path):
        # 정규장 window 의 전 ETF 부재는 정상이 아니라 결측(장애)이다 — 시각이 아니라
        # 행 유무로 접으면 하루 장애가 사유 없이 전부 성공으로 끝난다(#485 봇 P2).
        # 시가 해소를 타서 MISSING 사유가 원장에 남는다
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [None, None],
                    "500001": [None, None],
                    "100000": [(50, 50), (50, 51)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        checksum = claim_then_run(handler, second)
        assert len(checksum) == 64
        for entity in ("500000", "500001"):
            row = db.session_opens[(session_id, entity)]
            assert row["status"] == "MISSING" and row["reason"]
        assert db.triggers == {}

    def test_pre_open_window_succeeds_without_open_resolution(self, tmp_path):
        # 09:00 이전 window 는 ETF 가 기대되지 않는다 — 시가 해소를 걸면 09:00 커밋
        # 전까지 재시도만 돌다 예산 소진으로 DEAD 가 된다. 할 일 없음=성공
        universe_ext = Universe(
            universe_version="univ-ext-v2",
            etf_ids=("500000", "500001"),
            constituent_ids=("100000",),
            extended_hours_ids=("100000",),
        )
        db = FakeMinuteDB()
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        planned = plan_session_windows(SESSION_DATE, universe=universe_ext)
        session_id, _ = ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version=universe_ext.universe_version,
            universe_hash=universe_ext.universe_hash, windows=planned[:1],  # 08:00
        )
        worker = PriceWorker(
            session_id=session_id, ledger=ledger,
            committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
            storage=LocalStorage(root=tmp_path),
            collector=ScriptedCollector({"100000": [(50, 50)]}),
            config=WorkerConfig(
                worker_id="w1", dataset="price_minute", source="toss", market="KR",
                session_date="2026-07-31", universe=universe_ext, run_id="run_t",
                trigger_schema_version="trig-1",
                destination="price-analysis-realtime", lease_seconds=60,
            ),
        )

        class PreOpenCollector(ScriptedCollector):
            def collect(self, request, now):
                # 08:00 window 의 기대 유니버스는 시간외 종목뿐 — 그 봉을 그대로 낸다
                records = ({"unit_id": "100000", "ts": request.window_start.isoformat(),
                            "open": "50", "high": "50", "low": "50", "close": "50",
                            "volume": "10"},)
                from data_pipeline.minute.models import CollectionResult
                result = CollectionResult(
                    status="VALID", expected_count=len(request.unit_ids),
                    succeeded_count=len(request.unit_ids), failed_count=0,
                    retry_count=0, artifact_uri="memory://x",
                    manifest_checksum="a" * 64, result_checksum="b" * 64,
                    watermark_before=None, watermark_after=request.window_end,
                    generation=1,
                    stage_timestamps={"collection_started_at": now},
                )
                manifest = {"received": list(request.unit_ids), "no_trade": [],
                            "missing": [], "invalid": []}
                return result, records, manifest

        worker.collector = PreOpenCollector({})
        worker.tick(datetime(2026, 7, 31, 8, 5, tzinfo=KST))
        handler = build_handler(db, tmp_path,
                                universe_version=universe_ext.universe_version,
                                universe_hash=universe_ext.universe_hash)
        [event] = price_job_events(db)
        checksum = claim_then_run(handler, event)
        assert len(checksum) == 64
        assert db.session_opens == {}  # 시가 해소 자체를 걸지 않았다
        assert db.triggers == {}

    def test_in_flight_correction_blocks_trigger_persist(self, tmp_path):
        # 정정 진행 중(재claim)엔 세대가 아직 옛값이다 — 상태 대조 없이는 낡은 발화가
        # UNIQUE 를 선점해 정정 세대 판정이 막힌다(#485 봇 P2)
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        claim = handler.jobs.claim_job(
            kind="price", job_id=second["payload"]["job_id"], redrive_generation=0,
            worker_id="c-test", now=datetime.now(KST), lease_seconds=600,
        )
        window_start = datetime.fromisoformat(str(second["payload"]["window_start"]))
        db.windows[(session_id, window_start)]["data_status"] = "CLAIMED"
        with pytest.raises(TransientJobError, match="정정"):
            handler(job_id=second["payload"]["job_id"], payload=second["payload"],
                    attempt=claim["attempt_count"], redrive_generation=0)
        assert db.triggers == {}

    def test_in_flight_first_window_correction_blocks_open(self, tmp_path):
        # 첫 window 가 정정 진행 중이면 시가를 확정하지 않는다 — 확정하면 낡은 시가가
        # 불변 동결되고 정정 세대가 DO NOTHING 에 막힌다(#485 봇 P2)
        db = FakeMinuteDB()
        worker, _, session_id = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path)
        second = price_job_events(db)[1]
        first_start = min(ws for (sid, ws) in db.windows if sid == session_id)

        original = handler._artifact_rows

        def racing_read(session_date, window_start, generation, **kwargs):
            rows = original(session_date, window_start, generation, **kwargs)
            if window_start == first_start:
                db.windows[(session_id, first_start)]["data_status"] = "CLAIMED"
            return rows

        import pytest as _pytest
        monkey = _pytest.MonkeyPatch()
        monkey.setattr(handler, "_artifact_rows", racing_read)
        try:
            with pytest.raises(TransientJobError, match="정정"):
                claim_then_run(handler, second)
        finally:
            monkey.undo()
        assert db.session_opens == {}


    def test_universe_version_mismatch_is_transient(self, tmp_path):
        # 상주 Consumer 가 날을 넘겨 어제 집합으로 오늘 세션을 판정하면 빠진 ETF 가
        # 사유 없이 제외된다 — 세션이 고정한 universe_version 과 대조한다(#485 봇 P2)
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path, universe_version="univ-yesterday")
        second = price_job_events(db)[1]
        with pytest.raises(TransientJobError, match="universe"):
            claim_then_run(handler, second)
        assert db.triggers == {} and db.session_opens == {}

    def test_universe_hash_mismatch_is_transient(self, tmp_path):
        # version 을 재사용한 배포(구성만 다름)는 hash 만이 잡는다 — worker 와 동일 축
        db = FakeMinuteDB()
        worker, _, _ = build_pipeline(
            db, tmp_path,
            prices={"500000": [(100, 100), (100, 110)],
                    "500001": [(200, 200), (200, 200)],
                    "100000": [(50, 50), (50, 50)]},
        )
        worker.tick(NOW)
        handler = build_handler(db, tmp_path, universe_hash="0" * 64)
        second = price_job_events(db)[1]
        with pytest.raises(TransientJobError, match="universe"):
            claim_then_run(handler, second)
        assert db.triggers == {}


class TestPriceConsumerCli:
    """진입점 fail-loud + bounded 게이트 (ALPHA-711, relay_cli 동형)."""

    def _options(self, **overrides):
        from types import SimpleNamespace
        base = dict(queue_url="https://sqs/price", detection_policy_version=POLICY,
                    destination=DESTINATION, batch_size=1, wait_seconds=0,
                    visibility_seconds=300, heartbeat_seconds=60, max_concurrency=1,
                    lease_seconds=600, retry_base_seconds=5, retry_max_seconds=900,
                    max_attempts=5)
        return SimpleNamespace(**{**base, **overrides})

    def _settings(self, *, db=None, options=None, triggers=True):
        from types import SimpleNamespace
        return SimpleNamespace(
            db=db, minute_price_consumer=options,
            price_triggers=(SimpleNamespace(abs_threshold=0.05, revert_threshold=0.01)
                            if triggers else None),
            storage=None,
        )

    def _universe_file(self, tmp_path):
        import json as _json
        path = tmp_path / "u.json"
        path.write_text(_json.dumps({
            "universe_version": UNIVERSE.universe_version,
            "etf_ids": list(UNIVERSE.etf_ids),
            "constituent_ids": list(UNIVERSE.constituent_ids),
        }), encoding="utf-8")
        return str(path)

    def test_missing_db_fails_loud(self):
        from data_pipeline.minute.price_consumer import price_consumer_cli
        with pytest.raises(SystemExit, match="db 설정 없음"):
            price_consumer_cli(self._settings(options=self._options()), universe="u")

    def test_missing_config_fails_loud(self):
        from data_pipeline.minute.price_consumer import price_consumer_cli
        with pytest.raises(SystemExit, match="minute_price_consumer"):
            price_consumer_cli(self._settings(db=_DB), universe="u")

    def test_missing_price_triggers_fails_loud(self):
        # 임계의 정본은 price_triggers 재사용이다 — 따로 받으면 일 단위와 조용히 갈린다
        from data_pipeline.minute.price_consumer import price_consumer_cli
        with pytest.raises(SystemExit, match="price_triggers"):
            price_consumer_cli(
                self._settings(db=_DB, options=self._options(), triggers=False),
                universe="u",
            )

    def test_missing_universe_fails_loud(self):
        from data_pipeline.minute.price_consumer import price_consumer_cli
        with pytest.raises(SystemExit, match="--universe"):
            price_consumer_cli(self._settings(db=_DB, options=self._options()),
                               universe=None)

    def test_bounded_idle_run_exits_clean(self, tmp_path, monkeypatch):
        from data_pipeline.minute.price_consumer import price_consumer_cli

        class EmptyQueue:
            def __init__(self, *, wait_seconds):
                self.wait_seconds = wait_seconds

            def receive(self, **kwargs):
                return []

        monkeypatch.setattr("data_pipeline.minute.consumer.SqsQueue", EmptyQueue)
        monkeypatch.setattr("data_pipeline.lake.storage.make_storage",
                            lambda config: LocalStorage(root=tmp_path))
        code = price_consumer_cli(
            self._settings(db=_DB, options=self._options()),
            universe=self._universe_file(tmp_path), max_ticks=2,
        )
        assert code == 0

    def test_bad_destination_rejected_at_startup(self):
        # 오타 destination 은 판정·job 성공까지 커밋된 뒤 Relay 가 event 를 DEAD 로
        # 격리한다(결정적 event_id 라 건별 redrive 뿐) — 기동에서 어휘로 거부한다
        from data_pipeline.minute.price_consumer import price_consumer_cli
        with pytest.raises(SystemExit, match="트리거 사건 어휘"):
            price_consumer_cli(
                self._settings(db=_DB,
                               options=self._options(destination="price-explanation-realtme")),
                universe="u",
            )
