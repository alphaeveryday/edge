"""실제 AWS 격리 검증 절차(ALPHA-1119) — 운영자 PC 에서 돈다(AWS 자격증명, session-manager-plugin).

    python verify/run.py secrets                  # 앱·검증 시크릿 값 생성(없을 때만, 값은 찍지 않는다)
    python verify/run.py dbadmin <create|clone_schema|privcheck|stats|teardown>
    python verify/run.py setup                    # 재생 입력(레이크 읽기) → DB·역할 → 스키마 복제 → 권한 확인 → 종목 등록
    python verify/run.py forward                  # Airflow API 포트 포워딩(백그라운드, 127.0.0.1:18080)
    python verify/run.py deployinfo <exp>         # 배포 설정·이미지 digest·헬스체크 방식·호스트 등록 메모리 기록
    python verify/run.py batch <exp> <B1|B2|B3>   # 고정 시나리오(verify/criteria_aws.json) 실행 + 증거 저장
    python verify/run.py idle <exp> <tag> <초>     # 유휴 관찰 창(표시만 — 관측은 호스트 관측기·CloudWatch)
    python verify/run.py rds <exp> [--since epoch] # 기존 RDS 지표를 중단 기준과 대조 + dbadmin stats
    python verify/run.py obs <exp>                # 호스트 관측 기록 수거(SSM → 검증 버킷 → 로컬)
    python verify/run.py backup <exp>             # 검증 원장 백업(검증 버킷 → 로컬)

증거는 local/results/aws/<exp>/ 에 쌓인다. 판정은 verify/analyze_aws.py 가 한다(드라이버는 판정하지 않는다).
성공 기준은 Airflow 상태 표시가 아니라 **실제 ECS 태스크 수·업무 시작 수·원장 상태·산출물 쓰기**의 대조다.
운영 자원 쓰기 없음: 업무 레이크는 읽기(재생 입력 1개), 쓰기는 검증 버킷·검증 DB(edge_verify)·Airflow 클러스터뿐.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets as pysecrets
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

KST = timezone(timedelta(hours=9))
REGION = "ap-northeast-2"
PREFIX = "edge-dev-airflow"
CLUSTER = PREFIX
DAG = "edge_investor_intraday_verify"
LANE = "investor-intraday"
PORT = 18080
API = f"http://127.0.0.1:{PORT}"
LAKE = os.environ.get("EDGE_LAKE_BUCKET", "")
HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "local" / "results" / "aws"
CRIT = json.loads((HERE / "criteria_aws.json").read_text())
_c = lambda n: boto3.client(n, region_name=REGION)  # noqa: E731
ecs, s3, logs, sm, cw, ssm, asg, ec2, sfn = (_c(n) for n in (
    "ecs", "s3", "logs", "secretsmanager", "cloudwatch", "ssm", "autoscaling", "ec2", "stepfunctions"))


def out_dir(exp: str) -> Path:
    d = RESULTS / exp
    d.mkdir(parents=True, exist_ok=True)
    return d


def mark(exp: str, event: str, **fields) -> None:
    rec = {"t": round(time.time(), 1), "kst": datetime.now(KST).strftime("%H:%M:%S"), "event": event, **fields}
    with (out_dir(exp) / "marks.jsonl").open("a") as fp:
        fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(rec, ensure_ascii=False, default=str)[:400], flush=True)


def _bucket() -> str:
    return next(b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith(f"{PREFIX}-verify-"))


# ── 시크릿(값은 어디에도 찍지 않는다) ──
def scram(password: str) -> str:
    """PostgreSQL SCRAM-SHA-256 검증자(RFC 5802/7677) — 역할 생성 SQL 에 평문 대신 넣는다.
    CREATE/ALTER ROLE 이 실패하면 서버가 문장을 오류 로그에 남긴다(log_min_error_statement=error) — 평문이 거기 남지 않게."""
    salt, it = os.urandom(16), 4096
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, it)
    client = hmac.new(salted, b"Client Key", "sha256").digest()
    server = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${it}:{b64(salt)}${b64(hashlib.sha256(client).digest())}:{b64(server)}"


def secrets(_args) -> int:
    def ensure(sid: str, keys: list[str], db_pw: str) -> None:
        try:
            cur = json.loads(sm.get_secret_value(SecretId=sid)["SecretString"])
        except sm.exceptions.ResourceNotFoundException:
            cur = {}
        missing = [k for k in keys if not cur.get(k)]
        cur.update({k: pysecrets.token_urlsafe(32) for k in missing})
        if db_pw in missing or not cur.get(f"{db_pw}_scram"):     # 비밀번호가 바뀌면 검증자도 다시 만든다
            cur[f"{db_pw}_scram"] = scram(cur[db_pw])
            missing.append(f"{db_pw}_scram")
        if missing:
            sm.put_secret_value(SecretId=sid, SecretString=json.dumps(cur))
        print(f"{sid}: 키 {sorted(cur)} (새로 만든 키 {missing})")
    ensure(f"{PREFIX}/app", ["jwt_secret", "api_secret_key", "admin_password", "meta_db_password"], "meta_db_password")
    ensure(f"{PREFIX}/verify", ["verify_db_password"], "verify_db_password")
    return 0


# ── one-off 태스크 ──
def _network(sg_name: str) -> dict:
    svc = ecs.describe_services(cluster=CLUSTER, services=[PREFIX])["services"][0]
    net = svc["networkConfiguration"]["awsvpcConfiguration"]
    sg = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [sg_name]}])["SecurityGroups"][0]
    return {"awsvpcConfiguration": {"subnets": net["subnets"], "securityGroups": [sg["GroupId"]],
                                    "assignPublicIp": "DISABLED"}}


def _one_off(family: str, command: list[str], container: str, stream_prefix: str) -> tuple[int | None, str]:
    task = ecs.run_task(cluster=CLUSTER, taskDefinition=family, launchType="FARGATE",
                        networkConfiguration=_network(f"{PREFIX}-verify"), startedBy="verify-admin",
                        overrides={"containerOverrides": [{"name": container, "command": command}]})["tasks"][0]
    arn = task["taskArn"]
    while (t := ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0])["lastStatus"] != "STOPPED":
        time.sleep(8)
    code = t["containers"][0].get("exitCode")
    stream = f"{stream_prefix}/{container}/{arn.rsplit('/', 1)[1]}"
    text = ""
    for _ in range(8):                  # awslogs 전달은 STOPPED 보다 늦을 수 있다
        try:
            text = "\n".join(e["message"] for e in logs.get_log_events(
                logGroupName=f"/ecs/{PREFIX}-verify", logStreamName=stream, startFromHead=True)["events"])
        except logs.exceptions.ResourceNotFoundException:
            text = ""
        if text:
            break
        time.sleep(5)
    if code != 0:
        print(f"{family} → exit={code} {t.get('stopCode')} {t.get('stoppedReason', '')}\n{text[-2000:]}")
    return code, text


def dbadmin_run(cmd: str) -> tuple[int | None, list[str]]:
    # 원문은 RunTask override 8,192자 제한을 넘는다 — 접어서 넘기고 컨테이너 안에서 편다.
    packed = base64.b64encode(gzip.compress((HERE / "dbadmin.sh").read_bytes())).decode()
    script = f"echo {packed} | base64 -d | gunzip > /tmp/dbadmin.sh && source /tmp/dbadmin.sh && main {cmd}"
    assert len(script) < 7000, len(script)
    code, text = _one_off(f"{PREFIX}-dbadmin", [script], "dbadmin", "dbadmin")
    return code, [line for line in text.splitlines() if line.startswith("DBADMIN")]


def dbadmin(args) -> int:
    code, lines = dbadmin_run(args.cmd)
    print("\n".join(lines))
    return 0 if code == 0 else 1


def ops(command: list[str]) -> tuple[int | None, str]:
    return _one_off(f"{PREFIX}-verify-ops", command, "data-pipeline", "ops")


def setup(_args) -> int:
    bucket = _bucket()
    if not LAKE:
        raise SystemExit("EDGE_LAKE_BUCKET(업무 레이크 이름, 읽기 전용) 필요")
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=LAKE, Prefix="raw/source=kis/dataset=investor_flow_intraday/market=KR/")
        for o in page.get("Contents", []) if o["Key"].endswith("part-00000.ndjson")]
    src = sorted(keys)[-1]
    body = s3.get_object(Bucket=LAKE, Key=src)["Body"].read()
    s3.put_object(Bucket=bucket, Key="fixtures/investor_estimate.ndjson", Body=body,
                  Metadata={"sha256": hashlib.sha256(body).hexdigest()})
    print(f"재생 입력: {src} ({len(body.splitlines())}행, sha256 {hashlib.sha256(body).hexdigest()[:12]})")
    for cmd in ("create", "clone_schema", "privcheck"):
        code, lines = dbadmin_run(cmd)
        print("\n".join(lines))
        if code != 0:
            return 1
    return 0 if ops(["verify-seed"])[0] == 0 else 1


def backup(args) -> int:
    """검증 원장 백업 → 검증 버킷 → **로컬로 내려받는다**(정리 때 검증 버킷은 지워진다)."""
    if ops(["verify-backup"])[0] != 0:
        return 1
    bucket = _bucket()
    dest = out_dir(args.exp) / "ledger-backup"
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="backup/"):
        for o in page.get("Contents", []):
            path = dest / o["Key"].removeprefix("backup/")
            path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, o["Key"], str(path))
            n += 1
    print(f"백업 {n}개 → {dest}")
    return 0 if n else 1


# ── Airflow API(SSM 포트 포워딩) ──
def _admin_password() -> str:
    return json.loads(sm.get_secret_value(SecretId=f"{PREFIX}/app")["SecretString"])["admin_password"]


def _task_ip_and_host() -> tuple[str, str]:
    arn = ecs.list_tasks(cluster=CLUSTER, serviceName=PREFIX, desiredStatus="RUNNING")["taskArns"][0]
    t = ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0]
    ip = next(d["value"] for d in t["attachments"][0]["details"] if d["name"] == "privateIPv4Address")
    ci = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=[t["containerInstanceArn"]])
    return ip, ci["containerInstances"][0]["ec2InstanceId"]


def forward(_args) -> int:
    ip, iid = _task_ip_and_host()
    subprocess.run(["pkill", "-f", f"localPortNumber={PORT}"], check=False)
    RESULTS.mkdir(parents=True, exist_ok=True)
    log = open(RESULTS / "forward.log", "ab")
    subprocess.Popen(["aws", "ssm", "start-session", "--region", REGION, "--target", iid,
                      "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
                      "--parameters", f"host={ip},portNumber=8080,localPortNumber={PORT}"],
                     stdout=log, stderr=log, start_new_session=True)
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{API}/api/v2/monitor/health", timeout=3)
            print(f"포워딩 {API} → {iid} {ip}:8080")
            return 0
        except OSError:
            time.sleep(2)
    print("포워딩 실패")
    return 1


_TOKEN: dict = {}


def api(method: str, path: str, body: dict | None = None, timeout: float = 30) -> tuple[int, dict, float]:
    started = time.monotonic()
    if not _TOKEN or _TOKEN["at"] < time.time() - 600:
        req = urllib.request.Request(f"{API}/auth/token", method="POST",
                                     data=json.dumps({"username": "admin", "password": _admin_password()}).encode(),
                                     headers={"Content-Type": "application/json"})
        _TOKEN.update(v=json.load(urllib.request.urlopen(req, timeout=timeout))["access_token"], at=time.time())
    req = urllib.request.Request(f"{API}{path}", method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {_TOKEN['v']}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return r.status, (json.loads(data) if data else {}), time.monotonic() - started
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read()[:300].decode(errors="replace")}, time.monotonic() - started


# ── 배포 정보(헬스체크 방식·설정의 계획 대조 포함) ──
def deployinfo(args) -> int:
    svc = ecs.describe_services(cluster=CLUSTER, services=[PREFIX])["services"][0]
    td = ecs.describe_task_definition(taskDefinition=svc["taskDefinition"])["taskDefinition"]
    tasks = ecs.list_tasks(cluster=CLUSTER, serviceName=PREFIX, desiredStatus="RUNNING")["taskArns"]
    running = ecs.describe_tasks(cluster=CLUSTER, tasks=tasks)["tasks"] if tasks else []
    cis = ecs.list_container_instances(cluster=CLUSTER)["containerInstanceArns"]
    inst = ecs.describe_container_instances(cluster=CLUSTER, containerInstances=cis)["containerInstances"] if cis else []
    env = {e["name"]: e["value"] for e in td["containerDefinitions"][0]["environment"]}
    info = {
        "task_definition": td["taskDefinitionArn"], "task_memory": td.get("memory"), "cpu_arch": td["runtimePlatform"],
        "containers": [{"name": c["name"], "image": c["image"], "cpu": c.get("cpu"), "memory": c.get("memory"),
                        "memoryReservation": c.get("memoryReservation"), "health": c["healthCheck"]["command"]}
                       for c in td["containerDefinitions"]],
        "settings": {k: env.get(k) for k in CRIT["deploy"]["settings"]},
        "db": {k: env.get(k) for k in ("EDGE_AIRFLOW_DB_HOST", "EDGE_AIRFLOW_DB_NAME", "EDGE_AIRFLOW_DB_USER")},
        "running_image_digests": [{c["name"]: c.get("imageDigest")} for t in running for c in t["containers"]],
        "hosts": [{"ec2": i["ec2InstanceId"], "status": i["status"], "agent": i.get("versionInfo", {}),
                   "registered": {r["name"]: r.get("integerValue") for r in i["registeredResources"]
                                  if r["name"] in ("CPU", "MEMORY")},
                   "remaining": {r["name"]: r.get("integerValue") for r in i["remainingResources"]
                                 if r["name"] in ("CPU", "MEMORY")},
                   "attributes": {a["name"]: a.get("value") for a in i.get("attributes", [])
                                  if a["name"] in ("ecs.instance-type", "ecs.cpu-architecture", "ecs.ami-id")}}
                  for i in inst],
        "service": {"desired": svc["desiredCount"], "running": svc["runningCount"], "pending": svc["pendingCount"],
                    "events": [f"{e['createdAt']} {e['message']}" for e in svc["events"][:30]]},
        # 실제 배치·기동 상태 — A1 은 이것으로 판정한다(태스크 health·컨테이너별 health·시작 시각).
        "running_tasks": [{"arn": t["taskArn"].rsplit("/", 1)[1], "health": t.get("healthStatus"),
                           "created": t.get("createdAt"), "started": t.get("startedAt"),
                           "containers": {c["name"]: c.get("healthStatus") for c in t["containers"]}} for t in running],
    }
    info["airflow_version"] = api("GET", "/api/v2/version")[1] if running else None
    want = CRIT["deploy"]["health"]
    info["health_matches_plan"] = all(
        any(want[c["name"]] in part for part in c["health"]) and not any("jobs check" in part for part in c["health"])
        for c in info["containers"])
    info["settings_match_plan"] = info["settings"] == CRIT["deploy"]["settings"]
    (out_dir(args.exp) / f"deployinfo-{datetime.now(KST):%H%M%S}.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: info[k] for k in ("task_memory", "hosts", "service", "health_matches_plan",
                                           "settings_match_plan", "airflow_version")}, ensure_ascii=False,
                     default=str)[:3500])
    return 0


# ── 시나리오 ──
def _slot(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.now(KST).replace(hour=h, minute=m, second=0, microsecond=0)


def _news_window_wait(exp: str) -> None:
    """뉴스 배치(00:10 KST) 전후에는 새 run 을 시작하지 않는다 — 업무 배치와 겹치지 않게."""
    while True:
        now = datetime.now(KST)
        if not ((now.hour == 0 and now.minute < 45) or (now.hour == 23 and now.minute >= 58)):
            return
        mark(exp, "wait_news_window")
        time.sleep(60)


def trigger(exp: str, batch: str, hhmm: str, conf: dict | None = None) -> str:
    _news_window_wait(exp)
    api("PATCH", f"/api/v2/dags/{DAG}", {"is_paused": False})     # 일정 없음 — 수동 run 만 돈다
    rid = f"aws__{exp}__{batch}__{hhmm.replace(':', '')}"
    code, body, _ = api("POST", f"/api/v2/dags/{DAG}/dagRuns",
                        {"dag_run_id": rid, "logical_date": _slot(hhmm).isoformat(), "conf": conf or {}})
    mark(exp, "trigger", run=rid, code=code, conf=conf, detail=None if code == 200 else body)
    if code != 200:
        raise RuntimeError(f"run 생성 거절 {rid}: {code} {body}")
    return rid


def clear_runs(exp: str) -> None:
    """앞 배치의 검증 DAG run 을 지운다 — (dag_id, logical_date) 가 유일 키(3.3.2)라 배치마다 같은 슬롯을 다시 못 만든다.
    그 배치의 증거(<batch>.json)가 이미 저장된 run 만 지운다 — 증거 없는 run 은 남기고 중단한다."""
    _, body, _ = api("GET", f"/api/v2/dags/{DAG}/dagRuns?limit=100")
    for r in body.get("dag_runs", []):
        rid = r["dag_run_id"]
        if r["state"] in ("running", "queued"):
            raise RuntimeError(f"초기화 전 도는 run: {rid}")
        m = re.match(r"aws__(.+)__(B\d)__\d{4}$", rid)
        f = out_dir(m[1]) / f"{m[2]}.json" if m else None
        doc = json.loads(f.read_text()) if f and f.exists() else {}
        # 파일이 있는 것만으로는 부족하다(재실행이 남긴 옛 파일·조회 실패로 빈 증거) — 이 run 의 try 와 원장이 담겼는지 본다.
        # 같은 run id 를 재실행이 다시 쓰므로 시작 시각까지 같아야 "그 run" 의 증거다.
        if not (any(x["dag_run_id"] == rid and x.get("start_date") == r.get("start_date") for x in doc.get("runs", []))
                and any(x.get("dag_run_id") == rid for x in doc.get("task_tries", [])) and doc.get("ledger")):
            raise RuntimeError(f"증거가 저장되지 않은 run 은 지우지 않는다: {rid}")
        code, _, _ = api("DELETE", f"/api/v2/dags/{DAG}/dagRuns/{urllib.parse.quote(rid, safe='')}")
        if code not in (200, 204):
            raise RuntimeError(f"run 삭제 실패 {rid}: {code}")
    mark(exp, "runs_cleared", n=len(body.get("dag_runs", [])))


def wait_run(exp: str, rid: str, timeout: float = 2400) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            code, body, _ = api("GET", f"/api/v2/dags/{DAG}/dagRuns/{urllib.parse.quote(rid, safe='')}", timeout=15)
            if code == 200 and body.get("state") in ("success", "failed"):
                mark(exp, "run_done", run=rid, state=body["state"])
                return body["state"]
        except (OSError, ValueError):
            time.sleep(10)
            try:
                forward(None)             # 재시작으로 태스크 IP 가 바뀌면 포워딩을 다시 잡는다
            except Exception:
                pass
        time.sleep(5)
    mark(exp, "run_timeout", run=rid)
    return "timeout"


def verify_tasks() -> list[dict]:
    out = []
    for status in ("RUNNING", "STOPPED"):
        arns = [a for page in ecs.get_paginator("list_tasks").paginate(cluster=CLUSTER, desiredStatus=status)
                for a in page["taskArns"]]
        for i in range(0, len(arns), 100):
            out += ecs.describe_tasks(cluster=CLUSTER, tasks=arns[i:i + 100])["tasks"]
    return [t for t in out if "-verify-" in t["taskDefinitionArn"]]


def _ref(t: dict) -> str:
    env = (t.get("overrides", {}).get("containerOverrides") or [{}])[0].get("environment") or []
    return next((e["value"] for e in env if e["name"] == "OPS_ORCHESTRATOR_ATTEMPT_REF"), "")


def _cmd(t: dict) -> str | None:
    return ((t.get("overrides", {}).get("containerOverrides") or [{}])[0].get("command") or [None])[0]


def when_running(exp: str, rid: str, step_cmd: str, after: float, action) -> threading.Thread:
    ref = f"airflow:{DAG}/{rid}/"

    def go():
        deadline = time.monotonic() + 1500
        while time.monotonic() < deadline:
            for t in verify_tasks():
                if t["lastStatus"] == "RUNNING" and _cmd(t) == step_cmd and _ref(t).startswith(ref):
                    time.sleep(after)
                    action(t["taskArn"])
                    return
            time.sleep(5)
        mark(exp, "when_running_timeout", run=rid, step=step_cmd)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def burst(exp: str) -> None:
    _, dag, _ = api("GET", f"/api/v2/dags/{DAG}")
    ftok = dag.get("file_token")
    reparse = api("PUT", f"/api/v2/parseDagFile/{ftok}")[0] if ftok else None
    mark(exp, "reparse_requested", code=reparse)
    paths = [f"/api/v2/dags/{DAG}", f"/api/v2/dags/{DAG}/dagRuns?limit=20", "/api/v2/monitor/health",
             f"/api/v2/dags/{DAG}/dagRuns/~/taskInstances?limit=50", "/api/v2/dags?limit=20",
             f"/api/v2/dags/{DAG}/details", "/api/v2/importErrors", "/api/v2/version"]
    res = []
    for i in range(30):
        code, _, sec = api("GET", paths[i % len(paths)])
        res.append([paths[i % len(paths)], code, round(sec, 3)])
    mark(exp, "burst", results=res)


def stop_task(exp: str, arn: str) -> None:
    ecs.stop_task(cluster=CLUSTER, task=arn, reason="verify: 외부 종료(StopTask) 주입")
    mark(exp, "stop_task", arn=arn)


def restart_airflow(exp: str, arn: str | None = None) -> None:
    """실제 재시작 — 서비스 강제 새 배포(min 0 / max 100: 옛 태스크를 멈춘 뒤 새 태스크)."""
    mark(exp, "restart_airflow_begin", arn=arn)
    ecs.update_service(cluster=CLUSTER, service=PREFIX, forceNewDeployment=True)
    ecs.get_waiter("services_stable").wait(cluster=CLUSTER, services=[PREFIX],
                                           WaiterConfig={"Delay": 15, "MaxAttempts": 60})
    mark(exp, "restart_airflow_end")
    forward(None)


def cf(step: str, fault: dict, tries=(1,)) -> dict:
    return {"faults": {step: {"container": {str(t): fault for t in tries}}}}


def health_poller(exp: str, stop: threading.Event) -> threading.Thread:
    """15초마다 /monitor/health(heartbeat)와 import 오류 수 — 대상이 멈춰도 기록은 로컬 파일에 남는다."""
    def go():
        with (out_dir(exp) / "health.jsonl").open("a") as fp:
            while not stop.is_set():
                rec = {"t": round(time.time(), 1)}
                try:
                    code, h, sec = api("GET", "/api/v2/monitor/health", timeout=10)
                    rec.update(code=code, sec=round(sec, 3), health=h)
                    rec["import_errors"] = api("GET", "/api/v2/importErrors", timeout=10)[1].get("total_entries")
                except Exception as exc:          # 재시작 창·포워딩 끊김 — 공백으로 남는다(판정기는 공백을 성공으로 안 본다)
                    rec["error"] = repr(exc)[:200]
                fp.write(json.dumps(rec, default=str) + "\n")
                fp.flush()
                stop.wait(15)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def batch(args) -> int:
    stop = threading.Event()
    health_poller(args.exp, stop)
    try:
        return _batch(args)
    finally:
        stop.set()


def _batch(args) -> int:
    exp, b = args.exp, args.batch
    spec = CRIT["scenarios"][b]
    mark(exp, "reset_begin", batch=b)
    clear_runs(exp)
    if ops(["verify-reset"])[0] != 0:
        return 1
    mark(exp, "batch_begin", batch=b)
    wait = CRIT["scenarios"]["normalize_wait_seconds"]
    if b in ("B1", "B3"):
        for i, hhmm in enumerate(spec["slots"]):
            rid = trigger(exp, b, hhmm, cf("normalize", {"sleep_in_step": wait}))
            if b == "B1" and i == 2:
                when_running(exp, rid, "normalize-investor-estimate", 20, lambda _a: burst(exp))
            wait_run(exp, rid)
    else:
        s = spec["slots"]
        wait_run(exp, trigger(exp, b, s["fail_confirmed"], cf("load", {"exit": 1})))
        wait_run(exp, trigger(exp, b, s["retry_not_run"], cf("collect", {"exit": 75})))
        rid = trigger(exp, b, s["hold_result_unknown"], cf("normalize", {"sleep_in_step": 90}))
        when_running(exp, rid, "normalize-investor-estimate", 30, lambda a: stop_task(exp, a))
        wait_run(exp, rid)
        rid = trigger(exp, b, s["restart_tracking"], cf("normalize", {"sleep_in_step": 150}))
        when_running(exp, rid, "normalize-investor-estimate", 30, lambda a: restart_airflow(exp, a))
        wait_run(exp, rid)
        # ECS_STATE_UNKNOWN 보류는 같은 작업(수집)의 이후 모든 run 을 막는다 — 그래서 맨 뒤에 둔다.
        rid_hold = trigger(exp, b, s["hold_state_unknown"],
                           {"faults": {"collect": {"runtask_response_lost": [1], "list_tasks_error_after_submit": [1]}}})
        wait_run(exp, rid_hold)
        # 복구: 보류가 막는지 먼저 본다(수집 76) → 앞 태스크 종료(STOPPED) 확인 → 해제 → 정상 run.
        wait_run(exp, trigger(exp, b, s["blocked_before_release"]))
        held = [t for t in verify_tasks() if _ref(t).startswith(f"airflow:{DAG}/{rid_hold}/")]
        stopped = bool(held) and all(t["lastStatus"] == "STOPPED" for t in held)
        mark(exp, "release_check", held_tasks=[(t["taskArn"].rsplit("/", 1)[1], _cmd(t), t["lastStatus"],
                                               t["containers"][0].get("exitCode")) for t in held], all_stopped=stopped)
        if stopped:
            ops(["verify-resolve-holds"])
            mark(exp, "hold_released")
            wait_run(exp, trigger(exp, b, s["after_release"]))
        else:
            mark(exp, "hold_not_released", why="앞선 태스크 종료를 확인하지 못했다 — 해제하지 않는다")
    evidence(exp, b)
    mark(exp, "batch_end", batch=b)
    return 0


def evidence(exp: str, b: str) -> None:
    """배치 증거 — ECS(멈춘 태스크는 약 1시간만 조회된다: 배치 직후 수집), Airflow try 이력·보류, shim 계수, 원장."""
    tag = f"/aws__{exp}__{b}__"
    tasks = [t for t in verify_tasks() if tag in _ref(t)]
    _, runs, _ = api("GET", f"/api/v2/dags/{DAG}/dagRuns?limit=100")
    runs = [r for r in runs.get("dag_runs", []) if r["dag_run_id"].startswith(f"aws__{exp}__{b}__")]
    tries, holds = [], []
    for r in runs:
        rid = urllib.parse.quote(r["dag_run_id"], safe="")
        _, body, _ = api("GET", f"/api/v2/dags/{DAG}/dagRuns/{rid}/taskInstances")
        for ti in body.get("task_instances", []):
            _, tr, _ = api("GET", f"/api/v2/dags/{DAG}/dagRuns/{rid}/taskInstances/{ti['task_id']}/tries")
            tries += [{k: x.get(k) for k in ("dag_run_id", "task_id", "try_number", "state", "queued_when",
                                              "start_date", "end_date")} for x in tr.get("task_instances", [])]
            code, xc, _ = api("GET", f"/api/v2/dags/{DAG}/dagRuns/{rid}/taskInstances/{ti['task_id']}/xcomEntries/hold")
            if code == 200 and xc.get("value"):
                holds.append({"dag_run_id": r["dag_run_id"], "task_id": ti["task_id"], "hold": xc["value"]})
    bucket = _bucket()
    state = {}
    for kind in ("business_starts", "business_runs", "partition_writes", "external_calls"):
        objs = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"state/{kind}/")
                for o in page.get("Contents", [])]
        state[kind] = [r for r in (json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read()) for k in objs)
                       if tag in (r.get("attempt_ref") or "")]
    _, text = ops(["verify-ledger"])
    ledger = next((json.loads(line.removeprefix("VERIFY_LEDGER ")) for line in text.splitlines()
                   if line.startswith("VERIFY_LEDGER ")), None)
    doc = {"batch": b, "runs": runs, "task_tries": tries, "airflow_holds": holds, "state": state, "ledger": ledger,
           "ecs_tasks": [{"arn": t["taskArn"].rsplit("/", 1)[1], "ref": _ref(t), "startedBy": t.get("startedBy"),
                          "cmd": _cmd(t), "last": t["lastStatus"], "stopCode": t.get("stopCode"),
                          "exit": t["containers"][0].get("exitCode"), "createdAt": t.get("createdAt"),
                          "startedAt": t.get("startedAt"), "stoppedAt": t.get("stoppedAt")} for t in tasks]}
    (out_dir(exp) / f"{b}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
    mark(exp, "evidence", batch=b, ecs_tasks=len(tasks), runs=len(runs), ledger=ledger is not None)


def idle(args) -> int:
    stop = threading.Event()
    health_poller(args.exp, stop)
    mark(args.exp, "idle_begin", tag=args.tag, seconds=args.seconds)
    time.sleep(args.seconds)
    mark(args.exp, "idle_end", tag=args.tag)
    stop.set()
    return 0


# ── 기존 RDS 대조(중단 기준) ──
def rds(args) -> int:
    since = (datetime.fromtimestamp(float(args.since), timezone.utc) if args.since
             else datetime.now(timezone.utc) - timedelta(minutes=30))
    end = datetime.now(timezone.utc)
    out: dict = {"from": since.astimezone(KST).isoformat(), "to": end.astimezone(KST).isoformat()}
    stops = CRIT["rds_stop"]
    for m, st in (("FreeableMemory", "Minimum"), ("SwapUsage", "Maximum"), ("CPUUtilization", "Maximum"),
                  ("DatabaseConnections", "Maximum"), ("WriteLatency", "Maximum"), ("ReadLatency", "Maximum"),
                  ("CPUCreditBalance", "Minimum")):
        pts = sorted(cw.get_metric_statistics(Namespace="AWS/RDS", MetricName=m, StartTime=since, EndTime=end,
                                              Period=60, Statistics=[st],
                                              Dimensions=[{"Name": "DBInstanceIdentifier", "Value": "edge-dev"}])[
                         "Datapoints"], key=lambda x: x["Timestamp"])
        scale = 2 ** 20 if m in ("FreeableMemory", "SwapUsage") else (0.001 if "Latency" in m else 1)
        vals = [round(p[st] / scale, 2) for p in pts]
        out[m] = {"n": len(vals), "min": min(vals, default=None), "max": max(vals, default=None), "series": vals}

    def sustained(vals, pred, k):
        run = 0
        for v in vals:
            run = run + 1 if pred(v) else 0
            if run >= k:
                return True
        return False
    lat = [max(a, b) for a, b in zip(out["WriteLatency"]["series"], out["ReadLatency"]["series"])]
    out["stop"] = {
        "freeable": sustained(out["FreeableMemory"]["series"], lambda v: v < stops["freeable_mib_below"], 3),
        "swap": sustained(out["SwapUsage"]["series"], lambda v: v > stops["swap_mib_above"], 3),
        "cpu": sustained(out["CPUUtilization"]["series"], lambda v: v > stops["cpu_pct_above"], 5),
        "latency": sustained(lat, lambda v: v > stops["latency_ms_above"], 3),
        "connections": sustained(out["DatabaseConnections"]["series"], lambda v: v > stops["connections_above"], 2),
    }
    # 창의 분 수 대비 90% 미만인 지표가 하나라도 있으면 계측 공백(= 판정 불가) — 빈 지표가 "중단 없음"이 되지 않게.
    minutes = max(1, int((end - since).total_seconds() // 60) - 2)
    out["coverage"] = {m: round(out[m]["n"] / minutes, 2) for m in ("FreeableMemory", "SwapUsage", "CPUUtilization",
                                                                    "DatabaseConnections", "WriteLatency", "ReadLatency")}
    out["data_gap"] = any(v < 0.9 for v in out["coverage"].values())
    # Airflow 쪽 연결 오류·풀 대기 초과(구성요소 로그)
    pattern = '?OperationalError ?QueuePool ?"too many clients" ?"password authentication failed" ?"connection refused"'
    try:
        ev = logs.filter_log_events(logGroupName=f"/ecs/{PREFIX}", startTime=int(since.timestamp() * 1000),
                                    endTime=int(end.timestamp() * 1000), filterPattern=pattern, limit=50)["events"]
        out["airflow_db_error_logs"] = [e["message"][:200] for e in ev]
    except logs.exceptions.ResourceNotFoundException:
        out["airflow_db_error_logs"] = None
    if not args.no_stats:
        _, lines = dbadmin_run("stats")
        out["dbadmin_stats"] = [json.loads(line.removeprefix("DBADMIN stats ")) for line in lines
                                if line.startswith("DBADMIN stats ")]
    failed = []
    for smn in sfn.list_state_machines()["stateMachines"]:
        if smn["name"].startswith("edge-dev-data-pipeline"):
            failed += [e["name"] for e in sfn.list_executions(stateMachineArn=smn["stateMachineArn"],
                                                               statusFilter="FAILED", maxResults=20)["executions"]
                       if (e.get("stopDate") or e["startDate"]) >= since]  # 창 안에 끝난 실패(창 전에 시작한 것 포함)
    out["business_failed_sfn"] = failed
    (out_dir(args.exp) / f"rds-{datetime.now(KST):%H%M%S}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str))
    brief = {k: v for k, v in out.items() if k not in ("dbadmin_stats",) and not isinstance(v, dict)}
    brief.update({m: {k: v for k, v in out[m].items() if k != "series"} for m in (
        "FreeableMemory", "SwapUsage", "CPUUtilization", "DatabaseConnections", "WriteLatency", "ReadLatency",
        "CPUCreditBalance")})
    brief["stop"] = out["stop"]
    print(json.dumps(brief, ensure_ascii=False, default=str))
    if out.get("dbadmin_stats"):
        print(json.dumps(out["dbadmin_stats"][-1].get("sessions"), ensure_ascii=False))
    return 2 if any(out["stop"].values()) or failed or out["data_gap"] or out.get("airflow_db_error_logs") else 0


# ── 호스트 관측 기록 수거 ──
def obs(args) -> int:
    inst = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[f"{PREFIX}-host"])["AutoScalingGroups"][0]["Instances"]
    if not inst:
        print("호스트 없음")
        return 1
    iid = inst[0]["InstanceId"]
    bucket = _bucket()
    prefix = f"obs/{args.exp}/{datetime.now(KST):%H%M%S}"
    cid = ssm.send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript", OutputS3BucketName=bucket,
                           OutputS3KeyPrefix=prefix,
                           Parameters={"commands": ["tar czf - -C /var/log edge-obs | base64 -w0"]})["Command"]["CommandId"]
    inv = {}
    for _ in range(100):
        time.sleep(3)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            break
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
            if o["Key"].endswith("stdout")]
    if inv.get("Status") != "Success" or not keys:
        print(f"수거 실패: {inv.get('Status')} {inv.get('StandardErrorContent', '')[:300]}")
        return 1
    raw = base64.b64decode(s3.get_object(Bucket=bucket, Key=keys[0])["Body"].read())
    dest = out_dir(args.exp) / "host-obs" / iid
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        tf.extractall(dest)
    print(f"수거: {iid} → {dest} ({len(raw)} bytes)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="name", required=True)
    for name in ("secrets", "setup", "forward"):
        sub.add_parser(name)
    sub.add_parser("backup").add_argument("exp")
    d = sub.add_parser("dbadmin")
    d.add_argument("cmd")
    for name in ("deployinfo", "obs"):
        sub.add_parser(name).add_argument("exp")
    b = sub.add_parser("batch")
    b.add_argument("exp")
    b.add_argument("batch", choices=("B1", "B2", "B3"))
    i = sub.add_parser("idle")
    i.add_argument("exp")
    i.add_argument("tag")
    i.add_argument("seconds", type=int)
    r = sub.add_parser("rds")
    r.add_argument("exp")
    r.add_argument("--since", default="")
    r.add_argument("--no-stats", action="store_true")
    args = p.parse_args()
    return {"secrets": secrets, "dbadmin": dbadmin, "setup": setup, "forward": forward, "backup": backup,
            "deployinfo": deployinfo, "obs": obs, "batch": batch, "idle": idle, "rds": rds}[args.name](args)


if __name__ == "__main__":
    sys.exit(main())
