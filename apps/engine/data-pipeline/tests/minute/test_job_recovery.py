"""job 원장의 **메시지 기반 경로**와 복구 장치 테스트 (ALPHA-672, 계획 §12 PR 7A).

의도: 여기가 깨지면 중복 LLM 호출·되돌릴 수 없는 유실·영구 고착이 조용히 일어난다.
세 축을 붙잡는다.

1. **전이는 attempt + redrive_generation 이중 fence** — redrive 가 attempt_count 를
   0 으로 되돌리므로 새 세대의 첫 claim 이 옛 실행과 같은 attempt 번호를 갖는다.
2. **DLQ 대사는 근거가 있을 때만 죽인다** — 세대 불일치·살아 있는 lease 는 건드리지 않는다.
3. **redrive 는 "막힌 것"만, 그리고 복사해서 될 때만** — STALE·배선 불일치·크기 초과는
   복사해도 같은 이유로 다시 DEAD 다(세대만 오른다).

실제 JobLedger 를 FakeMinuteDB(SQL 매칭 fake) 위에서 돌려 SQL 경로를 그대로 태운다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.jobs import NEWS_EVENT_TYPE, PRICE_EVENT_TYPE, JobLedger, build_event_id
from data_pipeline.minute.models import KST

_DB = DbConfig(password="x")
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)
WINDOW_START = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
CHECKSUM = "c" * 64

NEWS_IDENTITY = dict(
    source_code="bigkinds",
    article_id="a" * 64,
    input_fingerprint="f" * 64,
    tagger_version="v4-pro",
    ontology_version="onto-7",
)


def enqueue_news(ledger, db, *, article_id=None, payload=None):
    """job + outbox event 를 실제 경로로 만든다."""
    identity = dict(NEWS_IDENTITY)
    if article_id is not None:
        identity["article_id"] = article_id
    job_id, _ = ledger.enqueue_news_job(
        destination="news-extraction-realtime",
        payload=payload if payload is not None else {"article_id": identity["article_id"]},
        **identity,
    )
    return job_id, db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id)]


@pytest.fixture
def env():
    db = FakeMinuteDB()
    ledger = JobLedger(db=_DB, connect_fn=db.connect)
    return db, ledger


class TestClaimByMessage:
    """메시지가 지목한 job 하나를 집는 경로 — 폴링 claim 과 자격 규칙을 공유한다."""

    def test_generation_guard_rejects_stale_delivery(self, env):
        # 상태 읽기와 claim 사이에 redrive 가 끼면 옛 세대 메시지가 새 세대 job 을
        # 집어 실행하고 SUCCEEDED 로 마감한다 — 그러면 redrive 가 통째로 사라진다
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)]["redrive_generation"] = 1

        assert ledger.claim_job(
            kind="news", job_id=job_id, redrive_generation=0, worker_id="c1",
            now=NOW, lease_seconds=60,
        ) is None
        assert ledger.claim_job(
            kind="news", job_id=job_id, redrive_generation=1, worker_id="c1",
            now=NOW, lease_seconds=60,
        )["attempt_count"] == 1

    def test_live_lease_blocks_and_expired_lease_is_reclaimed(self, env):
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        first = ledger.claim_job(kind="news", job_id=job_id, redrive_generation=0,
                                 worker_id="c1", now=NOW, lease_seconds=60)
        assert first["attempt_count"] == 1
        # 살아 있는 lease — 다른 Consumer 는 못 집는다
        assert ledger.claim_job(kind="news", job_id=job_id, redrive_generation=0,
                                worker_id="c2", now=NOW, lease_seconds=60) is None
        later = NOW + timedelta(seconds=61)
        second = ledger.claim_job(kind="news", job_id=job_id, redrive_generation=0,
                                  worker_id="c2", now=later, lease_seconds=60)
        assert second["attempt_count"] == 2   # 이어지는 attempt — 새로 세지 않는다

    def test_claimed_with_null_lease_is_reclaimable(self, env):
        # 복원·구 writer 가 남길 수 있는 형상이다. NULL 비교는 참이 안 되므로 예외 절이
        # 없으면 그 행은 어떤 경로로도 못 집어 영구 고착된다.
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="ghost", lease_expires_at=None
        )
        assert ledger.claim_job(kind="news", job_id=job_id, redrive_generation=0,
                                worker_id="c1", now=NOW, lease_seconds=60)

    def test_price_stale_generation_is_isolated(self, env):
        # stale 거부는 claim 시점 한 곳이다(v0.7 10.5) — 폴링·메시지 두 경로가 공유한다
        db, ledger = env
        db.windows[("msn_x", WINDOW_START)] = {
            "session_id": "msn_x", "window_start": WINDOW_START, "generation": 2,
        }
        job_id, _ = ledger.enqueue_price_job(
            destination="price-analysis-realtime", payload={"window": "0900"},
            session_id="msn_x", window_start=WINDOW_START, generation=1,
            trigger_schema_version="t1",
        )
        assert ledger.claim_job(kind="price", job_id=job_id, redrive_generation=0,
                                worker_id="c1", now=NOW, lease_seconds=60) == "STALE"
        row = db.jobs[("price", job_id)]
        assert row["status"] == "DEAD" and row["error_code"] == "STALE"


class TestGenerationFence:
    def test_old_attempt_cannot_close_the_new_generation(self, env):
        # ⚠️ attempt fence 만으로는 부족하다 — redrive 가 attempt_count 를 0 으로
        # 되돌리므로 새 세대의 첫 claim 이 **같은 attempt 번호**를 갖는다. 그러면 lease
        # 만료 뒤에도 살아 있던 옛 실행의 늦은 보고가 새 세대를 마감해, 운영자의
        # redrive 가 통째로 사라진다.
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="DEAD", error_code="SQS_MAX_RECEIVE")
        ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                           actor="tester@host", reason="테스트")
        fresh = ledger.claim_job(kind="news", job_id=job_id, redrive_generation=1,
                                 worker_id="c1", now=NOW, lease_seconds=60)
        assert fresh["attempt_count"] == 1 and fresh["redrive_generation"] == 1

        # 세대 0 의 attempt 1 이 뒤늦게 보고한다 — 두 값 다 새 claim 과 겹친다
        assert ledger.succeed_job(
            kind="news", job_id=job_id, worker_id="c1", attempt=1,
            redrive_generation=0, now=NOW, result_checksum="a" * 64,
        ) is False
        assert ledger.heartbeat_job(
            kind="news", job_id=job_id, worker_id="c1", attempt=1,
            redrive_generation=0, now=NOW, lease_seconds=600,
        ) is False
        assert db.jobs[("news", job_id)]["status"] == "CLAIMED"
        # 새 세대의 보고는 그대로 통과한다
        assert ledger.succeed_job(
            kind="news", job_id=job_id, worker_id="c1", attempt=1,
            redrive_generation=1, now=NOW, result_checksum=CHECKSUM,
        ) is True


class TestDeadOnDlq:
    """DLQ 도착 + DB non-terminal 을 SQS_MAX_RECEIVE DEAD 로 수렴시키는 CAS."""

    def test_non_terminal_job_converges(self, env):
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        assert ledger.dead_on_dlq(kind="news", job_id=job_id,
                                  redrive_generation=0, now=NOW) is True
        row = db.jobs[("news", job_id)]
        assert row["status"] == "DEAD" and row["error_code"] == "SQS_MAX_RECEIVE"

    def test_terminal_job_is_untouched(self, env):
        # SUCCEEDED 를 DEAD 로 덮으면 끝난 일이 실패로 뒤집힌다
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="SUCCEEDED", result_checksum=CHECKSUM)
        assert ledger.dead_on_dlq(kind="news", job_id=job_id,
                                  redrive_generation=0, now=NOW) is False
        assert db.jobs[("news", job_id)]["status"] == "SUCCEEDED"

    def test_live_lease_is_left_alone(self, env):
        # 지금 누가 실행 중이라는 뜻이고 그 결과는 곧 기록된다 — DLQ 도착은 transport
        # 사정이지 그 job 이 죽었다는 근거가 아니다. lease 가 만료되면 그때 수렴한다.
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="c1",
            lease_expires_at=NOW + timedelta(seconds=60),
        )
        assert ledger.dead_on_dlq(kind="news", job_id=job_id,
                                  redrive_generation=0, now=NOW) is False
        assert ledger.dead_on_dlq(kind="news", job_id=job_id, redrive_generation=0,
                                  now=NOW + timedelta(seconds=120)) is True

    def test_older_generation_does_not_kill_a_redriven_job(self, env):
        # 낡은 배달이 뒤늦게 DLQ 에 닿았을 뿐인데 방금 redrive 한 세대를 죽이면
        # 운영자의 복구가 무효가 된다
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="RETRY_WAIT", redrive_generation=1)
        assert ledger.dead_on_dlq(kind="news", job_id=job_id,
                                  redrive_generation=0, now=NOW) is False
        assert db.jobs[("news", job_id)]["status"] == "RETRY_WAIT"

    def test_claimed_with_null_lease_converges(self, env):
        db, ledger = env
        job_id, _ = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="ghost", lease_expires_at=None
        )
        assert ledger.dead_on_dlq(kind="news", job_id=job_id,
                                  redrive_generation=0, now=NOW) is True


class TestRedrive:
    def _dead_job(self):
        db = FakeMinuteDB()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, event = enqueue_news(ledger, db)
        db.jobs[("news", job_id)].update(status="DEAD", error_code="SQS_MAX_RECEIVE",
                                         attempt_count=5, completed_at=NOW)
        return db, ledger, job_id, event

    def test_creates_one_generation_and_one_event(self):
        db, ledger, job_id, _event = self._dead_job()

        event_id = ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

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
        db, ledger, job_id, _event = self._dead_job()
        before = db.connect_calls
        ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")
        # 갈리면 "살아난 job 인데 깨울 메시지가 없다"(또는 그 반대)가 남는다
        assert db.connect_calls - before == 1

    def test_succeeded_job_is_refused(self):
        db, ledger, job_id, _event = self._dead_job()
        db.jobs[("news", job_id)]["status"] = "SUCCEEDED"
        with pytest.raises(ValueError, match="SUCCEEDED"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

    def test_running_job_is_refused(self):
        # 살아 있는 lease = 지금 누가 돌고 있다. 세대를 올리면 그 실행의 결과가
        # 세대 fence 에 걸려 통째로 버려진다 — 만료를 기다리면 된다.
        db, ledger, job_id, _event = self._dead_job()
        db.jobs[("news", job_id)].update(
            status="CLAIMED", claimed_by="c1", lease_expires_at=NOW + timedelta(seconds=60)
        )
        db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id, 0)]["status"] = "DEAD"
        with pytest.raises(ValueError, match="실행 중"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

    def test_dead_job_with_stale_lease_is_redrivable(self):
        # DEAD 인데 lease 잔재가 남아 있어도 대상이다 — 실행 중이 아니다
        db, ledger, job_id, _event = self._dead_job()
        db.jobs[("news", job_id)].update(
            claimed_by="c1", lease_expires_at=NOW - timedelta(seconds=1)
        )
        assert ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")
        assert db.jobs[("news", job_id)]["status"] == "RETRY_WAIT"

    def test_healthy_job_is_refused(self):
        # 막혔다는 근거가 없으면 거절한다 — 정상 진행 중인 job 의 세대를 올리면 지금
        # 큐에 있는 배달이 superseded 로 버려지고 재시도 예산까지 초기화된다
        db = FakeMinuteDB()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        job_id, _body = enqueue_news(ledger, db)
        with pytest.raises(ValueError, match="막혀 있지 않다"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

    def test_superseded_dead_event_stops_blocking_the_drain_gate(self):
        # redrive 로 복구를 끝냈는데도 옛 DEAD 행이 계속 미발행으로 집계되면, 배출
        # 게이트(relay --max-ticks)가 영원히 "미발행 남음"으로 실패한다
        db, ledger, job_id, _event = self._dead_job()
        db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id, 0)]["status"] = "DEAD"
        assert ledger.count_unpublished()["DEAD"] == 1

        event_id = ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")
        db.outbox[event_id]["status"] = "PUBLISHED"   # Relay 가 새 세대를 발행했다

        assert ledger.count_unpublished() == {"NEW": 0, "DEAD": 0}

    def test_stale_price_job_is_not_redrivable(self):
        # STALE 은 시간이 풀어주지 않는다 — 되살려도 claim 이 같은 비교에서 다시 DEAD
        # 로 보내므로, terminal 이던 행을 성공할 수 없는 non-terminal 로 바꾸기만 한다
        db = FakeMinuteDB()
        ledger = JobLedger(db=_DB, connect_fn=db.connect)
        db.windows[("msn_x", WINDOW_START)] = {
            "session_id": "msn_x", "window_start": WINDOW_START, "generation": 2,
        }
        job_id, _ = ledger.enqueue_price_job(
            destination="price-analysis-realtime", payload={"window": "0900"},
            session_id="msn_x", window_start=WINDOW_START, generation=1,
            trigger_schema_version="t1",
        )
        db.jobs[("price", job_id)].update(status="DEAD", error_code="STALE")
        with pytest.raises(ValueError, match="STALE"):
            ledger.redrive_job(kind="price", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

    def test_audit_is_written_in_the_same_transaction(self):
        # 수동 개입의 유일한 근거다 — 트랜잭션 밖(로그)에만 남기면 롤백된 redrive 의
        # 기록만 남거나, 보존기간이 지나면 누가·왜가 사라진다
        db, ledger, job_id, _event = self._dead_job()
        before = db.connect_calls

        ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                           actor="oncall@host-1", reason="큐 URL 오타 수정 후 재시도")

        assert db.connect_calls - before == 1
        superseded = db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id, 0)]
        assert "oncall@host-1" in superseded["last_error"]
        assert "큐 URL 오타 수정 후 재시도" in superseded["last_error"]

    def test_actor_and_reason_are_required(self):
        db, ledger, job_id, _event = self._dead_job()
        with pytest.raises(ValueError, match="actor"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW, actor="", reason="x")

    def test_misrouted_event_is_repaired_by_giving_the_right_queue(self):
        # ⚠️ 배선이 어긋난 채 커밋된 행은 그 값이 컬럼에 박혀 있고, event_id 가 결정적
        # 이라(ON CONFLICT DO NOTHING) producer 를 고쳐 재실행해도 그 행은 안 바뀐다 —
        # 여기서 바로잡지 못하면 수동 SQL 말고는 복구 경로가 없다(#456 봇 2차 지적).
        db, ledger, job_id, _event = self._dead_job()
        event = db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id, 0)]
        event.update(status="DEAD", destination="price-analysis-realtime")

        # 그대로 복사하면 Relay 가 곧장 다시 격리한다 — 거부하고 가능한 큐를 알려준다
        with pytest.raises(ValueError, match="어긋난다"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

        event_id = ledger.redrive_job(
            kind="news", job_id=job_id, now=NOW, actor="oncall@host",
            reason="destination 오배선 정정", destination="news-extraction-realtime",
        )
        assert db.outbox[event_id]["destination"] == "news-extraction-realtime"
        assert db.outbox[event_id]["status"] == "NEW"

    def test_oversized_payload_is_not_redrivable(self):
        # 크기 초과로 격리된 event 를 그대로 복사하면 새 event 도 곧장 DEAD 다 —
        # 세대만 오르고 job 은 계속 안 나간다(#456 봇 3차 지적). 고칠 곳은 쓰는 쪽이다.
        db, ledger, job_id, _event = self._dead_job()
        event = db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id, 0)]
        event.update(status="DEAD", payload={"body": "x" * 1_100_000})
        with pytest.raises(ValueError, match="SQS 상한"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

    def test_corrected_destination_must_match_the_event_type(self):
        db, ledger, job_id, _event = self._dead_job()
        with pytest.raises(ValueError, match="어긋난다"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW, actor="a@b",
                               reason="테스트", destination="price-analysis-realtime")

    def test_relay_dead_event_is_recoverable(self):
        # PR 6 은 outbox DEAD 를 좁게 판정하면서 복구를 이 PR 에 위임했다 — job 은
        # 멀쩡한데 delivery event 만 DEAD 면 Relay 는 NEW 만 집으므로 영구 고착이다
        db, ledger, job_id, _event = self._dead_job()
        db.jobs[("news", job_id)].update(status="PENDING", error_code=None,
                                         attempt_count=0, completed_at=None)
        db.outbox[build_event_id(NEWS_EVENT_TYPE, job_id, 0)]["status"] = "DEAD"

        event_id = ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

        assert db.outbox[event_id]["status"] == "NEW"   # Relay 가 다시 집는다
        assert db.jobs[("news", job_id)]["redrive_generation"] == 1

    def test_dead_reason_survives_redrive(self):
        # 왜 죽었는지가 유일한 조회 근거다 — redrive 가 덮으면 사라진다
        db, ledger, job_id, _event = self._dead_job()
        ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")
        assert db.jobs[("news", job_id)]["error_code"] == "SQS_MAX_RECEIVE"

    def test_missing_job_raises(self):
        db, ledger, _job_id, _body = self._dead_job()
        with pytest.raises(LookupError):
            ledger.redrive_job(kind="news", job_id="unknown", now=NOW,
                               actor="tester@host", reason="테스트")

    def test_missing_previous_event_raises(self):
        # payload 를 복원할 근거가 없으면 event 를 지어내지 않는다
        db, ledger, job_id, _event = self._dead_job()
        db.outbox.clear()
        with pytest.raises(LookupError, match="직전 delivery event"):
            ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")

    def test_old_attempt_cannot_close_the_new_generation(self):
        # ⚠️ attempt fence 만으로는 부족하다 — redrive 가 attempt_count 를 0 으로
        # 되돌리므로 새 세대의 첫 claim 이 **같은 attempt 번호**를 갖는다. 그러면 lease
        # 만료 뒤에도 살아 있던 옛 실행의 늦은 보고가 새 세대를 마감해, 운영자의
        # redrive 가 통째로 사라진다.
        db, ledger, job_id, _event = self._dead_job()
        ledger.redrive_job(kind="news", job_id=job_id, now=NOW,
                               actor="tester@host", reason="테스트")
        fresh = ledger.claim_job(
            kind="news", job_id=job_id, redrive_generation=1, worker_id="c1",
            now=NOW, lease_seconds=60,
        )
        assert fresh["attempt_count"] == 1 and fresh["redrive_generation"] == 1

        # 세대 0 의 attempt 1 이 뒤늦게 보고한다 — 두 값 다 새 claim 과 겹친다
        assert ledger.succeed_job(
            kind="news", job_id=job_id, worker_id="c1", attempt=1,
            redrive_generation=0, now=NOW, result_checksum="a" * 64,
        ) is False
        assert ledger.heartbeat_job(
            kind="news", job_id=job_id, worker_id="c1", attempt=1,
            redrive_generation=0, now=NOW, lease_seconds=600,
        ) is False
        assert db.jobs[("news", job_id)]["status"] == "CLAIMED"
        # 새 세대의 보고는 그대로 통과한다
        assert ledger.succeed_job(
            kind="news", job_id=job_id, worker_id="c1", attempt=1,
            redrive_generation=1, now=NOW, result_checksum="c" * 64,
        ) is True
