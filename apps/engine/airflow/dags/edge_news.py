"""뉴스 레인(news) — SFN `edge-*-news` 의 Airflow 실행 경로(ALPHA-1409).

현행과 같은 것: 슬롯(매일 KST 00:10·08:10), Planner 원장 계획, 여섯 ECS 스텝과 명령(statemachine.tf 잡 목록과
news_pipeline.tf 직렬 state — tests 가 인자까지 대조), 분기 규칙(ASL):
- 수집이 실패해도 정제는 돈다(NewsNotifyRawPartial → Normalize).
- 정제는 exit 0 만 통과 — 아니면 뒤를 돌리지 않는다(NewsNormalizeCheckResults Default).
- 태깅·문서 적재 둘 다 exit 0 이어야 assertion 적재(NewsFeatureCheckResults), 그 뒤 이벤트 조립이 직렬로
  exit 0 이어야 한다(document FK·자연키 브리지).
- 끝에서 수집 부분 실패 런을 FAILED 로 마감(NewsRawPartialCheck).
FMP 수집(CollectFmpNews)은 `us_fmp_enabled` 일 때만 SFN 에 들어간다 — dev 는 꺼져 있어 이 DAG 엔 없다(tests 가
켜짐을 감지하면 실패한다).
달라진 것(의도):
- 실행 요청 = 이 DAG run. plan 은 원장만 쓰고 SFN 을 시작하지 않는다(OPS_ORCHESTRATOR=AIRFLOW).
- 업무 미시작이 확인된 경우와 도는 태스크 재접속만 재시도한다(EdgeStep). SFN 스케줄은 재시도 0 이었다.
- 태깅(LLM)·수집(BigKinds)은 성공 이력이 있으면 다시 부르지 않는다(외부 호출 스텝만 — 장중 수급과 같은 규칙).
- SFN 은 태깅·문서 적재를 병렬로 돌렸다. 여기선 PARALLELISM=1 이라 한 줄로 돈다 — 그만큼 길어진다.
- 시간 상한: SFN 2400초 → dagrun_timeout 1700초. Reconciler 수명(1800초)보다 짧아야 해서 SFN 값을 못 쓴다.
  실측(SFN 최근 40회, 병렬 실행) p50 477초·최대 1099초. 직렬로 늘어난 뒤의 값은 전환 전에 잰다(README).
- 통보: 부분 실패 즉시 통보 대신 run 실패 때 SNS 한 통(dagrun_timeout 포함).

일정·날짜:
- 수집 창은 실행 시각 기준 [어제, 오늘]이다 — 과거 슬롯 run 이 오늘 기사를 옛 run_id 로 라벨하지 않게 당일 슬롯만
  수집한다. 이벤트 조립 창도 실행 시각 기준(오늘−N일)이다.
- 08:10 run 은 뉴스 분 격자 시작(09:00) 전에 끝나야 한다(variables.tf `news_state_machine_timeout_seconds`).
- catchup 없음, max_active_runs=1: 두 실행의 이벤트 조립이 같은 미threaded event 를 동시에 처리하지 않게.
- 켜기 전까지 돌지 않는다(is_paused_upon_creation). 전환은 terraform `news_orchestrator` 와 unpause 를 함께
  한다(README "뉴스 레인").
"""

from __future__ import annotations

import json
import os
from datetime import timedelta

from airflow.providers.amazon.aws.hooks.sns import SnsHook
from airflow.sdk import DAG, Param, TriggerRule, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from edge_batch import (CLUSTER, HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN, EdgeStep, lane_arg, pipeline_run_id,
                        reprocess_slot, run_key, run_status, settlement, slot_time)

LANE = "news"
# variables.tf `news_schedule_expressions` 와 같은 슬롯(드리프트는 tests 가 대조).
CRONS = ("10 0 * * *", "10 8 * * *")
FEATURE_STEPS = ("tag", "load_documents")
STEPS = ("collect", "normalize", *FEATURE_STEPS, "load_assertions", "assemble")
# 원장 카탈로그 task_key(data_pipeline.ops.catalog — tests/test_airflow_dag_contract.py 가 대조).
TASK_KEYS = {"collect": "NEWS_COLLECTION_BIGKINDS", "normalize": "NORMALIZE_NEWS", "tag": "TAG_NEWS",
             "load_documents": "LOAD_DOCUMENTS", "load_assertions": "LOAD_ASSERTIONS",
             "assemble": "ASSEMBLE_EVENTS"}


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
    """런 실패 통보 — ASL NewsNotifyFailure 와 같은 SNS 토픽. dagrun_timeout 으로 죽은 경우도 탄다."""
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
        # Reconciler 수명(1800초)보다 짧아야 죽은 run 을 놓치지 않는다 — 그 안에서 가장 긴 값(SFN 은 2400초).
        dagrun_timeout=timedelta(seconds=int(os.environ.get("EDGE_LAB_DAGRUN_TIMEOUT_SECONDS", "1700"))),
        params={"reprocess_slot": Param("", type="string", description=(
            "비우면 일반 run. 기존 슬롯 ISO 시각(예 2026-10-20T08:10:00+09:00)을 주면 그 슬롯의 raw 를 "
            "다시 정제·태깅·적재·조립한다(수집 안 함, 성공 스텝 가드 해제)."))},
        user_defined_macros={
            "edge_slot": lambda logical_date, dag_run: slot_time(
                {"logical_date": logical_date, "dag_run": dag_run}),
            "edge_run_id": lambda logical_date, dag_run: pipeline_run_id(
                LANE, slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "edge_run_key": lambda logical_date, dag_run: run_key(
                LANE, slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "edge_run_status": _status,
            "edge_holds": _holds_env,
            "edge_lane_arg": lane_arg,
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
            **ecs_target, task_id="collect", taskdef_key="bigkinds", same_day_only=True, noop_on_reprocess=True,
            skip_if_succeeded=True,                   # 외부 호출(BigKinds)
            command=["ingest-raw", "--source", "bigkinds", "--run-id", rid],
        )
        # raw 부분 실패여도 정제는 돈다(ASL NewsNotifyRawPartial → Normalize). plan 실패면 돌지 않는다.
        normalize = step(
            **ecs_target, task_id="normalize", taskdef_key="bigkinds",
            trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
            command=["normalize-news", "--run-id", rid, "--input-run-id", rid],
        )
        tag = step(
            **ecs_target, task_id="tag", taskdef_key="deepseek",
            skip_if_succeeded=True,                   # 외부 호출(LLM) — 기사당 과금
            command=["tag-news", "--run-id", rid, "--input-run-id", rid,
                     "--limit", "{{ edge_lane_arg('tag_news_limit') }}"],
        )
        load_documents = step(
            **ecs_target, task_id="load_documents", taskdef_key="rds",
            command=["load-documents", "--run-id", rid, "--input-run-id", rid],
        )
        load_assertions = step(
            **ecs_target, task_id="load_assertions", taskdef_key="rds",
            command=["load-assertions", "--run-id", rid, "--input-run-id", rid],
        )
        assemble = step(
            **ecs_target, task_id="assemble", taskdef_key="events",
            command=["assemble-events", "--run-id", rid,
                     "--window-days", "{{ edge_lane_arg('assemble_window_days') }}"],
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
            """ASL NewsRawPartialCheck — 실행한 스텝이 모두 exit 0 일 때만 런 성공."""
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

        plan >> collect >> normalize >> [tag, load_documents] >> load_assertions >> assemble >> report >> verdict()
        plan >> normalize
        # 태깅·문서 적재 중 하나가 실패하면 assertion 적재는 다른 하나를 기다리지 않고 upstream_failed 가 된다
        # (all_success). report 가 둘을 직접 기다려야 아직 안 끝난 스텝을 보류로 옮기거나 판정에서 빠뜨리지 않는다.
        [tag, load_documents, load_assertions] >> report
    return dag


dag = build_dag("edge_news",
                schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)))
