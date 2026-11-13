"""시장 마감 수집 레인(etf-daily) — SFN `edge-*-data-pipeline` 의 Airflow 실행 경로.

현행과 같은 것: 슬롯(평일 KST 15:40), Planner 원장 계획, 21개 ECS 스텝과 명령(statemachine.tf 잡 목록과 직렬
state — tests 가 인자까지 대조), 분기 규칙(ASL):
- 수집이 실패해도 정제는 돈다(NotifyRawPartial → NormalizeParallel).
- 정제 6개가 모두 exit 0 또는 2 여야 마스터 적재(LoadInstruments)로 간다. 하나라도 그 밖이면 적재 전부를 돌리지
  않는다(NormalizeCheckResults Default). LoadInstruments·EnrichCorpCode 는 exit 0 만 통과한다.
- 적재 5개 중 LoadEtfHoldings 만 exit 0, 나머지 4개는 0 또는 2 면 가격변동 트리거로 간다(FeatureCheckResults).
  트리거는 0·2 통과다.
- 끝에서 21개 중 하나라도 exit 0 이 아니면 런을 FAILED 로 마감한다(RawPartialCheck).
- CollectDartFinancial 은 원장 카탈로그에 없다(하류 소비자 0 — catalog.py 제외 표). 같은 명령으로 돌리되 실행권·
  성공 skip 을 쓰지 않고(지킬 기대 작업이 없다) 보류도 원장에 옮기지 않는다. 런 판정에는 SFN 처럼 넣는다.
  ⚠️ report 가 판정을 남기지 못하면 Reconciler 는 원장 작업 20개로만 투영해, 이 스텝만 실패한 run 이 원장에
  SUCCEEDED 로 남을 수 있다(run 실패 SNS·Airflow 화면에는 드러난다 — README "시장 마감 수집 레인").
FMP 4스텝은 `us_fmp_enabled` 일 때만 SFN 에 들어간다 — dev 는 꺼져 있어 이 DAG 엔 없다(tests 가 켜짐을 감지하면
실패한다).
달라진 것(의도):
- 실행 요청 = 이 DAG run. plan 은 원장만 쓰고 SFN 을 시작하지 않는다(OPS_ORCHESTRATOR=AIRFLOW).
- 정제는 수집 7개 전체가 아니라 **자기 데이터셋 수집만** 기다린다. 정제는 `--input-run-id` 로 그 데이터셋 raw 만
  읽으므로 결과가 같고, 짧은 수집의 정제가 긴 가격 수집과 겹쳐 돈다. 정제→적재 관문(LoadInstruments)은 같다.
  예외: normalize_etf 는 가격·투자자 수집도 기다린다. 두 수집이 시작할 때 canonical KR holdings 최신 스냅샷으로
  수집 종목을 정하는데(ingest_price_raw·ingest_raw_investor `universe_from_holdings`), normalize_etf 가 그 스냅샷을
  쓴다 — 먼저 끝나면 수집 종목이 실행 순서에 따라 SFN 과 달라진다.
- 수집 하나가 보류되면(ECS 결과 미상) 그 정제가 시작하지 않고 skip 되고, skip 이 load_instruments 로 번져 그날
  적재가 모두 멈춘다(EdgeStep stop_on_upstream_hold). SFN 엔 보류가 없었다.
- 업무 미시작이 확인된 경우와 도는 태스크 재접속만 재시도한다(EdgeStep). SFN 스케줄은 재시도 0 이었다.
- 수집은 성공 이력이 있으면 다시 부르지 않는다(외부 호출 스텝 — 다른 레인과 같은 규칙).
- 가격·투자자 수집이 먼저 슬롯을 잡는다(priority_weight). 가장 긴 두 수집이 늦게 시작하면 run 꼬리가 길어진다.
- 시간 상한: SFN 21600초 → dagrun_timeout 2700초(Reconciler 수명 3000초보다 짧아야 한다). SFN 실측(원장 25회)
  시작~끝 중앙 824초·최대 1255초, 가장 긴 스텝은 가격 수집 최대 807초.
- 통보: SFN 의 부분 실패 즉시 통보 3종·NotifyFailure·시간 초과 알람 대신 run 실패 때 SNS 한 통(dagrun_timeout
  포함). 본문에 pipeline_run_id 를 싣는다 — 실패 런의 raw 는 사람이 `--input-run-id <그 값>` 으로 다시 정제한다
  (statemachine.tf NormalizeParallel 앞 주석).

일정·날짜:
- 슬롯은 variables.tf `schedule_expression` 과 같다(dev 는 기본값 — tests 대조).
- 수집은 당일 슬롯만 — KRX·KIS 는 실행 시각의 값을 주므로 과거 슬롯 run 이 오늘 값을 옛 run_id 로 라벨하면 안 된다.
- 평일 공휴일에도 run 이 생긴다 — 현행 SFN 과 같다(Planner 가 기대 작업을 SKIPPED 로 계획한다).
- catchup 없음, max_active_runs=1: 두 실행이 같은 canonical 파티션·마스터를 동시에 만지지 않게.
- 켜기 전까지 돌지 않는다(is_paused_upon_creation). 전환은 terraform `daily_orchestrator` 와 unpause 를 함께
  한다(README "시장 마감 수집 레인").
"""

from __future__ import annotations

import json
import os
from datetime import timedelta

from airflow.providers.amazon.aws.hooks.sns import SnsHook
from airflow.sdk import DAG, Param, TriggerRule, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import MultipleCronTriggerTimetable

from edge_batch import (CLUSTER, HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN, EdgeStep, lane_arg,
                        pipeline_run_id, reprocess_slot, run_key, run_status, settlement, slot_time)

LANE = "etf-daily"
# variables.tf `schedule_expression` 과 같은 슬롯(드리프트는 tests 가 대조).
CRONS = ("40 15 * * 1-5",)
COLLECT_STEPS = ("collect_price", "collect_investor", "collect_krx_etf", "collect_etf_profile", "collect_nav",
                 "collect_sector_index", "collect_dart_financial")
NORMALIZE_STEPS = ("normalize_price", "normalize_investor", "normalize_etf", "normalize_etf_profile",
                   "normalize_etf_nav", "normalize_sector_index")
LOAD_STEPS = ("load_etf_nav", "load_price_daily", "load_etf_holdings", "load_etf_flow", "load_market_series")
STEPS = (*COLLECT_STEPS, *NORMALIZE_STEPS, "load_instruments", "enrich_corp_code", *LOAD_STEPS,
         "load_price_triggers")
# 원장 카탈로그 task_key(data_pipeline.ops.catalog — tests/test_airflow_dag_contract.py 가 대조). collect_dart_financial
# 은 카탈로그에 없다(위 도크스트링).
TASK_KEYS = {"collect_price": "PRICE_COLLECTION_KIS", "collect_investor": "INVESTOR_COLLECTION_KIS",
             "collect_krx_etf": "ETF_HOLDINGS_COLLECTION_KRX", "collect_etf_profile": "ETF_PROFILE_COLLECTION_KIS",
             "collect_nav": "NAV_COLLECTION_KIS", "collect_sector_index": "SECTOR_INDEX_DAILY_COLLECTION_KIS",
             "normalize_price": "NORMALIZE_PRICE", "normalize_investor": "NORMALIZE_INVESTOR",
             "normalize_etf": "NORMALIZE_ETF", "normalize_etf_profile": "NORMALIZE_ETF_PROFILE",
             "normalize_etf_nav": "NORMALIZE_ETF_NAV", "normalize_sector_index": "NORMALIZE_SECTOR_INDEX_DAILY",
             "load_instruments": "LOAD_INSTRUMENTS", "enrich_corp_code": "ENRICH_CORP_CODE",
             "load_etf_nav": "LOAD_ETF_NAV", "load_price_daily": "LOAD_PRICE_DAILY",
             "load_etf_holdings": "LOAD_ETF_HOLDINGS", "load_etf_flow": "LOAD_ETF_FLOW",
             "load_market_series": "LOAD_MARKET_SERIES_DAILY", "load_price_triggers": "LOAD_PRICE_TRIGGERS"}


def _judged_steps(dag_run) -> tuple[str, ...]:
    """이 run 이 판정할 스텝 — 재처리 run 은 수집을 하지 않는다."""
    if reprocess_slot({"dag_run": dag_run}):
        return tuple(s for s in STEPS if s not in COLLECT_STEPS)
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
    """report 가 원장에 옮길 보류 — 원장 작업(TASK_KEYS)의 ECS 상태·결과를 모르는 것만. 없으면 빈 값."""
    holds = {TASK_KEYS[s]: h for s, h in _holds(ti, tuple(TASK_KEYS)).items()
             if h.get("kind") in (HOLD_ECS_STATE_UNKNOWN, HOLD_RESULT_UNKNOWN)}
    return json.dumps(holds, ensure_ascii=False) if holds else ""


ALARM_TOPIC = os.environ.get("EDGE_ALARM_TOPIC_ARN") or None


def _failed_pipeline_run_id(context) -> str:
    """통보 본문의 pipeline_run_id. 콜백 문맥에 슬롯 재료(logical_date·dag_run)가 없거나 conf 가 잘못됐으면 미상으로
    적는다 — 그 때문에 통보 자체를 잃지 않게."""
    try:
        return pipeline_run_id(LANE, slot_time(context))
    except Exception as exc:
        return f"미상({type(exc).__name__})"


def _notify_failure(context):
    """런 실패 통보 — ASL NotifyFailure 와 같은 SNS 토픽. dagrun_timeout 으로 죽은 경우도 탄다."""
    if ALARM_TOPIC is None:
        return
    run_id = context.get("run_id")
    SnsHook().publish_to_target(
        target_arn=ALARM_TOPIC,
        subject=f"[{LANE}] FAILED — airflow {run_id}",
        message=(f"dag={context['dag'].dag_id} run={run_id} pipeline_run_id={_failed_pipeline_run_id(context)} "
                 f"reason={context.get('reason')}"),
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
        # Reconciler 수명(3000초)보다 짧아야 죽은 run 을 놓치지 않는다(뉴스 DAG 와 같은 값).
        dagrun_timeout=timedelta(seconds=int(os.environ.get("EDGE_LAB_DAGRUN_TIMEOUT_SECONDS", "2700"))),
        params={"reprocess_slot": Param("", type="string", description=(
            "비우면 일반 run. 기존 슬롯 ISO 시각(예 2026-11-13T15:40:00+09:00)을 주면 그 슬롯의 raw 를 "
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

        # 수집 — 외부 호출(KIS·KRX·DART)이라 당일 슬롯만, 재처리엔 no-op, 성공 이력이 있으면 다시 부르지 않는다.
        collect = {"same_day_only": True, "noop_on_reprocess": True, "skip_if_succeeded": True}
        # 가장 긴 두 수집(가격 최대 807초·투자자 427초)이 먼저 슬롯을 잡는다. 나머지 수집은 하류 수가 같아 동률이다.
        collect_price = step(
            **ecs_target, **collect, task_id="collect_price", taskdef_key="kis", priority_weight=3,
            command=["ingest-price-raw", "--source", "kis", "--max-failed-symbols", "1", "--run-id", rid],
        )
        collect_investor = step(
            **ecs_target, **collect, task_id="collect_investor", taskdef_key="kis", priority_weight=3,
            command=["ingest-raw-investor", "--max-failed-symbols", "1", "--run-id", rid],
        )
        collect_krx_etf = step(
            **ecs_target, **collect, task_id="collect_krx_etf", taskdef_key="krx",
            command=["ingest-raw-etf", "--source", "krx", "--run-id", rid,
                     "--deadline-sec", "{{ edge_lane_arg('krx_etf_deadline_sec') }}"],
        )
        collect_etf_profile = step(
            **ecs_target, **collect, task_id="collect_etf_profile", taskdef_key="kis",
            command=["ingest-raw-etf-profile", "--run-id", rid],
        )
        collect_nav = step(
            **ecs_target, **collect, task_id="collect_nav", taskdef_key="kis",
            command=["ingest-raw-nav", "--run-id", rid],
        )
        collect_sector_index = step(
            **ecs_target, **collect, task_id="collect_sector_index", taskdef_key="kis",
            command=["ingest-raw-sector-index-daily", "--run-id", rid],
        )
        # 원장 밖 — 실행권은 작업 키 단위라 지킬 것이 없고, 성공 skip 은 원장 시도 기록이 없어 쓸 수 없다.
        collect_dart_financial = step(
            **ecs_target, task_id="collect_dart_financial", taskdef_key="dart", same_day_only=True,
            noop_on_reprocess=True, exclusive=False,
            command=["ingest-raw-financial", "--source", "dart", "--run-id", rid],
        )

        # 정제 — 자기 데이터셋 수집만 기다린다. 수집이 실패해도 돈다(ASL NotifyRawPartial → Normalize). plan 실패면
        # 돌지 않는다. exit 2 는 성공 winner·last-good pointer 를 확정한 부분 실패라 적재로 넘긴다.
        normalize = {"partial_exit_codes": (2,), "trigger_rule": TriggerRule.ALL_DONE_MIN_ONE_SUCCESS}
        normalize_price = step(
            **ecs_target, **normalize, task_id="normalize_price", taskdef_key="bigkinds",
            command=["normalize-price", "--run-id", rid, "--input-run-id", rid],
        )
        normalize_investor = step(
            **ecs_target, **normalize, task_id="normalize_investor", taskdef_key="bigkinds",
            command=["normalize-investor", "--run-id", rid, "--input-run-id", rid],
        )
        normalize_etf = step(
            **ecs_target, **normalize, task_id="normalize_etf", taskdef_key="bigkinds",
            command=["normalize-etf", "--run-id", rid, "--input-run-id", rid],
        )
        normalize_etf_profile = step(
            **ecs_target, **normalize, task_id="normalize_etf_profile", taskdef_key="bigkinds",
            command=["normalize-etf-profile", "--run-id", rid, "--input-run-id", rid],
        )
        normalize_etf_nav = step(
            **ecs_target, **normalize, task_id="normalize_etf_nav", taskdef_key="bigkinds",
            command=["normalize-etf-nav", "--run-id", rid, "--input-run-id", rid],
        )
        normalize_sector_index = step(
            **ecs_target, **normalize, task_id="normalize_sector_index", taskdef_key="bigkinds",
            command=["normalize-sector-index-daily", "--run-id", rid, "--input-run-id", rid],
        )

        # 마스터 적재 → corp_code 채움(직렬, exit 0 만). 정제 6개가 모두 성공(exit 2 포함)이어야 시작한다(all_success).
        load_instruments = step(
            **ecs_target, task_id="load_instruments", taskdef_key="rds",
            command=["load-instruments", "--run-id", rid, "--latest-good"],
        )
        enrich_corp_code = step(
            **ecs_target, task_id="enrich_corp_code", taskdef_key="rds_dart",
            command=["enrich-corp-code", "--run-id", rid],
        )

        # 적재 — holdings 만 exit 0, 나머지는 성공 winner 를 보존한 exit 2 도 통과(ASL FeatureCheckResults).
        load_etf_nav = step(
            **ecs_target, task_id="load_etf_nav", taskdef_key="rds", partial_exit_codes=(2,),
            command=["load-etf-nav", "--run-id", rid, "--input-run-id", rid],
        )
        load_price_daily = step(
            **ecs_target, task_id="load_price_daily", taskdef_key="rds", partial_exit_codes=(2,),
            command=["load-price-daily", "--run-id", rid, "--input-run-id", rid],
        )
        load_etf_holdings = step(
            **ecs_target, task_id="load_etf_holdings", taskdef_key="rds",
            command=["load-etf-holdings", "--run-id", rid, "--input-run-id", rid],
        )
        load_etf_flow = step(
            **ecs_target, task_id="load_etf_flow", taskdef_key="rds", partial_exit_codes=(2,),
            command=["load-etf-flow", "--run-id", rid, "--input-run-id", rid],
        )
        load_market_series = step(
            **ecs_target, task_id="load_market_series", taskdef_key="rds", partial_exit_codes=(2,),
            command=["load-market-series-daily", "--run-id", rid, "--input-run-id", rid],
        )
        # 가격·holdings DB commit 을 모두 본 뒤에만 돈다(ALPHA-1039 — 먼저 뜨면 stale DB 를 읽는다).
        load_price_triggers = step(
            **ecs_target, task_id="load_price_triggers", taskdef_key="rds", partial_exit_codes=(2,),
            command=["load-price-triggers", "--run-id", rid, "--input-run-id", rid],
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
            """ASL RawPartialCheck — 실행한 스텝이 모두 exit 0 일 때만 런 성공."""
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

        pairs = ((collect_price, normalize_price), (collect_investor, normalize_investor),
                 (collect_krx_etf, normalize_etf), (collect_etf_profile, normalize_etf_profile),
                 (collect_nav, normalize_etf_nav), (collect_sector_index, normalize_sector_index))
        for collect_step, normalize_step in pairs:
            plan >> collect_step >> normalize_step >> load_instruments
            plan >> normalize_step
        # 가격·투자자 수집은 canonical holdings 로 수집 종목을 정한다 — 그 스냅샷을 쓰는 normalize_etf 가 먼저 끝나면
        # 실행 순서에 따라 종목이 바뀐다. SFN 은 수집 전체가 끝난 뒤 정제해 늘 앞 스냅샷을 읽었다.
        [collect_price, collect_investor] >> normalize_etf
        plan >> collect_dart_financial
        loads = [load_etf_nav, load_price_daily, load_etf_holdings, load_etf_flow, load_market_series]
        load_instruments >> enrich_corp_code >> loads >> load_price_triggers
        # 한 스텝이 실패하면 그 하류는 다른 형제를 기다리지 않고 upstream_failed 가 된다(all_success). report 가 모든
        # 스텝을 직접 기다려야 아직 안 끝난 스텝을 보류로 옮기거나 판정에서 빠뜨리지 않는다(뉴스·공시 DAG 와 같은 이유).
        [dag.get_task(s) for s in STEPS] >> report >> verdict()
    return dag


dag = build_dag("edge_etf_daily",
                schedule=MultipleCronTriggerTimetable(*CRONS, timezone="Asia/Seoul", run_immediately=timedelta(0)))
