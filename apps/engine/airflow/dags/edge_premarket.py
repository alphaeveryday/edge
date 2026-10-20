"""장전 유니버스 레인(premarket) — SFN `edge-*-premarket` 의 Airflow 실행 경로(ALPHA-1408).

현행과 같은 것: 슬롯(평일 KST 07:00), 세 ECS 스텝과 명령(statemachine.tf·premarket_pipeline.tf 잡 목록 — tests 가
인자까지 대조), 전량 성공 게이트(어느 스텝이든 exit 0 이 아니면 뒤 스텝을 돌리지 않고 실패 — ASL
Premarket*CheckResults Default), 원장 밖(Planner·report 없음 — 같은 CLI 를 시장 레인이 소유한다,
premarket_pipeline.tf 서두), run_id = 슬롯 시각의 UTC `%Y%m%dT%H%M%SZ`(ASL PremarketMakeRunId 와 같다).
달라진 것(의도):
- 실행 요청 = 이 DAG run(SFN 직접 시작 대신). 업무 미시작이 확인된 경우와 도는 태스크 재접속만 재시도한다
  (EdgeStep). SFN 스케줄은 시작 재시도 3 이었다.
- 실행권 가드(`exclusive`)를 켜지 않는다 — 원장에 이 run 의 기대 작업이 없어 지킬 것이 없고, 실행권은 작업 키
  단위라 같은 CLI 를 쓰는 시장 레인 작업의 실행권을 잡게 된다. 성공 skip 도 같은 이유로 없다.
- 통보: SFN 의 ExecutionsFailed·ExecutionsTimedOut 알람과 정의 안 SNS 대신 run 실패 때 SNS 한 통
  (dagrun_timeout 포함). ⚠️ "실행이 아예 안 생김"을 잡던 스케줄러 DLQ 알람의 대체는 없다 — Airflow 서비스가 죽으면
  서비스 정지 경보가 울지만, 서비스는 살아 있는데 run 이 안 생기면(DAG import 오류·pause) 아무것도 울지 않는다.
  원장 밖이라 PLANNER_MISSING 도 없다(README "장전 유니버스 레인").
- 보류(실행 상태 미상)는 원장에 남지 않는다(원장 밖 레인) — Airflow task 실패·XCom `hold` 로만 보인다.
- 재처리 run(conf `reprocess_slot`)을 받지 않는다 — 원장 밖이라 Planner 의 "raw 가 있는 기존 슬롯" 확인이 없고,
  EdgeStep 은 재처리 run 의 당일 검사를 건너뛰어 오늘 KRX 값이 과거 run_id 로 쓰이거나 옛 유니버스가 오늘 것을
  덮는다. SFN 에도 재처리 경로가 없었다.

일정·날짜:
- 07:30 안에 끝나야 한다 — build-minute-universe 가 거래일 07:30 뒤 교체를 스스로 거부한다(ALPHA-953).
  dagrun_timeout 1500초는 SFN TimeoutSeconds 와 같다. 실측(SFN 최근 40회) p50 217초·최대 376초.
- 수집은 당일 슬롯만 — KRX 는 실행 시각의 PDF 를 주므로 과거 슬롯 run 이 오늘 값을 옛 run_id 로 라벨하면 안 된다.
- 공휴일에도 돈다(MON-FRI) — 유니버스가 안 바뀌어 build 가 no-op 이다(현행과 같다).
- 켜기 전까지 돌지 않는다(is_paused_upon_creation). 전환은 terraform `premarket_orchestrator` 와 unpause 를 함께
  한다(README "장전 유니버스 레인").
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone

from airflow.providers.amazon.aws.hooks.sns import SnsHook
from airflow.sdk import DAG, TriggerRule, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from edge_batch import CLUSTER, EdgeStep, lane_arg, reprocess_slot, run_status, settlement, slot_time

LANE = "premarket"
# variables.tf `premarket_schedule_expression` 와 같은 슬롯(드리프트는 tests 가 대조).
CRONS = ("0 7 * * 1-5",)
STEPS = ("collect", "normalize", "universe")


def utc_run_id(slot) -> str:
    """ASL PremarketMakeRunId — 슬롯 시각(실행 시각이 아니다)의 UTC `%Y%m%dT%H%M%SZ`(run.make_run_id 와 같은 형식).
    재시도가 늦게 떠도 같은 run_id 로 수렴한다."""
    return slot.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


ALARM_TOPIC = os.environ.get("EDGE_ALARM_TOPIC_ARN") or None


def _notify_failure(context):
    """런 실패 통보 — ASL PremarketNotifyFailure 와 같은 SNS 토픽. dagrun_timeout 으로 죽은 경우도 탄다."""
    if ALARM_TOPIC is None:
        return
    run = context["dag_run"]
    SnsHook().publish_to_target(
        target_arn=ALARM_TOPIC,
        subject=f"[{LANE}] FAILED — airflow {run.run_id}",
        message=f"dag={run.dag_id} run={run.run_id} reason={context.get('reason')} — 오늘 신규 편입 종목이 분봉을 못 받는다",
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
        dagrun_timeout=timedelta(seconds=int(os.environ.get("EDGE_LAB_DAGRUN_TIMEOUT_SECONDS", "1500"))),
        user_defined_macros={
            "edge_run_id": lambda logical_date, dag_run: utc_run_id(
                slot_time({"logical_date": logical_date, "dag_run": dag_run})),
            "edge_lane_arg": lane_arg,
        },
        default_args={"retries": 2, "retry_delay": timedelta(seconds=30)},
        on_failure_callback=_notify_failure if notify else None,
        tags=["edge", "batch", LANE],
    ) as dag:
        rid = "{{ edge_run_id(logical_date, dag_run) }}"

        @task(retries=0)
        def refuse_reprocess(dag_run=None):
            """재처리 conf 를 거부한다(위 '달라진 것'). 실패하면 뒤 스텝은 upstream_failed 로 돌지 않는다."""
            if reprocess_slot({"dag_run": dag_run}):
                raise AirflowFailException("장전 레인은 재처리 run 을 받지 않는다(원장 밖 — 슬롯 확인 없음)")

        collect = step(
            **ecs_target, task_id="collect", taskdef_key="krx", same_day_only=True, exclusive=False,
            # 선행은 EdgeStep 이 아닌 재처리 거부 task 라 결말 XCom(exit_code·no_ecs_task)이 없다 — 보류 검사를 켜면
            # 정상 run 마다 "결말 미확인"으로 수집을 건너뛴다(배당 DAG 의 plan 과 같은 처리).
            stop_on_upstream_hold=False,
            command=["ingest-raw-etf", "--source", "krx", "--run-id", rid,
                     "--deadline-sec", "{{ edge_lane_arg('krx_etf_deadline_sec') }}"],
        )
        normalize = step(
            **ecs_target, task_id="normalize", taskdef_key="bigkinds", exclusive=False,
            command=["normalize-etf", "--run-id", rid, "--input-run-id", rid],
        )
        # --universe 는 분 레인 소비자가 읽는 그 URI 다(terraform local.minute_universe_uri — 상수로 박지 않는다).
        universe = step(
            **ecs_target, task_id="universe", taskdef_key="bigkinds", exclusive=False,
            command=["build-minute-universe", "--run-id", rid,
                     "--universe", "{{ edge_lane_arg('minute_universe_uri') }}"],
        )

        @task(trigger_rule=TriggerRule.ALL_DONE, retries=0)
        def verdict(ti=None):
            """ASL PremarketUniverseCheckResults — 세 스텝이 모두 exit 0 일 때만 런 성공."""
            codes = {s: ti.xcom_pull(task_ids=s, key="exit_code") for s in STEPS}
            holds = {s: h for s in STEPS if (h := settlement(ti, s))}
            if holds:
                raise AirflowFailException(f"실행 보류 {holds} exit_codes={codes}")
            if run_status(codes) != "SUCCEEDED":
                raise AirflowFailException(f"런 실패 마감 exit_codes={codes}")
            return codes

        refuse_reprocess() >> collect >> normalize >> universe >> verdict()
    return dag


dag = build_dag("edge_premarket",
                schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)))
