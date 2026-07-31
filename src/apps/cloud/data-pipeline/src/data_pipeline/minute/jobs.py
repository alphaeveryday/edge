"""1분 파이프라인 job/outbox repository + 결정적 ID (ALPHA-664, 계획 §7 PR 2C).

PostgreSQL 이 논리 job/retry 의 SSOT 이고 SQS 는 wake-up transport 다(v0.7 12.4 —
재비교 금지). 이 모듈은 그 계약의 DB 측 기반이다:

- 결정적 ID (v0.7 10.6): 고정 필드 순서 UTF-8 JSON array·UTC RFC3339 Z·lowercase
  sha256. **두 writer 는 반드시 이 함수를 공유한다** — 각자 구현하면 구분자 하나로
  같은 자연키에 다른 ID 가 나온다(db.stable_domain_id 와 같은 원칙).
- 뉴스/가격 job 은 identity 가 한 컬럼도 겹치지 않아 테이블이 분리돼 있고(v0.7 10.5),
  lifecycle(claim·retry·dead)은 여기서 **파라미터화로만** 공유한다.
- stale 거부는 price claim 시점 한 곳: job.generation < window 현재 generation 이면
  DEAD('STALE') CAS (v0.7 10.5).
- outbox 는 ON CONFLICT (event_id) DO NOTHING — 같은 논리 사건의 일반 재전달은 같은
  event_id, 수동 redrive 만 redrive_generation 을 올린다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import DbConfig
from ..db import connect as _default_connect
from .models import canonical_json

NEWS_EVENT_TYPE = "NewsExtractionRequested"
PRICE_EVENT_TYPE = "PriceWindowCommitted"

# lifecycle 을 공유하는 두 테이블 — dict 가 곧 허용 목록이라 SQL 조립에 안전하다
_JOB_TABLES = {
    "news": "news_extraction_job",
    "price": "price_window_job",
}


# ── 결정적 ID (v0.7 10.6 유도식) ──


def _sha256_of(parts: list) -> str:
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()


def news_job_id(
    *, source_code: str, article_id: str, input_fingerprint: str,
    tagger_version: str, ontology_version: str,
) -> str:
    return _sha256_of(
        [source_code, article_id, input_fingerprint, tagger_version, ontology_version]
    )


def price_job_id(
    *, session_id: str, window_start: datetime, generation: int, trigger_schema_version: str,
) -> str:
    return _sha256_of([session_id, window_start, generation, trigger_schema_version])


def build_event_id(event_type: str, job_id: str, redrive_generation: int = 0) -> str:
    """일반 재전달은 redrive_generation 0 으로 같은 ID — 수동 redrive 만 올린다."""
    if event_type not in (NEWS_EVENT_TYPE, PRICE_EVENT_TYPE):
        raise ValueError(f"event_type {event_type!r} 는 정의된 사건이 아니다")
    if redrive_generation < 0:
        raise ValueError("redrive_generation 은 0 이상이다")
    return f"{event_type}:{job_id}:{redrive_generation}"


@dataclass
class JobLedger:
    """job/outbox 원장 접근. connect_fn 은 테스트가 가짜 커넥션을 주입하는 이음매다.

    MinuteLedger 와 같은 결: 실행을 제어하므로 실패는 예외로 올린다(fail loud).
    """

    db: DbConfig
    connect_fn: Callable = _default_connect

    # ── job identity INSERT ───────────────────────────────────
    # _tx 조각과 public 래퍼로 나눈다(ops Ledger 관례) — commit transaction(PR 3)과
    # enqueue Task(PR 8)는 job+outbox 를 **한 트랜잭션**에 넣어야 한다. 각 메서드가
    # 커넥션을 따로 열면 job 커밋 직후 프로세스가 죽었을 때 wake-up event 가 영구
    # 유실된다(원자성은 enqueue_* 결합 메서드 또는 호출자의 cur 재사용으로 확보).

    @staticmethod
    def _insert_news_job_tx(
        cur, *, source_code, article_id, input_fingerprint, tagger_version, ontology_version,
    ) -> tuple[str, bool]:
        job_id = news_job_id(
            source_code=source_code, article_id=article_id,
            input_fingerprint=input_fingerprint, tagger_version=tagger_version,
            ontology_version=ontology_version,
        )
        cur.execute(
            """
            INSERT INTO news_extraction_job (
                job_id, source_code, article_id, input_fingerprint,
                tagger_version, ontology_version
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO NOTHING
            RETURNING job_id
            """,
            (job_id, source_code, article_id, input_fingerprint,
             tagger_version, ontology_version),
        )
        return job_id, cur.fetchone() is not None

    @staticmethod
    def _insert_price_job_tx(
        cur, *, session_id, window_start, generation, trigger_schema_version,
    ) -> tuple[str, bool]:
        job_id = price_job_id(
            session_id=session_id, window_start=window_start,
            generation=generation, trigger_schema_version=trigger_schema_version,
        )
        cur.execute(
            """
            INSERT INTO price_window_job (
                job_id, session_id, window_start, generation, trigger_schema_version
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO NOTHING
            RETURNING job_id
            """,
            (job_id, session_id, window_start, generation, trigger_schema_version),
        )
        return job_id, cur.fetchone() is not None

    @staticmethod
    def _insert_outbox_tx(
        cur, *, event_id, event_type, destination, aggregate_id, generation, payload,
    ) -> bool:
        cur.execute(
            """
            INSERT INTO dataset_commit_outbox (
                event_id, event_type, destination, aggregate_id, generation, payload
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            (event_id, event_type, destination, aggregate_id, generation,
             canonical_json(payload)),
        )
        return cur.fetchone() is not None

    def insert_news_job(self, **identity) -> tuple[str, bool]:
        """identity UNIQUE 충돌은 no-op — (job_id, created)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._insert_news_job_tx(cur, **identity)

    def insert_price_job(self, **identity) -> tuple[str, bool]:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._insert_price_job_tx(cur, **identity)

    def enqueue_news_job(
        self, *, destination: str, payload: dict, generation: int = 1, **identity,
    ) -> tuple[str, bool]:
        """job + wake-up event 를 **한 트랜잭션**에 INSERT — 사이에서 죽어도 유실 0.

        event 는 job 존재 여부와 무관하게 멱등 INSERT 한다(둘 다 ON CONFLICT no-op) —
        이전 시도가 job 만 남기고 죽었어도 재호출이 event 를 self-heal 한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            job_id, created = self._insert_news_job_tx(cur, **identity)
            self._insert_outbox_tx(
                cur,
                event_id=build_event_id(NEWS_EVENT_TYPE, job_id),
                event_type=NEWS_EVENT_TYPE, destination=destination,
                aggregate_id=job_id, generation=generation, payload=payload,
            )
            return job_id, created

    def enqueue_price_job(
        self, *, destination: str, payload: dict, session_id: str,
        window_start: datetime, generation: int, trigger_schema_version: str,
    ) -> tuple[str, bool]:
        """price job + PriceWindowCommitted event 를 한 트랜잭션에 — PR 3 commit 이
        canonical/window 갱신과 같은 트랜잭션에서 이 _tx 조각들을 재사용한다."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            job_id, created = self._insert_price_job_tx(
                cur, session_id=session_id, window_start=window_start,
                generation=generation, trigger_schema_version=trigger_schema_version,
            )
            self._insert_outbox_tx(
                cur,
                event_id=build_event_id(PRICE_EVENT_TYPE, job_id),
                event_type=PRICE_EVENT_TYPE, destination=destination,
                aggregate_id=job_id, generation=generation, payload=payload,
            )
            return job_id, created

    # ── job claim·전이 (lifecycle 공유 — 파라미터화) ───────────
    def claim_due_job(
        self, *, kind: str, worker_id: str, now: datetime, lease_seconds: int,
    ) -> dict | None:
        """eligible job 하나를 CAS 로 claim 한다. 없으면 None.

        eligible = PENDING / RETRY_WAIT+next_attempt_at 도달 / lease 만료 CLAIMED.
        retry 자격·시각의 권위는 이 테이블뿐이다 — SQS receive 는 wake-up 일 뿐(12.4).
        price 는 claim 직후 window 의 현재 generation 과 대조해 낮으면 DEAD('STALE')
        CAS 하고 None — correction commit 이 새 generation job 을 이미 만들었다.
        """
        table = _JOB_TABLES[kind]
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table} j
                SET status = 'CLAIMED', claimed_by = %s, lease_expires_at = %s,
                    attempt_count = j.attempt_count + 1, updated_at = now()
                WHERE j.job_id = (
                    SELECT c.job_id FROM {table} c
                    WHERE c.status = 'PENDING'
                       OR (c.status = 'RETRY_WAIT' AND c.next_attempt_at <= %s)
                       OR (c.status = 'CLAIMED' AND c.lease_expires_at < %s)
                    ORDER BY c.created_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING j.job_id, j.attempt_count
                """,
                (worker_id, now + timedelta(seconds=lease_seconds), now, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            job_id, attempt_count = row
            if kind == "price":
                cur.execute(
                    """
                    SELECT j.generation, w.generation
                    FROM price_window_job j
                    JOIN minute_ingestion_window w
                      ON w.session_id = j.session_id AND w.window_start = j.window_start
                    WHERE j.job_id = %s
                    FOR UPDATE OF w
                    """,
                    (job_id,),
                )
                # FOR UPDATE OF w — 비잠금 검사는 correction commit 과 TOCTOU 다
                # (2B 에서 확립한 원칙): 잠그면 이 tx 가 끝날 때까지 세대 증가가 블록된다
                job_generation, window_generation = cur.fetchone()
                if job_generation < window_generation:
                    # stale 거부는 여기 한 곳 — 낮은 세대 job 은 실행하지 않고 격리
                    cur.execute(
                        """
                        UPDATE price_window_job
                        SET status = 'DEAD', error_code = 'STALE',
                            completed_at = %s, claimed_by = NULL,
                            lease_expires_at = NULL, updated_at = now()
                        WHERE job_id = %s AND claimed_by = %s
                        """,
                        (now, job_id, worker_id),
                    )
                    return None
            return {"job_id": job_id, "attempt_count": attempt_count}

    def _transition(
        self, kind: str, job_id: str, worker_id: str, attempt: int, *, to_status: str,
        now: datetime, next_attempt_at: datetime | None = None,
        result_checksum: str | None = None, error_code: str | None = None,
    ) -> bool:
        """CLAIMED(내 claim) → terminal/RETRY_WAIT 전이. stale claim 이면 False.

        attempt 는 claim 이 돌려준 값이다 — worker_id 만 보면 같은 Worker 가 재claim 한
        뒤 옛 attempt 의 늦은 보고가 새 attempt 를 terminal 로 만든다(2B claim_token
        과 같은 결).
        """
        table = _JOB_TABLES[kind]
        completed = now if to_status in ("SUCCEEDED", "DEAD") else None
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET status = %s, next_attempt_at = %s, result_checksum = %s,
                    error_code = %s, completed_at = %s,
                    claimed_by = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE job_id = %s AND claimed_by = %s AND status = 'CLAIMED'
                  AND attempt_count = %s
                """,
                (to_status, next_attempt_at, result_checksum, error_code, completed,
                 job_id, worker_id, attempt),
            )
            return cur.rowcount == 1

    def succeed_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int,
        now: datetime, result_checksum: str,
    ) -> bool:
        return self._transition(
            kind, job_id, worker_id, attempt, to_status="SUCCEEDED", now=now,
            result_checksum=result_checksum,
        )

    def retry_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int, now: datetime,
        next_attempt_at: datetime, error_code: str,
    ) -> bool:
        """transient 실패 — DB 가 다음 시각을 정하고 Consumer 는 visibility 를 맞춘다."""
        if next_attempt_at <= now:
            raise ValueError("next_attempt_at 은 미래여야 한다 — 즉시 재시도는 RETRY_WAIT 이 아니다")
        return self._transition(
            kind, job_id, worker_id, attempt, to_status="RETRY_WAIT", now=now,
            next_attempt_at=next_attempt_at, error_code=error_code,
        )

    def dead_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int,
        now: datetime, error_code: str,
    ) -> bool:
        """permanent 실패/예산 소진 — 조용한 폐기가 아니라 조회 가능한 terminal 격리."""
        return self._transition(
            kind, job_id, worker_id, attempt, to_status="DEAD", now=now,
            error_code=error_code,
        )

    # ── outbox ────────────────────────────────────────────────
    def insert_outbox_event(self, **event) -> bool:
        """ON CONFLICT (event_id) DO NOTHING — 같은 논리 사건의 재삽입은 no-op."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._insert_outbox_tx(cur, **event)

    def claim_outbox_batch(
        self, *, relay_id: str, now: datetime, limit: int, lease_seconds: int,
    ) -> list[dict]:
        """미발행(NEW) event 를 batch claim 한다 — claim 은 status 가 아니라
        claimed_by/claim_expires_at 이다(Relay crash 가 CLAIMED 고착을 만들지 않게)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dataset_commit_outbox o
                SET claimed_by = %s, claim_expires_at = %s
                WHERE o.event_id IN (
                    SELECT c.event_id FROM dataset_commit_outbox c
                    WHERE c.status = 'NEW'
                      AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= %s)
                      AND (c.claimed_by IS NULL OR c.claim_expires_at < %s)
                    ORDER BY c.created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING o.event_id, o.event_type, o.destination, o.payload,
                          o.claim_expires_at
                """,
                (relay_id, now + timedelta(seconds=lease_seconds), now, now, limit),
            )
            return [
                {"event_id": r[0], "event_type": r[1], "destination": r[2],
                 "payload": r[3], "claim_token": r[4]}
                for r in cur.fetchall()
            ]

    def mark_published(
        self, *, event_id: str, relay_id: str, claim_token: datetime, now: datetime,
    ) -> bool:
        """claim_token(=claim 이 돌려준 claim_expires_at)까지 대조한다 — relay_id 만
        보면 같은 Relay 의 만료된 옛 attempt 가 새 claim 을 마감한다."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dataset_commit_outbox
                SET status = 'PUBLISHED', published_at = %s,
                    claimed_by = NULL, claim_expires_at = NULL
                WHERE event_id = %s AND claimed_by = %s AND status = 'NEW'
                  AND claim_expires_at = %s
                """,
                (now, event_id, relay_id, claim_token),
            )
            return cur.rowcount == 1

    def record_publish_failure(
        self, *, event_id: str, relay_id: str, claim_token: datetime, now: datetime,
        next_attempt_at: datetime | None, error: str, terminal: bool = False,
    ) -> bool:
        """발행 실패 기록 — transient 는 재시도 예약, 지속 실패는 DEAD 로 조회 가능 격리.

        transient 는 **미래** next_attempt_at 이 필수다 — 없으면 claim 해제 즉시 다시
        eligible 이 돼 같은 장애를 tight loop 로 두드린다."""
        if not terminal and (next_attempt_at is None or next_attempt_at <= now):
            raise ValueError("transient 실패는 미래 next_attempt_at 이 필요하다")
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dataset_commit_outbox
                SET status = %s, attempt_count = attempt_count + 1,
                    next_attempt_at = %s, last_error = %s,
                    claimed_by = NULL, claim_expires_at = NULL
                WHERE event_id = %s AND claimed_by = %s AND status = 'NEW'
                  AND claim_expires_at = %s
                """,
                ("DEAD" if terminal else "NEW", next_attempt_at, error, event_id,
                 relay_id, claim_token),
            )
            return cur.rowcount == 1
