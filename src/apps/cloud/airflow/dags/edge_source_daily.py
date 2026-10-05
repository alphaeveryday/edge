"""원천 관측 레인(source-daily) — 매크로 5계열·KIS 지수업종·DART 재무 지표 (ALPHA-1130).

SFN 이 없는 **Airflow 전용 레인**이다. 업무는 기존 `data_pipeline.run` 스텝이 하고(`steps/source_observations`),
이 DAG 는 슬롯·의존·ECS 실행·원장 보고만 한다. 계약 정본은 docs/design/etf-data-storage-plan.md §10.

일정 — 매일 05:20 KST 한 슬롯(주말·휴일 포함). 전망 배치(analysis-v2, 매일 06:00 KST)가 기준시각에 읽으려면 그 전에
적재가 끝나야 한다 — run 상한(dagrun_timeout 1500초)을 다 써도 05:45 다. 같은 호스트의 장중 수급 DAG(09:35~14:35)와
겹치지 않는다. 이 시각에 공급자가 무엇을 주는가:
- 매크로(ECOS 원/달러·국고채 10년, FRED 미 국채 10년, EIA 브렌트): 그 시각까지 게시된 값만 받는다. 게시 시각은 실측하지
  않았다 — 더 늦게 게시된 값은 다음 날 슬롯이 받는다(소급 창 14일, 브렌트 28일). 받은 시각이 그대로 가시시각이다.
- KOSIS CPI 전년동월비: 통계청 공표가 공표일 08:00 KST — 다음 날 슬롯이 받는다(06:00 전망은 어느 슬롯이 받아도 다음 날부터 본다).
- DART 정기보고서: 접수일 다음날 00:00 KST 부터 보이는 계약이라 05:20 수집이 전날 접수분까지 받는다.
- KIS 마스터: 비거래일엔 받지 않는다(수집 스텝이 스스로 건너뛰고 원장도 SKIPPED 로 계획한다).
주말·휴일 슬롯은 US·DART 늦은 게시 흡수용이다 — 새 값이 없으면 같은 값의 판본만 추가된다(수신시각 이력).

정기·백필 구분:
- 정기 run(스케줄): 수집 창은 스텝이 정한다 — 매크로 (어제−소급일 ~ 어제), 재무 접수일 (오늘−14 ~ 오늘).
- 백필 run(수동 trigger + params `macro_from/macro_to`·`financial_from/financial_to`): 관측 기간·접수일 기간을
  명시한다. 비우면 정기 창이다. 매크로 `to` 는 어제까지, 재무 `to` 는 오늘까지만 받는다(스텝이 거부) —
  과거 날짜 요청이 오늘 자료를 과거로 라벨하지 않는다. 업종 마스터는 현재값뿐이라 백필 인자가 없다.
  한 run 은 1500초 안에 끝나야 한다(Reconciler 수명 1800초) — 긴 백필은 1년 단위로 나눠 trigger 한다.
  run_id 는 슬롯(분 단위)에서 나온다 — 청크는 1분 이상 간격을 두고 trigger 한다. 같은 분에 겹치면 두 번째 run 의
  수집 스텝이 '다른 요청 범위로 이미 수집됐다'로 실패한다(조용히 건너뛰지 않는다).
- 재처리(conf `reprocess_slot`): 이미 있는 raw 로 정제·적재만 다시 한다(공급자 재호출 없음).

재시도 책임: 공급자 일시 오류는 스텝 안 HTTP 클라이언트가 재시도한다. Airflow 는 컨테이너가 업무를 시작하지
않았을 때만(EdgeStep exit 75) 재시도한다 — 두 층이 곱해지지 않는다. 같은 run_id 재수집은 완료 raw manifest 가
있으면 공급자를 부르지 않는다.

DAG 는 생성 시 pause 다 — 켜는 절차와 결측 판정 env 는 README "원천 관측 레인".
"""

from __future__ import annotations

import json
import os
from datetime import timedelta

from airflow.providers.amazon.aws.hooks.logs import AwsLogsHook
from airflow.providers.amazon.aws.hooks.sns import SnsHook
from airflow.sdk import DAG, Param, TriggerRule, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from edge_batch import (CLUSTER, HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN, EdgeStep, pipeline_run_id,
                        reprocess_slot, run_key, run_status, settlement, slot_time)

LANE = "source-daily"
CRONS = ("20 5 * * *",)
# 계열 → 단계 → (원장 task_key, 태스크 정의 키, CLI 스텝). tests/test_airflow_dag_contract.py 가 카탈로그와 대조한다.
FAMILIES = {
    "macro": {"collect": ("MACRO_COLLECTION", "macro", "ingest-raw-macro"),
              "normalize": ("NORMALIZE_MACRO", "bigkinds", "normalize-macro"),
              "load": ("LOAD_MACRO", "rds", "load-macro")},
    "sector": {"collect": ("SECTOR_COLLECTION_KIS", "bigkinds", "ingest-raw-sector"),
               "normalize": ("NORMALIZE_SECTOR", "bigkinds", "normalize-sector"),
               "load": ("LOAD_SECTOR", "rds", "load-sector")},
    "financial": {"collect": ("FINANCIAL_METRIC_COLLECTION_DART", "dart", "ingest-raw-financial-metric"),
                  "normalize": ("NORMALIZE_FINANCIAL_METRIC", "bigkinds", "normalize-financial-metric"),
                  "load": ("LOAD_FINANCIAL_METRIC", "rds", "load-financial-metric")},
}
TASK_KEYS = {f"{family}_{stage}": spec[0] for family, stages in FAMILIES.items() for stage, spec in stages.items()}
STEPS = tuple(TASK_KEYS)
# 수집 스텝의 백필 인자(params 이름). 업종은 현재값만 주는 원천이라 없다.
BACKFILL_PARAMS = {"macro": ("macro_from", "macro_to"), "financial": ("financial_from", "financial_to")}


def _judged_steps(dag_run) -> tuple[str, ...]:
    """이 run 이 판정할 스텝 — 재처리 run 은 수집을 하지 않는다."""
    if reprocess_slot({"dag_run": dag_run}):
        return tuple(s for s in STEPS if not s.endswith("_collect"))
    return STEPS


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
# 정제 컨테이너가 거부 요약을 남기는 로그 줄의 표지(data_pipeline.steps.source_observations.REJECT_SUMMARY_MARK 와 같다 —
# data-pipeline tests/test_airflow_dag_contract.py 가 대조한다).
REJECT_SUMMARY_MARK = "EDGE_REJECT_SUMMARY "
# 통보 제목에 "적재 완료"를 덧붙여도 되는 거부 분류 — 이 파서가 읽지 못하는 표기·계정. 오류(error)가 하나라도 있으면 덧붙이지 않는다.
UNSUPPORTED_ONLY = frozenset({"unsupported"})


def _reject_summary(ti, task) -> dict | None:
    """정제 스텝의 거부 요약 — 그 ECS 태스크의 CloudWatch 로그에서 표지 줄을 찾는다. 로그 설정·ARN·줄이 없으면 None."""
    arn = ti.xcom_pull(task_ids=task.task_id, key="ecs_task_arn")
    if not (arn and task.awslogs_group and task.awslogs_stream_prefix):
        return None
    events = AwsLogsHook(aws_conn_id=task.aws_conn_id, region_name=task.awslogs_region).conn.get_log_events(
        logGroupName=task.awslogs_group, logStreamName=f"{task.awslogs_stream_prefix}/{arn.rsplit('/', 1)[-1]}",
        startFromHead=False, limit=200)["events"]
    for event in reversed(events):
        _, mark, body = event["message"].partition(REJECT_SUMMARY_MARK)
        if mark:
            return json.loads(body)
    return None


def _item_line(item: dict) -> str:
    who = " ".join(str(item[k]) for k in ("corp_code", "corp_name", "bsns_year", "fiscal_year", "reprt_code",
                                          "fs_basis", "metric", "report_nm") if item.get(k) is not None)
    return f"  - {who or '(대상 미상)'}: {','.join(item.get('reasons') or [])} [{item.get('class')}]"


def _failure_report(ti, dag, run) -> tuple[str | None, list[str]]:
    """실패 통보의 (제목 대체, 상세 줄). 제목 대체는 **적재가 끝났고 거부가 전부 미지원 분류일 때만** 준다.

    그 밖의 모든 경우(오류 분류 거부, 스텝 실패, 수집 부분 실패, 보류, 요약을 읽지 못함)는 None — 제목은 FAILED 그대로다.
    run 판정(FAILED)과 종료 코드 정책은 바꾸지 않는다. 통보 문구만 가른다 — 대체 제목에도 FAILED 와 run 식별자가 남고,
    "적재 완료"는 만들어진 지표가 실렸다는 뜻이지 전 지표를 확보했다는 뜻이 아니다(본문에 그렇게 적는다).
    """
    steps = _judged_steps(run)
    codes = {s: ti.xcom_pull(task_ids=s, key="exit_code") for s in ("plan", *steps, "report")}
    holds = _holds(ti)
    lines = ["스텝 종료 코드: " + " ".join(f"{s}={c}" for s, c in codes.items())]
    if holds:
        lines.append(f"실행 보류: {holds}")
    partial = [s for s in steps if codes[s] == 2]
    summaries = {}
    for step in partial:
        summary = summaries[step] = _reject_summary(ti, dag.get_task(step)) if step.endswith("_normalize") else None
        if summary is None:
            lines.append(f"{step}: 부분 실패(exit 2) — 거부 요약을 로그에서 찾지 못했다")
            continue
        classes = " ".join(f"{k} {v}" for k, v in summary["classes"].items())
        lines.append(f"{step}: 거부 {summary['failed']}건({classes}) · 결손 {summary['gaps']}건")
        lines.extend(_item_line(item) for item in summary["items"])
        if summary["failed"] > len(summary["items"]):
            lines.append(f"  … 외 {summary['failed'] - len(summary['items'])}건(품질 로그 failures 참조)")
    # 요약의 분류별 건수가 거부 건수와 맞을 때만 분류를 믿는다(분류 없는 거부가 섞인 요약을 미지원으로 읽지 않는다).
    loaded = (bool(partial) and not holds and all(code == 0 for s, code in codes.items() if s not in partial)
              and all(summaries[s] and set(summaries[s]["classes"]) <= UNSUPPORTED_ONLY
                      and 0 < summaries[s]["failed"] == sum(summaries[s]["classes"].values()) for s in partial))
    if not loaded:
        return None, lines
    count = sum(summaries[s]["failed"] for s in partial)
    return (f"[{LANE}] FAILED · 적재 완료 · 미지원 {count}건 — airflow {run.run_id}",
            [f"판정: DAG run 은 FAILED 다. 스텝은 모두 끝났고 만들어진 지표는 적재됐다. 정제가 읽지 못한 표기·계정 "
             f"{count}건의 지표는 만들지 못했다(오류로 분류된 거부는 없다). 전 지표를 확보했다는 뜻이 아니다 — "
             "거부·결손 건수는 아래에 있다.", *lines])


def _notify_failure(context):
    """런 실패 통보 — 장중 수급 DAG 와 같은 SNS 토픽. 상세를 만들지 못해도 통보는 나간다(종전 문구)."""
    if ALARM_TOPIC is None:
        return
    run = context.get("dag_run")
    run_id = getattr(run, "run_id", None) or context.get("run_id")
    subject = f"[{LANE}] FAILED — airflow {run_id}"
    lines = [f"dag={context['dag'].dag_id} run={run_id} reason={context.get('reason')}"]
    try:
        soft, detail = _failure_report(context["ti"], context["dag"], run)
        subject, lines = soft or subject, [*lines, *detail]
    except Exception as exc:     # XCom·로그 조회 실패, 마지막 태스크 정보가 없는 콜백 — 제목은 FAILED 그대로 둔다
        lines.append(f"상세를 만들지 못했다: {type(exc).__name__}: {exc}"[:300])
    SnsHook().publish_to_target(target_arn=ALARM_TOPIC, subject=subject[:99], message="\n".join(lines))


def build_dag(dag_id: str, *, schedule, step=EdgeStep, ecs_target: dict | None = None,
              notify: bool = True) -> DAG:
    """이 레인의 DAG. 계열 셋은 서로 기다리지 않는다 — 한 공급자 장애가 다른 원천을 막지 않게."""
    ecs_target = {"cluster": CLUSTER, **(ecs_target or {})}
    params = {"reprocess_slot": Param("", type="string", description=(
        "비우면 일반 run. 기존 슬롯 ISO 시각(예 2026-10-04T05:20:00+09:00)이면 그 슬롯의 raw 를 다시 "
        "정제·적재한다(수집 안 함)."))}
    for family, names in BACKFILL_PARAMS.items():
        for name in names:
            params[name] = Param("", type="string", description=(
                f"{family} 백필 {'시작' if name.endswith('from') else '끝'}일 YYYY-MM-DD. 비우면 정기 창 — "
                "from·to 는 함께 준다"))
    with DAG(
        dag_id=dag_id,
        auto_register=False,
        schedule=schedule,
        # 신규 레인은 켜기 전까지 돌지 않는다(환경 기본값과 별개로 DAG 가 스스로 요구한다).
        is_paused_upon_creation=True,
        catchup=False,
        max_active_runs=1,
        # Reconciler 수명(1800초)보다 짧아야 죽은 run 을 놓치지 않는다(장중 수급 DAG 와 같은 규칙).
        dagrun_timeout=timedelta(seconds=int(os.environ.get("EDGE_LAB_DAGRUN_TIMEOUT_SECONDS", "1500"))),
        params=params,
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
        loads = []
        for family, stages in FAMILIES.items():
            _, taskdef, cli = stages["collect"]
            window = []
            if family in BACKFILL_PARAMS:
                start, end = BACKFILL_PARAMS[family]
                window = ["--from", f"{{{{ params.{start} }}}}", "--to", f"{{{{ params.{end} }}}}"]
            collect = step(
                **ecs_target, task_id=f"{family}_collect", taskdef_key=taskdef, same_day_only=True,
                # 성공 이력 skip 을 켜지 않는다: 수집기가 완료 run_id 재실행을 무호출로 처리하고, 다른 요청 범위면
                # 거부한다(ensure_same_request). skip 은 그 범위 검사를 건너뛰어 같은 분의 두 백필을 한 범위로 합친다.
                noop_on_reprocess=True, partial_exit_codes=(2,),
                command=[cli, "--run-id", rid, *window],
            )
            _, taskdef, cli = stages["normalize"]
            normalize = step(
                **ecs_target, task_id=f"{family}_normalize", taskdef_key=taskdef, partial_exit_codes=(2,),
                trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
                command=[cli, "--run-id", rid, "--input-run-id", rid],
            )
            _, taskdef, cli = stages["load"]
            load = step(
                **ecs_target, task_id=f"{family}_load", taskdef_key=taskdef,
                command=[cli, "--run-id", rid, "--input-run-id", rid],
            )
            plan >> collect >> normalize >> load
            plan >> normalize
            loads.append(load)

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
            """실행한 스텝이 모두 exit 0 일 때만 런 성공. 부분 실패(2)는 하류를 돌린 뒤 여기서 실패로 마감한다."""
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

        loads >> report >> verdict()
    return dag


dag = build_dag("edge_source_daily",
                schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)))
