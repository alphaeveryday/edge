"""호스트 실행기 — 비교군별 시나리오를 돌리고 시간·호출·자원·대사 결과를 남긴다(표준 라이브러리만).

    python3 trial.py up
    python3 trial.py run <결과이름> [--reps N]    # results/<결과이름>/ (있으면 실패)
    python3 trial.py down

시나리오(비교군마다 초기화 후):
  N  정상: 결함 없이 7 업무일 처리 → 대사
  F  2026-09-17·09-23 정제에 일회성 raw 읽기 실패 주입 → 정상 요청 → 복구 절차 → 대사
  D  F 직후 같은 날짜 범위·같은 입력을 다시 요청 → 대사(중복·누락·잘못된 날짜)
복구 경과시간 = 복구 요청 시작 → 독립 대사 통과(monotonic). 사람 작업시간이 아니다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = "airflow-lab"
DC = ["docker", "compose", "-p", PROJECT, "-f", str(HERE / "compose.yaml")]
FROM, TO = "2026-09-15", "2026-09-23"
FAULTS = ["2026-09-17", "2026-09-23"]
DAG = "price_daily_recovery"
API = "http://127.0.0.1:58080"


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        self.phase = "idle"
        self.log = (out / "commands.log").open("a")
        self.stats = (out / "stats.jsonl").open("a")
        self._stop = False

    def sh(self, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
        started = time.monotonic()
        p = subprocess.run(args, capture_output=True, text=True)
        self.log.write(json.dumps({
            "phase": self.phase, "cmd": args, "rc": p.returncode,
            "sec": round(time.monotonic() - started, 3), "stdout": p.stdout, "stderr": p.stderr,
        }, ensure_ascii=False) + "\n")
        self.log.flush()
        if check and p.returncode != 0:
            raise RuntimeError(f"실패 rc={p.returncode}: {args}\n{p.stderr[-2000:]}")
        return p

    def lab(self, service: str, *args: str, check: bool = False, cand: str = "setup"):
        return self.sh(DC + ["exec", "-T", "-e", f"LAB_CANDIDATE={cand}", service,
                             "/opt/dp/bin/python", "/lab/lab.py", *args], check=check)

    def sample_forever(self):
        while not self._stop:
            p = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                               capture_output=True, text=True)
            ts = time.time()
            for line in p.stdout.splitlines():
                s = json.loads(line)
                if s["Name"].startswith(PROJECT):
                    self.stats.write(json.dumps({"t": ts, "phase": self.phase, "name": s["Name"],
                                                 "cpu": s["CPUPerc"], "mem": s["MemUsage"]}) + "\n")
            self.stats.flush()


# ── Airflow REST ─────────────────────────────────────────────────────────────

def _runs() -> list[dict]:
    # SIMPLE_AUTH_MANAGER_ALL_ADMINS — 로컬 127.0.0.1 전용이라 인증 없이 읽는다
    return json.load(urllib.request.urlopen(f"{API}/api/v2/dags/{DAG}/dagRuns?limit=100"))["dag_runs"]


def _wait_runs(expected: int, timeout: float = 900) -> list[dict]:
    """dag run 이 expected 개 생기고 전부 종료(success/failed)될 때까지 1초 간격으로 본다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = _runs()
        # backfill 은 DAG 당 하나만 돈다 — 스케줄러가 completed_at 을 찍어야 다음 요청이 받아진다
        backfills = json.load(urllib.request.urlopen(
            f"{API}/api/v2/backfills?dag_id={DAG}"))["backfills"]
        if (len(runs) >= expected and all(r["state"] in ("success", "failed") for r in runs)
                and all(b["completed_at"] for b in backfills)):
            return runs
        time.sleep(1)
    raise TimeoutError("Airflow dag run 대기 시간초과")


def airflow(rec: Recorder, *args: str, check: bool = True):
    return rec.sh(DC + ["exec", "-T", "airflow", "airflow", *args], check=check)


# ── 비교군별 절차 ─────────────────────────────────────────────────────────────

def _invocations(rec: Recorder) -> list[dict]:
    # 파일이 없을 때(호출 0건)만 빈 목록이다 — 조회 실패를 0건으로 접으면 단계별 집계가 밀린다
    p = rec.sh(DC + ["exec", "-T", "runner", "sh", "-c",
                     "f=/lab-data/state/invocations.jsonl; [ ! -e $f ] || cat $f"], check=True)
    return [json.loads(line) for line in p.stdout.splitlines() if line.strip()]


def normal_request(rec: Recorder, cand: str) -> dict:
    """정상 요청(날짜 범위 전체). 반환: 실패 알림 등 다음 절차에 필요한 관측."""
    if cand == "A":
        p = rec.lab("runner", "sfn-local", FROM, TO, cand="A")
        return {"notified": [l.split("=", 1)[1] for l in p.stdout.splitlines()
                             if l.startswith("NotifyFailure run_id=")]}
    if cand == "B":
        p = rec.lab("runner", "backfill", FROM, TO, cand="B")
        return {"summary": p.stdout.strip().splitlines()[-1]}
    before = len(_runs())
    airflow(rec, "backfill", "create", "--dag-id", DAG, "--from-date", FROM,
            "--to-date", f"{TO}T23:59:59+09:00", "--max-active-runs", "1",
            "--reprocess-behavior", "none")
    runs = _wait_runs(max(before, 7))
    return {"runs": {r["logical_date"][:10]: r["state"] for r in runs}}


def recover(rec: Recorder, cand: str, observed: dict) -> list[str]:
    """복구 요청. 반환: 운영자가 제출한 명령(수동 조작) 목록."""
    manual = []
    if cand == "A":
        # statemachine.tf NormalizeParallel 주석의 절차: 새 run-id 로 실패 런의 raw 를 명시 정제,
        # 적재는 그 정제 manifest(새 run-id)를 입력으로 받는다.
        for failed in observed["notified"]:
            new = f"manual_{failed[-8:]}_{int(time.time())}"
            for argv in (["normalize-price", "--run-id", new, "--input-run-id", failed],
                         ["load-price-daily", "--run-id", new, "--input-run-id", new]):
                rec.lab("runner", "step", *argv, cand="A")
                manual.append(" ".join(argv))
        return manual
    if cand == "B":
        rec.lab("runner", "backfill", FROM, TO, cand="B")
        return [f"backfill {FROM} {TO}"]
    args = ["tasks", "clear", DAG, "-s", FROM, "-e", "2026-09-24", "--only-failed",
            "--downstream", "-y"]
    airflow(rec, *args)
    _wait_runs(7)
    return [" ".join(args)]


def scenario(rec: Recorder, cand: str, name: str, faults: list[str]) -> dict:
    rec.phase = f"{cand}:{name}:reset"
    if cand == "C":
        airflow(rec, "dags", "delete", DAG, "-y", check=False)
        airflow(rec, "dags", "reserialize")
        airflow(rec, "dags", "unpause", DAG)
    rec.lab("runner", "reset", *sum((["--fault-date", d] for d in faults), []), check=True)
    result: dict = {"candidate": cand, "scenario": name, "faults": faults}

    rec.phase = f"{cand}:{name}:request"
    t0 = time.monotonic()
    observed = normal_request(rec, cand)
    result["request_sec"] = round(time.monotonic() - t0, 2)
    result["observed"] = observed
    v = rec.lab("runner", "verify")
    result["after_request"] = json.loads(v.stdout)
    n_request = len(_invocations(rec))

    if faults:
        rec.phase = f"{cand}:{name}:recover"
        t1 = time.monotonic()
        result["manual_commands"] = recover(rec, cand, observed)
        v = rec.lab("runner", "verify")
        result["recovery_sec"] = round(time.monotonic() - t1, 2)
        result["after_recovery"] = json.loads(v.stdout)
        n_recover = len(_invocations(rec))

        rec.phase = f"{cand}:D:rerequest"
        t2 = time.monotonic()
        result["rerequest_observed"] = normal_request(rec, cand)
        v = rec.lab("runner", "verify")
        result["rerequest_sec"] = round(time.monotonic() - t2, 2)
        result["after_rerequest"] = json.loads(v.stdout)
    inv = _invocations(rec)
    result["invocations"] = {
        "request": _count(inv[:n_request]),
        **({"recover": _count(inv[n_request:n_recover]),
            "rerequest": _count(inv[n_recover:])} if faults else {}),
    }
    result["invocation_log"] = inv
    rec.phase = "idle"
    return result


def _count(inv: list[dict]) -> dict:
    out: dict = {}
    for i in inv:
        # exit 2 를 계속 진행으로 보는 것은 normalize-price 뿐이다(lab.py step 과 같은 규칙)
        ok = i["exit"] == 0 or (i["step"] == "normalize-price" and i["exit"] == 2)
        key = f"{i['step']}:{'ok' if ok else 'fail'}"
        out[key] = out.get(key, 0) + 1
    return out


def versions(rec: Recorder) -> dict:
    def out(args):
        return rec.sh(args).stdout.strip()
    return {
        "git_sha": out(["git", "-C", str(HERE), "rev-parse", "HEAD"]),
        "docker": out(["docker", "version", "--format", "{{.Server.Version}}"]),
        "airflow": out(DC + ["exec", "-T", "airflow", "airflow", "version"]),
        "airflow_python": out(DC + ["exec", "-T", "airflow", "python", "--version"]),
        "dp_python": out(DC + ["exec", "-T", "runner", "/opt/dp/bin/python", "--version"]),
        "dp_freeze": out(DC + ["exec", "-T", "runner", "/opt/dp/bin/pip", "freeze"]).splitlines(),
        "images": out(DC + ["images", "--format", "json"]),
    }


def run(name: str, reps: int) -> int:
    out = HERE / "results" / name
    out.mkdir(parents=True)  # 기존 결과를 덮지 않는다
    rec = Recorder(out)
    sampler = threading.Thread(target=rec.sample_forever, daemon=True)
    sampler.start()
    results = []
    try:
        (out / "versions.json").write_text(json.dumps(versions(rec), indent=1))
        rec.phase = "airflow-idle"
        time.sleep(60)   # Airflow 유휴 자원 기준선
        orders = [["A", "B", "C"], ["B", "C", "A"], ["C", "A", "B"]]
        for cand in "ABC":
            results.append(scenario(rec, cand, "N", []))
        for rep in range(reps):
            for cand in orders[rep % 3]:
                r = scenario(rec, cand, "F", FAULTS)
                r["rep"] = rep
                results.append(r)
        rec.sh(["docker", "cp", f"{PROJECT}-airflow-1:/opt/airflow/logs", str(out / "airflow-logs")])
    finally:
        rec._stop = True
        sampler.join(timeout=10)
        (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    # 정상 요청 뒤(N)·복구 뒤·재요청 뒤(F) 대사를 각각 본다 — 재요청 성공이 복구 실패를 가리지
    # 않게. F 의 요청 직후 불일치는 주입한 실패라 판정에서 뺀다. 결과 파일은 그대로 남긴다.
    checks = {"N": ("after_request",), "F": ("after_recovery", "after_rerequest")}
    # B·C 는 원장 완료(7/7)까지가 완료 계약이다. A 복구 뒤 5/7 은 현행 절차의 관측값이라 뺀다.
    def bad(r, k):
        v = r[k]
        ledger_required = r["candidate"] != "A" or k != "after_recovery"
        return not v["business_ok"] or (ledger_required and v["runs_complete"] != 7)
    failed = [f"{r['candidate']}:{r['scenario']}:{k}" for r in results
              for k in checks[r["scenario"]] if bad(r, k)]
    if failed:
        print("최종 대사 불일치:", failed, file=sys.stderr)
        return 1
    return 0


def _mib(text: str) -> float:
    num = float("".join(c for c in text if c.isdigit() or c == "."))
    return num * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024}[text.strip()[-3:]]


def summary(name: str) -> int:
    """results.json·stats.jsonl → 표(markdown). 판단은 넣지 않고 수치만 모은다."""
    out = HERE / "results" / name
    results = json.loads((out / "results.json").read_text())
    lines = ["| 비교군 | 시나리오 | rep | 요청(s) | 복구(s) | 재요청(s) | 제출 명령 | 요청 호출 | 복구 호출 | 재요청 호출 | 요청 후 정확 | 복구 후 정확 | 재요청 후 정확 | 복구 후 원장 완료 | data_version≠순차 |",
             "|" + "---|" * 15]
    for r in results:
        def ok(k):
            v = r.get(k)
            return "-" if v is None else ("O" if v["business_ok"] else "X") + f"({v['runs_complete']}/7)"
        inv = r["invocations"]
        fmt = lambda d: " ".join(f"{k}={v}" for k, v in sorted(d.items())) or "0"
        last = r.get("after_rerequest") or r.get("after_recovery") or r["after_request"]
        lines.append("| " + " | ".join(str(x) for x in [
            r["candidate"], r["scenario"], r.get("rep", "-"), r["request_sec"],
            r.get("recovery_sec", "-"), r.get("rerequest_sec", "-"),
            len(r.get("manual_commands", [])) if r["faults"] else "-",
            fmt(inv["request"]), fmt(inv.get("recover", {})) if r["faults"] else "-",
            fmt(inv.get("rerequest", {})) if r["faults"] else "-",
            ok("after_request"), ok("after_recovery"), ok("after_rerequest"),
            r["after_recovery"]["runs_complete"] if r["faults"] else "-",
            last["price_daily"]["data_version_differs_from_sequential"],
        ]) + " |")
    stats: dict = {}
    for line in (out / "stats.jsonl").read_text().splitlines():
        s = json.loads(line)
        phase = s["phase"].split(":")
        bucket = "idle" if s["phase"] in ("idle", "airflow-idle") else f"{phase[0]}-{phase[-1]}"
        key = (s["name"].replace(PROJECT + "-", "").rsplit("-", 1)[0], bucket)
        stats.setdefault(key, []).append((float(s["cpu"].rstrip("%")), _mib(s["mem"].split("/")[0])))
    lines += ["", "| 컨테이너 | 구간 | 표본 | CPU% 평균 | CPU% 최대 | 메모리 MiB 평균 | 메모리 MiB 최대 |",
              "|---|---|---|---|---|---|---|"]
    for (name_, bucket), xs in sorted(stats.items()):
        cpu = [x[0] for x in xs]
        mem = [x[1] for x in xs]
        lines.append(f"| {name_} | {bucket} | {len(xs)} | {sum(cpu)/len(cpu):.1f} | {max(cpu):.1f} |"
                     f" {sum(mem)/len(mem):.0f} | {max(mem):.0f} |")
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text)
    print(text)
    return 0


def up() -> int:
    subprocess.run(DC + ["up", "-d", "--wait"], check=True)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "up":
        sys.exit(up())
    if cmd == "down":
        sys.exit(subprocess.run(DC + ["down", "-v"]).returncode)
    if cmd == "summary":
        sys.exit(summary(sys.argv[2]))
    if cmd == "run":
        reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else 3
        sys.exit(run(sys.argv[2], reps))
    raise SystemExit(f"알 수 없는 명령: {cmd}")
