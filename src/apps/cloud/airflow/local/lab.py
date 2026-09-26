"""장중 수급 레인의 기존 경로 vs Airflow 경로 로컬 비교 구동기(호스트 python3, 표준 라이브러리만).

기존 경로 = EventBridge 가 하던 ECS `plan-run` → Planner → 배포 ASL(대역 SFN 해석) → ECS 스텝.
Airflow 경로 = DAG `edge_investor_intraday` → plan-run(OPS_ORCHESTRATOR=AIRFLOW) → ECS 스텝.
두 경로 모두 같은 ECS 대역·같은 업무 코드·같은 저장 입력을 쓴다.

    python3 lab.py up | reset | legacy <slot> | airflow <slot> | snapshot <name> | scenarios
"""

from __future__ import annotations

import json
import subprocess
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
                 "scenario-holiday", "scenario-report"):
        try:
            {"scenario-legacy": scenario_legacy, "scenario-airflow": scenario_airflow,
             "scenario-partial": scenario_partial, "scenario-guard": scenario_guard,
             "scenario-holiday": scenario_holiday, "scenario-report": scenario_report}[cmd]()
        finally:
            RESULTS.mkdir(parents=True, exist_ok=True)
            (RESULTS / f"{cmd}.events.jsonl").write_text(
                "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in LOG))
    else:
        raise SystemExit(__doc__)
