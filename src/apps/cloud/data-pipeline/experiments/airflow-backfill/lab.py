"""컨테이너 안에서 도는 실험 도구 — 세 비교군(A·B·C)이 같은 명령을 호출한다.

업무 처리는 `data_pipeline.run.main`(운영 ECS 진입점)을 그대로 부른다. 여기 있는 것은
입력 선택·실패 주입·호출 기록·실행 확인·독립 대사뿐이다.

    reset [--fault-date D ...]   레이크 산출·DB·실행 기록 초기화 + 원장 계획(실제 planner)
    select-input D               업무일 D 의 고정 입력(run_id) 확인 후 출력
    step <run.py 인자...>        실제 스텝 실행(실패 주입·호출 기록 포함)
    check-run RUN_ID             원장·manifest 로 그 실행의 정제·적재 완료 확인
    sfn-local FROM TO            A: 현재 SFN 체인(정제→적재)의 로컬 재현
    backfill FROM TO             B: 입력 고정 + 완료 날짜 건너뛰기 + 실행 확인
    verify                       원본에서 직접 계산한 기대 결과와 대사(JSON 출력)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parent
INPUTS = json.loads((LAB / "inputs.json").read_text())["inputs"]
DATA = Path("/lab-data")
LAKE = DATA / "lake"
STATE = DATA / "state"
CANDIDATE = os.environ.get("LAB_CANDIDATE", "?")


def _settings():
    from data_pipeline.config import load_settings
    return load_settings()


def _db():
    from data_pipeline.db import connect
    return connect(_settings().db)


def _log(event: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "invocations.jsonl").open("a") as fp:
        fp.write(json.dumps({"candidate": CANDIDATE, **event}) + "\n")


def _input_for(date: str) -> dict:
    for item in INPUTS:
        if item["business_date"] == date:
            return item
    raise SystemExit(f"업무일 {date} 의 고정 입력이 없다 — 원본 누락을 성공으로 처리하지 않는다")


# ── reset ────────────────────────────────────────────────────────────────────

class _StubSfn:
    """planner 의 StartExecution 만 받아 준다 — AWS 호출 없음."""

    def start_execution(self, *, stateMachineArn, name, input):
        return {"executionArn": stateMachineArn.replace(":stateMachine:", ":execution:") + ":" + name}


def reset(fault_dates: list[str]) -> int:
    from data_pipeline.ops.ledger import Ledger
    from data_pipeline.ops.planner import plan_run

    if LAKE.exists():
        shutil.rmtree(LAKE)
    shutil.copytree("/inputs/raw", LAKE / "raw")
    if STATE.exists():
        shutil.rmtree(STATE)
    STATE.mkdir(parents=True)
    faults = {_input_for(d)["run_id"]: 1 for d in fault_dates}
    (STATE / "faults.json").write_text(json.dumps(faults))

    tickers = sorted({
        json.loads(line)["our_ticker"]
        for item in INPUTS
        for line in (LAKE / item["raw_key"]).read_text().splitlines() if line.strip()
    })
    with _db() as conn:
        conn.execute("TRUNCATE price_daily, ops_task_attempt, ops_reconciliation_issue,"
                     " ops_expectation_snapshot, ops_expected_task, ops_pipeline_run CASCADE")
        # fixture: 종목 마스터는 실험 대상이 아니다 — 원본에 나온 ticker 만 XKRX 로 등록한다.
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO entity (entity_id, entity_type, display_name) VALUES"
                " (%s,'INSTRUMENT',%s) ON CONFLICT DO NOTHING",
                [(f"lab_{t}", t) for t in tickers],
            )
            cur.executemany(
                "INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type,"
                " currency_code) VALUES (%s,'XKRX',%s,'EQUITY','KRW') ON CONFLICT DO NOTHING",
                [(f"lab_{t}", t) for t in tickers],
            )
    # 실제 Planner 로 기대 작업을 등록한다(SFN 시작만 stub). run_id 는 운영과 같은 값으로 파생된다.
    ledger = Ledger(_settings().db)
    for item in INPUTS:
        slot = datetime.fromisoformat(item["business_date"] + "T15:40:00+09:00")
        result = plan_run(ledger, state_machine_arn="arn:aws:states:lab:0:stateMachine:lab",
                          scheduled_time=slot, sfn_client=_StubSfn())
        if result.pipeline_run_id != item["run_id"]:
            raise SystemExit(f"planner run_id 불일치: {result.pipeline_run_id} != {item['run_id']}")
    print(json.dumps({"reset": True, "tickers": len(tickers), "faults": faults}))
    return 0


# ── select-input / step / check-run ───────────────────────────────────────────

def select_input(date: str) -> int:
    item = _input_for(date)
    raw = LAKE / item["raw_key"]
    if not raw.is_file():
        raise SystemExit(f"{date}: 원본 파일 없음 {item['raw_key']}")
    if hashlib.sha256(raw.read_bytes()).hexdigest() != item["sha256"]:
        raise SystemExit(f"{date}: 원본 checksum 불일치 — 고정 입력이 아니다")
    print(item["run_id"])
    return 0


def _arm_fault(argv: list[str]) -> bool:
    """normalize-price 가 실패 대상 입력을 읽으면 그 raw 읽기를 한 번 실패시킨다(일회성)."""
    if argv[0] != "normalize-price" or "--input-run-id" not in argv:
        return False
    target = argv[argv.index("--input-run-id") + 1]
    path = STATE / "faults.json"
    faults = json.loads(path.read_text()) if path.exists() else {}
    if faults.get(target, 0) <= 0:
        return False
    faults[target] -= 1
    path.write_text(json.dumps(faults))

    from data_pipeline.lake.storage import LocalStorage
    original = LocalStorage.get_bytes

    def failing_get_bytes(self, key):
        if key.startswith("raw/") and f"/run_id={target}/" in key:
            raise OSError("lab: injected raw read failure")
        return original(self, key)

    LocalStorage.get_bytes = failing_get_bytes
    return True


def step(argv: list[str]) -> int:
    from data_pipeline.run import main

    fault = _arm_fault(argv)
    started = time.time()
    code = main(argv)
    _log({"step": argv[0], "argv": argv, "exit": code, "fault": fault,
          "started": started, "ended": time.time()})
    # SFN 의 NormalizePrice 는 exit 2(행 일부 격리)에도 다음 단계로 간다 — 같은 규칙.
    if argv[0] == "normalize-price" and code == 2:
        return 0
    return code


def _run_status(run_id: str) -> dict:
    from data_pipeline.lake import LocalStorage, canonical_run_manifest_key

    with _db() as conn:
        rows = conn.execute(
            "SELECT task_key, task_outcome, data_status FROM ops_expected_task"
            " WHERE pipeline_run_id = %s AND task_key IN ('NORMALIZE_PRICE','LOAD_PRICE_DAILY')",
            (run_id,),
        ).fetchall()
    tasks = {k: {"outcome": o, "data_status": d} for k, o, d in rows}
    storage = LocalStorage(str(LAKE))
    try:
        manifest = json.loads(storage.get_bytes(canonical_run_manifest_key("price_daily", run_id)))
        written = manifest.get("canonical_written") is True
    except FileNotFoundError:
        written = False
    ok = written and all(tasks.get(k, {}).get("outcome") == "FULFILLED"
                         for k in ("NORMALIZE_PRICE", "LOAD_PRICE_DAILY"))
    return {"run_id": run_id, "ok": ok, "manifest_written": written, "tasks": tasks}


def check_run(run_id: str) -> int:
    status = _run_status(run_id)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["ok"] else 1


# ── A: 현재 SFN 체인의 로컬 재현 / B: 개선 스크립트 ────────────────────────────

def _dates(frm: str, to: str) -> list[str]:
    return [i["business_date"] for i in INPUTS if frm <= i["business_date"] <= to]


def sfn_local(frm: str, to: str) -> int:
    """현재 경로: 날짜마다 정제 → (성공 시) 적재. 실패는 실패 run_id 를 알린다(SNS 대신 출력)."""
    failed = []
    for date in _dates(frm, to):
        rid = _input_for(date)["run_id"]
        if step(["normalize-price", "--run-id", rid, "--input-run-id", rid]) != 0:
            failed.append(rid)
            print(f"NotifyFailure run_id={rid}")   # SNS 제목 = run_id (statemachine.tf)
            continue
        if step(["load-price-daily", "--run-id", rid, "--input-run-id", rid]) != 0:
            failed.append(rid)
            print(f"NotifyFailure run_id={rid}")
    return 1 if failed else 0


def backfill(frm: str, to: str) -> int:
    """개선: 고정 입력 확인, 완료 확인된 날짜는 건너뜀, 같은 run_id 로 원장에 기록, 결과 확인."""
    summary = {}
    for date in _dates(frm, to):
        rid = _input_for(date)["run_id"]
        if _run_status(rid)["ok"]:
            summary[date] = "skipped(done)"
            continue
        if select_input(date) != 0:
            summary[date] = "input_error"
            continue
        if step(["normalize-price", "--run-id", rid, "--input-run-id", rid]) != 0:
            summary[date] = "normalize_failed"
            continue
        if step(["load-price-daily", "--run-id", rid, "--input-run-id", rid]) != 0:
            summary[date] = "load_failed"
            continue
        summary[date] = "ok" if check_run(rid) == 0 else "check_failed"
    print(json.dumps(summary))
    return 0 if all(v in ("ok", "skipped(done)") for v in summary.values()) else 1


# ── 독립 대사 ────────────────────────────────────────────────────────────────

def _expected() -> tuple[dict, dict]:
    """원본에서 직접: (market,ticker,trade_date) → 최신 fetched_at 행. 정제 코드를 쓰지 않는다."""
    best: dict = {}
    last_run: dict = {}
    for item in INPUTS:  # 업무일 순
        for line in (Path("/inputs") / item["raw_key"]).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            d = r["stck_bsop_date"]
            key = (r["market"], r["our_ticker"], f"{d[:4]}-{d[4:6]}-{d[6:]}")
            val = (float(r["stck_oprc"]), float(r["stck_hgpr"]), float(r["stck_lwpr"]),
                   float(r["stck_clpr"]), int(r["acml_vol"]))
            at = datetime.fromisoformat(r["fetched_at"])
            if key not in best or at >= best[key][1]:
                best[key] = (val, at, r["fetched_at"])
            last_run[key] = item["run_id"]
    return best, last_run


def verify() -> int:
    import pyarrow.parquet as pq

    best, last_run = _expected()
    got: dict = {}
    dup = 0
    for f in (LAKE / "canonical").rglob("*.parquet") if (LAKE / "canonical").exists() else []:
        if "price_daily" not in str(f):
            continue
        for r in pq.read_table(f).to_pylist():
            key = (r["market"], r["ticker"], r["trade_date"])
            dup += key in got
            got[key] = ((r["open"], r["high"], r["low"], r["close"], r["volume"]), r["fetched_at"])
    canon = {"expected": len(best), "actual": len(got), "duplicates": dup,
             "missing": len(best.keys() - got.keys()), "extra": len(got.keys() - best.keys()),
             "value_mismatch": sum(1 for k in best.keys() & got.keys() if best[k][0] != got[k][0]),
             "fetched_at_mismatch": sum(1 for k in best.keys() & got.keys()
                                        if best[k][2] != got[k][1])}

    with _db() as conn:
        db_rows = conn.execute(
            "SELECT i.ticker, p.trade_date::text, p.close_price::float8, p.adjusted_close_price,"
            " p.volume, p.data_version FROM price_daily p JOIN instrument i USING (instrument_id)"
        ).fetchall()
        ledger = conn.execute(
            "SELECT r.trading_date::text, t.task_key, t.task_outcome, t.data_status,"
            " (SELECT count(*) FROM ops_task_attempt a WHERE a.expected_task_id = t.expected_task_id)"
            " FROM ops_expected_task t JOIN ops_pipeline_run r USING (pipeline_run_id)"
            " WHERE t.task_key IN ('NORMALIZE_PRICE','LOAD_PRICE_DAILY') ORDER BY 1, 2"
        ).fetchall()
    db = {(("KR", t, d)): (c, a, v, dv) for t, d, c, a, v, dv in db_rows}
    db_res = {"expected": len(best), "actual": len(db), "duplicates": len(db_rows) - len(db),
              "missing": len(best.keys() - db.keys()), "extra": len(db.keys() - best.keys()),
              "value_mismatch": sum(1 for k in best.keys() & db.keys()
                                    if (best[k][0][3], None, best[k][0][4]) != db[k][:3]),
              "data_version_differs_from_sequential": sum(
                  1 for k in best.keys() & db.keys() if db[k][3] != last_run[k])}

    runs = {i["business_date"]: _run_status(i["run_id"]) for i in INPUTS}
    ok = (canon["missing"] == canon["extra"] == canon["duplicates"] == canon["value_mismatch"] == 0
          and db_res["missing"] == db_res["extra"] == db_res["duplicates"]
          == db_res["value_mismatch"] == 0)
    report = {
        "business_ok": ok,
        "canonical": canon,
        "price_daily": db_res,
        "runs_complete": sum(1 for r in runs.values() if r["ok"]),
        "runs": {d: {"ok": r["ok"], "manifest": r["manifest_written"],
                     **{k: v["outcome"] for k, v in r["tasks"].items()}} for d, r in runs.items()},
        "ledger_attempts": {f"{d}/{k}": n for d, k, _, _, n in ledger},
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    cmd, rest = argv[0], argv[1:]
    if cmd == "reset":
        return reset([a for a in rest if a != "--fault-date"])
    if cmd == "select-input":
        return select_input(rest[0])
    if cmd == "step":
        return step(rest)
    if cmd == "check-run":
        return check_run(rest[0])
    if cmd == "sfn-local":
        return sfn_local(*rest)
    if cmd == "backfill":
        return backfill(*rest)
    if cmd == "verify":
        return verify()
    raise SystemExit(f"알 수 없는 명령: {cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
