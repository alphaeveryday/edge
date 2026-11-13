"""시장 마감 수집 DAG 계약 테스트 — SFN 경로와 갈라지면 안 되는 것(슬롯·스텝 집합·명령·분기 규칙·통보)을 고정한다.

EdgeStep 의 실행 판정은 장중 수급 테스트가 같은 클래스로 고정한다. 원장 카탈로그(task_key·태스크 정의·CLI) 대조는
업무 패키지가 있는 data-pipeline tests/test_airflow_dag_contract.py 가 한다.
"""

from __future__ import annotations

import importlib
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
TF = REPO / "infra/terraform/modules/data-pipeline"
sys.path.insert(0, str(ROOT / "dags"))
KST = timezone(timedelta(hours=9))

# DAG task → SFN state(statemachine.tf). 롤백하면 SFN 이 이 state 들을 같은 잡 목록으로 돌린다.
STATES = {
    "collect_price": "CollectKisPrice", "collect_investor": "CollectKisInvestor", "collect_krx_etf": "CollectKrxEtf",
    "collect_etf_profile": "CollectKisEtfProfile", "collect_nav": "CollectKisNav",
    "collect_sector_index": "CollectKisSectorIndexDaily", "collect_dart_financial": "CollectDartFinancial",
    "normalize_price": "NormalizePrice", "normalize_investor": "NormalizeInvestor", "normalize_etf": "NormalizeEtf",
    "normalize_etf_profile": "NormalizeEtfProfile", "normalize_etf_nav": "NormalizeEtfNav",
    "normalize_sector_index": "NormalizeSectorIndexDaily",
    "load_instruments": "LoadInstruments", "enrich_corp_code": "EnrichCorpCode",
    "load_etf_nav": "LoadEtfNav", "load_price_daily": "LoadPriceDaily", "load_etf_holdings": "LoadEtfHoldings",
    "load_etf_flow": "LoadEtfFlow", "load_market_series": "LoadMarketSeriesDaily",
    "load_price_triggers": "LoadPriceTriggers",
}


@pytest.fixture(scope="module")
def dag_module():
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        return importlib.import_module("edge_etf_daily")
    finally:
        socket.socket.connect = original


def _block(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def test_slot_matches_terraform_schedule(dag_module):
    # 슬롯이 어긋나면 Airflow 가 돈 슬롯과 Reconciler 가 기대하는 슬롯(OPS_DAILY_SCHED_HHMM — 이 cron 에서 파생)이
    # 달라 매일 PLANNER_MISSING 이 열린다.
    tf = (TF / "variables.tf").read_text()
    block = _block(tf, 'variable "schedule_expression"', "\n}\n")
    minute, hour = re.search(r'default\s+= "cron\((\d+) (\d+) \? \* MON-FRI \*\)"', block).groups()
    main = (REPO / "infra/terraform/envs/dev/main.tf").read_text()
    assert not re.search(r"^\s*schedule_expression\s*=", _block(main, 'module "data_pipeline"', "\n}\n"), re.M)
    assert dag_module.CRONS == (f"{minute} {hour} * * 1-5",)


def test_steps_are_the_market_sfn_steps(dag_module):
    # SFN 시장 레인에 스텝이 더해지거나 빠지면(잡 목록·제외 목록·직렬 state) 이 DAG 도 함께 고쳐야 한다 — 아니면 전환 뒤
    # 그 작업이 조용히 안 돈다(원장 작업이면 매 슬롯 MISSED, 원장 밖이면 아무도 모른다).
    sm = (TF / "statemachine.tf").read_text()
    states = lambda block: re.findall(r'^\s*state\s+= "(\w+)"', block, re.M)
    excluded = set(re.findall(r'"(\w+)"', _block(sm, "market_excluded_states = [", "]")))
    listed = (states(_block(sm, "kr_ingest_jobs = [", "raw_ingest_jobs ="))
              + states(_block(sm, "normalize_jobs = [", "feature_jobs = ["))
              + states(_block(sm, "feature_jobs = [", "market_excluded_states = [")))
    serial = re.findall(r"^\s*(\w+) = merge\(local\.ecs_run_task_base, \{", _block(sm, "sfn_definition = jsonencode", "\n}\n"), re.M)
    assert {s for s in listed if s not in excluded} | set(serial) == set(STATES.values())
    assert set(STATES) == set(dag_module.STEPS) and len(dag_module.STEPS) == 21


def test_commands_match_sfn_definition(dag_module):
    # 롤백하면 SFN 이 같은 잡 목록으로 돈다 — 인자가 갈리면 실행 주체에 따라 같은 슬롯이 다른 일을 한다.
    from sfn_commands import dag_command, sfn_command
    for task_id, state in STATES.items():
        taskdef, args = sfn_command(state)
        op = dag_module.dag.get_task(task_id)
        assert op.task_definition.endswith(f"-{taskdef}"), task_id
        assert dag_command(op) == args, task_id


def test_fmp_collection_is_off_in_dev():
    # us_fmp_enabled 를 켜면 SFN 이 FMP 수집 4개를 더한다 — 이 DAG 엔 없으므로 켜기 전에 DAG 도 고쳐야 한다.
    block = _block((TF / "variables.tf").read_text(), 'variable "us_fmp_enabled"', "\n}\n")
    assert re.search(r"default\s+= false", block)
    assert "us_fmp_enabled" not in (REPO / "infra/terraform/envs/dev/main.tf").read_text()


def test_branch_rules_follow_the_sfn(dag_module):
    dag, m = dag_module.dag, dag_module
    collects = {"normalize_price": "collect_price", "normalize_investor": "collect_investor",
                "normalize_etf": "collect_krx_etf", "normalize_etf_profile": "collect_etf_profile",
                "normalize_etf_nav": "collect_nav", "normalize_sector_index": "collect_sector_index"}
    # 정제는 자기 수집만 기다리고 수집이 실패해도 돈다(ASL NotifyRawPartial → Normalize). plan 실패면 안 돈다.
    # 정제 exit 2 는 성공 winner 를 확정한 부분 실패라 적재로 넘긴다(normalize_*_continue_check).
    for task_id in m.NORMALIZE_STEPS:
        op = dag.get_task(task_id)
        assert op.upstream_task_ids == {"plan", collects[task_id]}, task_id
        assert op.trigger_rule == "all_done_min_one_success"
        assert op.partial_exit_codes == (2,)
    # 정제 6개가 모두 0·2 여야 마스터 적재 — 하나라도 그 밖이면 적재 전부가 upstream_failed(NormalizeCheckResults Default).
    load_instruments = dag.get_task("load_instruments")
    assert load_instruments.upstream_task_ids == set(m.NORMALIZE_STEPS)
    assert load_instruments.trigger_rule == "all_success" and load_instruments.partial_exit_codes == ()
    enrich = dag.get_task("enrich_corp_code")
    assert enrich.upstream_task_ids == {"load_instruments"} and enrich.partial_exit_codes == ()
    # 적재 5개는 corp_code 채움 뒤. holdings 만 exit 0, 나머지 넷은 2 도 통과(FeatureCheckResults).
    for task_id in m.LOAD_STEPS:
        op = dag.get_task(task_id)
        assert op.upstream_task_ids == {"enrich_corp_code"} and op.trigger_rule == "all_success", task_id
        assert op.partial_exit_codes == (() if task_id == "load_etf_holdings" else (2,)), task_id
    # 트리거는 가격·holdings commit 을 모두 본 뒤(ALPHA-1039), 0·2 통과.
    triggers = dag.get_task("load_price_triggers")
    assert triggers.upstream_task_ids == set(m.LOAD_STEPS) and triggers.trigger_rule == "all_success"
    assert triggers.partial_exit_codes == (2,)
    # 수집은 exit 0 만 성공 — 그 밖은 task 실패지만 정제는 돈다.
    for task_id in m.COLLECT_STEPS:
        assert dag.get_task(task_id).upstream_task_ids == {"plan"} and dag.get_task(task_id).partial_exit_codes == ()
    # 보고는 모든 스텝을 직접 기다린다 — 한 스텝 실패로 하류가 upstream_failed 가 된 순간 형제가 아직 돌 수 있다.
    report = dag.get_task("report")
    assert report.upstream_task_ids == set(m.STEPS) and report.trigger_rule == "all_done"
    assert [t.task_id for t in dag.leaves] == ["verdict"]


def test_longest_collections_take_slots_first(dag_module):
    # PARALLELISM 이 수집 수보다 작으면 형제 수집은 가중치 순으로 슬롯을 잡는다. 동률이면 순서가 사실상 무작위라 가격
    # 수집(최대 807초)이 늦게 시작해 run 꼬리가 길어진다.
    dag = dag_module.dag

    def scheduler_weight(task_id):          # 기본 weight_rule(downstream): 자기 + 모든 하류의 priority_weight
        task = dag.get_task(task_id)
        assert task.weight_rule == "downstream"
        return task.priority_weight + sum(dag.get_task(t).priority_weight
                                          for t in task.get_flat_relative_ids(upstream=False))

    others = [t for t in dag_module.COLLECT_STEPS if t not in ("collect_price", "collect_investor")]
    assert min(scheduler_weight("collect_price"), scheduler_weight("collect_investor")) \
        > max(scheduler_weight(t) for t in others)


def test_scheduling_guards(dag_module):
    dag, m = dag_module.dag, dag_module
    # 전환 전엔 SFN 이 같은 슬롯을 계획한다 — 배포만으로 켜지면 두 주체가 한 슬롯을 돌린다.
    assert dag.is_paused_upon_creation is True and dag.catchup is False and dag.max_active_runs == 1
    assert dag.dagrun_timeout == timedelta(seconds=2700)
    for task_id in m.COLLECT_STEPS:
        op = dag.get_task(task_id)
        # 수집 창은 실행 시각(오늘)이 정한다 — 과거 슬롯 run 이 오늘 값을 옛 run_id 로 라벨하면 안 된다.
        assert op.same_day_only is True and op.noop_on_reprocess is True, task_id
    # 성공 이력 skip 은 원장 작업인 수집만 — 원장 밖 재무 수집엔 시도 기록이 없고, 하류에 켜면 앞 스텝 clear 로 입력이
    # 바뀌어도 이전 성공 태스크에 재접속해 새 입력을 처리하지 않는다.
    assert {t for t in m.STEPS if dag.get_task(t).skip_if_succeeded} == set(m.COLLECT_STEPS) - {"collect_dart_financial"}
    # 실행권은 작업 키 단위다 — 원장 밖 재무 수집이 잡을 작업이 없다. 원장 작업은 모두 잡는다.
    assert {t for t in m.STEPS if not dag.get_task(t).exclusive} == {"collect_dart_financial"}
    env = {e["name"]: e["value"] for e in dag.get_task("plan").overrides["containerOverrides"][0]["environment"]}
    assert env["OPS_PIPELINE_TYPE"] == "etf-daily" and env["OPS_ORCHESTRATOR"] == "AIRFLOW"


def test_ledger_task_keys_are_every_step_but_the_unregistered_collection(dag_module):
    # report 는 TASK_KEYS 로 보류를 원장에 옮긴다 — 키가 없는 스텝이 섞이면 KeyError 로 report 가 렌더링부터 죽는다.
    assert set(dag_module.TASK_KEYS) == set(dag_module.STEPS) - {"collect_dart_financial"}
    hold = {"kind": "ECS_STATE_UNKNOWN", "reason": "x"}
    ti = SimpleNamespace(xcom_pull=lambda task_ids, key: hold if key == "hold" else None)
    import json
    assert set(json.loads(dag_module._holds_env(ti))) == set(dag_module.TASK_KEYS.values())


def test_partial_runs_close_failed_and_reprocess_judges_no_collection(dag_module):
    # ASL RawPartialCheck — 21개(원장 밖 재무 수집 포함) 중 하나라도 exit 0 이 아니면 런은 적재까지 돈 뒤 FAILED 다.
    from edge_batch import run_status
    m = dag_module
    ok = dict.fromkeys(m.STEPS, 0)
    assert run_status(ok) == "SUCCEEDED"
    for step, code in (("collect_dart_financial", 1), ("normalize_etf", 2), ("load_price_triggers", 2)):
        assert run_status({**ok, step: code}) == "FAILED", step
    # 재처리 run 은 수집을 하지 않는다(no-op, exit_code 없음) — 판정에 넣으면 재처리가 늘 실패로 보고된다.
    reprocess = SimpleNamespace(conf={"reprocess_slot": "2026-11-12T15:40:00+09:00"})
    assert set(m._judged_steps(reprocess)) == set(m.STEPS) - set(m.COLLECT_STEPS)
    assert m._judged_steps(SimpleNamespace(conf={})) == m.STEPS


def test_failure_notice_carries_the_pipeline_run_id(dag_module, monkeypatch):
    # 실패 런의 raw 는 사람이 `--input-run-id <pipeline_run_id>` 로 다시 정제한다(SFN NotifyFailure 가 제목에 run_id 를
    # 넣은 이유) — 통보에 그 값이 없으면 메일을 받아도 회수 절차가 시작되지 않는다.
    sent = []
    monkeypatch.setattr(dag_module, "ALARM_TOPIC", "arn:aws:sns:ap-northeast-2:0:t")
    monkeypatch.setattr(dag_module, "SnsHook", lambda: SimpleNamespace(publish_to_target=lambda **kw: sent.append(kw)))
    slot = datetime(2026, 11, 13, 15, 40, tzinfo=KST)
    run = SimpleNamespace(conf={}, run_after=slot)
    dag = SimpleNamespace(dag_id="edge_etf_daily")
    dag_module._notify_failure({"dag": dag, "run_id": "scheduled__x", "dag_run": run, "logical_date": slot,
                                "reason": "timed_out"})
    from edge_batch import pipeline_run_id
    assert f"pipeline_run_id={pipeline_run_id('etf-daily', slot)}" in sent[-1]["message"]
    # 콜백 문맥이 최소(dag·run_id·reason)여도 통보는 나간다 — run id 만 미상으로 적는다.
    dag_module._notify_failure({"dag": dag, "run_id": "scheduled__x", "reason": "timed_out"})
    assert "pipeline_run_id=미상(KeyError)" in sent[-1]["message"] and len(sent) == 2


@pytest.mark.parametrize("now_kst, expected_kst", [
    ("2026-11-11T16:30:00", "2026-11-12T15:40:00"),   # 전환 절차: 수요일 SFN run 뒤 unpause — 15:40 재실행 없음
    ("2026-11-13T17:00:00", "2026-11-16T15:40:00"),   # 금요일 마감 뒤 — 다음 월요일
])
def test_activation_does_not_run_an_already_passed_slot(dag_module, monkeypatch, now_kst, expected_kst):
    # 실제 Airflow timetable 로 "처음 켰을 때 다음 run" 을 계산한다(catchup=False, 이전 run 없음). 지난 슬롯을 돌리면
    # SFN 이 이미 돈 슬롯을 다시 계획한다(LAUNCH_CONFLICT).
    import pendulum
    import airflow.timetables.trigger as trigger
    from airflow.timetables.base import TimeRestriction
    now = datetime.fromisoformat(now_kst).replace(tzinfo=KST).astimezone(timezone.utc)
    monkeypatch.setattr(trigger, "utcnow", lambda: pendulum.instance(now))
    monkeypatch.setattr(trigger.time, "time", lambda: now.timestamp())
    info = dag_module.dag.timetable.next_dagrun_info(
        last_automated_data_interval=None,
        restriction=TimeRestriction(earliest=None, latest=None, catchup=False))
    assert info.logical_date.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S") == expected_kst
