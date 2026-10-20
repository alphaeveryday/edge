"""뉴스 DAG 계약 테스트(ALPHA-1409) — SFN 경로와 갈라지면 안 되는 것(슬롯·명령·분기 규칙)을 고정한다.

EdgeStep 의 실행 판정은 장중 수급 테스트가 같은 클래스로 고정한다.
"""

from __future__ import annotations

import importlib
import re
import socket
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT / "dags"))


@pytest.fixture(scope="module")
def dag_module():
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        return importlib.import_module("edge_news")
    finally:
        socket.socket.connect = original


def test_slots_match_terraform_schedule(dag_module):
    # 슬롯이 어긋나면 Airflow 가 돈 슬롯과 Reconciler 가 기대하는 슬롯(OPS_NEWS_SCHED_HHMM)이 달라 PLANNER_MISSING 이 연다.
    tf = (REPO / "infra/terraform/modules/data-pipeline/variables.tf").read_text()
    block = tf[tf.index('variable "news_schedule_expressions"'):]
    block = block[:block.index("\n}\n")]
    crons = re.findall(r'cron\((\d+) (\d+) \* \* \? \*\)', block)
    assert "news_schedule_expressions" not in (REPO / "infra/terraform/envs/dev/main.tf").read_text()
    assert crons and sorted(f"{m} {h} * * *" for m, h in crons) == sorted(dag_module.CRONS)


def test_commands_match_sfn_definition(dag_module):
    from sfn_commands import dag_command, sfn_command
    for task_id, state in (("collect", "CollectBigKindsNews"), ("normalize", "NormalizeNews"), ("tag", "TagNews"),
                           ("load_documents", "LoadDocuments"), ("load_assertions", "NewsLoadAssertions"),
                           ("assemble", "NewsAssembleEvents")):
        taskdef, args = sfn_command(state)
        op = dag_module.dag.get_task(task_id)
        assert op.task_definition.endswith(f"-{taskdef}"), task_id
        assert dag_command(op) == args, task_id


def test_fmp_collection_is_off_in_dev():
    # us_fmp_enabled 를 켜면 SFN 이 FMP 뉴스 수집을 더한다 — 이 DAG 엔 없으므로 켜기 전에 DAG 도 고쳐야 한다.
    tf = (REPO / "infra/terraform/modules/data-pipeline/variables.tf").read_text()
    block = tf[tf.index('variable "us_fmp_enabled"'):]
    assert re.search(r"default\s+= false", block[:block.index("\n}\n")])
    assert "us_fmp_enabled" not in (REPO / "infra/terraform/envs/dev/main.tf").read_text()


def test_branch_rules_follow_the_sfn(dag_module):
    dag = dag_module.dag
    normalize = dag.get_task("normalize")
    assert normalize.upstream_task_ids == {"plan", "collect"}
    assert normalize.trigger_rule == "all_done_min_one_success"     # 수집 실패여도 정제
    # 정제·태깅·적재·조립은 exit 0 만 통과 — 부분 실패 코드를 두지 않는다(ASL CheckResults Default → 실패).
    for task_id in dag_module.STEPS:
        assert dag.get_task(task_id).partial_exit_codes == (), task_id
    for task_id in dag_module.FEATURE_STEPS:
        assert dag.get_task(task_id).upstream_task_ids == {"normalize"}
        assert dag.get_task(task_id).trigger_rule == "all_success"
    assert dag.get_task("load_assertions").upstream_task_ids == set(dag_module.FEATURE_STEPS)
    assert dag.get_task("assemble").upstream_task_ids == {"load_assertions"}
    # 태깅·문서 적재 하나가 실패하면 assertion 적재는 다른 하나를 기다리지 않고 끝난다 — 보고는 모두를 기다린다.
    report = dag.get_task("report")
    assert report.upstream_task_ids == {"tag", "load_documents", "load_assertions", "assemble"}
    assert report.trigger_rule == "all_done"
    assert [t.task_id for t in dag.leaves] == ["verdict"]


def test_scheduling_guards(dag_module):
    dag = dag_module.dag
    assert dag.is_paused_upon_creation is True and dag.catchup is False and dag.max_active_runs == 1
    assert dag.dagrun_timeout == timedelta(seconds=1700)
    collect = dag.get_task("collect")
    assert collect.same_day_only is True and collect.noop_on_reprocess is True   # 수집 창 = 실행 시각 기준
    # 외부 호출 스텝만 성공 이력 skip — 태깅은 기사당 LLM 과금이다.
    assert {t for t in dag_module.STEPS if dag.get_task(t).skip_if_succeeded} == {"collect", "tag"}
    env = {e["name"]: e["value"] for e in dag.get_task("plan").overrides["containerOverrides"][0]["environment"]}
    assert env["OPS_PIPELINE_TYPE"] == "news" and env["OPS_ORCHESTRATOR"] == "AIRFLOW"


def test_reprocess_of_a_past_day_is_refused_at_plan(dag_module, monkeypatch):
    # 조립 창은 실행 시각 기준(오늘−N일)이다 — 지난 날 슬롯을 재처리하면 그 슬롯 기사를 안 읽고 exit 0 으로 성공 보고한다.
    # plan(OPS_SCHEDULED_TIME 렌더링)에서 막아야 report 가 그 슬롯의 기존 판정을 덮지 않는다(plan 실패 → 판정 빈 값).
    from airflow.sdk.exceptions import AirflowFailException
    import edge_batch
    monkeypatch.setattr(dag_module, "today_kst", lambda: datetime(2026, 10, 20).date())
    slot = dag_module._planned_slot
    run = lambda iso: SimpleNamespace(conf={"reprocess_slot": iso}, run_after=None)
    with pytest.raises(AirflowFailException, match="오늘 슬롯만"):
        slot(None, run("2026-10-19T08:10:00+09:00"))
    assert slot(None, run("2026-10-20T00:10:00+09:00")).isoformat() == "2026-10-20T00:10:00+09:00"
    scheduled = datetime(2026, 10, 18, 8, 10, tzinfo=edge_batch.KST)          # 일정 run 은 plan 의 당일 검사가 맡는다
    assert slot(scheduled, SimpleNamespace(conf={}, run_after=scheduled)) == scheduled
    env = {e["name"]: e["value"] for e in dag_module.dag.get_task("plan").overrides["containerOverrides"][0]["environment"]}
    assert "edge_slot(logical_date, dag_run)" in env["OPS_SCHEDULED_TIME"]
