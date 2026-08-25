#!/usr/bin/env python3
"""publication-api 캐시 실험 Phase B 결과 집계.

results/ 아래 run 디렉터리(meta.json·summary.json·prom/*.json)를 읽어
(suite, mode, instances) 구성별로 반복 run 의 중앙값과 min~max 편차를 낸다.
본 측정(rate=1600, duration=3m) run 만 집계하고 스윕 run 은 건너뛴다.

사용법:
    python3 summarize.py                 # markdown 표를 stdout 으로
    python3 summarize.py --out cmp.md    # 파일로도 저장
    python3 summarize.py --results DIR   # 결과 디렉터리 지정
"""

import argparse
import json
import os
import sys
from statistics import median

PHASE_B_RATE = 1600
PHASE_B_DURATION = "3m"

# 부하가 실제로 건드리는 캐시 키 수: hot 1 종 + lib.js 기본 cold 2 종.
# (run-matrix 가 COLD_TICKERS 를 k6 에 넘기지 않아 기본값이 쓰인다.)
CACHE_KEYS = 3


def parse_duration(text):
    """'3m'/'30s' 같은 k6 기간 표기를 초로 바꾼다."""
    text = (text or "").strip()
    if text.endswith("ms"):
        return float(text[:-2]) / 1000.0
    if text.endswith("s"):
        return float(text[:-1])
    if text.endswith("m"):
        return float(text[:-1]) * 60
    if text.endswith("h"):
        return float(text[:-1]) * 3600
    return float(text or 0)


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def prom_series(run_dir, name):
    path = os.path.join(run_dir, "prom", name + ".json")
    if not os.path.exists(path):
        return []
    try:
        return load_json(path)["data"]["result"]
    except Exception:
        return []


def window_values(series, t0, t1):
    """[t0, t1] 창 안의 (ts, value) 만 남긴다."""
    out = []
    for ts, raw in series.get("values", []):
        ts = float(ts)
        if t0 <= ts <= t1:
            try:
                out.append((ts, float(raw)))
            except ValueError:
                pass
    return out


def summed_mean(result, t0, t1):
    """타임스탬프별로 전 시계열을 합친 뒤 시간평균."""
    per_ts = {}
    for series in result:
        for ts, val in window_values(series, t0, t1):
            per_ts[ts] = per_ts.get(ts, 0.0) + val
    if not per_ts:
        return None
    return sum(per_ts.values()) / len(per_ts)


def mean_by_label(result, label, t0, t1):
    """라벨 값별 시간평균 딕셔너리."""
    buckets = {}
    for series in result:
        key = series.get("metric", {}).get(label, "")
        vals = [v for _, v in window_values(series, t0, t1)]
        if vals:
            buckets.setdefault(key, []).append(sum(vals) / len(vals))
    return {k: sum(v) for k, v in buckets.items()}


def series_max(result, t0, t1):
    vals = []
    for series in result:
        vals.extend(v for _, v in window_values(series, t0, t1))
    return max(vals) if vals else None


def theoretical_loaders(meta, measure_s, instances):
    """이론상 DB loader 수: 키 수 × (측정시간 / 유효 TTL) × 캐시 사본 수.

    L1(caffeine)은 인스턴스마다 사본이 따로라 인스턴스 수만큼 곱하고,
    L2(redis·two-level)는 클러스터가 공유하므로 1 배다.
    캐시가 없는 none 모드는 이론치가 정의되지 않는다.
    """
    mode = meta.get("mode")
    cache = meta.get("cache", {})
    if mode == "caffeine":
        ttl_s, copies = parse_duration(cache.get("ttl")), instances
    elif mode in ("redis", "two-level"):
        ttl_s, copies = parse_duration(cache.get("l2_ttl")), 1
    else:
        return None
    if not ttl_s:
        return None
    return CACHE_KEYS * (measure_s / ttl_s) * copies


def collect_run(run_dir):
    """run 하나에서 지표를 뽑는다. 실패하면 예외를 올린다."""
    meta = load_json(os.path.join(run_dir, "meta.json"))
    metrics = load_json(os.path.join(run_dir, "summary.json"))["metrics"]

    measure_s = parse_duration(meta.get("duration"))
    warmup_s = parse_duration(meta.get("warmup"))
    t0 = float(meta["start_epoch"]) + warmup_s
    t1 = float(meta["end_epoch"])

    checks = metrics.get("checks{phase:measure}", {})
    measure_reqs = checks.get("passes", 0) + checks.get("fails", 0)
    dur = metrics.get("http_req_duration{phase:measure}", {})
    failed = metrics.get("http_req_failed{phase:measure}", {})

    db_mean = summed_mean(prom_series(run_dir, "db_loads"), t0, t1)
    l1 = mean_by_label(prom_series(run_dir, "l1_gets"), "result", t0, t1)
    l2 = mean_by_label(prom_series(run_dir, "l2_gets"), "result", t0, t1)
    l1_total = sum(l1.values())
    l2_total = sum(l2.values())

    instances = meta.get("instances", 1)
    loaders_total = db_mean * measure_s if db_mean is not None else None
    theoretical = theoretical_loaders(meta, measure_s, instances)

    return {
        "run_id": meta.get("run_id", os.path.basename(run_dir)),
        "suite": meta.get("suite"),
        "mode": meta.get("mode"),
        "instances": instances,
        "rate": meta.get("rate"),
        "duration": meta.get("duration"),
        "measure_s": measure_s,
        "measure_reqs": measure_reqs,
        "actual_rps": measure_reqs / measure_s if measure_s else None,
        "p95": dur.get("p(95)"),
        "p99": dur.get("p(99)"),
        "error_rate": failed.get("value"),
        # k6 는 드롭이 0 이면 카운터 자체를 내보내지 않는다.
        "dropped": meta.get("dropped_iterations") or 0,
        "status_200": metrics.get("status_200", {}).get("count", 0),
        # 구 키 폴백 — ADR-0054 전 정본 런의 raw 는 status_204 로 기록돼 있다(재처리 호환).
        "status_200_noresult": metrics.get(
            "status_200_noresult", metrics.get("status_204", {})
        ).get("count", 0),
        "l1_hit_ratio": (l1.get("hit", 0.0) / l1_total) if l1_total else None,
        "l2_ops": l2_total if l2_total else None,
        "l2_hit_ratio": (l2.get("hit", 0.0) / l2_total) if l2_total else None,
        "db_loads_rate": db_mean,
        "loaders_total": loaders_total,
        "loaders_per_req": (loaders_total / measure_reqs)
        if loaders_total is not None and measure_reqs
        else None,
        "loaders_vs_theory": (loaders_total / theoretical)
        if loaders_total is not None and theoretical
        else None,
        "theoretical_loaders": theoretical,
        "pg_xact_rate": summed_mean(prom_series(run_dir, "pg_xact"), t0, t1),
        "hikari_pending_max": series_max(prom_series(run_dir, "hikari_pending"), t0, t1),
    }


def is_phase_b(run):
    return run["rate"] == PHASE_B_RATE and run["duration"] == PHASE_B_DURATION


def agg(runs, key):
    vals = [r[key] for r in runs if r.get(key) is not None]
    if not vals:
        return None
    return (median(vals), min(vals), max(vals))


def fmt(stat, digits=1, scale=1.0, unit=""):
    if stat is None:
        return "-"
    med, lo, hi = (v * scale for v in stat)
    body = f"{med:,.{digits}f}"
    if abs(hi - lo) > 0:
        body += f" ({lo:,.{digits}f}~{hi:,.{digits}f})"
    return body + unit


def build_markdown(groups):
    lines = []
    lines.append("| 모드 | API 수 | 목표/실제 RPS | p95 (ms) | p99 (ms) | 오류율 | L1 hit | L2 ops/s | DB loader 총수(3m) | dropped |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for (suite, mode, inst), runs in groups:
        rps = agg(runs, "actual_rps")
        lines.append(
            "| {label} | {inst} | {target} / {rps} | {p95} | {p99} | {err} | {l1} | {l2} | {loaders} | {drop} |".format(
                label=f"{suite} ({mode})",
                inst=inst,
                target=runs[0]["rate"],
                rps=fmt(rps, 0),
                p95=fmt(agg(runs, "p95"), 1),
                p99=fmt(agg(runs, "p99"), 1),
                err=fmt(agg(runs, "error_rate"), 2, 100.0, "%"),
                l1=fmt(agg(runs, "l1_hit_ratio"), 2, 100.0, "%"),
                l2=fmt(agg(runs, "l2_ops"), 0),
                loaders=fmt(agg(runs, "loaders_total"), 0),
                drop=fmt(agg(runs, "dropped"), 0),
            )
        )
    return "\n".join(lines)


def build_detail(groups):
    lines = ["", "### 판정 보조", "", "| 구성 | 반복 | result / no result | loader/요청 | loader/이론치 배율 | 이론치(3m) | pg xact/s | hikari pending 최대 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for (suite, mode, inst), runs in groups:
        theory = agg(runs, "theoretical_loaders")
        lines.append(
            "| {label} {inst}대 | {n} | {s200} / {s204} | {lpr} | {mult} | {theory} | {xact} | {pend} |".format(
                label=f"{suite} ({mode})",
                inst=inst,
                n=len(runs),
                s200=fmt(agg(runs, "status_200"), 0),
                s204=fmt(agg(runs, "status_200_noresult"), 0),
                lpr=fmt(agg(runs, "loaders_per_req"), 4),
                mult=fmt(agg(runs, "loaders_vs_theory"), 2, 1.0, "x"),
                theory=fmt(theory, 0),
                xact=fmt(agg(runs, "pg_xact_rate"), 0),
                pend=fmt(agg(runs, "hikari_pending_max"), 0),
            )
        )
    lines.append("")
    lines.append(
        f"주: r1 은 컨테이너 재기동 직후 콜드 run 이라 지연·드롭이 부풀어 있다 "
        f"(중앙값은 r2·r3 를 대표). 이론치 분모는 캐시 키 {CACHE_KEYS} 종 "
        f"× 측정시간/유효 TTL × 사본 수(L1=인스턴스 수, L2=공유 1)."
    )
    return "\n".join(lines)


def main():
    default_results = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "results"
    )
    parser = argparse.ArgumentParser(description="Phase B 결과 집계")
    parser.add_argument("--results", default=os.path.normpath(default_results))
    parser.add_argument("--format", default="md", choices=["md"])
    parser.add_argument("--out")
    args = parser.parse_args()

    runs = []
    for name in sorted(os.listdir(args.results)):
        run_dir = os.path.join(args.results, name)
        if not os.path.isdir(run_dir):
            continue
        try:
            run = collect_run(run_dir)
        except Exception as exc:
            print(f"warn: {name} 건너뜀 ({exc})", file=sys.stderr)
            continue
        if not is_phase_b(run):
            continue
        # 요청 0건 = k6 가 시작도 못 한 run — 집계하면 구성 중앙값을 오염시킨다.
        if not run["measure_reqs"]:
            print(f"warn: {name} 건너뜀 (측정 요청 0건)", file=sys.stderr)
            continue
        runs.append(run)

    groups = {}
    for run in runs:
        groups.setdefault((run["suite"], run["mode"], run["instances"]), []).append(run)
    ordered = sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][0]))

    text = build_markdown(ordered) + "\n" + build_detail(ordered) + "\n"
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
