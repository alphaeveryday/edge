"""장중 수급 레인의 기존 경로 vs Airflow 경로 로컬 비교 구동기(호스트 python3, 표준 라이브러리만).

기존 경로 = EventBridge 가 하던 ECS `plan-run` → Planner → 배포 ASL(대역 SFN 해석) → ECS 스텝.
Airflow 경로 = DAG `edge_investor_intraday` → plan-run(OPS_ORCHESTRATOR=AIRFLOW) → ECS 스텝.
두 경로 모두 같은 ECS 대역·같은 업무 코드·같은 저장 입력을 쓴다.

    python3 lab.py up | reset | legacy <slot> | airflow <slot> | snapshot <name> | scenarios
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "raw"
FAKE = "http://127.0.0.1:54566"
DAG = "edge_investor_intraday"
LANE = "investor-intraday"
SM_ARN = "arn:aws:states:ap-northeast-2:000000000000:stateMachine:edge-dev-data-pipeline-investor-intraday"
SLOTS = {"2026-09-22": ["09:35", "10:05", "11:25", "13:25", "14:35"],
         "2026-09-23": ["09:35", "10:05", "11:25", "13:25", "14:35"]}
LOG: list[dict] = []


def note(event: str, **fields) -> None:
    record = {"t": round(time.time(), 1), "event": event, **fields}
    LOG.append(record)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def sh(*cmd: str, stdin: str | None = None, check: bool = True, env: dict | None = None) -> str:
    import os
    proc = subprocess.run(cmd, cwd=HERE, input=stdin, capture_output=True, text=True,
                          env={**os.environ, **(env or {})})
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def compose(*args: str, **kw) -> str:
    return sh("docker", "compose", *args, **kw)


def psql(sql: str) -> str:
    return compose("exec", "-T", "postgres", "psql", "-U", "edge", "-d", "edge", "-At", "-v",
                   "ON_ERROR_STOP=1", "-c", sql)


def airflow(*args: str, check: bool = True) -> str:
    return compose("exec", "-T", "airflow", "airflow", *args, check=check)


def fake(path: str, body: dict | None = None, target: str | None = None) -> dict:
    req = urllib.request.Request(FAKE + path, data=json.dumps(body or {}).encode(),
                                 method="POST" if body is not None or target else "GET")
    if target:
        req.add_header("X-Amz-Target", target)
        req.add_header("Content-Type", "application/x-amz-json-1.1")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def slot_iso(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00+09:00"


def faults(rules: list[dict]) -> None:
    compose("exec", "-T", "fake-aws", "sh", "-c",
            "mkdir -p /lab-data/state && cat > /lab-data/state/faults.json", stdin=json.dumps(rules))


# ── 초기화 ────────────────────────────────────────────────────────────
def idle_ecs(timeout: float = 300) -> None:
    """대역에서 도는 업무 프로세스가 없을 때까지 기다린다(초기화 중 뒤늦은 쓰기 방지)."""
    deadline = time.monotonic() + timeout
    while any(t["lastStatus"] == "RUNNING" for t in fake("/_lab/state")["tasks"]):
        if time.monotonic() > deadline:
            raise TimeoutError("ECS 대역이 비지 않는다")
        time.sleep(1)


def reset() -> None:
    # 도는 DAG run 이 있으면 아무것도 지우지 않고 먼저 실패한다 — ECS 대역만 비우고 DAG 삭제가 실패하면 남은 task 가
    # 사라진 ECS 태스크를 영영 기다린다(로컬 관찰).
    active = [r["run_id"] for r in json.loads(airflow("dags", "list-runs", DAG, "-o", "json", check=False) or "[]")
              if r["state"] in ("running", "queued")]
    if active:
        raise RuntimeError(f"초기화 전 도는 DAG run: {active}")
    idle_ecs()
    fake("/_lab/reset", {})
    compose("exec", "-T", "fake-aws", "sh", "-c",
            "rm -rf /lab-data/lake /lab-data/state /lab-data/ecs-logs && mkdir -p /lab-data/state")
    tickers = sorted({json.loads(line)["our_ticker"]
                      for f in (HERE / "inputs/dev-lake/raw").rglob("*.ndjson")
                      for line in f.read_text().splitlines() if line.strip()})
    values = ",".join(f"('lab_{t}','{t}')" for t in tickers)
    psql("TRUNCATE investor_flow_intraday, ops_task_attempt, ops_reconciliation_issue,"
         " ops_expectation_snapshot, ops_expected_task, ops_pipeline_run CASCADE;"
         # fixture: 종목 마스터는 비교 대상이 아니다 — 입력에 나온 ticker 만 XKRX 로 등록한다.
         f" INSERT INTO entity (entity_id, entity_type, display_name) SELECT id, 'INSTRUMENT', t"
         f"  FROM (VALUES {values}) v(id, t) ON CONFLICT DO NOTHING;"
         f" INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type, currency_code)"
         f"  SELECT id, 'XKRX', t, 'EQUITY', 'KRW' FROM (VALUES {values}) v(id, t) ON CONFLICT DO NOTHING;")
    airflow("dags", "reserialize")                 # 앞선 delete 뒤 미등록 상태여도 지울 수 있게
    airflow("dags", "delete", DAG, "-y")
    airflow("dags", "reserialize")
    airflow("dags", "unpause", DAG)
    leftover = json.loads(airflow("dags", "list-runs", DAG, "-o", "json") or "[]")
    if leftover:
        raise RuntimeError(f"초기화 뒤에도 DAG run 이 남았다: {[r['run_id'] for r in leftover]}")
    note("reset", tickers=len(tickers))


# ── 기존 경로 ─────────────────────────────────────────────────────────
def ecs_run(command: list[str], env: dict, taskdef: str) -> str:
    resp = fake("/", {"cluster": "lab", "taskDefinition": taskdef, "overrides": {"containerOverrides": [
        {"name": "data-pipeline", "command": command,
         "environment": [{"name": k, "value": v} for k, v in env.items()]}]}},
        target="AmazonEC2ContainerServiceV20141113.RunTask")
    return resp["tasks"][0]["taskArn"]


def ecs_wait(arn: str) -> int | None:
    while True:
        task = fake("/", {"tasks": [arn]}, target="AmazonEC2ContainerServiceV20141113.DescribeTasks")["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            return task["containers"][0].get("exitCode")
        time.sleep(0.5)


def legacy(day: str, hhmm: str) -> dict:
    """EventBridge 1회 발화 재현: ops 태스크 plan-run → Planner 가 SFN 시작 → 실행 종료까지 대기."""
    started = time.monotonic()
    iso = slot_iso(day, hhmm)
    arn = ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": iso, "OPS_PIPELINE_TYPE": LANE,
                                 "OPS_INVESTOR_INTRADAY_STATE_MACHINE_ARN": SM_ARN},
                  "edge-dev-data-pipeline-ops")
    plan_exit = ecs_wait(arn)
    name = f"{LANE}-{day}T{hhmm.replace(':', '-')}"
    status = None
    while plan_exit == 0:
        ex = next((e for e in fake("/_lab/state")["executions"] if e["name"] == name), None)
        if ex and ex["status"] != "RUNNING":
            status = ex["status"]
            break
        time.sleep(0.5)
    result = {"slot": iso, "plan_exit": plan_exit, "sfn": status,
              "seconds": round(time.monotonic() - started, 1)}
    note("legacy", **result)
    return result


# ── Airflow 경로 ──────────────────────────────────────────────────────
def af_trigger(run_id: str, logical: str | None, conf: dict | None = None) -> str:
    args = ["dags", "trigger", DAG, "--run-id", run_id]
    if logical:
        args += ["--logical-date", logical]
    if conf:
        args += ["--conf", json.dumps(conf)]
    proc = subprocess.run(["docker", "compose", "exec", "-T", "airflow", "airflow", *args], cwd=HERE,
                          capture_output=True, text=True)
    return proc.stdout + proc.stderr


def af_run(run_id: str) -> dict | None:
    runs = json.loads(airflow("dags", "list-runs", DAG, "-o", "json") or "[]")
    return next((r for r in runs if r["run_id"] == run_id), None)


def af_tasks(run_id: str) -> dict:
    rows = json.loads(airflow("tasks", "states-for-dag-run", DAG, run_id, "-o", "json") or "[]")
    return {r["task_id"]: r["state"] for r in rows}


def af_wait(run_id: str, timeout: float = 900) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = af_run(run_id)
        if run and run["state"] in ("success", "failed"):
            return run
        time.sleep(3)
    raise TimeoutError(run_id)


def af_slot(day: str, hhmm: str, conf: dict | None = None, run_id: str | None = None,
            logical: bool = True) -> dict:
    started = time.monotonic()
    iso = slot_iso(day, hhmm)
    run_id = run_id or f"lab__{day}T{hhmm}"
    out = af_trigger(run_id, iso if logical else None, conf)
    run = af_wait(run_id)
    result = {"run_id": run_id, "state": run["state"], "tasks": af_tasks(run_id),
              "seconds": round(time.monotonic() - started, 1), "trigger_out": out.strip()[-160:]}
    note("airflow", **result)
    return result


def set_today(day: str) -> None:
    """Airflow 의 '오늘'을 저장 입력의 거래일로 고정한다(과거 슬롯 재생). airflow 컨테이너 재기동."""
    compose("up", "-d", "--wait", "airflow", env={"EDGE_LAB_TODAY_KST": day})
    airflow("dags", "reserialize")
    note("today", day=day)


def snapshot(name: str) -> dict:
    data = json.loads(compose("exec", "-T", "fake-aws", "/opt/dp/bin/python", "/lab/inspect_state.py"))
    data["fake_aws"] = fake("/_lab/state")
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))
    note("snapshot", name=name, db_rows=data["db"]["rows"], calls=data["external_calls"])
    return data


def utc_of(day: str, hhmm: str) -> str:
    from datetime import datetime, timezone
    return datetime.fromisoformat(slot_iso(day, hhmm)).astimezone(timezone.utc).isoformat()


def af_clear(day: str, hhmm: str, task_regex: str, only_failed: bool = False) -> None:
    """REST API clearTaskInstances 로 **run 하나를 지정해** clear 한다.

    ⚠️ 3.3.2 CLI `tasks clear -s/-e` 는 수동 trigger run 을 logical date 로도, run_after 창으로도
    고르지 못했다(exit 0·출력 없음·상태 불변, 로컬 3회 관찰). 운영 복구도 dag_run_id 지정 API 를 쓴다."""
    import re
    token = json.loads(urllib.request.urlopen("http://127.0.0.1:58100/auth/token").read())["access_token"]
    tasks = [t for t in ("plan", "collect", "normalize", "load", "verdict") if re.fullmatch(task_regex, t)]
    body = {"dry_run": False, "dag_run_id": f"lab__{day}T{hhmm}", "task_ids": tasks,
            "include_downstream": True, "only_failed": only_failed}
    req = urllib.request.Request(f"http://127.0.0.1:58100/api/v2/dags/{DAG}/clearTaskInstances",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    cleared = json.loads(urllib.request.urlopen(req).read()).get("task_instances", [])
    note("airflow_clear", slot=slot_iso(day, hhmm), cleared=sorted(t["task_id"] for t in cleared))


def kill_task_process(after_seconds: float) -> None:
    """실행 중인 task 프로세스(LocalExecutor 자식)를 SIGKILL — ECS 는 계속 돈다(응답 유실)."""
    time.sleep(after_seconds)
    out = compose("exec", "-T", "airflow", "sh", "-c",
                  "for p in /proc/[0-9]*; do c=$(tr '\\0' ' ' < $p/cmdline 2>/dev/null); "
                  "case \"$c\" in 'airflow worker -- LocalExecutor'*) ;; 'airflow worker -- '*) "
                  "kill -9 ${p#/proc/} && echo ${p#/proc/};; esac; done")
    note("killed_task_process", pids=out.split())


def manual_legacy_step(command: list[str], taskdef: str) -> int | None:
    """현행 수동 복구(README 레시피): 원래 run_id 로 ECS 스텝을 직접 실행한다."""
    code = ecs_wait(ecs_run(command, {}, taskdef))
    note("legacy_manual", command=command[0], exit=code)
    return code


def rid(day: str, hhmm: str) -> str:
    import hashlib
    key = f"{LANE}:{day}T{hhmm}"
    return "run_" + hashlib.sha256("\x01".join(["alphamale-etf-daily-v1", key]).encode()).hexdigest()[:26]


def reconcile_all(day_after: str) -> dict:
    """Airflow 경로 런을 Reconciler 로 대조한다(hard deadline 뒤 시각으로)."""
    codes = {}
    for day, slots in SLOTS.items():
        for hhmm in slots:
            key = f"{LANE}:{day}T{hhmm}"
            arn = ecs_run(["reconcile"], {"OPS_RUN_KEY": key, "OPS_SCHEDULED_TIME": day_after,
                                          "OPS_CLUSTER_ARN": "lab"}, "edge-dev-data-pipeline-ops")
            codes[key] = ecs_wait(arn)
    note("reconcile", exits=codes)
    return codes


def scenario_legacy() -> None:
    reset()
    d1 = "2026-09-22"
    legacy(d1, "09:35")                                                     # S1
    faults([{"step": "normalize-investor-estimate", "action": "exit", "code": 1,
             "run_id": rid(d1, "10:05")}])
    legacy(d1, "10:05")                                                     # S2 업무 실패
    r = rid(d1, "10:05")
    manual_legacy_step(["normalize-investor-estimate", "--run-id", r, "--input-run-id", r],
                       "edge-dev-data-pipeline-bigkinds")
    manual_legacy_step(["load-investor-intraday", "--run-id", r, "--input-run-id", r],
                       "edge-dev-data-pipeline-rds")
    faults([{"step": "ingest-raw-investor-estimate", "action": "fail_to_start"}])
    legacy(d1, "11:25")                                                     # S2 인프라 실패
    legacy(d1, "13:25")
    legacy(d1, "14:35")
    snapshot("legacy-day1")
    legacy(d1, "09:35")                                                     # S3 재요청
    r = rid(d1, "10:05")                                                    # S4 수동 재처리
    manual_legacy_step(["normalize-investor-estimate", "--run-id", r, "--input-run-id", r],
                       "edge-dev-data-pipeline-bigkinds")
    manual_legacy_step(["load-investor-intraday", "--run-id", r, "--input-run-id", r],
                       "edge-dev-data-pipeline-rds")
    snapshot("legacy-day1-after")
    d2 = "2026-09-23"
    faults([{"step": "ingest-raw-investor-estimate", "action": "exit", "code": 1,
             "run_id": rid(d2, "09:35")}])
    legacy(d2, "09:35")                                                     # S5 선행 입력 미준비
    for hhmm in ("10:05", "11:25", "13:25", "14:35"):
        legacy(d2, hhmm)
    snapshot("legacy-final")
    set_today(d2)                                                           # S8a
    af_slot(d2, "11:25", run_id="lab__s8a")
    snapshot("legacy-s8")


def scenario_airflow() -> None:
    import threading
    set_today("2026-09-22")
    reset()
    d1 = "2026-09-22"
    af_slot(d1, "09:35")                                                    # S1
    faults([{"step": "normalize-investor-estimate", "action": "exit", "code": 1,
             "run_id": rid(d1, "10:05")}])
    af_slot(d1, "10:05")                                                    # S2 업무 실패
    af_clear(d1, "10:05", "normalize")
    time.sleep(10)
    note("airflow_recovered", **{"run": af_wait(f"lab__{d1}T10:05")["state"],
                                 "tasks": af_tasks(f"lab__{d1}T10:05")})
    faults([{"step": "ingest-raw-investor-estimate", "action": "fail_to_start"}])
    af_slot(d1, "11:25")                                                    # S2 인프라 실패(자동 재시도)
    faults([{"step": "ingest-raw-investor-estimate", "action": "sleep_before", "seconds": 25,
             "run_id": rid(d1, "13:25")}])
    threading.Thread(target=kill_task_process, args=(15,), daemon=True).start()
    af_slot(d1, "13:25")                                                    # S6 응답 유실
    af_slot(d1, "14:35")
    snapshot("airflow-day1")
    af_clear(d1, "09:35", ".*")                                             # S3 성공 run 재요청(clear)
    time.sleep(10)
    note("airflow_rerequest", run=af_wait(f"lab__{d1}T09:35")["state"],
         tasks=af_tasks(f"lab__{d1}T09:35"))
    note("airflow_duplicate_trigger", out=af_trigger("lab__dup", slot_iso(d1, "09:35"))[-300:])
    af_slot("2026-09-21", "09:35", run_id="lab__s4_pastdate")               # S4 과거 날짜 새 run
    af_slot(d1, "10:05", conf={"reprocess_slot": slot_iso(d1, "10:05")},     # S4 재처리 run
            run_id="lab__s4_reprocess", logical=False)
    snapshot("airflow-day1-after")
    d2 = "2026-09-23"
    set_today(d2)
    faults([{"step": "ingest-raw-investor-estimate", "action": "exit", "code": 1,
             "run_id": rid(d2, "09:35")}])
    af_slot(d2, "09:35")                                                    # S5 선행 입력 미준비
    faults([{"step": "normalize-investor-estimate", "action": "sleep_before", "seconds": 70,
             "run_id": rid(d2, "10:05")}])
    run_id = f"lab__{d2}T10:05"                                             # S7 실행 관리 중단·재개
    af_trigger(run_id, slot_iso(d2, "10:05"))
    while af_tasks(run_id).get("normalize") != "running":
        time.sleep(2)
    time.sleep(10)
    compose("kill", "-s", "SIGKILL", "airflow")
    note("airflow_killed")
    time.sleep(20)
    compose("up", "-d", "--wait", "airflow", env={"EDGE_LAB_TODAY_KST": d2})
    note("airflow_restarted")
    note("airflow", run_id=run_id, state=af_wait(run_id)["state"], tasks=af_tasks(run_id))
    for hhmm in ("11:25", "13:25", "14:35"):
        af_slot(d2, hhmm)
    snapshot("airflow-final")
    legacy(d2, "13:25")                                                     # S8b
    reconcile_all("2026-09-24T00:00:00+09:00")
    snapshot("airflow-s8")


AF_ENV = {"OPS_EXCLUSIVE_STEP": "1", "OPS_SKIP_IF_SUCCEEDED": "1"}
NORMALIZE_TD = "edge-dev-data-pipeline-bigkinds"


def attempts_of(day: str, hhmm: str, task_key: str) -> list[list]:
    rows = psql("SELECT a.ecs_task_arn, a.exit_code, a.started_at, a.finished_at FROM ops_task_attempt a"
                " JOIN ops_expected_task et USING (expected_task_id)"
                f" WHERE et.pipeline_run_id='{rid(day, hhmm)}' AND et.task_key='{task_key}'"
                " ORDER BY a.started_at").strip().splitlines()
    return [r.split("|") for r in rows]


def normalize_cmd(day: str, hhmm: str) -> list[str]:
    r = rid(day, hhmm)
    return ["normalize-investor-estimate", "--run-id", r, "--input-run-id", r]


def scenario_guard() -> None:
    """중복 실행 방지 계약(실제 PostgreSQL·실제 업무 코드, ECS 대역) — Airflow 가 주는 env 로 직접 실행."""
    d1, slot = "2026-09-22", "09:35"
    reset()
    iso = slot_iso(d1, slot)
    plan = ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": iso, "OPS_PIPELINE_TYPE": LANE,
                                           "OPS_ORCHESTRATOR": "AIRFLOW",
                                           "OPS_ORCHESTRATOR_RUN_REF": "lab/guard"}, "edge-dev-data-pipeline-ops"))
    r = rid(d1, slot)
    collect = ecs_wait(ecs_run(["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", r],
                               AF_ENV, "edge-dev-data-pipeline-kis"))
    note("guard_setup", plan=plan, collect=collect)
    # G1 성공 → 응답 유실 → 재시도 때 원장 장애
    first = ecs_wait(ecs_run(normalize_cmd(d1, slot), AF_ENV, NORMALIZE_TD))
    faults([{"step": "normalize-investor-estimate", "action": "ledger_down"}])
    retried = ecs_wait(ecs_run(normalize_cmd(d1, slot), AF_ENV, NORMALIZE_TD))
    faults([{"step": "normalize-investor-estimate", "action": "ledger_down"}])
    legacy_env = ecs_wait(ecs_run(normalize_cmd(d1, slot), {}, NORMALIZE_TD))   # SFN 경로(env 없음)
    note("G1_ledger_down_on_retry", first=first, airflow_retry=retried, sfn_path=legacy_env,
         attempts=attempts_of(d1, slot, "NORMALIZE_INVESTOR_INTRADAY"))
    # G2 같은 스텝 두 실행 동시 시작(A 가 실행권을 쥔 채 20초)
    d2 = "10:05"
    ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": slot_iso(d1, d2), "OPS_PIPELINE_TYPE": LANE,
                                   "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": "lab/g2"},
                     "edge-dev-data-pipeline-ops"))
    ecs_wait(ecs_run(["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", rid(d1, d2)],
                     AF_ENV, "edge-dev-data-pipeline-kis"))
    faults([{"step": "normalize-investor-estimate", "action": "sleep_in_step", "seconds": 20}])
    a = ecs_run(normalize_cmd(d1, d2), AF_ENV, NORMALIZE_TD)
    time.sleep(3)
    b = ecs_run(normalize_cmd(d1, d2), AF_ENV, NORMALIZE_TD)
    note("G2_concurrent_same_step", a_exit=ecs_wait(a), b_exit=ecs_wait(b),
         attempts=attempts_of(d1, d2, "NORMALIZE_INVESTOR_INTRADAY"))
    # G4 오래된 실행이 늦게 실패로 끝남 — 기다리던 재시도가 그 뒤에 실행(겹침 없음)
    d3 = "11:25"
    ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": slot_iso(d1, d3), "OPS_PIPELINE_TYPE": LANE,
                                   "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": "lab/g4"},
                     "edge-dev-data-pipeline-ops"))
    ecs_wait(ecs_run(["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", rid(d1, d3)],
                     AF_ENV, "edge-dev-data-pipeline-kis"))
    faults([{"step": "normalize-investor-estimate", "action": "sleep_in_step", "seconds": 20, "code": 1}])
    a = ecs_run(normalize_cmd(d1, d3), AF_ENV, NORMALIZE_TD)
    time.sleep(3)
    b = ecs_run(normalize_cmd(d1, d3), AF_ENV, NORMALIZE_TD)
    note("G4_stale_late_failure", a_exit=ecs_wait(a), b_exit=ecs_wait(b),
         attempts=attempts_of(d1, d3, "NORMALIZE_INVESTOR_INTRADAY"))
    # G5 의도적 재처리(실행권만, 성공 skip 없음)
    rep = ecs_wait(ecs_run(normalize_cmd(d1, slot), {"OPS_EXCLUSIVE_STEP": "1"}, NORMALIZE_TD))
    note("G5_reprocess", exit=rep, attempts=attempts_of(d1, slot, "NORMALIZE_INVESTOR_INTRADAY"))
    # G7 수집 실패 → 빈 입력 정제 성공 → 수집 복구 → 정제는 skip 하지 않고 다시 돈다(입력이 바뀌었다)
    g7 = "13:25"
    ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": slot_iso(d1, g7), "OPS_PIPELINE_TYPE": LANE,
                                   "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": "lab/g7"},
                     "edge-dev-data-pipeline-ops"))
    collect7 = ["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", rid(d1, g7)]
    faults([{"step": "ingest-raw-investor-estimate", "action": "exit", "code": 1}])
    c1 = ecs_wait(ecs_run(collect7, AF_ENV, "edge-dev-data-pipeline-kis"))
    n1 = ecs_wait(ecs_run(normalize_cmd(d1, g7), AF_ENV, NORMALIZE_TD))
    n_retry = ecs_wait(ecs_run(normalize_cmd(d1, g7), AF_ENV, NORMALIZE_TD))     # 입력 불변 → skip
    c2 = ecs_wait(ecs_run(collect7, AF_ENV, "edge-dev-data-pipeline-kis"))       # 수집 복구
    n2 = ecs_wait(ecs_run(normalize_cmd(d1, g7), AF_ENV, NORMALIZE_TD))          # 입력 바뀜 → 실행
    manifest = compose("exec", "-T", "fake-aws", "sh", "-c",
                       f"cat /lab-data/lake/operations_archive/canonical_run_manifests/"
                       f"dataset=investor_flow_intraday/run_id={rid(d1, g7)}/manifest.json")
    note("G7_upstream_recovered", exits=[c1, n1, n_retry, c2, n2],
         winners=sum(len(x.get("winner_ids", [])) for x in json.loads(manifest)["canonical_partitions"]),
         collect_attempts=[a[1] for a in attempts_of(d1, g7, "INVESTOR_INTRADAY_COLLECTION_KIS")],
         normalize_attempts=attempts_of(d1, g7, "NORMALIZE_INVESTOR_INTRADAY"))
    # G6 같은 슬롯을 SFN·Airflow 가 동시에 계획 — 시작 순서 교대
    d6 = "2026-09-23"
    for i, hhmm in enumerate(("13:25", "14:35")):
        iso6 = slot_iso(d6, hhmm)
        sfn_env = {"OPS_SCHEDULED_TIME": iso6, "OPS_PIPELINE_TYPE": LANE,
                   "OPS_INVESTOR_INTRADAY_STATE_MACHINE_ARN": SM_ARN}
        af_env = {**sfn_env, "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": f"lab/g6-{i}"}
        order = [(sfn_env, "SFN"), (af_env, "AIRFLOW")][:: (1 if i % 2 == 0 else -1)]
        arns = [(who, ecs_run(["plan-run"], env, "edge-dev-data-pipeline-ops")) for env, who in order]
        exits = {who: ecs_wait(arn) for who, arn in arns}
        owner = psql(f"SELECT orchestrator FROM ops_pipeline_run WHERE run_key='{LANE}:{d6}T{hhmm}'").strip()
        idle_ecs()
        sfn_started = [e["name"] for e in fake("/_lab/state")["executions"] if hhmm.replace(":", "-") in e["name"]]
        note("G6_concurrent_plan", slot=hhmm, started_first=order[0][1], exits=exits, owner=owner,
             sfn_executions=sfn_started)
    # G8 재처리 입력 확인 — 없는 슬롯·수집 실패 슬롯은 거부, 기존 슬롯은 수렴
    rep_env = {"OPS_PIPELINE_TYPE": LANE, "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_REPROCESS": "1",
               "OPS_ORCHESTRATOR_RUN_REF": "lab/g8"}
    g8 = {name: ecs_wait(ecs_run(["plan-run"], {**rep_env, "OPS_SCHEDULED_TIME": slot_iso(d1, hhmm)},
                                 "edge-dev-data-pipeline-ops"))
          for name, hhmm in (("missing_slot", "09:36"), ("collected_slot", slot))}
    ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": slot_iso(d1, "14:35"), "OPS_PIPELINE_TYPE": LANE,
                                   "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": "lab/g8b"},
                     "edge-dev-data-pipeline-ops"))
    faults([{"step": "ingest-raw-investor-estimate", "action": "exit", "code": 1}])
    ecs_wait(ecs_run(["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", rid(d1, "14:35")],
                     AF_ENV, "edge-dev-data-pipeline-kis"))
    g8["failed_collect_slot"] = ecs_wait(ecs_run(["plan-run"], {**rep_env, "OPS_SCHEDULED_TIME": slot_iso(d1, "14:35")},
                                                 "edge-dev-data-pipeline-ops"))
    note("G8_reprocess_ready", plan_exits=g8,
         run_created_for_missing=psql(f"SELECT count(*) FROM ops_pipeline_run WHERE run_key='{LANE}:{d1}T09:36'").strip())
    # G9 Airflow 런 상태 투영(Reconciler, hard deadline 전 시각)
    for hhmm in (slot, "14:35"):
        ecs_wait(ecs_run(["reconcile"], {"OPS_RUN_KEY": f"{LANE}:{d1}T{hhmm}", "OPS_CLUSTER_ARN": "lab",
                                        "OPS_SCHEDULED_TIME": slot_iso(d1, "15:00")}, "edge-dev-data-pipeline-ops"))
    note("G9_orchestration_status", rows=psql(
        f"SELECT run_key, orchestration_status FROM ops_pipeline_run WHERE run_key IN"
        f" ('{LANE}:{d1}T{slot}', '{LANE}:{d1}T14:35')").split())
    note("G_issues", issues=psql("SELECT issue_type, dedupe_key FROM ops_reconciliation_issue"))
    snapshot("guard")


def scenario_holiday() -> None:
    """휴장일(09-22 를 휴장으로 지정) — Airflow run 의 계획·수집 skip·attempt."""
    d1 = "2026-09-22"
    set_today(d1)
    # airflow 가 fake-aws 에 depends_on 이라 airflow 재기동이 fake-aws 를 재생성한다 — 두 변수를 함께 준다.
    compose("up", "-d", "--wait", "fake-aws", env={"OPS_KR_HOLIDAYS": d1, "EDGE_LAB_TODAY_KST": d1})
    reset()
    af_slot(d1, "09:35")
    note("holiday_ledger", rows=psql(
        "SELECT et.task_key, et.plan_status, et.task_outcome, et.skip_reason,"
        " (SELECT count(*) FROM ops_task_attempt a WHERE a.expected_task_id=et.expected_task_id)"
        " FROM ops_expected_task et ORDER BY 1"))
    # 같은 logical date 로 다시 trigger — Airflow 가 새 run 을 만드는가, 만들면 업무가 다시 도는가
    out = af_trigger("lab__same_logical", slot_iso(d1, "09:35"))
    time.sleep(5)
    rerun = af_run("lab__same_logical")
    if rerun:
        rerun = af_wait("lab__same_logical")
    note("same_logical_date_retrigger", created=rerun is not None, state=rerun and rerun["state"],
         trigger_errors=[l.strip()[:220] for l in out.splitlines()
                         if any(w in l for w in ("rror", "xist", "nique", "already"))][:4])
    compose("up", "-d", "--wait", "fake-aws", env={"OPS_KR_HOLIDAYS": "", "EDGE_LAB_TODAY_KST": d1})
    snapshot("holiday")


def scenario_report() -> None:
    """DAG 가 끝나며 보고한 판정이 원장 orchestration_status 에 들어가는가(정상·부분 실패·재처리)."""
    d1 = "2026-09-22"
    set_today(d1)
    reset()
    status = lambda hhmm: psql(f"SELECT orchestration_status FROM ops_pipeline_run"
                               f" WHERE run_key='{LANE}:{d1}T{hhmm}'").strip()
    af_slot(d1, "09:35")
    faults([{"step": "ingest-raw-investor-estimate", "action": "bad_row", "run_id": rid(d1, "10:05")}])
    af_slot(d1, "10:05")
    note("report_after_runs", normal=status("09:35"), partial=status("10:05"))
    af_slot(d1, "10:05", conf={"reprocess_slot": slot_iso(d1, "10:05")}, run_id="lab__report_reprocess",
            logical=False)
    note("report_after_reprocess", partial_slot=status("10:05"))
    af_slot(d1, "11:25", conf={"reprocess_slot": slot_iso(d1, "11:25")}, run_id="lab__report_missing",
            logical=False)
    note("report_reprocess_missing_slot", run_rows=psql(
        f"SELECT count(*) FROM ops_pipeline_run WHERE run_key='{LANE}:{d1}T11:25'").strip())
    snapshot("report")


def scenario_trace() -> None:
    """Airflow 시도 ↔ 원장 attempt 추적: 정상 슬롯 + 수집 응답 유실(재시도가 중복 skip 으로 남는가)."""
    d1 = "2026-09-22"
    set_today(d1)
    reset()
    faults([{"step": "ingest-raw-investor-estimate", "action": "sleep_before", "seconds": 25,
             "run_id": rid(d1, "09:35")}])
    import threading
    threading.Thread(target=kill_task_process, args=(15,), daemon=True).start()
    af_slot(d1, "09:35")
    note("trace_attempts", rows=psql(
        "SELECT et.task_key, a.record_source, a.exit_code, a.orchestrator_attempt_ref"
        " FROM ops_task_attempt a JOIN ops_expected_task et USING (expected_task_id)"
        " ORDER BY a.created_at").splitlines())
    note("trace_run", rows=psql(
        "SELECT run_key, orchestrator_run_ref, orchestration_status,"
        " orchestration_reported_at IS NOT NULL FROM ops_pipeline_run").splitlines())
    snapshot("trace")


def scenario_lockloss() -> None:
    """잠금 연결 상실: A 가 실행권을 쥔 채 일하는 중 A 의 잠금 커넥션만 끊기면 B 가 들어오는가."""
    d1, slot = "2026-09-22", "10:05"
    reset()
    ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": slot_iso(d1, slot), "OPS_PIPELINE_TYPE": LANE,
                                   "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": "lab/lock"},
                     "edge-dev-data-pipeline-ops"))
    ecs_wait(ecs_run(["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", rid(d1, slot)],
                     AF_ENV, "edge-dev-data-pipeline-kis"))
    reprocess_env = {"OPS_EXCLUSIVE_STEP": "1"}     # 정제는 성공 skip 없음(DAG 와 같은 env)
    faults([{"step": "normalize-investor-estimate", "action": "sleep_in_step", "seconds": 30}])
    a = ecs_run(normalize_cmd(d1, slot), reprocess_env, NORMALIZE_TD)
    time.sleep(5)
    killed = psql("SELECT count(pg_terminate_backend(pid)) FROM pg_locks WHERE locktype='advisory'").strip()
    note("lockloss_terminated_lock_backends", count=killed)
    b = ecs_run(normalize_cmd(d1, slot), reprocess_env, NORMALIZE_TD)
    b_exit = ecs_wait(b)
    a_exit = ecs_wait(a)
    rows = attempts_of(d1, slot, "NORMALIZE_INVESTOR_INTRADAY")
    note("lockloss_result", a_exit=a_exit, b_exit=b_exit,
         attempts=[(r[0][-6:], r[1], r[2][11:19], r[3][11:19]) for r in rows],
         overlap=len(rows) == 2 and rows[1][2] < rows[0][3])
    snapshot("lockloss")


# ── 실행 상태 불명 시 보류(초기 운영 정책) ────────────────────────────────
COLLECT, NORMALIZE, LOAD = "ingest-raw-investor-estimate", "normalize-investor-estimate", "load-investor-intraday"


def state_rows(name: str) -> list[dict]:
    out = compose("exec", "-T", "fake-aws", "sh", "-c", f"cat /lab-data/state/{name} 2>/dev/null || true")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def counts(step: str, run_id: str | None = None) -> dict:
    """업무 실행(스텝 함수 호출)·canonical 파티션 쓰기·ECS 태스크·RunTask 요청 수 — 상태 코드가 아니라 이것으로 본다."""
    state = fake("/_lab/state")
    mine = lambda r: r.get("step", step) == step and run_id in (None, r.get("run_id"))
    return {"business": len([r for r in state_rows("business_runs.jsonl") if mine(r)]),
            "partition_writes": len([r for r in state_rows("partition_writes.jsonl")
                                     if step == NORMALIZE and run_id in (None, r.get("run_id"))]),
            "ecs_tasks": len([t for t in state["tasks"] if t["command"][:1] == [step]
                              and (run_id is None or run_id in t["command"])]),
            "run_requests": len([r for r in state["run_requests"] if r["step"] == step])}


def af_clear_run(run_id: str, tasks: list[str]) -> list[str]:
    token = json.loads(urllib.request.urlopen("http://127.0.0.1:58100/auth/token").read())["access_token"]
    body = {"dry_run": False, "dag_run_id": run_id, "task_ids": tasks, "include_downstream": True,
            "only_failed": False}
    req = urllib.request.Request(f"http://127.0.0.1:58100/api/v2/dags/{DAG}/clearTaskInstances",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    cleared = sorted(t["task_id"] for t in json.loads(urllib.request.urlopen(req).read()).get("task_instances", []))
    note("airflow_clear", run_id=run_id, cleared=cleared)
    return cleared


def ti_rest(run_id: str, task_id: str) -> dict:
    token = json.loads(urllib.request.urlopen("http://127.0.0.1:58100/auth/token").read())["access_token"]
    url = (f"http://127.0.0.1:58100/api/v2/dags/{DAG}/dagRuns/{urllib.parse.quote(run_id, safe='')}"
           f"/taskInstances/{task_id}")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})) as r:
        return json.loads(r.read())


def wait_task_done(run_id: str, task_id: str, after_try: int, timeout: float = 600) -> None:
    """clear 뒤 run 상태는 잠깐 옛 값으로 남는다 — 그 task 의 try 가 올라가 다시 끝날 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ti = ti_rest(run_id, task_id)
        if ti["try_number"] > after_try and ti["state"] in ("success", "failed", "upstream_failed", "skipped"):
            af_wait(run_id)
            return
        time.sleep(2)
    raise TimeoutError(f"{run_id}/{task_id}")


def af_try_state(run_id: str, task_id: str) -> dict:
    """task 의 현재 try 번호·상태·보류 XCom."""
    out = airflow("tasks", "states-for-dag-run", DAG, run_id, "-o", "json")
    row = next(r for r in json.loads(out) if r["task_id"] == task_id)
    hold = compose("exec", "-T", "airflow", "airflow", "tasks", "state", DAG, task_id, run_id, check=False)
    return {"state": row["state"], "try_number": row.get("try_number"), "hold": xcom(run_id, task_id, "hold"),
            "cli_state": hold.strip()[-20:]}


def xcom(run_id: str, task_id: str, key: str):
    token = json.loads(urllib.request.urlopen("http://127.0.0.1:58100/auth/token").read())["access_token"]
    url = (f"http://127.0.0.1:58100/api/v2/dags/{DAG}/dagRuns/{urllib.parse.quote(run_id, safe='')}"
           f"/taskInstances/{task_id}/xcomEntries/{key}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})) as r:
            return json.loads(r.read()).get("value")
    except urllib.error.HTTPError:
        return None


def started_by(run_id: str, task_id: str) -> str:
    return compose("exec", "-T", "airflow", "python", "-c",
                   "from airflow.providers.amazon.aws.utils.identifiers import generate_uuid;"
                   f"print(generate_uuid('{DAG}', '{task_id}', '{run_id}', '-1'))").strip()


def holds_in_ledger() -> list[str]:
    return psql("SELECT i.status, i.evidence->>'kind', et.task_key, i.dedupe_key FROM ops_reconciliation_issue i"
                " JOIN ops_expected_task et ON et.expected_task_id=i.scope_key"
                " WHERE i.issue_type='EXECUTION_HOLD' ORDER BY i.first_seen_at").splitlines()


def attempts_by_task(task_key: str) -> list[str]:
    return psql("SELECT et.pipeline_run_id || ' ' || a.record_source || ' ' || a.execution_status || ' '"
                " || coalesce(a.exit_code::text,'-') FROM ops_task_attempt a JOIN ops_expected_task et"
                f" USING (expected_task_id) WHERE et.task_key='{task_key}' ORDER BY a.created_at").splitlines()


def plan_direct(day: str, hhmm: str, ref: str) -> None:
    ecs_wait(ecs_run(["plan-run"], {"OPS_SCHEDULED_TIME": slot_iso(day, hhmm), "OPS_PIPELINE_TYPE": LANE,
                                   "OPS_ORCHESTRATOR": "AIRFLOW", "OPS_ORCHESTRATOR_RUN_REF": ref},
                     "edge-dev-data-pipeline-ops"))
    ecs_wait(ecs_run([COLLECT, "--max-failed-symbols", "1", "--run-id", rid(day, hhmm)], AF_ENV,
                     "edge-dev-data-pipeline-kis"))


def reconcile_key(day: str, hhmm: str, at: str) -> int | None:
    return ecs_wait(ecs_run(["reconcile"], {"OPS_RUN_KEY": f"{LANE}:{day}T{hhmm}", "OPS_CLUSTER_ARN": "lab",
                                            "OPS_SCHEDULED_TIME": at}, "edge-dev-data-pipeline-ops"))


CHECKS: list[dict] = []


def check(name: str, ok: bool, **detail) -> None:
    """기대를 기록한다 — 하나라도 어긋나면 시나리오가 실패로 끝난다(note 만으로는 아무것도 증명하지 않는다)."""
    CHECKS.append({"check": name, "ok": bool(ok), **detail})
    note("check", name=name, ok=bool(ok), **detail)


def intervals_disjoint(rows: list[dict]) -> bool:
    spans = sorted((r["start"], r["end"]) for r in rows)
    return all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def scenario_hold() -> None:
    """실행 상태 불명 시 보류 — 업무 실행 수·파티션 쓰기·ECS 태스크 수로 확인한다(상태 코드만 보지 않는다)."""
    d1 = "2026-09-22"
    set_today(d1)
    reset()
    ex = {"OPS_EXCLUSIVE_STEP": "1"}                 # 정제·적재 env(DAG 와 같다)

    # V1 잠금 연결 상실: A 가 일하는 중 A 의 lock 세션을 끊는다 → B(다른 슬롯·같은 슬롯)는 lock 을 얻어도 업무 0.
    plan_direct(d1, "09:35", "lab/v1a")
    plan_direct(d1, "10:05", "lab/v1b")
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 30, "run_id": rid(d1, "09:35")}])
    a = ecs_run(normalize_cmd(d1, "09:35"), ex, NORMALIZE_TD)
    time.sleep(6)
    killed = psql("SELECT count(pg_terminate_backend(pid)) FROM pg_locks WHERE locktype='advisory'").strip()
    b_other = ecs_wait(ecs_run(normalize_cmd(d1, "10:05"), ex, NORMALIZE_TD))
    b_same = ecs_wait(ecs_run(normalize_cmd(d1, "09:35"), ex, NORMALIZE_TD))
    during = {"business": counts(NORMALIZE)["business"], "partition_writes": counts(NORMALIZE)["partition_writes"]}
    a_exit = ecs_wait(a)
    after_a = ecs_wait(ecs_run(normalize_cmd(d1, "10:05"), ex, NORMALIZE_TD))
    note("V1_lock_loss", terminated_lock_sessions=killed, b_other_slot_exit=b_other, b_same_slot_exit=b_same,
         while_a_running=during, a_exit=a_exit, after_a_closed_exit=after_a,
         business=[(r["run_id"][-6:], r["exit"]) for r in state_rows("business_runs.jsonl") if r["step"] == NORMALIZE],
         disjoint=intervals_disjoint([r for r in state_rows("business_runs.jsonl") if r["step"] == NORMALIZE]),
         holds=holds_in_ledger())
    check("V1 lock 을 얻은 B 는 업무 0(다른 슬롯·같은 슬롯)", b_other == 76 and b_same == 76
          and during["business"] == 0 and during["partition_writes"] == 0)
    check("V1 A 종료 뒤 다음 실행은 정상", a_exit == 0 and after_a == 0 and intervals_disjoint(
        [r for r in state_rows("business_runs.jsonl") if r["step"] == NORMALIZE]))

    # V1b A 가 강제 종료(원장 시도 RUNNING 잔존) → 보류 → Reconciler 가 ECS STOPPED 확인 → 해제 → 1회 실행.
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 60, "run_id": rid(d1, "10:05")}])
    a2 = ecs_run(normalize_cmd(d1, "10:05"), ex, NORMALIZE_TD)
    time.sleep(6)
    fake("/", {"cluster": "lab", "task": a2, "reason": "lab kill"}, target="AmazonEC2ContainerServiceV20141113.StopTask")
    a2_exit = ecs_wait(a2)
    before = counts(NORMALIZE)["business"]
    held = ecs_wait(ecs_run(normalize_cmd(d1, "09:35"), ex, NORMALIZE_TD))
    held_business = counts(NORMALIZE)["business"] - before
    rec = reconcile_key(d1, "10:05", slot_iso(d1, "10:30"))
    released = ecs_wait(ecs_run(normalize_cmd(d1, "09:35"), ex, NORMALIZE_TD))
    note("V1b_killed_holder", killed_exit=a2_exit, next_exit=held, business_while_held=held_business,
         reconcile_exit=rec, after_ecs_evidence_exit=released,
         attempts=attempts_by_task("NORMALIZE_INVESTOR_INTRADAY"), holds=holds_in_ledger())
    check("V1b 강제 종료된 시도가 열린 동안 보류, ECS 종료 확인 뒤 1회", held == 76 and held_business == 0
          and rec == 0 and released == 0)
    snapshot("hold-v1")

    # V2 제출 응답 유실(태스크는 생성) — boto 재전송은 같은 토큰, EdgeStep 은 재제출 없이 추적해 붙는다.
    reset()
    faults([{"api": "run_task", "step": NORMALIZE, "action": "lose_response", "sticky": True, "times": 1}])
    r = af_slot(d1, "09:35")
    n = counts(NORMALIZE)
    note("V2_lost_response_traced", run=r["state"], tasks=r["tasks"], normalize=n, load=counts(LOAD))
    check("V2 응답 유실: 재전송은 같은 토큰, 태스크·업무 1", r["state"] == "success" and n["ecs_tasks"] == 1
          and n["business"] == 1 and n["run_requests"] > 1, normalize=n)

    # V2b 응답 유실 + 조회에 안 보임 → 보류. 태스크는 실제로 돌았다(업무 1회) — 새 태스크는 없다.
    faults([{"api": "run_task", "step": NORMALIZE, "action": "lose_response_invisible", "sticky": True, "times": 1}])
    run_b = f"lab__{d1}T10:05"
    r = af_slot(d1, "10:05")
    idle_ecs()
    note("V2b_lost_untraceable", run=r["state"], tasks=r["tasks"], normalize=counts(NORMALIZE, rid(d1, "10:05")),
         hold_xcom=xcom(run_b, "normalize", "hold"), holds=holds_in_ledger(),
         outcome=psql(f"SELECT task_key, task_outcome, outcome_reason FROM ops_expected_task"
                      f" WHERE pipeline_run_id='{rid(d1, '10:05')}' ORDER BY 1").splitlines())
    n2b = counts(NORMALIZE, rid(d1, "10:05"))
    check("V2b 추적 불가 → 보류, 새 태스크 없음, 원장 ECS 보류", (xcom(run_b, "normalize", "hold") or {}).get("kind")
          == "ECS_STATE_UNKNOWN" and n2b["ecs_tasks"] == 1
          and any(h.startswith("OPEN|ECS_STATE_UNKNOWN|NORMALIZE") for h in holds_in_ledger()), normalize=n2b)

    # V7 보류는 task clear·수동(재처리) trigger 로 우회되지 않는다. 운영자 해제(RESOLVED) 뒤에만 1회 실행.
    before = counts(NORMALIZE, rid(d1, "10:05"))
    before_try = ti_rest(run_b, "report")["try_number"]
    af_clear_run(run_b, ["normalize"])
    wait_task_done(run_b, "report", before_try)
    after = counts(NORMALIZE, rid(d1, "10:05"))
    note("V7a_clear_does_not_bypass", tasks=af_tasks(run_b), normalize_before=before,
         normalize_after=after, hold_xcom=xcom(run_b, "normalize", "hold"))
    check("V7a clear 는 보류를 우회하지 못함", after == before
          and (xcom(run_b, "normalize", "hold") or {}).get("kind") == "ECS_STATE_UNKNOWN")
    rep = af_slot(d1, "10:05", conf={"reprocess_slot": slot_iso(d1, "10:05")}, run_id="lab__v7_reprocess",
                  logical=False)
    after_rep = counts(NORMALIZE, rid(d1, "10:05"))
    note("V7b_reprocess_does_not_bypass", run=rep["state"], tasks=rep["tasks"],
         normalize_after=after_rep, holds=holds_in_ledger())
    check("V7b 재처리 run 도 업무 0", after_rep["business"] == before["business"]
          and rep["tasks"].get("normalize") == "failed")
    psql("UPDATE ops_reconciliation_issue SET status='RESOLVED', resolution_reason='operator_confirmed_stopped:lab',"
         " resolution_source='operator', updated_at=now() WHERE issue_type='EXECUTION_HOLD' AND status='OPEN'"
         " AND evidence->>'kind'='ECS_STATE_UNKNOWN'")
    rep2 = af_slot(d1, "10:05", conf={"reprocess_slot": slot_iso(d1, "10:05")}, run_id="lab__v7_released",
                   logical=False)
    released_counts = counts(NORMALIZE, rid(d1, "10:05"))
    note("V7c_after_operator_release", run=rep2["state"], tasks=rep2["tasks"],
         normalize_after=released_counts, load=counts(LOAD, rid(d1, "10:05")))
    check("V7c 운영자 해제 뒤 1회 실행", rep2["state"] == "success"
          and released_counts["business"] == before["business"] + 1)
    snapshot("hold-v2-v7")

    # V3 ECS 상태 조회 실패 — 첫 시도: 제출 없이 실패. clear 로 둘째 시도: 조회 실패면 보류.
    reset()
    run_c = f"lab__{d1}T11:25"
    sb = started_by(run_c, "collect")
    faults([{"api": "list_tasks", "started_by": sb, "times": 3}])
    r = af_slot(d1, "11:25")
    first = {"tasks": r["tasks"], "collect": counts(COLLECT)}
    faults([{"api": "list_tasks", "started_by": sb, "times": 3}])
    before_try = ti_rest(run_c, "report")["try_number"]
    af_clear_run(run_c, ["collect"])
    wait_task_done(run_c, "report", before_try)
    second = {"tasks": af_tasks(run_c), "collect": counts(COLLECT), "hold": xcom(run_c, "collect", "hold")}
    note("V3_status_read_failure", first_try=first, second_try=second, holds=holds_in_ledger())
    check("V3 첫 시도 조회 실패: 제출 0·보류 아님", first["collect"]["run_requests"] == 0
          and first["tasks"]["collect"] == "failed")
    check("V3 둘째 시도 조회 실패: 제출 0·보류·원장 기록·정제 미시작", second["collect"]["run_requests"] == 0
          and (second["hold"] or {}).get("kind") == "ECS_STATE_UNKNOWN"
          and second["tasks"]["normalize"] == "skipped"
          and any(h.startswith("OPEN|ECS_STATE_UNKNOWN|INVESTOR_INTRADAY_COLLECTION_KIS") for h in holds_in_ledger()))

    # V3b 풀리지 않은 ECS 보류는 레인 전체의 같은 스텝을 막는다(대가) — 다음 슬롯 수집도 업무 0.
    blocked = af_slot(d1, "13:25", run_id="lab__v3b_blocked")
    c3b = counts(COLLECT, rid(d1, "13:25"))
    check("V3b 해제 전 다음 슬롯 수집도 보류(업무 0·외부 호출 0)", blocked["tasks"].get("collect") == "failed"
          and c3b["business"] == 0, collect=c3b)
    # 운영자가 종료를 확인하고 해제한다(삭제가 아니라 RESOLVED 전이). 막혔던 13:25 는 수집 공백으로 둔다.
    psql("UPDATE ops_reconciliation_issue SET status='RESOLVED', resolution_reason='operator_confirmed_stopped:lab',"
         " resolution_source='operator', updated_at=now() WHERE issue_type='EXECUTION_HOLD' AND status='OPEN'")
    # V5a 수집 응답 유실(Airflow 프로세스 강제 종료, ECS 는 계속) → 둘째 시도가 재접속 — 새 태스크·외부 호출 없음.
    import threading
    faults([{"step": COLLECT, "action": "sleep_before", "seconds": 75, "run_id": rid(d1, "14:35")}])
    threading.Thread(target=kill_task_process, args=(15,), daemon=True).start()
    r = af_slot(d1, "14:35")
    note("V5a_reattach", run=r["state"], tasks=r["tasks"], collect=counts(COLLECT, rid(d1, "14:35")),
         kis_calls=sum(c["calls"] for c in state_rows("external_calls.jsonl") if c["run_id"] == rid(d1, "14:35")),
         reattached=xcom(f"lab__{d1}T14:35", "collect", "ecs_reattached_arn"))
    c5 = counts(COLLECT, rid(d1, "14:35"))
    check("V5a 도는 수집에 재접속 — 새 태스크·외부 호출 0", r["state"] == "success" and c5["ecs_tasks"] == 1
          and c5["business"] == 1 and xcom(f"lab__{d1}T14:35", "collect", "ecs_reattached_arn") is not None)
    # 수집 보류 뒤 같은 run 의 정제는 시작하지 않는다(부분 실패와 다르다) — V3 둘째 시도에서 확인한다.
    # V5b 수집 성공 뒤 종료 확인 조회만 실패 → 재시도는 끝난 태스크의 exit 0 을 쓴다 — 새 태스크·외부 호출 없음.
    faults([{"api": "describe_tasks", "step": COLLECT, "times": 2}])
    r = af_slot(d1, "10:05")
    note("V5b_success_reused", run=r["state"], tasks=r["tasks"], collect=counts(COLLECT, rid(d1, "10:05")),
         kis_calls=sum(c["calls"] for c in state_rows("external_calls.jsonl") if c["run_id"] == rid(d1, "10:05")),
         reused=xcom(f"lab__{d1}T10:05", "collect", "ecs_reused_arn"))
    c5b = counts(COLLECT, rid(d1, "10:05"))
    check("V5b 끝난 성공을 재사용 — 새 태스크·외부 호출 0", r["state"] == "success" and c5b["ecs_tasks"] == 1
          and c5b["business"] == 1 and xcom(f"lab__{d1}T10:05", "collect", "ecs_reused_arn") is not None)
    snapshot("hold-v3-v5")

    # V6 기동 실패 확정 → 안전한 재시도. (a) 컨테이너 기동 실패(TaskFailedToStart) (b) 배치 거부(failures) 1회.
    reset()
    faults([{"step": COLLECT, "action": "fail_to_start"},
            {"api": "run_task", "step": NORMALIZE, "action": "refuse", "times": 1}])
    r = af_slot(d1, "09:35")
    note("V6_confirmed_start_failure", run=r["state"], tasks=r["tasks"], collect=counts(COLLECT),
         normalize=counts(NORMALIZE))
    c6, n6 = counts(COLLECT), counts(NORMALIZE)
    check("V6 기동 실패 확정 뒤에만 새 태스크, 업무 1회", r["state"] == "success" and c6["ecs_tasks"] == 2
          and c6["business"] == 1 and n6["run_requests"] == 2 and n6["ecs_tasks"] == 1 and n6["business"] == 1)

    # V8 다른 슬롯·재처리가 같은 파티션에서 겹치지 않는다. (a) 컨테이너 직접 동시 시작 (b) Airflow 두 run 동시 요청.
    plan_direct(d1, "10:05", "lab/v8")
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 15},
            {"step": NORMALIZE, "action": "sleep_in_step", "seconds": 15}])
    x = ecs_run(normalize_cmd(d1, "09:35"), ex, NORMALIZE_TD)
    time.sleep(1)
    y = ecs_run(normalize_cmd(d1, "10:05"), ex, NORMALIZE_TD)
    exits = [ecs_wait(x), ecs_wait(y)]
    disjoint = intervals_disjoint([r for r in state_rows("business_runs.jsonl") if r["step"] == NORMALIZE])
    note("V8a_direct_concurrent", exits=exits, disjoint=disjoint)
    check("V8a 다른 슬롯 동시 시작 — 직렬", exits == [0, 0] and disjoint)
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 20, "run_id": rid(d1, "11:25")}])
    af_trigger(f"lab__{d1}T11:25", slot_iso(d1, "11:25"))
    af_trigger("lab__v8_reprocess", None, {"reprocess_slot": slot_iso(d1, "09:35")})
    runs = {rid_: af_wait(rid_)["state"] for rid_ in (f"lab__{d1}T11:25", "lab__v8_reprocess")}
    rows = [r for r in state_rows("business_runs.jsonl") if r["step"] == NORMALIZE]
    note("V8b_airflow_two_runs", runs=runs, normalize_runs=len(rows), disjoint=intervals_disjoint(rows),
         partition_writes=len(state_rows("partition_writes.jsonl")), holds=holds_in_ledger())
    check("V8b 정기 run·재처리 run 동시 요청 — 직렬", set(runs.values()) == {"success"} and intervals_disjoint(rows))
    snapshot("hold-v6-v8")
    failed = [c["check"] for c in CHECKS if not c["ok"]]
    note("hold_summary", passed=len(CHECKS) - len(failed), failed=failed)
    if failed:
        raise SystemExit(f"기대와 다름: {failed}")


# ── 결말 없는 실행: 수동 failed·worker 사망·DAG 시간 초과·늦은 보고·같은 증거 같은 결론 ──────────────
def af_rest(method: str, path: str, body: dict | None = None) -> dict:
    token = json.loads(urllib.request.urlopen("http://127.0.0.1:58100/auth/token").read())["access_token"]
    req = urllib.request.Request(f"http://127.0.0.1:58100/api/v2/dags/{DAG}{path}",
                                 data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"{}")


def mark_failed(run_id: str, task_id: str) -> None:
    """운영자가 도는 task 를 failed 로 표시(REST). on_kill 이 불린다 — EdgeStep 은 ECS 를 멈추지 않는다."""
    af_rest("PATCH", f"/dagRuns/{urllib.parse.quote(run_id, safe='')}/taskInstances/{task_id}",
         {"new_state": "failed"})
    note("marked_failed", run_id=run_id, task_id=task_id)


def wait_task_state(run_id: str, task_id: str, states: tuple, timeout: float = 600) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = af_tasks(run_id).get(task_id)
        if state in states:
            return state
        time.sleep(2)
    raise TimeoutError(f"{run_id}/{task_id} -> {states}")


def periodic_reconcile(lifetime: int) -> int | None:
    """EventBridge 주기 Reconciler(OPS_RUN_KEY 없음) — 슬롯 대조 + Airflow sweep."""
    code = ecs_wait(ecs_run(["reconcile"], {"OPS_CLUSTER_ARN": "lab",
                                            "OPS_AIRFLOW_RUN_LIFETIME_SECONDS": str(lifetime)},
                            "edge-dev-data-pipeline-ops"))
    note("periodic_reconcile", exit=code, lifetime=lifetime)
    return code


def open_holds(run_id: str | None = None) -> list[dict]:
    rows = psql("SELECT i.dedupe_key, i.evidence->>'kind', et.task_key, et.pipeline_run_id FROM ops_reconciliation_issue i"
                " JOIN ops_expected_task et ON et.expected_task_id=i.scope_key"
                " WHERE i.issue_type='EXECUTION_HOLD' AND i.status='OPEN' ORDER BY i.first_seen_at").splitlines()
    out = [dict(zip(("dedupe_key", "kind", "task_key", "run_id"), r.split("|"))) for r in rows]
    return [h for h in out if run_id in (None, h["run_id"])]


def resolve_holds(note_text: str) -> None:
    psql("UPDATE ops_reconciliation_issue SET status='RESOLVED', resolution_source='operator',"
         f" resolution_reason='operator_confirmed_stopped:{note_text}', updated_at=now()"
         " WHERE issue_type='EXECUTION_HOLD' AND status='OPEN'")


def run_status(day: str, hhmm: str) -> list[str]:
    return psql("SELECT coalesce(orchestration_status,'NULL'), coalesce(orchestration_reported_at::text,'-')"
                f" FROM ops_pipeline_run WHERE run_key='{LANE}:{day}T{hhmm}'").strip().split("|")


def scenario_unsettled() -> None:
    import threading
    d1 = "2026-09-22"
    set_today(d1)
    reset()

    # U1 운영자가 도는 정제를 failed 로 표시 — ECS 는 계속 돈다. report 가 결말 없음을 보류로 원장에 남긴다.
    r1 = f"lab__{d1}T09:35"
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 45, "run_id": rid(d1, "09:35")}])
    af_trigger(r1, slot_iso(d1, "09:35"))
    wait_task_state(r1, "normalize", ("running",))
    time.sleep(12)
    mark_failed(r1, "normalize")
    af_wait(r1)
    t1 = af_tasks(r1)
    held1 = open_holds(rid(d1, "09:35"))
    check("U1 수동 failed → 보류 원장 기록, 하류 skip, verdict 보류", t1.get("normalize") == "failed"
          and t1.get("load") in ("skipped", "upstream_failed") and t1.get("report") == "success"
          and any(h["kind"] == "ECS_STATE_UNKNOWN" and h["task_key"] == "NORMALIZE_INVESTOR_INTRADAY" for h in held1),
          tasks=t1, holds=held1)
    # 보류 중: 다른 슬롯·재처리의 정제는 업무 0(원장 게이트). 표시된 태스크는 끝까지 돈다(업무 1).
    before = counts(NORMALIZE)
    af_slot(d1, "10:05")
    rep = af_slot(d1, "09:35", conf={"reprocess_slot": slot_iso(d1, "09:35")}, run_id="lab__u1_reprocess",
                  logical=False)
    idle_ecs()
    after = counts(NORMALIZE)
    check("U1 보류 중 다른 슬롯·재처리 정제 업무 0(표시된 태스크만 1회 완료)",
          after["business"] - before["business"] == 1 and after["partition_writes"] - before["partition_writes"] == 1
          and rep["tasks"].get("normalize") == "failed", before=before, after=after, reprocess=rep["tasks"])
    # clear 는 표시된 태스크가 이미 끝났으므로(자동 재시도가 아닌 clear) 새로 띄운다 — 원장 게이트가 76 으로 막는다.
    before = counts(NORMALIZE)
    try_before = ti_rest(r1, "report")["try_number"]
    af_clear_run(r1, ["normalize"])
    wait_task_done(r1, "report", try_before)
    after = counts(NORMALIZE)
    check("U1 보류 중 clear 도 업무 0", after["business"] == before["business"], before=before, after=after,
          tasks=af_tasks(r1))
    # 복구: 운영자가 ECS 종료를 확인하고 해제 → 재처리 1회.
    resolve_holds("u1")
    rec = af_slot(d1, "09:35", conf={"reprocess_slot": slot_iso(d1, "09:35")}, run_id="lab__u1_recovered",
                  logical=False)
    check("U1 해제 뒤 재처리 정상 1회", rec["state"] == "success"
          and counts(NORMALIZE)["business"] == after["business"] + 1, tasks=rec["tasks"])
    snapshot("unsettled-u1")

    # U2 마지막 시도까지 worker 가 죽는다 — 매 시도는 도는 ECS 에 재접속(새 태스크 0), 마지막엔 결말 없이 끝난다.
    r2 = f"lab__{d1}T11:25"
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 240, "run_id": rid(d1, "11:25")}])
    af_trigger(r2, slot_iso(d1, "11:25"))
    kills = []
    for _ in range(3):
        wait_task_state(r2, "normalize", ("running",))
        time.sleep(8)
        kill_task_process(0)
        kills.append(ti_rest(r2, "normalize")["try_number"])
        wait_task_state(r2, "normalize", ("up_for_retry", "failed"))
    af_wait(r2)
    idle_ecs()
    t2 = af_tasks(r2)
    n2 = counts(NORMALIZE, rid(d1, "11:25"))
    held2 = open_holds(rid(d1, "11:25"))
    check("U2 worker 사망(마지막 시도) → 보류 원장 기록, 새 태스크 0, 업무 1", t2.get("normalize") == "failed"
          and n2["ecs_tasks"] == 1 and n2["business"] == 1
          and any(h["kind"] == "ECS_STATE_UNKNOWN" for h in held2), tasks=t2, normalize=n2, kills=kills, holds=held2)
    resolve_holds("u2")
    snapshot("unsettled-u2")

    # U3 DAG 시간 초과 — report·verdict 가 안 돈다. 수집 컨테이너는 wrapper 앞에서 대기 중(원장 흔적 없음).
    compose("up", "-d", "--wait", "airflow", env={"EDGE_LAB_TODAY_KST": d1, "EDGE_LAB_DAGRUN_TIMEOUT_SECONDS": "90"})
    airflow("dags", "reserialize")
    r3 = f"lab__{d1}T13:25"
    faults([{"step": COLLECT, "action": "sleep_before", "seconds": 160, "run_id": rid(d1, "13:25")}])
    af_trigger(r3, slot_iso(d1, "13:25"))
    run3 = af_wait(r3)
    t3 = af_tasks(r3)
    ledger_holds_before = open_holds(rid(d1, "13:25"))
    time.sleep(10)
    code = periodic_reconcile(lifetime=60)
    held3 = open_holds(rid(d1, "13:25"))
    check("U3 DAG 시간 초과 → report 없이 주기 점검이 원장 밖 태스크를 보류로 기록", run3["state"] == "failed"
          and t3.get("report") not in ("success",) and not ledger_holds_before and code == 0
          and any(h["kind"] == "ECS_STATE_UNKNOWN" and h["task_key"] == "INVESTOR_INTRADAY_COLLECTION_KIS"
                  for h in held3), tasks=t3, holds=held3)
    # 그 태스크가 늦게 깨어나도 업무 0(자기 작업의 보류를 본다). 다른 슬롯 수집도 업무 0.
    af_slot(d1, "14:35")
    idle_ecs()
    c3 = counts(COLLECT)
    kis = sum(c["calls"] for c in state_rows("external_calls.jsonl") if c["run_id"] in (rid(d1, "13:25"), rid(d1, "14:35")))
    check("U3 늦게 뜬 원장 밖 태스크·다른 슬롯 수집 모두 업무 0·외부 호출 0", kis == 0
          and counts(COLLECT, rid(d1, "13:25"))["business"] == 0 and counts(COLLECT, rid(d1, "14:35"))["business"] == 0,
          collect=c3, kis=kis)
    compose("up", "-d", "--wait", "airflow", env={"EDGE_LAB_TODAY_KST": d1})
    airflow("dags", "reserialize")
    # 복구: 종료 확인(ECS STOPPED) → 해제 → 다음 슬롯 수집 정상.
    resolve_holds("u3")
    d2 = "2026-09-23"
    set_today(d2)
    rec3 = af_slot(d2, "09:35")
    check("U3 해제 뒤 다음 수집 정상", rec3["state"] == "success" and counts(COLLECT, rid(d2, "09:35"))["business"] == 1,
          tasks=rec3["tasks"])
    snapshot("unsettled-u3")

    # U4 늦게 도착한 옛 run 의 보고가 새 run 의 판정을 덮지 않는다. 새 재처리 run 은 정제 업무 실패(FAILED)로
    # 보고하고, 그 뒤 옛 run 의 보고(SUCCEEDED)가 다시 도착한다(clear — max_active_runs=1 이라 재처리 뒤에 돈다).
    r4 = f"lab__{d2}T10:05"
    af_slot(d2, "10:05")
    first = run_status(d2, "10:05")
    faults([{"step": NORMALIZE, "action": "exit", "code": 1, "run_id": rid(d2, "10:05")}])
    af_slot(d2, "10:05", conf={"reprocess_slot": slot_iso(d2, "10:05")}, run_id="lab__u4_reprocess", logical=False)
    newer = run_status(d2, "10:05")
    try_before = ti_rest(r4, "report")["try_number"]
    af_clear_run(r4, ["report"])
    wait_task_done(r4, "report", try_before)
    final = run_status(d2, "10:05")
    check("U4 옛 run 의 늦은 보고가 새 run 판정을 덮지 않음", first[0] == "SUCCEEDED" and newer[0] == "FAILED"
          and final == newer and af_tasks(r4).get("report") == "success", first=first, newer=newer, final=final)

    # U5 같은 증거 같은 결론: 도는 정제를 StopTask(외부 종료) → Airflow 보류(RESULT_UNKNOWN), 원장 stopped_result_unknown.
    faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 60, "run_id": rid(d2, "11:25")}])
    r5 = f"lab__{d2}T11:25"
    af_trigger(r5, slot_iso(d2, "11:25"))
    wait_task_state(r5, "normalize", ("running",))
    time.sleep(10)
    arn = next(t["taskArn"] for t in fake("/_lab/state")["tasks"]
               if t["command"][:1] == [NORMALIZE] and rid(d2, "11:25") in t["command"] and t["lastStatus"] == "RUNNING")
    fake("/", {"cluster": "lab", "task": arn, "reason": "lab stop"}, target="AmazonEC2ContainerServiceV20141113.StopTask")
    af_wait(r5)
    hold5 = xcom(r5, "normalize", "hold") or {}
    ledger5 = psql(f"SELECT et.task_outcome, coalesce(et.outcome_reason,'-'), a.execution_status, coalesce(a.exit_code::text,'-')"
                   f" FROM ops_expected_task et JOIN ops_task_attempt a USING (expected_task_id)"
                   f" WHERE et.pipeline_run_id='{rid(d2, '11:25')}' AND et.task_key='NORMALIZE_INVESTOR_INTRADAY'").split("|")
    check("U5 외부 종료: Airflow RESULT_UNKNOWN = 원장 stopped_result_unknown", hold5.get("kind") == "RESULT_UNKNOWN"
          and ledger5[:2] == ["FAILED", "stopped_result_unknown"], airflow=hold5, ledger=ledger5,
          holds=open_holds(rid(d2, "11:25")))
    snapshot("unsettled-u4-u5")
    failed = [c["check"] for c in CHECKS if not c["ok"]]
    note("unsettled_summary", passed=len(CHECKS) - len(failed), failed=failed)
    if failed:
        raise SystemExit(f"기대와 다름: {failed}")


def scenario_partial() -> None:
    """정제 부분 실패 — 벤더 응답에 깨진 행 1개(거래일 결측)가 섞인 슬롯. 실제 정제 게이트가 그 행만
    탈락시키고 나머지 winner 를 commit 한 뒤 exit 2 를 낸다. 두 경로의 exit 2 해석 대조."""
    d1 = "2026-09-22"
    set_today(d1)
    for path in ("legacy", "airflow"):
        reset()
        run = legacy if path == "legacy" else af_slot
        run(d1, "09:35")
        faults([{"step": "ingest-raw-investor-estimate", "action": "bad_row",
                 "run_id": rid(d1, "10:05")}])
        run(d1, "10:05")
        # 분석 엔진 v_flow_intraday 는 available_at <= as_of 로 자른다 — 10:05 부분 실패 슬롯의 행이
        # 11:25 전 분석에 보이는지가 두 경로의 실질 차이다(수량이 같아도).
        visible = {}
        for as_of in ("10:30", "11:30"):
            visible[as_of] = psql(
                "SELECT asof_slot, count(*) FROM investor_flow_intraday"
                f" WHERE available_at <= '{d1}T{as_of}:00+09:00' GROUP BY 1 ORDER BY 1").split()
        note("partial_point_in_time", path=path, visible_by_as_of=visible)
        run(d1, "11:25")
        visible_after = psql("SELECT asof_slot, count(*), min(available_at), max(available_at)"
                             " FROM investor_flow_intraday GROUP BY 1 ORDER BY 1").split()
        note("partial_after_next_slot", path=path, rows=visible_after)
        snapshot(f"{path}-partial")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "reset":
        reset()
    elif cmd == "legacy":
        legacy(*rest)
    elif cmd == "airflow":
        af_slot(*rest)
    elif cmd == "today":
        set_today(*rest)
    elif cmd == "snapshot":
        snapshot(*rest)
    elif cmd in ("scenario-legacy", "scenario-airflow", "scenario-partial", "scenario-guard",
                 "scenario-holiday", "scenario-report", "scenario-trace", "scenario-lockloss",
                 "scenario-hold", "scenario-unsettled"):
        try:
            {"scenario-legacy": scenario_legacy, "scenario-airflow": scenario_airflow,
             "scenario-partial": scenario_partial, "scenario-guard": scenario_guard,
             "scenario-holiday": scenario_holiday, "scenario-report": scenario_report,
             "scenario-trace": scenario_trace, "scenario-lockloss": scenario_lockloss,
             "scenario-hold": scenario_hold, "scenario-unsettled": scenario_unsettled}[cmd]()
        finally:
            RESULTS.mkdir(parents=True, exist_ok=True)
            (RESULTS / f"{cmd}.events.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in LOG))
    else:
        raise SystemExit(__doc__)
