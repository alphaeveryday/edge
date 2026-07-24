"""Reconciler — 예정(expected_task)과 실제(SFN/ECS 증거)를 대조 (ALPHA-530, 스펙 §7).

멱등하게 동작한다 — 같은 상태를 반복 탐지해도 이슈·알림이 무한 중복되지 않는다(OPEN 부분
유니크 + occurrence_count). 증거 규칙(스펙 §7):

  state 미진입 + eligible + deadline 초과        → MISSED
  state 진입 + RunTask submit 실패 + ECS ARN 없음 → outcome FAILED / FAILED_TO_START (attempt 없음)
  ECS ARN 확인 + attempt 행 없음                  → attempt backfill + LEDGER_GAP
  state 진입했으나 ECS 생성 확인 불가             → EVIDENCE_LOST (MISSED 로 단정 안 함)
  RUNNING + 시간 초과                             → STALLED(health, execution_status 유지)
  ECS STOPPED 확인                                → 그때만 실제 결과로 전이
  MISSED 후 늦은 성공                             → FULFILLED (missed_at 보존, MISSED 이슈 RESOLVED)
  hard deadline 뒤 미실행: eligible→MISSED / 미eligible(upstream·gate)→BLOCKED
  schedule 상 있어야 할 run_key 부재              → PLANNER_MISSING

"attempt 행 없음"만으로 MISSED 를 판정하지 않는다 — 원장 누락과 실제 미실행을 SFN/ECS 증거로
구분한다(스펙 §3.4).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import aws, catalog, states
from .ledger import Ledger

logger = logging.getLogger(__name__)

_DEFAULT_STALLED_AFTER_SEC = 3600


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _find_task_arn(obj) -> str | None:
    """이벤트 detail 에서 ECS Task ARN 을 재귀 탐색(키 'TaskArn'). SFN 이벤트 형상 변화에 견고.

    SFN history 의 output/parameters 는 **JSON 문자열**이라 문자열도 파싱해 재귀한다(실 데이터 형상).
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "TaskArn" and isinstance(v, str):
                return v
            found = _find_task_arn(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_task_arn(item)
            if found:
                return found
    elif isinstance(obj, str) and ("TaskArn" in obj):
        try:
            return _find_task_arn(json.loads(obj))
        except (ValueError, TypeError):
            return None
    return None


def paginate_history(sfn, execution_arn: str) -> list[dict]:
    """GetExecutionHistory 를 nextToken 까지 모은다(스펙 §7 pagination 포함)."""
    events: list[dict] = []
    token = None
    while True:
        kwargs = {"executionArn": execution_arn, "maxResults": 1000}
        if token:
            kwargs["nextToken"] = token
        resp = sfn.get_execution_history(**kwargs)
        events.extend(resp.get("events", []))
        token = resp.get("nextToken")
        if not token:
            break
    return events


def execution_evidence(events: list[dict]) -> dict[str, dict]:
    """SFN history → state 이름별 증거. {entered, ecs_task_arn, terminal, failed_to_start}.

    TaskStateEntered 로 현재 state 를 잡고, 이후 Task* 이벤트를 그 state 에 귀속시킨다. ECS Task
    ARN 은 detail 재귀 탐색으로 얻는다(TaskSubmitted 등). submit 단계 실패는 failed_to_start.
    """
    evidence: dict[str, dict] = {}
    current: str | None = None
    for ev in events:
        etype = ev.get("type", "")
        if etype == "TaskStateEntered":
            current = ev.get("stateEnteredEventDetails", {}).get("name")
            if current:
                evidence.setdefault(current, {
                    "entered": True, "ecs_task_arn": None, "terminal": None,
                    "failed_to_start": False,
                })
            continue
        if current is None:
            continue
        e = evidence.get(current)
        if e is None:
            continue
        details = next((v for k, v in ev.items() if k.endswith("EventDetails")), None) or {}
        arn = _find_task_arn(details)
        if arn:
            e["ecs_task_arn"] = arn
        if etype == "TaskSubmitFailed":
            e["failed_to_start"] = True
        if etype == "TaskFailed" and not e["ecs_task_arn"]:
            e["failed_to_start"] = True
        if etype in ("TaskSucceeded",):
            e["terminal"] = "SUCCEEDED"
        if etype == "TaskFailed" and e["ecs_task_arn"]:
            e["terminal"] = "FAILED"
    return evidence


_ORCH_MAP = {
    "RUNNING": states.ORCH_RUNNING, "SUCCEEDED": states.ORCH_SUCCEEDED,
    "FAILED": states.ORCH_FAILED, "TIMED_OUT": states.ORCH_TIMED_OUT,
    "ABORTED": states.ORCH_ABORTED,
}


def reconcile_run(
    ledger: Ledger,
    *,
    run_key: str,
    sfn_client=None,
    ecs_client=None,
    now: datetime | None = None,
    stalled_after_seconds: int = _DEFAULT_STALLED_AFTER_SEC,
) -> dict:
    """한 run 을 대조한다. 관측·판정 요약 dict 반환(테스트·로깅용)."""
    sfn = sfn_client if sfn_client is not None else aws.stepfunctions_client()
    ecs = ecs_client if ecs_client is not None else aws.ecs_client()
    now = now or datetime.now(timezone.utc)

    run = ledger.get_pipeline_run(run_key)
    if run is None:
        return {"run_key": run_key, "found": False}

    run_id = run["pipeline_run_id"]
    exec_arn = run["sfn_execution_arn"] or run["expected_execution_arn"]
    hard_deadline = _parse_ts(run["hard_deadline_at"])

    # ── SFN 실행 동기화(불분명 launch 확정 포함) ──
    evidence: dict[str, dict] = {}
    orchestration = run["orchestration_status"]
    if exec_arn:
        try:
            desc = sfn.describe_execution(executionArn=exec_arn)
            orchestration = _ORCH_MAP.get(desc.get("status", ""), states.ORCH_UNKNOWN)
            launch = states.LAUNCH_LAUNCHED  # describe 성공 = 실행 존재 확인
            ledger.set_launch_result(
                run_id, launch_status=launch,
                sfn_execution_arn=desc.get("executionArn", exec_arn),
                orchestration_status=orchestration,
            )
            evidence = execution_evidence(paginate_history(sfn, desc.get("executionArn", exec_arn)))
        except Exception:
            logger.exception("SFN describe/history 실패 — 증거 없이 진행(단정 금지)")

    summary = {"run_key": run_key, "found": True, "orchestration": orchestration,
               "missed": [], "blocked": [], "ledger_gap": [], "evidence_lost": [],
               "failed_to_start": [], "stalled": [], "fulfilled_late": []}

    deps_done = _completed_task_keys(ledger, run_id, evidence)

    for task in ledger.expected_tasks_for(run_id):
        if task["plan_status"] == states.PLAN_SKIPPED:
            continue
        _reconcile_task(
            ledger, task, run_id=run_id, evidence=evidence, ecs=ecs, now=now,
            hard_deadline=hard_deadline, stalled_after_seconds=stalled_after_seconds,
            deps_done=deps_done, summary=summary,
        )
    return summary


def _completed_task_keys(ledger: Ledger, run_id: str, evidence: dict[str, dict]) -> set[str]:
    """upstream 완료 판정용 — SUCCEEDED 증거가 있는 카탈로그 task_key 집합."""
    done: set[str] = set()
    for entry in catalog.entries():
        ev = evidence.get(entry.sfn_state_name)
        if ev and ev.get("terminal") == "SUCCEEDED":
            done.add(entry.task_key)
    return done


def _reconcile_task(ledger, task, *, run_id, evidence, ecs, now, hard_deadline,
                    stalled_after_seconds, deps_done, summary):
    etid = task["expected_task_id"]
    entry = catalog.get(task["task_key"])
    if entry is None:
        return
    ev = evidence.get(entry.sfn_state_name)
    eligible_at = _parse_ts(task["eligible_at"])
    deadline_at = _parse_ts(task["deadline_at"])
    outcome = task["task_outcome"]

    # eligibility: 정적 의존이 전부 완료면 eligible(아직 미표기면 지금 표기).
    deps_met = all(d in deps_done for d in entry.depends_on)
    if eligible_at is None and deps_met:
        ledger.set_eligible(etid)
        eligible_at = now

    # 늦은 성공 → 비래치: MISSED 였어도 FULFILLED 로(missed_at 보존, 스펙 §7).
    if ev and ev.get("terminal") == "SUCCEEDED":
        _ensure_attempt(ledger, etid, ev, task, summary)
        if outcome != states.OUTCOME_FULFILLED:
            ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_FULFILLED, fulfilled=True)
            if outcome == states.OUTCOME_MISSED:
                ledger.resolve_issue(
                    f"missed:{run_id}:{task['task_key']}",
                    resolution_reason="late_attempt_succeeded",
                    resolution_source="reconciler",
                )
                summary["fulfilled_late"].append(task["task_key"])
        return

    if ev and ev.get("entered"):
        if ev.get("failed_to_start") and not ev.get("ecs_task_arn"):
            # RunTask submit/start 실패 — 가짜 attempt 안 만들고 outcome 으로만 드러낸다(스펙 §6).
            ledger.update_task_outcome(
                etid, task_outcome=states.OUTCOME_FAILED,
                outcome_reason=states.REASON_FAILED_TO_START,
            )
            summary["failed_to_start"].append(task["task_key"])
            return
        if ev.get("ecs_task_arn"):
            _reconcile_entered_with_arn(
                ledger, task, ev, ecs=ecs, now=now,
                stalled_after_seconds=stalled_after_seconds, summary=summary)
            return
        # 진입했으나 ECS 생성 확인 불가 — MISSED 로 단정하지 않는다(스펙 §7).
        _open(ledger, states.ISSUE_EVIDENCE_LOST, f"evidence_lost:{run_id}:{task['task_key']}",
              run_id, task, summary, "evidence_lost")
        return

    # ── state 미진입 ──
    past_deadline = deadline_at is not None and now >= deadline_at
    past_hard = hard_deadline is not None and now >= hard_deadline
    if outcome in (states.OUTCOME_FULFILLED, states.OUTCOME_FAILED):
        return
    if eligible_at is not None and (past_deadline or past_hard):
        ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_MISSED, missed=True)
        _open(ledger, states.ISSUE_MISSED, f"missed:{run_id}:{task['task_key']}",
              run_id, task, summary, "missed")
    elif not deps_met and past_hard:
        # 끝까지 eligible 하지 못함(upstream/gate) → MISSED 아니라 BLOCKED(스펙 §7).
        ledger.update_task_outcome(
            etid, task_outcome=states.OUTCOME_BLOCKED, blocked=True,
            outcome_reason=states.REASON_DEADLINE_UNMET)
        summary["blocked"].append(task["task_key"])


def _reconcile_entered_with_arn(ledger, task, ev, *, ecs, now, stalled_after_seconds, summary):
    etid = task["expected_task_id"]
    arn = ev["ecs_task_arn"]
    attempts = ledger.attempts_for(etid)
    matching = next((a for a in attempts if a["ecs_task_arn"] == arn), None)

    if matching is None:
        # ECS ARN 은 있는데 원장에 attempt 없음 — 사후 복구 + LEDGER_GAP(스펙 §7).
        status = _ecs_terminal_status(ecs, arn) or (
            states.EXEC_SUCCEEDED if ev.get("terminal") == "SUCCEEDED"
            else states.EXEC_FAILED if ev.get("terminal") == "FAILED" else states.EXEC_RUNNING)
        ledger.backfill_attempt(
            expected_task_id=etid, ecs_task_arn=arn, execution_status=status,
            sfn_state_name=task["task_key"])
        _open(ledger, states.ISSUE_LEDGER_GAP, f"ledger_gap:{etid}:{arn}", None, task, summary,
              "ledger_gap")
        return

    # RUNNING attempt 의 ECS 실제 상태 확인 — 증거 없이 RUNNING 을 뒤집지 않는다(스펙 §7).
    if matching["execution_status"] == states.EXEC_RUNNING:
        terminal = _ecs_terminal_status(ecs, arn)
        if terminal is not None:
            ledger.record_attempt_end(matching["attempt_id"], execution_status=terminal)
        else:
            # STOPPED 증거가 없다 — RUNNING 유지, 시간 초과면 STALLED(health, 컬럼 아님).
            started = _parse_ts(matching.get("started_at"))
            if started is not None and (now - started).total_seconds() > stalled_after_seconds:
                _open(ledger, states.ISSUE_STALLED,
                      f"stalled:{matching['attempt_id']}", None, task, summary, "stalled")


def _ecs_terminal_status(ecs, task_arn: str) -> str | None:
    """DescribeTasks 로 실제 종료 확인. STOPPED 면 exit code 로 성패, 아니면 None(단정 안 함)."""
    try:
        resp = ecs.describe_tasks(tasks=[task_arn])
    except Exception:
        logger.exception("ECS describe_tasks 실패 — 상태 미확정")
        return None
    tasks = resp.get("tasks") or []
    if not tasks:
        return None
    t = tasks[0]
    if t.get("lastStatus") != "STOPPED":
        return None
    containers = t.get("containers") or []
    exit_code = containers[0].get("exitCode") if containers else None
    return states.EXEC_SUCCEEDED if exit_code == 0 else states.EXEC_FAILED


def _ensure_attempt(ledger, etid, ev, task, summary):
    """SUCCEEDED 증거가 있는데 원장에 attempt 가 없으면 backfill + LEDGER_GAP(스펙 §7)."""
    arn = ev.get("ecs_task_arn")
    if not arn:
        return
    if not any(a["ecs_task_arn"] == arn for a in ledger.attempts_for(etid)):
        ledger.backfill_attempt(
            expected_task_id=etid, ecs_task_arn=arn, execution_status=states.EXEC_SUCCEEDED)
        _open(ledger, states.ISSUE_LEDGER_GAP, f"ledger_gap:{etid}:{arn}", None, task, summary,
              "ledger_gap")


def _open(ledger, issue_type, dedupe_key, run_id, task, summary, bucket):
    ledger.open_or_bump_issue(
        issue_type=issue_type, dedupe_key=dedupe_key, scope="task",
        scope_key=task["expected_task_id"],
        evidence={"task_key": task["task_key"], "run_id": run_id})
    summary[bucket].append(task["task_key"])


def detect_planner_missing(ledger: Ledger, *, expected_run_keys: list[str]) -> list[str]:
    """schedule 상 있어야 할 run_key 가 원장에 없으면 PLANNER_MISSING(스펙 §7).

    pipeline_run 이 0건이라고 정상으로 보지 않는다 — 예상 슬롯을 계산해 부재를 이슈로 만든다.
    """
    missing: list[str] = []
    for run_key in expected_run_keys:
        if ledger.get_pipeline_run(run_key) is None:
            ledger.open_or_bump_issue(
                issue_type=states.ISSUE_PLANNER_MISSING,
                dedupe_key=f"planner_missing:{run_key}", scope="slot", scope_key=run_key,
                evidence={"run_key": run_key})
            missing.append(run_key)
    return missing
