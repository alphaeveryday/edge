"""micro·small 검증 판정기 — results/micro/<실험>/ 의 원자료를 criteria.json 기준으로 판정한다(ALPHA-1119).

    python3 micro/analyze.py E1 [E2 ...]      # 실험별 요약 + 기준별 PASS/FAIL, results/micro/<실험>/verdict.json
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "results" / "micro"
DAG = "edge_investor_intraday"
STEPS = ("plan", "collect", "normalize", "load", "report", "verdict")
BUSINESS = {"collect": "ingest-raw-investor-estimate", "normalize": "normalize-investor-estimate",
            "load": "load-investor-intraday"}
MiB = 2 ** 20


def ts(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return datetime.fromisoformat(str(v).replace(" ", "T")).timestamp()


def p95(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(0.95 * (len(xs) - 1) + 0.999))], 1) if xs else None


def load(exp: str):
    d = ROOT / exp
    samples = [json.loads(line) for line in (d / "samples.jsonl").read_text().splitlines() if line.strip()]
    marks = [json.loads(line) for line in (d / "marks.jsonl").read_text().splitlines() if line.strip()]
    batches = {b: json.loads((d / f"{b}.json").read_text()) for b in ("B1", "B2", "B3") if (d / f"{b}.json").exists()}
    log = (d / "airflow.log").read_text() if (d / "airflow.log").exists() else ""
    return samples, marks, batches, log


def windows(marks, begin_event, end_event, key=None):
    out, open_ = [], {}
    for m in marks:
        k = m.get(key) if key else None
        if m["event"] == begin_event:
            open_[k] = m["t"]
        elif m["event"] == end_event and k in open_:
            out.append((k, open_.pop(k), m["t"]))
    return out


def restart_windows(marks):
    """주입한 재시작의 영향 창 — 시작 10초 전부터 끝난 뒤 180초(재기동·heartbeat 회복)."""
    return [(b - 10, e + 180) for _, b, e in windows(marks, "restart_airflow_begin", "restart_airflow_end")]


def in_any(t, spans):
    return any(a <= t <= b for a, b in spans)


def mem_block(samples, t0=None, t1=None):
    rows = [s for s in samples if "memory.current" in s and (t0 is None or s["t"] >= t0) and (t1 is None or s["t"] <= t1)]
    if not rows:
        return {}
    comp: dict[str, int] = {}
    for s in rows:
        for k, v in s["procs"].items():
            comp[k] = max(comp.get(k, 0), v["pss_kb"] // 1024)
    return {"max_current_mib": max(s["memory.current"] for s in rows) // MiB,
            "max_anon_mib": max(s["memory.stat"]["anon"] for s in rows) // MiB,
            "mean_anon_mib": int(statistics.mean(s["memory.stat"]["anon"] for s in rows) // MiB),
            "max_pss_by_component_mib": comp,
            "max_total_pss_mib": max(sum(v["pss_kb"] for v in s["procs"].values()) for s in rows) // 1024}


def cpu_block(samples, t0, t1):
    rows = [s for s in samples if "cpu.stat" in s and t0 <= s["t"] <= t1]
    if len(rows) < 2:
        return {}
    a, b = rows[0]["cpu.stat"], rows[-1]["cpu.stat"]
    secs = rows[-1]["t"] - rows[0]["t"]
    periods = b.get("nr_periods", 0) - a.get("nr_periods", 0)
    return {"avg_cores": round((b["usage_usec"] - a["usage_usec"]) / 1e6 / secs, 3),
            "throttled_periods_ratio": round((b.get("nr_throttled", 0) - a.get("nr_throttled", 0)) / periods, 4)
            if periods else 0.0,
            "throttled_s": round((b.get("throttled_usec", 0) - a.get("throttled_usec", 0)) / 1e6, 2)}


def conns(s):
    c = s.get("db", {}).get("conns") or []
    return {"total": sum(n for app, _, n in c if app != "observer"),
            "active": sum(n for app, st, n in c if app != "observer" and st == "active"),
            "by_app": {app: sum(n for a, _, n in c if a == app) for app, _, _ in c if app != "observer"}}


def latency(batch):
    tis = batch["task_instances"] or []
    ecs = batch["snapshot"]["fake_aws"]["tasks"]
    by_try = {}
    for t in ecs:
        ref = (t.get("env") or {}).get("OPS_ORCHESTRATOR_ATTEMPT_REF", "")
        m = re.match(rf"airflow:{DAG}/(.+)/(\w+)/(\d+)$", ref)
        if m:
            by_try.setdefault((m[1], m[2], int(m[3])), []).append(t)
    runs: dict[str, list] = {}
    for ti in tis:
        runs.setdefault(ti["run_id"], []).append(ti)
    gap, queue, submit, detect = [], [], [], []
    for rid, rows in runs.items():
        final = {r["task_id"]: r for r in rows if r["src"] == "ti"}
        for i, step in enumerate(STEPS):
            r = final.get(step)
            if not r or ts(r["queued_dttm"]) is None or ts(r["start_date"]) is None:
                continue
            if r["try_number"] == 1:
                queue.append(ts(r["start_date"]) - ts(r["queued_dttm"]))
                prev = final.get(STEPS[i - 1]) if i else None
                if prev and ts(prev.get("end_date")):
                    gap.append(ts(r["queued_dttm"]) - ts(prev["end_date"]))
        for r in rows:
            for t in by_try.get((rid, r["task_id"], r["try_number"]), []):
                if ts(r["start_date"]) and t.get("createdAt"):
                    submit.append(ts(t["createdAt"]) - ts(r["start_date"]))
                if r["src"] == "ti" and ts(r["end_date"]) and t.get("stoppedAt") and r["state"] in ("success", "failed"):
                    detect.append(ts(r["end_date"]) - ts(t["stoppedAt"]))
    return {"prev_end_to_queued_p95": p95(gap), "queued_to_start_p95": p95(queue),
            "start_to_ecs_created_p95": p95(submit), "ecs_stopped_to_end_p95": p95(detect),
            "n": {"gap": len(gap), "queue": len(queue), "submit": len(submit), "detect": len(detect)}}


def ti_state(batch, rid_suffix):
    rows = [t for t in batch["task_instances"] or [] if t["run_id"].endswith(rid_suffix) and t["src"] == "ti"]
    return {t["task_id"]: (t["state"], t["try_number"]) for t in rows}


def run_state(batch, rid_suffix):
    return next((r["state"] for r in batch["dag_runs"] or [] if r["run_id"].endswith(rid_suffix)), None)


def business(batch, step_task, pipeline_hhmm):
    step = BUSINESS[step_task]
    return len([r for r in batch["business_runs"] if r.get("step") == step and pipeline_hhmm(r)])


def ecs_count(batch, step_task, hhmm_run_id):
    step = BUSINESS[step_task]
    return len([t for t in batch["snapshot"]["fake_aws"]["tasks"] if t["command"][:1] == [step]
                and hhmm_run_id in t["command"]])


def outcomes(batches, marks=()):
    import hashlib

    def pid(hhmm):   # edge_batch.pipeline_run_id 와 같은 재료
        material = "\x01".join(["alphamale-etf-daily-v1", f"investor-intraday:2026-09-22T{hhmm}"])
        return f"run_{hashlib.sha256(material.encode()).hexdigest()[:26]}"
    checks = {}
    slots = ["09:35", "10:05", "11:25", "13:25", "14:35"]
    for b in ("B1", "B3"):
        if b not in batches:
            checks[b] = "missing"
            continue
        d = batches[b]
        ok = all(run_state(d, f"2026-09-22T{h}") == "success" for h in slots)
        dup = {s: [business(d, s, lambda r, h=h: r.get("run_id") == pid(h)) for h in slots] for s in BUSINESS}
        canon = d["snapshot"]["canonical"]
        ref = d["snapshot"]["dev_canonical"]
        # 재생한 거래일의 dev 파티션이 결과에 **있고** 같아야 한다 — 결과 쪽 키만 돌면 빠진 파티션을 못 본다.
        want = [k for k in ref if "2026-09-22" in k]
        canon_ok = bool(want) and all(k in canon and canon[k]["rows_sha"] == ref[k]["rows_sha"] for k in want)
        checks[b] = {"all_success": ok, "business_per_slot": dup,
                     "no_duplicate": all(n == 1 for ns in dup.values() for n in ns), "canonical_matches_dev": canon_ok}
    if "B2" in batches:
        d = batches["B2"]
        s = lambda h: ti_state(d, f"2026-09-22T{h}")
        holds = d["holds"] or []
        led = {r["run_key"][-5:]: r for r in d["ledger"] or []}
        checks["B2"] = {
            "0935_load_failed_no_retry": run_state(d, "T09:35") == "failed" and s("09:35").get("load") == ("failed", 1)
            and (led.get("09:35") or {}).get("orchestration_status") == "FAILED",
            "1005_collect_retried_once": run_state(d, "T10:05") == "success" and s("10:05").get("collect", (0, 0))[1] == 2
            and ecs_count(d, "collect", pid("10:05")) == 2 and business(d, "collect", lambda r: r.get("run_id") == pid("10:05")) == 1,
            "1125_hold_recorded": run_state(d, "T11:25") == "failed" and (s("11:25").get("normalize") or ("",))[0] == "failed"
            and business(d, "load", lambda r: r.get("run_id") == pid("11:25")) == 0
            and any(h.get("kind") == "RESULT_UNKNOWN" and pid("11:25") in (h.get("dedupe_key") or "") for h in holds),
            # 재시작이 실제로 그 run 의 정제 대기 중에 주입됐어야 한다(주입이 빠진 run 의 성공은 복구 검증이 아니다).
            "1325_restart_recovered_no_dup": any(m["event"] == "restart_airflow_begin" and m.get("arn") for m in marks)
            and run_state(d, "T13:25") == "success"
            and business(d, "normalize", lambda r: r.get("run_id") == pid("13:25")) == 1
            and ecs_count(d, "normalize", pid("13:25")) == 1,
            "1435_after_restart_success": run_state(d, "T14:35") == "success",
            "detail": {h: s(h) for h in slots}, "holds": holds,
        }
    return checks


def analyze(exp: str) -> dict:
    samples, marks, batches, log = load(exp)
    crit = json.loads((Path(__file__).resolve().parent / "criteria.json").read_text())
    rw = restart_windows(marks)
    start_t = next(m["t"] for m in marks if m["event"] == "start_airflow")
    healthy = next((m for m in marks if m["event"] in ("healthy", "not_healthy")), {})
    after = [s for s in samples if s["t"] >= start_t + 60 and not in_any(s["t"], rw)]
    last = [s for s in samples if "memory.events" in s][-1] if any("memory.events" in s for s in samples) else {}
    # 두 구성요소가 **모두** 보여야 한다 — 한쪽 job 이 사라진 샘플을 다른 쪽 값으로 통과시키지 않는다(빠지면 999).
    hb = [max((s.get("db", {}).get("heartbeat_age_s") or {}).get(k, 999) for k in ("SchedulerJob", "DagProcessorJob"))
          for s in after if s.get("db")]
    parse = [v[0] for s in after if s.get("db") for v in (s["db"].get("dag_parse_age_s") or {}).values() if v[0] is not None]
    imports = [s["db"].get("import_errors") for s in after if s.get("db")]
    touch = next((m["t"] for m in marks if m["event"] == "touch_dags"), None)
    reparse = None
    if touch:
        for s in samples:
            ages = [v[0] for v in (s.get("db", {}).get("dag_parse_age_s") or {}).values() if v[0] is not None]
            if s["t"] > touch and ages and max(ages) < s["t"] - touch:
                reparse = round(s["t"] - touch, 1)
                break
    burst = next((m["results"] for m in marks if m["event"] == "burst"), [])
    idle = {k: (a, b) for k, a, b in windows(marks, "idle_begin", "idle_end", key="tag")}
    tail = lambda tag: [s for s in samples if tag in idle and idle[tag][1] - 60 <= s["t"] <= idle[tag][1]
                        and "memory.stat" in s]
    idle_mem = {tag: int(statistics.mean(s["memory.stat"]["anon"] for s in tail(tag)) // MiB) if tail(tag) else None
                for tag in idle}
    idle_conn = {tag: round(statistics.mean(conns(s)["total"] for s in tail(tag)), 1) if tail(tag) else None
                 for tag in idle}
    all_conns = [conns(s) for s in samples if s.get("db")]
    pool_errors = len(re.findall(r"QueuePool limit|TimeoutError|too many clients|OperationalError", log))
    db_errors = sum(1 for s in samples if s.get("db_error"))
    lat = {b: latency(d) for b, d in batches.items()}
    out = {
        "exp": exp,
        "limits": {k: last.get(k) for k in ("memory.max", "memory.swap.max", "cpu.max")},
        "healthy_after_s": round(healthy.get("t", start_t) - start_t, 1), "health": healthy.get("event"),
        "memory_events": last.get("memory.events"), "memory_peak_mib": (last.get("memory.peak") or 0) // MiB,
        "swap_current_max": max((s.get("memory.swap.current") or 0) for s in samples),
        "memory_all": mem_block(samples),
        "memory_by_phase": {tag: mem_block(samples, a, b) for tag, (a, b) in idle.items()},
        "memory_batches": {b: mem_block(samples, *next(((m1["t"], m2["t"]) for m1 in marks for m2 in marks
                                                         if m1["event"] == "reset" and m1.get("tag") == b
                                                         and m2["event"] == "dump" and m2.get("batch") == b), (0, 0)))
                           for b in batches},
        "cpu_idle_start": cpu_block(samples, *idle.get("start", (0, 0))),
        "cpu_all": cpu_block(samples, start_t, samples[-1]["t"]),
        "idle_anon_mib": idle_mem, "idle_conns": idle_conn,
        "conns_max_total": max((c["total"] for c in all_conns), default=None),
        "conns_max_active": max((c["active"] for c in all_conns), default=None),
        "conns_max_by_app": {app: max(c["by_app"].get(app, 0) for c in all_conns)
                             for app in {a for c in all_conns for a in c["by_app"]}},
        "heartbeat_age_max_s": max(hb) if hb else None, "parse_age_max_s": max(parse) if parse else None,
        "import_errors_max": max((i or 0) for i in imports) if imports else None, "reparse_after_touch_s": reparse,
        "burst": {"n": len(burst), "non_2xx": [b for b in burst if not 200 <= b[1] < 300],
                  "p95_s": p95([b[2] for b in burst])},
        "pool_errors_in_log": pool_errors, "latency": lat, "outcomes": outcomes(batches, marks),
        # 배치 덤프가 없는 중단 실험(E2·E4·E6)은 중단 기록의 재시작 수로 센다 — 덤프만 보면 재시작이 없어 보인다.
        "restart_count": {**{b: d.get("restart_count") for b, d in batches.items()},
                          **{"aborted": m["restart_count"] for m in marks if m["event"] == "suite_aborted"}},
        "suite_done": any(m["event"] == "suite_done" for m in marks),
        "code_unchanged": (lambda a, b: a is not None and a == b)(
            next((m["files"] for m in marks if m["event"] == "code_hash_start"), None),
            next((m["files"] for m in marks if m["event"] == "code_hash_end"), None)),
        "suite_error": next((m["error"] for m in marks if m["event"] == "suite_error"), None),
    }
    # Airflow 의 OOM: cgroup 경로(컨테이너 재시작마다 새로 생긴다)별 oom_kill 최대값의 합. memory.events 는 하위까지
    # 계층 누적이라 그 cgroup 안의 모든 프로세스를 센다. 부모(docker) 증가분은 다른 컨테이너(다른 세션의 lab 포함)가
    # 섞여 귀속할 수 없어 참고로만 남긴다(E3 에서 부모만 1 늘고 Airflow cgroup 은 0 이었다).
    per_cg: dict[str, int] = {}
    for s_ in samples:
        if "memory.events" in s_:
            per_cg[s_["cg"]] = max(per_cg.get(s_["cg"], 0), s_["memory.events"].get("oom_kill", 0))
    out["oom_kill_in_airflow"] = sum(per_cg.values())
    out["cgroups_seen"] = len(per_cg)
    parent = [s_["parent.memory.events"] for s_ in samples if "parent.memory.events" in s_]
    out["parent_oom_kill_delta_info"] = (parent[-1].get("oom_kill", 0) - parent[0].get("oom_kill", 0)) if parent else None
    out["db_errors_in_samples"] = db_errors
    # criteria C4 의 상대 기준: 작은 상한의 p95 가 2GiB 기준(E1)보다 10초 넘게 크지 않다.
    # 참조 실험이 무효(오염·중단)면 상대 기준은 판정 불가다 — 무효 실험을 기준값으로 쓰지 않는다.
    ref_path = ROOT / "E1" / "verdict.json"
    ref_v = json.loads(ref_path.read_text()) if ref_path.exists() and exp != "E1" else None
    out["latency_reference_valid"] = bool(ref_v and ref_v["pass"].get("C0_valid_run"))
    ref = ref_v["latency"] if out["latency_reference_valid"] else None
    out["latency_vs_E1_max_delta_s"] = None if ref is None else max(
        ((lat[b][k] or 0) - (ref.get(b, {}).get(k) or 0) for b in lat if b in ref for k in lat[b] if k != "n"),
        default=None)
    ev = out["memory_events"] or {}
    oc = out["outcomes"]
    b2 = oc.get("B2") if isinstance(oc.get("B2"), dict) else {}
    lat_all = [v for b in lat.values() for k, v in b.items() if k != "n"]
    out["pass"] = {
        "C0_valid_run": out["suite_done"] and out["code_unchanged"] and not out["suite_error"],
        "C1_start": out["health"] == "healthy" and out["healthy_after_s"] <= 300 and ev.get("oom", 1) == 0
        and ev.get("oom_kill", 1) == 0 and out["oom_kill_in_airflow"] == 0
        and all((v or 0) == 0 for v in out["restart_count"].values()),
        "C2_parse": out["import_errors_max"] == 0 and out["parse_age_max_s"] is not None and out["parse_age_max_s"] <= 360
        and reparse is not None and reparse <= 90,
        "C3_heartbeat": out["heartbeat_age_max_s"] is not None and out["heartbeat_age_max_s"] <= 30,
        "C4_latency_abs": bool(lat_all) and all(v is not None for v in lat_all) and all(
            (b["prev_end_to_queued_p95"] or 0) <= 15 and (b["queued_to_start_p95"] or 0) <= 15
            and (b["start_to_ecs_created_p95"] or 0) <= 20 and (b["ecs_stopped_to_end_p95"] or 0) <= 20
            for b in lat.values()),
        # 참조(E1) 자신만 비교 없이 통과. 지연 자료가 없는 실험(중단)은 판정 불가 = 통과 아님.
        # None = 판정 불가(참조 무효). 통과로 세지 않는다.
        "C4_latency_rel": True if exp == "E1" else (None if not out["latency_reference_valid"] else (
            out["latency_vs_E1_max_delta_s"] is not None and out["latency_vs_E1_max_delta_s"] <= 10)),
        "C5_outcomes": bool(b2) and all(v for k, v in b2.items() if k not in ("detail", "holds"))
        and all(isinstance(oc.get(b), dict) and oc[b]["all_success"] for b in ("B1", "B3")),
        "C6_business": all(isinstance(oc.get(b), dict) and oc[b]["no_duplicate"] and oc[b]["canonical_matches_dev"]
                           for b in ("B1", "B3")),
        # 로그가 없으면 "오류 없음"이 아니라 판정 불가다. 관측기의 메타DB 접속 실패도 본다.
        "C7_db": bool(log.strip()) and pool_errors == 0 and db_errors == 0,
        "C8_no_growth": None not in (idle_mem.get("after_B1"), idle_mem.get("after_B3"))
        and idle_mem["after_B3"] <= idle_mem["after_B1"] * 1.10
        and abs((idle_conn.get("after_B3") or 0) - (idle_conn.get("after_B1") or 0)) <= 1,
        "C9_ui": len(burst) == 30 and not out["burst"]["non_2xx"] and out["burst"]["p95_s"] is not None
        and out["burst"]["p95_s"] <= 2,
    }
    del crit
    (ROOT / exp / "verdict.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    for exp in sys.argv[1:]:
        v = analyze(exp)
        print(json.dumps({k: v[k] for k in ("exp", "limits", "pass", "memory_all", "memory_events", "idle_anon_mib",
                                            "conns_max_total", "conns_max_by_app", "heartbeat_age_max_s", "latency",
                                            "suite_error")}, ensure_ascii=False, indent=1))
