"""1분 window 의 fenced commit transaction + orphan 검출 (ALPHA-666, 계획 §8 후반부).

v0.7 9절 순서의 DB 측이다 — S3 artifact/manifest PUT(artifacts.py)은 **호출자가 먼저**
끝내고, 여기는 한 트랜잭션에 다음을 묶는다:

    fence/phase 확인(session 행 잠금)
    → claim 검증(window 행 잠금)
    → canonical upsert (CanonicalWriter 경계 뒤)
    → window checksum/generation 확정 (_record_window_outcome_tx)
    → price job + PriceWindowCommitted outbox (_insert_*_tx)
    → commit

트랜잭션이 통째로 성공하거나 통째로 없던 일이 된다 — S3 성공/DB 실패의 잔재(orphan)는
`find_orphan_artifacts` 가 검출하고, 처리 정책(재사용/quarantine)은 EOD QC(PR 8) 소관.

canonical 분봉의 실제 스키마·경로는 ALPHA-648(price_bars) 확정 설계가 정본이다 —
여기서는 CanonicalWriter 프로토콜 뒤로 격리하고 재도출하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..config import DbConfig
from ..db import connect as _default_connect
from ..lake.storage import Storage, raw_price_minute_artifact_key
from .jobs import PRICE_EVENT_TYPE, JobLedger, build_event_id
from .models import KST
from .repository import MinuteLedger


class CanonicalWriter(Protocol):
    """canonical 적재 경계 — 실 구현은 ALPHA-648 price_bars writer 와 조율한다.

    upsert 는 자연키 멱등이어야 하고(재실행 no-op), 주어진 cursor 의 트랜잭션 안에서만
    쓴다(커넥션을 새로 열면 commit 원자성이 깨진다).
    """

    def upsert_tx(self, cur, *, dataset: str, window_start: datetime,
                  records: tuple[dict, ...]) -> int: ...


class CommitRejectedError(RuntimeError):
    """fence/claim 이 더 이상 유효하지 않다 — canonical 을 포함해 아무것도 커밋되지 않았다."""


@dataclass
class MinuteCommitter:
    """price window commit 경계. 뉴스는 PR 5 worker 가 같은 _tx 조각들로 자기 조합을 만든다."""

    db: DbConfig
    connect_fn: Callable = _default_connect

    def commit_price_window(
        self,
        *,
        session_id: str,
        window_start: datetime,
        worker_id: str,
        fence_token: int,
        claim_token: int,
        data_status: str,
        expected_unit_count: int,
        succeeded_unit_count: int,
        failed_unit_count: int,
        record_count: int,
        checksum: str,
        manifest_uri: str,
        manifest_checksum: str,
        missing_units: list[str] | None,
        stage_timestamps: dict[str, datetime | str],
        records: tuple[dict, ...],
        canonical_writer: CanonicalWriter,
        dataset: str,
        trigger_schema_version: str,
        destination: str,
    ) -> int:
        """한 트랜잭션에 canonical/window/job/outbox 를 확정하고 generation 을 돌려준다.

        멱등성은 아래에서 나온다 — 재실행 같은 checksum 이면 generation 불변 →
        같은 job_id/event_id → ON CONFLICT no-op(outbox 재발행 없음). correction 은
        generation+1 → 새 job/event 1개.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            phase = MinuteLedger._fenced_phase(cur, session_id, fence_token)
            if phase not in ("ACTIVE", "DRAINING"):
                raise CommitRejectedError(
                    f"fence 무효(phase={phase}) — session {session_id} commit 거부"
                )
            # claim 을 **쓰기 전에** 검증한다: _record_window_outcome_tx 의 거부는
            # canonical upsert 뒤라서, 먼저 확인해야 rollback 없이도 fake/실DB 상태가
            # 갈리지 않는다 (실DB 는 예외 시 rollback, fake 는 트랜잭션이 없다)
            cur.execute(
                """
                SELECT claimed_by, claim_token FROM minute_ingestion_window
                WHERE session_id = %s AND window_start = %s
                FOR UPDATE
                """,
                (session_id, window_start),
            )
            row = cur.fetchone()
            if row is None or row[0] != worker_id or row[1] != claim_token:
                raise CommitRejectedError(
                    f"claim 무효(window {window_start}) — 다른 attempt 의 소유다"
                )
            canonical_writer.upsert_tx(
                cur, dataset=dataset, window_start=window_start, records=records,
            )
            generation = MinuteLedger._record_window_outcome_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, claim_token=claim_token, data_status=data_status,
                expected_unit_count=expected_unit_count,
                succeeded_unit_count=succeeded_unit_count,
                failed_unit_count=failed_unit_count, record_count=record_count,
                checksum=checksum, manifest_uri=manifest_uri,
                manifest_checksum=manifest_checksum, missing_units=missing_units,
                stage_timestamps=stage_timestamps,
            )
            if generation is None:
                # 위 FOR UPDATE 검증을 통과했으면 도달 불가 — 도달했다면 버그다
                raise CommitRejectedError("window 갱신 실패 — claim 검증과 모순")
            job_id, _ = JobLedger._insert_price_job_tx(
                cur, session_id=session_id, window_start=window_start,
                generation=generation, trigger_schema_version=trigger_schema_version,
            )
            JobLedger._insert_outbox_tx(
                cur,
                event_id=build_event_id(PRICE_EVENT_TYPE, job_id),
                event_type=PRICE_EVENT_TYPE, destination=destination,
                aggregate_id=job_id, generation=generation,
                payload={"job_id": job_id, "session_id": session_id,
                         "window_start": window_start, "generation": generation},
            )
            return generation


def find_orphan_artifacts(
    *,
    db: DbConfig,
    connect_fn: Callable,
    storage: Storage,
    session_id: str,
    source: str,
    market: str,
    session_date: str,
) -> list[str]:
    """S3 에는 있는데 window 원장에 대응 커밋이 없는 artifact 키 목록 (v0.7 9절 복구 표).

    "S3 PUT 후 DB commit 전 종료"의 잔재를 나열만 한다 — 재사용(재실행이 같은 key 를
    다시 씀)이 기본 복구고, 남은 orphan 의 quarantine 정책은 EOD QC(PR 8)가 정한다.
    """
    with connect_fn(db) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT window_start, generation FROM minute_ingestion_window
            WHERE session_id = %s AND checksum IS NOT NULL
            """,
            (session_id,),
        )
        committed = {
            raw_price_minute_artifact_key(
                source, market, session_date,
                ws.astimezone(KST).strftime("%H%M"),  # key 의 HHMM 은 세션 로컬(KST) 축
                generation,
            )
            for ws, generation in cur.fetchall()
        }
    prefix = f"raw/source={source}/dataset=price_minute/market={market}/session_date={session_date}/"
    return sorted(
        key for key in storage.list_keys(prefix)
        if key.endswith("/bars.ndjson") and key not in committed
    )
