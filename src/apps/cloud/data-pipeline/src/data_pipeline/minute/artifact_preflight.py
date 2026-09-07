"""운영자용 읽기 전용 content_v2 전환 사전검사. 미확인 값은 전환 허용으로 간주하지 않는다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re

from .reconciliation import CLOSED_ARTIFACT_PHASES, MINUTE_ARTIFACT_DATASETS
from .models import Universe
from .states import SOURCE_GROUPS_BY_DATASET

_WRITERS = ("price-worker", "inav-worker", "sector-index-worker")
_SERVICES = (*_WRITERS, "price-consumer", "analysis-consumer")
_MIGRATION = "202609071200"


def _database(ledger):
    with ledger.connect_fn(ledger.db) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cur.execute("""SELECT EXISTS(SELECT 1 FROM flyway_schema_history
                       WHERE version=%s AND success),
                       to_regclass('public.minute_window_artifact_commit') IS NOT NULL""", (_MIGRATION,))
        schema = cur.fetchone()
        cur.execute("""SELECT s.session_id, s.dataset, s.session_date, s.phase,
                              count(w.window_start), count(*) FILTER (WHERE w.data_status='CLAIMED')
                       FROM minute_ingestion_session s LEFT JOIN minute_ingestion_window w
                         ON w.session_id=s.session_id
                       WHERE s.dataset = ANY(%s)
                       GROUP BY s.session_id ORDER BY s.session_date DESC, s.dataset""",
                    (sorted(MINUTE_ARTIFACT_DATASETS),))
        sessions = [dict(zip(("session_id", "dataset", "session_date", "phase", "windows", "claimed"),
                             r, strict=True)) for r in cur.fetchall()]
    latest = {}
    for row in sessions:
        latest.setdefault(row["dataset"], row)
    blockers = [s for s in sessions if s["phase"] not in CLOSED_ARTIFACT_PHASES or s["claimed"]]
    closed = (set(latest) == MINUTE_ARTIFACT_DATASETS and not blockers
              and all(s["windows"] > 0 for s in latest.values())
              and len({s["session_date"] for s in latest.values()}) == 1)
    return {"schema": schema == (True, True), "sessions_closed": closed,
            "latest_sessions": list(latest.values()), "session_blockers": blockers}


def _image(ecr, uri):
    match = re.fullmatch(r"([0-9]{12})\.dkr\.ecr\.[^/]+\.amazonaws\.com/([^@:]+)(?::([^@]+)|@(sha256:[0-9a-f]{64}))", uri)
    if not match:
        raise ValueError(f"검증할 수 없는 ECR image URI: {uri}")
    registry, repository, tag, digest = match.groups()
    image_id = {"imageDigest": digest} if digest else {"imageTag": tag}
    details = ecr.describe_images(registryId=registry, repositoryName=repository,
                                  imageIds=[image_id])["imageDetails"]
    if len(details) != 1:
        raise ValueError(f"image digest 미확인: {uri}")
    return details[0]


def _tasks(ecs, cluster):
    arns = set()
    # desired STOPPED인 STOPPING 태스크도 아직 옛 PUT을 끝내는 중일 수 있다.
    for desired in ("RUNNING", "STOPPED"):
        for page in ecs.get_paginator("list_tasks").paginate(cluster=cluster, desiredStatus=desired):
            arns.update(page["taskArns"])
    tasks = []
    ordered = sorted(arns)
    for offset in range(0, len(ordered), 100):
        response = ecs.describe_tasks(cluster=cluster, tasks=ordered[offset:offset + 100])
        if response.get("failures"):
            raise ValueError(f"task 조회 실패: {response['failures']}")
        tasks.extend(t for t in response["tasks"] if t["lastStatus"] != "STOPPED")
    return tasks


def _lifecycle_conflicts(rules, prefixes):
    conflicts = []
    for rule in rules:
        if rule.get("Status") != "Enabled" or not any(
            k in rule for k in ("Expiration", "NoncurrentVersionExpiration", "Transitions", "NoncurrentVersionTransitions")
        ):
            continue
        selector = rule.get("Filter", {})
        prefix = selector.get("Prefix", selector.get("And", {}).get("Prefix", rule.get("Prefix", "")))
        # Tag/크기 조건은 현재·미래 승자에 적용되지 않는다고 인증할 근거가 없다.
        if any(p.startswith(prefix) or prefix.startswith(p) for p in prefixes):
            conflicts.append(rule)
    return conflicts


def artifact_preflight(*, ledger, aws_session, cluster: str, service_prefix: str,
                       bucket: str, pipeline_digest: str, analysis_digest: str) -> dict:
    """DB migration/닫힌 세션, ECS 실제·다음 기동 이미지, IAM 및 lifecycle을 조회한다.

    digest 인자는 CI/CD에서 검증한 두 이미지의 기대값이다. 이 명령은 배포/테스트 승인을
    만들지 않으며, PASS도 실행 시점의 증거다. 전환 직전에 재실행해야 한다.
    """
    checks = dict.fromkeys(("schema", "sessions_closed", "writer_services_stopped", "deployments",
                           "images", "scheduler_targets", "old_tasks_absent", "permissions", "lifecycle"), False)
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "scan_complete": False, "activation_allowed": False, "errors": []}
    try:
        for value in (pipeline_digest, analysis_digest):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError("검증된 pipeline/analysis image digest가 모두 필요하다")
        db = _database(ledger)
        report["database"] = db
        checks.update(schema=db["schema"], sessions_closed=db["sessions_closed"])
        ecs, ecr = aws_session.client("ecs"), aws_session.client("ecr")
        iam, s3 = aws_session.client("iam"), aws_session.client("s3")
        cluster_response = ecs.describe_clusters(clusters=[cluster])
        if cluster_response.get("failures") or len(cluster_response["clusters"]) != 1:
            raise ValueError("대상 ECS cluster ARN 미확인")
        cluster_record = cluster_response["clusters"][0]
        if cluster_record["status"] != "ACTIVE":
            raise ValueError("대상 ECS cluster 비활성")
        report["cluster"] = cluster_record["clusterArn"]
        names = [f"{service_prefix}-{suffix}" for suffix in _SERVICES]
        response = ecs.describe_services(cluster=cluster, services=names)
        services = {s["serviceName"]: s for s in response["services"]}
        if response.get("failures") or set(services) != set(names):
            raise ValueError("필수 writer/reader 서비스 조회 실패/결손")
        definitions, image_details = {}, {}

        def definition(arn):
            """같은 task definition은 한 번만 조회한다."""
            if arn not in definitions:
                definitions[arn] = ecs.describe_task_definition(taskDefinition=arn)["taskDefinition"]
            return definitions[arn]

        def resolved(uri):
            """같은 image 태그는 한 번 해소해 검사 중 비교 축을 고정한다."""
            if uri not in image_details:
                image_details[uri] = _image(ecr, uri)
            return image_details[uri]

        deployment_ok = image_ok = True
        report["services"] = []
        roles = {}
        universe_uri = price_source = None
        for suffix, name in zip(_SERVICES, names, strict=True):
            service = services[name]
            td = definition(service["taskDefinition"])
            containers = td["containerDefinitions"]
            if len(containers) != 1:
                raise ValueError(f"검증하지 못하는 multi-container task: {name}")
            container = containers[0]
            details = resolved(container["image"])
            expected = analysis_digest if suffix == "analysis-consumer" else pipeline_digest
            primary = [d for d in service["deployments"] if d["status"] == "PRIMARY"]
            stable = (service["status"] == "ACTIVE" and len(service["deployments"]) == len(primary) == 1
                      and primary[0].get("rolloutState") == "COMPLETED"
                      and service["pendingCount"] == 0 and service["runningCount"] == service["desiredCount"])
            deployment_ok &= stable
            image_ok &= details["imageDigest"] == expected
            env = {e["name"]: e["value"] for e in container.get("environment", [])}
            bucket_env = "ALPHAMALE_LAKE_BUCKET" if suffix == "analysis-consumer" else "DATA_PIPELINE_STORAGE__BUCKET"
            image_ok &= env.get(bucket_env) == bucket
            command = container.get("command", [])
            if suffix == "price-worker":
                if len(command) != 3 or command[:2] != [suffix, "--universe"]:
                    raise ValueError("price-worker의 필수 universe 명령 미확인")
                universe_uri = command[2]
                if not universe_uri.startswith(f"s3://{bucket}/") or not universe_uri[len(f"s3://{bucket}/"):]:
                    raise ValueError("ECS universe는 검사 대상 S3 bucket의 객체여야 한다")
                price_source = env.get("DATA_PIPELINE_MINUTE_PRICE_WORKER__SOURCE")
                if price_source not in SOURCE_GROUPS_BY_DATASET["price_minute"]:
                    raise ValueError("price-worker source 설정 미확인")
            expected_command = ([suffix, "--universe", universe_uri]
                if suffix in ("price-worker", "inav-worker", "price-consumer")
                else ["consume-triggers" if suffix == "analysis-consumer" else suffix])
            image_ok &= command == expected_command
            if suffix in _WRITERS:
                image_ok &= env.get("DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT") in ("legacy", "content_v2")
            # desired 0 분석 서비스는 scale-up 때 force를 하지 않는다. 새 image push 뒤
            # fresh deployment가 있어야 다음 scale-up이 옛 고정 digest를 재사용하지 않는다.
            fresh = (suffix != "analysis-consumer" or service["desiredCount"] > 0
                     or (len(primary) == 1 and primary[0]["createdAt"] >= details["imagePushedAt"]))
            image_ok &= fresh
            roles[suffix] = td["taskRoleArn"]
            report["services"].append({"name": name, "task_definition": service["taskDefinition"],
                "desired": service["desiredCount"], "running": service["runningCount"],
                "pending": service["pendingCount"], "image": container["image"],
                "command": command,
                "next_start_digest": details["imageDigest"], "expected_digest": expected,
                "artifact_format": env.get("DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT"),
                "stable": stable, "fresh_analysis_deployment": fresh})
        # EOD 롤업·대사가 실행되는 단발 task도 호환 reader여야 한다.
        ops_td = definition(f"{service_prefix}-minute-session")
        if len(ops_td["containerDefinitions"]) != 1:
            raise ValueError("minute-session container 형상 미확인")
        ops_image = ops_td["containerDefinitions"][0]["image"]
        image_ok &= resolved(ops_image)["imageDigest"] == pipeline_digest
        ops_env = {e["name"]: e["value"] for e in ops_td["containerDefinitions"][0].get("environment", [])}
        image_ok &= ops_env.get("DATA_PIPELINE_STORAGE__BUCKET") == bucket
        for dataset, name in (("etf_inav_minute", "INAV"), ("sector_index_minute", "SECTOR_INDEX")):
            image_ok &= ops_env.get(f"MINUTE_SESSION_{name}_SOURCE_GROUP") in SOURCE_GROUPS_BY_DATASET[dataset]
        universe_key = universe_uri[len(f"s3://{bucket}/"):]
        universe_body = s3.get_object(Bucket=bucket, Key=universe_key)["Body"].read()
        universe = Universe.model_validate(json.loads(universe_body))
        report["universe"] = {"uri": universe_uri, "universe_hash": universe.universe_hash}
        roles["minute-session"] = ops_td["taskRoleArn"]
        report["minute_session"] = {"task_definition": ops_td["taskDefinitionArn"],
                                    "next_start_digest": resolved(ops_image)["imageDigest"]}
        # Scheduler input은 revision ARN을 고정한다. family의 최신 revision 등록만
        # 성공하고 target 갱신이 실패한 경우도 다음 실행이 검증됐다고 보고하면 안 된다.
        scheduler = aws_session.client("scheduler")
        report["schedules"] = []
        schedules_ok = True
        for suffix, step in (("start", "start-minute-session"), ("stop", "stop-minute-session"),
                             ("rollup-sector", "rollup-minute-session")):
            name = f"{service_prefix}-minute-session-{suffix}"
            schedule = scheduler.get_schedule(Name=name, GroupName="default")
            target = schedule["Target"]
            inputs = json.loads(target["Input"])
            overrides = inputs.get("Overrides", {}).get("ContainerOverrides", [])
            command = overrides[0].get("Command", []) if len(overrides) == 1 else []
            expected_command = [step, "--dataset", "sector_index_minute" if suffix == "rollup-sector" else "price_minute",
                "--source-group", ops_env.get("MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP") if suffix == "rollup-sector" else price_source]
            if suffix == "start":
                expected_command += ["--universe", universe_uri]
            matches = (schedule["State"] == "ENABLED"
                       and target["Arn"] == "arn:aws:scheduler:::aws-sdk:ecs:runTask"
                       and inputs.get("Cluster") in (cluster_record["clusterArn"], cluster_record["clusterName"])
                       and inputs.get("TaskDefinition") == ops_td["taskDefinitionArn"]
                       and command == expected_command
                       and overrides[0].get("Name") == ops_td["containerDefinitions"][0]["name"]
                       and set(overrides[0]) == {"Name", "Command"}
                       and set(inputs["Overrides"]) == {"ContainerOverrides"})
            schedules_ok &= matches
            report["schedules"].append({"name": name, "state": schedule["State"],
                "task_definition": inputs.get("TaskDefinition"), "cluster": inputs.get("Cluster"),
                "command": command, "expression": schedule["ScheduleExpression"],
                "timezone": schedule["ScheduleExpressionTimezone"], "matches": matches})
        checks["scheduler_targets"] = schedules_ok
        tasks = _tasks(ecs, cluster)
        blockers = []
        report["tasks"] = []
        for task in tasks:
            td = definition(task["taskDefinitionArn"])
            for container in td["containerDefinitions"]:
                override = next((c for c in task.get("overrides", {}).get("containerOverrides", [])
                                 if c["name"] == container["name"]), {})
                command = override.get("command", container.get("command", []))
                writer = any(step in command for step in _WRITERS)
                # start는 현재 digest여도 조회 뒤 writer를 다시 scale-up할 수 있다.
                starter = "start-minute-session" in command
                related = td["family"] in (*names, f"{service_prefix}-minute-session")
                if not writer and not starter and not related:
                    continue
                runtime = next((c for c in task.get("containers", []) if c["name"] == container["name"]), {})
                expected = analysis_digest if container["name"] == "analysis-engine" else pipeline_digest
                actual = runtime.get("imageDigest")
                expected_td = (services[td["family"]]["taskDefinition"] if td["family"] in services
                               else ops_td["taskDefinitionArn"])
                if (writer or starter or actual != expected or task["taskDefinitionArn"] != expected_td
                        or override.get("environment") or override.get("environmentFiles")
                        or task.get("overrides", {}).get("taskRoleArn", td["taskRoleArn"]) != td["taskRoleArn"]):
                    blockers.append(task["taskArn"])
                report["tasks"].append({"task": task["taskArn"], "task_definition": task["taskDefinitionArn"],
                    "last_status": task["lastStatus"], "command": command, "writer": writer, "starter": starter,
                    "image_digest": actual, "expected_digest": expected})
        for name, service in services.items():
            running = [t for t in tasks if t.get("group") == f"service:{name}"
                       and t["lastStatus"] == "RUNNING"]
            if len(running) != service["runningCount"]:
                raise ValueError(f"service/task 실행 수 대조 실패: {name}")
        checks.update(writer_services_stopped=all(services[f"{service_prefix}-{w}"]["desiredCount"] == 0
                                                  for w in _WRITERS), deployments=deployment_ok,
                      images=image_ok, old_tasks_absent=not blockers)
        report["task_blockers"] = sorted(set(blockers))

        requests = set()
        for role in ("price-worker", "inav-worker", "price-consumer", "minute-session"):
            requests.add((roles[role], "s3:GetObject", f"arn:aws:s3:::{bucket}/{universe_key}"))
        for dataset in sorted(MINUTE_ARTIFACT_DATASETS):
            artifact = f"arn:aws:s3:::{bucket}/canonical/market_data/{dataset}/market=KR/session_date=2000-01-01/session_id=preflight/window=0900/content={'a'*64}/{'inav' if dataset == 'etf_inav_minute' else 'bars'}.ndjson"
            manifest = f"arn:aws:s3:::{bucket}/operations_archive/minute_manifests/dataset={dataset}/market=KR/session_date=2000-01-01/session_id=preflight/window=0900/generation=1/content={'b'*64}/manifest.json"
            writer_role = roles[{"price_minute": "price-worker", "etf_inav_minute": "inav-worker",
                                 "sector_index_minute": "sector-index-worker"}[dataset]]
            for resource in (artifact, manifest):
                for action in ("s3:PutObject", "s3:GetObject"):
                    requests.add((writer_role, action, resource))
                for reader in ("price-consumer", "analysis-consumer", "minute-session"):
                    requests.add((roles[reader], "s3:GetObject", resource))
        requests.add((roles["minute-session"], "s3:PutObject",
                      f"arn:aws:s3:::{bucket}/operations_archive/minute_artifact_quarantine/session_id=preflight/content={'c'*64}/record.json"))
        requests.add((roles["minute-session"], "s3:ListBucket", f"arn:aws:s3:::{bucket}"))
        permissions = []
        for role, action, resource in sorted(requests):
            response = iam.simulate_principal_policy(PolicySourceArn=role, ActionNames=[action],
                                                     ResourceArns=[resource])
            evaluations = response["EvaluationResults"]
            allowed = (len(evaluations) == 1 and evaluations[0]["EvalDecision"] == "allowed"
                       and not evaluations[0].get("MissingContextValues"))
            permissions.append({"role": role, "action": action, "resource": resource, "allowed": allowed})
        report["permissions"] = permissions
        checks["permissions"] = bool(permissions) and all(p["allowed"] for p in permissions)
        # IAM 허용만으로 bucket resource policy의 추가 Deny/KMS 제약을 숨기지 않는다.
        # 현재 dev는 bucket policy 없음 + SSE-S3다. 다른 구성이면 별도 검증 전에는 닫는다.
        try:
            bucket_policy = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") != "NoSuchBucketPolicy":
                raise
            bucket_policy = None
        encryption = s3.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]["Rules"]
        report["bucket_policy"] = bucket_policy
        report["encryption"] = encryption
        checks["permissions"] &= bucket_policy is None and bool(encryption) and all(
            r.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm") == "AES256"
            for r in encryption
        )
        try:
            rules = s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") != "NoSuchLifecycleConfiguration":
                raise
            rules = []
        prefixes = [f"canonical/market_data/{ds}/" for ds in sorted(MINUTE_ARTIFACT_DATASETS)]
        prefixes += ["operations_archive/minute_manifests/", "operations_archive/minute_artifact_quarantine/"]
        conflicts = _lifecycle_conflicts(rules, prefixes)
        report["lifecycle_conflicts"] = conflicts
        checks["lifecycle"] = not conflicts
        report["scan_complete"] = True
        report["activation_allowed"] = all(checks.values())
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    return json.loads(json.dumps(report, default=str))


def main(argv=None) -> int:
    """운영자 AWS 조회 자격과 DATA_PIPELINE_DB__* 설정으로 실행. 실패/미확인은 exit 2."""
    import boto3
    from ..config import load_settings
    from .repository import MinuteLedger

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--service-prefix", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--pipeline-digest", required=True)
    parser.add_argument("--analysis-digest", required=True)
    parser.add_argument("--region", default="ap-northeast-2")
    args = parser.parse_args(argv)
    settings = load_settings(args.config)
    if settings.db is None:
        parser.error("DATA_PIPELINE_DB__* 설정이 필요하다")
    report = artifact_preflight(
        ledger=MinuteLedger(db=settings.db), aws_session=boto3.Session(region_name=args.region),
        cluster=args.cluster, service_prefix=args.service_prefix, bucket=args.bucket,
        pipeline_digest=args.pipeline_digest, analysis_digest=args.analysis_digest,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["activation_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
