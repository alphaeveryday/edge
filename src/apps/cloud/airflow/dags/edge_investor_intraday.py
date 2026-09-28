"""장중 수급 레인(investor-intraday) — SFN `edge-*-investor-intraday` 의 Airflow 실행 경로.

현행과 같은 것: 슬롯(평일 KST 09:35·10:05·11:25·13:25·14:35), Planner 원장 계획, 세 ECS 스텝과 명령,
raw 부분 실패여도 정제·적재 계속, 정제 exit 2 면 적재 계속, 끝에서 런 FAILED 마감.
달라진 것(의도):
- 정제·적재 exit 2 가 실제로 하류를 계속 탄다. 현행 ASL 은 runTask.sync 가 비0 종료를 TaskFailed 로
  올려 Catch 경로(exit_code 없음)로 가므로 "exit 2 면 적재 계속" 분기에 도달하지 못한다(dev 실행 이력 확인).
- 실행 요청 = 이 DAG run. plan 은 원장만 쓰고 SFN 을 시작하지 않는다(OPS_ORCHESTRATOR=AIRFLOW).
- 업무를 시작하지 않았음이 확인된 경우(기동 실패·실행권 대기 초과)와 아직 도는 태스크 재접속만 재시도한다.
  실행 상태를 모르면(제출 응답 유실·조회 실패·강제 종료) 새 태스크를 띄우지 않고 보류한다(EdgeStep).

일정·날짜:
- logical_date = cron 발화 시각(슬롯). 데이터 구간 개념이 없는 trigger timetable 이다.
- 업무 기준일 = 슬롯의 KST 날짜. 장중 추정 수집은 날짜 인자가 없어 **당일 슬롯만** 수집한다.
- 휴장일도 현행처럼 run 이 생긴다 — Planner 가 SKIPPED 로 계획하고 수집은 skip(exit 0)이다.
- catchup 없음: 활성화 전 슬롯을 소급 실행하지 않는다(소급 수집은 오늘 값을 과거로 라벨한다).
- max_active_runs=1: 같은 거래일 canonical 파티션을 슬롯들이 CAS 없이 병합한다(ALPHA-1057).
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

LANE = "investor-intraday"
# variables.tf `investor_intraday_schedule_expressions` 와 같은 슬롯(드리프트는 tests 가 대조).
CRONS = ("35 9 * * 1-5", "5 10 * * 1-5", "25 11 * * 1-5", "25 13 * * 1-5", "35 14 * * 1-5")
STEPS = ("collect", "normalize", "load")
# 원장 카탈로그 task_key(data_pipeline.ops.catalog — tests/test_airflow_dag_contract.py 가 대조).
TASK_KEYS = {"collect": "INVESTOR_INTRADAY_COLLECTION_KIS", "normalize": "NORMALIZE_INVESTOR_INTRADAY",
             "load": "LOAD_INVESTOR_INTRADAY"}


def _judged_steps(dag_run) -> tuple[str, ...]:
    """이 run 이 판정할 스텝 — 재처리 run 은 수집을 하지 않는다. conf 를 읽는다(EdgeStep 과 같은 원천:
    params 는 core.dag_run_conf_overrides_params 설정에 따라 conf 와 갈릴 수 있다)."""
    return STEPS[1:] if reprocess_slot({"dag_run": dag_run}) else STEPS


def _status(ti, dag_run) -> str:
    """report 가 원장에 넘길 판정. plan 이 성공하지 않았으면 빈 값(보고 없음) — 이 run 은 업무를 건드리지
    않았으므로 그 슬롯의 기존 판정(예: 앞 run 의 SUCCEEDED)을 덮으면 안 된다."""
    if ti.xcom_pull(task_ids="plan", key="exit_code") != 0:
        return ""
    return run_status({s: ti.xcom_pull(task_ids=s, key="exit_code") for s in _judged_steps(dag_run)})


def _holds(ti, steps=("plan", *STEPS, "report")) -> dict:
    """이 run 에서 보류로 끝난 스텝 {task_id: {"kind","reason"}} — XCom `hold`, 그리고 시작했는데 결말 없이 끝난
    스텝(worker 사망·수동 failed: `settlement`). plan·report 보류도 런 판정에 드러낸다(원장 기록은 업무 스텝만 —
    plan·report 는 카탈로그 작업이 아니다). report 를 렌더링하는 시점엔 report 자신은 아직 시작 전이다."""
    return {s: h for s in steps if (h := settlement(ti, s))}


def _holds_env(ti) -> str:
    """report 가 원장에 옮길 보류 — ECS 상태·결과를 모르는 것만. 컨테이너 보류(OPEN_ATTEMPT)는 컨테이너가 이미
    원장에 남겼다. JSON {task_key: {"kind","reason"}}, 없으면 빈 값."""
    holds = {TASK_KEYS[s]: h for s, h in _holds(ti, STEPS).items()
             if h.get("kind") in (HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN)}
    return json.dumps(holds, ensure_ascii=False) if holds else ""


ALARM_TOPIC = os.environ.get("EDGE_ALARM_TOPIC_ARN") or None


def _notify_failure(context):
    """런 실패 통보 — ASL NotifyFailure 와 같은 SNS 토픽. dagrun_timeout 으로 죽은 경우도 탄다."""
    if ALARM_TOPIC is None:
        return
    run = context["dag_run"]
    SnsHook().publish_to_target(
        target_arn=ALARM_TOPIC,
        subject=f"[{LANE}] FAILED — airflow {run.run_id}",
        message=f"dag={run.dag_id} run={run.run_id} reason={context.get('reason')}",
    )


with DAG(
    dag_id="edge_investor_intraday",
    # run_immediately=timedelta(0): 활성화(unpause) 시 이미 지난 슬롯을 즉시 돌리지 않는다. 3.3.2 는
    # False 를 "다음 슬롯까지 대기"로 문서화했지만 코드는 None 처럼 다루어, 슬롯 뒤 max(다음 발화까지의
    # 10%, 5분) 안에 켜면 그 슬롯을 즉시 만든다 — 하루 1회 cron 5개라 창이 2.4시간이다(로컬 관찰: 11:25
    # 슬롯이 11:45 unpause 에 즉시 실행). 전환 절차상 15:00 에 켜면 SFN 이 끝낸 14:35 슬롯이 다시 불린다.
    schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)),
    catchup=False,
    max_active_runs=1,
    # SFN TimeoutSeconds(1500)와 같다: 최소 슬롯 간격(30분)보다 짧아 다음 슬롯과 겹치지 않는다.
    # 로컬 검증만 `EDGE_LAB_DAGRUN_TIMEOUT_SECONDS` 로 줄인다(시간 초과 경로 재현) — 운영 환경엔 두지 않는다.
    # 시간 초과로 report·verdict 가 돌지 못해도 주기 Reconciler 가 원장·ECS 로 미확정 실행을 찾아 보류로 남긴다
    # (reconciler.sweep_airflow_runs). 그 수명 기준(OPS_AIRFLOW_RUN_LIFETIME_SECONDS)은 이 값보다 커야 한다.
    dagrun_timeout=timedelta(seconds=int(os.environ.get("EDGE_LAB_DAGRUN_TIMEOUT_SECONDS", "1500"))),
    params={"reprocess_slot": Param("", type="string", description=(
        "비우면 일반 run. 기존 슬롯 ISO 시각(예 2026-09-22T10:05:00+09:00)을 주면 그 슬롯의 raw 를 "
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
    on_failure_callback=_notify_failure,
    tags=["edge", "batch", LANE],
) as dag:
    rid = "{{ edge_run_id(logical_date, dag_run) }}"
    # 당일 슬롯만 계획한다 — 과거 날짜 run(backfill·수동 -l)은 원장에 계획을 남기기 전에 거부.
    # 재처리 run 은 기존 슬롯의 계획에 수렴한다(created=False).
    plan = EdgeStep(
        task_id="plan", taskdef_key="ops", command=["plan-run"], same_day_only=True, exclusive=False,
        reprocess_env={"OPS_REPROCESS": "1"},     # 재처리는 raw 가 있는 기존 슬롯만(Planner 가 확인)
        env={"OPS_PIPELINE_TYPE": LANE, "OPS_ORCHESTRATOR": "AIRFLOW",
             "OPS_ORCHESTRATOR_RUN_REF": "{{ dag.dag_id }}/{{ run_id }}",
             "OPS_SCHEDULED_TIME": "{{ edge_slot(logical_date, dag_run).isoformat() }}"},
    )
    collect = EdgeStep(
        task_id="collect", taskdef_key="kis", same_day_only=True, noop_on_reprocess=True,
        skip_if_succeeded=True,                   # 외부 호출(KIS)이 있는 스텝만 성공 이력 skip
        command=["ingest-raw-investor-estimate", "--max-failed-symbols", "1", "--run-id", rid],
    )
    # raw 부분 실패여도 정제는 돈다(ASL NotifyRawPartial → Normalize). plan 실패면 돌지 않는다.
    normalize = EdgeStep(
        task_id="normalize", taskdef_key="bigkinds", partial_exit_codes=(2,),
        trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
        command=["normalize-investor-estimate", "--run-id", rid, "--input-run-id", rid],
    )
    load = EdgeStep(
        task_id="load", taskdef_key="rds", partial_exit_codes=(2,),
        command=["load-investor-intraday", "--run-id", rid, "--input-run-id", rid],
    )

    # 이 run 의 판정을 원장에 보고하고 그 run_key 를 즉시 대조한다(Reconciler, ops 태스크). 주기 대조는
    # 정해진 다섯 슬롯만 보므로 수동·재처리 run 은 이것 없이는 orchestration_status 가 비거나 낡는다.
    # verdict 앞에 둔다 — DAG run 상태는 마지막(leaf) task 가 정하므로 보고 성공이 런 실패를 가리지 않게.
    report = EdgeStep(
        task_id="report", taskdef_key="ops", command=["reconcile"], exclusive=False,
        trigger_rule=TriggerRule.ALL_DONE, stop_on_upstream_hold=False,     # 보류를 원장에 옮기는 것이 일이다
        env={"OPS_RUN_KEY": "{{ edge_run_key(logical_date, dag_run) }}",
             "OPS_ORCHESTRATION_STATUS": "{{ edge_run_status(ti, dag_run) }}",
             # 보류를 원장에 남겨야 재처리·수동 trigger 가 그 작업을 우회하지 못한다(원장 게이트).
             "OPS_EXECUTION_HOLDS": "{{ edge_holds(ti) }}",
             # 이 보고가 어느 DAG run 의 것인지 — 늦게 도착한 옛 run 의 보고가 새 run 의 판정을 덮지 않게 한다.
             "OPS_REPORT_RUN_REF": "{{ dag.dag_id }}/{{ run_id }}",
             "OPS_CLUSTER_ARN": CLUSTER},
    )

    @task(trigger_rule=TriggerRule.ALL_DONE, retries=0)
    def verdict(ti=None, dag_run=None):
        """ASL RawPartialCheck — 실행한 스텝이 모두 exit 0 일 때만 런 성공. 업무 완료 판정은 원장이 한다.
        재처리 run 은 수집을 하지 않으므로 정제·적재만 본다."""
        codes = {step: ti.xcom_pull(task_ids=step, key="exit_code") for step in _judged_steps(dag_run)}
        holds = _holds(ti)
        if holds:
            # 결손·업무 실패와 다른 사유 — 운영자가 기존 작업 종료를 확인해야 한다(README "보류 해제").
            raise AirflowFailException(f"실행 보류 {holds} exit_codes={codes}")
        if run_status(codes) != "SUCCEEDED":
            raise AirflowFailException(f"런 실패 마감 exit_codes={codes}")
        # 판정 보고가 원장에 닿지 않았으면 성공으로 닫지 않는다 — 재처리 run 은 뒤에 이 run_key 를 다시
        # 대조할 주기 실행이 없을 수 있어, 여기서 성공하면 원장의 낡은 상태가 영영 남는다.
        report_code = ti.xcom_pull(task_ids="report", key="exit_code")
        if report_code != 0:
            raise AirflowFailException(f"업무는 성공했으나 원장 판정 보고 실패(report exit={report_code})")
        return codes

    plan >> collect >> normalize >> load >> report >> verdict()
    plan >> normalize
