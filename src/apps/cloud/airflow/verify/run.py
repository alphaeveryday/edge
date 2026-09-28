"""격리 검증 절차(ALPHA-1119) — 운영자 PC 에서 돈다. AWS 자격증명과 Airflow API(SSM 포트 포워딩)가 필요하다.

    python verify/run.py setup                 # 재생 입력·검증 DB·스키마·종목 등록(한 번)
    python verify/run.py reset                 # 검증 원장 레인 행·버킷 state/lake 비우기(시나리오마다)
    python verify/run.py trigger --slot 10:05 [--conf '{"faults": ...}']
    python verify/run.py evidence --slot 10:05 --dag-run <dag_run_id>   # ECS 태스크 수·업무 실행 수·원장·산출물 쓰기

성공 기준은 Airflow 상태가 아니라 **네 가지 대조**다: 실제 ECS 태스크 수(검증 클러스터, 이 run 의 --run-id),
업무 실행 수(shim 의 business_starts — wrapper 가 보류·skip 하면 0), 원장 상태(검증 DB), 산출물 쓰기(partition_writes).
운영 자원은 쓰지 않는다: 업무 레이크는 **읽기만**(재생 입력 1개 복사 원본), 쓰기는 검증 버킷·검증 DB·검증 클러스터뿐.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

KST = timezone(timedelta(hours=9))
REGION = "ap-northeast-2"
PREFIX = "edge-dev-airflow"
CLUSTER = PREFIX
DAG = "edge_investor_intraday_verify"
LANE = "investor-intraday"
AIRFLOW = os.environ.get("AIRFLOW_URL", "http://127.0.0.1:18080")
LAKE = os.environ.get("EDGE_LAKE_BUCKET", "")          # 업무 레이크(읽기 전용) — 재생 입력 원본
ecs, s3, logs = (boto3.client(n, region_name=REGION) for n in ("ecs", "s3", "logs"))


def _bucket() -> str:
    return next(b["Name"] for b in s3.list_buckets()["Buckets"] if b["Name"].startswith(f"{PREFIX}-verify-"))


def _network() -> dict:
    svc = ecs.describe_services(cluster=CLUSTER, services=[PREFIX])["services"][0]
    net = svc["networkConfiguration"]["awsvpcConfiguration"]
    verify_sg = next(g for g in boto3.client("ec2", region_name=REGION).describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [f"{PREFIX}-verify"]}])["SecurityGroups"])["GroupId"]
    return {"awsvpcConfiguration": {"subnets": net["subnets"], "securityGroups": [verify_sg],
                                    "assignPublicIp": "DISABLED"}}


def _one_off(family: str, command: list[str] | None, container: str = "data-pipeline") -> tuple[int, str]:
    """검증 클러스터에서 one-off 태스크 하나를 끝까지 돌려 (exit, 로그) 를 준다."""
    overrides = {"containerOverrides": [{"name": container, "command": command}]} if command else {}
    task = ecs.run_task(cluster=CLUSTER, taskDefinition=family, launchType="FARGATE",
                        networkConfiguration=_network(), overrides=overrides,
                        startedBy="verify-admin")["tasks"][0]
    arn = task["taskArn"]
    while (t := ecs.describe_tasks(cluster=CLUSTER, tasks=[arn])["tasks"][0])["lastStatus"] != "STOPPED":
        time.sleep(10)
    code = t["containers"][0].get("exitCode")
    stream = f"{'migrate' if container == 'migrate' else 'ops'}/{container}/{arn.rsplit('/', 1)[1]}"
    text = ""
    for _ in range(6):                  # awslogs 전달은 STOPPED 보다 늦을 수 있다
        try:
            text = "\n".join(e["message"] for e in logs.get_log_events(
                logGroupName=f"/ecs/{PREFIX}-verify", logStreamName=stream, startFromHead=True)["events"])
        except logs.exceptions.ResourceNotFoundException:
            text = ""
        if text:
            break
        time.sleep(5)
    print(f"{family} {command} → exit={code} stop={t.get('stopCode')} {t.get('stoppedReason', '')}")
    return code, text


def setup(_args) -> int:
    bucket = _bucket()
    # 1) 재생 입력: 업무 레이크의 최신 장중 수급 raw 하나를 **읽어** 검증 버킷에 둔다(레이크에는 쓰지 않는다).
    if not LAKE:
        raise SystemExit("EDGE_LAKE_BUCKET(업무 레이크 이름, 읽기 전용) 필요")
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=LAKE, Prefix="raw/source=kis/dataset=investor_flow_intraday/market=KR/")
        for o in page.get("Contents", []) if o["Key"].endswith("part-00000.ndjson")]
    src = sorted(keys)[-1]
    body = s3.get_object(Bucket=LAKE, Key=src)["Body"].read()
    s3.put_object(Bucket=bucket, Key="fixtures/investor_estimate.ndjson", Body=body,
                  Metadata={"source-key": src[-200:], "sha256": hashlib.sha256(body).hexdigest()})
    print(f"재생 입력: {src} ({len(body.splitlines())}행) → s3://{bucket}/fixtures/")
    # 2) 검증 DB 생성 → 스키마(업무와 같은 migrations-cloud, 지금 dev 에 배포된 schema-migrate 이미지) → 종목 등록.
    code, _ = _one_off(f"{PREFIX}-verify-ops", ["verify-setup-db"])
    if code != 0:
        return 1
    base = ecs.describe_task_definition(taskDefinition=f"{PREFIX}-verify-ops")["taskDefinition"]
    env = {e["name"]: e["value"] for e in base["containerDefinitions"][0]["environment"]}
    image = ecs.describe_task_definition(taskDefinition="edge-dev-schema-migrate")["taskDefinition"][
        "containerDefinitions"][0]["image"]
    secret = next(s["valueFrom"] for s in base["containerDefinitions"][0]["secrets"])
    family = ecs.register_task_definition(
        family=f"{PREFIX}-verify-migrate", requiresCompatibilities=["FARGATE"], networkMode="awsvpc",
        cpu="256", memory="512", executionRoleArn=base["executionRoleArn"], taskRoleArn=base["taskRoleArn"],
        runtimePlatform={"operatingSystemFamily": "LINUX", "cpuArchitecture": "X86_64"},
        containerDefinitions=[{
            "name": "migrate", "image": image, "essential": True, "command": ["migrate"],
            "environment": [
                {"name": "FLYWAY_URL", "value": f"jdbc:postgresql://{env['DATA_PIPELINE_DB__HOST']}:"
                                               f"{env['DATA_PIPELINE_DB__PORT']}/{env['DATA_PIPELINE_DB__NAME']}"},
                {"name": "FLYWAY_USER", "value": env["DATA_PIPELINE_DB__USER"]}],
            "secrets": [{"name": "FLYWAY_PASSWORD", "valueFrom": secret}],
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": f"/ecs/{PREFIX}-verify", "awslogs-region": REGION,
                "awslogs-stream-prefix": "migrate"}}}],
    )["taskDefinition"]["taskDefinitionArn"]
    print(f"스키마 이미지 {image}")
    code, _ = _one_off(family, None, container="migrate")
    if code != 0:
        return 1
    code, _ = _one_off(f"{PREFIX}-verify-ops", ["verify-seed"])
    return code


def reset(_args) -> int:
    return _one_off(f"{PREFIX}-verify-ops", ["verify-reset"])[0]


def _slot(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.now(KST).replace(hour=h, minute=m, second=0, microsecond=0)


def _run_id(slot: datetime) -> str:
    """edge_batch.pipeline_run_id 와 같은 값(검증 절차에 Airflow 를 설치하지 않으려고 옮겼다)."""
    material = "\x01".join(["alphamale-etf-daily-v1", f"{LANE}:{slot.strftime('%Y-%m-%dT%H:%M')}"])
    return f"run_{hashlib.sha256(material.encode()).hexdigest()[:26]}"


def _api(method: str, path: str, body: dict | None = None) -> dict:
    token = json.load(urllib.request.urlopen(urllib.request.Request(
        f"{AIRFLOW}/auth/token", data=json.dumps({"username": "admin", "password": os.environ["AIRFLOW_PASSWORD"]})
        .encode(), headers={"Content-Type": "application/json"})))["access_token"]
    req = urllib.request.Request(f"{AIRFLOW}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def trigger(args) -> int:
    # 검증 DAG 는 pause 로 생성된다(dags_are_paused_at_creation). 일정이 없으니 풀어도 수동 run 만 돈다.
    _api("PATCH", f"/api/v2/dags/{DAG}", {"is_paused": False})
    slot = _slot(args.slot)             # 한 번만 — 자정을 넘기면 logical_date 와 run_id 의 날짜가 갈린다
    run = _api("POST", f"/api/v2/dags/{DAG}/dagRuns",
               {"logical_date": slot.isoformat(), "conf": json.loads(args.conf)})
    print(json.dumps({"dag_run_id": run["dag_run_id"], "pipeline_run_id": _run_id(slot)}))
    return 0


def evidence(args) -> int:
    # 슬롯이 같으면 run_id 가 같다(결정적) — 같은 슬롯을 여러 시나리오에서 쓰므로 DAG run 으로 거른다. 모든 EdgeStep
    # 태스크는 env OPS_ORCHESTRATOR_ATTEMPT_REF=airflow:<dag>/<dag_run_id>/<task>/<try> 를 싣고, shim 기록도 그 값을 남긴다.
    run_id = _run_id(_slot(args.slot))
    ref = f"airflow:{DAG}/{args.dag_run}/"
    tasks = []
    for status in ("RUNNING", "STOPPED"):
        arns = [a for page in ecs.get_paginator("list_tasks").paginate(cluster=CLUSTER, desiredStatus=status)
                for a in page["taskArns"]]
        for i in range(0, len(arns), 100):
            tasks += ecs.describe_tasks(cluster=CLUSTER, tasks=arns[i:i + 100])["tasks"]
    def ours(t: dict) -> bool:
        override = (t.get("overrides", {}).get("containerOverrides") or [{}])[0]
        return any(e.get("name") == "OPS_ORCHESTRATOR_ATTEMPT_REF" and str(e.get("value", "")).startswith(ref)
                   for e in override.get("environment") or [])
    mine = [t for t in tasks if ours(t)]
    bucket = _bucket()
    state = {}
    for kind in ("invocations", "business_starts", "business_runs", "partition_writes", "external_calls"):
        objs = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=f"state/{kind}/") for o in page.get("Contents", [])]
        records = [json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read()) for k in objs]
        state[kind] = [r for r in records if str(r.get("attempt_ref") or "").startswith(ref)]
    print(json.dumps({
        "pipeline_run_id": run_id,
        "ecs_tasks": [{"arn": t["taskArn"].rsplit("/", 1)[1], "cmd": t["overrides"]["containerOverrides"][0]["command"][0],
                       "started_by": t.get("startedBy"), "status": t["lastStatus"], "stop": t.get("stopCode"),
                       "exit": t["containers"][0].get("exitCode")} for t in sorted(mine, key=lambda t: t["createdAt"])],
        # 실제 업무 호출만 센다. 주입한 exit 는 따로(업무 함수를 부르지 않았다).
        "business_starts": [(r["step"], (r.get("ecs_task_arn") or "").rsplit("/", 1)[-1])
                            for r in state["business_starts"] if r.get("injected_exit") is None],
        "injected_exits": [(r["step"], r["injected_exit"], (r.get("ecs_task_arn") or "").rsplit("/", 1)[-1])
                           for r in state["business_starts"] if r.get("injected_exit") is not None],
        "business_runs": [(r["step"], r["exit"], (r.get("ecs_task_arn") or "").rsplit("/", 1)[-1])
                          for r in state["business_runs"]],
        "partition_writes": len(state["partition_writes"]),
        "external_calls_replayed": len(state["external_calls"]),
    }, ensure_ascii=False, indent=1))
    code, text = _one_off(f"{PREFIX}-verify-ops", ["verify-ledger"])
    lines = [line for line in text.splitlines() if line.startswith("VERIFY_LEDGER ")]
    if code != 0 or not lines:
        print(f"원장 조회 실패(exit={code}) — 네 가지 대조 중 원장이 없다", file=sys.stderr)
        return 1
    print(json.dumps(json.loads(lines[-1].removeprefix("VERIFY_LEDGER ")), ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("reset")
    t = sub.add_parser("trigger")
    t.add_argument("--slot", required=True)
    t.add_argument("--conf", default="{}")
    e = sub.add_parser("evidence")
    e.add_argument("--slot", required=True)
    e.add_argument("--dag-run", required=True, help="trigger 가 출력한 dag_run_id")
    args = p.parse_args()
    return {"setup": setup, "reset": reset, "trigger": trigger, "evidence": evidence}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
