#!/usr/bin/env python3
"""투표 부하 생성기. 결과 파일에 CSV(ts_ms,status,latency_ms).

사용: load.py <out_csv> <forecast_id> [rps=20] [duration_s=60] [users=100]
duration+10초에 무조건 종료한다(요청이 매달려도 부하 측정이 멈추지 않게).
"""
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

OUT = sys.argv[1]
FORECAST_ID = int(sys.argv[2])
RPS = int(sys.argv[3]) if len(sys.argv) > 3 else 20
DURATION = int(sys.argv[4]) if len(sys.argv) > 4 else 60
USERS = int(sys.argv[5]) if len(sys.argv) > 5 else 100
THREADS = 10

start = time.time()
buffers = [[] for _ in range(THREADS)]


def worker(n):
    interval = THREADS / RPS
    while time.time() < start + DURATION:
        t0 = time.time()
        req = urllib.request.Request(
            f"http://localhost:8080/api/forecasts/{FORECAST_ID}/vote",
            data=json.dumps({"choice": random.choice(["AGREE", "DISAGREE"])}).encode(),
            method="PUT",
            headers={"Content-Type": "application/json",
                     "X-User-Id": str(random.randint(1, USERS))})
        try:
            with urllib.request.urlopen(req, timeout=3) as res:
                status = res.status
        except urllib.error.HTTPError as e:
            status = e.code
        except Exception:
            status = 0
        buffers[n].append(f"{int(t0 * 1000)},{status},{(time.time() - t0) * 1000:.1f}")
        remain = interval - (time.time() - t0)
        if remain > 0:
            time.sleep(remain)


threads = [threading.Thread(target=worker, args=(n,), daemon=True) for n in range(THREADS)]
for t in threads:
    t.start()
deadline = start + DURATION + 10
for t in threads:
    t.join(max(0.1, deadline - time.time()))

with open(OUT, "w") as f:
    f.write("\n".join(sorted(line for buf in buffers for line in buf)) + "\n")
os._exit(0)
