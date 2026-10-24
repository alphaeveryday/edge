"""Public dividends at 03:00 KST, outside news/source/outlook batch slots.

Existing batch tasks and Step Functions executions must have finished before this
lane starts. A busy or unobservable window never becomes permission to overlap.
The new DAG remains paused until schema, pipeline image and IAM are deployed.
"""
import json
import os
from datetime import datetime, timedelta

from airflow.providers.amazon.aws.hooks.base_aws import AwsBaseHook
from airflow.providers.amazon.aws.hooks.ecs import EcsHook
from airflow.providers.amazon.aws.hooks.sns import SnsHook
from airflow.providers.standard.sensors.python import PythonSensor
from airflow.sdk import DAG, TriggerRule, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from edge_batch import (CLUSTER, KST, HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN, EdgeStep,
                        pipeline_run_id, reprocess_slot, run_key, run_status, settlement, slot_time)

LANE = "dividend-daily"
CRONS = ("0 3 * * *",)
TASK_KEYS = {"collect": "DIVIDEND_COLLECTION_KIS", "normalize": "NORMALIZE_DIVIDEND", "load": "LOAD_DIVIDEND"}
ALARM_TOPIC = os.environ.get("EDGE_ALARM_TOPIC_ARN") or None


def batch_window_clear(*, dag_run=None, now=None, ecs=None, sfn=None, state_machines=None, task_families=None):
    """Wait within the night window; fail closed on missing config or AWS errors."""
    if dag_run is not None and reprocess_slot({"dag_run": dag_run}):
        raise AirflowFailException("Use explicit normalize/load CLI runs for dividend reprocessing")
    now = (now or datetime.now(KST)).astimezone(KST)
    if not (3 * 60 <= now.hour * 60 + now.minute < 3 * 60 + 30):
        raise AirflowFailException("Dividend collection window is 03:00–03:30 KST")
    arns = state_machines if state_machines is not None else json.loads(os.environ["EDGE_DIVIDEND_BLOCKING_SFN_ARNS"])
    if not arns:
        raise AirflowFailException("Missing blocking batch state machines")
    families = task_families if task_families is not None else json.loads(os.environ["EDGE_DIVIDEND_BLOCKING_TASK_FAMILIES"])
    if not families:
        raise AirflowFailException("Missing blocking batch task families")
    sfn = sfn or AwsBaseHook(aws_conn_id="aws_default", client_type="stepfunctions").get_conn()
    for arn in arns:
        if sfn.list_executions(stateMachineArn=arn, statusFilter="RUNNING", maxResults=1)["executions"]:
            return False
    ecs = ecs or EcsHook(aws_conn_id="aws_default").get_conn()
    # desiredStatus=STOPPED can still mean a container is stopping, not stopped.
    for desired in ("RUNNING", "STOPPED"):
        for page in ecs.get_paginator("list_tasks").paginate(cluster=CLUSTER, desiredStatus=desired):
            arns = page["taskArns"]
            for offset in range(0, len(arns), 100):
                response = ecs.describe_tasks(cluster=CLUSTER, tasks=arns[offset:offset + 100])
                if response.get("failures"):
                    raise AirflowFailException("Cannot determine running batch tasks")
                for item in response["tasks"]:
                    # Long-running pilots and resident services are not batch jobs.
                    if item.get("group", "").startswith("service:") or item["lastStatus"] == "STOPPED":
                        continue
                    family = item["taskDefinitionArn"].rsplit("/", 1)[-1].rsplit(":", 1)[0]
                    if family in families:
                        return False
    return True


class DividendCollectionStep(EdgeStep):
    """Recheck admission on a fresh submission, including manually cleared tasks."""

    def _start_task(self):
        try:
            if not batch_window_clear():
                raise AirflowFailException("A batch started before dividend collection")
        except Exception as exc:
            self._no_task()
            raise AirflowFailException("Dividend collection admission failed") from exc
        return super()._start_task()


def _codes(ti):
    return {name: ti.xcom_pull(task_ids=name, key="exit_code") for name in TASK_KEYS}


def _status(ti):
    if ti.xcom_pull(task_ids="plan", key="exit_code") != 0:
        return ""
    if any(settlement(ti, name) for name in ("plan", *TASK_KEYS)):
        return "FAILED"
    return run_status(_codes(ti))


def _holds(ti):
    return json.dumps({key: hold for name, key in TASK_KEYS.items() if (hold := settlement(ti, name))
                       and hold.get("kind") in (HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN)})


def _notify_failure(context):
    """Run failure notice on the same SNS topic as the other DAGs, including a missed 03:00–03:30 window."""
    if ALARM_TOPIC is None:
        return
    run = context["dag_run"]
    SnsHook().publish_to_target(
        target_arn=ALARM_TOPIC,
        subject=f"[{LANE}] FAILED — airflow {run.run_id}"[:99],      # SNS 제목은 100자 미만 — 전체 run_id 는 본문에 있다
        message=f"dag={run.dag_id} run={run.run_id} reason={context.get('reason')}",
    )


def build_dag(step=EdgeStep, notify=True):
    """Use existing ECS tasks and ops ledger; create no additional AWS service."""
    with DAG("edge_dividend_daily", auto_register=False,
        schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)),
        is_paused_upon_creation=True, catchup=False, max_active_runs=1,
        dagrun_timeout=timedelta(seconds=1500), default_args={"retries": 2, "retry_delay": timedelta(seconds=30)},
        user_defined_macros={
            "dividend_slot": lambda logical_date, dag_run: slot_time({"logical_date": logical_date, "dag_run": dag_run}),
            "dividend_run": lambda logical_date, dag_run: pipeline_run_id(LANE, slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "dividend_key": lambda logical_date, dag_run: run_key(LANE, slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "dividend_status": _status, "dividend_holds": _holds,
        }, on_failure_callback=_notify_failure if notify else None, tags=["edge", "batch", LANE]) as dag:
        wait = PythonSensor(task_id="wait_for_batch_window", python_callable=batch_window_clear,
                            mode="reschedule", poke_interval=60, timeout=600, retries=0)
        rid = "{{ dividend_run(logical_date, dag_run) }}"
        plan = step(task_id="plan", taskdef_key="ops", command=["plan-run"], same_day_only=True, exclusive=False,
            # The sensor succeeds via Airflow state, not EdgeStep's ECS settlement XComs.
            stop_on_upstream_hold=False,
            env={"OPS_PIPELINE_TYPE": LANE, "OPS_ORCHESTRATOR": "AIRFLOW",
                 "OPS_ORCHESTRATOR_RUN_REF": "{{ dag.dag_id }}/{{ run_id }}",
                 "OPS_SCHEDULED_TIME": "{{ dividend_slot(logical_date, dag_run).isoformat() }}"})
        collect = DividendCollectionStep(task_id="collect", taskdef_key="kis", same_day_only=True, partial_exit_codes=(2,),
            command=["ingest-raw-dividend", "--run-id", rid, "--deadline-sec", "600"])
        normalize = step(task_id="normalize", taskdef_key="bigkinds", partial_exit_codes=(2,),
            trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
            command=["normalize-dividend", "--run-id", rid, "--input-run-id", rid])
        load = step(task_id="load", taskdef_key="rds",
            command=["load-dividend", "--run-id", rid, "--input-run-id", rid])
        report = step(task_id="report", taskdef_key="ops", command=["reconcile"], exclusive=False,
            trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS, stop_on_upstream_hold=False,
            env={"OPS_RUN_KEY": "{{ dividend_key(logical_date, dag_run) }}",
                 "OPS_ORCHESTRATION_STATUS": "{{ dividend_status(ti) }}",
                 "OPS_EXECUTION_HOLDS": "{{ dividend_holds(ti) }}",
                 "OPS_REPORT_RUN_REF": "{{ dag.dag_id }}/{{ run_id }}", "OPS_CLUSTER_ARN": CLUSTER})

        @task(trigger_rule=TriggerRule.ALL_DONE, retries=0)
        def verdict(ti=None):
            if _status(ti) != "SUCCEEDED" or ti.xcom_pull(task_ids="report", key="exit_code") != 0:
                raise AirflowFailException(f"Dividend run incomplete: {_codes(ti)}")
        wait >> plan >> collect >> normalize >> load >> report >> verdict()
        plan >> normalize
        plan >> report
    return dag


dag = build_dag()
