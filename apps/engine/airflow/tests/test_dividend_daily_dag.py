"""The dividend refresh must not compete with the existing batch schedule."""
import importlib
import re
import socket
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))
TF = Path(__file__).resolve().parents[4] / "infra/terraform/modules/data-pipeline"
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


def test_slot_matches_the_missed_run_judgment(module):
    # WHY(ALPHA-1443): Reconciler 는 `OPS_DIVIDEND_DAILY_SCHED_HHMM` 시각의 run 이 원장에 없으면 PLANNER_MISSING 을 연다.
    # 10-21 03:00 run 은 창 검사에서 plan 전에 죽어 원장에 행이 없었는데, 이 env 가 없어 결측 이슈가 0건이었다.
    # cron 변수가 없는 레인이라 Terraform 이 DAG 에서 시각을 뽑지 못한다 — 한쪽만 옮기면 매일 거짓 결측이 열리고 진짜 결측은 안 보인다.
    text = (TF / "ops_ledger.tf").read_text()
    slots = re.search(r'dividend_daily_schedule_hhmm\s*=\s*"([^"]*)"', text)[1].split(",")
    assert sorted(f"{int(m)} {int(h)} * * *" for h, m in (s.split(":") for s in slots)) == sorted(module.CRONS)
    # 매일 도는 레인이다 — 주말 플래그가 빠지면 토·일 결측이 조용히 판정에서 빠진다.
    assert re.search(r'OPS_DIVIDEND_DAILY_SCHED_HHMM\s*=\s*local\.dividend_daily_schedule_hhmm', text)
    assert re.search(r'OPS_DIVIDEND_DAILY_SCHED_WEEKEND\s*=\s*"true"', text)


def test_failed_run_publishes_to_the_alarm_topic(module, monkeypatch):
    # WHY(ALPHA-1443): 이 DAG 만 실패 통보가 없어 10-21 창 놓침 FAILED 가 메일 없이 지나갔다. 창을 놓친 FAILED 도
    # 통보 대상이다 — 그날 배당 수집이 없었다는 사실은 사람이 알아야 한다.
    assert module.dag.on_failure_callback is module._notify_failure
    assert module.build_dag(notify=False).on_failure_callback is None
    sent = []
    monkeypatch.setattr(module, "ALARM_TOPIC", "arn:aws:sns:ap-northeast-2:0:alarm")
    monkeypatch.setattr(module, "SnsHook", lambda: SimpleNamespace(publish_to_target=lambda **kw: sent.append(kw)))
    run = SimpleNamespace(run_id="scheduled__2026-10-20T18:00:00+00:00", dag_id="edge_dividend_daily")
    module._notify_failure({"dag_run": run, "reason": "task_failure"})
    assert sent == [{"target_arn": "arn:aws:sns:ap-northeast-2:0:alarm",
                     "subject": "[dividend-daily] FAILED — airflow scheduled__2026-10-20T18:00:00+00:00",
                     "message": "dag=edge_dividend_daily run=scheduled__2026-10-20T18:00:00+00:00 reason=task_failure"}]
    # 긴 수동 run_id 라도 SNS 제목 상한(100자 미만)에 걸려 통보가 통째로 거부되면 안 된다 — 본문에 전체 id 가 남는다.
    sent.clear()
    long_run = SimpleNamespace(run_id="manual__" + "a" * 120, dag_id="edge_dividend_daily")
    module._notify_failure({"dag_run": long_run, "reason": "task_failure"})
    assert len(sent[0]["subject"]) < 100 and long_run.run_id in sent[0]["message"]
