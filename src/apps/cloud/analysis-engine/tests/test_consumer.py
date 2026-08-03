"""분봉 트리거 큐 소비자 테스트 (ALPHA-719).

의도: 이 소비자의 결함은 돈과 유실 두 방향으로 조용하다 — ①멱등 프리플라이트가 깨지면
재배달마다 LLM 이 재과금되고(벽시계가 run id 재료), ②실패 메시지를 지우면 그 트리거의
설명이 영영 없다. 그래서 고정하는 건 **삭제의 조건**(성공·중복만 지운다)과 **실패 3분류의
처방**(ReturnsNotReady=긴 지연 / 계약 위반=남김 / 기타=기본 재배달), 그리고 route 유도식이
eventstore 계보 writer 와 **한 곳**에서 나온다는 사실이다.
"""
from __future__ import annotations

import json

import pytest

from edge_analysis.config import ReturnsNotReadyError
from edge_analysis.consumer import (
    RETURNS_RETRY_SECONDS,
    consume_triggers,
    parse_trigger_message,
)


def envelope(trigger_id="mpt_1", event_type="PriceTriggerFired"):
    return json.dumps({
        "event_id": f"{event_type}:{trigger_id}:0",
        "event_type": event_type,
        "payload": {"trigger_id": trigger_id, "entity_id": "e1"},
    })


class FakeSqs:
    def __init__(self, bodies):
        self._queue = [{"Body": b, "ReceiptHandle": f"r{i}"} for i, b in enumerate(bodies)]
        self.deleted = []
        self.visibility = []

    def receive_message(self, **_):
        return {"Messages": [self._queue.pop(0)]} if self._queue else {}

    def delete_message(self, *, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)

    def change_message_visibility(self, *, QueueUrl, ReceiptHandle, VisibilityTimeout):
        self.visibility.append((ReceiptHandle, VisibilityTimeout))


def test_route_derivation_is_single_sourced():
    """소비자의 프리플라이트와 계보 writer 가 같은 함수를 쓴다 — 갈리면 프리플라이트가
    항상 False 라 재배달마다 새 run + LLM 재과금(조용한 붕괴)."""
    from edge_analysis.adapters import eventstore
    import edge_analysis.consumer as consumer_module

    assert consumer_module.minute_route_id is eventstore.minute_route_id


def test_success_deletes_message():
    sqs = FakeSqs([envelope("t1")])
    rc = consume_triggers("q", max_polls=1, process_fn=lambda t: "explained", sqs_client=sqs)
    assert rc == 0
    assert sqs.deleted == ["r0"]


def test_duplicate_is_deleted_without_rerun():
    """중복(route 에 run 존재)은 지운다 — 남기면 재배달이 계속 돌며 receive 예산만 태운다."""
    seen = []
    sqs = FakeSqs([envelope("t1")])
    rc = consume_triggers("q", max_polls=1,
                          process_fn=lambda t: seen.append(t) or "skipped_duplicate",
                          sqs_client=sqs)
    assert rc == 0 and sqs.deleted == ["r0"] and seen == ["t1"]


def test_returns_not_ready_defers_without_delete():
    """장중(당일 price_daily 부재)은 시간이 낫게 하는 실패다 — 지우지 않고 길게 미룬다.
    짧은 재배달 반복은 maxReceiveCount 예산을 태워 15:40 배치 전에 DLQ 로 보낸다."""
    def boom(_):
        raise ReturnsNotReadyError("장중")
    sqs = FakeSqs([envelope("t1")])
    rc = consume_triggers("q", max_polls=1, process_fn=boom, sqs_client=sqs)
    assert rc == 0
    assert sqs.deleted == []
    # 처리 전 연장(900) 뒤 지연 재배달(1800)로 덮인다
    assert [v for _, v in sqs.visibility] == [900, RETURNS_RETRY_SECONDS]


def test_generic_failure_leaves_message_and_fails_bounded_run():
    """일시 장애는 지우지 않고 가시성을 되돌려 재배달 — bounded 검증에선 exit 1
    (소비 경로가 통째로 죽은 환경이 초록으로 보이면 안 된다)."""
    def boom(_):
        raise RuntimeError("db down")
    sqs = FakeSqs([envelope("t1")])
    rc = consume_triggers("q", max_polls=1, process_fn=boom, sqs_client=sqs)
    assert rc == 1
    assert sqs.deleted == []
    # 처리 전 연장(900) → 실패 후 되돌림(60)
    assert [v for _, v in sqs.visibility] == [900, 60]


def test_processing_extends_visibility_first():
    """LLM 처리(수분)가 큐 기본 가시성(300초)을 넘으면 재배달본이 프리플라이트(아직
    run 없음)를 통과해 이중 과금된다 — 연장이 **process_fn 보다 먼저** 걸려야 한다."""
    sqs = FakeSqs([envelope("t1")])
    order = []

    def probe(t):
        order.append(("processed", list(sqs.visibility)))
        return "explained"

    consume_triggers("q", max_polls=1, process_fn=probe, sqs_client=sqs)
    # process_fn 진입 시점에 이미 연장이 기록돼 있어야 한다 — 뒤로 옮기는 회귀를 거부
    assert order == [("processed", [("r0", 900)])]
    assert [v for _, v in sqs.visibility] == [900]


def test_visibility_failure_does_not_kill_the_loop():
    """가시성 변경은 최선 노력이다 — SQS 스로틀 한 번에 상주 소비가 통째로 서면 안 된다."""
    class ThrottlingSqs(FakeSqs):
        def change_message_visibility(self, **kwargs):
            raise RuntimeError("throttled")

    sqs = ThrottlingSqs([envelope("t1")])
    rc = consume_triggers("q", max_polls=1, process_fn=lambda t: "explained", sqs_client=sqs)
    assert rc == 0
    assert sqs.deleted == ["r0"], "연장 실패는 배달 타이밍 문제일 뿐 처리는 계속돼야 한다"


def test_zero_max_polls_is_rejected():
    with pytest.raises(SystemExit):
        consume_triggers("q", max_polls=0, sqs_client=FakeSqs([]))


def test_malformed_is_left_and_flagged():
    """계약 위반은 지우지 않고(근거 보존 — DLQ 행) bounded 실행에선 exit 1 로 드러난다."""
    sqs = FakeSqs(["not json", envelope("t1", event_type="SomethingElse")])
    rc = consume_triggers("q", max_polls=2, process_fn=lambda t: "explained", sqs_client=sqs)
    assert rc == 1
    assert sqs.deleted == []


@pytest.mark.parametrize("body,reason", [
    ("[]", "객체 아님"),
    (json.dumps({"event_type": "PriceTriggerFired", "payload": {}}), "trigger_id 결손"),
    (json.dumps({"event_type": "PriceTriggerFired", "payload": {"trigger_id": "  "}}), "공백"),
    (json.dumps({"event_type": "PriceTriggerFired", "payload": "x"}), "payload 비객체"),
])
def test_parse_rejects_contract_violations(body, reason):
    with pytest.raises(ValueError):
        parse_trigger_message(body)


def test_parse_extracts_trigger_id():
    assert parse_trigger_message(envelope("mpt_9")) == "mpt_9"
