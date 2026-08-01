"""Consumer kernel·DLQ reconciler·redrive 테스트 (ALPHA-672, 계획 §12 PR 7A).

의도: 이 계약이 깨지면 **중복 LLM 호출·이른 재시도·되돌릴 수 없는 유실**이 조용히
일어난다. 세 축을 특히 붙잡는다.

1. 재시도 권위는 DB 뿐이다 — SQS 배달이 아무리 와도 `next_attempt_at` 전엔 실행 0.
2. ack(삭제)는 되돌릴 수 없다 — 판정 가능한 경우만 지운다(파싱 실패·배선 오류·행 없음은
   남긴다). 남기면 DLQ 가 근거를 보존하고, 지우면 그 자리에서 사라진다.
3. DEAD 는 근거가 그 job 자체의 성질일 때만이다 — 미분류 예외는 재시도로 보낸다.

실제 JobLedger 를 FakeMinuteDB(SQL 매칭 fake) 위에서 돌려 SQL 경로를 그대로 태운다.
SQS 는 fake — vendor·AWS 실호출은 이 트랙의 경계다(계획 §2).
"""

from __future__ import annotations

import itertools
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.consumer import (
    ConsumerConfig,
    ConsumerMessage,
    DlqReconciler,
    MinuteConsumer,
    PermanentJobError,
    TransientJobError,
    _resolve_queue_urls,
    parse_delivery,
)
from data_pipeline.minute.jobs import (
    NEWS_EVENT_TYPE,
    PRICE_EVENT_TYPE,
    JobLedger,
    build_event_id,
)
from data_pipeline.minute.models import KST
from data_pipeline.minute.relay import build_message_body

_DB = DbConfig(password="x")
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)
WINDOW_START = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
QUEUE = "https://sqs.test/news-extraction-realtime"
DLQ = "https://sqs.test/news-extraction-realtime-dlq"

NEWS_IDENTITY = dict(
    source_code="bigkinds",
    article_id="a" * 64,
    input_fingerprint="f" * 64,
    tagger_version="v4-pro",
    ontology_version="onto-7",
)


class FakeSqs:
    """큐 하나당 메시지 리스트. receive 는 꺼내 가고(in-flight) delete 만 확정한다."""

    def __init__(self):
        self.queues: dict[str, list[ConsumerMessage]] = {}
        self.deleted: list[str] = []
        self.visibility_changes: list[tuple[str, int]] = []
        self.receive_calls: list[dict] = []
        self._ids = itertools.count(1)

    def send(self, queue_url: str, body: str) -> ConsumerMessage:
        index = next(self._ids)
        message = ConsumerMessage(
            message_id=f"m{index}", receipt_handle=f"r{index}", body=body
        )
        self.queues.setdefault(queue_url, []).append(message)
        return message

    def redeliver(self, queue_url: str, message: ConsumerMessage) -> None:
        """visibility 만료 후 같은 메시지가 다시 오는 상황."""
        self.queues.setdefault(queue_url, []).append(message)

    def receive(self, *, queue_url, max_messages, wait_seconds, visibility_seconds):
        self.receive_calls.append({
            "queue_url": queue_url, "max_messages": max_messages,
            "wait_seconds": wait_seconds, "visibility_seconds": visibility_seconds,
        })
        queued = self.queues.setdefault(queue_url, [])
        taken, self.queues[queue_url] = queued[:max_messages], queued[max_messages:]
        return tuple(taken)

    def delete(self, *, queue_url, receipt_handle):
        self.deleted.append(receipt_handle)

    def change_visibility(self, *, queue_url, receipt_handle, seconds):
        self.visibility_changes.append((receipt_handle, seconds))


def make_config(**overrides) -> ConsumerConfig:
    args = dict(
        consumer_id="consumer-1", kind="news", queue_url=QUEUE, batch_size=10,
        wait_seconds=0, visibility_seconds=60, heartbeat_seconds=30,
        max_concurrency=2, lease_seconds=60, retry_base_seconds=5,
        retry_max_seconds=900, max_attempts=3,
    )
    args.update(overrides)
    return ConsumerConfig(**args)


def enqueue_news(ledger, db, *, article_id=None, payload=None) -> tuple[str, str]:
    """job + outbox event 를 실제 경로로 만들고 (job_id, 메시지 본문)을 돌려준다."""
    identity = dict(NEWS_IDENTITY)
    if article_id is not None:
        identity["article_id"] = article_id
    job_id, _ = ledger.enqueue_news_job(
        destination="news-extraction-realtime",
        payload=payload if payload is not None else {"article_id": identity["article_id"]},
        **identity,
    )
    event = db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id)]
    return job_id, build_message_body(event)


def make_consumer(db, sqs, handler, **config_overrides):
    return MinuteConsumer(
        jobs=JobLedger(db=_DB, connect_fn=db.connect), queue=sqs, handler=handler,
        config=make_config(**config_overrides),
    )


def recording_handler(calls, *, result="checksum-1"):
    def handler(*, job_id, payload, attempt):
        calls.append({"job_id": job_id, "payload": payload, "attempt": attempt})
        return result
    return handler


class TestParseDelivery:
    def test_roundtrip_from_relay_envelope(self):
        db = FakeMinuteDB()
        job_id, body = enqueue_news(JobLedger(db=_DB, connect_fn=db.connect), db)
        delivery = parse_delivery(body)
        assert (delivery.kind, delivery.job_id, delivery.redrive_generation) == (
            "news", job_id, 0
        )
        assert delivery.payload == {"article_id": NEWS_IDENTITY["article_id"]}

    @pytest.mark.parametrize(
        "body",
        [
            "not json",
            '["array"]',
            '{"event_id": "x", "event_type": "NewsExtractionRequested"}',  # payload 누락
            # 미지 키 — envelope 계약이 바뀐 걸 조용히 흘리면 소비자마다 다른 걸 본다
            '{"event_id": "NewsExtractionRequested:j:0", "event_type": "NewsExtractionRequested",'
            ' "payload": {}, "extra": 1}',
            # payload 가 객체가 아니다
            '{"event_id": "NewsExtractionRequested:j:0", "event_type": "NewsExtractionRequested",'
            ' "payload": "text"}',
            # 정의되지 않은 event_type
            '{"event_id": "Whatever:j:0", "event_type": "Whatever", "payload": {}}',
            # event_id 접두가 event_type 과 다르다 — 다른 사건의 ID 를 실어 나른 것
            '{"event_id": "PriceWindowCommitted:j:0", "event_type": "NewsExtractionRequested",'
            ' "payload": {}}',
            # 비정규 세대 표기 — 같은 job 이 두 event_id 로 갈린다
            '{"event_id": "NewsExtractionRequested:j:007", "event_type": "NewsExtractionRequested",'
            ' "payload": {}}',
            # 음수·공백 세대
            '{"event_id": "NewsExtractionRequested:j:-1", "event_type": "NewsExtractionRequested",'
            ' "payload": {}}',
            # 구분자 개수가 다르다
            '{"event_id": "NewsExtractionRequested:j", "event_type": "NewsExtractionRequested",'
            ' "payload": {}}',
        ],
    )
    def test_contract_violations_raise(self, body):
        with pytest.raises(ValueError):
            parse_delivery(body)


class TestConsumerConfig:
    def test_heartbeat_must_fit_inside_visibility(self):
        # 만료 전에 연장이 한 번도 못 돌면 heartbeat 가 있으나 마나다 — 긴 job 의
        # 메시지가 재배달돼 같은 LLM 호출이 중복된다
        with pytest.raises(ValueError, match="heartbeat_seconds"):
            make_config(visibility_seconds=60, heartbeat_seconds=40)

    def test_lease_must_outlive_one_delivery(self):
        with pytest.raises(ValueError, match="lease_seconds"):
            make_config(visibility_seconds=60, lease_seconds=30)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"kind": "etf"},
            {"batch_size": 11},
            {"wait_seconds": 21},
            {"visibility_seconds": 43_201},
            {"max_concurrency": 0},
            {"max_attempts": 0},
            {"retry_base_seconds": 900, "retry_max_seconds": 5},
        ],
    )
    def test_out_of_range_rejected(self, overrides):
        with pytest.raises(ValueError):
            make_config(**overrides)


class TestStatusGate:
    def test_success_records_and_acks(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        message = sqs.send(QUEUE, body)
        calls = []
        consumer = make_consumer(db, sqs, recording_handler(calls))

        counts = consumer.tick(NOW)

        assert counts["succeeded"] == 1
        assert calls == [{"job_id": job_id, "payload": {"article_id": NEWS_IDENTITY["article_id"]},
                          "attempt": 1}]
        row = db.jobs[("news", job_id)]
        assert row["status"] == "SUCCEEDED" and row["result_checksum"] == "checksum-1"
        assert sqs.deleted == [message.receipt_handle]

    def test_duplicate_delivery_runs_once(self):
        # 같은 메시지가 두 번 와도 두 번째는 SUCCEEDED 를 보고 실행 없이 지운다
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        _job_id, body = enqueue_news(ledger, db)
        message = sqs.send(QUEUE, body)
        calls = []
        consumer = make_consumer(db, sqs, recording_handler(calls))
        consumer.tick(NOW)

        sqs.redeliver(QUEUE, message)
        counts = consumer.tick(NOW)

        assert counts["terminal"] == 1
        assert len(calls) == 1
        assert sqs.deleted == [message.receipt_handle, message.receipt_handle]

    def test_dead_job_is_acked_without_running(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)]["status"] = "DEAD"
        sqs.send(QUEUE, body)
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["terminal"] == 1 and calls == []

    def test_retry_wait_before_next_attempt_does_not_run(self):
        # **DB 가 정한 시각**이 권위다 — 배달이 왔다고 실행하면 재시도 간격이 SQS
        # 배달 주기로 바뀐다. visibility 만 남은 시간으로 민다.
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="RETRY_WAIT", next_attempt_at=NOW + timedelta(seconds=120)
        )
        message = sqs.send(QUEUE, body)
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["deferred"] == 1 and calls == []
        assert sqs.visibility_changes == [(message.receipt_handle, 120)]
        assert sqs.deleted == []   # 아직 할 일이 남았다 — 지우면 유실이다

    def test_missing_job_row_is_not_deleted(self):
        # job 과 event 는 같은 트랜잭션에서 쓰인다(2C) — 없다는 건 계약이 깨졌다는
        # 뜻이고, 지우면 근거가 사라진다
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        del db.jobs[("news", job_id)]
        sqs.send(QUEUE, body)

        counts = make_consumer(db, sqs, recording_handler([])).tick(NOW)

        assert counts["orphan"] == 1 and sqs.deleted == []

    def test_poison_message_is_left_for_dlq(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        sqs.send(QUEUE, "{not json")

        counts = make_consumer(db, sqs, recording_handler([])).tick(NOW)

        assert counts["poison"] == 1 and sqs.deleted == []

    def test_misrouted_event_type_is_left(self):
        # 가격 사건이 뉴스 큐에 있다 = destination↔큐 배선 오류. 실행도 삭제도 안 한다
        db, sqs = FakeMinuteDB(), FakeSqs()
        sqs.send(QUEUE, build_message_body({
            "event_id": build_event_id(PRICE_EVENT_TYPE, "p" * 64),
            "event_type": PRICE_EVENT_TYPE, "payload": {},
        }))
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["misrouted"] == 1 and calls == [] and sqs.deleted == []


class TestClaimContention:
    def test_live_lease_blocks_second_consumer(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="other", attempt_count=1,
            lease_expires_at=NOW + timedelta(seconds=300),
        )
        sqs.send(QUEUE, body)
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["contended"] == 1 and calls == []
        assert sqs.deleted == []          # 남의 job 의 wake-up 을 지우지 않는다
        assert db.jobs[("news", job_id)]["claimed_by"] == "other"

    def test_expired_lease_is_reclaimed(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="dead-consumer", attempt_count=1,
            lease_expires_at=NOW - timedelta(seconds=1),
        )
        sqs.send(QUEUE, body)
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["succeeded"] == 1
        assert calls[0]["attempt"] == 2   # 이어지는 attempt — 새로 세지 않는다


class TestFailureClassification:
    def _fail_with(self, error, **config_overrides):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        message = sqs.send(QUEUE, body)

        def handler(*, job_id, payload, attempt):
            raise error

        counts = make_consumer(db, sqs, handler, **config_overrides).tick(NOW)
        return db.jobs[("news", job_id)], sqs, counts, message

    def test_transient_sets_db_time_and_matching_visibility(self):
        # DB 시각과 visibility 가 갈리면, 짧으면 헛돌고 길면 재시도가 그만큼 늦는다
        row, sqs, counts, message = self._fail_with(TransientJobError("429", code="RATE_LIMIT"))

        assert counts["retried"] == 1
        assert row["status"] == "RETRY_WAIT" and row["error_code"] == "RATE_LIMIT"
        delay = 5   # retry_base 5 × 2**(attempt 1 - 1)
        assert NOW + timedelta(seconds=delay) <= row["next_attempt_at"] <= (
            NOW + timedelta(seconds=delay + 5)
        )
        assert sqs.visibility_changes == [(message.receipt_handle, delay)]
        assert sqs.deleted == []

    def test_unclassified_exception_retries_not_dead(self):
        # 근거가 job 자체의 성질이라는 보장이 없다 — 첫 회에 terminal 로 확정하면
        # 원인이 사라진 뒤에도 그 job 은 안 돌아온다
        row, _sqs, counts, _message = self._fail_with(RuntimeError("boom"))

        assert counts["retried"] == 1
        assert row["status"] == "RETRY_WAIT" and row["error_code"] == "UNCLASSIFIED"

    def test_permanent_is_dead_and_acked(self):
        row, sqs, counts, message = self._fail_with(
            PermanentJobError("본문 없음", code="EMPTY_BODY")
        )

        assert counts["dead"] == 1
        assert row["status"] == "DEAD" and row["error_code"] == "EMPTY_BODY"
        assert sqs.deleted == [message.receipt_handle]

    def test_budget_exhaustion_is_dead(self):
        # max_attempts=1 이면 첫 실패가 곧 예산 소진
        row, sqs, counts, message = self._fail_with(
            TransientJobError("timeout"), max_attempts=1
        )

        assert counts["dead"] == 1
        assert row["error_code"] == "RETRY_BUDGET_EXHAUSTED"
        assert sqs.deleted == [message.receipt_handle]

    def test_backoff_grows_with_attempt(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)]["attempt_count"] = 2   # 다음 claim 이 3회차

        def handler(*, job_id, payload, attempt):
            raise TransientJobError("5xx")

        sqs.send(QUEUE, body)
        make_consumer(db, sqs, handler, max_attempts=9).tick(NOW)

        # base 5 × 2**(3-1) = 20
        assert sqs.visibility_changes[0][1] == 20

    def test_backoff_capped(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        # 복원·마이그레이션으로 들어온 큰 attempt_count 에서 2**n 이 터지지 않아야 한다
        db.jobs[("news", job_id)]["attempt_count"] = 10_000

        def handler(*, job_id, payload, attempt):
            raise TransientJobError("5xx")

        sqs.send(QUEUE, body)
        make_consumer(db, sqs, handler, max_attempts=100_000).tick(NOW)

        assert sqs.visibility_changes[0][1] == 900   # retry_max

    def test_non_string_result_is_contract_failure(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        sqs.send(QUEUE, body)

        counts = make_consumer(
            db, sqs, lambda *, job_id, payload, attempt: None
        ).tick(NOW)

        assert counts["dead"] == 1
        assert db.jobs[("news", job_id)]["error_code"] == "RESULT_CONTRACT"

    def test_lost_claim_does_not_ack(self):
        # 늦은 보고 — 지금 이 job 은 남의 것이다. 지우면 새 소유자의 wake-up 이 사라진다
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        message = sqs.send(QUEUE, body)

        def steal(*, job_id, payload, attempt):
            db.jobs[("news", job_id)].update(claimed_by="other", attempt_count=99)
            return "checksum-1"

        counts = make_consumer(db, sqs, steal).tick(NOW)

        assert counts["lost"] == 1
        assert message.receipt_handle not in sqs.deleted


class TestHeartbeat:
    def test_extends_visibility_and_lease_while_running(self):
        extended = threading.Event()

        class HeartbeatSqs(FakeSqs):
            def change_visibility(self, **kwargs):
                super().change_visibility(**kwargs)
                extended.set()

        db, sqs = FakeMinuteDB(), HeartbeatSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        message = sqs.send(QUEUE, body)
        leases = []

        def slow(*, job_id, payload, attempt):
            assert extended.wait(10), "heartbeat 가 돌지 않았다"
            leases.append(db.jobs[("news", job_id)]["lease_expires_at"])
            return "checksum-1"

        consumer = make_consumer(
            db, sqs, slow, heartbeat_seconds=1, visibility_seconds=2, lease_seconds=2
        )
        counts = consumer.tick(NOW)

        assert counts["succeeded"] == 1
        # 실행 중 연장이 최소 1회 — visibility 는 설정값 전체로 다시 민다
        assert (message.receipt_handle, 2) in sqs.visibility_changes
        # DB lease 도 **함께** 밀린다. visibility 만 밀면 처리 시간이 lease 를 넘는
        # 순간 그 job 이 다른 Consumer 에게 eligible 로 보여 LLM 호출이 중복된다.
        assert leases[0] > NOW + timedelta(seconds=2)   # claim 당시 lease = now+2


class TestGenerationOrdering:
    def test_stale_generation_message_is_dropped(self):
        # redrive 뒤에 도착한 옛 세대 배달 — 더 높은 세대 event 가 durable 하게
        # 존재하므로 이 메시지는 지운다(안 지우면 매 배달마다 같은 판정을 반복한다)
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)]["redrive_generation"] = 1
        message = sqs.send(QUEUE, body)
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["superseded"] == 1 and calls == []
        assert sqs.deleted == [message.receipt_handle]

    def test_future_generation_message_is_surfaced(self):
        # job 보다 앞선 세대 = 원자 동기화가 깨졌다는 신호. 실행도 삭제도 하지 않는다
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, _body = enqueue_news(ledger, db)
        sqs.send(QUEUE, build_message_body({
            "event_id": build_event_id(NEWS_EVENT_TYPE, job_id, 3),
            "event_type": NEWS_EVENT_TYPE, "payload": {},
        }))
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls)).tick(NOW)

        assert counts["ahead"] == 1 and calls == [] and sqs.deleted == []

    def test_redrive_between_read_and_claim_is_rejected(self):
        # 상태 읽기와 claim 사이에 redrive 가 끼면 옛 세대가 새 세대 job 을 실행하고
        # SUCCEEDED 로 마감한다 — 그러면 redrive 가 통째로 사라진다
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        sqs.send(QUEUE, body)
        consumer = make_consumer(db, sqs, recording_handler([]))
        original_fetch = consumer.jobs.fetch_job

        def fetch_then_redrive(**kwargs):
            state = original_fetch(**kwargs)
            db.jobs[("news", job_id)]["redrive_generation"] = 1
            return state

        consumer.jobs.fetch_job = fetch_then_redrive
        counts = consumer.tick(NOW)

        assert counts["contended"] == 1
        assert db.jobs[("news", job_id)]["status"] == "PENDING"
        assert sqs.deleted == []


class TestPriceStale:
    def test_stale_window_generation_is_dead_and_acked(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        db.windows[("msn_x", WINDOW_START)] = {
            "session_id": "msn_x", "window_start": WINDOW_START, "generation": 2,
        }
        job_id, _ = ledger.enqueue_price_job(
            destination="price-analysis-realtime", payload={"window": "0900"},
            session_id="msn_x", window_start=WINDOW_START, generation=1,
            trigger_schema_version="t1",
        )
        event = db.outbox[build_event_id(PRICE_EVENT_TYPE, job_id)]
        message = sqs.send(QUEUE, build_message_body(event))
        calls = []

        counts = make_consumer(db, sqs, recording_handler(calls), kind="price").tick(NOW)

        assert counts["stale"] == 1 and calls == []
        row = db.jobs[("price", job_id)]
        assert row["status"] == "DEAD" and row["error_code"] == "STALE"
        # correction commit 이 만든 새 세대 job/event 가 대신 돈다 — 이 메시지는 끝났다
        assert sqs.deleted == [message.receipt_handle]


class TestLifecycle:
    def test_stop_blocks_new_receive(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        enqueue_news(ledger, db)
        consumer = make_consumer(db, sqs, recording_handler([]))
        consumer.request_stop()

        counts = consumer.tick(NOW)

        assert counts["stopped"] == 1 and sqs.receive_calls == []

    def test_idle_when_queue_empty(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        assert make_consumer(db, sqs, recording_handler([])).tick(NOW)["idle"] == 1

    def test_long_polling_and_batch_are_passed_through(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        make_consumer(db, sqs, recording_handler([]), wait_seconds=20, batch_size=5).tick(NOW)
        assert sqs.receive_calls[0]["wait_seconds"] == 20
        assert sqs.receive_calls[0]["max_messages"] == 5

    def test_burst_runs_each_job_exactly_once(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_ids = []
        for index in range(100):
            job_id, body = enqueue_news(ledger, db, article_id=f"art-{index:04d}")
            job_ids.append(job_id)
            sqs.send(QUEUE, body)
        calls = []
        consumer = make_consumer(db, sqs, recording_handler(calls), max_concurrency=4)

        while consumer.tick(NOW)["idle"] == 0:
            pass
        consumer.close()

        assert sorted(call["job_id"] for call in calls) == sorted(job_ids)
        assert all(db.jobs[("news", job_id)]["status"] == "SUCCEEDED" for job_id in job_ids)
        assert len(sqs.deleted) == 100


class TestDlqReconciler:
    def _reconciler(self, db, sqs):
        return DlqReconciler(
            jobs=JobLedger(db=_DB, connect_fn=db.connect), queue=sqs,
            queue_urls={"news-extraction-realtime": DLQ},
        )

    def test_non_terminal_job_converges_to_dead(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        message = sqs.send(DLQ, body)

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["dead"] == 1
        row = db.jobs[("news", job_id)]
        assert row["status"] == "DEAD" and row["error_code"] == "SQS_MAX_RECEIVE"
        # 지우지 않는다 — 근거는 보존기간까지 남아야 사람이 본다
        assert sqs.deleted == [] and message.receipt_handle not in sqs.deleted

    def test_terminal_job_is_untouched(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="SUCCEEDED", result_checksum="c")
        sqs.send(DLQ, body)

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["terminal"] == 1
        assert db.jobs[("news", job_id)]["status"] == "SUCCEEDED"

    def test_live_lease_is_deferred_then_converges(self):
        # 실행 중인 job 을 DLQ 도착만으로 죽이면 곧 기록될 결과가 버려진다.
        # 판정은 반복되므로 lease 가 만료된 다음 회차가 수렴시킨다.
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="c1", lease_expires_at=NOW + timedelta(seconds=60)
        )
        message = sqs.send(DLQ, body)
        reconciler = self._reconciler(db, sqs)

        assert reconciler.tick(NOW)["deferred"] == 1
        assert db.jobs[("news", job_id)]["status"] == "CLAIMED"

        sqs.redeliver(DLQ, message)
        assert reconciler.tick(NOW + timedelta(seconds=120))["dead"] == 1

    def test_older_generation_does_not_kill_redriven_job(self):
        # 운영자가 방금 redrive 한 job 을, 낡은 배달이 뒤늦게 DLQ 에 닿았다는 이유로
        # 죽이면 redrive 가 무효가 된다
        db, sqs = FakeMinuteDB(), FakeSqs()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="RETRY_WAIT", redrive_generation=1)
        sqs.send(DLQ, body)

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["superseded"] == 1
        assert db.jobs[("news", job_id)]["status"] == "RETRY_WAIT"

    def test_poison_and_orphan_are_reported(self):
        db, sqs = FakeMinuteDB(), FakeSqs()
        sqs.send(DLQ, "{broken")

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["poison"] == 1 and sqs.deleted == []


class TestRedrive:
    def _dead_job(self):
        db = FakeMinuteDB()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="DEAD", error_code="SQS_MAX_RECEIVE",
                                         attempt_count=5, completed_at=NOW)
        return db, ledger, job_id, body

    def test_creates_one_generation_and_one_event(self):
        db, ledger, job_id, _body = self._dead_job()

        event_id = ledger.redrive_job(kind="news", job_id=job_id, now=NOW)

        row = db.jobs[("news", job_id)]
        assert row["status"] == "RETRY_WAIT" and row["redrive_generation"] == 1
        assert row["attempt_count"] == 0 and row["next_attempt_at"] == NOW
        assert event_id == build_event_id(NEWS_EVENT_TYPE, job_id, 1)
        assert sorted(db.outbox) == sorted([
            build_event_id(NEWS_EVENT_TYPE, job_id, 0), event_id
        ])
        # payload·destination 은 직전 event 에서 복사한다 — 지어내면 commit 이 실제로
        # 보낸 것과 갈리고 아무도 대조하지 않는다
        assert db.outbox[event_id]["payload"] == db.outbox[
            build_event_id(NEWS_EVENT_TYPE, job_id, 0)
        ]["payload"]
        assert db.outbox[event_id]["destination"] == "news-extraction-realtime"
        assert db.outbox[event_id]["status"] == "NEW"

    def test_job_and_event_are_one_transaction(self):
        db, ledger, job_id, _body = self._dead_job()
        before = db.connect_calls
        ledger.redrive_job(kind="news", job_id=job_id, now=NOW)
        # 갈리면 "살아난 job 인데 깨울 메시지가 없다"(또는 그 반대)가 남는다
        assert db.connect_calls - before == 1

    def test_only_dead_jobs_are_redrivable(self):
        db, ledger, job_id, _body = self._dead_job()
        db.jobs[("news", job_id)]["status"] = "CLAIMED"
        with pytest.raises(ValueError, match="DEAD"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW)

    def test_missing_job_raises(self):
        db, ledger, _job_id, _body = self._dead_job()
        with pytest.raises(LookupError):
            ledger.redrive_job(kind="news", job_id="unknown", now=NOW)

    def test_missing_previous_event_raises(self):
        # payload 를 복원할 근거가 없으면 event 를 지어내지 않는다
        db, ledger, job_id, _body = self._dead_job()
        db.outbox.clear()
        with pytest.raises(LookupError, match="직전 delivery event"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW)

    def test_consumer_runs_new_generation_and_drops_old(self):
        db, ledger, job_id, old_body = self._dead_job()
        sqs = FakeSqs()
        old_message = sqs.send(QUEUE, old_body)          # DLQ 이전에 나갔던 배달
        event_id = ledger.redrive_job(kind="news", job_id=job_id, now=NOW)
        new_message = sqs.send(QUEUE, build_message_body(db.outbox[event_id]))
        calls = []
        consumer = make_consumer(db, sqs, recording_handler(calls))

        counts = consumer.tick(NOW)

        assert counts["superseded"] == 1 and counts["succeeded"] == 1
        # 논리 job 은 그대로다 — redrive 는 delivery 세대만 올린다
        assert [call["job_id"] for call in calls] == [job_id]
        assert calls[0]["attempt"] == 1   # 예산도 새로 받았다
        assert sorted(sqs.deleted) == sorted(
            [old_message.receipt_handle, new_message.receipt_handle]
        )


class TestCliGuards:
    class _Settings:
        def __init__(self, consumer, relay=None):
            self.minute_consumer = consumer
            self.minute_relay = relay

    class _Consumer:
        def __init__(self, dlq_urls):
            self.dlq_urls = dlq_urls

    class _Relay:
        def __init__(self, queue_urls):
            self.queue_urls = queue_urls

    def test_missing_config_is_fail_loud(self):
        with pytest.raises(SystemExit, match="minute_consumer"):
            _resolve_queue_urls(self._Settings(None))

    def test_dlq_url_overlapping_source_queue_is_rejected(self):
        # 원 큐를 DLQ 로 넣으면 정상 배달 중인 job 이 전부 DEAD 가 된다
        settings = self._Settings(
            self._Consumer({"news-extraction-realtime": QUEUE}),
            self._Relay({"news-extraction-realtime": QUEUE}),
        )
        with pytest.raises(SystemExit, match="원 큐"):
            _resolve_queue_urls(settings)

    def test_distinct_urls_pass(self):
        settings = self._Settings(
            self._Consumer({"news-extraction-realtime": DLQ}),
            self._Relay({"news-extraction-realtime": QUEUE}),
        )
        assert _resolve_queue_urls(settings) == {"news-extraction-realtime": DLQ}
