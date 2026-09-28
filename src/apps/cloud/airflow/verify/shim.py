"""격리 검증 태스크의 진입점(ALPHA-1119) — 운영 ENTRYPOINT(`python -m data_pipeline.run`)와 같은 main 을 부른다.

로컬 대역 `local/step_shim.py` 의 실제 AWS 판이다. 원장 wrapper·게이트·스텝 코드는 배포된 업무 이미지 그대로이고,
바꾸는 것은 셋뿐이다(저장 위치는 모두 검증 버킷 `VERIFY_BUCKET`, 원장은 검증 DB — 태스크 정의가 정한다).
1. KIS 장중 추정 소스 → 버킷의 저장 응답(`fixtures/investor_estimate.ndjson`) 재생. asof_date 는 오늘(KST)로 바꾼다
   (이 소스는 당일만 수집한다). 외부 호출 없음 — 재생 1회를 `state/external_calls/` 에 센다.
2. 계수: 스텝 함수가 실제로 불린 횟수(`state/business_starts/` — 호출 **전에** 남긴다. wrapper 가 보류·skip 하면 0)와
   끝난 횟수(`state/business_runs/`, exit 포함), canonical 장중 수급 파티션 쓰기(`state/partition_writes/`). exit code 가
   아니라 이것으로 "업무가 돌았나"를 판정한다.
3. 장애(env `VERIFY_FAULT`, 검증 DAG 가 try 별로 넣는다): {"exit": code} 업무 없이 code 로 끝냄(wrapper 는 그대로
   실패 attempt 를 남긴다) · {"sleep_in_step": 초} 실행권을 잡은 뒤 스텝 안에서 대기 후 정상 진행 ·
   {"sleep_before": 초} wrapper 전에 대기.

관리 명령(검증 절차 run.sh 가 ops 태스크 정의로 띄운다):
- `verify-setup-db`: 관리 DB(VERIFY_ADMIN_DB)에 붙어 검증 DB(DATA_PIPELINE_DB__NAME)가 없으면 만든다.
- `verify-seed`: 재생 입력에 나오는 종목만 instrument 로 등록한다(종목 마스터는 비교 대상이 아니다).
- `verify-reset`: 검증 원장의 레인 행과 버킷의 state·lake 를 지운다. 검증 DB 가 아니면 거부한다.
- `verify-ledger`: 검증 원장의 레인 행(런·기대 작업·시도·보류·적재 행 수)을 로그에 JSON 한 줄로 낸다.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import boto3

from data_pipeline import run as dp_run

KST = timezone(timedelta(hours=9))
BUCKET = os.environ["VERIFY_BUCKET"]
FIXTURE = "fixtures/investor_estimate.ndjson"
STEP_MODULES = ("ingest-raw-investor-estimate", "normalize-investor-estimate", "load-investor-intraday")
_s3 = boto3.client("s3")


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _record(kind: str, record: dict) -> None:
    """S3 는 append 가 없다 — 사건 하나 = 객체 하나. 개수가 곧 횟수다."""
    now = datetime.now(timezone.utc)
    record = {**record, "ecs_task_arn": _task_arn(), "at": now.isoformat(),
              "attempt_ref": os.environ.get("OPS_ORCHESTRATOR_ATTEMPT_REF")}   # airflow:<dag>/<dag_run>/<task>/<try>
    _s3.put_object(Bucket=BUCKET, Key=f"state/{kind}/{now:%Y%m%dT%H%M%S%f}-{uuid.uuid4().hex[:8]}.json",
                   Body=json.dumps(record).encode())


def _task_arn() -> str | None:
    from data_pipeline.ops import wrapper
    try:
        return wrapper._detect_ecs_task_arn()      # 원장이 attempt 에 남기는 것과 같은 값
    except Exception:
        return None


def _step_module(step: str):
    from data_pipeline.steps import ingest_raw_investor, load_investor_intraday, normalize_investor_estimate
    return {"ingest-raw-investor-estimate": ingest_raw_investor,
            "normalize-investor-estimate": normalize_investor_estimate,
            "load-investor-intraday": load_investor_intraday}.get(step)


def _fixture_rows() -> list[dict]:
    body = _s3.get_object(Bucket=BUCKET, Key=FIXTURE)["Body"].read().decode()
    return [json.loads(line) for line in body.splitlines() if line.strip()]


class ReplayEstimateSource:
    """KisInvestorEstimateSource 와 같은 인터페이스. 버킷의 저장 응답을 오늘 날짜로 낸다."""

    source_name = "kis"
    enabled = True
    universe_from_holdings = False

    def __init__(self, _config, _client):
        self.run_id = _arg(sys.argv, "--run-id")
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None

    @property
    def skip_reason(self):
        from data_pipeline.ops.trading_calendar import is_trading_day
        today = datetime.now(KST).date()
        return None if is_trading_day(today) else f"verify: {today} 휴장일 — 장중 추정 없음"

    def fetch(self, symbols, from_date=None, to_date=None):
        today = datetime.now(KST).date().isoformat()
        fetched = datetime.now(timezone.utc).isoformat()
        rows = [{**row, "asof_date": today, "fetched_at": fetched} for row in _fixture_rows()]
        self.planned_symbols = len({row.get("our_ticker") for row in rows})
        _record("external_calls", {"run_id": self.run_id, "calls": self.planned_symbols, "replayed": True})
        yield from rows


def _instrument(step: str, run_id: str | None, fault: dict) -> None:
    """스텝 함수 호출과 canonical 파티션 쓰기를 세고, 컨테이너 장애를 건다."""
    module = _step_module(step)
    if module is not None:
        inner = module.run

        def counted(*a, **k):
            # 시작을 먼저 남긴다 — 업무 도중 죽으면(강제 종료·OOM·예외) 끝 기록이 없어도 "시작했다"는 남는다.
            # 판정은 business_starts 로 한다("업무 실행 0" = 시작 기록 0). business_runs 는 끝까지 간 것만.
            # 장애 주입(exit)은 업무 함수를 부르지 않는다 — 실제 실행과 섞이지 않게 표시한다.
            _record("business_starts", {"step": step, "run_id": run_id, "injected_exit": fault.get("exit")})
            code = None
            try:
                if "sleep_in_step" in fault:
                    time.sleep(fault["sleep_in_step"])
                code = fault["exit"] if "exit" in fault else inner(*a, **k)
                return code
            finally:
                _record("business_runs", {"step": step, "run_id": run_id, "exit": code})
        module.run = counted

    from data_pipeline.lake import storage as lake_storage
    for name in ("put_bytes", "put_bytes_if_version"):
        original = getattr(lake_storage.S3Storage, name)

        def counted_put(self, key, *a, _original=original, **k):
            result = _original(self, key, *a, **k)
            if key.startswith("canonical/") and "investor_flow_intraday" in key:
                _record("partition_writes", {"key": key, "run_id": run_id})
            return result
        setattr(lake_storage.S3Storage, name, counted_put)


# ── 관리 명령 ──
def _connect(dbname: str):
    import psycopg
    return psycopg.connect(host=os.environ["DATA_PIPELINE_DB__HOST"], port=os.environ["DATA_PIPELINE_DB__PORT"],
                           user=os.environ["DATA_PIPELINE_DB__USER"], password=os.environ["DATA_PIPELINE_DB__PASSWORD"],
                           dbname=dbname, sslmode="require", autocommit=True)


def _verify_db() -> str:
    name = os.environ["DATA_PIPELINE_DB__NAME"]
    if name != "edge_verify":
        raise SystemExit(f"검증 DB 가 아니다: {name} — 관리 명령은 edge_verify 에만 쓴다")
    return name


def setup_db() -> int:
    name = _verify_db()
    with _connect(os.environ["VERIFY_ADMIN_DB"]) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            print(f"verify: {name} 이미 있음")
        else:
            conn.execute(f'CREATE DATABASE "{name}"')
            print(f"verify: {name} 생성")
    return 0


def seed() -> int:
    tickers = sorted({row["our_ticker"] for row in _fixture_rows()})
    with _connect(_verify_db()) as conn:
        for t in tickers:
            conn.execute("INSERT INTO entity (entity_id, entity_type, display_name) VALUES (%s, 'INSTRUMENT', %s)"
                         " ON CONFLICT DO NOTHING", (f"verify_{t}", t))
            conn.execute("INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type, currency_code)"
                         " VALUES (%s, 'XKRX', %s, 'EQUITY', 'KRW') ON CONFLICT DO NOTHING", (f"verify_{t}", t))
    print(f"verify: 종목 {len(tickers)}개 등록")
    return 0


def reset() -> int:
    with _connect(_verify_db()) as conn:
        conn.execute("TRUNCATE investor_flow_intraday, ops_task_attempt, ops_reconciliation_issue,"
                     " ops_expectation_snapshot, ops_expected_task, ops_pipeline_run CASCADE")
    deleted = 0
    for prefix in ("state/", "raw/", "canonical/", "operations_archive/", "manifests/"):
        for page in _s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                _s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys})
                deleted += len(keys)
    print(f"verify: 원장 레인 행 비움, 버킷 객체 {deleted}개 삭제(fixtures 유지)")
    return 0


def ledger() -> int:
    """검증 원장의 레인 상태를 로그로 낸다(검증 DB 는 private — 운영자는 이 태스크 로그로 읽는다)."""
    queries = {
        "runs": "SELECT run_key, pipeline_run_id, orchestrator, orchestrator_run_ref, orchestration_status,"
                " orchestration_reported_at FROM ops_pipeline_run WHERE pipeline_type = 'investor-intraday'",
        "tasks": "SELECT et.pipeline_run_id, et.task_key, et.task_outcome, et.outcome_reason FROM ops_expected_task et"
                 " JOIN ops_pipeline_run r USING (pipeline_run_id) WHERE r.pipeline_type = 'investor-intraday'",
        "attempts": "SELECT et.pipeline_run_id, et.task_key, a.record_source, a.execution_status, a.exit_code,"
                    " a.ecs_task_arn, a.orchestrator_attempt_ref, a.created_at FROM ops_task_attempt a"
                    " JOIN ops_expected_task et USING (expected_task_id) ORDER BY a.created_at",
        "holds": "SELECT dedupe_key, status, evidence->>'kind', evidence->>'reason' FROM ops_reconciliation_issue"
                 " WHERE issue_type = 'EXECUTION_HOLD' ORDER BY first_seen_at",
        "rows": "SELECT count(*), count(DISTINCT asof_slot) FROM investor_flow_intraday",
    }
    with _connect(_verify_db()) as conn:
        out = {k: [[str(v) for v in row] for row in conn.execute(q).fetchall()] for k, q in queries.items()}
    print("VERIFY_LEDGER " + json.dumps(out, ensure_ascii=False))
    return 0


ADMIN = {"verify-setup-db": setup_db, "verify-seed": seed, "verify-reset": reset, "verify-ledger": ledger}


def main(argv: list[str]) -> int:
    if argv and argv[0] in ADMIN:
        return ADMIN[argv[0]]()
    step, run_id = argv[0], _arg(argv, "--run-id")
    fault = json.loads(os.environ.get("VERIFY_FAULT") or "{}")
    _record("invocations", {"step": step, "run_id": run_id, "argv": argv, "fault": fault,
                            "skip_if_succeeded": os.environ.get("OPS_SKIP_IF_SUCCEEDED")})
    if "sleep_before" in fault:
        time.sleep(fault["sleep_before"])
    _instrument(step, run_id, fault)
    dp_run.KisInvestorEstimateSource = ReplayEstimateSource
    return dp_run.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
