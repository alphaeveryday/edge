"""공시 보충 배치 레인(disclosure) — SFN `edge-*-disclosure` 의 Airflow 실행 경로(ALPHA-1407).

현행과 같은 것: 슬롯(dev 평일 KST 19:30), Planner 원장 계획, 네 ECS 스텝과 명령(statemachine.tf 잡 목록 —
tests 가 인자까지 대조), raw 부분 실패여도 정제·적재 계속, 정제 둘 다 exit 0 또는 2 면 적재 계속(그 밖의 정제
실패면 적재 안 함 — ASL DisclosureNormalizeCheckResults), 끝에서 raw·정제 부분 실패 런을 FAILED 로 마감
(ASL DisclosureRawPartialCheck).
달라진 것(의도):
- 실행 요청 = 이 DAG run. plan 은 원장만 쓰고 SFN 을 시작하지 않는다(OPS_ORCHESTRATOR=AIRFLOW).
- 업무를 시작하지 않았음이 확인된 경우(기동 실패·실행권 대기 초과)와 아직 도는 태스크 재접속만 재시도한다.
  실행 상태를 모르면 새 태스크를 띄우지 않고 보류한다(EdgeStep). SFN 스케줄은 재시도 0 이었다.
- 통보: SFN 은 raw·정제 부분 실패를 그 자리에서 한 통씩, 실패 마감을 또 한 통 보냈다(최상위 TimeoutSeconds 로
  죽으면 Catch 가 안 타서 앞당긴 것). 여기선 run 실패 때 한 통이다 — on_failure_callback 은 dagrun_timeout 으로
  죽은 run 에도 불린다.
- 시간 상한: SFN 2400초 → dagrun_timeout 1500초(Reconciler 수명 1800초보다 짧아야 한다). 실측 최대 238초.

일정·날짜:
- 슬롯은 envs/dev/main.tf `disclosure_schedule_expressions` 와 같다(모듈 기본 10슬롯이 아니라 dev 값 — tests 대조).
- 업무 기준일 = 슬롯의 KST 날짜. 수집은 날짜창을 실행 시각(오늘)과 원장 워터마크로 정해 **당일 슬롯만** 수집한다.
- 평일 공휴일에도 run 이 생긴다 — 현행 배치와 같다(MON-FRI).
- catchup 없음, max_active_runs=1: 두 실행이 같은 rcept_no 본문·canonical 파티션을 동시에 만지지 않게
  (disclosure_pipeline.tf 스케줄 재시도 0 의 근거와 같다).
- 켜기 전까지 돌지 않는다(is_paused_upon_creation). 실행 주체 전환은 terraform `disclosure_orchestrator` 와
  unpause 를 함께 한다(README "운영 전환·롤백 절차").
"""

from __future__ import annotations

import json
import os
from datetime import timedelta

from airflow.providers.amazon.aws.hooks.sns import SnsHook
from airflow.sdk import DAG, Param, TriggerRule, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from edge_batch import (CLUSTER, HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN, EdgeStep, pipeline_run_id,
                        reprocess_slot, run_key, run_status, settlement, slot_time)

LANE = "disclosure"
# envs/dev/main.tf `disclosure_schedule_expressions` 와 같은 슬롯(드리프트는 tests 가 대조).
CRONS = ("30 19 * * 1-5",)
NORMALIZE_STEPS = ("normalize", "normalize_segment")
STEPS = ("collect", *NORMALIZE_STEPS, "load")
# 원장 카탈로그 task_key(data_pipeline.ops.catalog — tests/test_airflow_dag_contract.py 가 대조).
TASK_KEYS = {"collect": "DISCLOSURE_COLLECTION_DART", "normalize": "NORMALIZE_DISCLOSURE",
             "normalize_segment": "NORMALIZE_DISCLOSURE_SEGMENT", "load": "LOAD_DISCLOSURE"}


def _judged_steps(dag_run) -> tuple[str, ...]:
    """이 run 이 판정할 스텝 — 재처리 run 은 수집을 하지 않는다."""
    return STEPS[1:] if reprocess_slot({"dag_run": dag_run}) else STEPS


def _status(ti, dag_run) -> str:
    """report 가 원장에 넘길 판정. plan 이 성공하지 않았으면 빈 값(기존 슬롯 판정을 덮지 않는다)."""
    if ti.xcom_pull(task_ids="plan", key="exit_code") != 0:
        return ""
    return run_status({s: ti.xcom_pull(task_ids=s, key="exit_code") for s in _judged_steps(dag_run)})


def _holds(ti, steps=("plan", *STEPS, "report")) -> dict:
    """이 run 에서 보류로 끝난 스텝(XCom hold 또는 결말 없는 시작 — edge_batch.settlement)."""
    return {s: h for s in steps if (h := settlement(ti, s))}


def _holds_env(ti) -> str:
    """report 가 원장에 옮길 보류 — ECS 상태·결과를 모르는 것만. 없으면 빈 값."""
    holds = {TASK_KEYS[s]: h for s, h in _holds(ti, STEPS).items()
             if h.get("kind") in (HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN)}
    return json.dumps(holds, ensure_ascii=False) if holds else ""


ALARM_TOPIC = os.environ.get("EDGE_ALARM_TOPIC_ARN") or None


def _notify_failure(context):
    """런 실패 통보 — ASL DisclosureNotifyFailure 와 같은 SNS 토픽. dagrun_timeout 으로 죽은 경우도 탄다."""
    if ALARM_TOPIC is None:
        return
    run = context["dag_run"]
    SnsHook().publish_to_target(
        target_arn=ALARM_TOPIC,
        subject=f"[{LANE}] FAILED — airflow {run.run_id}",
        message=f"dag={run.dag_id} run={run.run_id} reason={context.get('reason')}",
    )


def build_dag(dag_id: str, *, schedule, step=EdgeStep, ecs_target: dict | None = None,
              notify: bool = True) -> DAG:
    """이 레인의 DAG. tests 는 다른 EdgeStep·ECS 대상으로 같은 그래프를 만든다."""
    ecs_target = {"cluster": CLUSTER, **(ecs_target or {})}
    with DAG(
        dag_id=dag_id,
        auto_register=False,
        schedule=schedule,
        is_paused_upon_creation=True,
        catchup=False,
        max_active_runs=1,
        # Reconciler 수명(1800초)보다 짧아야 죽은 run 을 놓치지 않는다(장중 수급 DAG 와 같은 규칙).
        dagrun_timeout=timedelta(seconds=int(os.environ.get("EDGE_LAB_DAGRUN_TIMEOUT_SECONDS", "1500"))),
        params={"reprocess_slot": Param("", type="string", description=(
            "비우면 일반 run. 기존 슬롯 ISO 시각(예 2026-10-20T19:30:00+09:00)을 주면 그 슬롯의 raw 를 "
            "다시 정제·적재한다(수집 안 함, 성공 스텝 가드 해제)."))},
        user_defined_macros={
            "edge_slot": lambda logical_date, dag_run: slot_time(
                {"logical_date": logical_date, "dag_run": dag_run}),
            "edge_run_id": lambda logical_date, dag_run: pipeline_run_id(
                LANE, slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "edge_run_key": lambda logical_date, dag_run: run_key(
                LANE, slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "edge_run_status": _status,
            "edge_holds": _holds_env,
        },
        default_args={"retries": 2, "retry_delay": timedelta(seconds=30)},
        on_failure_callback=_notify_failure if notify else None,
        tags=["edge", "batch", LANE],
    ) as dag:
        rid = "{{ edge_run_id(logical_date, dag_run) }}"
        plan = step(
            **ecs_target, task_id="plan", taskdef_key="ops", command=["plan-run"], same_day_only=True,
            exclusive=False, reprocess_env={"OPS_REPROCESS": "1"},
            env={"OPS_PIPELINE_TYPE": LANE, "OPS_ORCHESTRATOR": "AIRFLOW",
                 "OPS_ORCHESTRATOR_RUN_REF": "{{ dag.dag_id }}/{{ run_id }}",
                 "OPS_SCHEDULED_TIME": "{{ edge_slot(logical_date, dag_run).isoformat() }}"},
        )
        collect = step(
            **ecs_target, task_id="collect", taskdef_key="dart", same_day_only=True, noop_on_reprocess=True,
            skip_if_succeeded=True,                   # 외부 호출(DART)이 있는 스텝만 성공 이력 skip
            command=["ingest-raw-disclosure", "--run-id", rid],
        )
        # raw 부분 실패여도 정제는 돈다(ASL DisclosureNotifyRawPartial → Normalize). plan 실패면 돌지 않는다.
        # exit 2 는 성공 winner 를 commit 한 입력 부분 실패라 적재로 넘긴다. 그 밖의 정제 실패면 적재는
        # upstream_failed 로 돌지 않는다(ASL Default → DisclosureNotifyFailure).
        normalize = step(
            **ecs_target, task_id="normalize", taskdef_key="bigkinds", partial_exit_codes=(2,),
            trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
            command=["normalize-disclosure", "--run-id", rid, "--input-run-id", rid],
        )
        normalize_segment = step(
            **ecs_target, task_id="normalize_segment", taskdef_key="bigkinds", partial_exit_codes=(2,),
            trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
            command=["normalize-disclosure-segment", "--run-id", rid, "--input-run-id", rid],
        )
        # ASL DisclosureFeatureCheckResults 는 exit 0 만 성공으로 본다 — 부분 실패 코드를 두지 않는다.
        load = step(
            **ecs_target, task_id="load", taskdef_key="rds",
            command=["load-disclosure", "--run-id", rid, "--input-run-id", rid],
        )

        report = step(
            **ecs_target, task_id="report", taskdef_key="ops", command=["reconcile"], exclusive=False,
            trigger_rule=TriggerRule.ALL_DONE, stop_on_upstream_hold=False,
            env={"OPS_RUN_KEY": "{{ edge_run_key(logical_date, dag_run) }}",
                 "OPS_ORCHESTRATION_STATUS": "{{ edge_run_status(ti, dag_run) }}",
                 "OPS_EXECUTION_HOLDS": "{{ edge_holds(ti) }}",
                 "OPS_REPORT_RUN_REF": "{{ dag.dag_id }}/{{ run_id }}",
                 "OPS_CLUSTER_ARN": ecs_target["cluster"]},
        )

        @task(trigger_rule=TriggerRule.ALL_DONE, retries=0)
        def verdict(ti=None, dag_run=None):
            """ASL DisclosureRawPartialCheck — 실행한 스텝이 모두 exit 0 일 때만 런 성공."""
            codes = {s: ti.xcom_pull(task_ids=s, key="exit_code") for s in _judged_steps(dag_run)}
            holds = _holds(ti)
            if holds:
                raise AirflowFailException(f"실행 보류 {holds} exit_codes={codes}")
            if run_status(codes) != "SUCCEEDED":
                raise AirflowFailException(f"런 실패 마감 exit_codes={codes}")
            report_code = ti.xcom_pull(task_ids="report", key="exit_code")
            if report_code != 0:
                raise AirflowFailException(f"업무는 성공했으나 원장 판정 보고 실패(report exit={report_code})")
            return codes

        plan >> collect >> [normalize, normalize_segment] >> load >> report >> verdict()
        plan >> [normalize, normalize_segment]
        # 정제 하나가 실패하면 load 는 다른 정제를 기다리지 않고 upstream_failed 가 된다(all_success). report 가 두
        # 정제를 직접 기다려야 아직 안 끝난 정제를 보류(결말 없는 시작)로 옮기거나 판정에서 빠뜨리지 않는다.
        [normalize, normalize_segment] >> report
    return dag


dag = build_dag("edge_disclosure",
                schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)))
