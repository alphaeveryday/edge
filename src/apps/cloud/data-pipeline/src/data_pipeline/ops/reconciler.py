"""Reconciler — 예정(expected_task)과 실제(SFN/ECS 증거)를 대조 (ALPHA-530, 스펙 §7).

멱등하게 동작한다 — 같은 상태를 반복 탐지해도 이슈·알림이 무한 중복되지 않는다(OPEN 부분
유니크 + occurrence_count). 증거 규칙(스펙 §7):

  state 미진입 + eligible + deadline 초과        → MISSED
  state 진입 + RunTask submit 실패 + ECS ARN 없음 → outcome FAILED / FAILED_TO_START (attempt 없음)
  ECS ARN 확인 + attempt 행 없음                  → attempt backfill + LEDGER_GAP
  state 진입했으나 ECS 생성 확인 불가             → EVIDENCE_LOST (MISSED 로 단정 안 함)
  RUNNING + 시간 초과                             → STALLED(health, execution_status 유지)
  컨테이너 exit code 확인                          → 그때만 SUCCEEDED/FAILED 확정
  MISSED 후 늦은 성공                             → FULFILLED (missed_at 보존, MISSED 이슈 RESOLVED)
  hard deadline 뒤 미실행: eligible→MISSED / 미eligible(upstream·gate)→BLOCKED
  schedule 상 있어야 할 run_key 부재              → PLANNER_MISSING
  pipeline_run 있으나 SFN 실행 미확인             → LAUNCH_UNCONFIRMED

**컨테이너 성패는 SFN TaskSucceeded 가 아니라 exit code 로 판정한다** — ecs:runTask.sync 는
컨테이너가 non-zero 로 죽어도 TaskSucceeded 를 낼 수 있고(ASL 이 뒤 CheckExitCode Choice 로
exit code 를 따로 본다), 그래서 TaskSucceeded 를 성공으로 믿으면 실패를 FULFILLED 로 덮는다.
exit code 는 SFN history 이벤트 output(Containers[].ExitCode)에서 얻고, 없으면 ECS DescribeTasks
로 확인한다(둘 다 없으면 단정하지 않는다). SFN 증거 자체를 못 얻으면 MISSED/BLOCKED 를 판정하지
않는다 — "증거 없음"을 "미실행"으로 단정하면 실제 실행 중인 작업이 MISSED 가 된다(스펙 §3.4).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from . import aws, catalog, states
from .ledger import Ledger

logger = logging.getLogger(__name__)

_DEFAULT_STALLED_AFTER_SEC = 3600
_ORCH_MAP = {
    "RUNNING": states.ORCH_RUNNING, "SUCCEEDED": states.ORCH_SUCCEEDED,
    "FAILED": states.ORCH_FAILED, "TIMED_OUT": states.ORCH_TIMED_OUT,
    "ABORTED": states.ORCH_ABORTED,
}


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


def _find_key(obj, key: str):
    """이벤트 detail 에서 key 를 재귀 탐색. JSON 문자열 output 도 파싱해 들어간다(실 SFN 형상)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                return v
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_key(item, key)
            if found is not None:
                return found
    elif isinstance(obj, str) and key in obj:
        try:
            return _find_key(json.loads(obj), key)
        except (ValueError, TypeError):
            return None
    return None


def _find_task_arn(details) -> str | None:
    arn = _find_key(details, "TaskArn")
    return arn if isinstance(arn, str) else None


def _find_exit_code(details) -> int | None:
    ec = _find_key(details, "ExitCode")
    return ec if isinstance(ec, int) and not isinstance(ec, bool) else None


def paginate_history(sfn, execution_arn: str) -> list[dict]:
    """GetExecutionHistory 를 nextToken 까지 모은다(스펙 §7 pagination)."""
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
    """SFN history → state 이름별 증거. Parallel 브랜치가 섞여도 **previousEventId 체인**으로
    각 이벤트를 자기 state 에 귀속시킨다(선형 'current' 추적은 브랜치 교차에서 오귀속한다).

    state → {entered, ecs_task_arn, exit_code, sfn_completed, sfn_terminal_failed, failed_to_start}
    """
    by_id = {ev.get("id"): ev for ev in events if ev.get("id") is not None}
    memo: dict[object, str | None] = {}

    def owning_state(ev) -> str | None:
        # id 없는 이벤트는 memo 키로 쓰지 않는다 — memo[None] 을 저장하면 다른 브랜치의 id 결측
        # 이벤트가 그 값을 재사용해 오귀속한다(edge-review). id 있는 것만 캐싱한다.
        chain: list[object] = []
        cur = ev
        name = None
        while cur is not None:
            eid = cur.get("id")
            if eid is not None and eid in memo:
                name = memo[eid]
                break
            if cur.get("type") == "TaskStateEntered":
                name = cur.get("stateEnteredEventDetails", {}).get("name")
                break
            if eid is not None:
                if eid in chain:  # 순환 방지
                    break
                chain.append(eid)
            prev = cur.get("previousEventId")
            cur = by_id.get(prev) if prev is not None else None
        for eid in chain:
            memo[eid] = name
        return name

    evidence: dict[str, dict] = {}

    def _slot(name: str) -> dict:
        return evidence.setdefault(name, {
            "entered": True, "ecs_task_arn": None, "exit_code": None,
            "sfn_completed": False, "sfn_terminal_failed": False, "failed_to_start": False,
        })

    for ev in events:
        etype = ev.get("type", "")
        if etype == "TaskStateEntered":
            name = ev.get("stateEnteredEventDetails", {}).get("name")
            if name:
                _slot(name)
            continue
        state = owning_state(ev)
        if state is None:
            continue
        e = _slot(state)
        details = next((v for k, v in ev.items() if k.endswith("EventDetails")), None) or {}
        arn = _find_task_arn(details)
        if arn:
            e["ecs_task_arn"] = arn
        exit_code = _find_exit_code(details)
        if exit_code is not None:
            e["exit_code"] = exit_code
        if etype == "TaskSubmitFailed":
            e["failed_to_start"] = True
        if etype == "TaskFailed":
            if e["ecs_task_arn"]:
                e["sfn_terminal_failed"] = True
            else:
                e["failed_to_start"] = True
        if etype == "TaskSucceeded":
            e["sfn_completed"] = True
    return evidence


def _terminal_status(ev: dict | None, ecs, cluster_arn: str | None) -> str | None:
    """증거 + ECS 로 컨테이너 **성패**를 판정. SUCCEEDED/FAILED, 확인 불가면 None(단정 안 함).

    우선순위: SFN output 의 exit code → ECS DescribeTasks exit code. SFN TaskSucceeded 는
    exit code 가 아니므로 성공 근거로 쓰지 않는다.
    """
    if ev is None:
        return None
    if ev.get("exit_code") is not None:
        return states.EXEC_SUCCEEDED if ev["exit_code"] == 0 else states.EXEC_FAILED
    # exit code 가 없으면 **ECS 를 먼저** 물어본다(exit code 정본). ECS 가 확정하면 그걸 쓰고,
    # 미확정(None)일 때에 한해 SFN 통합 실패(TaskFailed)를 실패 신호로 인정한다 — 통합 실패는
    # 컨테이너가 exit code 를 못 낸 진짜 실패라 fail-loud 가 맞다(edge-review).
    if ev.get("ecs_task_arn"):
        ecs_status = _ecs_terminal_status(ecs, ev["ecs_task_arn"], cluster_arn)
        if ecs_status is not None:
            return ecs_status
    if ev.get("sfn_terminal_failed"):
        return states.EXEC_FAILED
    return None


def reconcile_run(
    ledger: Ledger,
    *,
    run_key: str,
    sfn_client=None,
    ecs_client=None,
    cluster_arn: str | None = None,
    now: datetime | None = None,
    stalled_after_seconds: int = _DEFAULT_STALLED_AFTER_SEC,
) -> dict:
    """한 run 을 대조한다. 관측·판정 요약 dict 반환."""
    sfn = sfn_client if sfn_client is not None else aws.stepfunctions_client()
    ecs = ecs_client if ecs_client is not None else aws.ecs_client()
    now = now or datetime.now(timezone.utc)

    run = ledger.get_pipeline_run(run_key)
    if run is None:
        return {"run_key": run_key, "found": False}

    run_id = run["pipeline_run_id"]
    hard_deadline = _parse_ts(run["hard_deadline_at"])

    summary = {"run_key": run_key, "found": True, "orchestration": run["orchestration_status"],
               "evidence_ok": False, "missed": [], "blocked": [], "ledger_gap": [],
               "evidence_lost": [], "failed_to_start": [], "stalled": [], "fulfilled_late": [],
               "failed": []}

    # Planner 가 이미 입력 충돌로 판정한 런은 reconcile 이 뒤엎지 않는다 — 그 이름을 차지한
    # 다른 실행의 history 를 우리 런의 작업에 적용하면 안 된다(edge-review). 충돌 이슈는 유지.
    if run["launch_status"] == states.LAUNCH_CONFLICT:
        summary["launch_conflict"] = True
        return summary

    # 확정된 sfn_execution_arn 은 우리 실행이라 신뢰한다. 없으면 expected_execution_arn(=locator)
    # 인데, **입력 해시로 우리 실행인지 검증**한 뒤에만 채택한다 — 같은 이름에 다른 입력의 실행이
    # 있으면(mode 다른 재계획) 그건 우리 게 아니다(스펙 §4 locator≠증거).
    confirmed_arn = run["sfn_execution_arn"]
    exec_arn = confirmed_arn or run["expected_execution_arn"]
    verify_hash = confirmed_arn is None

    # ── SFN 실행 동기화 ──
    evidence: dict[str, dict] = {}
    if exec_arn:
        try:
            desc = sfn.describe_execution(executionArn=exec_arn)
            confirmed = desc.get("executionArn", exec_arn)
            if verify_hash and run["input_hash"]:
                existing_hash = hashlib.sha256((desc.get("input") or "").encode("utf-8")).hexdigest()
                if existing_hash != run["input_hash"]:
                    # 예상 이름에 다른 입력의 실행 — 우리 게 아니다. 채택 안 하고 충돌로 드러낸다.
                    ledger.set_launch_result(run_id, launch_status=states.LAUNCH_CONFLICT)
                    ledger.open_or_bump_issue(
                        issue_type=states.ISSUE_LAUNCH_CONFLICT,
                        dedupe_key=f"launch_conflict:{run['execution_name']}",
                        scope="run", scope_key=run_id,
                        evidence={"expected_input_hash": run["input_hash"]})
                    summary["launch_conflict"] = True
                    return summary
            ledger.set_launch_result(
                run_id, launch_status=states.LAUNCH_LAUNCHED, sfn_execution_arn=confirmed,
                orchestration_status=_ORCH_MAP.get(desc.get("status", ""), states.ORCH_UNKNOWN))
            evidence = execution_evidence(paginate_history(sfn, confirmed))
            summary["evidence_ok"] = True
        except Exception:
            logger.exception("SFN describe/history 실패 — 증거 없이 판정하지 않는다")

    if not summary["evidence_ok"]:
        # 증거를 못 얻었다. **미실행으로 단정하지 않는다**(실제 실행 중일 수 있다). 다만 Planner
        # 가 pipeline_run 은 남겼는데 SFN 실행이 확인 안 되면(스케줄러 RunTask 성공≠Planner 성공)
        # 그 공백을 LAUNCH_UNCONFIRMED 로 드러낸다(fail-loud, 스펙 §5).
        if run["launch_status"] in (states.LAUNCH_PLANNING, states.LAUNCH_UNKNOWN):
            ledger.open_or_bump_issue(
                issue_type=states.ISSUE_LAUNCH_UNCONFIRMED,
                dedupe_key=f"launch_unconfirmed:{run_id}", scope="run", scope_key=run_id,
                evidence={"run_key": run_key, "launch_status": run["launch_status"]})
            summary["launch_unconfirmed"] = True
        return summary

    deps_done = _completed_task_keys(evidence)
    for task in ledger.expected_tasks_for(run_id):
        if task["plan_status"] == states.PLAN_SKIPPED:
            continue
        _reconcile_task(ledger, task, run_id=run_id, evidence=evidence, ecs=ecs,
                        cluster_arn=cluster_arn, now=now, hard_deadline=hard_deadline,
                        stalled_after_seconds=stalled_after_seconds, deps_done=deps_done,
                        summary=summary)
    return summary


def _completed_task_keys(evidence: dict[str, dict]) -> set[str]:
    """upstream 완료(성공) 판정 — exit0 또는 exit code 없이 완료 신호가 있는 카탈로그 작업."""
    # 선행 완료 = **컨테이너 exit0 확인**. exit code 없는 TaskSucceeded 만으로 완료 처리하면
    # 실제 실패한 선행 뒤의 downstream eligibility 를 잘못 열어 BLOCKED 여야 할 걸 MISSED 로
    # 판정한다(edge-review). exit0 미확인이면 미완으로 본다(보수적 — downstream 은 BLOCKED).
    done: set[str] = set()
    for entry in catalog.entries():
        ev = evidence.get(entry.sfn_state_name)
        if ev and ev.get("exit_code") == 0:
            done.add(entry.task_key)
    return done


def _reconcile_task(ledger, task, *, run_id, evidence, ecs, cluster_arn, now, hard_deadline,
                    stalled_after_seconds, deps_done, summary):
    etid = task["expected_task_id"]
    entry = catalog.get(task["task_key"])
    if entry is None:
        return
    ev = evidence.get(entry.sfn_state_name)
    eligible_at = _parse_ts(task["eligible_at"])
    deadline_at = _parse_ts(task["deadline_at"])
    outcome = task["task_outcome"]

    deps_met = all(d in deps_done for d in entry.depends_on)
    if eligible_at is None and deps_met:
        ledger.set_eligible(etid)
        eligible_at = now

    if ev and ev.get("entered"):
        if ev.get("failed_to_start") and not ev.get("ecs_task_arn"):
            ledger.update_task_outcome(
                etid, task_outcome=states.OUTCOME_FAILED,
                outcome_reason=states.REASON_FAILED_TO_START)
            summary["failed_to_start"].append(task["task_key"])
            return
        if ev.get("ecs_task_arn"):
            _reconcile_entered_with_arn(
                ledger, task, ev, ecs=ecs, cluster_arn=cluster_arn, now=now, outcome=outcome,
                run_id=run_id, stalled_after_seconds=stalled_after_seconds, summary=summary)
            return
        # 진입했으나 ECS 생성 확인 불가 — MISSED 로 단정하지 않는다(스펙 §7).
        _open(ledger, states.ISSUE_EVIDENCE_LOST, f"evidence_lost:{run_id}:{task['task_key']}",
              run_id, task, summary, "evidence_lost")
        return

    # ── state 미진입 ──
    if outcome in (states.OUTCOME_FULFILLED, states.OUTCOME_FAILED):
        return
    past_deadline = deadline_at is not None and now >= deadline_at
    past_hard = hard_deadline is not None and now >= hard_deadline
    if eligible_at is not None and (past_deadline or past_hard):
        ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_MISSED, missed=True)
        _open(ledger, states.ISSUE_MISSED, f"missed:{run_id}:{task['task_key']}",
              run_id, task, summary, "missed")
    elif not deps_met and past_hard:
        ledger.update_task_outcome(
            etid, task_outcome=states.OUTCOME_BLOCKED, blocked=True,
            outcome_reason=states.REASON_DEADLINE_UNMET)
        summary["blocked"].append(task["task_key"])


def _reconcile_entered_with_arn(ledger, task, ev, *, ecs, cluster_arn, now, outcome, run_id,
                                stalled_after_seconds, summary):
    etid = task["expected_task_id"]
    arn = ev["ecs_task_arn"]
    terminal = _terminal_status(ev, ecs, cluster_arn)   # SUCCEEDED/FAILED/None(미확정)
    attempts = ledger.attempts_for(etid)
    matching = next((a for a in attempts if a["ecs_task_arn"] == arn), None)

    if terminal == states.EXEC_SUCCEEDED:
        _ensure_attempt(ledger, etid, arn, states.EXEC_SUCCEEDED, task, summary, matching)
        if outcome != states.OUTCOME_FULFILLED:
            ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_FULFILLED, fulfilled=True)
            if outcome == states.OUTCOME_MISSED:  # 비래치: 늦은 성공(missed_at 보존)
                ledger.resolve_issue(f"missed:{run_id}:{task['task_key']}",
                                     resolution_reason="late_attempt_succeeded",
                                     resolution_source="reconciler")
                summary["fulfilled_late"].append(task["task_key"])
        return
    if terminal == states.EXEC_FAILED:
        _ensure_attempt(ledger, etid, arn, states.EXEC_FAILED, task, summary, matching)
        if outcome != states.OUTCOME_FAILED:
            ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_FAILED,
                                       outcome_reason="attempt_failed")
            summary["failed"].append(task["task_key"])
        return

    # 미확정(ECS STOPPED 증거 없음) — RUNNING 을 뒤집지 않는다. 시간 초과면 STALLED(health).
    if matching is None:
        ledger.backfill_attempt(expected_task_id=etid, ecs_task_arn=arn,
                                execution_status=states.EXEC_RUNNING, sfn_state_name=task["task_key"])
        _open(ledger, states.ISSUE_LEDGER_GAP, f"ledger_gap:{etid}:{arn}", None, task, summary,
              "ledger_gap")
        return
    if matching["execution_status"] == states.EXEC_RUNNING:
        started = _parse_ts(matching.get("started_at"))
        if started is not None and (now - started).total_seconds() > stalled_after_seconds:
            _open(ledger, states.ISSUE_STALLED, f"stalled:{matching['attempt_id']}", None, task,
                  summary, "stalled")


def _ecs_terminal_status(ecs, task_arn: str, cluster_arn: str | None) -> str | None:
    """DescribeTasks 로 실제 종료 확인. STOPPED 면 exit code 로 성패, 아니면 None(단정 안 함).

    **cluster 를 반드시 넘긴다** — 생략하면 default 클러스터를 조회해 실제 태스크를 못 찾는다.
    """
    try:
        kwargs = {"tasks": [task_arn]}
        if cluster_arn:
            kwargs["cluster"] = cluster_arn
        resp = ecs.describe_tasks(**kwargs)
    except Exception:
        logger.exception("ECS describe_tasks 실패 — 상태 미확정")
        return None
    tasks = resp.get("tasks") or []
    if not tasks or tasks[0].get("lastStatus") != "STOPPED":
        return None
    containers = tasks[0].get("containers") or []
    exit_code = containers[0].get("exitCode") if containers else None
    if exit_code is None:
        # STOPPED 인데 exit code 를 모른다 — FAILED 로 단정하지 않는다(exit0 이 유실됐을 수도).
        return None
    return states.EXEC_SUCCEEDED if exit_code == 0 else states.EXEC_FAILED


def _ensure_attempt(ledger, etid, arn, status, task, summary, matching):
    """attempt 가 없으면 backfill + LEDGER_GAP, 있는데 RUNNING 이면 확정 상태로 종료 기록."""
    if matching is None:
        ledger.backfill_attempt(expected_task_id=etid, ecs_task_arn=arn, execution_status=status,
                                sfn_state_name=task["task_key"])
        _open(ledger, states.ISSUE_LEDGER_GAP, f"ledger_gap:{etid}:{arn}", None, task, summary,
              "ledger_gap")
    elif matching["execution_status"] == states.EXEC_RUNNING:
        ledger.record_attempt_end(matching["attempt_id"], execution_status=status)


def _open(ledger, issue_type, dedupe_key, run_id, task, summary, bucket):
    ledger.open_or_bump_issue(
        issue_type=issue_type, dedupe_key=dedupe_key, scope="task",
        scope_key=task["expected_task_id"],
        evidence={"task_key": task["task_key"], "run_id": run_id})
    summary[bucket].append(task["task_key"])


def detect_planner_missing(ledger: Ledger, *, expected_run_keys: list[str]) -> list[str]:
    """schedule 상 있어야 할 run_key 가 원장에 없으면 PLANNER_MISSING, 있으면 열린 이슈 RESOLVE.

    pipeline_run 이 0건이라고 정상으로 보지 않는다(스펙 §7). 늦게 run 이 생기면 거짓 경보를 닫는다.
    호출부(entry)가 **스케줄 시각이 지난 슬롯만** 넘긴다 — 아직 예정 전인 슬롯을 결측으로 보지 않게.
    """
    missing: list[str] = []
    for run_key in expected_run_keys:
        if ledger.get_pipeline_run(run_key) is None:
            ledger.open_or_bump_issue(
                issue_type=states.ISSUE_PLANNER_MISSING,
                dedupe_key=f"planner_missing:{run_key}", scope="slot", scope_key=run_key,
                evidence={"run_key": run_key})
            missing.append(run_key)
        else:
            ledger.resolve_issue(f"planner_missing:{run_key}",
                                 resolution_reason="run_present", resolution_source="reconciler")
    return missing
