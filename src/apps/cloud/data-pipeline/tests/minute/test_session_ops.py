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
    # 기본은 단일(가격) 레인 — 뉴스 편입 케이스는 개별 테스트가 명시로 켠다
    monkeypatch.delenv(session_ops.ENV_NEWS_SOURCE_GROUP, raising=False)
    monkeypatch.delenv(session_ops.ENV_NEWS_WORKER_SERVICES, raising=False)
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
