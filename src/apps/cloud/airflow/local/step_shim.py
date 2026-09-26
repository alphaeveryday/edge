"""로컬 ECS 대역의 컨테이너 진입점 — 운영 ENTRYPOINT(`python -m data_pipeline.run`)와 같은 main 을 부른다.

바꾸는 것은 둘뿐이다.
1. KIS 장중 추정 소스 → 저장된 dev raw 재생(`/inputs/dev-lake`). 수집 스텝의 저장·로그·원장 계측은
   실제 코드가 한다. 재생 1회의 종목 수를 "외부 호출"로 `/lab-data/state/external_calls.jsonl` 에 센다.
2. 장애 주입(`/lab-data/state/faults.json`): {"step","run_id","action","times",...} 규칙을 한 번씩 소모한다.
   - exit: 스텝 함수가 일을 하지 않고 code 를 돌려준다(외부 호출·쓰기 없음). 원장 wrapper 는 그대로 돌아
     운영처럼 실패 attempt 를 남긴다.
   - raw_read_error: 정제가 raw 를 읽을 때 1회 IOError(실제 코드가 exit 를 정한다).
   - sleep_before: 실행 전에 seconds 만큼 대기(실행 관리 프로세스를 중간에 죽이는 시험용).
   - bad_row: 수집 재생에 거래일(asof_date)이 빠진 행 1개를 섞는다(벤더 이상 응답).
   - sleep_in_step: 실행권을 잡은 **뒤** 스텝 함수 안에서 seconds 대기 후 원래 스텝(또는 code)으로 끝난다.
   - ledger_down: 이 실행의 DB 접속지를 닿지 않는 곳으로 바꾼다(원장 장애).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from data_pipeline import run as dp_run

INPUTS = Path("/inputs/dev-lake")
STATE = Path("/lab-data/state")
_RAW = "raw/source=kis/dataset=investor_flow_intraday/market=KR"
_LOG = "operations_archive/collection_logs/source=kis/dataset=investor_flow_intraday"


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _append(name: str, record: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(STATE / name, "a") as fp:
        fp.write(json.dumps(record) + "\n")


def _take_fault(step: str, run_id: str | None) -> dict | None:
    path = STATE / "faults.json"
    if not path.exists():
        return None
    with open(path, "r+") as fp:
        fcntl.flock(fp, fcntl.LOCK_EX)
        rules = json.load(fp)
        for rule in rules:
            if rule["step"] == step and rule.get("run_id") in (None, run_id) and rule.get("times", 1) > 0:
                rule["times"] = rule.get("times", 1) - 1
                fp.seek(0); fp.truncate(); json.dump(rules, fp)
                return rule
    return None


BAD_ROW = False


class ReplayEstimateSource:
    """KisInvestorEstimateSource 와 같은 인터페이스. 그 run_id 의 저장 raw 행을 그대로 낸다."""

    source_name = "kis"
    enabled = True
    universe_from_holdings = False

    def __init__(self, _config, _client):
        self.run_id = _arg(sys.argv, "--run-id")
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None
        stored = list(INPUTS.glob(f"{_LOG}/started_date=*/run_id={self.run_id}/log.json"))
        self._log = json.loads(stored[0].read_text()) if stored else None

    @property
    def skip_reason(self):
        # 실제 소스처럼 휴장일에는 수집하지 않는다(거래일 판정은 운영과 같은 OPS_KR_HOLIDAYS). 재생이라
        # "지금" 대신 그 슬롯이 수집된 KST 날짜로 판정한다.
        if self._log is not None:
            from datetime import timedelta
            from data_pipeline.ops.trading_calendar import is_trading_day
            day = (datetime.fromisoformat(self._log["started_at"]) + timedelta(hours=9)).date()
            if not is_trading_day(day):
                return f"lab: {day} 휴장일 — 장중 추정 없음"
        if self._log is None:
            return f"lab: 저장된 입력 없음(run_id={self.run_id}) — 재생할 슬롯이 아니다"
        return self._log.get("reason") if self._log.get("status") == "skipped" else None

    def fetch(self, symbols, from_date=None, to_date=None):
        files = list(INPUTS.glob(f"{_RAW}/ingest_date=*/run_id={self.run_id}/part-00000.ndjson"))
        rows = [json.loads(line) for f in files for line in f.read_text().splitlines() if line.strip()]
        if BAD_ROW and rows:
            rows.append({**rows[0], "asof_date": None})
        tickers = sorted({row.get("our_ticker") for row in rows})
        self.planned_symbols = len(tickers)
        self.fetch_failures = list(self._log.get("failed_symbols") or [])
        _append("external_calls.jsonl", {"run_id": self.run_id, "calls": len(tickers),
                                          "ecs_task_arn": os.environ.get("OPS_ECS_TASK_ARN"),
                                          "at": datetime.now(timezone.utc).isoformat()})
        yield from rows


def main(argv: list[str]) -> int:
    step, run_id = argv[0], _arg(argv, "--run-id")
    _append("invocations.jsonl", {"step": step, "run_id": run_id, "argv": argv,
                                  "ecs_task_arn": os.environ.get("OPS_ECS_TASK_ARN"),
                                  "skip_if_succeeded": os.environ.get("OPS_SKIP_IF_SUCCEEDED"),
                                  "at": datetime.now(timezone.utc).isoformat()})
    fault = _take_fault(step, run_id)
    if fault and fault["action"] == "sleep_before":
        time.sleep(fault["seconds"])
    if fault and fault["action"] == "exit":
        from data_pipeline.steps import (ingest_raw_investor, load_investor_intraday,
                                         normalize_investor_estimate)
        module = {"ingest-raw-investor-estimate": ingest_raw_investor,
                  "normalize-investor-estimate": normalize_investor_estimate,
                  "load-investor-intraday": load_investor_intraday}[step]
        module.run = lambda *a, **k: fault["code"]
    if fault and fault["action"] == "ledger_down":
        os.environ["DATA_PIPELINE_DB__HOST"] = "ledger-down.invalid"
    if fault and fault["action"] == "sleep_in_step":
        from data_pipeline.steps import (ingest_raw_investor, load_investor_intraday,
                                         normalize_investor_estimate)
        module = {"ingest-raw-investor-estimate": ingest_raw_investor,
                  "normalize-investor-estimate": normalize_investor_estimate,
                  "load-investor-intraday": load_investor_intraday}[step]
        original = module.run

        def slow(*a, **k):
            time.sleep(fault["seconds"])
            return fault["code"] if "code" in fault else original(*a, **k)
        module.run = slow
    if fault and fault["action"] == "bad_row":
        global BAD_ROW
        BAD_ROW = True
    if fault and fault["action"] == "raw_read_error":
        from data_pipeline.lake import storage as lake_storage
        original, armed = lake_storage.LocalStorage.get_bytes, [True]

        def failing(self, key):
            if armed[0] and key.startswith("raw/"):
                armed[0] = False
                raise OSError("lab: 주입한 raw 읽기 실패")
            return original(self, key)

        lake_storage.LocalStorage.get_bytes = failing
    dp_run.KisInvestorEstimateSource = ReplayEstimateSource
    return dp_run.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
