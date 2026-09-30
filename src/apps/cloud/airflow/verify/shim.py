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

관리 명령(검증 절차 run.py 가 ops 태스크 정의로 띄운다. DB·역할 생성과 스키마는 dbadmin.sh 가 한다):
- `verify-seed`: 재생 입력에 나오는 종목만 instrument 로 등록한다(종목 마스터는 비교 대상이 아니다).
- `verify-reset`: 검증 원장의 레인 행과 버킷의 state·lake 를 지운다. 검증 DB 가 아니면 거부한다.
- `verify-ledger`: 검증 원장의 레인 행(런·기대 작업·시도·보류·적재 행 수)을 로그에 JSON 한 줄로 낸다.
- `verify-resolve-holds`: 종료 확인 뒤 보류 해제(README 절차 6). `verify-backup`: 검증 원장 표별 백업.
- `verify-shutdown <grace>`: 종료 장치(스케줄러가 띄움) — 서비스 0 → grace 대기 → 남은 검증 태스크 중단 → 호스트 0.
  `verify-sleep <초>`: 종료 장치 시험용 대기 태스크.
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


def resolve_holds() -> int:
    """README "보류 해제와 수동 복구" 6 — 운영자가 종료를 확인한 뒤에만 부른다(검증 절차가 ECS STOPPED 를 먼저 확인한다).
    검증 원장의 OPEN EXECUTION_HOLD 를 RESOLVED 로, 끝나지 않은 시도는 확인된 종료로 닫는다. VERIFY_EVIDENCE 에 근거."""
    evidence = os.environ.get("VERIFY_EVIDENCE") or "verify: ecs stopped confirmed"
    with _connect(_verify_db()) as conn:
        n_att = conn.execute("UPDATE ops_task_attempt SET execution_status='FAILED', finished_at=now(),"
                             " failure_reason=%s WHERE execution_status='RUNNING'",
                             (f"OPERATOR_CONFIRMED_STOPPED: {evidence}",)).rowcount
        n_hold = conn.execute("UPDATE ops_reconciliation_issue SET status='RESOLVED', resolution_source='operator',"
                              " resolution_reason=%s, updated_at=now() WHERE issue_type='EXECUTION_HOLD'"
                              " AND status='OPEN'", (f"operator_confirmed_stopped: {evidence}",)).rowcount
    print(f"verify: 보류 해제 holds={n_hold} attempts={n_att}")
    return 0


def backup() -> int:
    """검증 원장 전체(표별 COPY csv.gz)를 검증 버킷 backup/ 에 둔다 — DB 를 지우기 전 재현 근거."""
    import gzip
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    with _connect(_verify_db()) as conn:
        tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]
        for t in tables:
            buf = bytearray()
            with conn.cursor().copy(f'COPY public."{t}" TO STDOUT WITH (FORMAT csv, HEADER)') as cp:
                for chunk in cp:
                    buf += chunk
            _s3.put_object(Bucket=BUCKET, Key=f"backup/{stamp}/edge_verify/{t}.csv.gz", Body=gzip.compress(bytes(buf)))
    print(f"verify: 백업 {len(tables)}표 → s3://{BUCKET}/backup/{stamp}/edge_verify/")
    return 0


def shutdown() -> int:
    """종료 장치(verify_shutdown.tf) — 스케줄러가 띄운다. 운영자 PC 와 무관하게 검증을 끝낸다.
    Airflow 서비스 desired 0(새 제출 중단) → grace 동안 검증 태스크의 자연 종료 대기 → 남은 **검증 태스크만** StopTask
    (결과는 꾸미지 않는다 — 원장의 RUNNING 시도가 보류로 남고, 여기 목록이 종료 증거다) → 호스트 ASG 0 → 보고서.
    한 단계가 실패해도 나머지 단계와 보고서는 진행한다(실패는 보고서 errors 와 exit 1 로 드러난다)."""
    grace = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    ecs, asg = boto3.client("ecs"), boto3.client("autoscaling")
    cluster, me = os.environ["OPS_CLUSTER_ARN"], _task_arn()
    report = {"started": datetime.now(KST).isoformat(), "grace": grace, "self": me, "stopped": [], "errors": []}

    def step(name, fn):
        try:
            fn()
            report[name] = datetime.now(KST).isoformat()
        except Exception as exc:                  # 다음 단계는 계속한다
            report["errors"].append(f"{name}: {exc!r}"[:400])

    def running_verify() -> list[dict]:
        arns = [a for p in ecs.get_paginator("list_tasks").paginate(cluster=cluster, desiredStatus="RUNNING")
                for a in p["taskArns"] if a != me]
        out = []
        for i in range(0, len(arns), 100):
            resp = ecs.describe_tasks(cluster=cluster, tasks=arns[i:i + 100])
            if resp.get("failures"):          # 일부라도 못 읽었으면 "남은 태스크 없음"이 아니라 조회 실패다
                raise RuntimeError(f"describe_tasks failures: {resp['failures']}")
            # 자기 자신은 ARN 조회가 실패해도 startedBy 로 뺀다
            out += [t for t in resp["tasks"] if "-verify-" in t["taskDefinitionArn"] and t.get("startedBy") != "verify-shutdown"]
        return out

    try:
        step("service_desired_0_at", lambda: ecs.update_service(cluster=cluster, service=os.environ["VERIFY_SERVICE"],
                                                                desiredCount=0))
        # 조회 실패(None)는 "남은 태스크 없음"과 다르다 — grace 안에서 다시 보고, 끝까지 모르면 그 사실을 남긴다.
        deadline, tasks = time.time() + grace, None
        while True:
            try:
                tasks = running_verify()
            except Exception as exc:
                report["errors"].append(f"list: {exc!r}"[:400])
                tasks = None
            if tasks == [] or time.time() >= deadline:
                break
            time.sleep(20)
        if tasks is None:
            report["errors"].append("list: 남은 검증 태스크를 끝내 조회하지 못했다 — 중단 여부 미상")
            tasks = []
        for t in tasks:
            env = (t.get("overrides", {}).get("containerOverrides") or [{}])[0].get("environment") or []
            row = {"arn": t["taskArn"], "family": t["taskDefinitionArn"].rsplit("/", 1)[1], "result": "unknown",
                   "ref": next((e["value"] for e in env if e["name"] == "OPS_ORCHESTRATOR_ATTEMPT_REF"), None)}
            try:
                ecs.stop_task(cluster=cluster, task=t["taskArn"], reason="verify-shutdown: 종료 시각 강제 중단 — 결과 미상")
            except Exception as exc:
                row["stop_error"] = repr(exc)[:300]
                report["errors"].append(f"stop {t['taskArn']}: {exc!r}"[:400])
            report["stopped"].append(row)
    finally:
        step("asg_0_at", lambda: asg.update_auto_scaling_group(AutoScalingGroupName=os.environ["VERIFY_ASG"],
                                                                MinSize=0, MaxSize=0, DesiredCapacity=0))
        print("VERIFY_SHUTDOWN " + json.dumps(report, ensure_ascii=False), flush=True)
        _s3.put_object(Bucket=BUCKET, Key=f"shutdown/{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json",
                       Body=json.dumps(report, ensure_ascii=False).encode())
    return 1 if report["errors"] else 0


def sleep() -> int:
    """종료 장치 시험용 — 검증 태스크 하나를 N초 살려 둔다(업무 코드를 부르지 않는다)."""
    time.sleep(int(sys.argv[2]) if len(sys.argv) > 2 else 600)
    return 0


ADMIN = {"verify-seed": seed, "verify-reset": reset, "verify-ledger": ledger, "verify-resolve-holds": resolve_holds,
         "verify-backup": backup, "verify-shutdown": shutdown, "verify-sleep": sleep}


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
