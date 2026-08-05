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

from edge_analysis.adapters.superadmin import SuperAdminUnavailableError
from edge_analysis.config import ReturnsNotReadyError
from edge_analysis.consumer import (
    RETURNS_RETRY_SECONDS,
    REVERT_REASON,
    consume_triggers,
    parse_message,
    revert_explanations,
)


def envelope(trigger_id="mpt_1", event_type="PriceTriggerFired"):
    return json.dumps({
        "event_id": f"{event_type}:{trigger_id}:0",
        "event_type": event_type,
        "payload": {"trigger_id": trigger_id, "entity_id": "e1"},
    })


def revert_envelope(entity_id="091160", session_id="s-2026-08-04", **extra):
    payload = {
        "entity_id": entity_id, "session_id": session_id,
        "window_start": "2026-08-04T02:31:00+00:00", "prev_close": "10000",
        "close_price": "10050", "open_change": "0.005",
        "detection_policy_version": "v1", **extra,
    }
    return json.dumps({
        "event_id": f"ExposureReverted:{entity_id}:0",
        "event_type": "ExposureReverted",
        "payload": payload,
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


def test_returns_not_ready_defers_without_delete(caplog):
    """분봉 window 원장·분모 미준비는 시간이 낫게 하는 실패다(ALPHA-710) — 지우지 않고
    짧은 고정 지연으로 미룬다. 진짜 결손이면 반복이 receive 예산(16)을 태워 DLQ(근거
    보존)로 가는 것이 맞다. 지연이 길어지면(구 배치 착지 산술) 장중 즉시성이 죽는다.

    로그에 **예외 메시지가 실려야 한다**: 사유가 넷(트리거 window 원장 미착지·분모
    파티션 부재·구성종목 가격 0건·checksum 불일치)인데 고정 문구만 찍으면 어느 것인지
    못 가린다 — 08-05 dev 에서 하루치 실패(709건)가 그렇게 조용히 흘렀다(Rule 12).
    """
    import logging

    def boom(_):
        raise ReturnsNotReadyError("직전 거래일 price_daily 파티션이 없다")
    sqs = FakeSqs([envelope("t1")])
    with caplog.at_level(logging.INFO, logger="edge_analysis.consumer"):
        rc = consume_triggers("q", max_polls=1, process_fn=boom, sqs_client=sqs)
    assert rc == 0
    assert sqs.deleted == []
    # 처리 전 연장(900) 뒤 짧은 지연 재배달 — 분 단위 즉시성의 상한을 고정한다.
    assert sqs.visibility == [("r0", 900), ("r0", RETURNS_RETRY_SECONDS)]
    assert RETURNS_RETRY_SECONDS <= 300, "재시도 지연이 분 단위 즉시성을 깨면 안 된다"
    assert "직전 거래일 price_daily 파티션이 없다" in caplog.text, (
        "사유 없이 고정 문구만 남으면 네 갈래를 로그로 못 가린다")


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
    (json.dumps({"event_type": "ExposureReverted",
                 "payload": {"entity_id": "e1", "window_start": "2026-08-04T02:31:00+00:00"}}),
     "session_id 결손 — 세션 없이 회수하면 남의 날짜 설명까지 내린다"),
    (json.dumps({"event_type": "ExposureReverted",
                 "payload": {"session_id": "s1", "window_start": "2026-08-04T02:31:00+00:00"}}),
     "entity_id 결손"),
    (json.dumps({"event_type": "ExposureReverted",
                 "payload": {"entity_id": "e1", "session_id": "s1"}}),
     "window_start 결손 — 상한 없이 회수하면 이후 재발화 설명까지 내린다"),
    (json.dumps({"event_type": "ExposureReverted",
                 "payload": {"entity_id": "e1", "session_id": "s1", "window_start": "어제쯤"}}),
     "window_start 비ISO"),
    (json.dumps({"event_type": "ExposureReverted",
                 "payload": {"entity_id": "e1", "session_id": "s1",
                             "window_start": "2026-08-04T02:31:00"}}),
     "window_start naive — TIMESTAMPTZ 비교가 DB 세션 시간대로 재해석돼 상한이 어긋난다"),
    (json.dumps({"event_type": "ExposureReverted",
                 "payload": {"entity_id": "091160 ", "session_id": "s1",
                             "window_start": "2026-08-04T02:31:00+00:00"}}),
     "entity_id 공백 — 정확 일치 질의 0건이 no-op 성공으로 위장된다"),
])
def test_parse_rejects_contract_violations(body, reason):
    with pytest.raises(ValueError):
        parse_message(body)


def test_parse_extracts_trigger_id():
    event_type, payload = parse_message(envelope("mpt_9"))
    assert (event_type, payload["trigger_id"]) == ("PriceTriggerFired", "mpt_9")


# ── ExposureReverted — 분봉 설명 자동 회수 (ALPHA-746) ─────────────────────


class FakeStore:
    """find_published_minute_run_ids 만 흉내 — 질의 축(entity·session·상한)을 기록한다."""

    def __init__(self, run_ids):
        self._run_ids = list(run_ids)
        self.queries = []

    def find_published_minute_run_ids(self, entity_id, session_id, until_window_start):
        self.queries.append((entity_id, session_id, until_window_start))
        return list(self._run_ids)


class FakeAdminClient:
    def __init__(self, outcomes=None, fail=None):
        self.logins = 0
        self.invalidations = []
        self._outcomes = dict(outcomes or {})
        self._fail = fail

    def login(self):
        if self._fail == "login":
            raise SuperAdminUnavailableError("login 5xx")
        self.logins += 1

    def invalidate(self, run_id, reason):
        if self._fail == "invalidate":
            raise SuperAdminUnavailableError("connection reset")
        self.invalidations.append((run_id, reason))
        return self._outcomes.get(run_id, "invalidated")


REVERT_PAYLOAD = {"entity_id": "091160", "session_id": "s1",
                  "window_start": "2026-08-04T02:31:00+00:00"}


def test_revert_invalidates_only_store_selected_published_minute_runs():
    """회수 대상은 store 질의(그 종목·세션·복귀 window 이전의 분봉 기원 PUBLISHED)가
    결정하고, 소비자는 그 목록 전부를 사유와 함께 무효화 API 로 보낸다. EOD 제외는 질의의
    minute_price_trigger INNER JOIN 이 맡는다 — 관측의 트리거 축은 정확히 하나라
    (ck_etf_contribution_one_trigger) EOD 계보는 구조적으로 안 걸린다. window_start 상한이
    질의에 전달돼야 지연된 회수가 이후 재발화 설명을 잡지 않는다(앵커 리셋, ALPHA-745)."""
    from datetime import datetime

    store = FakeStore(["run_a", "run_b"])
    client = FakeAdminClient()

    outcome = revert_explanations(dict(REVERT_PAYLOAD), store=store, client=client)

    assert outcome == "reverted"
    assert store.queries == [
        ("091160", "s1", datetime.fromisoformat("2026-08-04T02:31:00+00:00"))]
    assert client.invalidations == [("run_a", REVERT_REASON), ("run_b", REVERT_REASON)]
    assert "ALPHA-746" in REVERT_REASON, "감사 로그에 회수 주체 티켓이 남아야 한다"


def test_revert_with_no_published_targets_is_quiet_noop():
    """발화 전 회수(설명이 아직 없다)는 정상이다 — 로그인·API 호출 없이 성공으로 접는다.
    실패로 접으면 재배달이 영원히 돌고, API 를 부르면 자격 미주입 환경까지 죽는다."""
    client = FakeAdminClient()

    outcome = revert_explanations(
        dict(REVERT_PAYLOAD), store=FakeStore([]), client=client)

    assert outcome == "reverted_noop"
    assert client.logins == 0 and client.invalidations == []


def test_revert_any_target_missing_in_api_is_transient_not_success():
    """404 는 방금 읽은 run 이 API 원장에 없다는 뜻(오배선 URL·원장 불일치) — 그 설명은
    PUBLISHED 로 남았는데 성공으로 접으면(부분 404 포함) 재시도 기회가 사라진다(Rule 12).
    전 대상 시도 후 transient 로 올린다 — 재배달 재질의는 이미 내려간 건(WITHDRAWN)을
    다시 안 잡으므로 남은 건만 재시도되고, 반복되면 DLQ 가 드러낸다."""
    partial = FakeAdminClient(outcomes={"run_a": "not_found"})
    with pytest.raises(SuperAdminUnavailableError):
        revert_explanations(dict(REVERT_PAYLOAD),
                            store=FakeStore(["run_a", "run_b"]), client=partial)
    # 남은 대상(run_b)은 raise 전에 이미 시도됐다 — 부분 진행을 버리지 않는다.
    assert ("run_b", REVERT_REASON) in partial.invalidations


@pytest.mark.parametrize("fail", ["login", "invalidate"])
def test_revert_api_failure_is_transient_and_message_survives(fail):
    """API 연결 실패·5xx 는 성공으로 접지 않는다(Rule 12) — 메시지를 지우지 않고 재배달로
    올려야 남은 대상이 회수된다. invalidate 는 멱등(이미 무효=409)이라 재실행이 안전하다."""
    def failing_revert(payload):
        return revert_explanations(
            payload, store=FakeStore(["run_a"]), client=FakeAdminClient(fail=fail))

    sqs = FakeSqs([revert_envelope()])
    rc = consume_triggers("q", max_polls=1, revert_fn=failing_revert, sqs_client=sqs)

    assert rc == 1
    assert sqs.deleted == []
    assert [v for _, v in sqs.visibility] == [900, 60]


def test_revert_redelivery_is_idempotent():
    """재배달 재수신: 이미 내려간 설명은 PUBLISHED 질의에 안 걸리고(재질의=no-op), 경합으로
    걸려도 409(이미 무효)는 멱등 정상으로 접는다 — 재배달이 실패를 재생산하면 DLQ 만 쌓인다."""
    # 1차 집행 후 재배달 — 질의가 빈 목록을 돌려 no-op.
    assert revert_explanations(
        dict(REVERT_PAYLOAD), store=FakeStore([]),
        client=FakeAdminClient()) == "reverted_noop"
    # 질의·무효화 사이 경합 — 409 로 돌아와도 성공이다.
    client = FakeAdminClient(outcomes={"run_a": "already_withdrawn"})
    assert revert_explanations(
        dict(REVERT_PAYLOAD), store=FakeStore(["run_a"]),
        client=client) == "reverted"
    assert client.invalidations == [("run_a", REVERT_REASON)]


def test_consume_dispatches_revert_and_deletes_on_success():
    """같은 큐의 두 사건이 각자의 처리기로 갈린다 — ExposureReverted 가 트리거 경로로 새면
    trigger_id 결손으로 죽고, 계약 위반으로 분류되면 DLQ 로 가 회수가 영영 없다(이 PR 이
    판정기 PR(ALPHA-745)보다 먼저 배포돼야 하는 이유와 같은 축)."""
    reverted, explained = [], []
    sqs = FakeSqs([revert_envelope(entity_id="091160"), envelope("t1")])

    rc = consume_triggers(
        "q", max_polls=2,
        process_fn=lambda t: explained.append(t) or "explained",
        revert_fn=lambda p: reverted.append(p["entity_id"]) or "reverted",
        sqs_client=sqs)

    assert rc == 0
    assert reverted == ["091160"] and explained == ["t1"]
    assert sqs.deleted == ["r0", "r1"]
