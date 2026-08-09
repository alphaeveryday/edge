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
    # 설명 소비자 목록(ALPHA-910) — 기본은 **컷오버가 끝난** 배선이다. 빈 값(구 task-def)
    # 경로는 TestAnalysisConsumerOwnList 가 명시로 지운다.
    monkeypatch.setenv(session_ops.ENV_ANALYSIS_SERVICES, "svc-analysis-consumer")
    monkeypatch.delenv(session_ops.ENV_DRAIN_TIMEOUT, raising=False)
    # 기본은 단일(가격) 레인 — 선택 레인 편입 케이스는 개별 테스트가 명시로 켠다.
    # ⚠️ 표(_OPTIONAL_LANES)에서 돌려 지운다 — 레인을 늘리며 여기 한 줄을 빠뜨리면 호스트
    # env 가 새어 테스트 결과가 개발자 환경에 따라 갈린다(그 상태가 가장 늦게 발견된다).
    for _lane in session_ops._OPTIONAL_LANES:
        monkeypatch.delenv(_lane.source_env, raising=False)
        monkeypatch.delenv(_lane.services_env, raising=False)
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
    assert wiring.calls == [
        {"desiredCount": 1, "forceNewDeployment": True, "services": None},
        {"desiredCount": 1, "forceNewDeployment": True,
         "services": ["svc-analysis-consumer"]},
    ]


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
    assert wiring.calls == [
        {"desiredCount": 0, "forceNewDeployment": False, "services": None},
        {"desiredCount": 0, "forceNewDeployment": False,
         "services": ["svc-analysis-consumer"]},
    ]
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


class TestAnalysisConsumerOwnList:
    """설명 소비자를 공용 목록에서 뗀 축(ALPHA-910) — 소유는 분리되고 **수명은 그대로**다.

    이 분리의 목적은 desired 를 오토스케일링에 넘기는 것이고, 그 전제가 "세션이 이 값을
    덮어쓰지 않는다"이다. 그래서 여기서 보는 것은 두 가지다: 자기 목록으로 실제로 오르내리는가
    (안 그러면 장중 설명이 하루 통째로 안 난다), 그리고 공용 목록과 **섞이지 않는가**.
    """

    def test_공용_목록이_아니라_자기_목록으로_오른다(self, monkeypatch, wiring):
        """공용 목록에 얹어 올리면 스케일 단위가 다시 하나로 붙어 분리가 무의미해진다 —
        후속 스케일러가 이 서비스만 따로 움직일 수 없다."""
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        monkeypatch.setattr(session_ops, "plan_session_cli", lambda *a, **k: 0)

        session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert {"desiredCount": 1, "forceNewDeployment": True,
                "services": ["svc-analysis-consumer"]} in wiring.calls
        common = [c for c in wiring.calls if c["services"] is None]
        assert common == [{"desiredCount": 1, "forceNewDeployment": True, "services": None}], \
            "공용 목록 호출은 그대로 한 번뿐이어야 한다 — 소비자가 거기 섞이면 분리가 없던 일이 된다"

    def test_계획이_실패하면_소비자도_안_오른다(self, monkeypatch, wiring):
        """자기 목록이 됐다고 가격 레인과 다른 조건으로 뜨면 안 된다 — 세션이 없는 날
        떠 있으면 다음 아침 force-new-deployment 밖이라 낡은 이미지로 남는다."""
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        monkeypatch.setattr(session_ops, "plan_session_cli", lambda *a, **k: 1)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert rc == 1
        assert wiring.calls == [], "가격 계획이 실패한 날은 소비자도 안 올린다"

    def test_게이트가_빈_뒤_자기_목록으로_내려간다(self, monkeypatch, wiring):
        """분리 전과 같은 자리에서 같이 내려간다 — 안 내려가면 야간 비용이 그대로 남고,
        게이트 전에 내려가면 처리 중이던 설명이 결손된다."""
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)
        ledger = FakeLedger(["DRAINED"])
        ledger.request_drain = lambda *, session_id, now: True
        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 0
        assert {"desiredCount": 0, "forceNewDeployment": False,
                "services": ["svc-analysis-consumer"]} in wiring.calls

    def test_게이트가_안_비면_소비자도_안_내려간다(self, monkeypatch, wiring):
        """상한 초과에서 공용만 살려 두고 소비자를 내리면, 큐 게이트가 못 본 설명 처리분이
        조용히 끊긴다(설명 큐는 게이트 밖이라 이 서비스의 in-flight 를 아무도 안 센다)."""
        monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, "0.01")
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [("q", 3)])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)
        ledger = FakeLedger(["DRAINED"])
        ledger.request_drain = lambda *, session_id, now: True
        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 1
        assert wiring.calls == []

    def test_공용_목록에_실려_와도_코드가_빼낸다(self, monkeypatch, wiring):
        """소유 축을 **코드가** 정한다 — terraform 이 컷오버 안전망으로 공용에도 싣고 있으니,
        여기서 안 빼면 같은 서비스에 desired 를 두 번 쓴다(force 라 배포도 두 번). 그 상태로
        오토스케일링을 붙이면 공용 경로가 스케일러의 desired 를 계속 되돌린다."""
        monkeypatch.setenv(session_ops.ENV_SERVICES,
                           "svc-worker,svc-analysis-consumer,svc-relay")

        assert session_ops._services() == ["svc-worker", "svc-relay"]

    def test_빼기가_공용을_비우면_죽는다(self, monkeypatch, wiring):
        """빈 값 검사는 **빼기 전**이라 이 경로를 못 막는다 — 통과시키면 가격 워커·relay 를
        하나도 안 올린 채 소비자만 올리고 exit 0 이 되어, `_services` 의 원래 가드가
        막으려던 "스케일링 성공(0건)" 이 다른 원인으로 그대로 재현된다."""
        monkeypatch.setenv(session_ops.ENV_SERVICES, "svc-analysis-consumer")

        with pytest.raises(SystemExit, match="빼고 나면 빈다"):
            session_ops._services()

    def test_컷오버_중_빈_목록은_죽지_않고_공용에_맡긴다(self, monkeypatch, wiring):
        """이 이미지가 terraform apply 보다 **먼저** 착지한 날의 상태다(두 워크플로는 독립).
        여기서 죽으면 거래일 판정 전이라 그날 1분 파이프라인이 통째로 안 뜬다 — 구 task-def
        의 공용 목록이 아직 소비자를 싣고 있으므로 스케일은 그대로 된다."""
        monkeypatch.delenv(session_ops.ENV_ANALYSIS_SERVICES, raising=False)
        monkeypatch.setenv(session_ops.ENV_SERVICES,
                           "svc-worker,svc-analysis-consumer,svc-relay")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        monkeypatch.setattr(session_ops, "plan_session_cli", lambda *a, **k: 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert rc == 0
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
        ], "구 task-def 에선 자기 목록 스케일이 없다 — 공용 한 번이 소비자까지 덮는다"
        # 그 공용 한 번이 실제로 소비자를 포함해야 한다(빼기가 무조건 도는 회귀 방지).
        assert "svc-analysis-consumer" in session_ops._services()

    def test_terraform_이_자기_목록_env_를_준다(self):
        """코드가 공용에서 빼는 근거가 이 env 다 — terraform 이 안 실으면 코드는 컷오버
        상태로 오인해 소유 축이 영영 안 갈린다(그리고 그 사실이 드러나는 자리가 없다).
        이름은 terraform 이 정본이고 코드가 따라간다(`_services` 주석과 같은 결)."""
        try:
            text = _module_tf()
        except StopIteration:
            pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

        import re
        # ⚠️ 이름 존재와 값 존재를 **따로** 보면 안 된다 — `aws_ecs_service.analysis_consumer.name`
        # 은 공용 목록·IAM 에도 있어서, env 가 `""` 로 바뀌어도 두 단언이 각자 통과한다.
        # 대입식 하나로 묶어야 배선 회귀를 실제로 거부한다.
        assert re.search(
            rf"{session_ops.ENV_ANALYSIS_SERVICES}\s*=\s*aws_ecs_service\.analysis_consumer\.name",
            _tf_code(text),
        ), ("설명 소비자 목록 env 가 terraform 에서 그 서비스명을 안 싣는다 — "
            "코드가 공용에서 뺄 근거를 잃고 컷오버 상태로 오인한다")

    def test_terraform_공용_목록은_컷오버_안전망으로_남아_있다(self):
        """⚠️ 여기서 빼면 apply 가 이미지 CD 보다 **늦게** 착지한 날 구 이미지가 소비자를
        아무 목록으로도 안 올린다 — 그날 장중 설명이 통째로 없다. 실제 제거는 새 이미지가
        오래 떠 있는 오토스케일링 부착 PR 소관이다. 코드가 이미 빼내므로 남겨도 무해하다."""
        try:
            text = _module_tf()
        except StopIteration:
            pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

        import re
        block = re.search(r"MINUTE_SESSION_SERVICES\s*=\s*join\(.*?\n\s*\)\)", _tf_code(text), re.S)
        assert block, "공용 목록 파생을 못 찾았다 — 이 계약 검사가 헛돌고 있다"
        assert "aws_ecs_service.analysis_consumer.name" in block.group(0), \
            "공용 목록에서 소비자를 뺐다 — apply 가 늦은 날 구 이미지가 소비자를 안 올린다"


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "0", "-5"])
def test_non_finite_timeout_is_rejected(monkeypatch, raw):
    """NaN 은 `<= 0` 도 `경과 >= nan` 도 False 라 상한이 통째로 사라진다 — bounded wait 가 무한이 된다."""
    monkeypatch.setenv(session_ops.ENV_DRAIN_TIMEOUT, raw)
    with pytest.raises(SystemExit):
        session_ops._drain_timeout_sec()


class TestDisclosureLane:
    """공시 세션 편입(ALPHA-875) — 뉴스와 **같은 축**이다. 두 레인이 표 하나를 도므로 여기서
    보는 것은 "공시가 그 표에 제대로 들어갔나"이고, 축 자체의 반례는 TestNewsLane 이 든다."""

    def test_두_선택_레인이_함께_계획되고_각자_목록으로_올라간다(self, monkeypatch, wiring):
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setenv(session_ops.ENV_DISCLOSURE_SOURCE_GROUP, "dart")
        monkeypatch.setenv(session_ops.ENV_DISCLOSURE_WORKER_SERVICES, "svc-disclosure-worker")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        calls = []
        monkeypatch.setattr(session_ops, "plan_session_cli",
                            lambda settings, **k: calls.append(k) or 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert rc == 0
        assert [c["dataset"] for c in calls] == [
            "price_minute", "news_minute", "disclosure_minute"]
        # 공시도 유니버스를 쓰지 않는다(소스 단위 — 유니버스는 기대 집합이 아니라 필터다)
        assert calls[2]["source_group"] == "dart" and calls[2]["universe"] is None
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-news-worker"]},
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-disclosure-worker"]},
        ], "각 생산자는 자기 세션이 선 뒤 자기 목록으로만 올라간다"

    def test_공시_계획_실패는_공시_워커만_막는다(self, monkeypatch, wiring):
        """레인이 둘이 되면서 생긴 축 — 한 레인의 실패가 **다른 선택 레인**까지 막으면
        안 된다(뉴스 실패가 가격을 안 막는 것과 같은 이유, 한 겹 더)."""
        monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")
        monkeypatch.setenv(session_ops.ENV_NEWS_WORKER_SERVICES, "svc-news-worker")
        monkeypatch.setenv(session_ops.ENV_DISCLOSURE_SOURCE_GROUP, "dart")
        monkeypatch.setenv(session_ops.ENV_DISCLOSURE_WORKER_SERVICES, "svc-disclosure-worker")
        monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
        monkeypatch.setattr(
            session_ops, "plan_session_cli",
            lambda settings, **k: 2 if k["dataset"] == "disclosure_minute" else 0)

        rc = session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss",
            universe="s3://b/u.json")

        assert rc == 2, "공시 실패가 exit 에 실려야 스케줄 기록에 남는다"
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
            {"desiredCount": 1, "forceNewDeployment": True, "services": ["svc-news-worker"]},
        ], "뉴스는 올라가고 공시만 안 올라간다"

    def test_토글이_꺼져도_떠_있는_서비스는_내린다(self, monkeypatch, wiring):
        """어제 켜고 오늘 끈 경우 — 토글로 스케일다운을 가두면 그 서비스를 아무도 안 내려
        계속 돈다(세션 없이 도는 Worker 는 기동 거부 루프다)."""
        monkeypatch.setenv(session_ops.ENV_DISCLOSURE_WORKER_SERVICES, "svc-disclosure-worker")
        monkeypatch.delenv(session_ops.ENV_DISCLOSURE_SOURCE_GROUP, raising=False)
        monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
        monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)
        ledger = FakeLedger(["DRAINED"])
        ledger.request_drain = lambda *, session_id, now: True
        monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: ledger)
        monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

        rc = session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="toss")

        assert rc == 0
        assert {"desiredCount": 0, "forceNewDeployment": False,
                "services": ["svc-disclosure-worker"]} in wiring.calls


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
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
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
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
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
            {"desiredCount": 0, "forceNewDeployment": False,
             "services": ["svc-analysis-consumer"]},
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
    """iNAV 세션 편입(ALPHA-882) — 뉴스·공시와 같은 선택 레인 축이다.

    뉴스와 갈리는 지점만 여기서 고정한다. 공통 축(계획 순서·워커 분리·게이트 참여)은
    `TestNewsLane` 이 이미 잡고 있고, 표(`_OPTIONAL_LANES`)로 접혀 있어 한쪽만 도는
    경로가 없다 — **선택 레인이 여럿일 때 생기는 것**이 이 클래스의 관심사다.
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
        # ⚠️ iNAV 는 universe 를 **받아야 한다** — 뉴스와 갈리는 지점이다. 안 넘기면
        # planner 가 거부해 exit 2 고(그 레인이 매일 안 선다), 통과시켜도 원장에
        # universe_version="none" 이 박혀 Worker 가 영영 처리를 시작 안 한다.
        assert calls[1]["universe"] == "s3://b/u.json"
        assert wiring.calls == [
            {"desiredCount": 1, "forceNewDeployment": True, "services": None},
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
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
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
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
            {"desiredCount": 1, "forceNewDeployment": True,
             "services": ["svc-analysis-consumer"]},
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
            {"desiredCount": 0, "forceNewDeployment": False,
             "services": ["svc-analysis-consumer"]},
            {"desiredCount": 0, "forceNewDeployment": False, "services": ["svc-inav-worker"]},
        ], "토글이 꺼져 있어도 목록에 있으면 내린다"


def _tf_code(text: str) -> str:
    """주석을 걷어낸 terraform 본문. 주석 처리된 대입식을 배선으로 인정하면 계약 검사가
    **회귀를 거부하지 못한다** — 실제 값을 `""` 로 바꾸고 옛 줄을 주석으로 남기는 것이
    가장 흔한 모양이다(Rule 9: 단언이 계약보다 약하면 안 된다)."""
    import re
    return re.sub(r"(?m)^[ \t]*(#|//).*$", "", text)


def _module_tf() -> str | None:
    """`minute_services.tf` 본문 — 저장소 체크아웃에서만 있다(패키지 설치 환경엔 없다)."""
    from pathlib import Path as _P
    here = _P(__file__).resolve()
    rel = "infra/terraform/modules/data-pipeline/minute_services.tf"
    return next((p / rel).read_text() for p in here.parents if (p / rel).exists())


def test_선택레인_토글_env_이름이_terraform_과_일치한다():
    """⚠️ **이 드리프트는 완전히 조용하다.** terraform 이 심는 env 이름과 코드가 읽는
    이름이 갈리면 `_lane_source_group` 이 None 을 돌려주고, 그 레인은 계획도
    스케일업도 없이 **exit 0** 으로 지나간다 — 실패가 아니라 부재라 알람도 안 뜬다.
    그 날 그 레인은 통째로 안 도는데 스케줄 기록은 초록이다(Rule 12).

    이름은 terraform 이 정본이고 코드가 따라간다(`_services` 주석과 같은 결) — 그래서
    대조 방향은 "코드 상수가 tf 에 실제로 있는가"다.
    """
    try:
        text = _module_tf()
    except StopIteration:
        pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    import re
    # ⚠️ `NAME =` 를 그대로 찾지 않는다 — `terraform fmt` 가 블록 안에서 `=` 를 정렬해
    # 이름 뒤 공백 수가 **형제 키 이름 길이에 따라 바뀐다**. 무관한 env 하나가 늘어나면
    # 이 검사가 배선과 상관없이 빨개진다(그때 고쳐야 할 건 배선이 아니라 이 정규식이었다).
    for lane in session_ops._OPTIONAL_LANES:
        assert re.search(rf"{lane.source_env}\s*=", text), \
            f"{lane.dataset} 토글 env 가 terraform 에 없다: {lane.source_env}"
        assert re.search(rf"{lane.services_env}\s*=", text), \
            f"{lane.dataset} 워커 목록 env 가 terraform 에 없다: {lane.services_env}"


def test_세션결속_생산자는_공용_스케일_목록에서_빠진다():
    """공용 목록에 남으면 **그 세션이 안 선 날에도** 올라가 기동 거부 재기동 루프를
    돈다(비용·알람 소음). 선택 레인 Worker 는 자기 목록으로만 올라간다.

    terraform 의 제외 목록(`session_bound_workers`)과 코드의 `_OPTIONAL_LANES` 는
    짝이다 — 서비스만 추가하고 제외를 빠뜨리는 것이 이 배선의 기본 실수다.
    """
    try:
        text = _module_tf()
    except StopIteration:
        pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    import re
    block = re.search(r"session_bound_workers\s*=\s*\[([^\]]*)\]", text)
    assert block, "공용 목록 제외가 terraform 에서 사라졌다 — 선택 레인 Worker 가 매일 올라간다"
    excluded = set(re.findall(r'"([^"]+)"', block.group(1)))
    assert len(excluded) == len(session_ops._OPTIONAL_LANES), \
        f"선택 레인 수와 제외 목록이 갈렸다: {excluded} vs {[l.dataset for l in session_ops._OPTIONAL_LANES]}"
    # 제외된 워커는 **자기 목록**에 반드시 있어야 한다 — 빼고 안 넣으면 아무도 안 올린다
    for worker in excluded:
        assert f'aws_ecs_service.minute["{worker}"].name' in text, \
            f"{worker} 가 공용에서 빠졌는데 자기 목록 env 에도 없다 — 그 레인이 조용히 안 돈다"


def test_inav_worker_가_휴장일_집합을_받는다():
    """`skip_reason` 을 실제로 여는 컨테이너가 `OPS_KR_HOLIDAYS` 를 못 받으면
    `is_trading_day` 가 **주말만 아는 상태로 조용히 퇴화**한다 — 가드가 있는데 평일
    공휴일에 안 걸린다(tasks.tf `env_sets.kis` 주석과 같은 축).

    오케스트레이터가 그날 안 띄우니 괜찮다는 논증은 틀렸다 — 수동 확인이나 EOD stop
    타임아웃 뒤 잔존 `desired_count=1` 로 이 서비스만 살아 있을 수 있고, 그때 KIS 는
    직전 거래일 값을 줘서 오늘 파티션에 유령 as-of 가 앉는다(ALPHA-387 과 동형).
    """
    try:
        text = _module_tf()
    except StopIteration:
        pytest.skip("minute_services.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")

    import re
    # 블록 단위로 본다 — 파일 어딘가(오케스트레이터 task-def)에 있는 것으로는 이 서비스가
    # 받는다는 증거가 안 된다. 실제로 그 구멍이 이렇게 났다(#642 봇 P2).
    block = re.search(r"\n    inav-worker = \{.*?\n    \}\n", text, re.S)
    assert block, "inav-worker 서비스 블록을 못 찾았다 — 이 계약 검사가 헛돌고 있다"
    assert "OPS_KR_HOLIDAYS" in block.group(0), \
        "inav-worker 가 휴장일 집합을 못 받는다 — 평일 공휴일 가드가 조용히 퇴화한다"


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


def test_승객의_universe_인자가_UNIVERSE_DATASETS_축을_따른다(monkeypatch, wiring):
    """⭐ 승객을 표로 접으면서 **레인마다 갈리는 축**을 하나로 뭉갠 자리다.

    뉴스는 소스 단위라 universe 를 안 쓰고(주면 planner 가 거부), iNAV 는
    `UNIVERSE_DATASETS` 라 없으면 거부한다. 즉 상수 None 도 상수 universe 도 둘 다
    틀린다 — 판정이 dataset 별이어야 한다.

    `dataset in UNIVERSE_DATASETS` 를 그대로 기대값으로 쓴다. 레인이 늘 때 이 테스트가
    자동으로 그 레인을 덮고, 축이 뒤집히면(예: 조건을 `not in` 으로) 전건 빨개진다.
    """
    from data_pipeline.minute.states import SOURCE_GROUPS_BY_DATASET, UNIVERSE_DATASETS

    # 레인을 이름으로 켜지 않는다 — 표를 돌며 그 dataset 의 어휘에서 하나를 집는다.
    # 레인이 늘면 이 테스트가 **자동으로** 그 레인까지 덮는다(축을 뭉갠 자리를 지키는 게
    # 이 테스트의 목적인데, 켜는 쪽을 손으로 나열하면 새 레인이 조용히 빠진다).
    for lane in session_ops._OPTIONAL_LANES:
        monkeypatch.setenv(lane.services_env, "svc-x")
        monkeypatch.setenv(lane.source_env, sorted(SOURCE_GROUPS_BY_DATASET[lane.dataset])[0])
    monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)
    calls = []
    monkeypatch.setattr(session_ops, "plan_session_cli",
                        lambda settings, **k: calls.append(k) or 0)

    session_ops.start_session_cli(
        FakeSettings(), dataset="price_minute", source_group="kis",
        universe="s3://b/u.json")

    seen = {c["dataset"]: c["universe"] for c in calls}
    lane_datasets = [lane.dataset for lane in session_ops._OPTIONAL_LANES]
    assert set(seen) == {"price_minute", *lane_datasets}
    for dataset in lane_datasets:
        expected = "s3://b/u.json" if dataset in UNIVERSE_DATASETS else None
        assert seen[dataset] == expected, (
            f"{dataset}: universe 축이 UNIVERSE_DATASETS 와 갈렸다 — "
            f"기대 {expected!r}, 실제 {seen[dataset]!r}")


def test_승객_계획이_예외로_죽어도_구동_레인은_올라간다(monkeypatch, wiring):
    """`plan_session_cli` 의 except 는 (ValueError, OSError) 뿐이라 universe 객체를 읽는
    S3 의 botocore ClientError 는 그대로 뚫고 나온다. 구동 레인 스케일업이 승객 계획
    **뒤**에 있으면 그 한 번에 가격 레인이 통째로 안 뜬다 — 승객 결손이 하루치 가격
    결손으로 번지는 것이 이 모듈이 가장 피하려는 결과다."""
    monkeypatch.setenv(session_ops.ENV_INAV_SOURCE_GROUP, "kis")
    monkeypatch.setenv(session_ops.ENV_INAV_WORKER_SERVICES, "svc-inav-worker")
    monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)

    def plan(settings, **k):
        if k["dataset"] == "price_minute":
            return 0
        raise RuntimeError("botocore ClientError: AccessDenied on GetObject")

    monkeypatch.setattr(session_ops, "plan_session_cli", plan)

    with pytest.raises(RuntimeError):
        session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

    assert wiring.calls == [
        {"desiredCount": 1, "forceNewDeployment": True, "services": None},
        {"desiredCount": 1, "forceNewDeployment": True,
         "services": ["svc-analysis-consumer"]},
    ], "승객 계획의 예외가 구동 레인 스케일업을 삼키면 안 된다"


def test_뒤_레인_계획이_예외로_죽어도_앞_레인은_이미_올라가_있다(monkeypatch, wiring):
    """⭐ 위 테스트와 **같은 논증의 한 층 아래**다(#642 봇 P2).

    스케일업을 계획 루프 **뒤**로 모으면, 뒤 레인의 계획이 예외로 죽을 때 이미 계획에
    성공한 앞 레인이 desired 0 인 채 남는다 — 세션은 원장에 섰는데 Worker 가 없으니
    그 레인은 그날 조용히 아무것도 수집하지 않는다(계획 성공이라 실패로도 안 보인다).
    레인은 서로 독립이므로 한 레인의 S3 사고가 다른 레인을 끌고 내려가면 안 된다.

    첫 레인만 계획에 성공시키고 그 다음 레인에서 터뜨린다 — 표 순서에 기대지 않도록
    `_OPTIONAL_LANES` 에서 앞 둘을 뽑는다.
    """
    from data_pipeline.minute.states import SOURCE_GROUPS_BY_DATASET

    first, second = session_ops._OPTIONAL_LANES[0], session_ops._OPTIONAL_LANES[1]
    for lane in (first, second):
        monkeypatch.setenv(lane.source_env, sorted(SOURCE_GROUPS_BY_DATASET[lane.dataset])[0])
        monkeypatch.setenv(lane.services_env, f"svc-{lane.dataset}")
    monkeypatch.setattr(session_ops, "is_trading_day", lambda day: True)

    def plan(settings, **k):
        if k["dataset"] == second.dataset:
            raise RuntimeError("botocore ClientError: AccessDenied on GetObject")
        return 0

    monkeypatch.setattr(session_ops, "plan_session_cli", plan)

    with pytest.raises(RuntimeError):
        session_ops.start_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis",
            universe="s3://b/u.json")

    assert wiring.calls == [
        {"desiredCount": 1, "forceNewDeployment": True, "services": None},
        {"desiredCount": 1, "forceNewDeployment": True,
         "services": ["svc-analysis-consumer"]},
        {"desiredCount": 1, "forceNewDeployment": True, "services": [f"svc-{first.dataset}"]},
    ], f"{second.dataset} 의 예외가 이미 계획된 {first.dataset} 의 스케일업을 삼켰다"


def test_stop_은_승객_배선_결손에도_부분_스케일다운을_안_남긴다(monkeypatch, wiring):
    """토글은 켜졌는데 워커 목록이 빈 배선에서, 그 조회를 스케일다운 루프 안에서 처음
    하면 공용 서비스를 0 으로 내린 **뒤** SystemExit 이라 나머지 승객 워커가 밤새
    desired 1 로 남는다. 게이트 전에 전부 해석하면 아무것도 안 내린 채 죽는다."""
    monkeypatch.setenv(session_ops.ENV_NEWS_SOURCE_GROUP, "bigkinds")   # 켜고
    monkeypatch.delenv(session_ops.ENV_NEWS_WORKER_SERVICES, raising=False)  # 목록은 빔
    monkeypatch.setenv(session_ops.ENV_INAV_WORKER_SERVICES, "svc-inav-worker")
    monkeypatch.setattr(session_ops, "_queue_depths", lambda queues: [])
    monkeypatch.setattr(session_ops.time, "sleep", lambda _: None)

    class Ledger:
        def session_snapshot(self, *, session_id):
            return {"phase": "DRAINED"}

        def request_drain(self, *, session_id, now):
            return True

    monkeypatch.setattr(session_ops, "MinuteLedger", lambda **_: Ledger())
    monkeypatch.setattr(session_ops, "JobLedger", lambda **_: FakeJobs())

    with pytest.raises(SystemExit):
        session_ops.stop_session_cli(
            FakeSettings(), dataset="price_minute", source_group="kis")

    assert wiring.calls == [], "배선 결손은 아무것도 내리기 전에 죽어야 한다"
