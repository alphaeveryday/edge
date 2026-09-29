"""micro·small 검증 관측기 — Airflow cgroup **밖**에서 2초마다 한 줄(JSONL)을 남긴다(ALPHA-1119).

읽는 것(호스트 pid·cgroup 네임스페이스, 읽기 전용):
- Airflow 컨테이너 cgroup: memory.current·peak·max·swap.max·swap.current·events(oom·oom_kill·max·high)·
  stat(anon·file·kernel)·cpu.stat(usage·throttled)·cpu.max·pids.current.
- 그 cgroup 의 프로세스별 PSS(smaps_rollup — fork 로 공유한 페이지를 나눠 센다. RSS 합은 과대)를 구성요소로 묶는다.
- 메타DB: 연결 수(application_name = 구성요소 표지 PGAPPNAME, state), scheduler·dag-processor heartbeat 경과,
  DAG 마지막 파싱 경과, import 오류 수.
측정 대상 cgroup 에 프로세스를 넣지 않는다(docker exec 로 재면 재는 행위가 메모리·CPU 에 섞인다).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import psycopg2

OUT = Path(os.environ["OBS_OUT"])
INTERVAL = float(os.environ.get("OBS_INTERVAL", "2"))
DSN = os.environ["OBS_DSN"]
CG = Path("/sys/fs/cgroup")


def _cgroup_of_airflow() -> Path | None:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if b"/micro/supervisor.sh" in (proc / "cmdline").read_bytes():
                rel = (proc / "cgroup").read_text().strip().split("::", 1)[1]
                return CG / rel.lstrip("/")
        except (OSError, IndexError):
            continue
    return None


def _kv(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        k, _, v = line.partition(" ")
        out[k] = int(v) if v.strip().lstrip("-").isdigit() else v.strip()
    return out


def _one(path: Path):
    text = path.read_text().strip()
    return int(text) if text.isdigit() else text


def _component(cmd: str) -> str:
    if "supervisor.sh" in cmd:
        return "supervisor"
    if "healthcheck.sh" in cmd or "jobs check" in cmd or cmd.startswith("curl"):
        return "healthcheck"
    if "serve-logs" in cmd:
        return "serve_logs"
    if "api-server" in cmd or "api_server" in cmd or "uvicorn" in cmd or "gunicorn" in cmd:
        return "api"
    if "dag-processor" in cmd:
        return "dag_processor"
    if "LocalExecutor" in cmd:
        return "executor_worker"
    if cmd.startswith("airflow worker") or "task runner" in cmd or "supervise" in cmd:
        return "task"
    if "scheduler" in cmd:
        return "scheduler"
    return "other"


def _processes(cg: Path) -> dict:
    groups: dict[str, dict] = {}
    for pid in (cg / "cgroup.procs").read_text().split():
        p = Path("/proc") / pid
        try:
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            roll = (p / "smaps_rollup").read_text()
        except OSError:
            continue
        pss = next((int(line.split()[1]) for line in roll.splitlines() if line.startswith("Pss:")), 0)
        rss = next((int(line.split()[1]) for line in roll.splitlines() if line.startswith("Rss:")), 0)
        g = groups.setdefault(_component(cmd), {"n": 0, "pss_kb": 0, "rss_kb": 0, "cmds": []})
        g["n"] += 1
        g["pss_kb"] += pss
        g["rss_kb"] += rss
        if len(g["cmds"]) < 4:
            g["cmds"].append(cmd[:80])
    return groups


def _db(cur) -> dict:
    out = {}
    cur.execute("SELECT coalesce(application_name,''), coalesce(state,'?'), count(*) FROM pg_stat_activity"
                " WHERE datname = 'airflow' GROUP BY 1, 2")
    out["conns"] = [[a, s, n] for a, s, n in cur.fetchall()]
    cur.execute("SELECT job_type, extract(epoch FROM now() - max(latest_heartbeat)) FROM job"
                " WHERE state = 'running' GROUP BY job_type")
    out["heartbeat_age_s"] = {k: round(float(v), 1) for k, v in cur.fetchall()}
    cur.execute("SELECT dag_id, extract(epoch FROM now() - last_parsed_time), is_paused FROM dag")
    out["dag_parse_age_s"] = {k: [round(float(v), 1) if v is not None else None, p] for k, v, p in cur.fetchall()}
    try:
        cur.execute("SELECT count(*) FROM import_error")
        out["import_errors"] = cur.fetchone()[0]
    except psycopg2.Error:
        cur.connection.rollback()
        out["import_errors"] = None
    return out


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    conn = None
    while True:
        rec: dict = {"t": round(time.time(), 1)}
        cg = _cgroup_of_airflow()
        if cg is None:
            rec["airflow"] = "absent"
        else:
            try:
                rec["cg"] = str(cg)
                for name in ("memory.current", "memory.peak", "memory.max", "memory.swap.max", "memory.swap.current",
                             "cpu.max", "pids.current"):
                    rec[name] = _one(cg / name)
                rec["memory.events"] = _kv(cg / "memory.events")
                # 컨테이너가 재시작하면 cgroup 이 새로 생겨 위 카운터가 0 으로 돌아간다(E2 에서 OOM 이력을 잃었다).
                # 부모(docker) cgroup 의 memory.events 는 하위 전체의 누적이라 재시작을 넘어 남는다 — 상한이 걸린 것은
                # airflow 뿐이므로 여기서 늘어난 oom_kill 은 사실상 airflow 의 것이다(호스트 OOM 이면 다른 것도 섞인다).
                rec["parent.memory.events"] = _kv(cg.parent / "memory.events")
                stat = _kv(cg / "memory.stat")
                rec["memory.stat"] = {k: stat.get(k) for k in ("anon", "file", "kernel", "sock", "shmem")}
                rec["cpu.stat"] = _kv(cg / "cpu.stat")
                rec["procs"] = _processes(cg)
            except OSError as exc:
                rec["cg_error"] = str(exc)
        try:
            if conn is None or conn.closed:
                conn = psycopg2.connect(DSN)
                conn.autocommit = True
            with conn.cursor() as cur:
                rec["db"] = _db(cur)
        except psycopg2.Error as exc:
            rec["db_error"] = str(exc)[:200]
            conn = None
        with OUT.open("a") as fp:
            fp.write(json.dumps(rec) + "\n")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
