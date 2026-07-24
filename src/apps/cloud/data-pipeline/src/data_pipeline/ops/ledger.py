"""운영 원장 repository — 5테이블 CRUD (ALPHA-530).

`db.py`(DB 접속 SSOT)를 재사용한다. **커넥션은 lazy** 다 — 이 모듈 import 는 DB 를 열지 않고,
psycopg 도 db.connect 안에서 지연 import 된다(원장 설정 없는 태스크가 import 로 죽지 않게, 스펙 §6).

멱등 근거:
  ops_pipeline_run       run_key (슬롯 1회 계획)
  ops_expected_task      (pipeline_run_id, task_key)
  ops_task_attempt       (expected_task_id, ecs_task_arn) — ARN 없는 시도는 아예 안 만든다
  ops_reconciliation_issue  OPEN 부분 유니크(dedupe_key)

원장 기록 실패 정책(스펙 §3.4): attempt 시작 INSERT 는 ~30초 bounded backoff, 종료 UPDATE 는
bounded retry. 계속 실패하면 경고만 남기고 **본 작업 결과를 바꾸지 않는다** — 그래서 이 계층의
쓰기 메서드는 예외를 던지지 않고 성패를 bool/None 으로 돌린다(호출부가 무시하고 진행할 수 있게).

JSONB 파라미터는 json.dumps 문자열 + `%s::jsonb` 캐스트로 넘긴다(load 스텝 관례·FakeCursor 친화).
시간은 DB now() 기본값과 파이썬 UTC ISO 를 함께 쓴다 — 이벤트 시각은 관측원이 주는 게 맞다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from ..config import DbConfig
from ..db import connect as _default_connect
from ..db import domain_id, stable_domain_id
from . import states

logger = logging.getLogger(__name__)

# attempt 시작 INSERT 의 bounded backoff 상한(스펙 §3.4 "약 30초").
_START_BACKOFF_DEADLINE_SEC = 30.0
_END_RETRY_ATTEMPTS = 4


def _jsonb(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


@dataclass
class Ledger:
    """운영 원장 접근. connect_fn 은 테스트가 가짜 커넥션을 주입하는 이음매다."""

    db: DbConfig
    connect_fn: Callable = _default_connect
    # 백오프 대기 함수(테스트가 실제 sleep 없이 돌 수 있게 주입 가능).
    sleep_fn: Callable = time.sleep
    # 단조 시계(백오프 마감 판정) — 테스트 주입용.
    clock_fn: Callable = time.monotonic

    # ── pipeline_run ──────────────────────────────────────────
    def create_pipeline_run(
        self,
        *,
        run_key: str,
        execution_name: str,
        pipeline_type: str,
        schedule_slot: str | None,
        trading_date: str | None,
        hard_deadline_at: str | None,
        catalog_version: str,
        catalog_content_hash: str,
        image_digest: str | None,
        input_hash: str | None,
        expected_execution_arn: str | None,
    ) -> tuple[str, bool]:
        """run_key 로 멱등 생성. (pipeline_run_id, created) 반환. 이미 있으면 기존 id·False.

        Planner 재기동이 같은 슬롯을 두 번 만들지 않게 하는 정본(스펙 §5). expected_task 와
        **한 트랜잭션**이어야 하므로 이 메서드는 그 트랜잭션 안에서 호출된다(plan_run 참조).

        pipeline_run_id 는 run_key 에서 **결정적**으로 파생한다(stable_domain_id) — 랜덤 ULID 면
        Planner 재기동이 다른 id 를 만들어 pipeline_run_id 기반 execution_name 의 멱등이 깨진다.
        """
        new_id = stable_domain_id("run", run_key)
        with self.connect_fn(self.db) as conn:
            return self._create_pipeline_run_tx(
                conn, new_id, run_key=run_key, execution_name=execution_name,
                pipeline_type=pipeline_type, schedule_slot=schedule_slot,
                trading_date=trading_date, hard_deadline_at=hard_deadline_at,
                catalog_version=catalog_version, catalog_content_hash=catalog_content_hash,
                image_digest=image_digest, input_hash=input_hash,
                expected_execution_arn=expected_execution_arn,
            )

    @staticmethod
    def _create_pipeline_run_tx(conn, new_id: str, **f) -> tuple[str, bool]:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ops_pipeline_run (pipeline_run_id, run_key, execution_name,"
                " pipeline_type, schedule_slot, trading_date, hard_deadline_at,"
                " catalog_version, catalog_content_hash, image_digest, input_hash,"
                " expected_execution_arn, launch_status)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (run_key) DO NOTHING"
                " RETURNING pipeline_run_id",
                (new_id, f["run_key"], f["execution_name"], f["pipeline_type"],
                 f["schedule_slot"], f["trading_date"], f["hard_deadline_at"],
                 f["catalog_version"], f["catalog_content_hash"], f["image_digest"],
                 f["input_hash"], f["expected_execution_arn"], states.LAUNCH_PLANNING),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row[0]), True
            cur.execute(
                "SELECT pipeline_run_id FROM ops_pipeline_run WHERE run_key = %s", (f["run_key"],)
            )
            return str(cur.fetchone()[0]), False

    def set_launch_result(
        self, pipeline_run_id: str, *, launch_status: str,
        sfn_execution_arn: str | None = None, orchestration_status: str | None = None,
    ) -> None:
        """StartExecution 결과 반영. sfn_execution_arn 은 **실행 확인 뒤에만** 넘긴다(스펙 §4)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ops_pipeline_run SET launch_status=%s,"
                " sfn_execution_arn=COALESCE(%s, sfn_execution_arn),"
                " orchestration_status=COALESCE(%s, orchestration_status),"
                " updated_at=now() WHERE pipeline_run_id=%s",
                (launch_status, sfn_execution_arn, orchestration_status, pipeline_run_id),
            )

    # ── expectation_snapshot ──────────────────────────────────
    @staticmethod
    def _create_snapshot_tx(
        conn, *, pipeline_run_id: str, task_key: str, universe_version: str | None,
        constituent_as_of_date: str | None, entity_kind: str,
        entity_ids: list | None, storage_uri: str | None, content_hash: str | None,
    ) -> str:
        snap_id = domain_id("snap")
        count = len(entity_ids) if entity_ids is not None else 0
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ops_expectation_snapshot (expectation_snapshot_id, pipeline_run_id,"
                " task_key, universe_version, constituent_as_of_date, entity_kind,"
                " expected_entity_count, entity_ids, storage_uri, content_hash)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
                (snap_id, pipeline_run_id, task_key, universe_version, constituent_as_of_date,
                 entity_kind, count, _jsonb(entity_ids), storage_uri, content_hash),
            )
        return snap_id

    # ── expected_task ─────────────────────────────────────────
    @staticmethod
    def _create_expected_task_tx(
        conn, *, pipeline_run_id: str, task_key: str, stage: str, dataset: str | None,
        required: bool, plan_status: str, expected_at: str | None, deadline_at: str | None,
        eligible_at: str | None, expected_as_of_date: str | None,
        expectation_snapshot_id: str | None, skip_reason: str | None,
    ) -> tuple[str, bool]:
        """(run, task_key) 멱등 생성. SKIPPED 이면 outcome/data_status 는 NULL(축 분리, 스펙 §3.2)."""
        new_id = domain_id("etask")
        outcome = None if plan_status == states.PLAN_SKIPPED else states.OUTCOME_PENDING
        data_status = None if plan_status == states.PLAN_SKIPPED else states.DATA_UNKNOWN
        idem = f"{pipeline_run_id}{task_key}"
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ops_expected_task (expected_task_id, pipeline_run_id, task_key,"
                " stage, dataset, plan_status, task_outcome, data_status, required, expected_at,"
                " deadline_at, eligible_at, expected_as_of_date, expectation_snapshot_id,"
                " skip_reason, idempotency_key)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (pipeline_run_id, task_key) DO NOTHING"
                " RETURNING expected_task_id",
                (new_id, pipeline_run_id, task_key, stage, dataset, plan_status, outcome,
                 data_status, required, expected_at, deadline_at, eligible_at,
                 expected_as_of_date, expectation_snapshot_id, skip_reason, idem),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row[0]), True
            cur.execute(
                "SELECT expected_task_id FROM ops_expected_task"
                " WHERE pipeline_run_id=%s AND task_key=%s",
                (pipeline_run_id, task_key),
            )
            return str(cur.fetchone()[0]), False

    def find_expected_task(self, *, run_id: str, task_key: str) -> dict | None:
        """(run, task_key) 로 expected_task 조회(wrapper 가 attempt 를 붙일 대상)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT expected_task_id, plan_status, task_outcome, data_status, required"
                " FROM ops_expected_task WHERE pipeline_run_id=%s AND task_key=%s",
                (run_id, task_key),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return {"expected_task_id": str(row[0]), "plan_status": row[1],
                    "task_outcome": row[2], "data_status": row[3], "required": row[4]}

    def update_task_outcome(
        self, expected_task_id: str, *, task_outcome: str | None = None,
        data_status: str | None = None, outcome_reason: str | None = None,
        current_attempt_id: str | None = None, completeness: dict | None = None,
        fulfilled: bool = False, missed: bool = False, blocked: bool = False,
    ) -> None:
        """expected_task 의 결과 축을 갱신. 시각 컬럼은 해당 전이일 때만 찍는다(비래치 규칙 보존).

        missed_at 은 한 번 찍히면 보존한다(MISSED→FULFILLED 로 가도 미실행 이력이 남게, 스펙 §7).
        """
        sets = ["updated_at=now()"]
        params: list = []
        if task_outcome is not None:
            sets.append("task_outcome=%s"); params.append(task_outcome)
        if data_status is not None:
            sets.append("data_status=%s"); params.append(data_status)
        if outcome_reason is not None:
            sets.append("outcome_reason=%s"); params.append(outcome_reason)
        if current_attempt_id is not None:
            sets.append("current_attempt_id=%s"); params.append(current_attempt_id)
        if completeness is not None:
            sets.append("completeness=%s::jsonb"); params.append(_jsonb(completeness))
        if fulfilled:
            sets.append("fulfilled_at=COALESCE(fulfilled_at, now())")
        if missed:
            sets.append("missed_at=COALESCE(missed_at, now())")
        if blocked:
            sets.append("blocked_at=COALESCE(blocked_at, now())")
        params.append(expected_task_id)
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE ops_expected_task SET {', '.join(sets)} WHERE expected_task_id=%s",
                tuple(params),
            )

    def set_eligible(self, expected_task_id: str) -> None:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ops_expected_task SET eligible_at=COALESCE(eligible_at, now()),"
                " updated_at=now() WHERE expected_task_id=%s",
                (expected_task_id,),
            )

    # ── task_attempt ──────────────────────────────────────────
    def record_attempt_start(
        self, *, expected_task_id: str, ecs_task_arn: str,
        sfn_execution_arn: str | None = None, sfn_state_name: str | None = None,
        record_source: str = states.SOURCE_WRAPPER,
    ) -> str | None:
        """attempt 시작 기록(멱등). 성공 시 attempt_id, 계속 실패 시 None(본 작업은 진행).

        **ECS ARN 이 필수**다 — 없는 시도는 아예 만들지 않는다(스펙 §6, 가짜 attempt 금지).
        bounded backoff(~30초): 원장 DB 가 잠깐 흔들려도 본 작업을 막지 않는다(스펙 §3.4).
        """
        if not ecs_task_arn:
            logger.warning("record_attempt_start: ecs_task_arn 없음 — attempt 생성 안 함")
            return None
        deadline = self.clock_fn() + _START_BACKOFF_DEADLINE_SEC
        delay = 0.5
        while True:
            try:
                return self._insert_attempt(
                    expected_task_id=expected_task_id, ecs_task_arn=ecs_task_arn,
                    sfn_execution_arn=sfn_execution_arn, sfn_state_name=sfn_state_name,
                    record_source=record_source,
                )
            except Exception:
                if self.clock_fn() >= deadline:
                    logger.exception(
                        "attempt 시작 기록 실패(backoff 소진) — 본 작업 계속(원장 누락은 Reconciler 복구)"
                    )
                    return None
                self.sleep_fn(delay)
                delay = min(delay * 2, 5.0)

    def _insert_attempt(
        self, *, expected_task_id: str, ecs_task_arn: str, sfn_execution_arn: str | None,
        sfn_state_name: str | None, record_source: str,
    ) -> str:
        new_id = domain_id("att")
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            # attempt_number 는 표시용 — 조회+1(멱등 수단 아님, 경쟁은 표시 부정확만 유발).
            cur.execute(
                "SELECT count(*) FROM ops_task_attempt WHERE expected_task_id=%s",
                (expected_task_id,),
            )
            number = int(cur.fetchone()[0]) + 1
            cur.execute(
                "INSERT INTO ops_task_attempt (attempt_id, expected_task_id, attempt_number,"
                " ecs_task_arn, execution_status, started_at, sfn_execution_arn, sfn_state_name,"
                " record_source) VALUES (%s,%s,%s,%s,%s,now(),%s,%s,%s)"
                " ON CONFLICT (expected_task_id, ecs_task_arn) DO NOTHING"
                " RETURNING attempt_id",
                (new_id, expected_task_id, number, ecs_task_arn, states.EXEC_RUNNING,
                 sfn_execution_arn, sfn_state_name, record_source),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row[0])
            cur.execute(
                "SELECT attempt_id FROM ops_task_attempt"
                " WHERE expected_task_id=%s AND ecs_task_arn=%s",
                (expected_task_id, ecs_task_arn),
            )
            return str(cur.fetchone()[0])

    def record_attempt_end(
        self, attempt_id: str, *, execution_status: str, exit_code: int | None = None,
        failure_reason: str | None = None, data_status: str | None = None,
    ) -> bool:
        """attempt 종료 기록. bounded retry, 실패해도 본 작업 결과 불변(성공 여부만 bool 반환)."""
        for i in range(_END_RETRY_ATTEMPTS):
            try:
                with self.connect_fn(self.db) as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ops_task_attempt SET execution_status=%s, finished_at=now(),"
                        " exit_code=%s, failure_reason=%s,"
                        " data_status=COALESCE(%s, data_status) WHERE attempt_id=%s",
                        (execution_status, exit_code, failure_reason, data_status, attempt_id),
                    )
                return True
            except Exception:
                if i == _END_RETRY_ATTEMPTS - 1:
                    logger.exception("attempt 종료 기록 실패(재시도 소진) — Reconciler 가 복구")
                    return False
                self.sleep_fn(0.5 * (2 ** i))
        return False

    def backfill_attempt(
        self, *, expected_task_id: str, ecs_task_arn: str, execution_status: str,
        sfn_execution_arn: str | None = None, sfn_state_name: str | None = None,
        exit_code: int | None = None,
    ) -> str | None:
        """Reconciler 가 ECS ARN 은 있는데 원장에 attempt 가 없는 누락을 사후 복구(LEDGER_GAP).

        멱등: 이미 있으면 그 id 를 돌려준다. record_source=RECONCILER_BACKFILL 로 출처를 남긴다.
        """
        if not ecs_task_arn:
            return None
        new_id = domain_id("att")
        # finished_at 은 **종료 상태일 때만** 찍는다 — RUNNING 을 backfill 하면서 finished_at 을
        # 넣으면 종료로 오독되고, started_at 이 없으면 다음 reconcile 이 경과시간을 못 재 STALLED
        # 탐지가 영영 안 된다(edge-review). started_at 은 항상 채운다(경과 기준선).
        terminal = execution_status != states.EXEC_RUNNING
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ops_task_attempt (attempt_id, expected_task_id, ecs_task_arn,"
                " execution_status, exit_code, sfn_execution_arn, sfn_state_name, record_source,"
                " started_at, finished_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now(),"
                f" {'now()' if terminal else 'NULL'})"
                " ON CONFLICT (expected_task_id, ecs_task_arn) DO NOTHING"
                " RETURNING attempt_id",
                (new_id, expected_task_id, ecs_task_arn, execution_status, exit_code,
                 sfn_execution_arn, sfn_state_name, states.SOURCE_RECONCILER_BACKFILL),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row[0])
            cur.execute(
                "SELECT attempt_id FROM ops_task_attempt"
                " WHERE expected_task_id=%s AND ecs_task_arn=%s",
                (expected_task_id, ecs_task_arn),
            )
            return str(cur.fetchone()[0])

    def attempts_for(self, expected_task_id: str) -> list[dict]:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT attempt_id, ecs_task_arn, execution_status, exit_code, record_source,"
                " started_at FROM ops_task_attempt WHERE expected_task_id=%s ORDER BY created_at",
                (expected_task_id,),
            )
            return [
                {"attempt_id": str(r[0]), "ecs_task_arn": r[1], "execution_status": r[2],
                 "exit_code": r[3], "record_source": r[4], "started_at": r[5]}
                for r in cur.fetchall()
            ]

    # ── reconciliation_issue ──────────────────────────────────
    def open_or_bump_issue(
        self, *, issue_type: str, dedupe_key: str, scope: str | None = None,
        scope_key: str | None = None, evidence: dict | None = None,
    ) -> tuple[str, bool]:
        """OPEN 이슈를 열거나(없으면) 재발 카운트를 올린다(있으면). (issue_id, created) 반환.

        OPEN 부분 유니크 덕에 같은 문제 반복 탐지가 새 행을 안 만들고 occurrence_count 만 올린다
        (무한 중복 알림 억제, 스펙 §7). 해결(RESOLVED) 뒤 재발은 새 OPEN 이 열린다.
        """
        new_id = domain_id("issue")
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ops_reconciliation_issue (issue_id, issue_type, scope, scope_key,"
                " dedupe_key, status, evidence) VALUES (%s,%s,%s,%s,%s,'OPEN',%s::jsonb)"
                " ON CONFLICT (dedupe_key) WHERE status='OPEN' DO UPDATE"
                " SET occurrence_count = ops_reconciliation_issue.occurrence_count + 1,"
                "     last_seen_at = now(), updated_at = now(),"
                "     evidence = COALESCE(EXCLUDED.evidence, ops_reconciliation_issue.evidence)"
                " RETURNING issue_id, (xmax = 0) AS inserted",
                (new_id, issue_type, scope, scope_key, dedupe_key, _jsonb(evidence)),
            )
            row = cur.fetchone()
            return str(row[0]), bool(row[1])

    @contextmanager
    def advisory_lock(self, key: int):
        """Postgres advisory lock — Reconciler 중복 실행 방지(스펙 §7). 획득 여부를 yield 한다.

        같은 커넥션에서 lock/unlock 해야 하므로 커넥션을 열어 잡고 컨텍스트 종료 시 푼다.
        """
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            acquired = bool(cur.fetchone()[0])
            try:
                yield acquired
            finally:
                if acquired:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (key,))

    # ── Reconciler 조회 ───────────────────────────────────────
    def get_pipeline_run(self, run_key: str) -> dict | None:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT pipeline_run_id, run_key, execution_name, expected_execution_arn,"
                " sfn_execution_arn, launch_status, orchestration_status, hard_deadline_at,"
                " trading_date, input_hash FROM ops_pipeline_run WHERE run_key=%s",
                (run_key,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            keys = ("pipeline_run_id", "run_key", "execution_name", "expected_execution_arn",
                    "sfn_execution_arn", "launch_status", "orchestration_status",
                    "hard_deadline_at", "trading_date", "input_hash")
            return dict(zip(keys, row))

    def expected_tasks_for(self, pipeline_run_id: str) -> list[dict]:
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT expected_task_id, task_key, stage, plan_status, task_outcome,"
                " data_status, required, eligible_at, deadline_at, missed_at"
                " FROM ops_expected_task WHERE pipeline_run_id=%s",
                (pipeline_run_id,),
            )
            keys = ("expected_task_id", "task_key", "stage", "plan_status", "task_outcome",
                    "data_status", "required", "eligible_at", "deadline_at", "missed_at")
            return [dict(zip(keys, r)) for r in cur.fetchall()]

    def resolve_issue(
        self, dedupe_key: str, *, resolution_reason: str, resolution_source: str,
    ) -> bool:
        """열린 이슈를 RESOLVED 로. 해결할 게 있었으면 True(dedupe 재발 허용 상태로 전환)."""
        with self.connect_fn(self.db) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ops_reconciliation_issue SET status='RESOLVED', resolution_reason=%s,"
                " resolution_source=%s, updated_at=now()"
                " WHERE dedupe_key=%s AND status='OPEN'",
                (resolution_reason, resolution_source, dedupe_key),
            )
            return cur.rowcount > 0
