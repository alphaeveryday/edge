"""DLQ 대사와 운영 진입점 테스트 (ALPHA-672, 계획 §12 PR 7A 의 3/3).

의도: 이 층이 틀리면 **살아 있는 job 을 몰살하거나(원 큐를 DLQ 로 오인) 부분 실행을
완료로 위장**한다. 셋 다 되돌리기 비싼 실수다.

1. 대사는 근거가 있을 때만 죽인다 — 세대 불일치·살아 있는 lease·남의 레인은 안 건드린다.
2. 메시지를 지우지 않는다(근거 보존) — 대신 이미 판정한 것은 다시 세지 않는다.
3. 끊긴 스캔·판정 불가는 exit 1 이다("다 훑었다"고 말하지 않는다).
"""

from __future__ import annotations

import itertools
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.consumer import (
    ConsumerMessage,
    DlqReconciler,
    SqsQueue,
    _resolve_queue_urls,
    dlq_reconcile_cli,
)
from data_pipeline.minute.jobs import NEWS_EVENT_TYPE, PRICE_EVENT_TYPE, JobLedger, build_event_id
from data_pipeline.minute.models import KST
from data_pipeline.minute.relay import KNOWN_DESTINATIONS, build_message_body

_DB = DbConfig(password="x")
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)
WINDOW_START = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
QUEUE = "https://sqs.test/news-extraction-realtime"
DLQ = "https://sqs.test/news-extraction-realtime-dlq"
CHECKSUM = "c" * 64
ALL_DLQ_URLS = {name: f"https://sqs.test/{name}-dlq" for name in KNOWN_DESTINATIONS}
ALL_DLQ_URLS["news-extraction-realtime"] = DLQ
ALL_QUEUE_URLS = {name: f"https://sqs.test/{name}" for name in KNOWN_DESTINATIONS}

@pytest.fixture
def env():
    """db·큐·원장 셋업 한 벌 + job 하나 넣기."""
    db, sqs = FakeMinuteDB(), FakeSqs()
    ledger = JobLedger(db=_DB, connect_fn=db.connect)
    holder = SimpleNamespace(db=db, sqs=sqs, ledger=ledger)

    def enqueue(queue_url=DLQ, **kwargs):
        job_id, body = enqueue_news(ledger, db, **kwargs)
        return job_id, sqs.send(queue_url, body)

    holder.enqueue = enqueue
    return holder


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
        self._ids = itertools.count(1)

    def send(self, queue_url: str, body: str) -> ConsumerMessage:
        index = next(self._ids)
        message = ConsumerMessage(
            message_id=f"m{index}", receipt_handle=f"r{index}", body=body
        )
        self.queues.setdefault(queue_url, []).append(message)
        return message

    def redeliver(self, queue_url: str, message: ConsumerMessage) -> None:
        self.queues.setdefault(queue_url, []).append(message)

    def receive(self, *, queue_url, max_messages, wait_seconds, visibility_seconds):
        queued = self.queues.setdefault(queue_url, [])
        taken, self.queues[queue_url] = queued[:max_messages], queued[max_messages:]
        return tuple(taken)

    def delete(self, *, queue_url, receipt_handle):
        self.deleted.append(receipt_handle)

    def change_visibility(self, *, queue_url, receipt_handle, seconds):
        self.visibility_changes.append((receipt_handle, seconds))


def enqueue_news(ledger, db, *, article_id=None):
    identity = dict(NEWS_IDENTITY)
    if article_id is not None:
        identity["article_id"] = article_id
    job_id, _ = ledger.enqueue_news_job(
        destination="news-extraction-realtime",
        payload={"article_id": identity["article_id"]}, **identity,
    )
    return job_id, build_message_body(db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id)])


class TestDlqReconciler:
    def _reconciler(self, db, sqs):
        return DlqReconciler(
            jobs=JobLedger(db=_DB, connect_fn=db.connect), queue=sqs,
            queue_urls={"news-extraction-realtime": DLQ},
        )

    def test_non_terminal_job_converges_to_dead(self, env):
        db, sqs, ledger = env.db, env.sqs, env.ledger
        job_id, body = enqueue_news(ledger, db)
        message = sqs.send(DLQ, body)

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["dead"] == 1
        row = db.jobs[("news", job_id)]
        assert row["status"] == "DEAD" and row["error_code"] == "SQS_MAX_RECEIVE"
        # 지우지 않는다 — 근거는 보존기간까지 남아야 사람이 본다
        assert sqs.deleted == [] and message.receipt_handle not in sqs.deleted

    def test_terminal_job_is_untouched(self, env):
        db, sqs, ledger = env.db, env.sqs, env.ledger
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="SUCCEEDED", result_checksum="c")
        sqs.send(DLQ, body)

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["terminal"] == 1
        assert db.jobs[("news", job_id)]["status"] == "SUCCEEDED"

    def test_live_lease_is_deferred_then_converges(self, env):
        # 실행 중인 job 을 DLQ 도착만으로 죽이면 곧 기록될 결과가 버려진다.
        # 판정은 반복되므로 lease 가 만료된 다음 회차가 수렴시킨다.
        db, sqs, ledger = env.db, env.sqs, env.ledger
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="c1", lease_expires_at=NOW + timedelta(seconds=60)
        )
        message = sqs.send(DLQ, body)

        assert self._reconciler(db, sqs).tick(NOW)["deferred"] == 1
        assert db.jobs[("news", job_id)]["status"] == "CLAIMED"

        # 다음 **실행**(주기 잡)이 수렴시킨다 — 한 실행 안에서는 이미 판정한 메시지를
        # 다시 세지 않는다(지우지 않는 메시지가 재수신돼 종료를 막지 않게).
        sqs.redeliver(DLQ, message)
        assert self._reconciler(db, sqs).tick(NOW + timedelta(seconds=120))["dead"] == 1

    def test_misrouted_event_does_not_kill_another_lane(self, env):
        # 가격 사건이 뉴스 DLQ 에 있다 = 발행 배선 오류다. 그 도착은 price job 이
        # 죽었다는 근거가 아닌데, kind 만 보고 죽이면 배선 오류 하나가 멀쩡한 다른
        # 레인의 job 을 몰살한다
        db, sqs, ledger = env.db, env.sqs, env.ledger
        db.windows[("msn_x", WINDOW_START)] = {
            "session_id": "msn_x", "window_start": WINDOW_START, "generation": 1,
        }
        job_id, _ = ledger.enqueue_price_job(
            destination="price-analysis-realtime", payload={"window": "0900"},
            session_id="msn_x", window_start=WINDOW_START, generation=1,
            trigger_schema_version="t1",
        )
        sqs.send(DLQ, build_message_body(db.outbox[build_event_id(PRICE_EVENT_TYPE, job_id)]))

        counts = self._reconciler(db, sqs).tick(NOW)

        assert counts["misrouted"] == 1
        assert db.jobs[("price", job_id)]["status"] == "PENDING"

    def test_claimed_with_null_lease_is_recoverable(self, env):
        # 복원·구 writer 가 남길 수 있는 형상이다. NULL 비교는 참이 안 되므로 예외
        # 절이 없으면 claim 도 대사도 못 해 그 job 이 영구 고착된다.
        db, sqs, ledger = env.db, env.sqs, env.ledger
        job_id, body = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="ghost", lease_expires_at=None
        )
        sqs.send(DLQ, body)

        assert self._reconciler(db, sqs).tick(NOW)["dead"] == 1
        assert db.jobs[("news", job_id)]["error_code"] == "SQS_MAX_RECEIVE"

    def test_older_generation_does_not_kill_redriven_job(self, env):
        # 운영자가 방금 redrive 한 job 을, 낡은 배달이 뒤늦게 DLQ 에 닿았다는 이유로
        # 죽이면 redrive 가 무효가 된다
        db, sqs, ledger = env.db, env.sqs, env.ledger
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

    @staticmethod
    def _all(prefix):
        """어휘 3종을 다 채운 매핑 — 부분 매핑은 그 자체가 거부 사유다."""
        return {name: f"{prefix}/{name}" for name in KNOWN_DESTINATIONS}

    def test_missing_config_is_fail_loud(self):
        with pytest.raises(SystemExit, match="minute_consumer"):
            _resolve_queue_urls(self._Settings(None))

    def test_relay_mapping_is_required(self):
        # "있으면 검사"로 두면 사고가 나는 바로 그 상황(일회성 실행에 consumer 설정만
        # 넣고 원 큐 URL 을 붙여넣기)에서 검사가 통째로 꺼진다
        settings = self._Settings(self._Consumer(self._all("https://sqs.test/dlq")))
        with pytest.raises(SystemExit, match="minute_relay"):
            _resolve_queue_urls(settings)

    def test_empty_dlq_mapping_is_rejected_by_config(self):
        # 빈 매핑이 통과하면 reconciler 가 큐를 하나도 안 보고 exit 0 — 실제 DLQ 의
        # non-terminal job 이 남아도 운영 게이트가 초록이다(Rule 12)
        from pydantic import ValidationError

        from data_pipeline.config.models import MinuteConsumerConfig

        with pytest.raises(ValidationError):
            MinuteConsumerConfig(dlq_urls={})

    def test_dlq_url_overlapping_source_queue_is_rejected(self):
        # 원 큐를 DLQ 로 넣으면 정상 배달 중인 job 이 전부 DEAD 가 된다
        dlq_urls = self._all("https://sqs.test/dlq")
        queue_urls = self._all("https://sqs.test/main")
        dlq_urls["news-extraction-realtime"] = queue_urls["news-extraction-realtime"]
        settings = self._Settings(self._Consumer(dlq_urls), self._Relay(queue_urls))
        with pytest.raises(SystemExit, match="원 큐"):
            _resolve_queue_urls(settings)

    def test_duplicate_dlq_urls_are_rejected(self):
        # 두 레인이 같은 DLQ 를 가리키면 한쪽은 한 번도 조회되지 않는데 명령은 성공한다
        dlq_urls = self._all("https://sqs.test/dlq")
        dlq_urls["news-extraction-backfill"] = dlq_urls["news-extraction-realtime"]
        settings = self._Settings(
            self._Consumer(dlq_urls), self._Relay(self._all("https://sqs.test/main"))
        )
        with pytest.raises(SystemExit, match="같은 DLQ"):
            _resolve_queue_urls(settings)

    def test_reconcile_cli_terminates_when_messages_repeat(self, monkeypatch):
        # 지우지 않는 메시지는 visibility 가 풀리면 다시 온다 — 그걸 새 일감으로 세면
        # 종료 조건이 영원히 성립하지 않아 일회성 명령이 안 끝난다
        db = FakeMinuteDB()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, body = enqueue_news(ledger, db)

        class RepeatingSqs(FakeSqs):
            rounds = 0

            def receive(self, **kwargs):
                RepeatingSqs.rounds += 1
                # 큐 3개 × (판정 1회차 + 조용한 2회차) — 그보다 커지면 종료 조건이 없는 것
                assert RepeatingSqs.rounds <= 12, "종료 조건이 성립하지 않는다(무한 루프)"
                return tuple(self.queues.get(kwargs["queue_url"], ()))

        sqs = RepeatingSqs()
        sqs.send(DLQ, body)   # 매 회차 같은 메시지가 다시 보인다
        settings = SimpleNamespace(
            db=_DB,
            minute_consumer=SimpleNamespace(
                dlq_urls=ALL_DLQ_URLS, batch_size=10, wait_seconds=0,
                visibility_seconds=60,
            ),
            minute_relay=SimpleNamespace(queue_urls=ALL_QUEUE_URLS),
        )
        monkeypatch.setattr("data_pipeline.minute.consumer.JobLedger",
                            lambda db: ledger)
        monkeypatch.setattr("data_pipeline.minute.consumer.SqsQueue", lambda **_: sqs)

        assert dlq_reconcile_cli(settings) == 0
        assert db.jobs[("news", job_id)]["error_code"] == "SQS_MAX_RECEIVE"

    def test_partial_destination_coverage_is_rejected(self):
        # 한 레인의 DLQ 가 빠지면 그 레인의 job 은 아무도 대사하지 않는데, 명령은
        # 나머지만 훑고 성공으로 끝나 부분 커버리지가 초록으로 보인다.
        # 두 매핑이 **함께** 부실하면 교집합·차집합 비교로는 안 잡힌다 — 어휘와 댄다.
        dlq_urls = self._all("https://sqs.test/dlq")
        del dlq_urls["price-analysis-realtime"]
        settings = self._Settings(
            self._Consumer(dlq_urls), self._Relay(self._all("https://sqs.test/main"))
        )
        with pytest.raises(SystemExit, match="누락"):
            _resolve_queue_urls(settings)

    def test_both_mappings_short_of_the_vocabulary_is_rejected(self):
        settings = self._Settings(
            self._Consumer({"news-extraction-realtime": DLQ}),
            self._Relay({"news-extraction-realtime": QUEUE}),
        )
        with pytest.raises(SystemExit, match="큐 어휘"):
            _resolve_queue_urls(settings)

    def test_truncated_scan_is_not_reported_as_success(self, monkeypatch):
        # --max-ticks 상한이나 SIGTERM 으로 끊기면 남은 메시지를 **보지도 못했다** —
        # 성공으로 보고하면 부분 실행이 완료로 위장된다(Rule 12)
        db = FakeMinuteDB()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        sqs = FakeSqs()
        for index in range(3):
            _job_id, body = enqueue_news(ledger, db, article_id=f"art-{index}")
            sqs.send(DLQ, body)
        settings = SimpleNamespace(
            db=_DB,
            minute_consumer=SimpleNamespace(
                dlq_urls=ALL_DLQ_URLS, batch_size=1, wait_seconds=0,
                visibility_seconds=60,
            ),
            minute_relay=SimpleNamespace(queue_urls=ALL_QUEUE_URLS),
        )
        monkeypatch.setattr("data_pipeline.minute.consumer.JobLedger", lambda db: ledger)
        monkeypatch.setattr("data_pipeline.minute.consumer.SqsQueue", lambda **_: sqs)

        assert dlq_reconcile_cli(settings, max_ticks=1) == 1

    def test_distinct_urls_pass(self):
        dlq_urls = self._all("https://sqs.test/dlq")
        settings = self._Settings(
            self._Consumer(dlq_urls), self._Relay(self._all("https://sqs.test/main"))
        )
        assert _resolve_queue_urls(settings) == dlq_urls
