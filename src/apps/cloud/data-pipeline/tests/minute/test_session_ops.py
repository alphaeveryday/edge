"""세션 스케일 오케스트레이션 테스트 (ALPHA-712).

의도: 이 진입점의 결함은 **조용하다**. 잘못 내리면 처리 중이던 window 가 사라지고, 잘못
올리면 하루 종일 재기동 루프만 돈다 — 둘 다 exit 0 으로 보인다. 그래서 고정하는 건 넷이다.

- **비거래일엔 아무것도 안 올린다** — 세션 없이 뜬 Worker 는 기동을 거부하고(fail-loud)
  ECS 가 하루 종일 재기동한다. 알람 소음이 곧 그 날의 관측을 덮는다.
- **계획이 실패하면 안 올린다** — 같은 이유다. 계획 exit 를 그대로 전달해 스케줄 재시도가
  같은 판정을 다시 받게 한다.
- **게이트가 안 비면 안 내린다** — 시각으로 내리면 recovery 레인이 집고 있던 window 가
  결손된다. phase·outbox·큐 깊이 셋이 순서대로 비어야 내린다.
- **스케일업은 force_new_deployment 를 동반한다** — desired 0 인 동안 CD 재배포가 no-op 라,
  빼면 직전 세션의 낡은 다이제스트로 뜬다(#488 봇 P2).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.config import DbConfig
from data_pipeline.minute import session_ops


class FakeSettings:
    def __init__(self, db=True):
        self.db = DbConfig(password="x") if db else None


class FakeEcs:
    def __init__(self):
        self.calls = []

    def update_service(self, **kwargs):
        self.calls.append(kwargs)


class FakeLedger:
    """`session_snapshot` 만 보는 게이트용 가짜 — phase 를 호출마다 순서대로 돌려준다."""

    def __init__(self, phases):
        self.phases = list(phases)
        self.observed = 0

    def session_snapshot(self, *, session_id):
        self.observed += 1
        phase = self.phases.pop(0) if len(self.phases) > 1 else self.phases[0]
        return None if phase is None else {"phase": phase}


class FakeJobs:
    def __init__(self, new=0, dead=0):
        self.counts = {"NEW": new, "DEAD": dead}

    def count_unpublished(self):
        return self.counts


@pytest.fixture
def wiring(monkeypatch):
    """env 배선 + boto3 대체. `_scale` 이 실제로 무엇을 불렀는지 남긴다."""
    ecs = FakeEcs()
    monkeypatch.setenv(session_ops.ENV_CLUSTER, "arn:aws:ecs:ap-northeast-2:1:cluster/edge-dev")
    monkeypatch.setenv(session_ops.ENV_SERVICES, "svc-worker,svc-relay,svc-consumer")
    monkeypatch.setenv(session_ops.ENV_GATE_QUEUES, "https://q/price")
    monkeypatch.delenv(session_ops.ENV_DRAIN_TIMEOUT, raising=False)
    # 기본은 단일(구동) 레인 — 승객 편입 케이스는 개별 테스트가 명시로 켠다.
    # ⚠️ 표 전체를 훑어 지운다: 레인이 늘 때 여기 한 줄을 빠뜨리면 개발자 셸의 env 가
    # 전 테스트에 새어 들어와, 켠 적 없는 레인이 계획되는 채로 초록이 된다.
    for _env_group, _env_services in session_ops.PASSENGER_LANES.values():
        monkeypatch.delenv(_env_group, raising=False)
        monkeypatch.delenv(_env_services, raising=False)
    monkeypatch.setattr(
        session_ops, "_scale",
        lambda *, desired, force, services=None: ecs.update_service(
            desiredCount=desired, forceNewDeployment=force, services=services))
    return ecs


def test_non_trading_day_scales_nothing(monkeypatch, wiring):
    """공휴일에 올리면 세션이 없어 Worker 가 하루 종일 재기동을 반복한다."""
    monkeypatch.setattr(session_ops, "is_trading_day", lambda day: False)
    planned = []
    monkeypatch.setattr(session_ops, "plan_session_cli",
                        lambda *a, **k: planned.append(k) or 0)

    rc = session_ops.start_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss", universe="s3://b/u.json")

    assert rc == 0
    assert planned == [], "비거래일에 세션을 만들면 안 된다"
    assert wiring.calls == [], "비거래일에 서비스를 올리면 안 된다"


def test_plan_failure_blocks_scale_up(monkeypatch, wiring):
    """계획 실패를 무시하고 올리면 세션 없는 Worker 가 크래시 루프를 돈다."""
    monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
    monkeypatch.setattr(session_ops, "plan_session_cli", lambda *a, **k: 1)

    rc = session_ops.start_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss", universe="s3://b/u.json")

    assert rc == 1, "계획의 exit 를 그대로 전달해야 스케줄 재시도가 같은 판정을 받는다"
    assert wiring.calls == []


def test_start_forces_new_deployment(monkeypatch, wiring):
    """desired 0 동안의 CD 재배포는 no-op 다 — force 없이 올리면 낡은 다이제스트가 뜬다."""
    monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
    monkeypatch.setattr(session_ops, "plan_session_cli", lambda *a, **k: 0)

    rc = session_ops.start_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss", universe="s3://b/u.json")

    assert rc == 0
    assert wiring.calls == [{"desiredCount": 1, "forceNewDeployment": True, "services": None}]


def test_unknown_source_group_is_rejected():
    """session_id 가 dataset·source_group 에서 결정적으로 유도된다 — 오타는 없는 세션을 가리킨다."""
    with pytest.raises(SystemExit):
        session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="tos", universe="s3://b/u.json")


@pytest.mark.parametrize(
    ("phase", "outbox_new", "queue_depth", "expected"),
    [
        ("DRAINING", 0, 0, ["session msn1\u2026 phase=DRAINING"]),
        ("DRAINED", 3, 0, ["outbox NEW=3"]),
        ("DRAINED", 0, 2, ["queue depth=2 https://q/price"]),
        ("DRAINED", 0, 0, []),
        # QC 가 이미 돌기 시작한 세션도 원장은 더 움직이지 않는다 — 내려도 잃을 게 없다
        ("FINALIZED", 0, 0, []),
        # 파이프가 순환하므로 outbox 를 큐보다 **나중에** 읽는다 — 둘 다 남았으면 큐가 먼저다
        ("DRAINED", 3, 2, ["queue depth=2 https://q/price"]),
    ],
)
def test_gate_pending_orders_the_three_stages(
    monkeypatch, phase, outbox_new, queue_depth, expected
):
    """앞 단계가 안 끝났으면 뒤를 보지 않는다 — 아직 안 만든 것과 다 나간 것은 둘 다 '0' 이다."""
    monkeypatch.setattr(session_ops, "_queue_depths",
                        lambda queues: [("https://q/price", queue_depth)])

    pending = session_ops._gate_pending(
        FakeLedger([phase]), FakeJobs(new=outbox_new),
        session_ids=["msn1"], queues=["https://q/price"],
    )

    assert pending == expected


def test_dead_outbox_does_not_block_forever(monkeypatch):
    """DEAD 는 기다려서 안 없어진다(redrive 대상) — 게이트로 세면 영영 안 내려간다."""
    monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])

    pending = session_ops._gate_pending(
        FakeLedger(["DRAINED"]), FakeJobs(new=0, dead=7), session_ids=["msn1"], queues=[])

    assert pending == []


def test_stop_waits_then_scales_down(monkeypatch, wiring):
    """게이트가 비기 전에는 폴링하고, **연속 확인**까지 끝난 뒤에야 내린다.

    큐 깊이는 approximate 라 한 번의 0 이 곧 비었음이 아니다 — 마지막 메시지를 남긴 채
    Consumer 를 내리면 그 트리거는 다음 세션까지 아무도 안 집는다.
    """
    monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
    monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)
    ledger = FakeLedger(["ACTIVE", "DRAINING", "DRAINED"])
    ledger.request_drain = lambda *, session_id, now: True
    monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
    monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

    rc = session_ops.stop_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss")

    assert rc == 0
    assert wiring.calls == [{"desiredCount": 0, "forceNewDeployment": False, "services": None}]
    # 진입 조회 1 + DRAINING 관측 1 + 연속 clear CLEAR_CONFIRMATIONS 회.
    # 한 번의 clear 로 내려가는 구현이면 이 값이 3 이다.
    assert ledger.observed == 1 + 1 + session_ops.CLEAR_CONFIRMATIONS


def test_late_message_resets_the_clear_streak(monkeypatch, wiring):
    """확인 도중 큐가 다시 차면 연속이 끊긴다 — 늦게 보이는 메시지가 게이트를 통과하면 안 된다."""
    monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)
    depths = iter([[("q", 0)], [("q", 0)], [("q", 1)]])
    monkeypatch.setattr(session_ops, "_queue_depths",
                        lambda queues: next(depths, [("q", 0)]))
    ledger = FakeLedger(["DRAINED"])
    ledger.request_drain = lambda *, session_id, now: True
    monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
    monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

    rc = session_ops.stop_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss")

    assert rc == 0
    # 3번째 관측에서 깊이 1 이 보여 streak 이 끊겼다 — 그 뒤로 다시 5회를 채워야 한다
    assert ledger.observed >= 1 + 3 + session_ops.CLEAR_CONFIRMATIONS


def test_scale_updates_every_service_with_force(monkeypatch):
    """`_scale` **본체**를 태운다 — 다른 테스트는 이 함수를 통째로 대체하므로 여기가
    아니면 forceNewDeployment 누락도, 서비스 하나만 갱신하는 회귀도 초록으로 통과한다.

    두 계약을 함께 고정한다: ① 세 서비스 전부를 갱신한다(하나만 뜨면 레인이 반만 돈다),
    ② 스케일업은 force 를 동반한다(desired 0 동안 CD 재배포가 no-op 라, 빼면 직전
    세션의 낡은 다이제스트로 뜬다).
    """
    ecs = FakeEcs()
    monkeypatch.setenv(session_ops.ENV_CLUSTER, "arn:cluster/edge-dev")
    monkeypatch.setenv(session_ops.ENV_SERVICES, "svc-worker,svc-relay,svc-consumer")
    monkeypatch.setattr("data_pipeline.ops.aws.ecs_client", lambda: ecs)

    session_ops._scale(desired=1, force=True)

    assert [c["service"] for c in ecs.calls] == ["svc-worker", "svc-relay", "svc-consumer"]
    assert all(c["desiredCount"] == 1 and c["forceNewDeployment"] is True for c in ecs.calls)
    assert all(c["cluster"] == "arn:cluster/edge-dev" for c in ecs.calls)


def test_queue_lookup_failure_is_not_empty(monkeypatch):
    """조회 실패를 0 으로 읽으면 **안 본 큐**가 '비었음' 으로 인증된다."""
    class Boom:
        def get_queue_attributes(self, **_):
            raise RuntimeError("throttled")

    monkeypatch.setattr("data_pipeline.ops.aws.sqs_client", lambda *a, **k: Boom())

    pending = session_ops._gate_pending(
        FakeLedger(["DRAINED"]), FakeJobs(), session_ids=["msn1"], queues=["https://q/price"])

    assert pending == ["queue depth=unknown https://q/price"]


def test_gate_queues_missing_fails_loud(monkeypatch, wiring):
    """큐 게이트 배선이 빠지면 '볼 큐 없음' 이 아니라 게이트가 통째로 사라진 것이다."""
    monkeypatch.setenv(session_ops.ENV_GATE_QUEUES, "")
    with pytest.raises(SystemExit):
        session_ops._gate_queues()


def test_stop_timeout_leaves_services_up(monkeypatch, wiring):
    """상한을 넘겨도 **내리지 않는다** — 내리면 처리 중이던 window 가 조용히 결손된다."""
    monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, "0.01")
    monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
    monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)
    ledger = FakeLedger(["DRAINING"])
    ledger.request_drain = lambda *, session_id, now: True
    monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
    monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

    rc = session_ops.stop_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss")

    assert rc == 1
    assert wiring.calls == [], "안 끝난 세션을 내리면 결손이 난다"


def test_stop_without_session_touches_nothing(monkeypatch, wiring):
    """오늘 세션이 없으면 **스케일을 건드리지 않는다**.

    전 거래일 stop 이 상한 초과로 서비스를 살려 둔 채 끝났고(exit 1) 다음 날이 휴장일이면,
    여기서 내리는 것은 지목 없이 어제의 복구 작업을 죽이는 것이다. 오늘 세션이 없다는 건
    서비스가 노는 중이라는 뜻이 아니라 무엇을 하는지 모른다는 뜻이다.
    """
    ledger = FakeLedger([None])
    monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
    monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

    rc = session_ops.stop_session_cli(
        FakeSettings(), dataset="price_minute", source_group="toss")

    assert rc == 0
    assert wiring.calls == []


def test_missing_service_wiring_fails_loud(monkeypatch):
    """빈 서비스 목록을 통과시키면 '스케일링 성공(0건)' 이 되어 아침에 아무것도 안 뜬다."""
    monkeypatch.setenv(session_ops.ENV_SERVICES, "")
    with pytest.raises(SystemExit):
        session_ops._services()


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "0", "-5"])
def test_non_finite_timeout_is_rejected(monkeypatch, raw):
    """NaN 은 `<= 0` 도 `경과 >= nan` 도 False 라 상한이 통째로 사라진다 — bounded wait 가 무한이 된다."""
    monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, raw)
    with pytest.raises(SystemExit):
        session_ops._drain_timeout_sec()


class TestNewsLane:
    """뉴스 세션 편입(ALPHA-717) — start 가 두 세션을 계획하고 stop 이 함께 드레인한다."""

    def test_start_plans_news_session_too(self, monkeypatch, wiring):
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        calls = []
        monkeypatch.setattr(session_ops, "plan_session_cli",
                            lambda settings, **k: calls.append(k) or 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert rc == 0
        assert [c["dataset"] for c in calls] == ["price_minute", "news_minute"]
        assert calls[1]["source_group"] == "bigkinds"
        assert calls[1]["universe"] is None, "뉴스 세션은 universe 를 쓰지 않는다"
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-news-worker"]},
        ], "news-worker 는 뉴스 세션이 선 뒤 별도 목록으로 올라간다"

    def test_news_plan_failure_still_scales_price_lane(self, monkeypatch, wiring):
        """뉴스 결손이 하루치 가격 결손으로 번지면 안 된다 — 공용은 올리고 news-worker 는
        올리지 않는다(세션 부재 재기동 루프 방지). exit 는 뉴스 실패를 실어 나른다."""
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        monkeypatch.setattr(session_ops, "plan_session_cli",
                            lambda settings, **k: 0 if k["dataset"] == "price_minute" else 2)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert rc == 2, "뉴스 실패가 exit 에 실려야 스케줄 기록에 남는다"
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
        ], "news-worker 는 세션이 안 선 날 올리지 않는다"

    def test_bad_news_source_group_fails_loud(self, monkeypatch, wiring):
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkindz")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        with pytest.raises(SystemExit):
            session_ops.start_session_cli(
                FakeSettings(), dataset="price_minute", source_group="toss",
                universe="s3://b/u.json")

    def test_stop_gates_on_both_sessions(self, monkeypatch, wiring):
        """가격만 DRAINED 이고 뉴스가 ACTIVE 면 내리면 안 된다 — 뉴스 in-flight 가 결손된다."""
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, "0.01")
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)

        from datetime import datetime as _dt
        from data_pipeline.db import stable_domain_id
        from data_pipeline.minute.models import KST as _KST
        day = _dt.now(_KST).date().isoformat()
        price_id = stable_domain_id("msn", "price_minute", "toss", day)
        news_id = stable_domain_id("msn", "news_minute", "bigkinds", day)

        class TwoLaneLedger:
            def __init__(self, phases):
                self.phases = phases
                self.drained = []

            def session_snapshot(self, *, session_id):
                return {"phase": self.phases[session_id]}

            def request_drain(self, *, session_id, now):
                self.drained.append(session_id)
                return True

        ledger = TwoLaneLedger({price_id: "DRAINED", news_id: "ACTIVE"})
        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 1, "뉴스 세션이 안 끝났는데 내리면 결손이다"
        assert sorted(ledger.drained) == sorted([price_id, news_id]), "두 세션 모두 drain 요청"
        assert wiring.calls == []

    def test_stop_skips_missing_news_session(self, monkeypatch, wiring):
        """뉴스 계획이 실패한 날 — 뉴스 부재가 가격 레인 종료를 막으면 안 된다."""
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)

        from datetime import datetime as _dt
        from data_pipeline.db import stable_domain_id
        from data_pipeline.minute.models import KST as _KST
        day = _dt.now(_KST).date().isoformat()
        price_id = stable_domain_id("msn", "price_minute", "toss", day)

        class OneLaneLedger:
            def session_snapshot(self, *, session_id):
                return {"phase": "DRAINED"} if session_id == price_id else None

            def request_drain(self, *, session_id, now):
                return True

        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: OneLaneLedger())
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 0
        # 뉴스 워커는 별도 목록으로 함께 내려간다 — 세션이 안 선 날도 안전(desired 이미 0)
        assert wiring.calls == [
            {"desiredCount": 0, "forceNewDeployment": False, "services": None},
            {"desiredCount": 0, "forceNewDeployment": False, "services": ["svc-news-worker"]},
        ]

    def test_late_created_session_joins_gate(self, monkeypatch, wiring):
        """stop 도중 생긴 세션은 다음 폴링부터 게이트에 들어와야 한다 — 최초 live 만
        고정하는 회귀면 실행 중인 뉴스 세션을 두고 서비스를 내린다."""
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, "0.01")
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)

        from datetime import datetime as _dt
        from data_pipeline.db import stable_domain_id
        from data_pipeline.minute.models import KST as _KST
        day = _dt.now(_KST).date().isoformat()
        price_id = stable_domain_id("msn", "price_minute", "toss", day)
        news_id = stable_domain_id("msn", "news_minute", "bigkinds", day)

        class LateNewsLedger:
            """진입 조회 시엔 뉴스 세션이 없고(계획 전), 게이트 폴링부터 ACTIVE 로 나타난다."""

            def __init__(self):
                self.news_queries = 0

            def session_snapshot(self, *, session_id):
                if session_id == price_id:
                    return {"phase": "DRAINED"}
                self.news_queries += 1
                return None if self.news_queries <= 1 else {"phase": "ACTIVE"}

            def request_drain(self, *, session_id, now):
                return True

        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: LateNewsLedger())
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 1, "늦게 나타난 ACTIVE 세션이 게이트에 안 들어오면 내려서 결손이 난다"
        assert wiring.calls == []

    def test_drain_failure_on_one_lane_still_tries_the_other(self, monkeypatch, wiring):
        """첫 레인 drain 예외에서 끊으면 뒤 레인이 ACTIVE 로 고립된다 — 전 레인 시도 후
        실패 판정이어야 한다(멱등이라 재실행 안전)."""
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")

        from datetime import datetime as _dt
        from data_pipeline.db import stable_domain_id
        from data_pipeline.minute.models import KST as _KST
        day = _dt.now(_KST).date().isoformat()
        price_id = stable_domain_id("msn", "price_minute", "toss", day)
        news_id = stable_domain_id("msn", "news_minute", "bigkinds", day)

        class FlakyDrainLedger:
            def __init__(self):
                self.attempted = []

            def session_snapshot(self, *, session_id):
                return {"phase": "ACTIVE"}

            def request_drain(self, *, session_id, now):
                self.attempted.append(session_id)
                if session_id == price_id:
                    raise RuntimeError("transient db")
                return True

        ledger = FlakyDrainLedger()
        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 2
        assert sorted(ledger.attempted) == sorted([price_id, news_id]), \
            "첫 실패에서 끊으면 뒤 레인이 drain 을 영영 못 받는다"
        assert wiring.calls == [], "부분 드레인 상태에서 내리면 안 된다"


class TestInavLane:
    """iNAV 세션 편입(ALPHA-882) — 뉴스와 같은 승객 축이다.

    뉴스와 갈리는 지점만 여기서 고정한다. 공통 축(계획 순서·워커 분리·게이트 참여)은
    `TestNewsLane` 이 이미 잡고 있고, 표(`PASSENGER_LANES`)로 접혀 있어 한쪽만 도는
    경로가 없다 — **승객이 둘일 때 생기는 것**이 이 클래스의 관심사다.
    """

    def test_start_plans_inav_session_too(self, monkeypatch, wiring):
        monkeypatch.setenv(session_ops.ENV_INAV_SOURCE_GROUP, "kis")
        monkeypatch.setenv(session_ops.ENV_INAV_WORKER_SERVICES, "svc-inav-worker")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        calls = []
        monkeypatch.setattr(session_ops, "plan_session_cli",
                            lambda settings, **k: calls.append(k) or 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

        assert rc == 0
        assert [c["dataset"] for c in calls] == ["price_minute", "etf_inav_minute"]
        assert calls[1]["source_group"] == "kis"
        # ⚠️ universe 는 **planner 에만** None 이다 — worker 는 `--universe` 를 따로 받는다
        # (terraform command). 여기서 넘기면 iNAV 격자가 가격 유니버스로 계획된다.
        assert calls[1]["universe"] is None
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-inav-worker"]},
        ], "inav-worker 는 공용 목록이 아니라 자기 목록으로 올라간다"

    def test_bad_inav_source_group_fails_loud(self, monkeypatch, wiring):
        """iNAV 는 KIS 단독이다 — 토스는 분봉 API 에 NAV 축이 없다."""
        monkeypatch.setenv(session_ops.ENV_INAV_SOURCE_GROUP, "toss")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        with pytest.raises(SystemExit, match="etf_inav_minute 어휘 밖이다"):
            session_ops.start_session_cli(
                FakeSettings(), dataset="price_minute", source_group="kis",
                universe="s3://b/u.json")

    def test_toggle_without_service_list_fails_before_planning(self, monkeypatch, wiring):
        """계획만 서고 올릴 서비스가 없으면 그 레인은 하루 종일 PLANNED 로 남는다 —
        그리고 그 사실이 드러나는 자리가 없다. 계획 **전에** 죽어야 한다."""
        monkeypatch.setenv(session_ops.ENV_INAV_SOURCE_GROUP, "kis")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        planned = []
        monkeypatch.setattr(session_ops, "plan_session_cli",
                            lambda settings, **k: planned.append(k) or 0)

        with pytest.raises(SystemExit, match="MINUTE_SESSION_INAV_WORKER_SERVICES 가 비었다"):
            session_ops.start_session_cli(
                FakeSettings(), dataset="price_minute", source_group="kis",
                universe="s3://b/u.json")
        assert planned == [], "배선 결손은 세션을 만들기 전에 죽어야 한다"


class TestTwoPassengers:
    """승객이 둘이 된 뒤에만 나타나는 축 (ALPHA-882).

    뉴스 하나였을 때는 `return news_exit` 로 충분했다. 둘이 되면 exit 하나에 실패
    둘을 실어야 하고, 레인끼리 서로의 스케일업을 막으면 안 된다.
    """

    @pytest.fixture
    def both(self, monkeypatch, wiring):
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setenv(session_ops.ENV_INAV_SOURCE_GROUP, "kis")
        monkeypatch.setenv(session_ops.ENV_INAV_WORKER_SERVICES, "svc-inav-worker")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        return wiring

    def test_both_lanes_plan_and_scale(self, monkeypatch, both):
        calls = []
        monkeypatch.setattr(session_ops, "plan_session_cli",
                            lambda settings, **k: calls.append(k) or 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

        assert rc == 0
        assert [c["dataset"] for c in calls] == [
            "price_minute", "news_minute", "etf_inav_minute"]
        assert both.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-news-worker"]},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-inav-worker"]},
        ]

    def test_first_failure_survives_a_later_success(self, monkeypatch, both):
        """⭐ 이 PR 이 막는 회귀다. 승객마다 exit 를 **덮어쓰면**(`rc = plan(...)` 을
        루프 안에서) 뒤에 성공한 iNAV 가 앞의 뉴스 실패를 0 으로 지운다 — 스케줄
        기록만 보는 사람에겐 그 날 뉴스 레인이 정상으로 보인다(Rule 12).

        순서에 기대지 않도록 반대 방향도 아래 테스트가 함께 고정한다.
        """
        monkeypatch.setattr(
            session_ops, "plan_session_cli",
            lambda settings, **k: 2 if k["dataset"] == "news_minute" else 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

        assert rc == 2, "뒤 레인의 성공이 앞 레인의 실패를 덮으면 안 된다"

    def test_later_failure_is_not_swallowed_by_an_earlier_success(self, monkeypatch, both):
        """반대 방향 — 앞이 성공하고 뒤가 실패해도 exit 에 실려야 한다. 위 테스트만
        있으면 `return passenger_exits[첫_레인]` 같은 구현이 통과한다."""
        monkeypatch.setattr(
            session_ops, "plan_session_cli",
            lambda settings, **k: 2 if k["dataset"] == "etf_inav_minute" else 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

        assert rc == 2

    def test_one_lane_failure_does_not_block_the_other_worker(self, monkeypatch, both):
        """레인은 서로 독립이다 — 뉴스 계획이 실패해도 iNAV 워커는 올라가야 한다.
        승객 전체를 한 플래그로 묶으면 한 레인의 실패가 나머지를 통째로 세운다."""
        monkeypatch.setattr(
            session_ops, "plan_session_cli",
            lambda settings, **k: 2 if k["dataset"] == "news_minute" else 0)

        session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

        assert both.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-inav-worker"]},
        ], "뉴스 워커는 빠지고 iNAV 워커는 올라가야 한다"

    def test_stop_gates_on_all_three_sessions(self, monkeypatch, both):
        """iNAV 가 ACTIVE 인데 내리면 그 window 가 결손된다 — iNAV 는 소급이 불가라
        영구 결손이다(가격·뉴스와 달리 되받을 길이 없다)."""
        monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, "0.01")
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)

        from datetime import datetime as _dt
        from data_pipeline.db import stable_domain_id
        from data_pipeline.minute.models import KST as _KST
        day = _dt.now(_KST).date().isoformat()
        ids = {
            stable_domain_id("msn", "price_minute", "kis", day): "DRAINED",
            stable_domain_id("msn", "news_minute", "bigkinds", day): "DRAINED",
            stable_domain_id("msn", "etf_inav_minute", "kis", day): "ACTIVE",
        }

        class ThreeLaneLedger:
            def __init__(self):
                self.drained = []

            def session_snapshot(self, *, session_id):
                return {"phase": ids[session_id]}

            def request_drain(self, *, session_id, now):
                self.drained.append(session_id)
                return True

        ledger = ThreeLaneLedger()
        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis")

        assert rc == 1, "iNAV 가 안 끝났는데 내리면 영구 결손이다"
        assert sorted(ledger.drained) == sorted(ids), "세 세션 모두 drain 요청"
        assert both.calls == []

    def test_stop_scales_down_listed_workers_even_when_toggle_is_off(
            self, monkeypatch, wiring):
        """편입 롤백(토글만 끄고 목록은 남긴 채 배포)에서 그 워커가 desired 1 로 떠
        있는 채 아무도 안 내리면 밤새 돈다. 내리는 방향은 과하게 잡아도 안전하다."""
        monkeypatch.setenv(session_ops.ENV_INAV_WORKER_SERVICES, "svc-inav-worker")
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)

        class OneLaneLedger:
            def session_snapshot(self, *, session_id):
                return {"phase": "DRAINED"}

            def request_drain(self, *, session_id, now):
                return True

        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: OneLaneLedger())
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis")

        assert rc == 0
        assert wiring.calls == [
            {"desiredCount": 0, "forceNewDeployment": False, "services": None},
            {"desiredCount": 0, "forceNewDeployment": False, "services": ["svc-inav-worker"]},
        ], "토글이 꺼져 있어도 목록에 있으면 내린다"


def _module_tf() -> str | None:
    """`minute_services.tf` 본문 — 저장소 체크아웃에서만 있다(패키지 설치 환경엔 없다)."""
    from pathlib import Path as _P
    here = _P(__file__).resolve()
    rel = "infra/terraform/modules/data-pipeline/minute_services.tf"
    return next((p / rel).read_text() for p in here.parents if (p / rel).exists())


def test_승객_토글_env_이름이_terraform_과_일치한다():
    """⚠️ **이 드리프트는 완전히 조용하다.** terraform 이 심는 env 이름과 코드가 읽는
    이름이 갈리면 `_passenger_source_group` 이 None 을 돌려주고, 그 레인은 계획도
    스케일업도 없이 **exit 0** 으로 지나간다 — 실패가 아니라 부재라 알람도 안 뜬다.
    그 날 iNAV 는 통째로 안 도는데 스케줄 기록은 초록이다(Rule 12).

    이름은 terraform 이 정본이고 코드가 따라간다(`_services` 주석과 같은 결) — 그래서
    대조 방향은 "코드 상수가 tf 에 실제로 있는가"다.
    """
    try:
        text = _module_tf()
    except StopIteration:
        pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    for dataset, (env_group, env_services) in session_ops.PASSENGER_LANES.items():
        assert f"{env_group} =" in text, f"{dataset} 토글 env 가 terraform 에 없다: {env_group}"
        assert f"{env_services} =" in text, \
            f"{dataset} 워커 목록 env 가 terraform 에 없다: {env_services}"


def test_승객_생산자는_공용_스케일_목록에서_빠진다():
    """공용 목록에 남으면 **그 세션이 안 선 날에도** 올라가 기동 거부 재기동 루프를
    돈다(비용·알람 소음). 승객 워커는 자기 목록으로만 올라간다.

    terraform 의 제외 목록(`minute_passenger_workers`)과 코드의 `PASSENGER_LANES` 는
    짝이다 — 서비스만 추가하고 제외를 빠뜨리는 것이 이 배선의 기본 실수다.
    """
    try:
        text = _module_tf()
    except StopIteration:
        pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    import re
    block = re.search(r"minute_passenger_workers\s*=\s*\[([^\]]*)\]", text)
    assert block, "공용 목록 제외가 terraform 에서 사라졌다 — 승객 워커가 매일 올라간다"
    excluded = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert excluded == {"news-worker", "inav-worker"}, \
        f"승객 생산자와 제외 목록이 갈렸다: {excluded}"
    # 제외된 워커는 **자기 목록**에 반드시 있어야 한다 — 빼고 안 넣으면 아무도 안 올린다
    for worker in excluded:
        assert f'aws_ecs_service.minute["{worker}"].name' in text, \
            f"{worker} 가 공용에서 빠졌는데 자기 목록 env 에도 없다 — 그 레인이 조용히 안 돈다"


def test_iNAV_토글_기본값이_어휘_안이다():
    """어휘 밖 기본값이면 apply 는 통과하고 **다음 아침 오케스트레이터가 죽는다** —
    가격 레인까지 그날 통째로 안 뜬다. plan 보다 여기서 막는 게 싸다."""
    from pathlib import Path as _P
    from data_pipeline.minute.states import (
        DATASET_ETF_INAV_MINUTE, SOURCE_GROUPS_BY_DATASET)
    import re

    here = _P(__file__).resolve()
    rel = "infra/terraform/modules/data-pipeline/variables.tf"
    root = next((p for p in here.parents if (p / rel).exists()), None)
    if root is None:
        pytest.skip("variables.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    block = re.search(
        r'variable\s+"minute_session_inav_source_group"\s*\{[^}]*default\s*=\s*"([^"]+)"',
        (root / rel).read_text())
    assert block, "minute_session_inav_source_group 기본값을 못 찾았다"
    assert block.group(1) in SOURCE_GROUPS_BY_DATASET[DATASET_ETF_INAV_MINUTE]
