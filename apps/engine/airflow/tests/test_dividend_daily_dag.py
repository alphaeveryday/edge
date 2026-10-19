"""The dividend refresh must not compete with the existing batch schedule."""
import importlib
import socket
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))
KST = timezone(timedelta(hours=9))


@pytest.fixture(autouse=True)
def configured_families(monkeypatch):
    monkeypatch.setenv("EDGE_DIVIDEND_BLOCKING_TASK_FAMILIES", '["batch"]')


@pytest.fixture(scope="module")
def module():
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        return importlib.import_module("edge_dividend_daily")
    finally:
        socket.socket.connect = original


class SFN:
    def __init__(self, busy=False): self.busy = busy
    def list_executions(self, **kwargs): return {"executions": [{}] if self.busy else []}


class ECS:
    def __init__(self, tasks=(), failures=(), desired="RUNNING"):
        self.tasks, self.failures, self.desired = tasks, failures, desired
    def get_paginator(self, name): return self
    def paginate(self, **kwargs):
        return [{"taskArns": [str(i) for i in range(len(self.tasks))] if kwargs['desiredStatus'] == self.desired else []}]
    def describe_tasks(self, **kwargs): return {"tasks": self.tasks, "failures": self.failures}


def test_only_idle_night_window_allows_collection(module):
    now = datetime(2026, 10, 19, 3, tzinfo=KST)
    assert module.batch_window_clear(now=now, ecs=ECS(), sfn=SFN(), state_machines=["news"])
    assert not module.batch_window_clear(now=now, ecs=ECS(), sfn=SFN(True), state_machines=["news"])
    assert not module.batch_window_clear(now=now, ecs=ECS([{"group": "family:batch", "taskDefinitionArn": "arn:task-definition/batch:1", "lastStatus": "RUNNING"}]), sfn=SFN(), state_machines=["news"])
    assert module.batch_window_clear(now=now, ecs=ECS([{"group": "service:reader", "lastStatus": "RUNNING"}]), sfn=SFN(), state_machines=["news"])
    assert module.batch_window_clear(now=now, ecs=ECS([{"group": "family:pilot", "taskDefinitionArn": "arn:task-definition/pilot:1", "lastStatus": "RUNNING"}]), sfn=SFN(), state_machines=["news"])


def test_stopping_batch_still_blocks_until_its_container_has_stopped(module):
    for actual, allowed in [("STOPPING", False), ("RUNNING", False), ("STOPPED", True)]:
        ecs = ECS([{"group": "family:batch", "taskDefinitionArn": "arn:task-definition/batch:1", "lastStatus": actual}], desired="STOPPED")
        assert module.batch_window_clear(now=datetime(2026, 10, 19, 3, tzinfo=KST), ecs=ecs, sfn=SFN(), state_machines=["news"]) is allowed


def test_unknown_running_task_or_late_start_is_not_treated_as_idle(module):
    with pytest.raises(Exception, match="Cannot determine"):
        module.batch_window_clear(now=datetime(2026, 10, 19, 3, tzinfo=KST),
            ecs=ECS([{}], [{"arn": "missing"}]), sfn=SFN(), state_machines=["news"])
    for hour, minute in [(2, 59), (3, 30), (5, 20), (6, 0)]:
        with pytest.raises(Exception, match="window"):
            module.batch_window_clear(now=datetime(2026, 10, 19, hour, minute, tzinfo=KST),
                ecs=ECS(), sfn=SFN(), state_machines=["news"])


def test_historical_slot_cannot_receive_a_new_live_collection(module):
    with pytest.raises(module.AirflowFailException, match="reprocessing"):
        module.batch_window_clear(dag_run=SimpleNamespace(conf={"reprocess_slot": "2026-10-18T03:00:00+09:00"}),
            now=datetime(2026, 10, 19, 3, tzinfo=KST), ecs=ECS(), sfn=SFN(), state_machines=["news"])


def test_dag_waits_before_planning_and_retains_partial_raw(module):
    dag = module.dag
    assert module.CRONS == ("0 3 * * *",)
    assert dag.is_paused_upon_creation and dag.max_active_runs == 1 and not dag.catchup
    assert dag.dagrun_timeout < timedelta(hours=1)
    assert dag.get_task("plan").upstream_task_ids == {"wait_for_batch_window"}
    assert dag.get_task("plan").stop_on_upstream_hold is False
    assert dag.get_task("collect").upstream_task_ids == {"plan"}
    assert dag.get_task("normalize").upstream_task_ids == {"plan", "collect"}
    # A rejected run has no successful plan; collection failures after planning still report.
    assert dag.get_task("report").upstream_task_ids == {"plan", "load"}
    assert dag.get_task("report").trigger_rule == "all_done_min_one_success"
    assert dag.get_task("collect").overrides["containerOverrides"][0]["command"][-2:] == ["--deadline-sec", "600"]
    assert dag.get_task("collect").partial_exit_codes == (2,)


def test_collision_inventory_includes_families_airflow_cannot_launch():
    root = Path(__file__).resolve().parents[4]
    config = (root / "infra/terraform/envs/dev/main.tf").read_text()
    module_config = (root / "infra/terraform/modules/airflow/main.tf").read_text()
    assert "blocking_batch_task_families = concat(values(module.data_pipeline.task_definition_families)," in config
    assert "EDGE_DIVIDEND_BLOCKING_TASK_FAMILIES = jsonencode(var.blocking_batch_task_families)" in module_config


def test_cleared_collector_cannot_submit_outside_current_admission(module, monkeypatch):
    submitted, refused = [], []
    collector = module.dag.get_task("collect")
    monkeypatch.setattr(module.EdgeStep, "_start_task", lambda self: submitted.append(True))
    monkeypatch.setattr(module.EdgeStep, "_no_task", lambda self: refused.append(True))
    for admission in (False, RuntimeError("AWS unavailable"), module.AirflowFailException("late")):
        def check():
            if isinstance(admission, Exception):
                raise admission
            return admission
        monkeypatch.setattr(module, "batch_window_clear", check)
        with pytest.raises(module.AirflowFailException, match="admission failed"):
            collector._start_task()
    assert not submitted and len(refused) == 3
    monkeypatch.setattr(module, "batch_window_clear", lambda: True)
    collector._start_task()
    assert submitted == [True]
