"""1분 파이프라인 session/window repository (ALPHA-662, 계획 §7 PR 2B 전반부).

`db.py`(DB 접속 SSOT)를 재사용하고 커넥션은 lazy 다. ops Ledger(관측만)와 달리 이 원장은
실행을 **제어**한다 — 쓰기 실패를 삼키면 Worker 가 유령 상태로 진행하므로 예외를 그대로
올린다(fail loud). 그래서 ops 의 bounded-backoff·bool 반환 정책을 복제하지 않는다.

멱등·경합 근거:
  minute_ingestion_session  UNIQUE (dataset, source_group, session_date) — 슬롯 1회 계획.
                            session_id 는 자연키에서 결정적 파생(stable_domain_id) —
                            Planner 재기동이 같은 id 를 만든다.
  minute_ingestion_window   PK (session_id, window_start) ON CONFLICT DO NOTHING —
                            장 시작 시 하루치 미리 materialize(안 뜨면 MISSING 으로 관측).
  claim                     FOR UPDATE SKIP LOCKED + lease — 동시 claim winner 1,
                            lease 만료된 CLAIMED 는 재청구 가능.
  fencing                   session.worker_fencing_token CAS — 새 Worker 가 token 을
                            올리면 구 Worker 의 claim/기록이 전부 거부된다.

watermark 계산·realtime/recovery 분리·drain 은 2B-2, S3+canonical+job/outbox 를 묶는
commit transaction 은 PR 3 소관 — `record_window_outcome` 은 그 transaction 의 window
갱신 조각으로 재사용된다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..config import DbConfig
from ..db import connect as _default_connect
from ..db import stable_domain_id
from .models import canonical_json
from .states import (
    RESULT_STATUSES,
    WINDOW_CLAIMED,
    WINDOW_DUE,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)


class UniverseConflictError(RuntimeError):
    """같은 날짜의 session 에 다른 universe 가 제시됐다 (v0.7 10.1).

    새 session 을 만들지 않고 실패시킨다 — 장중 universe 교체는 session_epoch 설계 전까지
    허용하지 않는다. FINALIZED 라도 조용히 넘기지 않는다(호출자가 날짜를 잘못 잡은 신호).
    """


class SessionFinalizedError(RuntimeError):
    """drain 경계 이후(DRAINING~FINALIZED/FAILED) session 재계획 시도 — snapshot 경계
    뒤에 window 를 더하지 않는다."""


@dataclass
class MinuteLedger:
    """session/window 원장 접근. connect_fn 은 테스트가 가짜 커넥션을 주입하는 이음매다."""

    db: DbConfig
    connect_fn: Callable = _default_connect

    # ── session 계획 ──────────────────────────────────────────
    def plan_session(
        self,
        *,
        dataset: str,
        source_group: str,
        session_date: date,
        universe_version: str,
        universe_hash: str,
        windows: Sequence[tuple[datetime, datetime]],
    ) -> tuple[str, bool]:
        """session + expected window 를 한 트랜잭션에 멱등 생성. (session_id, created).

        재계획(같은 identity)은 no-op 이고, universe 가 다르면 UniverseConflictError.
        window 의 scheduled_at 은 window_end 다 — bar 는 구간이 닫혀야 존재한다.
        """
        session_id = stable_domain_id("msn", dataset, source_group, session_date.isoformat())
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO minute_ingestion_session (
                    session_id, dataset, source_group, session_date,
                    universe_version, universe_hash, expected_window_count
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dataset, source_group, session_date) DO NOTHING
                RETURNING session_id
                """,
                (session_id, dataset, source_group, session_date, universe_version,
                 universe_hash, len(windows)),
            )
            created = cur.fetchone() is not None
            if not created:
                # FOR UPDATE — phase 확인과 window 삽입 사이에 drain 전환이 끼어들면
                # (TOCTOU) DRAINED 경계 뒤로 DUE 가 삽입된다. 행 잠금으로 직렬화한다.
                cur.execute(
                    """
                    SELECT session_id, universe_version, universe_hash, phase
                    FROM minute_ingestion_session
                    WHERE dataset = %s AND source_group = %s AND session_date = %s
                    FOR UPDATE
                    """,
                    (dataset, source_group, session_date),
                )
                existing_id, existing_version, existing_hash, phase = cur.fetchone()
                if (existing_version, existing_hash) != (universe_version, universe_hash):
                    raise UniverseConflictError(
                        f"session {existing_id} ({dataset}/{source_group}/{session_date}) 는 "
                        f"universe {existing_version} 로 고정됐다 — {universe_version} 거부"
                    )
                if phase not in ("PLANNED", "ACTIVE"):
                    # DRAINING 부터는 재계획 금지 — DRAINED snapshot 경계 뒤에 DUE window
                    # 를 삽입하면 claim/record 게이트를 planner 경로가 우회한다
                    raise SessionFinalizedError(
                        f"session {existing_id} 는 {phase} 다 — 재계획 불가"
                    )
                session_id = existing_id
            # 재계획에도 window INSERT 는 멱등이라 무해하다 — 누락분만 채워진다
            cur.executemany(
                """
                INSERT INTO minute_ingestion_window (
                    session_id, window_start, window_end, scheduled_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (session_id, window_start) DO NOTHING
                """,
                [(session_id, ws, we, we) for ws, we in windows],
            )
            # 재계획이 window 를 더했을 수 있다 — 집계는 실제 행 수가 정본
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET expected_window_count = (
                        SELECT COUNT(*) FROM minute_ingestion_window
                        WHERE session_id = %s
                    ),
                    updated_at = now()
                WHERE session_id = %s
                """,
                (session_id, session_id),
            )
        return session_id, created

    # ── worker fence ──────────────────────────────────────────
    def acquire_worker_fence(
        self, *, session_id: str, worker_id: str, now: datetime, lease_seconds: int
    ) -> int | None:
        """session 의 worker lease 를 CAS 로 획득하고 증가한 fencing token 을 돌려준다.

        살아 있는 lease 가 있으면 None — 호출자는 기다린다. 만료 lease 는 넘겨받으며,
        token 증가가 구 Worker 의 이후 쓰기를 전부 거부되게 만든다(중복 Worker 대비).
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET worker_fencing_token = worker_fencing_token + 1,
                    lease_expires_at = %s,
                    heartbeat_at = %s,
                    phase = CASE WHEN phase = 'PLANNED' THEN 'ACTIVE' ELSE phase END,
                    updated_at = now()
                WHERE session_id = %s
                  AND (lease_expires_at IS NULL OR lease_expires_at < %s)
                  AND phase IN ('PLANNED', 'ACTIVE', 'DRAINING')
                RETURNING worker_fencing_token
                """,
                (now + timedelta(seconds=lease_seconds), now, session_id, now),
            )
            row = cur.fetchone()
            return None if row is None else row[0]

    def heartbeat(
        self, *, session_id: str, fence_token: int, now: datetime, lease_seconds: int
    ) -> bool:
        """lease 연장. stale token 이거나 session 이 terminal(DRAINED 이후)이면 False —
        어느 쪽이든 Worker 는 즉시 멈춰야 한다는 신호다."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET lease_expires_at = %s, heartbeat_at = %s, updated_at = now()
                WHERE session_id = %s AND worker_fencing_token = %s
                  AND phase IN ('ACTIVE', 'DRAINING')
                """,
                (now + timedelta(seconds=lease_seconds), now, session_id, fence_token),
            )
            return cur.rowcount == 1

    # ── window claim ──────────────────────────────────────────
    @staticmethod
    def _fenced_phase(cur, session_id: str, fence_token: int) -> str | None:
        """session 행을 잠그고 fence 를 검사한다. 유효하면 현재 phase, 아니면 None.

        비잠금 조인으로 검사하면 READ COMMITTED 스냅샷이 token 증가 **이전**을 볼 수
        있어, 새 Worker 가 fence 를 가져간 뒤에도 구 Worker 의 쓰기가 통과한다(P1).
        FOR UPDATE 로 잠그면 이 트랜잭션이 끝날 때까지 fence 교체(acquire)가 블록되므로
        검사와 쓰기가 직렬화된다. phase 를 함께 주는 이유: DRAINED 이후의 claim/기록은
        fence 가 유효해도 거부해야 EOD snapshot 경계가 지켜진다.
        """
        if fence_token < 1:
            # 발급된 token 은 항상 1 이상이다 — 0 을 허용하면 fence 를 획득한 적 없는
            # 호출이 기본값 0 과 일치해 통과한다(예: PLANNED→DRAINING 을 token 0 으로 ack)
            return None
        cur.execute(
            """
            SELECT worker_fencing_token, phase FROM minute_ingestion_session
            WHERE session_id = %s
            FOR UPDATE
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row is None or row[0] != fence_token:
            return None
        return row[1]

    def claim_due_window(
        self,
        *,
        session_id: str,
        worker_id: str,
        fence_token: int,
        now: datetime,
        lease_seconds: int,
        lane: str = "realtime",
    ) -> dict | None:
        """due window 하나를 lease 로 claim 한다. 없으면 None.

        lane 이 순서를 정한다(v0.7 7절 — realtime 우선, recovery 는 bounded budget):
        - "realtime": **최신** due window 부터 — 장중 지연이 최신 분 처리를 밀지 않게.
        - "recovery": **최고령** backlog 부터 — 복구가 오래된 hole 을 먼저 메우게.

        due = scheduled_at 도달 + (DUE 이거나 lease 만료된 CLAIMED). FOR UPDATE SKIP LOCKED
        로 동시 Worker 의 winner 는 1이다. stale fence 는 여기서부터 거부된다 — session 의
        현재 token 과 일치하는 호출만 claim 이 성립한다.

        claim_token 은 **claim 마다 고유**해야 한다(attempt_count 재사용) — session fence
        를 그대로 쓰면 같은 Worker 의 만료된 옛 claim 과 새 claim 이 구분되지 않아, 늦게
        도착한 옛 attempt 의 결과가 기록을 통과한다.
        """
        if lane not in ("realtime", "recovery"):
            raise ValueError(f"lane {lane!r} 는 realtime/recovery 만 허용된다")
        order = "DESC" if lane == "realtime" else "ASC"
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            phase = self._fenced_phase(cur, session_id, fence_token)
            if phase == "ACTIVE":
                # 신규(DUE) + 만료 claim 재청구
                due_condition = "(c.data_status = %s OR (c.data_status = %s AND c.lease_expires_at < %s))"
                due_params = (WINDOW_DUE, WINDOW_CLAIMED, now)
            elif phase == "DRAINING":
                # drain 중엔 **만료된 고아 claim 회수만** — DUE 신규 claim 을 열면 drain 이
                # 수렴하지 않고, 막으면 죽은 Worker 의 in-flight 가 CLAIMED 로 봉인된다
                due_condition = "(c.data_status = %s AND c.lease_expires_at < %s)"
                due_params = (WINDOW_CLAIMED, now)
            else:
                return None
            cur.execute(
                f"""
                UPDATE minute_ingestion_window w
                SET data_status = %s, claimed_by = %s,
                    claim_token = w.attempt_count + 1,
                    lease_expires_at = %s, attempt_count = w.attempt_count + 1,
                    updated_at = now()
                WHERE (w.session_id, w.window_start) = (
                    SELECT c.session_id, c.window_start
                    FROM minute_ingestion_window c
                    WHERE c.session_id = %s
                      AND c.scheduled_at <= %s
                      AND {due_condition}
                    ORDER BY c.window_start {order}
                    LIMIT 1
                    FOR UPDATE OF c SKIP LOCKED
                )
                RETURNING w.window_start, w.window_end, w.generation,
                          w.attempt_count, w.claim_token
                """,
                (WINDOW_CLAIMED, worker_id,
                 now + timedelta(seconds=lease_seconds),
                 session_id, now, *due_params),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "session_id": session_id,
                "window_start": row[0],
                "window_end": row[1],
                "generation": row[2],
                "attempt_count": row[3],
                "claim_token": row[4],
            }

    # ── window 결과 기록 (PR 3 commit transaction 의 window 조각) ──────
    def record_window_outcome(
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
    ) -> bool:
        """claim 한 window 에 수집 결과를 기록한다. stale fence/claim 이면 False.

        거부 조건 셋이 각자 다른 결함을 막는다: claimed_by(다른 Worker 의 claim),
        claim_token(같은 Worker 의 옛 claim — claim 마다 고유), session token(fence 를
        뺏긴 구 Worker). generation 은 **checksum 이 실제로 바뀔 때만** +1 — 같은 데이터
        재실행은 generation 불변이어야 "같은 checksum → artifact 재사용" 판정(계획 §8)이
        성립한다.
        """
        if data_status not in RESULT_STATUSES:
            raise ValueError(f"data_status {data_status!r} 는 수집 결과 어휘가 아니다")
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            if self._fenced_phase(cur, session_id, fence_token) not in ("ACTIVE", "DRAINING"):
                return False
            generation = self._record_window_outcome_tx(
                cur, session_id=session_id, window_start=window_start,
                worker_id=worker_id, claim_token=claim_token, data_status=data_status,
                expected_unit_count=expected_unit_count,
                succeeded_unit_count=succeeded_unit_count,
                failed_unit_count=failed_unit_count, record_count=record_count,
                checksum=checksum, manifest_uri=manifest_uri,
                manifest_checksum=manifest_checksum, missing_units=missing_units,
                stage_timestamps=stage_timestamps,
            )
            return generation is not None

    @staticmethod
    def _record_window_outcome_tx(
        cur, *, session_id, window_start, worker_id, claim_token, data_status,
        expected_unit_count, succeeded_unit_count, failed_unit_count, record_count,
        checksum, manifest_uri, manifest_checksum, missing_units, stage_timestamps,
    ) -> int | None:
        """window 갱신 조각 — commit transaction(3-2)이 자기 트랜잭션에서 재사용한다.

        성공 시 **확정된 generation** 을 돌려준다(같은 checksum 재실행=불변, 변화=+1) —
        commit 이 이 값으로 price job/event identity 를 만든다. claim 불일치는 None.
        fence/phase 검사는 호출자 몫(같은 트랜잭션에서 _fenced_phase 선행).
        """
        cur.execute(
            """
            UPDATE minute_ingestion_window w
            SET data_status = %s, expected_unit_count = %s, succeeded_unit_count = %s,
                failed_unit_count = %s, record_count = %s,
                generation = CASE WHEN w.checksum IS NOT DISTINCT FROM %s
                                  THEN w.generation ELSE w.generation + 1 END,
                checksum = %s, manifest_uri = %s, manifest_checksum = %s,
                missing_units = %s::jsonb, stage_timestamps = %s::jsonb,
                claimed_by = NULL, claim_token = NULL, lease_expires_at = NULL,
                updated_at = now()
            WHERE w.session_id = %s AND w.window_start = %s
              AND w.claimed_by = %s AND w.claim_token = %s
            RETURNING w.generation
            """,
            (data_status, expected_unit_count, succeeded_unit_count, failed_unit_count,
             record_count, checksum, checksum, manifest_uri, manifest_checksum,
             None if missing_units is None else json.dumps(missing_units),
             # CollectionResult.stage_timestamps 는 datetime 값이다 — canonical_json
             # 이 UTC Z 로 직렬화한다(json.dumps 는 datetime 에서 TypeError)
             canonical_json(dict(stage_timestamps)),
             session_id, window_start, worker_id, claim_token),
        )
        row = cur.fetchone()
        return None if row is None else row[0]

    # ── watermark (ALPHA-663) ─────────────────────────────────
    def advance_watermarks(self, *, session_id: str) -> tuple[datetime | None, datetime | None]:
        """session watermark 두 개를 재계산해 저장하고 (processed, contiguous) 를 돌려준다.

        processed_through   = 결과가 기록된 가장 최신 구간 — 앞쪽 hole 이 있어도 전진.
        contiguous_complete_through = 처음부터 VALID/VALID_EMPTY 연속인 마지막 구간 —
        첫 hole(미처리·INCOMPLETE·INVALID·MISSING)에서 멈추고, correction 이 hole 을
        메우면 다시 전진한다 (v0.7 10.1). 계산과 저장은 **단일 UPDATE** 다 — 분리하면
        늦은 계산 결과가 더 새 watermark 를 후퇴시키는 lost update 가 생긴다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            # session 행을 먼저 잠근다 — record 트랜잭션도 fence 검사로 같은 행을 잠그므로
            # watermark 계산의 스냅샷이 진행 중인 기록과 인터리브되지 않는다(stale 후퇴 방지)
            cur.execute(
                """
                SELECT 1 FROM minute_ingestion_session WHERE session_id = %s FOR UPDATE
                """,
                (session_id,),
            )
            if cur.fetchone() is None:
                raise ValueError(f"session {session_id} 이 없다 — watermark 대상 오류")
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET processed_through = (
                      SELECT MAX(window_end) FROM minute_ingestion_window
                      WHERE session_id = %s AND data_status = ANY(%s)),
                    contiguous_complete_through = (
                      SELECT MAX(window_end) FROM minute_ingestion_window
                      WHERE session_id = %s AND data_status = ANY(%s)
                        AND window_start < COALESCE(
                          (SELECT MIN(window_start) FROM minute_ingestion_window
                            WHERE session_id = %s AND NOT data_status = ANY(%s)),
                          'infinity'::timestamptz)),
                    updated_at = now()
                WHERE session_id = %s
                RETURNING processed_through, contiguous_complete_through
                """,
                (session_id, sorted(RESULT_STATUSES),
                 session_id, [WINDOW_VALID, WINDOW_VALID_EMPTY],
                 session_id, [WINDOW_VALID, WINDOW_VALID_EMPTY],
                 session_id),
            )
            row = cur.fetchone()
            return (None, None) if row is None else (row[0], row[1])

    # ── drain (ALPHA-663) ─────────────────────────────────────
    def request_drain(self, *, session_id: str, now: datetime) -> bool:
        """drain 을 요청한다(EOD SFN 경로 — fence 무관). 이미 DRAINING 이후면 no-op False.

        phase 를 DRAINING 으로 올려 두면 Worker 가 fence heartbeat 사이에 관측하고
        새 claim 을 멈춘 뒤 ack 한다. SessionDrained 는 SQS drain 을 기다리지 않는다
        (계획 §13) — window 원장 기준의 경계다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET phase = 'DRAINING', drain_requested_at = %s, updated_at = now()
                WHERE session_id = %s AND phase IN ('PLANNED', 'ACTIVE')
                """,
                (now, session_id),
            )
            return cur.rowcount == 1

    def ack_drain(self, *, session_id: str, fence_token: int, now: datetime) -> bool:
        """Worker 가 drain 관측을 ack 한다 — in-flight 를 끝냈고 새 claim 을 멈췄다는 표식.

        stale fence 의 ack 는 거부한다: 구 Worker 의 ack 가 통과하면 새 Worker 가 아직
        처리 중인데 DRAINED 로 넘어가 EOD QC 가 이른 snapshot 을 찍는다. CLAIMED window
        가 남아 있어도 거부한다 — 미완료 in-flight 를 봉인하면 그 window 가 유실된다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            if self._fenced_phase(cur, session_id, fence_token) is None:
                return False
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET phase = 'DRAINED', drain_ack_at = %s, updated_at = now()
                WHERE session_id = %s AND phase = 'DRAINING'
                  AND NOT EXISTS (
                    SELECT 1 FROM minute_ingestion_window
                    WHERE session_id = %s AND data_status = 'CLAIMED'
                  )
                """,
                (now, session_id, session_id),
            )
            return cur.rowcount == 1
