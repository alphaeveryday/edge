"""DB transport(`DbQueue`) 테스트 — SQS 없이 outbox 행을 메시지로 쓰는 경로 (OCI 이전).

의도: SQS 가 대신 져 주던 것이 사라진다. 셋을 붙잡는다.

1. **끝이 있어야 한다.** maxReceiveCount·DLQ 가 없으므로 실패만 하는 job, 기록 전에 죽는
   소비자, 판정할 수 없는 event 가 스스로 DEAD 로 닫히지 않으면 영원히 돈다. 횟수는
   **claim 시점**에 올라야 한다 — 실패를 기록할 때만 세면 기록 전에 죽는 반복이 안 잡힌다.
2. **한 행은 한 번만 집힌다.** 소비자끼리도, Relay 와도.
3. **깨울 근거는 event 다.** destination 이 realtime/backfill 을 가르고, event 없는 job
   (과거일 세션, ALPHA-863)은 집히지 않는다.

실제 JobLedger·MinuteConsumer 를 FakeMinuteDB(SQL 매칭 fake) 위에서 돌린다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.consumer import (
    DB_MAX_RECEIVES,
    ConsumerConfig,
    DbQueue,
    MinuteConsumer,
    SqsQueue,
    TransientJobError,
    make_queue,
)
from data_pipeline.minute.jobs import (
    NEWS_EVENT_TYPE,
    JobLedger,
    build_event_id,
    price_job_id,
)
from data_pipeline.minute.models import KST
from data_pipeline.minute.relay import relay_cli

_DB = DbConfig(password="x")
START = datetime(2026, 12, 1, 9, 5, tzinfo=KST)
REALTIME = "news-extraction-realtime"
BACKFILL = "news-extraction-backfill"
CHECKSUM = "c" * 64
MAX_ATTEMPTS = 3
VISIBILITY = 300
LEASE = 600

NEWS_IDENTITY = dict(
    source_code="bigkinds", input_fingerprint="f" * 64,
    tagger_version="v4-pro", ontology_version="onto-7",
)


class Clock:
    """DbQueue 의 벽시계와 tick 의 now 를 함께 움직인다."""

    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Crash(BaseException):
    """kernel 이 잡지 못하는 종료(OOM kill·SIGKILL) — 실패를 **기록하지 못한 채** 죽는다."""


@pytest.fixture
def env():
    db, clock = FakeMinuteDB(), Clock()
    ledger = JobLedger(db=_DB, connect_fn=db.connect)
    sleeps = []

    def queue(consumer_id="consumer-1"):
        return DbQueue(jobs=ledger, consumer_id=consumer_id, clock=clock, sleep=sleeps.append)

    def consumer(handler, *, consumer_id="consumer-1", queue_url=REALTIME):
        return MinuteConsumer(
            jobs=ledger, queue=queue(consumer_id), handler=handler,
            config=ConsumerConfig(
                consumer_id=consumer_id, kind="news", queue_url=queue_url, batch_size=10,
                wait_seconds=0, visibility_seconds=VISIBILITY, heartbeat_seconds=30,
                max_concurrency=1, lease_seconds=LEASE, retry_base_seconds=5,
                retry_max_seconds=900, max_attempts=MAX_ATTEMPTS,
            ),
        )

    def enqueue(article="a", destination=REALTIME):
        job_id, _ = ledger.enqueue_news_job(
            destination=destination, payload={"article_id": article * 64},
            article_id=article * 64, **NEWS_IDENTITY,
        )
        return job_id, build_event_id(NEWS_EVENT_TYPE, job_id)

    return SimpleNamespace(db=db, clock=clock, ledger=ledger, sleeps=sleeps,
                           queue=queue, consumer=consumer, enqueue=enqueue)


def recording(calls, error=None):
    def handler(*, job_id, attempt, **_):
        calls.append(attempt)
        if error is not None:
            raise error
        return CHECKSUM
    return handler


def receive(queue, destination=REALTIME, limit=10):
    return queue.receive(queue_url=destination, max_messages=limit, wait_seconds=0,
                         visibility_seconds=VISIBILITY)


class TestDelivery:
    def test_success_closes_job_and_event(self, env):
        job_id, event_id = env.enqueue()
        calls = []
        consumer = env.consumer(recording(calls))

        assert consumer.tick(env.clock.now)["succeeded"] == 1

        assert env.db.jobs[("news", job_id)]["status"] == "SUCCEEDED"
        assert env.db.outbox[event_id]["status"] == "PUBLISHED"
        # 닫힌 event 는 다시 오지 않는다 — 오면 끝난 job 을 매 폴링마다 다시 판정한다
        assert consumer.tick(env.clock.now) == {"idle": 1}
        assert calls == [1]

    def test_empty_queue_sleeps_instead_of_spinning(self, env):
        """CLI 루프는 tick 사이에 쉬지 않는다(SQS 는 long polling 이 쉬어 줬다) — 빈 큐에서
        receive 가 쉬지 않으면 소비자가 DB 를 쉼 없이 두드린다."""
        consumer = env.consumer(recording([]))
        consumer.tick(env.clock.now)
        assert env.sleeps == [1.0]

    def test_retry_waits_for_the_time_the_db_set(self, env):
        """재시도 시각의 권위는 DB 다 — 그 전의 폴링이 실행하면 backoff 가 폴링 간격(1초)이 된다."""
        job_id, _ = env.enqueue()
        calls = []
        consumer = env.consumer(recording(calls, TransientJobError("vendor 503")))
        assert consumer.tick(env.clock.now)["retried"] == 1
        wait = (env.db.jobs[("news", job_id)]["next_attempt_at"] - env.clock.now).total_seconds()

        env.clock.advance(wait - 1)
        assert consumer.tick(env.clock.now) == {"idle": 1}
        env.clock.advance(2)
        assert consumer.tick(env.clock.now)["retried"] == 1
        assert calls == [1, 2]


class TestRetryCeiling:
    def test_always_failing_job_ends_dead_after_max_attempts(self, env):
        """SQS 가 없으면 배달은 끝없이 돌아온다 — DB 예산이 닫지 않으면 같은 LLM 호출을
        영원히 반복한다."""
        job_id, event_id = env.enqueue()
        calls = []
        consumer = env.consumer(recording(calls, TransientJobError("vendor 503")))

        outcomes = []
        for _ in range(MAX_ATTEMPTS + 3):
            outcomes.append(dict(consumer.tick(env.clock.now)))
            env.clock.advance(1000)   # 어떤 backoff 보다도 길게

        job = env.db.jobs[("news", job_id)]
        assert (job["status"], job["error_code"]) == ("DEAD", "RETRY_BUDGET_EXHAUSTED")
        assert calls == list(range(1, MAX_ATTEMPTS + 1)), "예산만큼만 실행한다"
        # event 도 닫혀야 한다 — 열려 있으면 DEAD job 을 매 폴링마다 다시 본다
        assert env.db.outbox[event_id]["status"] == "PUBLISHED"
        assert outcomes[MAX_ATTEMPTS:] == [{"idle": 1}] * 3

    def test_crash_before_recording_is_counted_at_claim(self, env):
        """실패를 기록하기 전에 죽는 소비자(OOM 등) — 횟수를 실패 기록 때 세면 0 에 머물러
        handler 가 영원히 불린다. claim 이 세므로 예산을 넘긴 claim 은 실행 없이 DEAD 다."""
        job_id, event_id = env.enqueue()
        calls = []
        consumer = env.consumer(recording(calls, Crash()))

        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(Crash):
                consumer.tick(env.clock.now)
            # 죽은 실행은 아무것도 못 남겼다 — lease 만료가 유일한 회수 경로다
            assert env.db.jobs[("news", job_id)]["status"] == "CLAIMED"
            env.clock.advance(LEASE + 1)
        assert consumer.tick(env.clock.now)["dead"] == 1

        job = env.db.jobs[("news", job_id)]
        assert (job["status"], job["error_code"]) == ("DEAD", "RETRY_BUDGET_EXHAUSTED")
        assert len(calls) == MAX_ATTEMPTS, "예산을 넘긴 claim 은 handler 를 부르지 않는다"
        assert env.db.outbox[event_id]["status"] == "PUBLISHED"

    def test_dead_job_can_be_redriven(self, env):
        """DB 가 닫은 DEAD 도 되돌릴 수 있어야 한다 — 새 event 가 다시 배달된다."""
        job_id, _ = env.enqueue()
        failing = env.consumer(recording([], TransientJobError("x")))
        for _ in range(MAX_ATTEMPTS):
            failing.tick(env.clock.now)
            env.clock.advance(1000)
        assert env.db.jobs[("news", job_id)]["status"] == "DEAD"

        env.ledger.redrive_job(kind="news", job_id=job_id, now=env.clock.now,
                               actor="op", reason="vendor 복구")
        assert env.consumer(recording([])).tick(env.clock.now)["succeeded"] == 1
        assert env.db.jobs[("news", job_id)]["status"] == "SUCCEEDED"

    def test_undecidable_event_ends_dead_at_receive_ceiling(self, env):
        """kernel 은 판정할 수 없는 메시지를 지우지 않는다(근거 보존 — SQS 에선 DLQ 가 받았다).
        DB 에는 받아 줄 DLQ 가 없어, 상한이 없으면 visibility 마다 영원히 돌아온다."""
        env.ledger.insert_outbox_event(   # job 행이 없는 event = orphan
            event_id=build_event_id(NEWS_EVENT_TYPE, "0" * 64), event_type=NEWS_EVENT_TYPE,
            destination=REALTIME, aggregate_id="0" * 64, generation=1, payload={},
        )
        event = env.db.outbox[build_event_id(NEWS_EVENT_TYPE, "0" * 64)]
        consumer = env.consumer(recording([]))

        for _ in range(DB_MAX_RECEIVES):
            assert consumer.tick(env.clock.now)["orphan"] == 1
            assert event["status"] == "NEW", "상한 전에는 근거를 남겨 둔다"
            env.clock.advance(VISIBILITY + 1)
        consumer.tick(env.clock.now)

        assert event["status"] == "DEAD"
        assert event["last_error"] == "DB_MAX_RECEIVE: orphan"
        env.clock.advance(VISIBILITY + 1)
        assert consumer.tick(env.clock.now) == {"idle": 1}

    def test_receive_ceiling_closes_a_live_job_with_its_own_reason(self, env):
        """배달만 소진되고 job 은 살아 있는 경우(SQS 의 DLQ 도착 + 대사) — job 을 DEAD 로
        닫되 사유를 예산 소진과 구분한다. 닫지 않으면 깨울 event 없는 job 이 남는다."""
        job_id, event_id = env.enqueue()
        env.db.outbox[event_id]["attempt_count"] = DB_MAX_RECEIVES
        calls = []

        assert env.consumer(recording(calls)).tick(env.clock.now)["terminal"] == 1

        job = env.db.jobs[("news", job_id)]
        assert (job["status"], job["error_code"]) == ("DEAD", "DB_MAX_RECEIVE")
        assert calls == []
        assert env.db.outbox[event_id]["status"] == "PUBLISHED"


class TestExclusiveClaim:
    def test_two_consumers_never_hold_the_same_event(self, env):
        """둘이 같은 행을 집으면 같은 기사에 LLM 을 두 번 부른다."""
        _, first_event = env.enqueue("a")
        _, second_event = env.enqueue("b")
        one, two = env.queue("consumer-1"), env.queue("consumer-2")

        got_one = receive(one, limit=1)
        got_two = receive(two, limit=1)

        assert [m.message_id for m in got_one] == [first_event]
        assert [m.message_id for m in got_two] == [second_event]
        assert receive(one) == () and receive(two) == ()

    def test_a_job_runs_once_across_two_consumers(self, env):
        job_id, _ = env.enqueue()
        calls = []
        for consumer_id in ("consumer-1", "consumer-2"):
            env.consumer(recording(calls), consumer_id=consumer_id).tick(env.clock.now)
        assert calls == [1]
        assert env.db.jobs[("news", job_id)]["status"] == "SUCCEEDED"

    def test_expired_holder_cannot_close_the_next_holders_claim(self, env):
        """visibility 가 지나 남이 다시 집은 뒤의 늦은 ack — 닫히면 지금 쥔 쪽의 배달이
        사라지고, 그쪽이 재시도로 남긴 job 은 깨울 event 가 없다."""
        _, event_id = env.enqueue()
        one, two = env.queue("consumer-1"), env.queue("consumer-2")
        stale = receive(one)[0]
        env.clock.advance(VISIBILITY + 1)
        current = receive(two)[0]

        one.delete(queue_url=REALTIME, receipt_handle=stale.receipt_handle)
        assert env.db.outbox[event_id]["status"] == "NEW"
        two.delete(queue_url=REALTIME, receipt_handle=current.receipt_handle)
        assert env.db.outbox[event_id]["status"] == "PUBLISHED"

    def test_relay_and_consumer_never_hold_the_same_event(self, env):
        """Relay 가 함께 떠 있어도(컷오버 실수) 한 행을 둘이 쥐지 않는다."""
        env.enqueue("a")
        relay_rows = env.ledger.claim_outbox_batch(
            relay_id="relay-1", now=env.clock.now, limit=10, lease_seconds=150,
            destination=REALTIME,
        )
        assert len(relay_rows) == 1
        assert receive(env.queue()) == ()

        _, second_event = env.enqueue("b")
        assert [m.message_id for m in receive(env.queue())] == [second_event]
        assert env.ledger.claim_outbox_batch(
            relay_id="relay-1", now=env.clock.now + timedelta(seconds=1), limit=10,
            lease_seconds=150, destination=REALTIME,
        ) == []

    def test_relay_refuses_to_start_in_db_mode(self):
        """쥐는 것은 배타라도 Relay 가 먼저 집은 행은 SQS 로 나가 PUBLISHED 가 된다 —
        db 모드의 소비자는 그 행을 영영 못 본다. 그래서 뜨는 것 자체를 막는다."""
        settings = SimpleNamespace(db=_DB, minute_relay=None, minute_queue_transport="db")
        with pytest.raises(SystemExit, match="minute_queue_transport=db"):
            relay_cli(settings, max_ticks=1)


class TestRouting:
    def test_destination_keeps_backfill_out_of_realtime(self, env):
        """realtime/backfill 은 outbox destination 이 가른다 — 섞이면 밤 backlog 가 장중
        실시간 태깅 앞에 선다."""
        _, backfill_event = env.enqueue("a", destination=BACKFILL)
        assert receive(env.queue(), REALTIME) == ()
        assert [m.message_id for m in receive(env.queue(), BACKFILL)] == [backfill_event]

    def test_job_without_event_is_never_picked_up(self, env):
        """과거일 세션의 가격 job 은 event 없이 행만 남는다(ALPHA-863). job 을 직접 집는
        폴링이면 지난 날짜의 가격 변동이 실시간 설명으로 나간다."""
        window = datetime(2026, 11, 20, 9, 0, tzinfo=KST)
        env.ledger.insert_price_job(
            session_id="msn_backfill", window_start=window, generation=1,
            trigger_schema_version="trig-1", delivery_expected=False,
        )
        job_id = price_job_id(session_id="msn_backfill", window_start=window,
                              generation=1, trigger_schema_version="trig-1")
        assert env.db.jobs[("price", job_id)]["status"] == "PENDING"

        assert receive(env.queue(), "price-analysis-realtime") == ()
        assert env.db.jobs[("price", job_id)]["status"] == "PENDING"


class TestModeSelection:
    OPTIONS = SimpleNamespace(queue_url=REALTIME, wait_seconds=20)

    def _make(self, settings, options=OPTIONS):
        return make_queue(settings, options, jobs=object(), kind="news", consumer_id="c")

    def test_default_is_sqs(self):
        """필드가 없거나 sqs 면 지금까지와 같은 큐다 — AWS 배포가 그대로 돌아야 한다."""
        assert isinstance(self._make(SimpleNamespace()), SqsQueue)
        assert isinstance(self._make(SimpleNamespace(minute_queue_transport="sqs")), SqsQueue)

    def test_db_mode_uses_the_outbox(self):
        assert isinstance(self._make(SimpleNamespace(minute_queue_transport="db")), DbQueue)

    def test_db_mode_rejects_a_queue_url_left_over_from_sqs(self):
        """SQS URL 이 남은 채 db 로 바꾸면 그 이름의 행이 없어 조용히 idle 로만 돈다."""
        options = SimpleNamespace(queue_url="https://sqs.test/news", wait_seconds=20)
        with pytest.raises(SystemExit, match="destination"):
            self._make(SimpleNamespace(minute_queue_transport="db"), options)
        # 남의 레인 destination 도 같다 — 가격 큐를 뉴스 소비자가 보면 전건 misrouted 다
        options = SimpleNamespace(queue_url="price-analysis-realtime", wait_seconds=20)
        with pytest.raises(SystemExit, match="destination"):
            self._make(SimpleNamespace(minute_queue_transport="db"), options)
