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
- `verify-shutdown <grace>`: 종료 장치(스케줄러가 띄움) — 서비스 0 → grace 대기 → 남은 검증 태스크 중단 → 관측 기록 전송 → 호스트 0.
- `verify-watchdog <HH:MM> <rds_stop JSON>`: 실험 중 감시(PC 무관). 중단 기준·감시 상실이면 위 종료 절차를 바로 부른다.
  업무 스텝은 감시 심장박동이 2분 넘게 없으면 시작하지 않는다(exit 75).
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


def shutdown(grace: int | None = None, reason: str = "schedule") -> int:
    """종료 장치(verify_shutdown.tf) — 스케줄러가 띄운다. 운영자 PC 와 무관하게 검증을 끝낸다.
    Airflow 서비스 desired 0(새 제출 중단) → grace 동안 검증 태스크의 자연 종료 대기 → 남은 **검증 태스크만** StopTask
    (결과는 꾸미지 않는다 — 원장의 RUNNING 시도가 보류로 남고, 여기 목록이 종료 증거다) → 호스트 ASG 0 → 보고서.
    한 단계가 실패해도 나머지 단계와 보고서는 진행한다(실패는 보고서 errors 와 exit 1 로 드러난다)."""
    if grace is None:
        grace = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    ecs, asg = boto3.client("ecs"), boto3.client("autoscaling")
    cluster, me = os.environ["OPS_CLUSTER_ARN"], _task_arn()
    report = {"started": datetime.now(KST).isoformat(), "grace": grace, "reason": reason, "self": me,
              "stopped": [], "errors": []}

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
            out += [t for t in resp["tasks"] if "-verify-" in t["taskDefinitionArn"]
                    and t.get("startedBy") not in ("verify-shutdown", "verify-watchdog")]
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
        # 호스트를 내리기 전에 관측 기록(/var/log/edge-obs — health·메모리·커널·docker)을 버킷으로 보낸다. 운영자 PC 가
        # 잠들어 수거하지 못해도 A2~A4 증거가 호스트와 함께 사라지지 않게. 실패해도 호스트는 내린다.
        step("host_obs_shipped_at", lambda: _ship_host_obs(asg, report))
        step("asg_0_at", lambda: asg.update_auto_scaling_group(AutoScalingGroupName=os.environ["VERIFY_ASG"],
                                                                MinSize=0, MaxSize=0, DesiredCapacity=0))
        print("VERIFY_SHUTDOWN " + json.dumps(report, ensure_ascii=False), flush=True)
        _s3.put_object(Bucket=BUCKET, Key=f"shutdown/{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json",
                       Body=json.dumps(report, ensure_ascii=False).encode())
    return 1 if report["errors"] else 0


def _ship_host_obs(asg, report: dict, wait_seconds: int = 180) -> None:
    """ASG 호스트마다 SSM 으로 edge-obs 를 tar·base64 해 검증 버킷 obs/shutdown/ 에 남긴다(run.py obs 와 같은 형식)."""
    ssm = boto3.client("ssm")
    groups = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[os.environ["VERIFY_ASG"]])["AutoScalingGroups"]
    ids = [i["InstanceId"] for g in groups for i in g["Instances"]]
    if not ids:
        report["host_obs"] = "호스트 없음"
        return
    prefix = f"obs/shutdown/{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    cid = ssm.send_command(InstanceIds=ids, DocumentName="AWS-RunShellScript", OutputS3BucketName=BUCKET,
                           OutputS3KeyPrefix=prefix,
                           Parameters={"commands": ["tar czf - -C /var/log edge-obs | base64 -w0"]})["Command"]["CommandId"]
    status, deadline = {}, time.time() + wait_seconds
    while time.time() < deadline and len(status) < len(ids):
        time.sleep(5)
        for iid in ids:
            try:
                st = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)["Status"]
            except ssm.exceptions.InvocationDoesNotExist:
                continue
            if st in ("Success", "Failed", "Cancelled", "TimedOut"):
                status[iid] = st
    report["host_obs"] = {"prefix": prefix, "command": cid, "status": status}
    if any(status.get(i) != "Success" for i in ids):
        raise RuntimeError(f"관측 기록 전송 미완: {status}")


# ── 실험 중 감시(ALPHA-1119 A4·A5 재검증) ──
# 운영자 PC·포트 포워딩과 무관하게 AWS 안에서 중단 기준을 본다. 기준을 넘거나 감시가 3회 연속 실패하면 종료 장치와
# 같은 절차(서비스 0 → 검증 태스크 중단 → 관측 기록 전송 → 호스트 0)를 바로 부른다. 업무 스텝은 이 감시의 심장박동이
# 2분 넘게 끊기면 시작하지 않는다(exit 75) — 감시가 죽으면 검증 부하도 멈춘다.
WATCH_KEY = "state/watchdog/heartbeat.json"
WATCH_FRESH_SECONDS = 120
WATCH_LOSS_LIMIT = 3
HOST_MEM_MIN_MIB = 64
HOST_PROBE = r"""f=/var/log/edge-obs; now=$(date +%s)
awk -v since=$((now-90)) '/^T /{t=$2} /^M MemAvailable:/{if(t>=since && (m==""||$3<m)) m=$3} END{print "memavail_min_kb", (m==""?-1:m)}' $f/samples.log
echo "sample_age $(( now - $(grep '^T ' $f/samples.log | tail -1 | cut -d' ' -f2) ))"
echo "kmsg_oom $(grep -ciE 'out of memory|oom-kill|oom_kill_process' $f/kmsg.log)"
echo "docker_oom $(grep -c '"Action":"oom"' $f/docker-events.log)"
echo "cg_oom_kill $(tail -n 4000 $f/samples.log | grep -o 'ev_oom_kill=[0-9]*' | cut -d= -f2 | sort -n | tail -1)"
"""


def _host_trip(probe: dict, baseline_kmsg: int) -> str | None:
    """호스트 관측 한 번 → 중단 사유(없으면 None). 관측기가 멈췄으면(표본 60초 초과) 관측 실패로 올린다."""
    if probe.get("sample_age", 10 ** 9) > 60:
        raise RuntimeError(f"호스트 관측 표본이 {probe.get('sample_age')}초 묵었다")
    if 0 <= probe.get("memavail_min_kb", -1) < HOST_MEM_MIN_MIB * 1024:
        return f"호스트 MemAvailable {probe['memavail_min_kb'] // 1024}MiB < {HOST_MEM_MIN_MIB}"
    if probe.get("memavail_min_kb", -1) < 0:
        raise RuntimeError("최근 90초 MemAvailable 표본 없음")
    if probe.get("kmsg_oom", 0) > baseline_kmsg or probe.get("docker_oom", 0) > 0 or probe.get("cg_oom_kill", 0) > 0:
        return f"OOM 흔적 kmsg={probe.get('kmsg_oom')}(기준 {baseline_kmsg}) docker={probe.get('docker_oom')} cgroup={probe.get('cg_oom_kill')}"
    return None


def _sustained(vals: list, pred, k: int) -> bool:
    run = 0
    for v in vals:
        run = run + 1 if pred(v) else 0
        if run >= k:
            return True
    return False


def _rds_trip(series: dict, stops: dict) -> str | None:
    """분 단위 RDS 지표(MiB·%·ms·개) → 중단 사유. 기준은 실행기가 criteria 의 rds_stop 을 그대로 넘긴다."""
    lat = [max(a, b) for a, b in zip(series.get("WriteLatency", []), series.get("ReadLatency", []))]
    k = stops["sustain_minutes"]
    checks = {"freeable": (series.get("FreeableMemory", []), lambda v: v < stops["freeable_mib_below"], k["freeable"]),
              "swap": (series.get("SwapUsage", []), lambda v: v > stops["swap_mib_above"], k["swap"]),
              "cpu": (series.get("CPUUtilization", []), lambda v: v > stops["cpu_pct_above"], k["cpu"]),
              "latency": (lat, lambda v: v > stops["latency_ms_above"], k["latency"]),
              "connections": (series.get("DatabaseConnections", []), lambda v: v > stops["connections_above"], k["connections"])}
    hit = [name for name, (vals, pred, n) in checks.items() if _sustained(vals, pred, n)]
    return f"RDS 중단 기준 {hit}" if hit else None


def watchdog() -> int:
    """`verify-watchdog <HH:MM 종료> <rds_stop JSON>` — 실행기가 본 실험 전에 띄운다(startedBy verify-watchdog)."""
    until_hm, stops = sys.argv[2], json.loads(sys.argv[3])
    ecs, ssm, cw, sfn = (boto3.client(n) for n in ("ecs", "ssm", "cloudwatch", "stepfunctions"))
    asg = boto3.client("autoscaling")
    cluster, service = os.environ["OPS_CLUSTER_ARN"], os.environ["VERIFY_SERVICE"]
    started = datetime.now(timezone.utc)
    losses = {"host": 0, "rds": 0, "sfn": 0, "service": 0}
    baseline_kmsg, service_tasks, last = None, None, {}

    def host_probe() -> dict:
        ids = [i["InstanceId"] for g in asg.describe_auto_scaling_groups(
            AutoScalingGroupNames=[os.environ["VERIFY_ASG"]])["AutoScalingGroups"] for i in g["Instances"]]
        if len(ids) != 1:
            raise RuntimeError(f"호스트 {len(ids)}대")
        cid = ssm.send_command(InstanceIds=ids, DocumentName="AWS-RunShellScript",
                               Parameters={"commands": [HOST_PROBE]})["Command"]["CommandId"]
        for _ in range(12):
            time.sleep(3)
            try:
                inv = ssm.get_command_invocation(CommandId=cid, InstanceId=ids[0])
            except ssm.exceptions.InvocationDoesNotExist:
                continue
            if inv["Status"] == "Success":
                return {k: int(v) for k, v in (line.split() for line in inv["StandardOutputContent"].splitlines()
                                               if len(line.split()) == 2 and line.split()[1].lstrip("-").isdigit())}
            if inv["Status"] in ("Failed", "Cancelled", "TimedOut"):
                break
        raise RuntimeError("호스트 관측 명령 실패")

    def rds_series() -> dict:
        end = datetime.now(timezone.utc)
        out = {}
        for m, st, scale in (("FreeableMemory", "Minimum", 2 ** 20), ("SwapUsage", "Maximum", 2 ** 20),
                             ("CPUUtilization", "Maximum", 1), ("DatabaseConnections", "Maximum", 1),
                             ("WriteLatency", "Maximum", 0.001), ("ReadLatency", "Maximum", 0.001)):
            pts = sorted(cw.get_metric_statistics(Namespace="AWS/RDS", MetricName=m, StartTime=end - timedelta(minutes=8),
                                                  EndTime=end, Period=60, Statistics=[st],
                                                  Dimensions=[{"Name": "DBInstanceIdentifier", "Value": "edge-dev"}])["Datapoints"],
                         key=lambda x: x["Timestamp"])
            out[m] = [p[st] / scale for p in pts]
        if not out["FreeableMemory"]:
            raise RuntimeError("RDS 지표 없음")
        return out

    def sfn_failed() -> list:
        failed = []
        for smn in sfn.list_state_machines()["stateMachines"]:
            if smn["name"].startswith("edge-dev-data-pipeline"):
                failed += [e["name"] for e in sfn.list_executions(stateMachineArn=smn["stateMachineArn"],
                                                                   statusFilter="FAILED", maxResults=20)["executions"]
                           if (e.get("stopDate") or e["startDate"]) >= started]
        return failed

    def running_service_tasks() -> set:
        return set(ecs.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING")["taskArns"])

    trip = None
    while trip is None and datetime.now(KST).strftime("%H:%M") < until_hm:
        for name, fn in (("host", host_probe), ("rds", rds_series), ("sfn", sfn_failed), ("service", running_service_tasks)):
            try:
                val = fn()
                losses[name] = 0
                if name == "host":
                    baseline_kmsg = val.get("kmsg_oom", 0) if baseline_kmsg is None else baseline_kmsg
                    trip = trip or _host_trip(val, baseline_kmsg)
                elif name == "rds":
                    trip = trip or _rds_trip(val, stops)
                elif name == "sfn":
                    trip = trip or (f"창 안 업무 SFN 실패 {val}" if val else None)
                else:
                    service_tasks = val if service_tasks is None else service_tasks
                    trip = trip or (f"Airflow 서비스 태스크 교체 {sorted(service_tasks)} → {sorted(val)}"
                                    if val != service_tasks else None)
                last[name] = val if name != "service" else sorted(val)
            except Exception as exc:
                losses[name] += 1
                last[name] = f"error: {exc!r}"[:300]
                if losses[name] >= WATCH_LOSS_LIMIT:
                    trip = f"감시 상실({name} {losses[name]}회 연속 실패) — 감시 없이 계속하지 않는다"
        beat = {"t": time.time(), "at": datetime.now(KST).isoformat(), "trip": trip, "losses": losses,
                "last": last, "task": _task_arn()}
        _s3.put_object(Bucket=BUCKET, Key=WATCH_KEY, Body=json.dumps(beat, ensure_ascii=False, default=str).encode())
        print("VERIFY_WATCH " + json.dumps(beat, ensure_ascii=False, default=str), flush=True)
        if trip is None:
            time.sleep(30)
    if trip:
        _record("watchdog_trips", {"reason": trip})
        return shutdown(grace=60, reason=f"watchdog: {trip}")
    return 0


def _watch_gate() -> str | None:
    """업무 스텝 시작 전 — 감시 심장박동이 신선하고 중단이 없어야 한다. 아니면 거부 사유."""
    try:
        beat = json.loads(_s3.get_object(Bucket=BUCKET, Key=WATCH_KEY)["Body"].read())
    except Exception as exc:
        return f"감시 심장박동 없음({type(exc).__name__})"
    if beat.get("trip"):
        return f"감시가 중단을 선언했다: {beat['trip']}"
    if time.time() - beat.get("t", 0) > WATCH_FRESH_SECONDS:
        return f"감시 심장박동이 {int(time.time() - beat.get('t', 0))}초 묵었다"
    return None


def sleep() -> int:
    """종료 장치 시험용 — 검증 태스크 하나를 N초 살려 둔다(업무 코드를 부르지 않는다)."""
    time.sleep(int(sys.argv[2]) if len(sys.argv) > 2 else 600)
    return 0


ADMIN = {"verify-seed": seed, "verify-reset": reset, "verify-ledger": ledger, "verify-resolve-holds": resolve_holds,
         "verify-backup": backup, "verify-shutdown": shutdown, "verify-sleep": sleep, "verify-watchdog": watchdog}


def main(argv: list[str]) -> int:
    if argv and argv[0] in ADMIN:
        return ADMIN[argv[0]]()
    step, run_id = argv[0], _arg(argv, "--run-id")
    refused = _watch_gate()
    if refused:
        # 업무를 시작하지 않았다(75 = 미실행, 재시도 대상) — 감시 없이 검증 부하를 걸지 않는다.
        _record("watchdog_gate", {"step": step, "run_id": run_id, "reason": refused})
        print(f"VERIFY_WATCH_GATE {step}: {refused}", flush=True)
        return 75
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
