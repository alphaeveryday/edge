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
        self.commits = 0                          # 명시 commit 횟수(실행권 세션의 시작 기록)

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

    def commit(self):
        self.db.commits += 1


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
        elif "SELECT orchestrator FROM ops_pipeline_run WHERE run_key" in s:
            r = self.db.runs.get(p[0])
            self._rows = [(r["orchestrator"],)] if r else []
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
                    row.get("expected_as_of_date"),
                    row.get("records_out"),
                    row.get("current_attempt_id"),
                )]
        elif "SELECT expected_task_id, task_key, stage, plan_status" in s:  # expected_tasks_for
            self._etasks_for(p)
        elif s.startswith("UPDATE ops_expected_task SET eligible_at"):
            self._set_eligible(p, conditional="AND updated_at=%s" in s)
        elif s.startswith("UPDATE ops_expected_task SET"):
            self._upd_etask(s, p, conditional="AND updated_at=%s" in s)
        elif "SELECT count(*) FROM ops_task_attempt" in s:
            self._rows = [(sum(1 for a in self.db.attempts if a["etid"] == p[0]),)]
        elif "execution_status, started_at, finished_at, exit_code, record_source" in s:   # duplicate skip
            if not self._find_attempt(p[1], p[2]):
                self.db.attempts.append({"attempt_id": p[0], "etid": p[1], "number": None, "arn": p[2],
                                         "status": p[3], "sfn_arn": None, "sfn_state": None, "source": p[4],
                                         "orchestrator_attempt_ref": p[5], "exit_code": 0,
                                         "started_at": "STARTED", "quality_diagnostics": None,
                                         "entity_resolution_arguments_total": None,
                                         "entity_resolution_arguments_resolved": None})
        elif "INSERT INTO ops_task_attempt" in s and "attempt_number" in s:
            self._ins_attempt(p)
        elif "INSERT INTO ops_task_attempt" in s:  # backfill (no attempt_number col)
            self._ins_backfill(p)
        elif "SELECT attempt_id FROM ops_task_attempt WHERE expected_task_id" in s:
            a = self._find_attempt(p[0], p[1])
            self._rows = [(a["attempt_id"],)] if a else []
        elif "FROM ops_pipeline_run r LEFT JOIN ops_expected_task et" in s:   # reprocess_ready
            run = self.db.runs.get(p[0])
            raw = [row for row in self.db.etasks.values()
                   if run and row["pipeline_run_id"] == run["pipeline_run_id"] and row["stage"] == "raw"]
            done = [row for row in raw
                    if (row["task_outcome"] == "FULFILLED" and (row.get("records_out") or 0) > 0)
                    or row["plan_status"] == "SKIPPED"]
            self._rows = [(len(raw), len(done))]
        elif "max(a.created_at) FROM ops_expected_task et" in s:   # latest_business_attempts
            # created_at 대신 삽입 순서(index)를 시각의 대역으로 쓴다.
            run_id, skip_source = p
            latest = {}
            for index, a in enumerate(self.db.attempts):
                row = self.db.etasks_by_id.get(a["etid"])
                if row and row["pipeline_run_id"] == run_id and a["source"] != skip_source:
                    latest[row["task_key"]] = (row["stage"], index)
            self._rows = [(k, v[0], v[1]) for k, v in latest.items()]
        elif "SELECT EXISTS (SELECT 1 FROM ops_task_attempt a" in s and "et.stage" in s:   # attempt_created_after
            # created_at 대신 삽입 순서(= DB 시계 순서의 대역)로 비교한다.
            run_id, stages, skip_source, attempt_id = p
            order = [a["attempt_id"] for a in self.db.attempts]
            since = order.index(attempt_id) if attempt_id in order else len(order)
            etids = {row["expected_task_id"] for row in self.db.etasks.values()
                     if row["pipeline_run_id"] == run_id and row["stage"] in stages}
            self._rows = [(any(a["etid"] in etids and a["source"] != skip_source
                               for a in self.db.attempts[since + 1:]),)]
        elif "SELECT EXISTS (SELECT 1 FROM ops_task_attempt a" in s:   # business_attempt_after
            # 보고 시각(대역: 보고 때의 attempt 개수) 뒤에 생긴 업무 시도가 있는가. skip 제외는 **SQL 에
            # 그 조건이 있을 때만** 흉내 낸다 — 조건이 빠진 변이를 대역이 대신 막아 주지 않게.
            excludes = "a.record_source <> %s" in s
            run_id, skip_source, since = p if excludes else (p[0], None, p[1])
            etids = {row["expected_task_id"] for row in self.db.etasks.values()
                     if row["pipeline_run_id"] == run_id}
            self._rows = [(any(a["etid"] in etids and a["source"] != skip_source
                               for a in self.db.attempts[since:]),)]
        elif s.startswith("UPDATE ops_pipeline_run SET orchestration_status=NULL"):
            row = self.db.runs_by_id.get(p[0])
            if row:
                row["orchestration_status"] = None
        elif s.startswith("UPDATE ops_pipeline_run SET orchestration_status"):   # report
            row = self.db.runs_by_id.get(p[1])
            if row:
                row["orchestration_status"] = p[0]
                row["orchestration_reported_at"] = len(self.db.attempts)   # 대역 시각 = attempt 수
        elif "SELECT attempt_id, ecs_task_arn, execution_status, exit_code, record_source" in s:
            self._attempts_for(p)
        elif s.startswith("UPDATE ops_task_attempt SET execution_status"):
            self._upd_attempt(p)
        elif s.startswith("UPDATE ops_task_attempt SET started_at"):
            self._correct_backfill_started_at(p)
        elif ("SELECT a.attempt_id, a.ecs_task_arn, et.pipeline_run_id, a.started_at::text" in s
              and "WHERE et.task_key=%s AND a.execution_status=%s" in s):
            # StepLock.blocking — 같은 task_key(모든 run) 의 RUNNING 시도. 조건이 바뀐 SQL 은 미처리로 떨어진다.
            task_key, running = p
            self._rows = [(a["attempt_id"], a["arn"], self.db.etasks_by_id[a["etid"]]["pipeline_run_id"],
                           str(a.get("started_at")))
                          for a in self.db.attempts
                          if a["status"] == running
                          and self.db.etasks_by_id.get(a["etid"], {}).get("task_key") == task_key]
        elif "SELECT i.dedupe_key, et.pipeline_run_id FROM ops_reconciliation_issue i" in s:
            # 보류 종류 조건은 **SQL 에 그 조건이 있을 때만** 흉내 낸다 — 조건이 빠지거나 뒤집힌 변이를 대역이
            # 대신 막아 주지 않게(결과 미확정 보류가 새 실행을 막으면 안 된다).
            issue_type, task_key, kind = p
            by_kind = "i.evidence->>'kind'=%s" in s
            self._rows = [(i["dedupe_key"], self.db.etasks_by_id[i["scope_key"]]["pipeline_run_id"])
                          for i in self.db.issues
                          if i["issue_type"] == issue_type and i["status"] == "OPEN"
                          and i["scope"] == "task" and i["scope_key"] in self.db.etasks_by_id
                          and self.db.etasks_by_id[i["scope_key"]]["task_key"] == task_key
                          and (not by_kind or (i.get("evidence") or {}).get("kind") == kind)]
        elif "SELECT a.orchestrator_attempt_ref FROM ops_task_attempt a" in s:   # latest_business_attempt_ref
            run_id, skip_source = p
            rows = [a for a in self.db.attempts
                    if self.db.etasks_by_id.get(a["etid"], {}).get("pipeline_run_id") == run_id
                    and a["source"] != skip_source]
            self._rows = [(rows[-1].get("orchestrator_attempt_ref"),)] if rows else []   # 삽입 순서 = created_at
        elif ("SELECT r.run_key FROM ops_task_attempt a" in s
              and " UNION SELECT r.run_key FROM ops_reconciliation_issue i JOIN ops_expected_task et ON"
                  " i.scope='task' AND et.expected_task_id = i.scope_key" in s
              and "WHERE i.issue_type=%s AND i.status='OPEN' AND i.dedupe_key LIKE %s" in s):
            # airflow_run_keys_with_open_attempts — 두 갈래 조건이 SQL 에 그대로 있을 때만 응답한다(변이 방어)
            running, orchestrator, issue_type, like, _ = p
            suffix = like.lstrip("%")
            keys = set()
            for a in self.db.attempts:
                et = self.db.etasks_by_id.get(a["etid"])
                run = et and self.db.runs_by_id.get(et["pipeline_run_id"])
                if a["status"] == running and run and run.get("orchestrator") == orchestrator:
                    keys.add(run["run_key"])
            for i in self.db.issues:
                et = self.db.etasks_by_id.get(i["scope_key"]) if i["scope"] == "task" else None
                run = et and self.db.runs_by_id.get(et["pipeline_run_id"])
                if (i["issue_type"] == issue_type and i["status"] == "OPEN" and i["dedupe_key"].endswith(suffix)
                        and run and run.get("orchestrator") == orchestrator):
                    keys.add(run["run_key"])
            self._rows = [(k,) for k in sorted(keys)]
        elif "SELECT run_key, orchestrator FROM ops_pipeline_run WHERE pipeline_run_id" in s:
            run = self.db.runs_by_id.get(p[0])
            self._rows = [(run["run_key"], run.get("orchestrator", "SFN"))] if run else []
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
               "sfn_execution_arn": None, "launch_status": p[12], "orchestration_status": None,
               "orchestrator": p[13], "orchestrator_run_ref": p[14]}
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
                       row["hard_deadline_at"], row["trading_date"], row.get("input_hash"),
                       row.get("orchestrator", "SFN"), row.get("orchestrator_run_ref"),
                       row.get("orchestration_reported_at"))]

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
               "records_out": None, "unsupported_records": None, "failed_records": None,
               "entity_resolution_arguments_total": None,
               "entity_resolution_arguments_resolved": None, "updated_at": 1}
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
                            row["missed_at"], row.get("updated_at", 1),
                            row.get("outcome_reason")))
        self._rows = out

    def _set_eligible(self, p, *, conditional=False):
        row = self.db.etasks_by_id.get(p[0])
        if row:
            if conditional and row.get("updated_at", 1) != p[1]:
                return
            if row["eligible_at"] is None:
                row["eligible_at"] = "ELIGIBLE"
            row["updated_at"] = row.get("updated_at", 1) + 1
            self._rows = [(row["updated_at"],)]

    def _upd_etask(self, s, p, *, conditional=False):
        expected_task_id = p[-2] if conditional else p[-1]
        row = self.db.etasks_by_id.get(expected_task_id)
        if not row:
            return
        if conditional and row.get("updated_at", 1) != p[-1]:
            return
        i = 0
        for col in ("task_outcome", "data_status", "outcome_reason", "current_attempt_id"):
            if f"{col}=%s" in s:
                row[col] = p[i]; i += 1
        if "outcome_reason=NULL" in s:
            row["outcome_reason"] = None
        if "completeness=%s::jsonb" in s:
            row["completeness"] = json.loads(p[i]); i += 1
        # 실제 ledger 의 sets 순서와 같아야 한다 — 어긋나면 파라미터가 밀려 엉뚱한 컬럼에 박힌다.
        for col in ("records_out", "unsupported_records", "failed_records",
                    "entity_resolution_arguments_total",
                    "entity_resolution_arguments_resolved"):
            if f"{col}=%s" in s:
                row[col] = p[i]; i += 1
        if "actual_as_of_date=%s" in s:
            row["actual_as_of_date"] = p[i]; i += 1
        if "collected_at=now()" in s:
            row["collected_at"] = "SET"
        elif "collected_at=NULL" in s:
            row["collected_at"] = None
        if "observed_at=now()" in s:
            row["observed_at"] = "SET"
        elif "observed_at=NULL" in s:
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
        row["updated_at"] = row.get("updated_at", 1) + 1
        self.rowcount = 1

    def _find_attempt(self, etid, arn):
        return next((a for a in self.db.attempts if a["etid"] == etid and a["arn"] == arn), None)

    def _ins_attempt(self, p):
        # (new_id, etid, number, arn, status, sfn_arn, sfn_state, source)
        if self._find_attempt(p[1], p[3]):
            self._rows = []
            return
        self.db.attempts.append({"attempt_id": p[0], "etid": p[1], "number": p[2], "arn": p[3],
                                 "status": p[4], "sfn_arn": p[5], "sfn_state": p[6], "source": p[7],
                                 "orchestrator_attempt_ref": p[8] if len(p) > 8 else None,
                                 "exit_code": None, "started_at": "STARTED",
                                 "quality_diagnostics": None,
                                 "entity_resolution_arguments_total": None,
                                 "entity_resolution_arguments_resolved": None})
        self._rows = [(p[0],)]

    def _ins_backfill(self, p):
        # (new_id, etid, arn, status, exit_code, sfn_arn, sfn_state, source, started_at)
        if self._find_attempt(p[1], p[2]):
            self._rows = []
            return
        self.db.attempts.append({"attempt_id": p[0], "etid": p[1], "arn": p[2], "status": p[3],
                                 "exit_code": p[4], "sfn_arn": p[5], "sfn_state": p[6],
                                 "source": p[7], "number": None,
                                 "started_at": p[8] or "STARTED",
                                 "quality_diagnostics": None,
                                 "entity_resolution_arguments_total": None,
                                 "entity_resolution_arguments_resolved": None})
        self._rows = [(p[0],)]

    def _attempts_for(self, p):
        self._rows = [(a["attempt_id"], a["arn"], a["status"], a["exit_code"], a["source"],
                       a["started_at"]) for a in self.db.attempts if a["etid"] == p[0]]

    def _upd_attempt(self, p):
        # 기본: (status, exit_code, failure_reason, data_status, quality_json, attempt_id)
        # 해소 pair 동반: 위 다섯 값 + (total, resolved, attempt_id)
        a = next((x for x in self.db.attempts if x["attempt_id"] == p[-1]), None)
        if a:
            a["status"] = p[0]; a["exit_code"] = p[1]
            a["failure_reason"] = p[2]
            if p[4]:
                a["quality_diagnostics"] = json.loads(p[4])
            if len(p) == 8:
                a["entity_resolution_arguments_total"] = p[5]
                a["entity_resolution_arguments_resolved"] = p[6]

    def _correct_backfill_started_at(self, p):
        # (started_at, attempt_id, record_source, started_at)
        a = next((x for x in self.db.attempts if x["attempt_id"] == p[1]), None)
        if a and a["source"] == p[2] and a["started_at"] != p[0]:
            a["started_at"] = p[0]
            self.rowcount = 1

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
                task = {"lastStatus": t.get("lastStatus", "RUNNING"),
                        "containers": [{"exitCode": t.get("exitCode")}]}
                if "stopCode" in t:
                    task["stopCode"] = t["stopCode"]
                out.append(task)
        return {"tasks": out}
