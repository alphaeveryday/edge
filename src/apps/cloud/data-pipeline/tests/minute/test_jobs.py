"""job/outbox repository 테스트 (ALPHA-664, 계획 §7 2C 해당분).

의도: job 결정적 identity·retry 권위(PG)·outbox 멱등이 깨지면 중복 LLM 호출·이벤트
유실·유령 재시도가 조용히 일어난다. 실제 JobLedger 를 FakeMinuteDB 위에서 돌려
SQL 경로 그대로 검증한다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.jobs import (
    NEWS_EVENT_TYPE,
    PRICE_EVENT_TYPE,
    JobLedger,
    build_event_id,
    news_job_id,
    price_job_id,
)
from data_pipeline.minute.models import KST

_DB = DbConfig(password="x")
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)
WINDOW_START = datetime(2026, 7, 31, 9, 0, tzinfo=KST)

NEWS_IDENTITY = dict(
    source_code="bigkinds",
    article_id="a" * 64,
    input_fingerprint="f" * 64,
    tagger_version="v4-pro",
    ontology_version="onto-7",
)


def make_ledger(db):
    return JobLedger(db=_DB, connect_fn=db.connect)


class TestDeterministicIds:
    def test_same_materials_same_ids(self):
        assert news_job_id(**NEWS_IDENTITY) == news_job_id(**NEWS_IDENTITY)
        first = price_job_id(
            session_id="msn_x", window_start=WINDOW_START,
            generation=1, trigger_schema_version="trig-1",
        )
        # 같은 순간의 UTC 표현도 같은 ID — canonical Z 정규화가 깨지면 KST/UTC 호출자가
        # 같은 window 에 다른 job 을 만든다
        second = price_job_id(
            session_id="msn_x", window_start=WINDOW_START.astimezone(timezone.utc),
            generation=1, trigger_schema_version="trig-1",
        )
        assert first == second
        assert len(first) == 64 and first == first.lower()

    def test_different_generation_different_job(self):
        base = dict(session_id="msn_x", window_start=WINDOW_START, trigger_schema_version="t1")
        assert price_job_id(generation=1, **base) != price_job_id(generation=2, **base)

    def test_event_id_redrive_only_changes_suffix(self):
        job_id = news_job_id(**NEWS_IDENTITY)
        initial = build_event_id(NEWS_EVENT_TYPE, job_id)
        redrive = build_event_id(NEWS_EVENT_TYPE, job_id, redrive_generation=1)
        assert initial == f"NewsExtractionRequested:{job_id}:0"
        assert redrive == f"NewsExtractionRequested:{job_id}:1"
        with pytest.raises(ValueError):
            build_event_id("BogusEvent", job_id)


class TestJobIdentityInsert:
    def test_duplicate_insert_is_noop(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, created1 = ledger.insert_news_job(**NEWS_IDENTITY)
        job_id2, created2 = ledger.insert_news_job(**NEWS_IDENTITY)
        assert created1 is True and created2 is False and job_id == job_id2
        assert len(db.jobs) == 1

    def test_price_job_insert_noop(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        args = dict(session_id="msn_x", window_start=WINDOW_START,
                    generation=1, trigger_schema_version="t1")
        _, created1 = ledger.insert_price_job(**args)
        _, created2 = ledger.insert_price_job(**args)
        assert created1 is True and created2 is False


class TestJobClaim:
    def test_claim_then_no_more_eligible(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, _ = ledger.insert_news_job(**NEWS_IDENTITY)
        claim = ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=60)
        assert claim == {"job_id": job_id, "attempt_count": 1}
        assert ledger.claim_due_job(kind="news", worker_id="c2", now=NOW, lease_seconds=60) is None

    def test_retry_wait_gates_on_next_attempt_at(self):
        # retry 자격·시각의 권위는 PG — next_attempt_at 전 delivery 는 실행 0 이어야 한다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, _ = ledger.insert_news_job(**NEWS_IDENTITY)
        ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=60)
        retry_at = NOW + timedelta(minutes=5)
        assert ledger.retry_job(
            kind="news", job_id=job_id, worker_id="c1", now=NOW,
            next_attempt_at=retry_at, error_code="LLM_TIMEOUT",
        ) is True
        early = NOW + timedelta(minutes=1)
        assert ledger.claim_due_job(kind="news", worker_id="c1", now=early, lease_seconds=60) is None
        reclaim = ledger.claim_due_job(kind="news", worker_id="c1", now=retry_at, lease_seconds=60)
        assert reclaim["job_id"] == job_id and reclaim["attempt_count"] == 2

    def test_past_next_attempt_rejected(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, _ = ledger.insert_news_job(**NEWS_IDENTITY)
        ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=60)
        with pytest.raises(ValueError):
            ledger.retry_job(
                kind="news", job_id=job_id, worker_id="c1", now=NOW,
                next_attempt_at=NOW, error_code="X",
            )

    def test_expired_lease_reclaimed(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, _ = ledger.insert_news_job(**NEWS_IDENTITY)
        ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=1)
        later = NOW + timedelta(seconds=2)
        reclaim = ledger.claim_due_job(kind="news", worker_id="c2", now=later, lease_seconds=60)
        assert reclaim["job_id"] == job_id and reclaim["attempt_count"] == 2

    def test_succeed_and_stale_claim_transition_rejected(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, _ = ledger.insert_news_job(**NEWS_IDENTITY)
        ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=1)
        later = NOW + timedelta(seconds=2)
        ledger.claim_due_job(kind="news", worker_id="c2", now=later, lease_seconds=60)
        # c1 의 늦은 성공 보고는 거부 — claim 은 이미 c2 것이다
        assert ledger.succeed_job(
            kind="news", job_id=job_id, worker_id="c1", now=later, result_checksum="e" * 64,
        ) is False
        assert ledger.succeed_job(
            kind="news", job_id=job_id, worker_id="c2", now=later, result_checksum="e" * 64,
        ) is True
        assert db.jobs[("news", job_id)]["status"] == "SUCCEEDED"

    def test_dead_terminal(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        job_id, _ = ledger.insert_news_job(**NEWS_IDENTITY)
        ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=60)
        assert ledger.dead_job(
            kind="news", job_id=job_id, worker_id="c1", now=NOW, error_code="BUDGET",
        ) is True
        assert db.jobs[("news", job_id)]["status"] == "DEAD"
        assert ledger.claim_due_job(kind="news", worker_id="c1", now=NOW, lease_seconds=60) is None


class TestPriceStaleRejection:
    def test_stale_generation_dead_at_claim(self):
        # correction commit 이 window generation 을 올렸으면 낮은 세대 job 은 실행하지
        # 않고 DEAD('STALE') 로 격리한다 (v0.7 10.5 — claim 시점 한 곳)
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        db.windows[("msn_x", WINDOW_START)] = {
            "session_id": "msn_x", "window_start": WINDOW_START, "generation": 2,
        }
        job_id, _ = ledger.insert_price_job(
            session_id="msn_x", window_start=WINDOW_START,
            generation=1, trigger_schema_version="t1",
        )
        assert ledger.claim_due_job(kind="price", worker_id="c1", now=NOW, lease_seconds=60) is None
        row = db.jobs[("price", job_id)]
        assert row["status"] == "DEAD" and row["error_code"] == "STALE"

    def test_current_generation_claims_normally(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        db.windows[("msn_x", WINDOW_START)] = {
            "session_id": "msn_x", "window_start": WINDOW_START, "generation": 1,
        }
        job_id, _ = ledger.insert_price_job(
            session_id="msn_x", window_start=WINDOW_START,
            generation=1, trigger_schema_version="t1",
        )
        claim = ledger.claim_due_job(kind="price", worker_id="c1", now=NOW, lease_seconds=60)
        assert claim["job_id"] == job_id


class TestOutbox:
    def _event(self, ledger, *, suffix="0", **overrides):
        job_id = news_job_id(**NEWS_IDENTITY)
        args = dict(
            event_id=f"{NEWS_EVENT_TYPE}:{job_id}:{suffix}",
            event_type=NEWS_EVENT_TYPE,
            destination="news-extraction-realtime",
            aggregate_id=job_id,
            generation=1,
            payload={"job_id": job_id},
        )
        args.update(overrides)
        return ledger.insert_outbox_event(**args)

    def test_duplicate_event_noop(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        assert self._event(ledger) is True
        assert self._event(ledger) is False  # 같은 논리 사건 재전달 — 같은 event_id
        assert len(db.outbox) == 1

    def test_claim_publish_flow(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        self._event(ledger)
        self._event(ledger, suffix="1")
        batch = ledger.claim_outbox_batch(relay_id="r1", now=NOW, limit=10, lease_seconds=30)
        assert len(batch) == 2
        # claim 중(미만료)엔 다른 Relay 가 못 가져간다
        assert ledger.claim_outbox_batch(relay_id="r2", now=NOW, limit=10, lease_seconds=30) == []
        assert ledger.mark_published(
            event_id=batch[0]["event_id"], relay_id="r1", now=NOW,
        ) is True
        assert db.outbox[batch[0]["event_id"]]["status"] == "PUBLISHED"

    def test_claim_expiry_allows_other_relay(self):
        # Relay crash — claim 이 status 가 아니라서 만료 뒤 자연 회수된다
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        self._event(ledger)
        ledger.claim_outbox_batch(relay_id="r1", now=NOW, limit=10, lease_seconds=1)
        later = NOW + timedelta(seconds=2)
        batch = ledger.claim_outbox_batch(relay_id="r2", now=later, limit=10, lease_seconds=30)
        assert len(batch) == 1

    def test_publish_failure_retry_then_terminal(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        self._event(ledger)
        [event] = ledger.claim_outbox_batch(relay_id="r1", now=NOW, limit=1, lease_seconds=30)
        retry_at = NOW + timedelta(minutes=1)
        assert ledger.record_publish_failure(
            event_id=event["event_id"], relay_id="r1", now=NOW,
            next_attempt_at=retry_at, error="SQS 5xx",
        ) is True
        row = db.outbox[event["event_id"]]
        assert row["status"] == "NEW" and row["attempt_count"] == 1
        # next_attempt_at 전엔 재claim 불가, 도달 후 가능
        assert ledger.claim_outbox_batch(relay_id="r1", now=NOW, limit=1, lease_seconds=30) == []
        [again] = ledger.claim_outbox_batch(relay_id="r1", now=retry_at, limit=1, lease_seconds=30)
        assert ledger.record_publish_failure(
            event_id=again["event_id"], relay_id="r1", now=retry_at,
            next_attempt_at=None, error="destination 미정의", terminal=True,
        ) is True
        assert db.outbox[event["event_id"]]["status"] == "DEAD"
        assert ledger.claim_outbox_batch(
            relay_id="r1", now=retry_at + timedelta(minutes=5), limit=1, lease_seconds=30,
        ) == []

    def test_batch_limit_and_order(self):
        db = FakeMinuteDB()
        ledger = make_ledger(db)
        for i in range(5):
            self._event(ledger, suffix=str(i))
        batch = ledger.claim_outbox_batch(relay_id="r1", now=NOW, limit=3, lease_seconds=30)
        assert [e["event_id"].rsplit(":", 1)[1] for e in batch] == ["0", "1", "2"]  # 오래된 순
