"""micro·small 사양 검증 드라이버(ALPHA-1119, 호스트 python3·표준 라이브러리만).

    python3 micro/run.py <실험 이름> --mem 1024m --parallelism 4 --parsing 2 --pool 3 --overflow 5

한 실험 = criteria.json 의 한 스위트(기동 → 유휴 → B1 정상 5회(+버스트) → B2 실패·재시도·보류·재시작·복구 → B3 반복·재시작).
Airflow 는 **REST 로만** 부른다 — CLI 를 docker exec 하면 측정 대상 cgroup 에 150MB 급 프로세스가 끼어든다.
업무 처리·결과 스냅샷은 fake-aws(상한 밖)에서 한다. 판정은 analyze.py 가 저장한 원자료로 한다(드라이버는 판정하지 않는다).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parent
sys.path.insert(0, str(LOCAL))
os.environ.setdefault("COMPOSE_FILE", "compose.yaml:micro/compose.micro.yaml")
os.environ.setdefault("COMPOSE_PROJECT_NAME", "edge-micro")
import lab  # noqa: E402  (fake·psql·faults·snapshot·state_rows 재사용)

lab.FAKE = "http://127.0.0.1:54666"
API = "http://127.0.0.1:58200"
DAG = lab.DAG
DAY = "2026-09-22"
NORMALIZE = "normalize-investor-estimate"
OUT: Path
MARKS: list[dict] = []


def mark(event: str, **fields) -> None:
    rec = {"t": round(time.time(), 1), "event": event, **fields}
    MARKS.append(rec)
    with (OUT / "marks.jsonl").open("a") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rec, ensure_ascii=False, default=str), flush=True)


def rest(method: str, path: str, body: dict | None = None, timeout: float = 30) -> tuple[int, dict, float]:
    started = time.monotonic()
    token = json.loads(urllib.request.urlopen(f"{API}/auth/token", timeout=timeout).read())["access_token"]
    req = urllib.request.Request(f"{API}{path}", data=None if body is None else json.dumps(body).encode(),
                                 method=method, headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return r.status, (json.loads(data) if data else {}), time.monotonic() - started
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read()[:300].decode(errors="replace")}, time.monotonic() - started


def compose_env(args) -> dict:
    return {"MICRO_MEM": args.mem, "MICRO_PARALLELISM": str(args.parallelism), "MICRO_PARSING": str(args.parsing),
            "MICRO_POOL": str(args.pool), "MICRO_OVERFLOW": str(args.overflow), "MICRO_HEALTH": args.health,
            "MICRO_MALLOC_ARENA_MAX": args.malloc_arena_max,
            "MICRO_OBS_OUT": f"/out/{OUT.relative_to(LOCAL / 'results' / 'micro')}/samples.jsonl",
            "EDGE_LAB_TODAY_KST": DAY}


def code_hash() -> dict:
    """컨테이너에 마운트되는 실행 코드의 해시 — 실험 시작·끝이 다르면 그 실험은 무효다(실행 중 수정 금지)."""
    import hashlib
    files = [*sorted(HERE.glob("*.sh")), HERE / "compose.micro.yaml", LOCAL / "compose.yaml", LOCAL / "fake_aws.py",
             LOCAL / "step_shim.py", *sorted((LOCAL.parent / "dags").glob("*.py"))]
    return {str(f.relative_to(LOCAL.parent)): hashlib.sha256(f.read_bytes()).hexdigest()[:12] for f in files}


def start(args) -> None:
    mark("code_hash_start", files=code_hash())
    env = compose_env(args)
    lab.compose("up", "-d", "--wait", "postgres", "flyway", "fake-aws", "airflow-db")
    lab.compose("stop", "airflow", "observer", check=False)
    lab.compose("rm", "-f", "airflow", "observer", check=False)
    # 메타DB 스키마 — 운영처럼 서비스와 별개의 단일 작업(측정 밖). 실험마다 메타DB 를 새로(이전 실험 이력 제외).
    lab.compose("exec", "-T", "airflow-db", "psql", "-U", "airflow", "-d", "postgres", "-c",
                "DROP DATABASE IF EXISTS airflow WITH (FORCE)")
    lab.compose("exec", "-T", "airflow-db", "psql", "-U", "airflow", "-d", "postgres", "-c", "CREATE DATABASE airflow")
    lab.compose("run", "--rm", "--no-deps", "airflow", "airflow", "db", "migrate", env=env)
    lab.compose("up", "-d", "observer", env=env)
    time.sleep(3)
    mark("start_airflow", mem=args.mem, settings=vars(args))
    lab.compose("up", "-d", "airflow", env=env)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        state = subprocess.run(["docker", "inspect", "edge-micro-airflow-1", "--format", "{{.State.Health.Status}}"],
                               capture_output=True, text=True).stdout.strip()
        if state == "healthy":
            break
        time.sleep(5)
    mark("healthy" if state == "healthy" else "not_healthy", health=state)
    # DAG 등록 대기 → unpause(정기 스케줄 생성은 use_job_schedule=False 로 꺼져 있다 — 수동 run 만 돈다).
    while time.monotonic() < deadline + 120:
        code, _, _ = rest("GET", f"/api/v2/dags/{DAG}")
        if code == 200:
            break
        time.sleep(3)
    mark("dag_loaded", code=code)
    rest("PATCH", f"/api/v2/dags/{DAG}", {"is_paused": False})


def reset(tag: str) -> None:
    """이전 배치의 run·원장·레이크를 비운다(REST·fake·업무 DB — Airflow 컨테이너에 exec 하지 않는다)."""
    lab.idle_ecs()
    code, runs, _ = rest("GET", f"/api/v2/dags/{DAG}/dagRuns?limit=100")
    for r in runs.get("dag_runs", []):
        if r["state"] in ("running", "queued"):
            raise RuntimeError(f"초기화 전 도는 run: {r['dag_run_id']}")
        rest("DELETE", f"/api/v2/dags/{DAG}/dagRuns/{urllib.parse.quote(r['dag_run_id'], safe='')}")
    lab.fake("/_lab/reset", {})
    lab.compose("exec", "-T", "fake-aws", "sh", "-c",
                "rm -rf /lab-data/lake /lab-data/state /lab-data/ecs-logs && mkdir -p /lab-data/state")
    tickers = sorted({json.loads(line)["our_ticker"] for f in (LOCAL / "inputs/dev-lake/raw").rglob("*.ndjson")
                      for line in f.read_text().splitlines() if line.strip()})
    values = ",".join(f"('lab_{t}','{t}')" for t in tickers)
    lab.psql("TRUNCATE investor_flow_intraday, ops_task_attempt, ops_reconciliation_issue,"
             " ops_expectation_snapshot, ops_expected_task, ops_pipeline_run CASCADE;"
             f" INSERT INTO entity (entity_id, entity_type, display_name) SELECT id, 'INSTRUMENT', t"
             f"  FROM (VALUES {values}) v(id, t) ON CONFLICT DO NOTHING;"
             f" INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type, currency_code)"
             f"  SELECT id, 'XKRX', t, 'EQUITY', 'KRW' FROM (VALUES {values}) v(id, t) ON CONFLICT DO NOTHING;")
    mark("reset", tag=tag)


def run_id_of(batch: str, hhmm: str) -> str:
    return f"micro__{batch}__{DAY}T{hhmm}"


def trigger(batch: str, hhmm: str, conf: dict | None = None) -> str:
    rid = run_id_of(batch, hhmm)
    code, body, _ = rest("POST", f"/api/v2/dags/{DAG}/dagRuns",
                         {"dag_run_id": rid, "logical_date": lab.slot_iso(DAY, hhmm), "conf": conf or {}})
    mark("trigger", run=rid, code=code, detail=None if code == 200 else body)
    return rid


def wait_run(rid: str, timeout: float = 1800) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code, body, _ = rest("GET", f"/api/v2/dags/{DAG}/dagRuns/{urllib.parse.quote(rid, safe='')}", timeout=15)
            if code == 200 and body.get("state") in ("success", "failed"):
                mark("run_done", run=rid, state=body["state"])
                return body["state"]
        except (OSError, ValueError):
            pass                        # 재시작 창 — API 가 잠시 없다
        time.sleep(3)
    mark("run_timeout", run=rid)
    return "timeout"


def ecs_running(step: str, rid_pipeline: str) -> str | None:
    for t in lab.fake("/_lab/state")["tasks"]:
        if t["command"][:1] == [step] and rid_pipeline in t["command"] and t["lastStatus"] == "RUNNING":
            return t["taskArn"]
    return None


def when_running(step: str, hhmm: str, after: float, action) -> threading.Thread:
    """그 슬롯의 스텝 ECS 태스크가 RUNNING 이 되면 after 초 뒤 action(arn) — 외부 작업 대기 중에 사건을 일으킨다."""
    pipeline = lab.rid(DAY, hhmm)

    def go():
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            arn = ecs_running(step, pipeline)
            if arn:
                time.sleep(after)
                action(arn)
                return
            time.sleep(1)
        mark("when_running_timeout", step=step, slot=hhmm)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def burst(arn: str) -> None:
    """외부 작업 대기 중 UI·API 접근과 DAG 재파싱 유도."""
    for f in (LOCAL.parent / "dags").glob("*.py"):
        os.utime(f)
    mark("touch_dags")
    paths = [f"/api/v2/dags/{DAG}", f"/api/v2/dags/{DAG}/dagRuns?limit=20", "/api/v2/monitor/health",
             f"/api/v2/dags/{DAG}/dagRuns/~/taskInstances?limit=50", "/api/v2/dags?limit=20",
             f"/api/v2/dags/{DAG}/details", "/api/v2/importErrors", "/api/v2/version"]
    results = []
    for i in range(30):
        code, _, sec = rest("GET", paths[i % len(paths)])
        results.append([paths[i % len(paths)], code, round(sec, 3)])
    mark("burst", arn=arn, results=results)


def stop_task(arn: str) -> None:
    lab.fake("/", {"cluster": "lab", "task": arn, "reason": "micro: 외부 종료"},
             target="AmazonEC2ContainerServiceV20141113.StopTask")
    mark("stop_task", arn=arn)


def restart_airflow(arn: str | None = None) -> None:
    mark("restart_airflow_begin", arn=arn)
    subprocess.run(["docker", "restart", "edge-micro-airflow-1"], check=True, capture_output=True)
    mark("restart_airflow_end")


def idle(seconds: int, tag: str) -> None:
    mark("idle_begin", tag=tag, seconds=seconds)
    time.sleep(seconds)
    mark("idle_end", tag=tag)


def dump(batch: str) -> None:
    """배치 원자료: 결과 스냅샷(업무 DB·canonical·원장), ECS 대역 상태, 업무 실행 기록, Airflow task 시각."""
    snap = lab.snapshot(f"micro-{OUT.name}-{batch}")
    ti = lab.compose("exec", "-T", "airflow-db", "psql", "-U", "airflow", "-d", "airflow", "-At", "-c",
                     "SELECT json_agg(t) FROM ("
                     " SELECT run_id, task_id, try_number, state, queued_dttm, start_date, end_date, 'ti' AS src"
                     "  FROM task_instance WHERE dag_id = 'edge_investor_intraday'"
                     " UNION ALL SELECT run_id, task_id, try_number, state, queued_dttm, start_date, end_date, 'hist'"
                     "  FROM task_instance_history WHERE dag_id = 'edge_investor_intraday') t")
    runs = lab.compose("exec", "-T", "airflow-db", "psql", "-U", "airflow", "-d", "airflow", "-At", "-c",
                       "SELECT json_agg(r) FROM (SELECT run_id, state, start_date, end_date FROM dag_run"
                       " WHERE dag_id = 'edge_investor_intraday') r")
    ledger = lab.psql("SELECT json_agg(x) FROM (SELECT r.run_key, r.orchestrator, r.orchestration_status,"
                      " (SELECT json_agg(json_build_object('task', et.task_key, 'outcome', et.task_outcome,"
                      "   'reason', et.outcome_reason)) FROM ops_expected_task et"
                      "   WHERE et.pipeline_run_id = r.pipeline_run_id) AS tasks FROM ops_pipeline_run r) x")
    holds = lab.psql("SELECT json_agg(x) FROM (SELECT dedupe_key, status, evidence->>'kind' AS kind"
                     " FROM ops_reconciliation_issue WHERE issue_type = 'EXECUTION_HOLD') x")
    inspect = json.loads(subprocess.run(["docker", "inspect", "edge-micro-airflow-1"], capture_output=True,
                                        text=True).stdout)[0]
    (OUT / f"{batch}.json").write_text(json.dumps({
        "snapshot": snap, "task_instances": json.loads(ti.strip() or "null"), "dag_runs": json.loads(runs.strip() or "null"),
        "ledger": json.loads(ledger.strip() or "null"), "holds": json.loads(holds.strip() or "null"),
        "business_runs": lab.state_rows("business_runs.jsonl"),
        "partition_writes": lab.state_rows("partition_writes.jsonl"),
        "restart_count": inspect["RestartCount"], "oom_killed": inspect["State"]["OOMKilled"],
        "health": inspect["State"].get("Health", {}).get("Status"),
    }, ensure_ascii=False, indent=1, default=str))
    mark("dump", batch=batch)


def smoke(args) -> None:
    """사전 점검(판정 제외) — 기동·run 1회·원자료 덤프가 되는지만 본다."""
    start(args)
    reset("smoke")
    wait_run(trigger("smoke", "09:35"))
    dump("B1")
    mark("smoke_done")


def suite(args) -> None:
    start(args)
    idle(300, "start")
    reset("B1")
    for hhmm in ["09:35", "10:05", "11:25", "13:25", "14:35"]:
        lab.faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 120, "times": 1}])
        if hhmm == "11:25":
            when_running(NORMALIZE, hhmm, 20, burst)
        wait_run(trigger("B1", hhmm))
    dump("B1")
    idle(300, "after_B1")

    reset("B2")
    lab.faults([{"step": "load-investor-intraday", "action": "exit", "code": 1, "times": 1}])
    wait_run(trigger("B2", "09:35"))
    lab.faults([{"step": "ingest-raw-investor-estimate", "action": "fail_to_start", "times": 1}])
    wait_run(trigger("B2", "10:05"))
    lab.faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 90, "times": 1}])
    when_running(NORMALIZE, "11:25", 30, stop_task)
    wait_run(trigger("B2", "11:25"))
    lab.faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 150, "times": 1}])
    when_running(NORMALIZE, "13:25", 30, restart_airflow)
    wait_run(trigger("B2", "13:25"))
    wait_run(trigger("B2", "14:35"))
    dump("B2")
    idle(300, "after_B2")

    reset("B3")
    for hhmm in ["09:35", "10:05", "11:25", "13:25", "14:35"]:
        lab.faults([{"step": NORMALIZE, "action": "sleep_in_step", "seconds": 120, "times": 1}])
        wait_run(trigger("B3", hhmm))
    dump("B3")
    idle(300, "after_B3")
    for n in (1, 2):
        restart_airflow()
        idle(180, f"after_restart_{n}")
    (OUT / "airflow.log").write_text(lab.compose("logs", "--no-color", "airflow", check=False))
    mark("code_hash_end", files=code_hash())
    mark("suite_done")


def main() -> int:
    global OUT
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--mem", required=True)
    p.add_argument("--parallelism", type=int, default=4)
    p.add_argument("--parsing", type=int, default=2)
    p.add_argument("--pool", type=int, default=3)
    p.add_argument("--overflow", type=int, default=5)
    p.add_argument("--health", choices=("cli", "light"), default="cli")
    p.add_argument("--malloc-arena-max", default="", help="비우면 glibc 기본")
    p.add_argument("--smoke", action="store_true", help="사전 점검 1회(판정 제외, results/micro/explore/)")
    args = p.parse_args()
    OUT = LOCAL / "results" / "micro" / ("explore" if args.smoke else "") / args.name
    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit(f"{OUT} 가 비어 있지 않다 — 실험 결과를 덮어쓰지 않는다")
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        (smoke if args.smoke else suite)(args)
    except Exception as exc:
        mark("suite_error", error=repr(exc)[:500])
        (OUT / "airflow.log").write_text(lab.compose("logs", "--no-color", "airflow", check=False))
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
