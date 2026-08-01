"""Outbox Relay 테스트 (ALPHA-670, 계획 §11 해당분).

의도: Relay 가 멈추면 **커밋은 됐는데 아무도 모르는** 상태가 무한정 쌓인다(outbox 는
차오르고 Consumer 는 놀고 있다). 그래서 여기서 고정하는 건 "멈추지 않는가"다:

- 발행 못 할 event 하나(미정의 destination·크기 초과)가 나머지 큐를 막지 않는가
- 발행 결과를 모르는 event 를 성공으로 접지 않는가(유실 은폐 금지)
- 발행 불가 event 만 DEAD 로 격리하는가(일시 장애는 **횟수로 포기하지 않는다** — 이
  단계엔 redrive 가 없어 DEAD 가 곧 유실이다)
- 경쟁 Relay·crash 에서 event 하나가 두 번 처리되거나 사라지지 않는가
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from hashlib import md5
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

    def test_each_destination_goes_to_its_own_queue(self, tmp_path):
        # ⚠️ URL **집합**만 비교하면 price↔news 를 서로 바꿔 보내도 통과한다 — 잘못된
        # Consumer 가 깨어나고 행은 PUBLISHED 로 확정돼 설정을 고쳐도 안 되살아난다
        db = FakeMinuteDB()
        for index, destination in enumerate(sorted(QUEUES)):
            enqueue(db, f"e{index}", destination=destination)
        relay = build_relay(db)
        assert relay.tick(NOW) == "PUBLISHED"
        routed = {
            url: {m.event_id for m in messages} for url, messages in relay.publisher.sent
        }
        expected = {
            QUEUES[destination]: {f"e{index}"}
            for index, destination in enumerate(sorted(QUEUES))
        }
        assert routed == expected

    def test_backlog_drains_across_ticks(self, tmp_path):
        # batch 상한을 넘는 backlog 도 tick 을 거듭해 비워야 한다 — 남으면 oldest-age 가 안 준다
        db = FakeMinuteDB()
        for index in range(25):
            enqueue(db, f"e{index}")
        relay = build_relay(db, batch_limit=10)
        states = [relay.tick(NOW + timedelta(seconds=i)) for i in range(4)]
        assert states == ["PUBLISHED", "PUBLISHED", "PUBLISHED", "IDLE"]
        assert {r["status"] for r in db.outbox.values()} == {"PUBLISHED"}


class TestLaneIsolation:
    def test_failing_lane_does_not_starve_healthy_lanes(self, tmp_path):
        # ⚠️ 공용 Relay 를 쓰는 근거가 "가격 장애가 뉴스 발행을 막지 않는다"(v0.7 11.1)다.
        # 전역 FIFO 로 집으면 장애 레인의 오래된 재시도 행이 매 batch 를 채워 그 근거가
        # claim 층에서 무너진다 — destination 별로 나눠 집어야 한다.
        db = FakeMinuteDB()
        for index in range(30):  # 가격 레인이 batch 를 가득 채울 만큼 오래된 행
            enqueue(db, f"price{index}", destination="price-analysis-realtime")
        enqueue(db, "news1", destination="news-extraction-realtime")  # 가장 나중에 들어옴
        failing = FakePublisher(failures=[
            PublishFailure(f"price{i}", "ServiceUnavailable: 503") for i in range(30)
        ])
        relay = build_relay(db, publisher=failing, batch_limit=10)
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["news1"]["status"] == "PUBLISHED", "가격 장애가 뉴스를 굶겼다"

    def test_lane_is_claimed_right_before_its_publish(self, tmp_path):
        # ⚠️ 전부 집어놓고 순차 발행하면 뒤 레인의 lease 가 자기 차례 전에 흘러가
        # 다른 Relay 가 탈취한다 — lease 검증(batch × 호출 예산)이 무의미해진다.
        # claim 은 그 레인을 발행하기 **직전**에 일어나야 한다.
        db = FakeMinuteDB()
        enqueue(db, "p1", destination="price-analysis-realtime")
        enqueue(db, "n1", destination="news-extraction-realtime")
        order: list[str] = []

        class OrderRecordingPublisher(FakePublisher):
            def publish_batch(self, queue_url, messages):
                order.append(f"publish:{queue_url}")
                return super().publish_batch(queue_url, messages)

        relay = build_relay(db, publisher=OrderRecordingPublisher())
        original_claim = relay.jobs.claim_outbox_batch

        def recording_claim(**kwargs):
            claimed = original_claim(**kwargs)
            if claimed:
                order.append(f"claim:{kwargs.get('destination')}")
            return claimed

        relay.jobs.claim_outbox_batch = recording_claim
        assert relay.tick(NOW) == "PUBLISHED"
        # 각 레인의 claim 바로 뒤에 그 레인의 발행이 온다(claim 을 몰아서 하지 않는다)
        assert order == [
            "claim:news-extraction-realtime", "publish:https://sqs/news",
            "claim:price-analysis-realtime", "publish:https://sqs/price",
        ]

    def test_unknown_destination_rows_are_still_claimed(self, tmp_path):
        # destination 별 claim 은 설정에 없는 큐의 행을 건너뛴다 — 그것만 따로 집어
        # 격리하지 않으면 영원히 NEW 로 남아 아무도 모른다
        db = FakeMinuteDB()
        enqueue(db, "orphan", destination="retired-queue")
        enqueue(db, "ok")
        relay = build_relay(db)
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["orphan"]["status"] == "DEAD"
        assert db.outbox["ok"]["status"] == "PUBLISHED"

    def test_sweep_does_not_lease_healthy_rows(self, tmp_path):
        # 전역으로 집은 뒤 골라내면 처리하지 않을 정상 행까지 lease 로 묶여 그만큼
        # 발행이 밀린다 — 집을 것만 집어야 한다
        db = FakeMinuteDB()
        for index in range(15):
            enqueue(db, f"e{index}")
        relay = build_relay(db, batch_limit=10)
        relay.tick(NOW)
        leftover = [r for r in db.outbox.values() if r["status"] == "NEW"]
        assert len(leftover) == 5
        assert all(r["claimed_by"] is None for r in leftover), "처리 안 할 행을 묶어뒀다"


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

    def test_unserializable_event_is_isolated_from_its_batch(self, tmp_path):
        # 거대·비직렬화 payload 한 건이 같은 destination 의 정상 행까지 끌고 가면
        # 그 큐가 통째로 고착된다 — 행 단위로 격리해야 나머지가 흐른다
        db = FakeMinuteDB()
        enqueue(db, "bad")
        enqueue(db, "good")
        db.outbox["bad"]["payload"] = {"nan": float("nan")}  # canonical_json 이 거부
        relay = build_relay(db)
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["good"]["status"] == "PUBLISHED"
        assert db.outbox["bad"]["status"] == "NEW"
        assert "직렬화 실패" in db.outbox["bad"]["last_error"]

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
        # 계약은 "실패 시점부터 최소 base*2**n" — tick 안에서 흐른 시간이 더해진다
        for delay, expected in zip(delays, [2, 4, 8], strict=True):
            assert expected <= delay < expected + 1, f"backoff {delay} 이 {expected} 미만이다"

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

    def test_terminal_failure_is_recorded_as_dead(self, tmp_path):
        # publisher 가 terminal 로 분류한 실패(메시지 자체가 못 실림)는 재시도 예약 없이
        # 조회 가능한 DEAD 로 간다. **무엇이 terminal 인지의 판정**은 SqsPublisher 소관이라
        # TestSqsResponseGate 가 실제 응답으로 검증한다(여기서 SenderFault 를 논하지 않는다).
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(
            db, publisher=FakePublisher(
                failures=[PublishFailure("e1", "InvalidMessageContents", True)]
            )
        )
        assert relay.tick(NOW) == "PARTIAL"
        assert db.outbox["e1"]["status"] == "DEAD"
        assert db.outbox["e1"]["next_attempt_at"] is None


class TestConcurrencyAndCrash:
    def test_two_relays_do_not_double_claim(self, tmp_path):
        # 진짜 경쟁은 **발행 전**이다 — 한쪽이 claim 을 쥔 동안 다른 쪽이 같은 event 를
        # 집으면 같은 메시지가 두 번 나간다. lease 가 살아 있는 동안은 침범 금지.
        db = FakeMinuteDB()
        enqueue(db, "e1")
        first = build_relay(db, relay_id="relay-1", lease_seconds=150)
        second = build_relay(db, relay_id="relay-2", lease_seconds=150)
        held = first.jobs.claim_outbox_batch(
            relay_id="relay-1", now=NOW, limit=10, lease_seconds=150
        )
        assert [e["event_id"] for e in held] == ["e1"]
        assert second.tick(NOW) == "IDLE"
        assert not second.publisher.sent, "lease 유효 구간에서 두 번째 Relay 가 발행했다"
        # 발행까지 끝난 뒤에도 재발행하지 않는다
        assert first.tick(NOW + timedelta(seconds=151)) == "PUBLISHED"
        assert second.tick(NOW + timedelta(seconds=152)) == "IDLE"

    def test_crash_before_publish_is_reclaimed_after_lease(self, tmp_path):
        # claim 만 하고 죽은 event 는 lease 만료 후 다른 Relay 가 회수한다(유실 0)
        db = FakeMinuteDB()
        enqueue(db, "e1")
        dead = build_relay(db, relay_id="relay-dead", lease_seconds=150)
        dead.jobs.claim_outbox_batch(relay_id="relay-dead", now=NOW, limit=10, lease_seconds=150)
        alive = build_relay(db, relay_id="relay-2")
        assert alive.tick(NOW + timedelta(seconds=100)) == "IDLE"  # lease 유효 — 침범 금지
        assert alive.tick(NOW + timedelta(seconds=151)) == "PUBLISHED"

    def test_crash_after_publish_before_mark_republishes_same_event_id(self, tmp_path):
        # 발행은 나갔는데 DB 기록 전에 죽으면 행은 NEW 로 남아 **다시 발행**된다.
        # 중복은 Consumer 가 event_id 로 흡수한다(v0.7 9절 복구 표) — 유실은 불가.
        class CrashAfterSend(FakePublisher):
            def publish_batch(self, queue_url, messages):
                result = super().publish_batch(queue_url, messages)
                raise RuntimeError("전송 직후 프로세스 종료")

        db = FakeMinuteDB()
        enqueue(db, "e1")
        crashing = CrashAfterSend()
        relay = build_relay(db, publisher=crashing, relay_id="relay-1", lease_seconds=150)
        assert relay.tick(NOW) == "PARTIAL"  # 전송됐는지 알 수 없다 → 재시도 예약
        assert db.outbox["e1"]["status"] == "NEW"

        alive = build_relay(db, relay_id="relay-2")
        # next_attempt_at 이 지난 뒤 다른 Relay 가 같은 event 를 다시 발행한다
        assert alive.tick(NOW + timedelta(seconds=151)) == "PUBLISHED"
        [(_, messages)] = alive.publisher.sent
        assert messages[0].event_id == "e1", "재발행이 같은 event_id 로 나가지 않았다"
        assert json.loads(messages[0].body)["event_id"] == "e1"


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

    def test_lease_must_cover_worst_case_publish_time(self):
        # 한 batch 는 최악의 경우 건수만큼 요청으로 쪼개진다(큰 메시지는 하나씩) —
        # 그 전부를 못 견디는 lease 는 발행 도중 만료돼 경쟁 Relay 가 행을 탈취하고,
        # 양쪽 기록이 서로 거부되는 동안 메시지만 중복 발행된다
        with pytest.raises(ValueError, match="lease_seconds"):
            RelayConfig(relay_id="r", queue_urls=QUEUES, batch_limit=10, lease_seconds=60)
        # 건수를 줄이면 짧은 lease 도 안전하다
        RelayConfig(relay_id="r", queue_urls=QUEUES, batch_limit=2, lease_seconds=30)

    def test_shared_queue_url_refuses_to_start(self):
        # 세 destination 이 한 큐를 가리키면 다른 큐의 Consumer 가 wake-up 을 못 받는데
        # event 는 PUBLISHED 로 확정돼 URL 을 고쳐도 되살아나지 않는다(v0.7 12.1)
        same = dict.fromkeys(QUEUES, "https://sqs/one")
        with pytest.raises(ValueError, match="같은 큐"):
            RelayConfig(relay_id="r", queue_urls=same)

    def test_backoff_cap_is_respected(self, tmp_path):
        db = FakeMinuteDB()
        enqueue(db, "e1")
        relay = build_relay(db, publisher=FakePublisher(failures=[PublishFailure("e1", "503")]),
                            retry_base_seconds=2, retry_max_seconds=5)
        for attempt in range(4):
            relay.tick(NOW + timedelta(hours=attempt))
        capped = (
            db.outbox["e1"]["next_attempt_at"] - (NOW + timedelta(hours=3))
        ).total_seconds()
        assert 5 <= capped < 6, f"cap(5초)을 넘겼다: {capped}"


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
            return {"Successful": [
                {"Id": e["Id"], "MessageId": f"m-{e['Id']}",
                 # 실제 SQS 는 받은 본문의 MD5 를 돌려준다 — 아무 값이나 넣으면
                 # 무결성 게이트가 검증되지 않는다(Rule 9)
                 "MD5OfMessageBody": md5(e["MessageBody"].encode()).hexdigest()}
                for e in kwargs["Entries"]
            ]}

    def _messages(self, count, body="{}"):
        return tuple(OutboxMessage(f"e{i}", body) for i in range(count))

    def test_partial_batch_failure_maps_to_right_events(self):
        body_md5 = md5(b"{}").hexdigest()
        stub = self.StubSqs([{
            "Successful": [
                {"Id": "0", "MessageId": "m0", "MD5OfMessageBody": body_md5},
                {"Id": "2", "MessageId": "m2", "MD5OfMessageBody": body_md5},
            ],
            "Failed": [{"Id": "1", "Code": "InternalError", "Message": "boom"}],
        }])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(3))
        assert published == frozenset({"e0", "e2"})
        assert [(f.event_id, f.terminal) for f in failures] == [("e1", False)]

    def test_sender_fault_alone_is_not_terminal(self):
        # ⚠️ SenderFault 는 "호출자 측 오류"일 뿐이다. 잘못된 큐 URL·권한 오류도 여기
        # 해당하는데 그건 배포로 고쳐지는 설정 문제지 event 의 결함이 아니다 —
        # DEAD 로 확정하면 URL 오타 하나가 레인 전체를 되돌릴 수 없게 만든다.
        stub = self.StubSqs([{
            "Failed": [{"Id": "0", "Code": "AWS.SimpleQueueService.NonExistentQueue",
                        "Message": "no queue", "SenderFault": True}],
        }])
        _, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert failures[0].terminal is False, "설정으로 고쳐질 실패를 영구 폐기했다"

    def test_message_defect_code_is_terminal(self):
        # 본문 자체가 SQS 규격을 어긴 경우만 재시도해도 결과가 같다
        stub = self.StubSqs([{
            "Failed": [{"Id": "0", "Code": "InvalidMessageContents",
                        "Message": "bad chars", "SenderFault": True}],
        }])
        _, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert failures[0].terminal is True

    @pytest.mark.parametrize("bogus_id", ["-1", "00", " 0", "9", "", "abc"])
    def test_unknown_id_never_marks_another_event_published(self, bogus_id):
        # ⚠️ int() 강제였다면 "-1" 이 chunk[-1](마지막 event)을 PUBLISHED 로 확정해
        # 그 event 가 재발행 대상에서 빠진 채 조용히 유실된다
        stub = self.StubSqs([{"Successful": [{"Id": bogus_id, "MessageId": "m?"}]}])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(3))
        assert published == frozenset(), f"보내지 않은 Id({bogus_id!r})가 성공 처리됐다"
        assert failures == ()

    def test_contradictory_id_is_left_unreported(self):
        # ⚠️ 같은 Id 가 성공·실패 양쪽에 오면 발행 여부를 **알 수 없다**. 성공으로 접으면
        # 행이 NEW 에서 빠져 재평가되지 않는다 — 미보고로 둬야 Relay 가 재시도한다.
        stub = self.StubSqs([{
            "Successful": [{"Id": "0", "MessageId": "m0"}],
            "Failed": [{"Id": "0", "Code": "InternalError", "Message": "boom"}],
        }])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert published == frozenset(), "모순 응답을 성공으로 확정했다"
        assert failures == ()

    def test_body_md5_mismatch_is_not_published(self):
        # ⚠️ 큐가 받은 본문이 보낸 것과 다르면 발행됐다고 할 수 없다. 확정하면 그 행은
        # 다시 claim 되지 않아 손상된 채 영구화된다 — 무결성은 **우리가** 본다.
        stub = self.StubSqs([{"Successful": [
            {"Id": "0", "MessageId": "m0", "MD5OfMessageBody": md5("다른 본문".encode()).hexdigest()}
        ]}])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert published == frozenset() and failures == ()

    def test_failed_entry_without_code_is_not_terminal(self):
        # Code 없는 실패 항목은 무엇이 왜 실패했는지 모른다 — SenderFault 하나로
        # 비가역 DEAD 를 확정하지 않는다(다음 응답이 제대로 판정한다)
        stub = self.StubSqs([{"Failed": [{"Id": "0", "SenderFault": True}]}])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert published == frozenset() and failures == ()

    @pytest.mark.parametrize("message_id", [None, "", "   ", True, 1])
    def test_successful_without_valid_message_id_is_not_published(self, message_id):
        # 큐가 받았다는 근거(MessageId)가 없는 성공 항목 — 확정하면 유실이 영구화된다.
        # MD5 는 **올바르게** 준다: 그래야 MessageId 게이트만 단독으로 검증된다(Rule 9).
        entry = {"Id": "0", "MD5OfMessageBody": md5(b"{}").hexdigest()}
        if message_id is not None:
            entry["MessageId"] = message_id
        stub = self.StubSqs([{"Successful": [entry]}])
        published, failures = SqsPublisher(client=stub).publish_batch("q", self._messages(1))
        assert published == frozenset(), f"MessageId={message_id!r} 를 발행 근거로 받았다"
        assert failures == ()

    def test_missing_entry_is_left_unreported(self):
        # 응답에 아예 없는 event 는 성공도 실패도 아니다 — Relay 가 "미보고"로 재시도한다
        stub = self.StubSqs([{"Successful": [
            {"Id": "0", "MessageId": "m0", "MD5OfMessageBody": md5(b"{}").hexdigest()}
        ]}])
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

    def test_bounded_mode_counts_delayed_backlog(self, monkeypatch):
        # ⚠️ IDLE 은 "지금 집을 게 없다"일 뿐이다 — 재시도 대기(next_attempt_at 미래) 행은
        # claim 에 안 잡힌다. 그걸 완료로 읽으면 남은 event 를 두고 성공으로 끝난다.
        db = FakeMinuteDB()
        enqueue(db, "e1")
        db.outbox["e1"]["next_attempt_at"] = NOW + timedelta(hours=1)  # 재시도 대기
        settings = SimpleNamespace(
            db=_DB,
            minute_relay=SimpleNamespace(
                queue_urls=QUEUES, batch_limit=10, lease_seconds=150,
                retry_base_seconds=2, retry_max_seconds=300, tick_seconds=0.0,
            ),
        )
        db_ = db
        monkeypatch.setattr("data_pipeline.minute.relay.JobLedger",
                            lambda db: JobLedger(db=_DB, connect_fn=db_.connect))
        monkeypatch.setattr("data_pipeline.minute.relay.SqsPublisher", lambda: FakePublisher())
        assert relay_cli(settings, max_ticks=1) == 1, "대기 중 backlog 를 완료로 보고했다"

    def test_bounded_mode_sigterm_is_not_success(self, monkeypatch):
        # 일회성 배출 중 SIGTERM 이면 "비운 걸 확인"하지 못한 채 끝난 것이다 —
        # 상주 Relay 가 없으면 남은 event 는 계속 미발행이므로 성공으로 보고하지 않는다
        db = FakeMinuteDB()
        enqueue(db, "e1")
        settings = SimpleNamespace(
            db=_DB,
            minute_relay=SimpleNamespace(
                queue_urls=QUEUES, batch_limit=10, lease_seconds=150,
                retry_base_seconds=2, retry_max_seconds=300, tick_seconds=0.0,
            ),
        )
        stopper = FakePublisher()
        monkeypatch.setattr("data_pipeline.minute.relay.JobLedger",
                            lambda db: JobLedger(db=_DB, connect_fn=db_.connect))
        db_ = db
        monkeypatch.setattr("data_pipeline.minute.relay.SqsPublisher", lambda: stopper)
        original = OutboxRelay.tick
        monkeypatch.setattr(OutboxRelay, "tick",
                            lambda self, now: "STOPPED")
        assert relay_cli(settings, max_ticks=5) == 1
        monkeypatch.setattr(OutboxRelay, "tick", original)

    def test_missing_db_config_fails_loud(self):
        with pytest.raises(SystemExit, match="db 설정 없음"):
            relay_cli(self._settings(minute_relay=object()))

    def test_bounded_mode_signals_remaining_backlog(self, monkeypatch):
        # ⚠️ 상한에 걸려 backlog 를 남긴 채 끝났는데 exit 0 이면, 일회성 배출 게이트가
        # "다 나갔다"로 오독한다(남은 wake-up 은 상주 Relay 가 없으면 발행되지 않는다)
        db = FakeMinuteDB()
        for index in range(25):
            enqueue(db, f"e{index}")
        settings = SimpleNamespace(
            db=_DB,
            minute_relay=SimpleNamespace(
                queue_urls=QUEUES, batch_limit=10, lease_seconds=150,
                retry_base_seconds=2, retry_max_seconds=300, tick_seconds=0.0,
            ),
        )
        monkeypatch.setattr(
            "data_pipeline.minute.relay.JobLedger",
            lambda db: JobLedger(db=_DB, connect_fn=db_.connect),
        )
        db_ = db
        monkeypatch.setattr(
            "data_pipeline.minute.relay.SqsPublisher",
            lambda: FakePublisher(),
        )
        assert relay_cli(settings, max_ticks=1) == 1  # 15건 남았다
        assert relay_cli(settings, max_ticks=5) == 0  # 미발행 0건으로 확인됨
        assert {r["status"] for r in db.outbox.values()} == {"PUBLISHED"}

    def test_missing_queue_mapping_fails_loud(self):
        # 큐 매핑 없이 뜨면 **모든** event 가 미정의 destination 으로 DEAD 된다 —
        # 조용히 기동시키지 않는다
        with pytest.raises(SystemExit, match="minute_relay 설정 없음"):
            relay_cli(self._settings(db=_DB))
