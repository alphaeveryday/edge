"""실제 AWS 단기 검증 판정기(ALPHA-1119) — local/results/aws/<exp>/ 를 verify/criteria_aws.json 기준으로 판정한다.

    python verify/analyze_aws.py <exp>      # → results/aws/<exp>/verdict.json, 요약 출력

판정은 True(통과)·False(실패)·None(판정 불가: 표본 부족·계측 공백·근거 없음) 셋이다. None 을 통과로 세지 않는다.
관측 원천: 호스트 관측기(samples.log·kmsg.log·docker-events.log — 대상이 죽어도 남는다), 배치 증거(B1~B3.json),
health.jsonl(15초), deployinfo-*.json, rds-*.json.
"""

from __future__ import annotations

import glob
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent / "local" / "results" / "aws"
CRIT = json.loads((HERE / "criteria_aws.json").read_text())
DAG = "edge_investor_intraday_verify"
MiB = 2 ** 20
BUSINESS = {"collect": "ingest-raw-investor-estimate", "normalize": "normalize-investor-estimate",
            "load": "load-investor-intraday"}


def ts(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()


def p95(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(0.95 * (len(xs) - 1) + 0.999))], 1) if xs else None


def jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


# ── 호스트 관측 ──
def host_samples(d: Path) -> list[dict]:
    out, cur = [], None
    for f in sorted(glob.glob(str(d / "host-obs" / "*" / "edge-obs" / "samples.log"))):
        for line in open(f, errors="replace"):
            tag, _, rest = line.rstrip("\n").partition(" ")
            if tag == "T":
                cur = {"t": float(rest), "mem": {}, "cg": {}, "ps": [], "psi": {}}
                out.append(cur)
            elif cur is None:
                continue
            elif tag == "M":
                k, _, v = rest.partition(":")
                if v.split() and v.split()[0].isdigit():
                    cur["mem"][k.strip()] = int(v.split()[0]) * (1024 if "kB" in v else 1)
            elif tag == "S":
                cur["psi"][rest.split(" ", 2)[0] + " " + rest.split(" ", 2)[1]] = rest
            elif tag == "C":
                path, *kv = rest.split(" ")
                vals = dict(x.split("=", 1) for x in kv if "=" in x)
                cur["cg"][path.rstrip("/")] = {k: (int(v) if v.lstrip("-").isdigit() else v) for k, v in vals.items()}
            elif tag == "P":
                cur["ps"].append(rest)
    return out


def classify(path: str) -> str:
    if "/ecstasks" in path:
        return "airflow_task" if re.search(r"/ecstasks/[^/]+$", path) else "airflow_task_child"
    for key, name in (("ecs.service", "ecs_agent"), ("docker.service", "dockerd"), ("containerd.service", "containerd"),
                      ("amazon-ssm-agent", "ssm_agent"), ("edge-obs", "observer"), ("system.slice", "system_other"),
                      ("user.slice", "user")):
        if key in path:
            return name
    return "other"


def host_summary(samples, windows):
    if not samples:
        return None
    total = samples[0]["mem"].get("MemTotal")
    avail = [s["mem"].get("MemAvailable") for s in samples]
    swap_total = samples[0]["mem"].get("SwapTotal", 0)
    swap_used = max(s["mem"].get("SwapTotal", 0) - s["mem"].get("SwapFree", 0) for s in samples)
    # Airflow 태스크 cgroup(ecstasks/<task>) — 경로별 최댓값, OOM 은 경로별 최댓값의 합(재시작으로 새 경로가 생긴다)
    # ECS 가 태스크마다 만드는 cgroup(…/ecstasks/<task-id>) — 컨테이너 하위 cgroup 은 제외(합이 태스크 값이다).
    task_paths = {p for s in samples for p in s["cg"] if re.search(r"/ecstasks/[^/]+$", p)}
    oom = sum(max((s["cg"].get(p, {}).get("ev_oom_kill", 0) or 0) for s in samples) for p in task_paths)
    comp_max: dict[str, int] = {}
    comp_mean: dict[str, list] = {}
    for s in samples:
        agg: dict[str, int] = {}
        for p, v in s["cg"].items():
            c = classify(p)
            if c in ("airflow_task_child",) or not isinstance(v.get("cur"), int):
                continue
            if c == "system_other" and p.count("/") > 4:
                continue
            agg[c] = agg.get(c, 0) + v["cur"]
        for c, v in agg.items():
            comp_max[c] = max(comp_max.get(c, 0), v)
            comp_mean.setdefault(c, []).append(v)

    def window_mean(tag, key):
        if tag not in windows:
            return None
        a, b = windows[tag]
        rows = [s for s in samples if b - 120 <= s["t"] <= b]
        if key == "avail":
            vals = [s["mem"].get("MemAvailable") for s in rows]
        else:
            vals = [sum(v.get("anon", 0) or 0 for p, v in s["cg"].items() if p in task_paths) for s in rows]
        return int(statistics.mean(vals) / MiB) if vals else None
    return {
        "mem_total_mib": total // MiB if total else None,
        "mem_available_min_mib": min(a for a in avail if a is not None) // MiB,
        "mem_available_p50_mib": int(statistics.median(a for a in avail if a is not None) // MiB),
        "swap_total_mib": swap_total // MiB, "swap_used_max_mib": swap_used // MiB,
        "task_cgroups": len(task_paths), "task_oom_kill": oom,
        "task_mem_max_mib": max((max((s["cg"].get(p, {}).get("cur", 0) or 0) for s in samples) for p in task_paths),
                                default=0) // MiB,
        "task_mem_limit_mib": max(((s["cg"].get(p, {}).get("limit") if isinstance(s["cg"].get(p, {}).get("limit"), int)
                                    else 0) for s in samples for p in task_paths), default=0) // MiB,
        "task_hit_limit_events": sum(max((s["cg"].get(p, {}).get("ev_max", 0) or 0) for s in samples) for p in task_paths),
        "component_max_mib": {k: v // MiB for k, v in sorted(comp_max.items())},
        "component_mean_mib": {k: int(statistics.mean(v) // MiB) for k, v in sorted(comp_mean.items())},
        "idle_task_anon_mib": {t: window_mean(t, "anon") for t in windows},
        "idle_mem_available_mib": {t: window_mean(t, "avail") for t in windows},
        "samples": len(samples),
        "task_paths_seen": len(task_paths),
        "max_gap_s": max((b["t"] - a["t"] for a, b in zip(samples, samples[1:])), default=None),
    }


def kernel_ooms(d: Path) -> list[str]:
    lines = []
    for f in glob.glob(str(d / "host-obs" / "*" / "edge-obs" / "kmsg.log")):
        lines += [x.strip() for x in open(f, errors="replace") if re.search(r"oom-kill|Out of memory|Killed process", x)]
    return lines


def docker_events(d: Path) -> dict:
    ev = []
    for f in glob.glob(str(d / "host-obs" / "*" / "edge-obs" / "docker-events.log")):
        for x in open(f, errors="replace"):
            try:
                ev.append(json.loads(x))
            except ValueError:
                continue
    af = [e for e in ev if "airflow" in json.dumps(e.get("Actor", {}).get("Attributes", {}))]
    return {"oom": [e.get("Actor", {}).get("Attributes", {}).get("name") for e in af if e.get("Action") == "oom"],
            "die": [(e.get("Actor", {}).get("Attributes", {}).get("name"),
                     e.get("Actor", {}).get("Attributes", {}).get("exitCode"), e.get("time")) for e in af
                    if e.get("Action") == "die"]}


# ── 배치 ──
def latency(batch: dict, restart_windows) -> dict:
    tries = [t for t in batch["task_tries"] if t.get("try_number")]
    key = {(t["dag_run_id"], t["task_id"], t["try_number"]): t for t in tries}
    gap, queue, submit, detect, unmatched, expected_missing = [], [], [], [], [], []
    for e in batch["ecs_tasks"]:
        m = re.match(rf"airflow:{DAG}/(.+)/(\w+)/(\d+)$", e["ref"] or "")
        tr = key.get((m[1], m[2], int(m[3]))) if m else None
        if tr is None:
            unmatched.append(e["arn"])
            continue
        if ts(tr["start_date"]) and ts(e["createdAt"]):
            submit.append(ts(e["createdAt"]) - ts(tr["start_date"]))
        else:
            expected_missing.append((e["arn"], tr["task_id"], tr["try_number"], "submit"))
        if ts(e["stoppedAt"]) and ts(tr["end_date"]):
            detect.append(ts(tr["end_date"]) - ts(e["stoppedAt"]))
        else:
            # 주입 재시작 창에 걸친 try(대기 중 Airflow 가 죽은 try)는 종료 감지 표본이 원래 없다 — 그 밖의 공백은 판정 불가
            if not any(a <= (ts(tr["start_date"]) or 0) <= b or a <= (ts(tr["end_date"]) or b) <= b + 600
                       for a, b in restart_windows):
                expected_missing.append((e["arn"], tr["task_id"], tr["try_number"]))
    by_run: dict = {}
    for t in tries:
        by_run.setdefault(t["dag_run_id"], {})[(t["task_id"], t["try_number"])] = t
    order = ("plan", "collect", "normalize", "load", "report", "verdict")
    for rid, rows in by_run.items():
        for i, step in enumerate(order):
            t = rows.get((step, 1))
            if not t:
                continue
            if not ts(t["queued_when"]) or not ts(t["start_date"]):
                expected_missing.append((rid, step, 1, "queue"))   # try 는 있는데 시각이 없다 = 표본 누락
                continue
            queue.append(ts(t["start_date"]) - ts(t["queued_when"]))
            prev = rows.get((order[i - 1], 1)) if i else None
            if prev and ts(prev.get("end_date")):
                gap.append(ts(t["queued_when"]) - ts(prev["end_date"]))
    return {"prev_end_to_queued_p95": p95(gap), "queued_to_start_p95": p95(queue),
            "start_to_ecs_created_p95": p95(submit), "ecs_stopped_to_end_p95": p95(detect),
            "n": {"gap": len(gap), "queue": len(queue), "submit": len(submit), "detect": len(detect)},
            "ecs_tasks": len(batch["ecs_tasks"]), "unmatched": unmatched, "missing_samples": expected_missing}


def run_of(batch, hhmm):
    return next((r for r in batch["runs"] if r["dag_run_id"].endswith("__" + hhmm.replace(":", ""))), None)


def tries_of(batch, rid, task):
    return sorted((t for t in batch["task_tries"] if t.get("dag_run_id") == rid and t.get("task_id") == task),
                  key=lambda t: t["try_number"])


def ecs_of(batch, rid, cmd):
    return [e for e in batch["ecs_tasks"] if f"/{rid}/" in (e["ref"] or "") and e["cmd"] == cmd]


def starts(batch, rid, step):
    return len([r for r in batch["state"]["business_starts"] if f"/{rid}/" in (r.get("attempt_ref") or "")
                and r.get("step") == BUSINESS[step] and r.get("injected_exit") is None])


def ok_runs(batch, rid, step):
    """끝까지 간 업무 실행(주입 exit 아님) 중 exit 0 인 수."""
    return len([r for r in batch["state"]["business_runs"] if f"/{rid}/" in (r.get("attempt_ref") or "")
                and r.get("step") == BUSINESS[step] and r.get("exit") == 0])


def writes(batch, rid):
    return len([r for r in batch["state"]["partition_writes"] if f"/{rid}/" in (r.get("attempt_ref") or "")])


def ledger_run(batch, hhmm):
    """검증 원장(verify-ledger) 의 그 슬롯 행 — (orchestrator, orchestration_status, {task_key: outcome}).
    원장이 없으면 None(판정 불가). 배치마다 verify-reset 으로 비우므로 슬롯 시각으로 짝짓는다."""
    led = batch.get("ledger")
    if not led:
        return None
    run = next((r for r in led.get("runs", []) if r[0].endswith("T" + hhmm)), None)
    if run is None:
        return ("MISSING", None, {})
    return (run[2], run[4], {t[1]: t[2] for t in led.get("tasks", []) if t[0] == run[1]})


def holds(batch, rid):
    return [h["hold"] for h in batch.get("airflow_holds", []) if h["dag_run_id"] == rid]


def outcomes(batches) -> dict:
    res = {}
    for b in ("B1", "B3"):
        if b not in batches:
            res[b] = None
            continue
        d = batches[b]
        rows = []
        for hhmm in CRIT["scenarios"][b]["slots"]:
            r = run_of(d, hhmm)
            rid = r["dag_run_id"] if r else None
            rows.append({"slot": hhmm, "state": r and r["state"],
                         "starts": {s: starts(d, rid, s) for s in BUSINESS} if rid else None,
                         "ok_runs": {s: ok_runs(d, rid, s) for s in BUSINESS} if rid else None,
                         "writes": writes(d, rid) if rid else None,
                         "ledger": ledger_run(d, hhmm),
                         "ecs": len([e for e in d["ecs_tasks"] if f"/{rid}/" in (e["ref"] or "")]) if rid else None})
        one = {s: 1 for s in BUSINESS}
        # Airflow 가 성공이라 말한 것만이 아니라, 업무가 정확히 한 번 끝났고(exit 0) 산출물이 쓰였고
        # 원장이 AIRFLOW·SUCCEEDED·전 작업 FULFILLED 로 닫았는지까지 본다. 원장이 없으면 판정 불가.
        res[b] = None if any(x["ledger"] is None for x in rows) else {
            "runs": rows,
            "ok": all(x["state"] == "success" and x["starts"] == one and x["ok_runs"] == one and x["writes"]
                      and x["ecs"] == 5 and x["ledger"][:2] == ("AIRFLOW", "SUCCEEDED") and x["ledger"][2]
                      and set(x["ledger"][2].values()) == {"FULFILLED"} for x in rows)}
    if "B2" in batches:
        d = batches["B2"]
        s = CRIT["scenarios"]["B2"]["slots"]
        rid = lambda k: (run_of(d, s[k]) or {}).get("dag_run_id")  # noqa: E731
        st = lambda k: (run_of(d, s[k]) or {}).get("state")  # noqa: E731
        if not d.get("ledger"):
            res["B2"] = None                     # 원장 없음 = 판정 불가
            return res
        led = lambda k: ledger_run(d, s[k])[1]  # noqa: E731 — orchestration_status
        holds_l = d["ledger"].get("holds", [])
        chk = {
            "ledger_status": led("fail_confirmed") == "FAILED" and led("retry_not_run") == "SUCCEEDED"
            and led("restart_tracking") == "SUCCEEDED" and led("after_release") == "SUCCEEDED"
            and led("hold_result_unknown") != "SUCCEEDED" and led("hold_state_unknown") != "SUCCEEDED",
            "outputs": writes(d, rid("after_release")) > 0 and writes(d, rid("restart_tracking")) > 0
            and writes(d, rid("fail_confirmed")) == 0 and writes(d, rid("hold_result_unknown")) == 0,
            "fail_confirmed": st("fail_confirmed") == "failed"
            and [t["state"] for t in tries_of(d, rid("fail_confirmed"), "load")] == ["failed"]
            and len(ecs_of(d, rid("fail_confirmed"), BUSINESS["load"])) == 1,
            "retry_not_run": st("retry_not_run") == "success"
            and len(tries_of(d, rid("retry_not_run"), "collect")) == 2
            and len(ecs_of(d, rid("retry_not_run"), BUSINESS["collect"])) == 2
            and starts(d, rid("retry_not_run"), "collect") == 1,
            "hold_result_unknown": st("hold_result_unknown") == "failed"
            and any(h.get("kind") == "RESULT_UNKNOWN" for h in holds(d, rid("hold_result_unknown")))
            and starts(d, rid("hold_result_unknown"), "load") == 0,
            "restart_tracking": st("restart_tracking") == "success"
            and len(ecs_of(d, rid("restart_tracking"), BUSINESS["normalize"])) == 1
            and starts(d, rid("restart_tracking"), "normalize") == 1,
            "hold_state_unknown": st("hold_state_unknown") == "failed"
            and any(h.get("kind") == "ECS_STATE_UNKNOWN" for h in holds(d, rid("hold_state_unknown")))
            and len(ecs_of(d, rid("hold_state_unknown"), BUSINESS["collect"])) == 1,
            "blocked_before_release": st("blocked_before_release") == "failed"
            and [e["exit"] for e in ecs_of(d, rid("blocked_before_release"), BUSINESS["collect"])] == [76]
            and starts(d, rid("blocked_before_release"), "collect") == 0,
            "after_release": st("after_release") == "success" and starts(d, rid("after_release"), "collect") == 1,
            "ledger_holds_recorded": {h[2] for h in holds_l} >= {"RESULT_UNKNOWN", "ECS_STATE_UNKNOWN"},
        }
        res["B2"] = {"checks": chk, "ok": all(chk.values()), "ledger_runs": [r[0] for r in d["ledger"].get("runs", [])]}
    return res


def analyze(exp: str) -> dict:
    d = ROOT / exp
    marks = jl(d / "marks.jsonl")
    health = jl(d / "health.jsonl")
    batches = {b: json.loads((d / f"{b}.json").read_text()) for b in ("B1", "B2", "B3") if (d / f"{b}.json").exists()}
    deploys = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(d / "deployinfo-*.json")))]
    rdss = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(d / "rds-*.json")))]
    windows = {}
    open_ = {}
    for m in marks:
        if m["event"] == "idle_begin":
            open_[m["tag"]] = m["t"]
        elif m["event"] == "idle_end" and m["tag"] in open_:
            windows[m["tag"]] = (open_.pop(m["tag"]), m["t"])
    rw = []
    for m in marks:
        if m["event"] == "restart_airflow_begin":
            end = next((x["t"] for x in marks if x["event"] == "restart_airflow_end" and x["t"] >= m["t"]), m["t"] + 600)
            rw.append((m["t"] - 10, end + 180))
    samples = host_samples(d)
    host = host_summary(samples, windows)
    kern = kernel_ooms(d)
    obs_files = {n: bool(glob.glob(str(d / "host-obs" / "*" / "edge-obs" / n)))
                 for n in ("samples.log", "kmsg.log", "docker-events.log")}
    dock = docker_events(d)
    lat = {b: latency(x, rw) for b, x in batches.items()}
    out_come = outcomes(batches)
    burst = next((m["results"] for m in marks if m["event"] == "burst"), [])
    # health 공백: 주입 재시작 창 밖에서 60초 넘게 샘플이 없거나 오류
    ok_h = [h for h in health if "health" in h and h.get("code") == 200]
    # 공백은 **성공한** 표본 사이로 잰다 — 실패 행이 촘촘해도 heartbeat 를 확인한 것이 아니다.
    gaps = [b["t"] - a["t"] for a, b in zip(ok_h, ok_h[1:]) if not any(x <= a["t"] <= y for x, y in rw)]
    err_outside = [h for h in health if h not in ok_h and not any(x <= h["t"] <= y for x, y in rw)]
    bad_h = [h for h in ok_h if not any(x <= h["t"] <= y for x, y in rw) and
             not (h["health"].get("scheduler", {}).get("status") == "healthy"
                  and h["health"].get("dag_processor", {}).get("status") == "healthy")]
    last_deploy = deploys[-1] if deploys else {}
    # RDS 관측이 실험 전 구간(첫 배포 확인 ~ 마지막 표시)을 덮어야 A8 을 판정한다 — 빈 구간이 2분 넘으면 판정 불가.
    spans = sorted((datetime.fromisoformat(r["from"]).timestamp(), datetime.fromisoformat(r["to"]).timestamp())
                   for r in rdss)
    need = (min(m["t"] for m in marks), max(m["t"] for m in marks)) if marks else None
    covered_to, rds_uncovered = (need[0] if need else 0), []
    for a, b in spans:
        if need and a > covered_to + 120:
            rds_uncovered.append((covered_to, a))
        covered_to = max(covered_to, b)
    if need and covered_to < need[1] - 120:
        rds_uncovered.append((covered_to, need[1]))
    db_roles = [max((sum(s["n"] for s in (st.get("sessions") or []) if s["usename"] == u) for r in rdss
                     for st in r.get("dbadmin_stats", [])), default=None) for u in ("airflow_meta", "airflow_verify")]
    out = {
        "exp": exp, "deploy": {k: last_deploy.get(k) for k in ("task_memory", "hosts", "health_matches_plan",
                                                                "settings_match_plan", "airflow_version",
                                                                "running_image_digests")},
        "host": host, "host_obs_files": obs_files, "kernel_oom_lines": kern, "docker": dock, "latency": lat, "outcomes": out_come,
        "health": {"samples": len(health), "errors": len(health) - len(ok_h), "errors_outside_restart": len(err_outside),
                   "unhealthy_outside_restart": len(bad_h), "max_gap_s": max(gaps, default=None),
                   "import_errors_missing": sum(1 for h in ok_h if h.get("import_errors") is None),
                   "import_errors_max": max((h["import_errors"] for h in ok_h if h.get("import_errors") is not None),
                                            default=None)},
        "burst": {"n": len(burst), "non_2xx": [x for x in burst if not 200 <= x[1] < 300], "p95_s": p95([x[2] for x in burst])},
        "rds": {"checks": len(rdss), "uncovered": rds_uncovered, "any_stop": any(any(r["stop"].values()) for r in rdss),
                "business_failed_sfn": sorted({x for r in rdss for x in r.get("business_failed_sfn", [])}),
                "data_gap": any(r.get("data_gap") for r in rdss),
                "freeable_min": min((r["FreeableMemory"]["min"] for r in rdss if r["FreeableMemory"]["min"] is not None), default=None),
                "connections_max": max((r["DatabaseConnections"]["max"] or 0 for r in rdss), default=None),
                "cpu_max": max((r["CPUUtilization"]["max"] or 0 for r in rdss), default=None),
                "role_sessions_max": {"airflow_meta": db_roles[0], "airflow_verify": db_roles[1]},
                "airflow_db_error_logs": (None if any(r.get("airflow_db_error_logs") is None for r in rdss)
                                          else [x for r in rdss for x in r["airflow_db_error_logs"]])},
        "stopped": [m for m in marks if m["event"] in ("abort", "unplaceable")],
    }
    unplaceable = [e for dep in deploys for e in dep.get("service", {}).get("events", [])
                   if "unable to place" in e or "insufficient memory" in e or "insufficient CPU" in e]
    out["unplaceable_events"] = unplaceable
    unexpected_die = [d for d in dock["die"] if d[2] and not any(x <= float(d[2]) <= y for x, y in rw)]
    out["unexpected_airflow_container_exits"] = unexpected_die
    lim = CRIT
    def lat_ok():
        if not lat:
            return None
        if any(v["unmatched"] or v["missing_samples"] for v in lat.values()):
            return None
        vals = [v[k] for v in lat.values() for k in v if k.endswith("_p95")]
        if any(x is None for x in vals):
            return None
        return all(v["prev_end_to_queued_p95"] <= 15 and v["queued_to_start_p95"] <= 15 and
                   v["start_to_ecs_created_p95"] <= 20 and v["ecs_stopped_to_end_p95"] <= 30 for v in lat.values())
    placement = [h for h in (last_deploy.get("hosts") or [])]
    swap_used = host and host["swap_used_max_mib"]
    out["pass"] = {
        "A0_deploy_matches_plan": (None if not deploys else bool(last_deploy.get("health_matches_plan")
                                                                   and last_deploy.get("settings_match_plan"))),
        # 배치·기동: 실제 서비스 태스크가 RUNNING·HEALTHY 로 조회된 적이 있는가(deployinfo). 미배치 이벤트는 실패.
        "A1_placement": None if not deploys else (
            not unplaceable and any(t.get("health") == "HEALTHY" and all(v == "HEALTHY" for v in t["containers"].values())
                                    for dep in deploys for t in dep.get("running_tasks", []))),
        # OOM(커널·docker·cgroup) 0, 그리고 주입 재시작 창 밖의 Airflow 컨테이너 종료 0.
        # 관측이 온전해야 판정한다 — 태스크 cgroup 미관측·커널/docker 로그 부재·샘플 공백(5초 주기의 6배 초과)은 판정 불가.
        # 실패 증거가 하나라도 있으면 관측 공백과 무관하게 실패다 — 공백이 확인된 OOM 을 판정 불가로 덮지 않는다.
        "A2_no_oom_restart": False if (kern or dock["oom"] or unexpected_die or (host and host["task_oom_kill"])) else
        None if (host is None or not all(obs_files.values()) or not host["task_paths_seen"]
                 or (host["max_gap_s"] or 0) > 30) else (host["task_oom_kill"] == 0 and not kern and not dock["oom"]
                                                         and not unexpected_die),
        "A3_host_memory": None if host is None else (None if swap_used else host["mem_available_min_mib"] >= 64),
        "A4_heartbeat_parse": None if (not ok_h or out["health"]["max_gap_s"] is None or out["health"]["max_gap_s"] > 60
                                       or err_outside or out["health"]["import_errors_missing"]) else (
            out["health"]["unhealthy_outside_restart"] == 0 and out["health"]["import_errors_max"] == 0),
        "A5_latency": lat_ok(),
        "A6_outcomes": None if not all(b in out_come and out_come[b] for b in ("B1", "B2", "B3")) else all(
            out_come[b]["ok"] for b in ("B1", "B2", "B3")),
        "A7_business": None if not all(b in out_come and out_come[b] for b in ("B1", "B3")) else all(
            out_come[b]["ok"] for b in ("B1", "B3")),
        "A8_db": None if (not rdss or rds_uncovered or out["rds"]["data_gap"] or None in out["rds"]["role_sessions_max"].values()
                          or out["rds"]["airflow_db_error_logs"] is None) else (
            not out["rds"]["any_stop"] and not out["rds"]["business_failed_sfn"]
            and not out["rds"]["airflow_db_error_logs"]
            and all(x <= 10 for x in out["rds"]["role_sessions_max"].values())),
        "A9_no_growth": None if host is None or None in (host["idle_task_anon_mib"].get("after_B1"),
                                                         host["idle_task_anon_mib"].get("after_B3")) else (
            host["idle_task_anon_mib"]["after_B3"] <= host["idle_task_anon_mib"]["after_B1"] * 1.10
            and host["idle_mem_available_mib"]["after_B3"] >= host["idle_mem_available_mib"]["after_B1"] * 0.90),
        "A10_ui": None if len(burst) != 30 else (not out["burst"]["non_2xx"] and out["burst"]["p95_s"] <= 2),
    }
    del lim, placement
    (d / "verdict.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return out


if __name__ == "__main__":
    for e in sys.argv[1:]:
        v = analyze(e)
        print(json.dumps({"exp": e, "pass": v["pass"], "host": v["host"] and {k: v["host"][k] for k in (
            "mem_total_mib", "mem_available_min_mib", "swap_total_mib", "swap_used_max_mib", "task_oom_kill",
            "task_mem_max_mib", "component_max_mib")}, "rds": v["rds"]}, ensure_ascii=False, indent=1, default=str))
