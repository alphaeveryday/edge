"""E1(콜드스타트)·E2(동시 만료) 시계열 일회성 판독. summarize.py 와 무관."""
import json
import os
import sys
from statistics import median

RESULTS = "/Users/choyoungseo/Developer/edge/tests/loadtest/publication/results"


def load(d, name):
    p = os.path.join(RESULTS, d, "prom", name + ".json")
    if not os.path.exists(p):
        return []
    return json.load(open(p))["data"]["result"]


def summed(result, label=None, want=None):
    """타임스탬프별 합산 시계열 -> [(ts, val)] 정렬."""
    per = {}
    for ser in result:
        if label and ser["metric"].get(label) != want:
            continue
        for ts, v in ser["values"]:
            per[float(ts)] = per.get(float(ts), 0.0) + float(v)
    return sorted(per.items())


def integral(series, t0, t1):
    """rate 시계열의 [t0,t1] 적분 = 구간 총 건수(사다리꼴)."""
    pts = [(t, v) for t, v in series if t0 <= t <= t1]
    if len(pts) < 2:
        return 0.0
    tot = 0.0
    for (ta, va), (tb, vb) in zip(pts, pts[1:]):
        tot += (va + vb) / 2 * (tb - ta)
    return tot


def peak(series, t0, t1):
    vals = [v for t, v in series if t0 <= t <= t1]
    return max(vals) if vals else 0.0


def runs_of(suite):
    out = []
    for name in sorted(os.listdir(RESULTS)):
        p = os.path.join(RESULTS, name, "meta.json")
        if not os.path.exists(p):
            continue
        m = json.load(open(p))
        if m.get("suite") == suite:
            out.append((name, m))
    return out


def stat(vals, digits=0):
    if not vals:
        return "-"
    f = f"{{:,.{digits}f}}"
    body = f.format(median(vals))
    if max(vals) - min(vals) > 0:
        body += " (" + f.format(min(vals)) + "~" + f.format(max(vals)) + ")"
    return body


def e1():
    by_mode = {}
    for name, m in runs_of("E1"):
        s0 = float(m["start_epoch"])
        s1 = float(m["end_epoch"])
        db = summed(load(name, "db_loads"))
        met = json.load(open(os.path.join(RESULTS, name, "summary.json")))["metrics"]
        dur = met.get("http_req_duration{phase:measure}", met.get("http_req_duration", {}))
        rec = {
            "run": name,
            "first30": integral(db, s0, s0 + 30),
            "peak30": peak(db, s0, s0 + 30),
            "total": integral(db, s0, s1),
            "steady": median([v for t, v in db if t >= s0 + 50] or [0]),
            "p99": dur.get("p(99)"),
            "p95": dur.get("p(95)"),
            "dropped": m.get("dropped_iterations") or 0,
            "reqs": met.get("http_reqs", {}).get("count"),
        }
        by_mode.setdefault(m["mode"], []).append(rec)

    print("## E1 콜드스타트 (4대, rate 1600, 90s, 워밍업 없음)\n")
    print("| 모드 | 첫 30s loader 총량 | 첫 30s 피크 rate | 90s 총 loader | 정상상태 rate | p99 (ms) | dropped |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for mode in ("caffeine", "redis", "two-level"):
        rs = by_mode.get(mode, [])
        if not rs:
            continue
        print("| {m} | {a} | {b} | {c} | {d} | {e} | {f} |".format(
            m=mode,
            a=stat([r["first30"] for r in rs]),
            b=stat([r["peak30"] for r in rs], 1),
            c=stat([r["total"] for r in rs]),
            d=stat([r["steady"] for r in rs], 1),
            e=stat([r["p99"] for r in rs], 2),
            f=stat([r["dropped"] for r in rs]),
        ))
    print()
    for mode in ("caffeine", "redis", "two-level"):
        for r in by_mode.get(mode, []):
            print(f"  {mode:10s} {r['run'][-6:]} first30={r['first30']:8.0f} peak={r['peak30']:7.1f} "
                  f"total={r['total']:8.0f} steady={r['steady']:5.1f} p95={r['p95']} p99={r['p99']} "
                  f"reqs={r['reqs']} dropped={r['dropped']}")
    print()
    # 스탬피드 판정: 첫 30s loader 가 요청 수 비례인지, 키×인스턴스 수준인지
    print("판정 보조 — 첫 30s 이론 하한(키 3 × 인스턴스, 1회 로딩)=12, "
          "L2 공유라면 3, 스탬피드라면 O(요청 수)")
    for mode in ("caffeine", "redis", "two-level"):
        for r in by_mode.get(mode, []):
            # 첫 30s 정상상태 기대분을 뺀 초과분 = 콜드 폭
            expected_steady = r["steady"] * 30
            print(f"  {mode:10s} {r['run'][-6:]} 초과 콜드분={r['first30']-expected_steady:8.0f} "
                  f"(정상상태분 {expected_steady:6.0f})")


def e2():
    print("\n\n## E2 동시 만료 (two-level, 4대)\n")
    for name, m in runs_of("E2"):
        s0 = float(m["start_epoch"])
        s1 = float(m["end_epoch"])
        db = summed(load(name, "db_loads"))
        l2h = summed(load(name, "l2_gets"), "result", "hit")
        l2m = summed(load(name, "l2_gets"), "result", "miss")
        l1m = summed(load(name, "l1_gets"), "result", "miss")
        met = json.load(open(os.path.join(RESULTS, name, "summary.json")))["metrics"]
        dur = met.get("http_req_duration{phase:measure}", {})
        pk = max(db, key=lambda kv: kv[1]) if db else (s0, 0)
        print(f"### {name}  span={s1-s0:.0f}s  p99={dur.get('p(99)')} dropped={m.get('dropped_iterations') or 0} "
              f"reqs={met.get('http_reqs',{}).get('count')}")
        print(f"  db_loads 피크 {pk[1]:.1f}/s @ t+{pk[0]-s0:.0f}s, 90s 총량 {integral(db,s0,s1):.0f}")
        print("  t(s) | db_loads/s | l1_miss/s | l2_miss/s | l2_hit/s")
        idx = {t: v for t, v in l1m}
        idx2 = {t: v for t, v in l2m}
        idx3 = {t: v for t, v in l2h}
        for t, v in db:
            print(f"  {t-s0:5.0f} | {v:10.1f} | {idx.get(t,0):9.1f} | {idx2.get(t,0):9.1f} | {idx3.get(t,0):8.1f}")
        print()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("e1", "both"):
        e1()
    if which in ("e2", "both"):
        e2()


def e2_e3():
    """E2(jitter 0) vs E3(jitter 2s) 대조. burst 시작 = warm 30s + gap(k6 TTL 기본 3s +1)."""
    from statistics import mean, pstdev
    rows = {}
    for suite in ("E2", "E3"):
        for name, m in runs_of(suite):
            s0, s1 = float(m["start_epoch"]), float(m["end_epoch"])
            bs = s0 + 34
            db = summed(load(name, "db_loads"))
            l1m = summed(load(name, "l1_gets"), "result", "miss")
            l2m = summed(load(name, "l2_gets"), "result", "miss")
            met = json.load(open(os.path.join(RESULTS, name, "summary.json")))["metrics"]
            dur = met.get("http_req_duration{phase:measure}", {})
            ck = met.get("checks{phase:measure}", {})
            burst_vals = [v for t, v in db if bs <= t <= s1]
            rows.setdefault(suite, []).append({
                "run": name[-6:],
                "jitter": m["cache"]["l2_jitter"],
                "onset_db": integral(db, bs, bs + 10),
                "onset_l1": integral(l1m, bs, bs + 10),
                "onset_l2": integral(l2m, bs, bs + 10),
                "burst_db": integral(db, bs, s1),
                "burst_l1": integral(l1m, bs, s1),
                "burst_l2": integral(l2m, bs, s1),
                "peak_db": peak(db, bs, s1),
                "peak_l1": peak(l1m, bs, s1),
                "cv": (pstdev(burst_vals) / mean(burst_vals)) if burst_vals and mean(burst_vals) else 0,
                "p99": dur.get("p(99)"),
                "p95": dur.get("p(95)"),
                "dropped": m.get("dropped_iterations") or 0,
                "burst_reqs": ck.get("passes", 0) + ck.get("fails", 0),
            })

    print("\n\n## E2(jitter 0) vs E3(jitter 2s) — two-level 4대, burst 60s @1600\n")
    print("| 지표 | E2 (jitter 0) | E3 (jitter 2s) |")
    print("| --- | ---: | ---: |")
    keys = [("burst_reqs", "버스트 요청 수", 0), ("onset_l1", "온셋 10s L1 miss", 1),
            ("onset_l2", "온셋 10s L2 miss", 1), ("onset_db", "온셋 10s DB loader", 1),
            ("burst_l1", "버스트 60s L1 miss", 0), ("burst_l2", "버스트 60s L2 miss", 0),
            ("burst_db", "버스트 60s DB loader", 0), ("peak_l1", "L1 miss 피크/s", 1),
            ("peak_db", "DB loader 피크/s", 2), ("cv", "DB loader 변동계수", 2),
            ("p95", "p95 (ms)", 2), ("p99", "p99 (ms)", 2), ("dropped", "dropped", 0)]
    for key, label, dg in keys:
        cells = []
        for suite in ("E2", "E3"):
            cells.append(stat([r[key] for r in rows[suite] if r[key] is not None], dg))
        print(f"| {label} | {cells[0]} | {cells[1]} |")
    print("\nrun 별:")
    for suite in ("E2", "E3"):
        for r in rows[suite]:
            print(f"  {suite} {r['run']} jitter={r['jitter']:>3s} onset(l1/l2/db)="
                  f"{r['onset_l1']:5.1f}/{r['onset_l2']:5.1f}/{r['onset_db']:5.1f}  "
                  f"burst(l1/l2/db)={r['burst_l1']:6.1f}/{r['burst_l2']:5.1f}/{r['burst_db']:5.1f}  "
                  f"peak_db={r['peak_db']:.2f} cv={r['cv']:.2f} p99={r['p99']} drop={r['dropped']}")
