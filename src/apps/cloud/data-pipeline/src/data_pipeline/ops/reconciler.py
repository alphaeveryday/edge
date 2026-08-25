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

**증거는 state 이름별이 아니라 실행 발생(occurrence)별로 본다** — 한 state 가 재시도·redrive 로
여러 번 뜨면 각 진입(TaskStateEntered)이 별개 occurrence 다. 이름별로 뭉치면 앞 시도의 exit code
가 남아 새 시도를 오판하고 앞 시도 ARN 을 잃는다(ALPHA-530 #2). occurrence 마다 자기 ECS ARN·exit
code 를 들고, 원장의 `ops_task_attempt`(ARN 별)와 1:1 로 맞물린다. 작업의 최종 outcome 은 **최신
occurrence** 로 판정하되, 각 occurrence 의 물리 attempt 는 모두 기록한다(재시도 이력 보존).

**컨테이너 성패는 SFN TaskSucceeded 가 아니라 exit code 로 판정한다** — ecs:runTask.sync 는
컨테이너가 non-zero 로 죽어도 TaskSucceeded 를 낼 수 있고(ASL 이 뒤 CheckExitCode Choice 로
exit code 를 따로 본다). exit code 는 SFN history 이벤트 output(Containers[].ExitCode)에서 얻고,
없으면 ECS DescribeTasks 로 확인한다(둘 다 없으면 단정하지 않는다). SFN 증거 자체를 못 얻으면
MISSED/BLOCKED 를 판정하지 않는다 — "증거 없음"을 "미실행"으로 단정하지 않는다(스펙 §3.4).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from . import aws, catalog, states
from .ledger import Ledger

logger = logging.getLogger(__name__)

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


# ARN·exit code 를 **믿을 수 있는** 이벤트 — 그 태스크의 ECS 생애주기를 서술하는 것만이다.
# `*StateExited`·Choice·Pass·Parallel 의 details 는 실행 증거가 아니라 **상태 데이터 흐름**이고,
# 그 `output` 에는 앞 페이즈의 누적 JSON 이 그대로 실려 온다. `_find_key` 가 그 JSON 을 파고들기
# 때문에, 이 목록으로 좁히지 않으면 **다른 스텝의 TaskArn·ExitCode 를 주워 와 마지막 값으로
# 덮어쓴다**(ALPHA-566: 실패한 investor 태스크 1개가 성공한 17개 작업에 번져 전부 FAILED).
# 양방향 결함이다 — 순서가 반대면 성공 ARN·exit 0 이 실패 작업을 덮어 거짓 초록이 된다.
_ECS_EVIDENCE_EVENTS = frozenset({
    "TaskSubmitted",    # runTask 응답 — 이 occurrence 의 TaskArn 이 처음 확정되는 곳
    "TaskSucceeded",    # sync 통합의 종료 output(TaskArn + Containers[].ExitCode)
    "TaskFailed",       # 실패 cause 에 같은 정보가 실린다
    "TaskTimedOut",
    "TaskStartFailed",
})


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


def _new_occ(entered_id) -> dict:
    return {"entered_id": entered_id, "ecs_task_arn": None, "exit_code": None,
            "sfn_completed": False, "sfn_terminal_failed": False, "failed_to_start": False}


def execution_evidence(events: list[dict]) -> dict[str, list[dict]]:
    """SFN history → {state 이름: [occurrence, ...]}(시간순). 각 occurrence 는 한 번의 실행 발생.

    이벤트를 **자기 TaskStateEntered 의 id**(occurrence 키)에 귀속시킨다 — previousEventId 체인으로
    올라가 그 진입 이벤트를 찾는다. 이렇게 하면 재시도(같은 state 재진입)가 별 occurrence 로 갈려
    exit code 섞임·ARN 유실이 없다. Parallel 브랜치 교차도 체인이 갈라 처리한다.
    """
    by_id = {ev.get("id"): ev for ev in events if ev.get("id") is not None}
    memo: dict[object, object] = {}   # eid -> 소속 entered-id(occurrence 키)

    def owning_entered_id(ev):
        """이벤트가 속한 TaskStateEntered 의 id(occurrence 키) — previousEventId 체인을 memo 로 거슬러 찾는다."""
        chain: list[object] = []
        cur = ev
        result = None
        while cur is not None:
            eid = cur.get("id")
            if eid is not None and eid in memo:
                result = memo[eid]
                break
            if cur.get("type") == "TaskStateEntered":
                result = eid       # 진입 이벤트 자신의 id 가 occurrence 키
                break
            if eid is not None:
                if eid in chain:   # 순환 방지
                    break
                chain.append(eid)
            prev = cur.get("previousEventId")
            cur = by_id.get(prev) if prev is not None else None
        for eid in chain:
            memo[eid] = result
        return result

    occ_by_id: dict[object, dict] = {}   # entered-id -> occurrence
    name_by_id: dict[object, str] = {}   # entered-id -> state 이름
    for ev in events:
        if ev.get("type") == "TaskStateEntered":
            eid = ev.get("id")
            name = ev.get("stateEnteredEventDetails", {}).get("name")
            if eid is not None and name:
                name_by_id[eid] = name
                occ_by_id.setdefault(eid, _new_occ(eid))

    for ev in events:
        if ev.get("type") == "TaskStateEntered":
            continue
        oid = owning_entered_id(ev)
        occ = occ_by_id.get(oid)
        if occ is None:
            continue
        details = next((v for k, v in ev.items() if k.endswith("EventDetails")), None) or {}
        etype = ev.get("type", "")
        # ECS 생애주기 이벤트에서만 실행 증거를 읽는다(_ECS_EVIDENCE_EVENTS 주석 참조).
        if etype in _ECS_EVIDENCE_EVENTS:
            arn = _find_task_arn(details)
            if arn:
                occ["ecs_task_arn"] = arn
            exit_code = _find_exit_code(details)
            if exit_code is not None:
                occ["exit_code"] = exit_code
        if etype == "TaskSubmitFailed":
            occ["failed_to_start"] = True
        if etype == "TaskFailed":
            if occ["ecs_task_arn"]:
                occ["sfn_terminal_failed"] = True
            else:
                occ["failed_to_start"] = True
        if etype == "TaskSucceeded":
            occ["sfn_completed"] = True

    result: dict[str, list[dict]] = {}
    for eid, occ in sorted(occ_by_id.items(), key=lambda kv: kv[0]):
        name = name_by_id.get(eid)
        if name:
            result.setdefault(name, []).append(occ)
    return result


def _terminal_status(occ: dict | None, ecs, cluster_arn: str | None) -> str | None:
    """occurrence 의 컨테이너 **성패**. SUCCEEDED/FAILED, 확인 불가면 None(단정 안 함).

    우선순위: SFN output exit code → ECS DescribeTasks exit code → (둘 다 없고) SFN 통합실패면 FAILED.
    SFN TaskSucceeded 는 exit code 가 아니므로 성공 근거로 쓰지 않는다.
    """
    if occ is None:
        return None
    if occ.get("exit_code") is not None:
        return states.EXEC_SUCCEEDED if occ["exit_code"] == 0 else states.EXEC_FAILED
    if occ.get("ecs_task_arn"):
        ecs_status = _ecs_terminal_status(ecs, occ["ecs_task_arn"], cluster_arn)
        if ecs_status is not None:
            return ecs_status
    if occ.get("sfn_terminal_failed"):
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
    stalled_after_seconds: int | None = None,
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

    # Planner 가 이미 입력 충돌로 판정한 런은 reconcile 이 뒤엎지 않는다.
    if run["launch_status"] == states.LAUNCH_CONFLICT:
        summary["launch_conflict"] = True
        return summary

    # 확정 sfn_execution_arn 은 우리 실행이라 신뢰. 없으면 expected(locator)를 input_hash 로 검증.
    confirmed_arn = run["sfn_execution_arn"]
    exec_arn = confirmed_arn or run["expected_execution_arn"]
    verify_hash = confirmed_arn is None

    evidence: dict[str, list[dict]] = {}
    used_arn = None
    # 실행이 아직 도는 중인가. **MISSED 판정을 보류하는 근거**다 — deadline 은 작업별 잠정
    # 오프셋이라(카탈로그 주석) SFN 이 정상 실행 중인데도 뒤 스테이지가 아직 진입 안 한 시점을
    # 자주 지난다. "안 돌았다"와 "아직 차례가 아니다"는 다르다(ALPHA-181).
    execution_running = False
    if exec_arn:
        try:
            desc = sfn.describe_execution(executionArn=exec_arn)
            used_arn = desc.get("executionArn", exec_arn)
            if verify_hash and run["input_hash"]:
                existing_hash = hashlib.sha256((desc.get("input") or "").encode("utf-8")).hexdigest()
                if existing_hash != run["input_hash"]:
                    ledger.set_launch_result(run_id, launch_status=states.LAUNCH_CONFLICT)
                    ledger.open_or_bump_issue(
                        issue_type=states.ISSUE_LAUNCH_CONFLICT,
                        dedupe_key=f"launch_conflict:{run['execution_name']}",
                        scope="run", scope_key=run_id,
                        evidence={"expected_input_hash": run["input_hash"]})
                    summary["launch_conflict"] = True
                    return summary
            orchestration = _ORCH_MAP.get(desc.get("status", ""), states.ORCH_UNKNOWN)
            execution_running = orchestration == states.ORCH_RUNNING
            ledger.set_launch_result(
                run_id, launch_status=states.LAUNCH_LAUNCHED, sfn_execution_arn=used_arn,
                orchestration_status=orchestration)
            summary["orchestration"] = orchestration
            evidence = execution_evidence(paginate_history(sfn, used_arn))
            summary["evidence_ok"] = True
        except Exception:
            logger.exception("SFN describe/history 실패 — 증거 없이 판정하지 않는다")

    if not summary["evidence_ok"]:
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
                        sfn_execution_arn=used_arn, stalled_after_seconds=stalled_after_seconds,
                        deps_done=deps_done, execution_running=execution_running, summary=summary)
    return summary


def _completed_task_keys(evidence: dict[str, list[dict]]) -> set[str]:
    """선행 완료 = 어느 occurrence든 컨테이너 exit0 확인. exit code 없는 TaskSucceeded 는 신뢰
    안 한다(edge-review). exit0 미확인이면 미완으로 봐 downstream 은 BLOCKED(보수적·안전)."""
    done: set[str] = set()
    for entry in catalog.entries():
        occs = evidence.get(entry.sfn_state_name, [])
        if any(o.get("exit_code") == 0 for o in occs):
            done.add(entry.task_key)
    return done


def _reconcile_task(ledger, task, *, run_id, evidence, ecs, cluster_arn, now, hard_deadline,
                    sfn_execution_arn, stalled_after_seconds, deps_done, execution_running,
                    summary):
    etid = task["expected_task_id"]
    entry = catalog.get(task["task_key"])
    if entry is None:
        return
    occs = evidence.get(entry.sfn_state_name, [])   # 시간순
    eligible_at = _parse_ts(task["eligible_at"])

    deps_met = all(d in deps_done for d in entry.depends_on)
    if eligible_at is None and deps_met:
        ledger.set_eligible(etid)
        eligible_at = now

    if not occs:
        _judge_not_entered(ledger, task, eligible_at=eligible_at, now=now,
                           hard_deadline=hard_deadline, deps_met=deps_met, run_id=run_id,
                           execution_running=execution_running, summary=summary)
        return

    # 1) 각 occurrence 의 물리 attempt 를 기록/확정한다(outcome 은 안 건드린다).
    for occ in occs:
        occ["_terminal"] = _terminal_status(occ, ecs, cluster_arn)   # 한 번 계산해 재사용
        _reconcile_attempt(ledger, etid, occ, entry=entry, sfn_execution_arn=sfn_execution_arn,
                           now=now, stalled_after_seconds=stalled_after_seconds, task=task,
                           summary=summary)

    # 2) **최신 occurrence** 로 작업 outcome 을 판정한다(재시도면 마지막 시도가 이긴다).
    _judge_outcome(ledger, task, occs[-1], outcome=task["task_outcome"], run_id=run_id,
                   summary=summary)


def _judge_not_entered(ledger, task, *, eligible_at, now, hard_deadline, deps_met, run_id,
                       execution_running, summary):
    etid = task["expected_task_id"]
    outcome = task["task_outcome"]
    if outcome in (states.OUTCOME_FULFILLED, states.OUTCOME_FAILED):
        return
    deadline_at = _parse_ts(task["deadline_at"])
    past_deadline = deadline_at is not None and now >= deadline_at
    past_hard = hard_deadline is not None and now >= hard_deadline
    # 실행이 아직 도는 중이면 **작업별 deadline 만으로 MISSED 를 찍지 않는다**(ALPHA-181).
    # deadline 은 작업별 잠정 오프셋이라(카탈로그: 스테이지별 SLA 가 코드에 없다) 정상 실행
    # 중에도 뒤 스테이지에서 자주 지나간다. MISSED 는 "원래 실행돼야 했는데 아예 시작되지
    # 않았다"는 원장의 핵심 지표이고, `missed_at` 은 COALESCE 라 한 번 찍히면 안 지워진다 —
    # 거짓 양성이 영구 기록된다. 런 전체 하드 데드라인(hard_deadline)은 실행 중이어도 존중한다
    # (그건 "이 런은 이미 끝났어야 한다"는 별개 사실이다).
    if execution_running and past_deadline and not past_hard:
        summary.setdefault("deadline_pending", []).append(task["task_key"])
        return
    if eligible_at is not None and (past_deadline or past_hard):
        ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_MISSED, missed=True)
        _open(ledger, states.ISSUE_MISSED, f"missed:{run_id}:{task['task_key']}",
              run_id, task, summary, "missed")
    elif not deps_met and past_hard:
        ledger.update_task_outcome(
            etid, task_outcome=states.OUTCOME_BLOCKED, blocked=True,
            outcome_reason=states.REASON_DEADLINE_UNMET)
        summary["blocked"].append(task["task_key"])


def _reconcile_attempt(ledger, etid, occ, *, entry, sfn_execution_arn, now, stalled_after_seconds,
                       task, summary):
    """occurrence 하나의 물리 attempt 를 원장에 반영. ARN 없으면 물리 시도가 없다(건너뜀)."""
    arn = occ.get("ecs_task_arn")
    if not arn:
        return
    terminal = occ["_terminal"]
    matching = next((a for a in ledger.attempts_for(etid) if a["ecs_task_arn"] == arn), None)
    if matching is None:
        # ECS ARN 은 있는데 원장에 attempt 없음 — 사후 복구 + LEDGER_GAP. state 이름·실행 ARN 을
        # 채워 attempt↔SFN 계보를 잇는다(#5). exit code 도 남긴다.
        ledger.backfill_attempt(
            expected_task_id=etid, ecs_task_arn=arn,
            execution_status=terminal or states.EXEC_RUNNING,
            sfn_state_name=entry.sfn_state_name, sfn_execution_arn=sfn_execution_arn,
            exit_code=occ.get("exit_code"))
        # 자기 원장 기록이 불가능한 작업(task-def 에 DB env 없음)은 attempt 결측이 **정상**이다 —
        # 위 backfill 이 유일·정확한 경로이므로 LEDGER_GAP 을 열지 않는다. 열면 매 런 새 ARN 으로
        # 새 이슈가 쌓이고(dedupe 키에 ARN 이 들어간다) resolve 경로도 없다(ALPHA-181).
        if entry is None or entry.instrumented:
            _open(ledger, states.ISSUE_LEDGER_GAP, f"ledger_gap:{etid}:{arn}", None, task, summary,
                  "ledger_gap")
        return
    if matching["execution_status"] == states.EXEC_RUNNING:
        if terminal is not None:
            ledger.record_attempt_end(matching["attempt_id"], execution_status=terminal,
                                      exit_code=occ.get("exit_code"))
        else:
            started = _parse_ts(matching.get("started_at"))
            # 작업별 임계가 정본이고, 호출부가 명시하면 그게 이긴다(운영 오버라이드).
            # LLM 스텝은 1시간을 정상적으로 넘고, STALLED 는 resolve 경로가 없어 한 번 열리면
            # 영구 OPEN 이라 전역 상수 하나로 판정하면 안 된다.
            threshold = (stalled_after_seconds if stalled_after_seconds is not None
                         else entry.stalled_after_seconds)
            if started is not None and (now - started).total_seconds() > threshold:
                _open(ledger, states.ISSUE_STALLED, f"stalled:{matching['attempt_id']}", None,
                      task, summary, "stalled")


def _judge_outcome(ledger, task, latest, *, outcome, run_id, summary):
    """작업 최종 outcome 을 **최신 occurrence** 로 판정한다(물리 attempt 는 이미 기록됨)."""
    etid = task["expected_task_id"]
    if latest.get("ecs_task_arn"):
        terminal = latest.get("_terminal")
        if terminal == states.EXEC_SUCCEEDED and outcome != states.OUTCOME_FULFILLED:
            ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_FULFILLED, fulfilled=True)
            if outcome == states.OUTCOME_MISSED:   # 비래치: 늦은 성공(missed_at 보존)
                ledger.resolve_issue(f"missed:{run_id}:{task['task_key']}",
                                     resolution_reason="late_attempt_succeeded",
                                     resolution_source="reconciler")
                summary["fulfilled_late"].append(task["task_key"])
        elif terminal == states.EXEC_FAILED and outcome != states.OUTCOME_FAILED:
            ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_FAILED,
                                       outcome_reason="attempt_failed")
            summary["failed"].append(task["task_key"])
        # terminal None → 미확정, 건드리지 않는다(attempt 는 RUNNING/STALLED 로 이미 처리).
    elif latest.get("failed_to_start"):
        # RunTask submit 실패 + ARN 없음 — 가짜 attempt 안 만들고 outcome 으로만(스펙 §6).
        ledger.update_task_outcome(etid, task_outcome=states.OUTCOME_FAILED,
                                   outcome_reason=states.REASON_FAILED_TO_START)
        summary["failed_to_start"].append(task["task_key"])
    else:
        # 진입했으나 ECS 생성 확인 불가 — MISSED 로 단정하지 않는다(스펙 §7).
        _open(ledger, states.ISSUE_EVIDENCE_LOST, f"evidence_lost:{run_id}:{task['task_key']}",
              run_id, task, summary, "evidence_lost")


def _ecs_terminal_status(ecs, task_arn: str, cluster_arn: str | None) -> str | None:
    """DescribeTasks 로 실제 종료 확인. STOPPED + exit code 있을 때만 성패, 아니면 None.

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
        return None   # STOPPED 인데 exit code 미상 — FAILED 로 단정하지 않는다
    return states.EXEC_SUCCEEDED if exit_code == 0 else states.EXEC_FAILED


def _open(ledger, issue_type, dedupe_key, run_id, task, summary, bucket):
    ledger.open_or_bump_issue(
        issue_type=issue_type, dedupe_key=dedupe_key, scope="task",
        scope_key=task["expected_task_id"],
        evidence={"task_key": task["task_key"], "run_id": run_id})
    summary[bucket].append(task["task_key"])


def detect_planner_missing(ledger: Ledger, *, expected_run_keys: list[str]) -> list[str]:
    """schedule 상 있어야 할 run_key 가 원장에 없으면 PLANNER_MISSING, 있으면 열린 이슈 RESOLVE.

    pipeline_run 이 0건이라고 정상으로 보지 않는다(스펙 §7). 늦게 run 이 생기면 거짓 경보를 닫는다.
    호출부(entry)가 **스케줄 시각이 지난 슬롯만** 넘긴다.
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
