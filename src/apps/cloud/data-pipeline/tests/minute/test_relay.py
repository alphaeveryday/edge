"""Outbox Relay 테스트 (ALPHA-670, 계획 §11 해당분).

의도: Relay 가 멈추면 **커밋은 됐는데 아무도 모르는** 상태가 무한정 쌓인다(outbox 는
차오르고 Consumer 는 놀고 있다). 그래서 여기서 고정하는 건 "멈추지 않는가"다:

- 발행 못 할 event 하나(미정의 destination·크기 초과)가 나머지 큐를 막지 않는가
- 발행 결과를 모르는 event 를 성공으로 접지 않는가(유실 은폐 금지)
- 실패가 backlog 를 영원히 붙잡지 않는가(예산 소진 → 조회 가능한 DEAD)
- 경쟁 Relay·crash 에서 event 하나가 두 번 처리되거나 사라지지 않는가
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.jobs import JobLedger
from data_pipeline.minute.relay import (
    OutboxMessage,
    OutboxRelay,
    relay_cli,
    PublishFailure,
    RelayConfig,
    SqsPublisher,
    build_message_body,
)

_DB = DbConfig(password="x")
NOW = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
QUEUES = {
    "price-analysis-realtime": "https://sqs/price",
    "news-extraction-realtime": "https://sqs/news",
    "news-extraction-backfill": "https://sqs/backfill",
}


class FakePublisher:
    """발행 결과를 시나리오로 선언하는 fake — 실 SQS 호출 없이 결과 분기를 재현한다."""

    def __init__(self, *, failures=(), raises=None, drop=()):
        self.failures = {f.event_id: f for f in failures}
        self.raises = raises
        self.drop = set(drop)  # 성공에도 실패에도 안 나오는 event(결과 미보고)
        self.sent: list[tuple[str, tuple]] = []

    def publish_batch(self, queue_url, messages):
        if self.raises is not None:
            raise self.raises
        self.sent.append((queue_url, messages))
        published = {
            m.event_id for m in messages
            if m.event_id not in self.failures and m.event_id not in self.drop
        }
        return frozenset(published), tuple(
            self.failures[m.event_id] for m in messages if m.event_id in self.failures
        )


def enqueue(db, event_id, destination="price-analysis-realtime", payload=None):
    jobs = JobLedger(db=_DB, connect_fn=db.connect)
    jobs.insert_outbox_event(
        event_id=event_id, event_type="PriceWindowCommitted", destination=destination,
        aggregate_id="job-" + event_id, generation=1,
        payload=payload or {"job_id": "job-" + event_id},
    )
    return jobs


def build_relay(db, *, publisher=None, relay_id="relay-1", queues=None, **overrides):
    return OutboxRelay(
        jobs=JobLedger(db=_DB, connect_fn=db.connect),
        publisher=publisher or FakePublisher(),
        config=RelayConfig(
            relay_id=relay_id,
            queue_urls=QUEUES if queues is None else queues,
            **overrides,
        ),
    )


class TestPublishing:
    def test_publishes_batch_and_records_published_at(self, tmp_path):
        db = FakeMinuteDB()
        enqueue(db, "e1")
        enqueue(db, "e2", destination="news-extraction-realtime")
        relay = build_relay(db)
        assert relay.tick(NOW) == "PUBLISHED"
        assert {r["status"] for r in db.outbox.values()} == {"PUBLISHED"}
        assert all(r["published_at"] == NOW for r in db.outbox.values())
        # destination 별로 각자의 큐에 간다 — 가격·뉴스는 큐를 공유하지 않는다(v0.7 12.1)
        assert {url for url, _ in relay.publisher.sent} == {
            "https://sqs/price", "https://sqs/news"
        }

    def test_message_body_carries_event_identity(self, tmp_path):
        # Consumer(7A)의 멱등 키가 event_id 다 — payload 만 실으면 재전달을 구분 못 한다
        db = FakeMinuteDB()
        enqueue(db, "e1", payload={"job_id": "job-1", "generation": 2})
        relay = build_relay(db)
        relay.tick(NOW)
        [(_, messages)] = relay.publisher.sent
        body = json.loads(messages[0].body)
        assert body == {
            "event_id": "e1", "event_type": "PriceWindowCommitted",
            "payload": {"job_id": "job-1", "generation": 2},
        }

    def test_routing_covers_all_three_queues(self, tmp_path):
        db = FakeMinuteDB()
        for index, destination in enumerate(sorted(QUEUES)):
            enqueue(db, f"e{index}", destination=destination)
        relay = build_relay(db)
        assert relay.tick(NOW) == "PUBLISHED"
        assert {url for url, _ in relay.publisher.sent} == set(QUEUES.values())

    def test_backlog_drains_across_ticks(self, tmp_path):
        # batch 상한을 넘는 backlog 도 tick 을 거듭해 비워야 한다 — 남으면 oldest-age 가 안 준다
        db = FakeMinuteDB()
        for index in range(25):
            enqueue(db, f"e{index}")
        relay = build_relay(db, batch_limit=10)
        states = [relay.tick(NOW + timedelta(seconds=i)) for i in range(4)]
        assert states == ["PUBLISHED", "PUBLISHED", "PUBLISHED", "IDLE"]
        assert {r["status"] for r in db.outbox.values()} == {"PUBLISHED"}


class TestOneEventCannotStopTheRelay:
    def test_unknown_destination_is_isolated_not_fatal(self, tmp_path):
        # ⚠️ 예외로 죽으면 그 행이 outbox 에 남아 다음 tick 도 같은 자리에서 죽고,
        # 멀쩡한 다른 큐까지 영구히 멈춘다. 격리하고 나머지는 계속 흐른다.
        db = FakeMinuteDB()
        enqueue(db, "bad", destination="typo-queue")
        enqueue(db, "good")
        relay = build_relay(db)
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["bad"]["status"] == "DEAD"
        assert "미정의 destination" in db.outbox["bad"]["last_error"]
        assert db.outbox["good"]["status"] == "PUBLISHED"
        # 다음 tick 은 DEAD 를 다시 집지 않는다 — 루프가 살아 있다
        assert relay.tick(NOW + timedelta(seconds=1)) == "IDLE"

    def test_oversized_message_is_terminal(self, tmp_path):
        # 재시도해도 영원히 안 들어가는 메시지 — transient 로 두면 backlog 가 안 빈다
        db = FakeMinuteDB()
        enqueue(db, "huge", payload={"blob": "x" * 1_100_000})  # 1 MiB 초과
        relay = build_relay(db, publisher=SqsPublisher(client=object()))
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["huge"]["status"] == "DEAD"
        assert "상한" in db.outbox["huge"]["last_error"]

    def test_publish_exception_retries_whole_batch(self, tmp_path):
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(db, publisher=FakePublisher(raises=RuntimeError("network down")))
        assert relay.tick(NOW) == "PARTIAL"
        row = db.outbox["e1"]
        assert row["status"] == "NEW" and row["attempt_count"] == 1
        assert row["next_attempt_at"] > NOW  # 즉시 재시도 금지(tight loop 방지)
        assert row["claimed_by"] is None  # claim 해제 — 다음 tick 이 다시 집는다

    def test_unreported_result_is_not_treated_as_success(self, tmp_path):
        # 성공 목록에도 실패 목록에도 없는 event = 발행 여부 불명. 성공으로 접으면
        # 유실이 조용히 확정된다(Rule 12).
        db = FakeMinuteDB()
        enqueue(db, "ghost")
        relay = build_relay(db, publisher=FakePublisher(drop=["ghost"]))
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["ghost"]["status"] == "NEW"
        assert db.outbox["ghost"]["last_error"] == "발행 결과 미보고"


class TestRetryBudget:
    def test_transient_failure_backs_off_exponentially(self, tmp_path):
        db = FakeMinuteDB()
        enqueue(db, "e1")
        failure = PublishFailure("e1", "ServiceUnavailable: 503")
        relay = build_relay(db, publisher=FakePublisher(failures=[failure]),
                            retry_base_seconds=2, retry_max_seconds=60)
        delays = []
        for attempt in range(3):
            # 재시도 시각이 지난 뒤에만 다시 집힌다 — DB 가 시각의 권위다
            at = NOW + timedelta(hours=attempt)
            assert relay.tick(at) == "PARTIAL"
            delays.append((db.outbox["e1"]["next_attempt_at"] - at).total_seconds())
        assert delays == [2, 4, 8]

    def test_long_outage_never_turns_transient_into_dead(self, tmp_path):
        # ⚠️ 시도 횟수로 포기하면 몇 분짜리 SQS 장애가 event 를 되돌릴 수 없는 DEAD 로
        # 만든다 — 이 단계엔 redrive 가 없어(PR 7A) 큐가 복구돼도 영원히 미발행이다.
        # 지연은 알람이 잡지만 유실은 아무도 못 되돌린다.
        db = FakeMinuteDB()
        enqueue(db, "e1")
        broken = FakePublisher(failures=[PublishFailure("e1", "ServiceUnavailable: 503")])
        relay = build_relay(db, publisher=broken)
        for attempt in range(20):
            relay.tick(NOW + timedelta(hours=attempt))
        row = db.outbox["e1"]
        assert row["status"] == "NEW", "일시 장애가 event 를 영구 폐기했다"
        assert row["attempt_count"] == 20 and row["next_attempt_at"] is not None
        # 큐가 돌아오면 그대로 발행된다
        relay.publisher = FakePublisher()
        assert relay.tick(NOW + timedelta(hours=21)) == "PUBLISHED"
        assert db.outbox["e1"]["status"] == "PUBLISHED"

    def test_sender_fault_is_terminal_immediately(self, tmp_path):
        # 요청 자체가 틀린 실패(SenderFault)는 재시도해도 같다
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(
            db, publisher=FakePublisher(failures=[PublishFailure("e1", "InvalidParameter", True)])
        )
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["e1"]["status"] == "DEAD"


class TestConcurrencyAndCrash:
    def test_two_relays_do_not_double_claim(self, tmp_path):
        # 진짜 경쟁은 **발행 전**이다 — 한쪽이 claim 을 쥔 동안 다른 쪽이 같은 event 를
        # 집으면 같은 메시지가 두 번 나간다. lease 가 살아 있는 동안은 침범 금지.
        db = FakeMinuteDB()
        enqueue(db, "e1")
        first = build_relay(db, relay_id="relay-1", lease_seconds=60)
        second = build_relay(db, relay_id="relay-2", lease_seconds=60)
        held = first.jobs.claim_outbox_batch(
            relay_id="relay-1", now=NOW, limit=10, lease_seconds=60
        )
        assert [e["event_id"] for e in held] == ["e1"]
        assert second.tick(NOW) == "IDLE"
        assert not second.publisher.sent, "lease 유효 구간에서 두 번째 Relay 가 발행했다"
        # 발행까지 끝난 뒤에도 재발행하지 않는다
        assert first.tick(NOW + timedelta(seconds=61)) == "PUBLISHED"
        assert second.tick(NOW + timedelta(seconds=62)) == "IDLE"

    def test_crash_before_publish_is_reclaimed_after_lease(self, tmp_path):
        # claim 만 하고 죽은 event 는 lease 만료 후 다른 Relay 가 회수한다(유실 0)
        db = FakeMinuteDB()
        enqueue(db, "e1")
        dead = build_relay(db, relay_id="relay-dead", lease_seconds=60)
        dead.jobs.claim_outbox_batch(relay_id="relay-dead", now=NOW, limit=10, lease_seconds=60)
        alive = build_relay(db, relay_id="relay-2")
        assert alive.tick(NOW + timedelta(seconds=30)) == "IDLE"  # lease 유효 — 침범 금지
        assert alive.tick(NOW + timedelta(seconds=61)) == "PUBLISHED"

    def test_mark_failure_after_claim_loss_does_not_corrupt(self, tmp_path):
        # 발행 후 DB 기록 전에 claim 을 잃으면 행은 NEW 로 남아 재발행된다 —
        # 중복은 Consumer 의 event_id 멱등이 흡수한다(v0.7 9절 복구 표)
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(db, relay_id="relay-1", lease_seconds=60)
        batch = relay.jobs.claim_outbox_batch(
            relay_id="relay-1", now=NOW, limit=10, lease_seconds=60
        )
        db.outbox["e1"]["claim_expires_at"] = NOW + timedelta(seconds=999)  # 다른 claim 흉내
        assert relay.jobs.mark_published(
            event_id="e1", relay_id="relay-1", claim_token=batch[0]["claim_token"], now=NOW
        ) is False
        assert db.outbox["e1"]["status"] == "NEW"


class TestShutdown:
    def test_sigterm_stops_before_new_claim(self, tmp_path):
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(db)
        relay.request_stop()
        assert relay.tick(NOW) == "STOPPED"
        assert db.outbox["e1"]["status"] == "NEW"  # 남은 건 다음 Relay 가 가져간다
        assert not relay.publisher.sent


class TestConfigGuards:
    @pytest.mark.parametrize(
        ("field", "value"),
        [("batch_limit", 0), ("retry_base_seconds", 0)],
    )
    def test_invalid_config_fails_loud(self, field, value):
        with pytest.raises(ValueError):
            RelayConfig(relay_id="r", queue_urls=QUEUES, **{field: value})

    def test_missing_known_destination_refuses_to_start(self):
        # ⚠️ 런타임 DEAD 격리는 한 건짜리 사고용이다. 큐 매핑에서 destination 하나가
        # 빠지면 그 큐로 갈 event 가 **전부** DEAD 가 되는데 DEAD 는 스스로 안 풀린다
        # (redrive=PR 7A). 설정 오타를 데이터 파괴가 아니라 배포 실패로 만든다.
        partial = {k: v for k, v in QUEUES.items() if k != "news-extraction-backfill"}
        with pytest.raises(ValueError, match="news-extraction-backfill"):
            RelayConfig(relay_id="r", queue_urls=partial)

    def test_backoff_cap_is_respected(self, tmp_path):
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(db, publisher=FakePublisher(failures=[PublishFailure("e1", "503")]),
                            retry_base_seconds=2, retry_max_seconds=5)
        for attempt in range(4):
            relay.tick(NOW + timedelta(hours=attempt))
        assert (db.outbox["e1"]["next_attempt_at"] - (NOW + timedelta(hours=3))).total_seconds() == 5


class TestEnvelopeDeterminism:
    def test_same_event_yields_same_bytes(self):
        event = {"event_id": "e1", "event_type": "NewsExtractionRequested",
                 "payload": {"b": 2, "a": 1}}
        assert build_message_body(event) == build_message_body(dict(event))


class TestSqsResponseGate:
    """SqsPublisher 가 **실제 SQS 응답 형상**을 소비하는 경로. FakePublisher 로는
    이 게이트가 전혀 검증되지 않는다(응답을 event_id 집합으로 선처리하므로) — 여기서만
    Id 대조·청킹 경계가 반례에 걸린다."""

    class StubSqs:
        def __init__(self, responses=None):
            self.responses = list(responses or [])
            self.requests: list[dict] = []

        def send_message_batch(self, **kwargs):
            self.requests.append(kwargs)
            if self.responses:
                return self.responses.pop(0)
            return {"Successful": [{"Id": e["Id"]} for e in kwargs["Entries"]]}

    def _messages(self, count, body="{}"):
        return tuple(OutboxMessage(f"e{i}", body) for i in range(count))

    def test_partial_batch_failure_maps_to_right_events(self):
        stub = self.StubSqs([{
            "Successful": [{"Id": "0"}, {"Id": "2"}],
            "Failed": [{"Id": "1", "Code": "InternalError", "Message": "boom"}],
        }])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(3))
        assert published == frozenset({"e0", "e2"})
        assert [(f.event_id, f.terminal) for f in failures] == [("e1", False)]

    def test_sender_fault_entry_is_terminal(self):
        stub = self.StubSqs([{
            "Failed": [{"Id": "0", "Code": "InvalidParameterValue",
                        "Message": "bad", "SenderFault": True}],
        }])
        _, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert failures[0].terminal is True

    @pytest.mark.parametrize("bogus_id", ["-1", "00", " 0", "9", "", "abc"])
    def test_unknown_id_never_marks_another_event_published(self, bogus_id):
        # ⚠️ int() 강제였다면 "-1" 이 chunk[-1](마지막 event)을 PUBLISHED 로 확정해
        # 그 event 가 재발행 대상에서 빠진 채 조용히 유실된다
        stub = self.StubSqs([{"Successful": [{"Id": bogus_id}]}])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(3))
        assert published == frozenset(), f"보내지 않은 Id({bogus_id!r})가 성공 처리됐다"
        assert failures == ()

    def test_duplicate_id_reported_once(self):
        stub = self.StubSqs([{
            "Successful": [{"Id": "0"}],
            "Failed": [{"Id": "0", "Code": "InternalError", "Message": "boom"}],
        }])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        # 같은 Id 가 양쪽에 오면 먼저 온 판정만 쓴다 — 성공/실패를 동시에 세지 않는다
        assert published == frozenset({"e0"}) and failures == ()

    def test_missing_entry_is_left_unreported(self):
        # 응답에 아예 없는 event 는 성공도 실패도 아니다 — Relay 가 "미보고"로 재시도한다
        stub = self.StubSqs([{"Successful": [{"Id": "0"}]}])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(2))
        assert published == frozenset({"e0"}) and failures == ()

    def test_chunks_by_count_of_ten(self):
        stub = self.StubSqs()
        published, _ = SqsPublisher(client=stub).publish_batch("q", self._messages(23))
        assert [len(r["Entries"]) for r in stub.requests] == [10, 10, 3]
        assert len(published) == 23

    def test_chunks_by_total_bytes(self):
        # 건수만 보면 큰 메시지 5건이 한 요청에 몰려 BatchRequestTooLong 으로 요청
        # 전체가 거부되고, 같은 묶음이 예산 소진까지 반복 실패한다
        stub = self.StubSqs()
        big = tuple(OutboxMessage(f"e{i}", "x" * 400_000) for i in range(5))
        published, _ = SqsPublisher(client=stub).publish_batch("q", big)
        assert [len(r["Entries"]) for r in stub.requests] == [2, 2, 1]
        assert len(published) == 5
        for request in stub.requests:
            total = sum(len(e["MessageBody"].encode()) for e in request["Entries"])
            assert total <= 1_048_576

    def test_oversized_single_message_never_reaches_sqs(self):
        stub = self.StubSqs()
        published, failures = SqsPublisher(client=stub).publish_batch(
            "q", (OutboxMessage("big", "x" * 1_100_000), OutboxMessage("ok", "{}"))
        )
        assert published == frozenset({"ok"})
        assert failures[0].event_id == "big" and failures[0].terminal is True
        # 상한 초과분은 요청에 실리지 않는다 — 실리면 요청 전체가 거부된다
        assert [len(r["Entries"]) for r in stub.requests] == [1]


class TestCliGuards:
    """진입점의 fail-loud — 설정 누락은 배포 시점에 조용히 통과하면 안 된다."""

    def _settings(self, *, db=None, minute_relay=None):
        return SimpleNamespace(db=db, minute_relay=minute_relay)

    def test_missing_db_config_fails_loud(self):
        with pytest.raises(SystemExit, match="db 설정 없음"):
            relay_cli(self._settings(minute_relay=object()))

    def test_missing_queue_mapping_fails_loud(self):
        # 큐 매핑 없이 뜨면 **모든** event 가 미정의 destination 으로 DEAD 된다 —
        # 조용히 기동시키지 않는다
        with pytest.raises(SystemExit, match="minute_relay 설정 없음"):
            relay_cli(self._settings(db=_DB))
