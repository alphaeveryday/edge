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
- 메시지 기반 경로(ALPHA-672, Consumer 계약): fetch_job/claim_job/heartbeat_job 은
  "메시지가 지목한 job" 을 다루고, dead_on_dlq·redrive_job 이 DEAD 를 되돌리는 유일한
  장치다. redrive 는 job.redrive_generation 증가와 새 outbox event 를 **한 트랜잭션**
  에 넣는다(PR 2C 에서 미뤄둔 원자 동기화) — 갈리면 깨울 메시지 없는 job 이 남는다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..config import DbConfig
from ..db import connect as _default_connect
from .models import canonical_json

logger = logging.getLogger(__name__)

NEWS_EVENT_TYPE = "NewsExtractionRequested"
PRICE_EVENT_TYPE = "PriceWindowCommitted"

# lifecycle 을 공유하는 두 테이블 — dict 가 곧 허용 목록이라 SQL 조립에 안전하다
_JOB_TABLES = {
    "news": "news_extraction_job",
    "price": "price_window_job",
}

# kind ↔ event_type. 두 축은 1:1 이다 — 큐에서 온 메시지의 event_type 으로 어느 job
# 테이블을 볼지 정하고(consumer), redrive 는 반대로 kind 에서 event_id 를 짓는다.
JOB_EVENT_TYPES = {"news": NEWS_EVENT_TYPE, "price": PRICE_EVENT_TYPE}
EVENT_TYPE_JOB_KINDS = {event: kind for kind, event in JOB_EVENT_TYPES.items()}

# 큐마다 실리는 사건의 종류. 큐는 가격·뉴스를 공유하지 않는다(v0.7 12.1)는 계약을
# 발행(Relay)·대사(DLQ reconciler)·복구(redrive)가 **같은 한 곳**을 보고 대조한다.
DESTINATION_JOB_KINDS = {
    "price-analysis-realtime": "price",
    "news-extraction-realtime": "news",
    "news-extraction-backfill": "news",
}

# 논리 job 이 non-terminal 인데 메시지가 DLQ 에 도착했을 때의 사유(v0.7 12.4).
# transport 예산(maxReceiveCount)이 먼저 소진된 것이라 job 자체의 결함과 구분한다.
DLQ_ERROR_CODE = "SQS_MAX_RECEIVE"


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

    def insert_news_job(
        self, *, source_code: str, article_id: str, input_fingerprint: str,
        tagger_version: str, ontology_version: str,
    ) -> tuple[str, bool]:
        """identity UNIQUE 충돌은 no-op — (job_id, created)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._insert_news_job_tx(
                cur, source_code=source_code, article_id=article_id,
                input_fingerprint=input_fingerprint, tagger_version=tagger_version,
                ontology_version=ontology_version,
            )

    def insert_price_job(
        self, *, session_id: str, window_start: datetime, generation: int,
        trigger_schema_version: str,
    ) -> tuple[str, bool]:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._insert_price_job_tx(
                cur, session_id=session_id, window_start=window_start,
                generation=generation, trigger_schema_version=trigger_schema_version,
            )

    def enqueue_news_job(
        self, *, destination: str, payload: dict, source_code: str, article_id: str,
        input_fingerprint: str, tagger_version: str, ontology_version: str,
        generation: int = 1,
    ) -> tuple[str, bool]:
        """job + wake-up event 를 **한 트랜잭션**에 INSERT — 사이에서 죽어도 유실 0.

        event 는 job 존재 여부와 무관하게 멱등 INSERT 한다(둘 다 ON CONFLICT no-op) —
        이전 시도가 job 만 남기고 죽었어도 재호출이 event 를 self-heal 한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            job_id, created = self._insert_news_job_tx(
                cur, source_code=source_code, article_id=article_id,
                input_fingerprint=input_fingerprint, tagger_version=tagger_version,
                ontology_version=ontology_version,
            )
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
            # stale 정리 후 다음 후보를 이어서 본다 — 정리 한 건에 None 을 돌려주면
            # 호출자가 "없음"으로 읽고 뒤의 eligible job 이 wake-up 없이 고착된다.
            # 각 반복이 job 하나를 소비(DEAD)하므로 루프는 유한하다.
            while True:
                claimed = self._claim_one(cur, table, kind, worker_id, now, lease_seconds)
                if claimed is None or claimed != "STALE":
                    return claimed

    def _claim_one(self, cur, table, kind, worker_id, now, lease_seconds):
        """한 건 claim 시도 — dict(성공)/None(후보 없음)/"STALE"(정리했으니 계속)."""
        cur.execute(
            f"""
            UPDATE {table} j
            SET status = 'CLAIMED', claimed_by = %s, lease_expires_at = %s,
                attempt_count = j.attempt_count + 1, updated_at = now()
            WHERE j.job_id = (
                SELECT c.job_id FROM {table} c
                WHERE c.status = 'PENDING'
                   OR (c.status = 'RETRY_WAIT' AND c.next_attempt_at <= %s)
                   OR (c.status = 'CLAIMED' AND (c.lease_expires_at IS NULL
                                                 OR c.lease_expires_at < %s))
                ORDER BY c.created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING j.job_id, j.attempt_count, j.redrive_generation
            """,
            (worker_id, now + timedelta(seconds=lease_seconds), now, now),
        )
        row = cur.fetchone()
        if row is None:
            return None
        job_id, attempt_count, redrive_generation = row
        if kind == "price" and self._reject_if_stale(cur, job_id, worker_id, now):
            return "STALE"
        # redrive_generation 을 함께 돌려준다 — 전이 CAS 가 이 값을 fence 로 쓴다.
        return {"job_id": job_id, "attempt_count": attempt_count,
                "redrive_generation": redrive_generation}

    @staticmethod
    def _reject_if_stale(cur, job_id: str, worker_id: str, now: datetime) -> bool:
        """claim 한 price job 이 stale 이면 DEAD('STALE') 로 격리한다. 격리했으면 True.

        stale 거부는 **claim 시점 한 곳**이다(v0.7 10.5) — 그래서 claim 하는 두 경로
        (폴링 claim_due_job·메시지 기반 claim_job)가 이 검사를 공유한다. 한쪽에만
        두면 그 쪽으로 들어온 낮은 세대 job 이 이미 정정된 window 를 다시 설명한다.
        """
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
        if job_generation >= window_generation:
            return False
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
        return True

    def _transition(
        self, kind: str, job_id: str, worker_id: str, attempt: int,
        redrive_generation: int, *, to_status: str,
        now: datetime, next_attempt_at: datetime | None = None,
        result_checksum: str | None = None, error_code: str | None = None,
    ) -> bool:
        """CLAIMED(내 claim) → terminal/RETRY_WAIT 전이. stale claim 이면 False.

        attempt 는 claim 이 돌려준 값이다 — worker_id 만 보면 같은 Worker 가 재claim 한
        뒤 옛 attempt 의 늦은 보고가 새 attempt 를 terminal 로 만든다(2B claim_token
        과 같은 결).

        ⚠️ `redrive_generation` 도 함께 fence 한다. attempt 만으로는 부족하다 —
        lease 만료 뒤에도 살아 있는 옛 실행이 있는 사이 redrive 가 attempt_count 를
        0 으로 되돌리면, 새 세대의 첫 claim 이 **같은 attempt 번호**를 갖는다. 그러면
        옛 실행의 늦은 보고가 새 세대를 마감해 운영자의 redrive 가 통째로 사라진다.
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
                  AND attempt_count = %s AND redrive_generation = %s
                """,
                (to_status, next_attempt_at, result_checksum, error_code, completed,
                 job_id, worker_id, attempt, redrive_generation),
            )
            return cur.rowcount == 1

    def succeed_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int,
        redrive_generation: int, now: datetime, result_checksum: str,
    ) -> bool:
        return self._transition(
            kind, job_id, worker_id, attempt, redrive_generation,
            to_status="SUCCEEDED", now=now, result_checksum=result_checksum,
        )

    def retry_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int,
        redrive_generation: int, now: datetime,
        next_attempt_at: datetime, error_code: str,
    ) -> bool:
        """transient 실패 — DB 가 다음 시각을 정하고 Consumer 는 visibility 를 맞춘다."""
        if next_attempt_at <= now:
            raise ValueError("next_attempt_at 은 미래여야 한다 — 즉시 재시도는 RETRY_WAIT 이 아니다")
        return self._transition(
            kind, job_id, worker_id, attempt, redrive_generation,
            to_status="RETRY_WAIT", now=now,
            next_attempt_at=next_attempt_at, error_code=error_code,
        )

    def dead_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int,
        redrive_generation: int, now: datetime, error_code: str,
    ) -> bool:
        """permanent 실패/예산 소진 — 조용한 폐기가 아니라 조회 가능한 terminal 격리."""
        return self._transition(
            kind, job_id, worker_id, attempt, redrive_generation,
            to_status="DEAD", now=now, error_code=error_code,
        )

    # ── 메시지 기반 경로 (Consumer 계약 v0.7 12.4) ─────────────
    def fetch_job(self, *, kind: str, job_id: str) -> dict | None:
        """job 상태 조회 — 실행 전 판정용(terminal 이면 실행 없이 ack, 이른 RETRY_WAIT
        이면 visibility 만 조정). **이 읽기로 상태를 확정하지 않는다** — 실행 자격은
        claim_job 의 CAS 가 정한다(비잠금 읽기는 그 자체로 TOCTOU).
        """
        table = _JOB_TABLES[kind]
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT status, next_attempt_at, redrive_generation, attempt_count
                FROM {table} WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {"status": row[0], "next_attempt_at": row[1],
                    "redrive_generation": row[2], "attempt_count": row[3]}

    def claim_job(
        self, *, kind: str, job_id: str, redrive_generation: int, worker_id: str,
        now: datetime, lease_seconds: int,
    ) -> dict | None | str:
        """메시지가 지목한 job 하나를 CAS 로 claim 한다 — dict/None/"STALE".

        `claim_due_job` 이 "아무 eligible job 하나"를 고르는 폴링용이라면 이쪽은
        Consumer 용이다: 무엇을 할지는 메시지가 이미 정했고 DB 는 **자격만** 판정한다.

        `redrive_generation` 을 WHERE 에 바인딩하는 이유: 상태 읽기와 claim 사이에
        redrive 가 끼면(DEAD→RETRY_WAIT + generation 증가) 옛 세대 메시지가 새 세대의
        job 을 집어 실행하고 SUCCEEDED 로 마감한다. 그러면 redrive 가 만든 새 메시지는
        SUCCEEDED 를 보고 삭제돼 **운영자의 redrive 가 통째로 사라진다**.
        """
        table = _JOB_TABLES[kind]
        # lease 가 NULL 인 CLAIMED 도 회수 대상이다 — NULL 비교는 참이 안 되므로 빼
        # 두면 그 행은 어떤 경로로도 못 집고 DLQ 대사도 못 죽여 영구 고착된다
        # (복원·구 writer 가 남길 수 있는 형상).
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET status = 'CLAIMED', claimed_by = %s, lease_expires_at = %s,
                    attempt_count = attempt_count + 1, updated_at = now()
                WHERE job_id = %s AND redrive_generation = %s
                  AND (status = 'PENDING'
                       OR (status = 'RETRY_WAIT' AND next_attempt_at <= %s)
                       OR (status = 'CLAIMED' AND (lease_expires_at IS NULL
                                                   OR lease_expires_at < %s)))
                RETURNING attempt_count
                """,
                (worker_id, now + timedelta(seconds=lease_seconds), job_id,
                 redrive_generation, now, now),
            )
            row = cur.fetchone()
            if row is None:
                return None
            if kind == "price" and self._reject_if_stale(cur, job_id, worker_id, now):
                return "STALE"
            return {"job_id": job_id, "attempt_count": row[0],
                    "redrive_generation": redrive_generation}

    def news_job_identity(self, *, job_id: str) -> dict | None:
        """job 이 **생성 시점에 고정한** 정체성 — 없으면 None (ALPHA-689).

        handler 가 이걸 읽어야 하는 이유: 실행 코드로 job_id 를 재계산해 비교하면
        불일치의 원인 두 가지가 한 값으로 뭉개진다 — ①태거·온톨로지 버전을 올린 뒤
        큐에 남은 **정상 backlog**(실행해야 한다)와 ②payload 가 다른 기사를 가리키는
        **결함**(실행하면 안 된다). 어느 쪽인지는 job 행이 선언한 값과 대조해야만 갈린다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_code, article_id, input_fingerprint,
                       tagger_version, ontology_version
                FROM news_extraction_job WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("source_code", "article_id", "input_fingerprint",
             "tagger_version", "ontology_version"), row, strict=True,
        ))

    def price_job_identity(self, *, job_id: str) -> dict | None:
        """price job 이 생성 시점에 고정한 정체성 — 없으면 None (ALPHA-708).

        news_job_identity 와 같은 이유: payload 만 믿으면 정상 경로와 "다른 window 를
        가리키는 결함"이 구분되지 않는다. payload 와 job 행은 commit_price_window 가
        같은 트랜잭션에서 같은 값으로 쓰므로, 어긋남은 재시도로 낫지 않는다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id, window_start, generation, trigger_schema_version
                FROM price_window_job WHERE job_id = %s
                """,
                (job_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("session_id", "window_start", "generation", "trigger_schema_version"),
            row, strict=True,
        ))

    def heartbeat_job(
        self, *, kind: str, job_id: str, worker_id: str, attempt: int,
        redrive_generation: int, now: datetime, lease_seconds: int,
    ) -> bool:
        """실행 중 job 의 lease 를 연장한다. 내 claim 이 아니면 False.

        SQS visibility 만 연장하고 DB lease 를 두면, 처리 시간이 lease 를 넘는 순간
        그 job 이 다른 Consumer 에게 eligible 로 보여 같은 LLM 호출이 중복된다 —
        lease 는 "지금 누가 하고 있다"의 유일한 근거다.
        """
        table = _JOB_TABLES[kind]
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table} SET lease_expires_at = %s, updated_at = now()
                WHERE job_id = %s AND claimed_by = %s AND status = 'CLAIMED'
                  AND attempt_count = %s AND redrive_generation = %s
                """,
                (now + timedelta(seconds=lease_seconds), job_id, worker_id, attempt,
                 redrive_generation),
            )
            return cur.rowcount == 1

    def dead_on_dlq(
        self, *, kind: str, job_id: str, redrive_generation: int, now: datetime,
    ) -> bool:
        """DLQ 도착 + DB non-terminal → `SQS_MAX_RECEIVE` 사유 DEAD CAS (v0.7 12.4).

        세 가드가 다 있어야 한다:
        - `redrive_generation` 일치 — 옛 세대 메시지가 뒤늦게 DLQ 에 닿았을 뿐인데
          운영자가 방금 redrive 한 새 세대를 죽이면 redrive 가 무효가 된다.
        - non-terminal 만 — SUCCEEDED 를 DEAD 로 덮으면 끝난 일이 실패로 뒤집힌다.
        - **살아 있는 lease 를 가진 CLAIMED 는 건드리지 않는다** — 지금 누가 실행
          중이라는 뜻이고, 그 결과는 곧 기록된다. DLQ 도착은 transport 사정이지 그
          job 이 죽었다는 근거가 아니다. 재조정은 반복 실행되므로 lease 가 만료되면
          다음 회차가 수렴시킨다(관측 안 됨을 없음으로 읽지 않는다).
        """
        table = _JOB_TABLES[kind]
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET status = 'DEAD', error_code = %s, completed_at = %s,
                    claimed_by = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE job_id = %s AND redrive_generation = %s
                  AND status IN ('PENDING', 'RETRY_WAIT', 'CLAIMED')
                  AND (status <> 'CLAIMED'
                       OR lease_expires_at IS NULL OR lease_expires_at < %s)
                """,
                (DLQ_ERROR_CODE, now, job_id, redrive_generation, now),
            )
            return cur.rowcount == 1

    def redrive_job(
        self, *, kind: str, job_id: str, now: datetime, actor: str, reason: str,
        destination: str | None = None,
    ) -> str:
        """막힌 job 을 한 트랜잭션에서 다시 깨운다 — 새 delivery event_id 를 돌려준다.

        v0.7 12.4: 콘솔에서 메시지만 blind redrive 하지 않는다. **DB 가 먼저**다 —
        `DEAD → RETRY_WAIT`, `redrive_generation` 증가, 증가한 세대로 만든 새 outbox
        event INSERT 가 같은 트랜잭션이라 "job 은 살아났는데 깨울 메시지가 없다"
        (또는 그 반대)가 생기지 않는다. 기존 DLQ 메시지는 옮기지 않는다 — 그 메시지의
        세대는 이미 낡아서 Consumer 가 superseded 로 버린다.

        payload 는 **직전 event 에서 복사**한다. 지어내면 그 job 을 만든 commit 이 실은
        뭘 보냈는지와 갈리고, 그건 아무도 대조하지 않는다. destination 도 기본은 복사이나
        `destination` 인자로 **운영자가 고칠 수 있다** — 배선이 어긋난 채 커밋된 행은
        그 값이 컬럼에 박혀 있어(결정적 event_id + ON CONFLICT DO NOTHING 이라 producer 를
        고쳐 재실행해도 그 행은 안 바뀐다) 여기서 바로잡지 않으면 복구 경로가 아예 없다.

        대상은 **"막힌 것 전부"** 다 — DEAD job(Consumer 가 포기)뿐 아니라 job 은
        멀쩡한데 delivery event 가 DEAD 인 것(Relay 가 그 event 를 격리)까지다. PR 6 은
        outbox DEAD 를 좁게 판정하면서 복구를 이 PR 에 위임했는데, job 상태만 보면
        그쪽은 여전히 영구 고착이다(Relay 는 NEW 만 집는다).

        거절은 둘: **SUCCEEDED**(다시 할 일이 없다)와 **살아 있는 lease 의 CLAIMED**
        (지금 누가 돌고 있고, 세대를 올리면 그 실행의 결과가 fence 에 걸려 버려진다 —
        lease 가 만료되면 그때 하면 된다).

        감사 기록: 누가(`actor`)·왜(`reason`) 했는지를 **대체되는 event 행의
        `last_error`** 에 **덧붙인다**(덮어쓰지 않는다 — Relay 가 왜 그 event 를
        포기했는지가 거기 유일하게 남아 있고, 그게 redrive 판단의 근거였다).
        같은 트랜잭션이라 전이와 갈리지 않고, 그 행은 이후 아무도 건드리지 않는다. DEAD 사유(`error_code`)도 덮지
        않아 무엇을 되살렸는지가 남는다. ⚠️ 천장: 전용 감사 테이블이 아니라 여러 번의
        redrive 이력이 쌓이지는 않는다(세대마다 행이 하나씩 생기므로 세대별로는 남는다).
        """
        table = _JOB_TABLES[kind]
        event_type = JOB_EVENT_TYPES[kind]
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT status, redrive_generation, lease_expires_at, error_code
                FROM {table} WHERE job_id = %s FOR UPDATE
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError(f"{kind} job 이 없다: {job_id}")
            status, generation, lease_expires_at, error_code = row
            if not actor.strip() or not reason.strip():
                # 되돌리기 어려운 수동 개입이다 — 근거 없는 실행을 원장에 남기지 않는다
                raise ValueError("redrive 는 actor 와 reason 이 필요하다")
            if status == "SUCCEEDED":
                raise ValueError(f"{job_id} 는 이미 SUCCEEDED 다 — 다시 깨울 일이 없다")
            if error_code == "STALE":
                # 세대가 지난 window 의 job 이다(v0.7 10.5). 되살려도 claim 이 같은
                # 비교에서 다시 DEAD 로 보내므로, terminal 이던 행을 성공할 수 없는
                # non-terminal 로 바꿔 놓기만 한다 — 그 window 의 일은 correction
                # commit 이 만든 새 세대 job 이 이미 맡고 있다.
                raise ValueError(
                    f"{job_id} 는 STALE 이다 — 새 세대 job 이 대신하므로 redrive 대상이 아니다"
                )
            if status == "CLAIMED" and (
                lease_expires_at is not None and lease_expires_at >= now
            ):
                raise ValueError(
                    f"{job_id} 는 실행 중이다(lease {lease_expires_at.isoformat()}) — "
                    "지금 세대를 올리면 그 실행의 결과가 버려진다"
                )
            previous_event_id = build_event_id(event_type, job_id, generation)
            cur.execute(
                """
                SELECT destination, generation, payload, status
                FROM dataset_commit_outbox WHERE event_id = %s
                """,
                (previous_event_id,),
            )
            previous = cur.fetchone()
            if previous is None:
                # payload 없이 event 를 만들면 Consumer 가 계약 위반으로 죽는다.
                raise LookupError(
                    f"직전 delivery event 가 없다: {previous_event_id} — "
                    "payload 를 복원할 근거가 없어 redrive 를 중단한다"
                )
            previous_destination, data_generation, payload, event_status = previous
            if status != "DEAD" and event_status != "DEAD":
                # **막혔다는 근거**가 없다. 정상 진행 중인 job 의 세대를 올리면 지금
                # 큐에 있는 배달이 superseded 로 버려지고 재시도 예산까지 초기화된다 —
                # 되살리는 명령이 오히려 멀쩡한 진행을 되돌린다.
                raise ValueError(
                    f"{job_id} 는 막혀 있지 않다(job={status}, event={event_status}) — "
                    "redrive 대상은 DEAD job 이거나 DEAD delivery event 다"
                )
            # destination 은 복사가 기본이고, 운영자가 준 값이 있으면 그걸 쓴다.
            # 어느 쪽이든 **사건 종류와 맞아야** 한다 — 어긋난 값으로 새 event 를 만들면
            # Relay 가 곧장 다시 격리해 세대만 오른다.
            target = destination or previous_destination
            if DESTINATION_JOB_KINDS.get(target) != EVENT_TYPE_JOB_KINDS.get(event_type):
                raise ValueError(
                    f"destination({target})이 event_type({event_type})과 어긋난다 — "
                    f"올바른 큐를 `destination` 으로 지정하라"
                    f"(가능: {sorted(d for d, k in DESTINATION_JOB_KINDS.items() if k == kind)})"
                )
            if event_status == "DEAD":
                # Relay 가 "이 event 는 못 나간다"고 판정했던 것이다(미정의 destination·
                # 크기 초과·본문 결함). 원인을 고치지 않았으면 같은 destination·payload
                # 로 만든 새 event 도 같은 이유로 다시 DEAD 가 된다.
                logger.warning(
                    "직전 event %s 는 Relay 가 발행 불가로 판정한 것이다 — 원인(설정·"
                    "크기·본문)을 고쳤는지 확인하라", previous_event_id,
                )
            # attempt_count 를 0 으로 되돌린다 — 운영자가 명시적으로 다시 시도하라고
            # 한 것이라 예산도 새로 준다. 소진된 채로 두면 첫 실패에 다시 DEAD 가 돼
            # redrive 가 사실상 아무것도 하지 않는다.
            # error_code 는 **덮지 않는다** — 왜 죽었는지가 유일한 조회 근거다.
            cur.execute(
                f"""
                UPDATE {table}
                SET status = 'RETRY_WAIT', redrive_generation = redrive_generation + 1,
                    next_attempt_at = %s, attempt_count = 0, completed_at = NULL,
                    claimed_by = NULL, lease_expires_at = NULL, updated_at = now()
                WHERE job_id = %s AND status = %s AND redrive_generation = %s
                """,
                (now, job_id, status, generation),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"redrive CAS 거부 — {job_id} 의 상태가 그새 바뀌었다")
            # 복사한 payload 가 **그 자체로** 발행 불가면 새 event 도 곧장 DEAD 다 —
            # 세대만 오르고 job 은 계속 안 나간다. 크기는 여기서 결정적으로 검사할 수
            # 있으니(엔벨로프까지 같은 함수로 만든다) 만들기 전에 막는다. 고칠 곳은
            # **쓰는 쪽의 크기 계약**이고, 그 job 은 줄어든 payload 로 다시 커밋돼야 한다.
            # 지연 import: relay 가 이 모듈을 import 하므로 최상단이면 순환이다.
            from .relay import SQS_MAX_MESSAGE_BYTES, build_message_body

            event_id = build_event_id(event_type, job_id, generation + 1)
            body_bytes = len(
                build_message_body({
                    "event_id": event_id, "event_type": event_type, "payload": payload,
                }).encode("utf-8")
            )
            if body_bytes > SQS_MAX_MESSAGE_BYTES:
                raise ValueError(
                    f"payload 가 SQS 상한을 넘는다({body_bytes}B > "
                    f"{SQS_MAX_MESSAGE_BYTES}B) — 복사해도 같은 이유로 다시 DEAD 다. "
                    "쓰는 쪽 크기 계약으로 고치고 그 job 을 다시 커밋해야 한다"
                )
            if not self._insert_outbox_tx(
                cur, event_id=event_id, event_type=event_type, destination=target,
                aggregate_id=job_id, generation=data_generation, payload=payload,
            ):
                # 같은 세대의 event 가 이미 있다 = 이 세대를 만든 redrive 가 이미
                # 있었다는 뜻이다. job 쪽 CAS 가 통과했는데 여기서 충돌하는 건 두 값이
                # 어긋났다는 신호라 조용히 넘기지 않는다(트랜잭션 전체가 롤백된다).
                raise RuntimeError(
                    f"redrive event 가 이미 있다: {event_id} — "
                    "job.redrive_generation 과 outbox 가 어긋났다"
                )
            cur.execute(
                """
                UPDATE dataset_commit_outbox
                SET last_error = concat_ws(' | ', last_error, %s)
                WHERE event_id = %s
                """,
                (f"redrive by {actor} at {now.isoformat()}: {reason}", previous_event_id),
            )
        # 로그는 **커밋 뒤**다 — 트랜잭션 안에서 찍으면 롤백된 redrive 의 기록만 남는다
        logger.warning(
            "redrive: %s job %s (%s, 세대 %d→%d, → %s) → %s [%s: %s]",
            kind, job_id, status, generation, generation + 1, target, event_id,
            actor, reason,
        )
        return event_id

    # ── outbox ────────────────────────────────────────────────
    def insert_outbox_event(
        self, *, event_id: str, event_type: str, destination: str,
        aggregate_id: str, generation: int, payload: dict,
    ) -> bool:
        """ON CONFLICT (event_id) DO NOTHING — 같은 논리 사건의 재삽입은 no-op."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            return self._insert_outbox_tx(
                cur, event_id=event_id, event_type=event_type, destination=destination,
                aggregate_id=aggregate_id, generation=generation, payload=payload,
            )

    def claim_outbox_batch(
        self, *, relay_id: str, now: datetime, limit: int, lease_seconds: int,
        destination: str | None = None,
        exclude_destinations: tuple[str, ...] = (),
    ) -> list[dict]:
        """미발행(NEW) event 를 batch claim 한다 — claim 은 status 가 아니라
        claimed_by/claim_expires_at 이다(Relay crash 가 CLAIMED 고착을 만들지 않게).

        `destination` 을 주면 그 큐의 event 만 집는다. **Relay 는 destination 별로 나눠
        claim 한다** — 전역 FIFO 로 집으면 장애 난 레인의 오래된 재시도 행이 매 batch 를
        채워 멀쩡한 레인이 계속 밀린다. 가격 장애가 뉴스 발행을 막지 않는다는 게 공용
        Relay 를 쓰는 근거였다(v0.7 11.1) — 그 근거가 claim 층에서 무너진다.

        `exclude_destinations` 는 그 나머지를 쓸어 담는 반대편이다(설정에 없는 큐로 쓰인
        행 — 안 집으면 영원히 NEW 로 남아 아무도 모른다). **집을 것만 집어야 한다** —
        전역으로 집은 뒤 골라내면 처리하지 않을 행까지 lease 로 묶어 그만큼 지연된다.
        """
        if destination is not None and exclude_destinations:
            raise ValueError("destination 과 exclude_destinations 는 함께 쓸 수 없다")
        destination_filter = ""
        destination_params: tuple = ()
        if destination is not None:
            destination_filter = " AND c.destination = %s"
            destination_params = (destination,)
        elif exclude_destinations:
            destination_filter = " AND NOT (c.destination = ANY(%s))"
            destination_params = (sorted(exclude_destinations),)
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE dataset_commit_outbox o
                SET claimed_by = %s, claim_expires_at = %s
                WHERE o.event_id IN (
                    SELECT c.event_id FROM dataset_commit_outbox c
                    WHERE c.status = 'NEW'
                      AND (c.next_attempt_at IS NULL OR c.next_attempt_at <= %s)
                      AND (c.claimed_by IS NULL OR c.claim_expires_at < %s)
                      {destination_filter}
                    ORDER BY c.created_at
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING o.event_id, o.event_type, o.destination, o.payload,
                          o.claim_expires_at, o.attempt_count
                """,
                (relay_id, now + timedelta(seconds=lease_seconds), now, now,
                 *destination_params, limit),
            )
            # attempt_count 는 Relay 의 지수 backoff 재료다 — 이 값 없이는 재시도 간격을
            # 시도 횟수에 따라 벌릴 수 없다(ALPHA-670). 시도 횟수로 DEAD 를 만들지는
            # 않는다 — terminal 은 "재시도해도 같은 결과"일 때만이다.
            return [
                {"event_id": r[0], "event_type": r[1], "destination": r[2],
                 "payload": r[3], "claim_token": r[4], "attempt_count": r[5]}
                for r in cur.fetchall()
            ]

    def count_unpublished(self) -> dict[str, int]:
        """발행되지 않은 outbox event 수를 상태별로 — {"NEW": n, "DEAD": n}.

        배출 게이트(`relay --max-ticks`)가 "다 나갔나"를 판정하는 근거다. claim 결과가
        비었다는 것(IDLE)은 **지금 집을 게 없다**는 뜻일 뿐이다 — next_attempt_at 이
        미래인 행이나 다른 Relay 가 쥔 행은 claim 에 안 잡히므로, IDLE 을 완료로 읽으면
        남은 event 를 두고 성공으로 끝난다(Rule 12).

        **DEAD 도 미발행이다** — 격리됐을 뿐 큐에 나간 적이 없다. 빼고 세면 격리분을
        남긴 채 배출 성공으로 보고하게 된다. 둘을 나눠 돌려주는 이유는 조치가 달라서다:
        NEW 는 기다리면 나가고, DEAD 는 사람이 봐야 한다(redrive).

        단 **더 새 delivery 가 있는 DEAD 는 제외**한다(ALPHA-672) — redrive 가 만든 새
        세대 event 가 이미 그 일을 대신하기 때문이다. 안 빼면 복구를 끝내도 배출 게이트
        (`relay --max-ticks`)가 영원히 "미발행 남음"으로 실패한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.status, COUNT(*) FROM dataset_commit_outbox o
                WHERE o.status = 'NEW'
                   OR (o.status = 'DEAD' AND NOT EXISTS (
                        SELECT 1 FROM dataset_commit_outbox n
                        WHERE n.aggregate_id = o.aggregate_id
                          AND n.created_at > o.created_at
                   ))
                GROUP BY o.status
                """
            )
            counts = dict(cur.fetchall())
            return {"NEW": counts.get("NEW", 0), "DEAD": counts.get("DEAD", 0)}

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
        """발행 실패 기록 — transient 는 재시도 예약, terminal 은 DEAD 로 조회 가능 격리.

        ⚠️ terminal 판정의 주체는 **호출자(Relay)** 이고, 기준은 "재시도해도 결과가 같은가"
        다 — 미정의 destination·크기 초과·메시지 본문 결함처럼 **그 event 자체가 못 나가는**
        경우만이다. SenderFault 는 단독 근거가 아니다(잘못된 큐 URL·권한 오류도 SenderFault
        인데 그건 배포로 고쳐진다). 일시 실패를 시도 횟수로 DEAD 로 바꾸지도 않는다 —
        몇 분짜리 큐 장애가 되돌릴 수 없는 유실이 된다(ALPHA-670).

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
