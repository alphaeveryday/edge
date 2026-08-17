"""E6 재실행 판독 — raw.json.gz 에서 fresh/stale/blocked 전환 시각을 뽑는다. 일회성."""
import gzip
import json
import os
import sys
from datetime import datetime

RESULTS = "/Users/choyoungseo/Developer/edge/tests/loadtest/publication/results"
WANT = (b'"fresh_responses"', b'"stale_responses"', b'"blocked_responses"')


def parse_ts(s):
    return datetime.fromisoformat(s).timestamp()


def scan(run):
    meta = json.load(open(os.path.join(RESULTS, run, "meta.json")))
    s0 = float(meta["start_epoch"])
    acc = {"fresh_responses": [], "stale_responses": [], "blocked_responses": []}
    with gzip.open(os.path.join(RESULTS, run, "raw.json.gz"), "rb") as fh:
        for line in fh:
            if not any(w in line for w in WANT):
                continue
            rec = json.loads(line)
            if rec.get("type") != "Point":
                continue
            name = rec.get("metric")
            if name in acc:
                acc[name].append(parse_ts(rec["data"]["time"]) - s0)
    for v in acc.values():
        v.sort()
    return meta, acc


def pct(xs, q):
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


def main():
    runs = sorted(d for d in os.listdir(RESULTS) if "E6" in d
                  and os.path.exists(os.path.join(RESULTS, d, "summary.json"))
                  and json.load(open(os.path.join(RESULTS, d, "meta.json")))["k6_exit_code"] == 0)
    print("이벤트: 새 스냅샷 INSERT = t+120s, 차단 = t+150s (k6 start 기준)\n")
    for run in runs:
        meta, acc = scan(run)
        fresh, stale, blocked = acc["fresh_responses"], acc["stale_responses"], acc["blocked_responses"]
        # 차단 이후에는 fresh/stale 자체가 안 나오므로 전환 판독은 차단 전 구간만 본다.
        last_stale = stale[-1] if stale else None
        first_fresh = fresh[0] if fresh else None
        first_blocked = blocked[0] if blocked else None
        # 차단 후 마지막 200(=fresh) — 차단 스테일 관측
        last_fresh = fresh[-1] if fresh else None
        print(f"== {run}")
        print(f"   관측 수: fresh {len(fresh):,} / stale {len(stale):,} / blocked {len(blocked):,}")
        print(f"   stale 구간   : t+{stale[0]:.2f}s ~ t+{last_stale:.2f}s")
        print(f"   fresh 구간   : t+{first_fresh:.2f}s ~ t+{last_fresh:.2f}s")
        print(f"   blocked 구간 : t+{first_blocked:.2f}s ~ t+{blocked[-1]:.2f}s")
        print(f"   [반영] 마지막 stale − INSERT(120) = {last_stale - 120:.2f}s"
              f"   | 첫 fresh − INSERT = {first_fresh - 120:.2f}s"
              f"   | 전환 폭 = {last_stale - first_fresh:.2f}s")
        print(f"   [차단] 첫 blocked − 차단(150) = {first_blocked - 150:.2f}s"
              f"   | 마지막 fresh − 차단 = {last_fresh - 150:.2f}s")
        print()


if __name__ == "__main__":
    main()
