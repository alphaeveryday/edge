"""운영 원장 테스트 더블 (ALPHA-530).

FakeOpsDB — 5테이블을 인메모리로 모델링하는 가짜 커넥션. **실제 Ledger** 를 이 위에서 돌려
SQL 경로(멱등·dedupe·backfill)를 그대로 검증한다(가짜 Ledger 를 따로 두면 실제와 갈리므로).
FakeSfn·FakeEcs — StepFunctions/ECS 클라이언트 더블(history·describe 주입).

기존 test_load_price_daily 의 FakeCursor 관례(정규화 SQL 매칭)와 같은 결이되, 상태를 보존해
ON CONFLICT/RETURNING 의미를 흉내 낸다.
"""

from __future__ import annotations

import json
from contextlib import contextmanager


class FakeOpsDB:
    def __init__(self, *, advisory_grants=True):
        self.runs: dict[str, dict] = {}          # run_key -> row
        self.runs_by_id: dict[str, dict] = {}
        self.etasks: dict[tuple, dict] = {}       # (run_id, task_key) -> row
        self.etasks_by_id: dict[str, dict] = {}
        self.attempts: list[dict] = []
        self.snapshots: list[dict] = []
        self.issues: list[dict] = []
        self.advisory_grants = advisory_grants
        self.fail = False                         # True 면 커넥션이 예외(원장 장애 시뮬)

    @contextmanager
    def connect(self, _db):
        if self.fail:
            raise RuntimeError("simulated ledger DB failure")
        yield _Conn(self)

    def open_issues(self, issue_type=None):
        return [i for i in self.issues if i["status"] == "OPEN"
                and (issue_type is None or i["issue_type"] == issue_type)]


class _Conn:
    def __init__(self, db):
        self.db = db

    @contextmanager
    def cursor(self):
        yield _Cursor(self.db)


class _Cursor:
    def __init__(self, db):
        self.db = db
        self._rows: list[tuple] = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = params or ()
        self._rows = []
        self.rowcount = 0
        if "pg_try_advisory_lock" in s:
            self._rows = [(self.db.advisory_grants,)]
        elif "pg_advisory_unlock" in s:
            self._rows = [(True,)]
        elif "INSERT INTO ops_pipeline_run" in s:
            self._ins_run(p)
        elif "SELECT pipeline_run_id FROM ops_pipeline_run WHERE run_key" in s:
            r = self.db.runs.get(p[0])
            self._rows = [(r["pipeline_run_id"],)] if r else []
        elif s.startswith("UPDATE ops_pipeline_run SET launch_status"):
            self._upd_run_launch(p)
        elif "SELECT pipeline_run_id, run_key, execution_name" in s:  # get_pipeline_run
            self._get_run(p)
        elif "INSERT INTO ops_expectation_snapshot" in s:
            self.db.snapshots.append({"id": p[0], "run_id": p[1], "task_key": p[2],
                                      "expected_entity_count": p[6], "entity_ids": p[7]})
        elif "INSERT INTO ops_expected_task" in s:
            self._ins_etask(p)
        elif "SELECT expected_task_id FROM ops_expected_task WHERE pipeline_run_id" in s:
            row = self.db.etasks.get((p[0], p[1]))
            self._rows = [(row["expected_task_id"],)] if row else []
        elif "SELECT et.expected_task_id, et.plan_status, et.task_outcome" in s:
            row = self.db.etasks.get((p[0], p[1]))
            if row:
                snapshot = next(
                    (snap for snap in self.db.snapshots
                     if snap["id"] == row.get("expectation_snapshot_id")),
                    None,
                )
                self._rows = [(
                    row["expected_task_id"], row["plan_status"], row["task_outcome"],
                    row["data_status"], row["required"],
                    snapshot["expected_entity_count"] if snapshot else None,
                    row.get("dataset_contract_key"),
                )]
        elif "SELECT expected_task_id, task_key, stage, plan_status" in s:  # expected_tasks_for
            self._etasks_for(p)
        elif s.startswith("UPDATE ops_expected_task SET eligible_at"):
            self._set_eligible(p)
        elif s.startswith("UPDATE ops_expected_task SET"):
            self._upd_etask(s, p)
        elif "SELECT count(*) FROM ops_task_attempt" in s:
            self._rows = [(sum(1 for a in self.db.attempts if a["etid"] == p[0]),)]
        elif "INSERT INTO ops_task_attempt" in s and "attempt_number" in s:
            self._ins_attempt(p)
        elif "INSERT INTO ops_task_attempt" in s:  # backfill (no attempt_number col)
            self._ins_backfill(p)
        elif "SELECT attempt_id FROM ops_task_attempt WHERE expected_task_id" in s:
            a = self._find_attempt(p[0], p[1])
            self._rows = [(a["attempt_id"],)] if a else []
        elif "SELECT attempt_id, ecs_task_arn, execution_status, exit_code, record_source" in s:
            self._attempts_for(p)
        elif s.startswith("UPDATE ops_task_attempt SET execution_status"):
            self._upd_attempt(p)
        elif "INSERT INTO ops_reconciliation_issue" in s:
            self._upsert_issue(p)
        elif s.startswith("UPDATE ops_reconciliation_issue SET status='RESOLVED'"):
            self._resolve_issue(p)
        else:
            raise AssertionError(f"FakeOpsDB: 미처리 SQL: {s[:90]}")

    # ── fetch ──
    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    # ── handlers ──
    def _ins_run(self, p):
        run_key = p[1]
        if run_key in self.db.runs:
            self._rows = []  # ON CONFLICT DO NOTHING
            return
        row = {"pipeline_run_id": p[0], "run_key": run_key, "execution_name": p[2],
               "pipeline_type": p[3], "schedule_slot": p[4], "trading_date": p[5],
               "hard_deadline_at": p[6], "input_hash": p[10], "expected_execution_arn": p[11],
               "sfn_execution_arn": None, "launch_status": p[12], "orchestration_status": None}
        self.db.runs[run_key] = row
        self.db.runs_by_id[p[0]] = row
        self._rows = [(p[0],)]

    def _upd_run_launch(self, p):
        # params: (launch_status, sfn_arn, orch, pipeline_run_id)
        row = self.db.runs_by_id.get(p[3])
        if row:
            row["launch_status"] = p[0]
            row["sfn_execution_arn"] = p[1] or row["sfn_execution_arn"]
            row["orchestration_status"] = p[2] or row["orchestration_status"]

    def _get_run(self, p):
        row = self.db.runs.get(p[0])
        if not row:
            self._rows = []
            return
        self._rows = [(row["pipeline_run_id"], row["run_key"], row["execution_name"],
                       row["expected_execution_arn"], row["sfn_execution_arn"],
                       row["launch_status"], row["orchestration_status"],
                       row["hard_deadline_at"], row["trading_date"], row.get("input_hash"))]

    def _ins_etask(self, p):
        key = (p[1], p[2])
        if key in self.db.etasks:
            self._rows = []
            return
        row = {"expected_task_id": p[0], "pipeline_run_id": p[1], "task_key": p[2], "stage": p[3],
               "dataset": p[4], "plan_status": p[5], "task_outcome": p[6], "data_status": p[7],
               "required": p[8], "expected_at": p[9], "deadline_at": p[10], "eligible_at": p[11],
               "expected_as_of_date": p[12], "expectation_snapshot_id": p[13], "skip_reason": p[14],
               "dataset_contract_key": p[16], "dataset_contract_version": p[17],
               "dataset_contract_snapshot": json.loads(p[18]) if p[18] else None,
               "freshness_status": p[19], "freshness_reason": p[20],
               "actual_as_of_date": None, "collected_at": None, "observed_at": None,
               "freshness_evidence": None,
               "missed_at": None, "fulfilled_at": None, "blocked_at": None,
               "outcome_reason": None, "current_attempt_id": None, "completeness": None,
               "records_out": None, "failed_records": None}
        self.db.etasks[key] = row
        self.db.etasks_by_id[p[0]] = row
        self._rows = [(p[0],)]

    def _etasks_for(self, p):
        out = []
        for row in self.db.etasks.values():
            if row["pipeline_run_id"] == p[0]:
                out.append((row["expected_task_id"], row["task_key"], row["stage"],
                            row["plan_status"], row["task_outcome"], row["data_status"],
                            row["required"], row["eligible_at"], row["deadline_at"],
                            row["missed_at"]))
        self._rows = out

    def _set_eligible(self, p):
        row = self.db.etasks_by_id.get(p[0])
        if row and row["eligible_at"] is None:
            row["eligible_at"] = "ELIGIBLE"

    def _upd_etask(self, s, p):
        row = self.db.etasks_by_id.get(p[-1])
        if not row:
            return
        i = 0
        for col in ("task_outcome", "data_status", "outcome_reason", "current_attempt_id"):
            if f"{col}=%s" in s:
                row[col] = p[i]; i += 1
        if "completeness=%s::jsonb" in s:
            row["completeness"] = json.loads(p[i]); i += 1
        # 실제 ledger 의 sets 순서와 같아야 한다 — 어긋나면 파라미터가 밀려 엉뚱한 컬럼에 박힌다.
        for col in ("records_out", "failed_records"):
            if f"{col}=%s" in s:
                row[col] = p[i]; i += 1
        if "actual_as_of_date=%s" in s:
            row["actual_as_of_date"] = p[i]; i += 1
        if "collected_at=now()" in s:
            row["collected_at"] = "SET"
        elif "collected_at=NULL" in s:
            row["collected_at"] = None
        if "observed_at=NULL" in s:
            row["observed_at"] = None
        for col in ("freshness_status", "freshness_reason"):
            if f"{col}=%s" in s:
                row[col] = p[i]; i += 1
        if "freshness_evidence=%s::jsonb" in s:
            row["freshness_evidence"] = json.loads(p[i]) if p[i] else None
            i += 1
        if "fulfilled_at=COALESCE" in s and row["fulfilled_at"] is None:
            row["fulfilled_at"] = "SET"
        if "missed_at=COALESCE" in s and row["missed_at"] is None:
            row["missed_at"] = "SET"
        if "blocked_at=COALESCE" in s and row["blocked_at"] is None:
            row["blocked_at"] = "SET"

    def _find_attempt(self, etid, arn):
        return next((a for a in self.db.attempts if a["etid"] == etid and a["arn"] == arn), None)

    def _ins_attempt(self, p):
        # (new_id, etid, number, arn, status, sfn_arn, sfn_state, source)
        if self._find_attempt(p[1], p[3]):
            self._rows = []
            return
        self.db.attempts.append({"attempt_id": p[0], "etid": p[1], "number": p[2], "arn": p[3],
                                 "status": p[4], "sfn_arn": p[5], "sfn_state": p[6], "source": p[7],
                                 "exit_code": None, "started_at": "STARTED"})
        self._rows = [(p[0],)]

    def _ins_backfill(self, p):
        # (new_id, etid, arn, status, exit_code, sfn_arn, sfn_state, source)
        if self._find_attempt(p[1], p[2]):
            self._rows = []
            return
        self.db.attempts.append({"attempt_id": p[0], "etid": p[1], "arn": p[2], "status": p[3],
                                 "exit_code": p[4], "sfn_arn": p[5], "sfn_state": p[6],
                                 "source": p[7], "number": None, "started_at": "STARTED"})
        self._rows = [(p[0],)]

    def _attempts_for(self, p):
        self._rows = [(a["attempt_id"], a["arn"], a["status"], a["exit_code"], a["source"],
                       a["started_at"]) for a in self.db.attempts if a["etid"] == p[0]]

    def _upd_attempt(self, p):
        # (status, exit_code, failure_reason, data_status, attempt_id)
        a = next((x for x in self.db.attempts if x["attempt_id"] == p[4]), None)
        if a:
            a["status"] = p[0]; a["exit_code"] = p[1]

    def _upsert_issue(self, p):
        # (new_id, issue_type, scope, scope_key, dedupe_key, evidence_json)
        dedupe = p[4]
        existing = next((i for i in self.db.issues if i["dedupe_key"] == dedupe
                         and i["status"] == "OPEN"), None)
        if existing:
            existing["occurrence_count"] += 1
            self._rows = [(existing["issue_id"], False)]
            return
        self.db.issues.append({"issue_id": p[0], "issue_type": p[1], "scope": p[2],
                               "scope_key": p[3], "dedupe_key": dedupe, "status": "OPEN",
                               "occurrence_count": 1,
                               "evidence": json.loads(p[5]) if p[5] else None})
        self._rows = [(p[0], True)]

    def _resolve_issue(self, p):
        # (resolution_reason, resolution_source, dedupe_key)
        found = [i for i in self.db.issues if i["dedupe_key"] == p[2] and i["status"] == "OPEN"]
        for i in found:
            i["status"] = "RESOLVED"; i["resolution_reason"] = p[0]
        self.rowcount = len(found)


class FakeSfn:
    """StepFunctions 클라이언트 더블. start_execution 동작·describe·history 를 주입한다."""

    class ExecutionAlreadyExists(Exception):
        pass

    def __init__(self, *, already_exists=False, describe=None, history=None, start_arn=None,
                 describe_error=False):
        self._already = already_exists
        self._describe = describe or {}
        self._history = history or []
        self._start_arn = start_arn or "arn:aws:states:...:execution:sm:name"
        self._describe_error = describe_error
        self.start_calls = []

    def start_execution(self, *, stateMachineArn, name, input):
        self.start_calls.append({"name": name, "input": input})
        if self._already:
            raise FakeSfn.ExecutionAlreadyExists("already")
        return {"executionArn": self._start_arn}

    def describe_execution(self, *, executionArn):
        if self._describe_error:
            raise RuntimeError("simulated describe/history failure")
        return self._describe

    def get_execution_history(self, *, executionArn, maxResults=1000, nextToken=None):
        # 페이지네이션: history 가 페이지 리스트면 토큰으로 넘긴다.
        if self._history and isinstance(self._history[0], dict) and "events" in self._history[0]:
            idx = int(nextToken) if nextToken else 0
            page = self._history[idx]
            nxt = str(idx + 1) if idx + 1 < len(self._history) else None
            return {"events": page["events"], "nextToken": nxt}
        return {"events": self._history}


class FakeEcs:
    def __init__(self, *, tasks=None):
        self._tasks = tasks or {}   # arn -> {"lastStatus":..., "exitCode":...}

    def describe_tasks(self, *, tasks):
        out = []
        for arn in tasks:
            t = self._tasks.get(arn)
            if t:
                out.append({"lastStatus": t.get("lastStatus", "RUNNING"),
                            "containers": [{"exitCode": t.get("exitCode")}]})
        return {"tasks": out}
