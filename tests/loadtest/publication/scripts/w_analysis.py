"""W(워킹셋 스윕)·S(핫키 스파이크) 일회성 판독. summarize.py 와 무관.

사용:
  python3 w_analysis.py sweep [--results DIR]
  python3 w_analysis.py spike [--results DIR]

W 는 meta.working_set 의 존재로, S 는 meta.suite == "S" 로 식별한다.
"""
import json
import os
import sys
from statistics import median

DEFAULT_RESULTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")


# ── 공통 유틸 (e_analysis.py 와 같은 규약) ──────────────────────────────────
def load(results, d, name):
    p = os.path.join(results, d, "prom", name + ".json")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return json.load(f)["data"]["result"]


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


def window_median(series, t0, t1):
    vals = [v for t, v in series if t0 <= t <= t1]
    return median(vals) if vals else 0.0


def to_seconds(raw):
    """k6 기간 표기("3s","500ms","3m")를 초로. run-matrix 의 to_seconds 와 같은 규칙."""
    if not raw:
        return 0.0
    s = str(raw).strip()
    for unit, mul in (("ms", 0.001), ("s", 1.0), ("m", 60.0), ("h", 3600.0)):
        if s.endswith(unit):
            try:
                return float(s[:-len(unit)]) * mul
            except ValueError:
                return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def all_runs(results):
    out = []
    for name in sorted(os.listdir(results)):
        p = os.path.join(results, name, "meta.json")
        if not os.path.exists(p):
            continue
        with open(p) as f:
            out.append((name, json.load(f)))
    return out


def summary_metrics(results, name):
    p = os.path.join(results, name, "summary.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f).get("metrics", {})


def measure_duration(metrics, prefer="measure"):
    """관심 구간의 지연 분포. 시나리오마다 태그가 다르다 — W 는 phase:measure,
    S 는 phase:spike 가 판정 구간이다(hot-spike.js 의 threshold 와 같은 태그)."""
    return metrics.get("http_req_duration{phase:%s}" % prefer,
                       metrics.get("http_req_duration{phase:measure}",
                                   metrics.get("http_req_duration", {})))


def fmt(v, digits=1):
    if v is None:
        return "-"
    return ("{:,.%df}" % digits).format(v)


# ── sweep: 워킹셋 격자 ───────────────────────────────────────────────────────
def collect_sweep(results):
    """(mode, working_set) -> [run 지표 dict] 격자."""
    grid = {}
    for name, m in all_runs(results):
        if "working_set" not in m:
            continue
        # 본 측정(rate 1600)만 집계 — 배선 검증용 스모크(rate 200 등)가 같은 N 의
        # 중앙값을 오염시키는 것을 막는다(실측: n100 스모크 혼입으로 hit 69% 오판).
        if float(m.get("rate") or 0) != 1600:
            continue
        t0 = float(m["start_epoch"]) + to_seconds(m.get("warmup"))
        t1 = float(m["end_epoch"])
        l1 = load(results, name, "l1_gets")
        l1h = integral(summed(l1, "result", "hit"), t0, t1)
        l1m = integral(summed(l1, "result", "miss"), t0, t1)
        l2 = load(results, name, "l2_gets")
        met = summary_metrics(results, name)
        dur = measure_duration(met)
        ws = int(m["working_set"])
        inst = int(m.get("instances") or 4)
        rate = float(m.get("rate") or 0)
        ttl = to_seconds(m.get("cache", {}).get("ttl"))
        span = max(t1 - t0, 0.0)

        # 단순 모델 — 어디까지나 대략치다. "키 하나당 TTL 마다 인스턴스 수만큼 미스가
        # 난다"는 가정(요청이 키에 고르게 퍼지고, 만료가 겹치지 않음)만 깔았다.
        # 실제로는 도착 편차·동시 만료·L2 공유가 이 값을 위아래로 흔든다.
        supply = (ws * inst / ttl) if ttl > 0 else 0.0   # 초당 필연 미스 수
        pred_hit = max(0.0, 1.0 - (ws * inst) / (rate * ttl)) if rate > 0 and ttl > 0 else None
        reqs = rate * span
        pred_loads = min(reqs, supply * span) if supply else None

        grid.setdefault((m["mode"], ws), []).append({
            "run": name,
            "rate": rate,
            "ttl": ttl,
            "instances": inst,
            "hit_ratio": (l1h / (l1h + l1m)) if (l1h + l1m) > 0 else None,
            "l1_miss": l1m,
            "db_loads": integral(summed(load(results, name, "db_loads")), t0, t1),
            "l2_hit": integral(summed(l2, "result", "hit"), t0, t1),
            "l2_miss": integral(summed(l2, "result", "miss"), t0, t1),
            "p99": dur.get("p(99)"),
            "dropped": m.get("dropped_iterations") or 0,
            "pred_hit": pred_hit,
            "pred_loads": pred_loads,
        })
    return grid


def med(rows, key, digits=1):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return fmt(median(vals), digits) if vals else "-"


def med_raw(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return median(vals) if vals else None


def knee(points, threshold):
    """hit ratio 가 threshold 를 하향 돌파하는 워킹셋 크기를 선형 보간."""
    pts = sorted((n, h) for n, h in points if h is not None)
    for (na, ha), (nb, hb) in zip(pts, pts[1:]):
        if ha >= threshold > hb:
            if ha == hb:
                return nb
            return na + (ha - threshold) / (ha - hb) * (nb - na)
    return None


def sweep(results):
    grid = collect_sweep(results)
    if not grid:
        print("W run 이 없다 (meta.working_set 을 가진 결과 디렉터리 없음): " + results)
        return

    modes = []
    for mode, _ws in grid:
        if mode not in modes:
            modes.append(mode)

    print("## W 워킹셋 스윕 — L1 적중이 무너지는 지점\n")
    print("측정창 = [start + warmup, end]. 반복이 있으면 중앙값.")
    print("cache eviction 은 collect-result.sh 의 쿼리 목록에 없어 집계에서 뺐다 "
          "— 축출은 L1 miss·db_loads 증가로 간접 관측한다.\n")

    for mode in sorted(modes):
        rows_by_ws = {ws: rows for (m, ws), rows in grid.items() if m == mode}
        print("### mode = %s\n" % mode)
        print("| 워킹셋 | 반복 | L1 hit | 모델 예측 hit | L1 miss | DB loader | 모델 예측 loader "
              "| L2 hit | L2 miss | p99 (ms) | dropped |")
        print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for ws in sorted(rows_by_ws):
            rows = rows_by_ws[ws]
            hit = med_raw(rows, "hit_ratio")
            ph = med_raw(rows, "pred_hit")
            print("| {ws} | {rep} | {hit} | {ph} | {miss} | {db} | {pdb} | {l2h} | {l2m} | {p99} | {dr} |".format(
                ws=ws, rep=len(rows),
                hit=fmt(hit * 100, 1) + "%" if hit is not None else "-",
                ph=fmt(ph * 100, 1) + "%" if ph is not None else "-",
                miss=med(rows, "l1_miss", 0),
                db=med(rows, "db_loads", 0),
                pdb=med(rows, "pred_loads", 0),
                l2h=med(rows, "l2_hit", 0),
                l2m=med(rows, "l2_miss", 0),
                p99=med(rows, "p99", 2),
                dr=med(rows, "dropped", 0)))
        print()

        pts = [(ws, med_raw(rows, "hit_ratio")) for ws, rows in rows_by_ws.items()]
        for th in (0.9, 0.5):
            k = knee(pts, th)
            label = "hit %d%% 하향 돌파 워킹셋" % int(th * 100)
            print("  %s: %s" % (label, fmt(k, 0) if k is not None else "관측 구간 안에서 미돌파"))
        print()

    print("\n모델 주석 — 예측 hit ≈ max(0, 1 − 키수×인스턴스 / (도착률 × L1 TTL)),")
    print("예측 loader ≈ min(구간 요청수, 키수×인스턴스/TTL × 구간초). 둘 다 대략치다:")
    print("키가 고르게 도착하고 만료가 겹치지 않는다고 가정했고, L2 공유·스탬피드·")
    print("축출은 넣지 않았다. 실측이 모델보다 나쁘면 그 차이가 곧 축출·동시 만료 몫이다.")

    print("\nrun 별:")
    for (mode, ws) in sorted(grid, key=lambda k: (k[0], k[1])):
        for r in grid[(mode, ws)]:
            hr = r["hit_ratio"]
            print("  {m:10s} n={ws:<6d} {run} hit={hit} db_loads={db:.0f} "
                  "l1_miss={miss:.0f} p99={p99} drop={dr}".format(
                      m=mode, ws=ws, run=r["run"][-8:],
                      hit=(fmt(hr * 100, 1) + "%") if hr is not None else "-",
                      db=r["db_loads"], miss=r["l1_miss"],
                      p99=fmt(r["p99"], 2), dr=r["dropped"]))


# ── spike: 핫키 스파이크 ─────────────────────────────────────────────────────
BASE_FROM = 10   # 기동 직후 램프를 피해 baseline 창을 늦춰 연다
BASE_TO = 45     # hot-spike.js 의 baseline 스테이지는 60s — 그 안쪽만 쓴다


def detect_onset(arrival, t0, t1):
    """도착률이 baseline 의 2배를 처음 넘는 시점 = 스파이크 온셋.

    램프 시점(hot-spike.js 의 ONSET_AT=60s)은 시나리오가 쥐고 있고 meta 에 남지
    않는다. 상수를 여기 복제하면 시나리오만 고쳤을 때 조용히 어긋나므로 계측된
    도착률에서 역산한다 — baseline 은 t+10~45s 중앙값이다.
    """
    base = window_median(arrival, t0 + BASE_FROM, min(t0 + BASE_TO, t1))
    if base <= 0:
        return None, 0.0
    for t, v in arrival:
        if t > t0 + BASE_TO and v > base * 2:
            return t, base
    return None, base


def spike(results):
    runs = [(n, m) for n, m in all_runs(results) if m.get("suite") == "S"]
    if not runs:
        print("S run 이 없다: " + results)
        return

    print("## S 핫키 스파이크 — 도착률 급증이 loader 로 얼마나 새는가\n")
    print("phase 구분은 계측 도착률에서 역산한다(온셋 = baseline 도착률의 2배를 처음 "
          "넘는 시점). 시나리오의 램프 시점은 meta 에 남지 않는다.")
    print("W 와 달리 warmup 오프셋을 두지 않는다 — hot-spike 는 baseline 스테이지가 "
          "시나리오 안에 있어 실행 시작이 곧 baseline 시작이다.\n")

    print("| run | mode | baseline 도착 | 피크 도착 | 도착 배율 | baseline loader/s "
          "| 온셋 10s loader 총량 | 피크 loader/s | loader 배율 | p99 (ms) | dropped |")
    print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    detail = []
    for name, m in runs:
        t0 = float(m["start_epoch"])
        t1 = float(m["end_epoch"])
        arrival = summed(load(results, name, "api_rps_by_status"))
        db = summed(load(results, name, "db_loads"))
        met = summary_metrics(results, name)
        dur = measure_duration(met, prefer="spike")

        onset, base_arrival = detect_onset(arrival, t0, t1)
        base_db = window_median(db, t0 + BASE_FROM,
                                (onset - 5) if onset else min(t0 + BASE_TO, t1))
        peak_arrival = peak(arrival, onset or t0, t1)
        peak_db = peak(db, onset or t0, t1)
        onset_db = integral(db, onset, onset + 10) if onset else None

        print("| {run} | {mode} | {ba} | {pa} | {am} | {bd} | {od} | {pd} | {dm} | {p99} | {dr} |".format(
            run=name[-8:], mode=m.get("mode"),
            ba=fmt(base_arrival, 1), pa=fmt(peak_arrival, 1),
            am=(fmt(peak_arrival / base_arrival, 1) + "x") if base_arrival else "-",
            bd=fmt(base_db, 2), od=fmt(onset_db, 1) if onset_db is not None else "-",
            pd=fmt(peak_db, 2),
            dm=(fmt(peak_db / base_db, 1) + "x") if base_db else "-",
            p99=fmt(dur.get("p(99)"), 2), dr=m.get("dropped_iterations") or 0))
        detail.append((name, m, t0, t1, onset, arrival, db, base_arrival, base_db))

    print("\n판정 보조 — 도착률이 20배 뛸 때 loader 배율이 1에 가까우면 캐시가 스파이크를")
    print("흡수한 것이고(원본 부하는 키 수 × TTL 로 상한이 잡힌다), loader 배율이 도착 배율을")
    print("따라가면 스탬피드다. 온셋 10s 총량은 그 순간 DB 가 실제로 맞은 건수다.\n")

    for name, m, t0, t1, onset, arrival, db, base_arrival, base_db in detail:
        print("### %s (mode=%s, rate=%s)" % (name, m.get("mode"), m.get("rate")))
        if onset is None:
            print("  온셋 미검출 — 도착률이 baseline 2배를 넘지 않았다.\n")
            continue
        print("  온셋 t+%.0fs (측정창 기준), baseline 도착 %.1f/s, baseline loader %.2f/s"
              % (onset - t0, base_arrival, base_db))
        print("  phase | t(s) | 도착/s | db_loads/s")
        idx = dict(arrival)
        for t, v in db:
            if t < t0:
                continue
            a = idx.get(t, 0.0)
            if t < onset:
                phase = "baseline   "
            elif t <= onset + 10:
                phase = "spike-onset"
            elif a > base_arrival * 2:
                phase = "spike      "
            else:
                # 도착률이 baseline 수준으로 돌아온 뒤 = 감쇠. 여기 남는 loader 는
                # 스파이크가 밀어 넣은 키의 만료 잔향이다.
                phase = "cooldown   "
            print("  %s | %5.0f | %9.1f | %10.2f" % (phase, t - t0, a, v))
        print()


if __name__ == "__main__":
    args = sys.argv[1:]
    results_dir = DEFAULT_RESULTS
    if "--results" in args:
        i = args.index("--results")
        results_dir = args[i + 1]
        del args[i:i + 2]
    which = args[0] if args else "sweep"
    if which == "sweep":
        sweep(results_dir)
    elif which == "spike":
        spike(results_dir)
    else:
        print(__doc__)
        sys.exit(2)
