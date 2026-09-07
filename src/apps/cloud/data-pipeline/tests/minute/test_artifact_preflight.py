"""전환 검사는 미확인/진행 중/권한 부족을 PASS로 바꿀 수 없다."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
import pytest

from data_pipeline.minute import artifact_preflight as mod

DP = "sha256:" + "a" * 64
AE = "sha256:" + "b" * 64
PREFIX = "edge-dev-data-pipeline"
NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
UNIVERSE = "s3://lake/config/minute/universe.json"


@pytest.fixture
def ready(monkeypatch):
    clients = {name: MagicMock() for name in ("ecs", "ecr", "iam", "s3", "scheduler")}
    aws = SimpleNamespace(client=lambda name: clients[name])
    services, definitions = [], {}
    for suffix in mod._SERVICES:
        name = f"{PREFIX}-{suffix}"
        td = f"arn:aws:ecs:ap-northeast-2:123456789012:task-definition/{name}:5"
        analysis = suffix == "analysis-consumer"
        image = f"123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/edge/pipeline:{'analysis-engine' if analysis else 'data-pipeline'}-latest"
        definitions[td] = {"taskDefinitionArn": td, "family": name, "taskRoleArn": "arn:aws:iam::123456789012:role/" + suffix,
                           "containerDefinitions": [{"name": "analysis-engine" if analysis else "data-pipeline",
                               "image": image, "command": ([suffix, "--universe", UNIVERSE] if suffix in ("price-worker", "inav-worker", "price-consumer") else ["consume-triggers" if analysis else suffix]),
                               "environment": [{"name": "DATA_PIPELINE_MINUTE_ARTIFACT_FORMAT", "value": "legacy"},
                                               {"name": "ALPHAMALE_LAKE_BUCKET" if analysis else "DATA_PIPELINE_STORAGE__BUCKET", "value": "lake"},
                                               {"name": "DATA_PIPELINE_MINUTE_PRICE_WORKER__SOURCE", "value": "kis"}]}]}
        services.append({"serviceName": name, "taskDefinition": td, "status": "ACTIVE",
                         "desiredCount": 0, "runningCount": 0, "pendingCount": 0,
                         "deployments": [{"status": "PRIMARY", "rolloutState": "COMPLETED", "createdAt": NOW}]})
    ops = deepcopy(definitions[services[0]["taskDefinition"]])
    ops.update(family=PREFIX+"-minute-session", taskDefinitionArn="ops:3")
    ops["containerDefinitions"][0]["command"] = ["start-minute-session"]
    ops["containerDefinitions"][0]["environment"] += [{"name": "MINUTE_SESSION_INAV_SOURCE_GROUP", "value": "kis"}, {"name": "MINUTE_SESSION_SECTOR_INDEX_SOURCE_GROUP", "value": "kis"}]
    definitions[PREFIX+"-minute-session"] = ops
    clients["ecs"].describe_services.return_value = {"services": services, "failures": []}
    clients["ecs"].describe_clusters.return_value = {"clusters": [
        {"clusterArn": "arn:aws:ecs:ap-northeast-2:123456789012:cluster/cluster", "clusterName": "cluster", "status": "ACTIVE"}], "failures": []}
    clients["ecs"].describe_task_definition.side_effect = lambda taskDefinition: {"taskDefinition": definitions[taskDefinition]}
    tasks = []
    clients["ecs"].get_paginator.return_value.paginate.side_effect = lambda **kw: [{"taskArns": [t["taskArn"] for t in tasks]}]
    clients["ecs"].describe_tasks.side_effect = lambda **kw: {"tasks": tasks, "failures": []}

    def images(**kw):
        tag = kw["imageIds"][0]["imageTag"]
        return {"imageDetails": [{"imageDigest": AE if tag.startswith("analysis") else DP,
                                  "imagePushedAt": NOW - timedelta(minutes=1)}]}

    clients["ecr"].describe_images.side_effect = images
    clients["iam"].simulate_principal_policy.return_value = {"EvaluationResults": [{"EvalDecision": "allowed"}]}
    clients["s3"].get_bucket_lifecycle_configuration.return_value = {"Rules": [
        {"ID": "old-only", "Status": "Enabled", "Filter": {"Prefix": "operations_archive/canonical_run_artifacts/"},
         "Expiration": {"Days": 30}}]}
    clients["s3"].get_bucket_policy.side_effect = ClientError({"Error": {"Code": "NoSuchBucketPolicy"}}, "GetBucketPolicy")
    clients["s3"].get_bucket_encryption.return_value = {"ServerSideEncryptionConfiguration": {"Rules": [
        {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}}
    clients["s3"].get_object.side_effect = lambda **kw: {"Body": BytesIO(json.dumps({"universe_version": "test", "etf_ids": ["E1"], "constituent_ids": ["C1"]}).encode())}
    schedules = {}
    for suffix, command in (("start", "start-minute-session"), ("stop", "stop-minute-session"),
                            ("rollup-sector", "rollup-minute-session")):
        schedules[f"{PREFIX}-minute-session-{suffix}"] = {
            "State": "ENABLED", "ScheduleExpression": "cron(0 1 * * ? *)", "ScheduleExpressionTimezone": "Asia/Seoul",
            "Target": {"Arn": "arn:aws:scheduler:::aws-sdk:ecs:runTask", "Input": json.dumps({
                "Cluster": "cluster", "TaskDefinition": "ops:3",
                "Overrides": {"ContainerOverrides": [{"Name": "data-pipeline", "Command": [command, "--dataset", "sector_index_minute" if suffix == "rollup-sector" else "price_minute", "--source-group", "kis"] + (["--universe", UNIVERSE] if suffix == "start" else [])}]}})}}
    clients["scheduler"].get_schedule.side_effect = lambda Name, GroupName: schedules[Name]
    database = {"schema": True, "sessions_closed": True, "latest_sessions": [], "session_blockers": []}
    monkeypatch.setattr(mod, "_database", lambda ledger: database)
    return SimpleNamespace(clients=clients, aws=aws, services=services, definitions=definitions,
                           database=database, tasks=tasks, schedules=schedules)


def run(h, **kw):
    return mod.artifact_preflight(ledger=object(), aws_session=h.aws, cluster="cluster",
                                  service_prefix=PREFIX, bucket="lake", pipeline_digest=DP,
                                  analysis_digest=AE, **kw)


def test_all_actual_gates_are_required_even_at_desired_zero(ready):
    result = run(ready)
    assert result["activation_allowed"] and result["scan_complete"]
    assert all(result["checks"].values()) and not result["errors"]
    assert len(result["services"]) == 5 and result["minute_session"]["next_start_digest"] == DP
    assert any("minute_artifact_quarantine" in p["resource"] for p in result["permissions"])


@pytest.mark.parametrize("gate", ["schema", "sessions_closed"])
def test_database_gate_failure_blocks_activation(ready, gate):
    ready.database[gate] = False
    assert not run(ready)["activation_allowed"]


@pytest.mark.parametrize("state", ["desired", "pending", "rolling", "missing_service", "failed_lookup"])
def test_service_boundary_and_lookup_are_not_empty_success(ready, state):
    if state == "desired":
        ready.services[0]["desiredCount"] = 1
    elif state == "pending":
        ready.services[0]["pendingCount"] = 1
    elif state == "rolling":
        ready.services[0]["deployments"][0]["rolloutState"] = "IN_PROGRESS"
    elif state == "missing_service":
        ready.services.pop()
    else:
        ready.clients["ecs"].describe_services.side_effect = RuntimeError("AccessDenied")
    result = run(ready)
    assert not result["activation_allowed"]
    if state in ("missing_service", "failed_lookup"):
        assert not result["scan_complete"] and result["errors"]


@pytest.mark.parametrize("state", ["old_tag", "old_zero_deployment", "missing_writer_setting", "old_rollup"])
def test_next_start_image_must_also_be_compatible(ready, state):
    if state == "old_tag":
        ready.clients["ecr"].describe_images.side_effect = lambda **kw: {"imageDetails": [
            {"imageDigest": "sha256:" + "f"*64, "imagePushedAt": NOW-timedelta(minutes=1)}]}
    elif state == "old_zero_deployment":
        ready.services[-1]["deployments"][0]["createdAt"] = NOW - timedelta(days=1)
    elif state == "missing_writer_setting":
        ready.definitions[ready.services[0]["taskDefinition"]]["containerDefinitions"][0]["environment"] = []
    else:
        ready.definitions[PREFIX+"-minute-session"]["containerDefinitions"][0]["image"] = "unverifiable"
    assert not run(ready)["activation_allowed"]


@pytest.mark.parametrize("state", ["old_reader", "writer_stopping", "standalone_writer", "missing_digest", "task_count_gap"])
def test_actual_tasks_cannot_hide_behind_current_task_definition(ready, state):
    td = ready.services[3]["taskDefinition"]
    task = {"taskArn": "task:1", "taskDefinitionArn": td, "lastStatus": "RUNNING",
            "group": f"service:{ready.services[3]['serviceName']}",
            "containers": [{"name": "data-pipeline", "imageDigest": "sha256:"+"c"*64}]}
    if state == "task_count_gap":
        ready.services[3].update(desiredCount=1, runningCount=1)
    else:
        if state in ("old_reader", "missing_digest", "standalone_writer"):
            ready.services[3].update(desiredCount=1, runningCount=1)
        if state == "writer_stopping":
            task.update(taskDefinitionArn=ready.services[0]["taskDefinition"], lastStatus="STOPPING", group="service:old")
            task["containers"][0]["imageDigest"] = DP
        elif state == "standalone_writer":
            task["overrides"] = {"containerOverrides": [{"name": "data-pipeline", "command": ["price-worker"]}]}
            task["containers"][0]["imageDigest"] = DP
        elif state == "missing_digest":
            task["containers"][0].pop("imageDigest")
        ready.tasks.append(task)
    assert not run(ready)["activation_allowed"]


@pytest.mark.parametrize("state", ["deny", "missing_context", "bucket_policy", "kms", "list_error", "lifecycle"])
def test_permissions_and_retention_must_be_verified(ready, state):
    if state == "deny":
        ready.clients["iam"].simulate_principal_policy.return_value["EvaluationResults"][0]["EvalDecision"] = "implicitDeny"
    elif state == "missing_context":
        ready.clients["iam"].simulate_principal_policy.return_value["EvaluationResults"][0]["MissingContextValues"] = ["unknown"]
    elif state == "bucket_policy":
        ready.clients["s3"].get_bucket_policy.side_effect = None
        ready.clients["s3"].get_bucket_policy.return_value = {"Policy": '{"Statement":[{"Effect":"Deny"}]}'}
    elif state == "kms":
        ready.clients["s3"].get_bucket_encryption.return_value["ServerSideEncryptionConfiguration"]["Rules"][0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] = "aws:kms"
    elif state == "list_error":
        ready.clients["s3"].get_bucket_lifecycle_configuration.side_effect = RuntimeError("denied")
    else:
        ready.clients["s3"].get_bucket_lifecycle_configuration.return_value["Rules"][0]["Filter"]["Prefix"] = "operations_archive/"
    assert not run(ready)["activation_allowed"]


@pytest.mark.parametrize("selector", [{}, {"Tag": {"Key": "ttl", "Value": "30"}},
                                      {"And": {"Prefix": "canonical/", "Tags": []}}])
def test_broad_or_unverifiable_lifecycle_filter_is_not_safe(selector):
    rule = {"Status": "Enabled", "Filter": selector, "Expiration": {"Days": 30}}
    assert mod._lifecycle_conflicts([rule], ["canonical/market_data/price_minute/"]) == [rule]


def test_no_lifecycle_configuration_is_verified_absence(ready):
    ready.clients["s3"].get_bucket_lifecycle_configuration.side_effect = ClientError(
        {"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "GetBucketLifecycleConfiguration")
    assert run(ready)["activation_allowed"]


def test_running_compatible_reader_is_verified_from_actual_task(ready):
    service = ready.services[3]
    service.update(desiredCount=1, runningCount=1)
    ready.tasks.append({"taskArn": "task:good", "taskDefinitionArn": service["taskDefinition"],
                        "lastStatus": "RUNNING", "group": f"service:{service['serviceName']}",
                        "containers": [{"name": "data-pipeline", "imageDigest": DP}]})
    assert run(ready)["activation_allowed"]


def test_a_different_runtime_bucket_cannot_validate_the_requested_bucket(ready):
    ready.definitions[ready.services[0]["taskDefinition"]]["containerDefinitions"][0]["environment"][1]["value"] = "different"
    result = run(ready)
    assert not result["activation_allowed"] and not result["checks"]["images"]


@pytest.mark.parametrize("drift", ["old_revision", "disabled", "wrong_cluster", "wrong_command", "missing", "lookup_error"])
def test_scheduler_actual_target_must_match_verified_revision(ready, drift):
    name = PREFIX + "-minute-session-stop"
    if drift == "missing":
        del ready.schedules[name]
    elif drift == "lookup_error":
        ready.clients["scheduler"].get_schedule.side_effect = RuntimeError("Scheduler denied")
    elif drift == "disabled":
        ready.schedules[name]["State"] = "DISABLED"
    else:
        target = ready.schedules[name]["Target"]
        value = json.loads(target["Input"])
        if drift == "old_revision":
            value["TaskDefinition"] = "ops:2"
        elif drift == "wrong_cluster":
            value["Cluster"] = "other-cluster"
        else:
            value["Overrides"]["ContainerOverrides"][0]["Command"] = ["price-worker"]
        target["Input"] = json.dumps(value)
    result = run(ready)
    assert not result["activation_allowed"] and not result["checks"]["scheduler_targets"]


@pytest.mark.parametrize("suffix", ["price-worker", "inav-worker", "price-consumer"])
@pytest.mark.parametrize("drift", ["missing", "different_universe"])
def test_service_required_universe_arguments_are_verified(ready, suffix, drift):
    service = next(s for s in ready.services if s["serviceName"] == PREFIX+"-"+suffix)
    command = [suffix] if drift == "missing" else [suffix, "--universe", "s3://other/universe.json"]
    ready.definitions[service["taskDefinition"]]["containerDefinitions"][0]["command"] = command
    assert not run(ready)["activation_allowed"]


@pytest.mark.parametrize("suffix", ["start", "stop", "rollup-sector"])
@pytest.mark.parametrize("drift", ["missing", "wrong_dataset", "wrong_source", "extra_override"])
def test_scheduler_required_arguments_and_overrides_are_verified(ready, suffix, drift):
    target = ready.schedules[PREFIX+"-minute-session-"+suffix]["Target"]
    value = json.loads(target["Input"])
    container = value["Overrides"]["ContainerOverrides"][0]
    if drift == "missing":
        container["Command"] = container["Command"][:1]
    elif drift == "wrong_dataset":
        container["Command"][2] = "news_minute"
    elif drift == "wrong_source":
        container["Command"][4] = "wrong"
    else:
        container["Environment"] = [{"Name": "DATA_PIPELINE_STORAGE__BUCKET", "Value": "other"}]
    target["Input"] = json.dumps(value)
    assert not run(ready)["activation_allowed"]


@pytest.mark.parametrize("failure", ["missing", "invalid"])
def test_universe_object_is_actually_read_and_validated(ready, failure):
    if failure == "missing":
        ready.clients["s3"].get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    else:
        ready.clients["s3"].get_object.side_effect = lambda **kw: {"Body": BytesIO(b"{}")}
    result = run(ready)
    assert not result["activation_allowed"] and not result["scan_complete"] and result["errors"]


@pytest.mark.parametrize("status", ["RUNNING", "STOPPING"])
@pytest.mark.parametrize("standalone", [False, True])
def test_session_starter_cannot_restart_writers_after_boundary_check(ready, status, standalone):
    # 현재 digest도 start 명령의 뒤쪽 UpdateService를 아직 실행할 수 있다.
    td = deepcopy(ready.definitions[PREFIX + "-minute-session"])
    if standalone:
        td.update(family="manual-session-start", taskDefinitionArn="manual:1")
    ready.definitions[td["taskDefinitionArn"]] = td
    ready.tasks.append({"taskArn": "task:starter", "taskDefinitionArn": td["taskDefinitionArn"],
        "lastStatus": status, "group": "family:" + td["family"],
        "overrides": {"containerOverrides": [{"name": "data-pipeline", "command": [
            "start-minute-session", "--dataset", "price_minute", "--source-group", "kis",
            "--universe", UNIVERSE]}]},
        "containers": [{"name": "data-pipeline", "imageDigest": DP}]})
    result = run(ready)
    assert result["scan_complete"] and not result["activation_allowed"]
    assert not result["checks"]["old_tasks_absent"]
    assert result["task_blockers"] == ["task:starter"]
