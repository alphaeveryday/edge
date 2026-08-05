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
from .models import canonical_json, scheduled_at_for
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
        window 의 scheduled_at 은 `scheduled_at_for` 가 정한다 — 보통 window_end(구간이
        닫혀야 봉이 있다)고, 종가 단일가 접수 구간(15:20~15:30) window 만 마감 확정을
        기다려 늦춘다.

        ⚠️ window INSERT 는 `DO NOTHING` 이라 **재계획이 기존 행의 scheduled_at 을 안
        고친다**. 이미 계획된 세션에 이 규칙을 소급 적용하려면 그 행을 직접 UPDATE 해야
        한다(2026-08-05 에 그렇게 당일 적용했다).
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
                [(session_id, ws, we, scheduled_at_for(we, dataset=dataset))
                 for ws, we in windows],
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

    def session_universe(self, *, session_id: str) -> tuple[str, str] | None:
        """session 에 고정된 (universe_version, universe_hash). 없는 session 이면 None.

        Worker 가 자기 설정과 대조하는 용도다 — 원장은 universe 를 session 생성 시
        고정하는데(v0.7 10.1) Worker 는 자기 설정으로 기대 집합을 계산하므로, 둘이
        갈리면 남의 기대를 내 기준으로 확정하게 된다.

        window 계획 범위가 그 universe 와 맞는지는 여기서 보지 않는다 — 계획과 hash 를
        같은 universe 에서 뽑는 것은 planner 의 불변식이고, 강제할 자리도 planner 다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT universe_version, universe_hash FROM minute_ingestion_session
                WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
            return None if row is None else (row[0], row[1])

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
                          w.checksum, w.manifest_checksum, w.attempt_count, w.claim_token
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
                # Worker 가 세대를 **결정적으로** 예측하는 재료: 새 records checksum
                # 과 manifest_checksum 이 **둘 다** 이 값과 같으면 세대 불변, 아니면 +1
                # — 예측이 틀리면 commit 이 rollback 한다
                "checksum": row[3],
                "manifest_checksum": row[4],
                "attempt_count": row[5],
                "claim_token": row[6],
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

        성공 시 **확정된 generation** 을 돌려준다 — records checksum **과**
        manifest_checksum 이 둘 다 불변일 때만 세대 유지, 어느 쪽이든 변하면 +1.
        (분류만 바뀐 정정 — 예: missing→no_trade — 은 records 가 같아도 manifest 가
        달라 새 세대·새 manifest key 를 받아야 불변 PUT 과 충돌하지 않는다.)
        commit 이 이 값으로 price job/event identity 를 만든다. claim 불일치는 None.
        fence/phase 검사는 호출자 몫(같은 트랜잭션에서 _fenced_phase 선행).
        어휘 검증은 여기(모든 경로의 합류점)에 둔다 — 래퍼에만 두면 _tx 직접 호출이
        DUE/MISSING 같은 원장 축 값을 결과로 위장할 수 있다.
        """
        if data_status not in RESULT_STATUSES:
            raise ValueError(f"data_status {data_status!r} 는 수집 결과 어휘가 아니다")
        cur.execute(
            """
            UPDATE minute_ingestion_window w
            SET data_status = %s, expected_unit_count = %s, succeeded_unit_count = %s,
                failed_unit_count = %s, record_count = %s,
                generation = CASE WHEN w.checksum IS NOT DISTINCT FROM %s
                                       AND w.manifest_checksum IS NOT DISTINCT FROM %s
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
             record_count, checksum, manifest_checksum, checksum, manifest_uri,
             manifest_checksum,
             None if missing_units is None else json.dumps(missing_units),
             # CollectionResult.stage_timestamps 는 datetime 값이다 — canonical_json
             # 이 UTC Z 로 직렬화한다(json.dumps 는 datetime 에서 TypeError)
             canonical_json(dict(stage_timestamps)),
             session_id, window_start, worker_id, claim_token),
        )
        row = cur.fetchone()
        return None if row is None else row[0]

    def release_window_claim(
        self, *, session_id: str, window_start: datetime, worker_id: str, claim_token: int,
    ) -> bool:
        """내 claim 을 자발 반납한다(DUE 복귀) — drain 중 반복 실패 window 용.

        lease 만료를 기다리면 drain ack 가 CLAIMED 잔존으로 계속 거부돼 세션이
        DRAINING 에 고착된다. 반납된 DUE 는 ack 를 막지 않고, 잔여 판정(MISSING 등)은
        EOD QC 소관이다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_window
                SET data_status = 'DUE', claimed_by = NULL, claim_token = NULL,
                    lease_expires_at = NULL, updated_at = now()
                WHERE session_id = %s AND window_start = %s
                  AND claimed_by = %s AND claim_token = %s AND data_status = 'CLAIMED'
                """,
                (session_id, window_start, worker_id, claim_token),
            )
            return cur.rowcount == 1

    def release_worker_fence(self, *, session_id: str, fence_token: int) -> bool:
        """graceful 종료 시 session lease 를 즉시 반납한다 — token 은 유지.

        반납 없이 죽으면 교체 Worker 가 lease 만료(기본 5분)까지 진입하지 못해
        장중 window 여러 개가 밀린다. token 을 지우지 않으므로 구 프로세스의 잔여
        쓰기는 계속 거부된다(다음 acquire 가 token 을 올린다).
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET lease_expires_at = NULL, updated_at = now()
                WHERE session_id = %s AND worker_fencing_token = %s
                """,
                (session_id, fence_token),
            )
            return cur.rowcount == 1

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

    # ── EOD QC (ALPHA-693) ────────────────────────────────────

    def begin_qc(self, *, session_id: str, now: datetime) -> dict | None:
        """QC 진입 — `DRAINED`(또는 재실행) → `QC_RUNNING` CAS. 자격이 없으면 None.

        재진입을 허용하는 phase 가 셋인 이유는 각각 다르다:
        - `DRAINED` — 정상 진입.
        - `QC_RUNNING` — 앞선 QC 가 중간에 죽은 자리다. 막으면 그 세션은 **누구도 끝낼 수
          없다**(QC 에는 lease 가 없다). QC 는 판정만 하고 쓰기가 전부 멱등이라 재실행이
          안전하다 — 되돌릴 수 없는 상태를 만들지 않는 대신 재진입을 연다.
        - `FAILED` — 불변식 위반으로 멈춘 자리다. 원인을 고친 뒤 다시 판정할 수 있어야 한다.

        `FINALIZED` 는 자격이 없다(None) — 확정된 하루를 다시 열지 않는다. 정정이 필요하면
        correction 경로가 새 세대를 만든다(v0.7 10.5).

        ⚠️ **fencing token 을 올리고 돌려준다.** phase 만으로 CAS 하면 ABA 가 통과한다:
        실행 A 가 스냅샷을 뜬 뒤 멈추고, B 가 FAILED 로 바꾸고, C 가 다시 QC_RUNNING 으로
        들어와도, A 의 늦은 확정이 "지금 QC_RUNNING 이다"만 보고 성공해 **C 의 판정을
        덮는다**(단방향 FINALIZED 를 낡은 checksum 으로 만든다). 재진입을 여는 대신
        소유권을 토큰으로 증명한다. Worker 용 컬럼을 함께 쓰는 건 안전하다 — 이 phase
        에서는 fence 획득·claim·결과 기록이 모두 막혀 있어 경쟁자가 없다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET phase = 'QC_RUNNING',
                    worker_fencing_token = worker_fencing_token + 1,
                    updated_at = now()
                WHERE session_id = %s AND phase IN ('DRAINED', 'QC_RUNNING', 'FAILED')
                RETURNING dataset, source_group, session_date, expected_window_count,
                          universe_version, worker_fencing_token
                """,
                (session_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("dataset", "source_group", "session_date", "expected_window_count",
             "universe_version", "fence_token"), row, strict=True,
        ))

    def confirm_missing_windows(self, *, session_id: str, fence_token: int, now: datetime) -> int:
        """**이미 도래한** `DUE` 를 `MISSING` 으로 확정한다 — 확정된 행 수.

        세션이 QC 에 들어왔다는 건 그 window 를 처리할 주체가 더 없다는 뜻이다(Worker 는
        drain 에서 claim 을 반납만 하고 떠난다). 여기서 확정하지 않으면 누락이 원장에
        **DUE 로 영원히 남아**, "아직 안 한 것"과 "끝내 못 한 것"이 구분되지 않는다.

        가드가 둘이고 막는 사고가 다르다.

        ⚠️ **phase** — 안 묶으면 살아 있는(ACTIVE) 세션에 이 명령을 잘못 겨눴을 때 처리
        대기 중인 window 를 전부 MISSING 으로 죽인다(claim 대상에서 빠져 그날 데이터가
        통째로 사라진다).

        ⚠️ **fencing token 을 잠그고 검사한다** — phase 만 보면 소유권을 잃은 낡은 QC 도
        쓴다. 두 실행의 `now` 가 다르면 낡은 쪽이 새 쪽 기준으로 미도래인 window 까지
        MISSING 으로 바꾸고, 같아도 새 쪽의 `missing_confirmed` 가 0 으로 왜곡된다.
        ⚠️⚠️ 그런데 토큰을 `UPDATE … FROM session s` 의 조건으로만 두면 **s 를 잠그지
        않는다** — READ COMMITTED 스냅샷이 옛 토큰을 본 채 진행할 수 있다(이 레포가 반복해
        데인 비잠금 검사 = TOCTOU). 그래서 같은 트랜잭션에서 세션 행을 `FOR UPDATE` 로
        먼저 잠그고 대조한 뒤 쓴다. 이 쓰기는 되돌릴 수 없어 값이 비쌀 자격이 있다.

        ⚠️ **scheduled_at ≤ now** — phase 만으로는 부족하다. 장중에 `request_drain` 이
        잘못 호출되면 Worker 가 새 claim 을 멈추고, CLAIMED 만 없으면 `ack_drain` 이
        DRAINED 를 만든다. 그 상태로 QC 를 돌리면 **아직 오지도 않은 분**까지 MISSING 으로
        확정하고 하루가 봉인된다. 도래하지 않은 window 는 판정 대상이 아니다 — 남은 DUE 는
        호출자(QC)가 위반으로 드러낸다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT phase, worker_fencing_token FROM minute_ingestion_session
                WHERE session_id = %s FOR UPDATE
                """,
                (session_id,),
            )
            row = cur.fetchone()
            if row is None or row[0] != "QC_RUNNING" or row[1] != fence_token:
                # 내 QC 가 아니다 — 되돌릴 수 없는 쓰기를 하지 않는다
                return 0
            cur.execute(
                """
                UPDATE minute_ingestion_window
                SET data_status = 'MISSING', updated_at = now()
                WHERE session_id = %s AND data_status = 'DUE' AND scheduled_at <= %s
                """,
                (session_id, now),
            )
            return cur.rowcount

    def session_snapshot(self, *, session_id: str) -> dict | None:
        """세션 한 행의 QC 관련 값 — 없으면 None.

        QC 재실행이 **이미 FINALIZED 인 세션**을 만났을 때 쓴다: 확정 직후 출력 전에 죽은
        실행을 재시도하면 그 판정을 다시 읽어 돌려줘야 한다(못 읽으면 정상 확정된 하루가
        재시도마다 실패로 보인다). 필요한 값을 한 번에 읽는다 — 같은 행을 두 번 조회하면
        그 사이에 바뀐 값으로 서로 안 맞는 보고가 나온다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT dataset, source_group, session_date, expected_window_count,
                       universe_version, phase, final_checksum, final_generation
                FROM minute_ingestion_session WHERE session_id = %s
                """,
                (session_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("dataset", "source_group", "session_date", "expected_window_count",
             "universe_version", "phase", "final_checksum", "final_generation"),
            row, strict=True,
        ))

    def session_window_rows(self, *, session_id: str) -> list[tuple]:
        """QC 판정 입력 — (window_start, window_end, data_status, generation, checksum).

        집계만 돌려주지 않는 이유: `final_checksum` 이 이 목록에서 나오고, 집계는 그 목록
        에서 다시 셀 수 있다. 반대로 집계만 받으면 같은 개수의 다른 결과가 같은 checksum 을
        갖는다(예: 두 window 의 상태가 서로 뒤바뀐 경우).

        `window_end` 도 함께 준다 — DB 제약은 `window_start < window_end` 뿐이라 겹치거나
        1분이 아닌 구간도 저장될 수 있고, Worker 는 **저장된 window_end 를 그대로** 수집기에
        넘긴다. 즉 end 가 어긋나면 수집 구간 자체가 어긋난 것이라 판정 입력에 있어야 한다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT window_start, window_end, data_status, generation, checksum
                FROM minute_ingestion_window
                WHERE session_id = %s ORDER BY window_start
                """,
                (session_id,),
            )
            return list(cur.fetchall())

    def finalize_session(
        self, *, session_id: str, fence_token: int, final_checksum: str,
        final_generation: int | None, now: datetime,
    ) -> bool:
        """`QC_RUNNING` → `FINALIZED` CAS. **내 QC** 가 아니면 False(token 대조).

        `finalized_through` 는 채우지 않는다 — 스키마 주석대로 correction 대기기간이
        업무상 필요할 때만 쓰는 컬럼이고, 지금은 그 정책이 없다(있는 것처럼 채우면
        소비자가 그 값을 경계로 읽는다).
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET phase = 'FINALIZED', final_checksum = %s, final_generation = %s,
                    updated_at = now()
                WHERE session_id = %s AND phase = 'QC_RUNNING'
                  AND worker_fencing_token = %s
                """,
                (final_checksum, final_generation, session_id, fence_token),
            )
            return cur.rowcount == 1

    def fail_session_qc(self, *, session_id: str, fence_token: int, now: datetime) -> bool:
        """`QC_RUNNING` → `FAILED` CAS — 불변식 위반을 phase 로 드러낸다.

        사유 컬럼이 없어 **원장에는 사유가 안 남는다**(로그·CLI 출력이 그 자리다).
        `begin_qc` 가 FAILED 재진입을 허용하므로 원인을 고친 뒤 다시 판정할 수 있다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE minute_ingestion_session
                SET phase = 'FAILED', updated_at = now()
                WHERE session_id = %s AND phase = 'QC_RUNNING'
                  AND worker_fencing_token = %s
                """,
                (session_id, fence_token),
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
