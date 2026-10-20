"""공시 보충 배치 DAG 계약 테스트(ALPHA-1407) — SFN 경로와 갈라지면 안 되는 것(슬롯·명령·분기 규칙)을 고정한다.

EdgeStep 의 실행 판정(exit code 해석·당일 수집·재처리)은 장중 수급 테스트가 같은 클래스로 고정한다.
"""

from __future__ import annotations

import importlib
import re
import socket
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dags"))


@pytest.fixture(scope="module")
def dag_module():
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        return importlib.import_module("edge_disclosure")
    finally:
        socket.socket.connect = original


def test_slots_match_dev_schedule(dag_module):
    # 모듈 기본(평일 10슬롯)이 아니라 dev 가 실제로 켠 슬롯이다 — 원장 슬롯 대조(OPS_DISCLOSURE_SCHED_HHMM)도 이 값에서
    # 파생되므로, 어긋나면 Airflow 가 돈 슬롯과 Reconciler 가 기대하는 슬롯이 달라 매일 PLANNER_MISSING 이 열린다.
    main = (ROOT.parents[2] / "infra/terraform/envs/dev/main.tf").read_text()
    block = main[main.index("disclosure_schedule_expressions = {"):]
    block = block[:block.index("}")]
    crons = re.findall(r'cron\((\d+) (\d+) \? \* MON-FRI \*\)', block)
    assert crons and sorted(f"{m} {h} * * 1-5" for m, h in crons) == sorted(dag_module.CRONS)


def test_commands_match_sfn_definition(dag_module):
    # 롤백하면 SFN 이 같은 잡 목록으로 돈다 — 인자가 갈리면 실행 주체에 따라 같은 슬롯이 다른 일을 한다.
    from sfn_commands import dag_command, sfn_command
    for task_id, state in (("collect", "CollectDartDisclosure"), ("normalize", "NormalizeDisclosure"),
                           ("normalize_segment", "NormalizeDisclosureSegment"), ("load", "LoadDisclosure")):
        taskdef, args = sfn_command(state)
        op = dag_module.dag.get_task(task_id)
        assert op.task_definition.endswith(f"-{taskdef}"), task_id
        assert dag_command(op) == args, task_id


def test_branch_rules_follow_the_sfn(dag_module):
    dag = dag_module.dag
    # raw 실패여도 정제는 돈다(ASL DisclosureNotifyRawPartial → Normalize). plan 실패면 아무것도 안 돈다.
    for task_id in ("normalize", "normalize_segment"):
        op = dag.get_task(task_id)
        assert op.upstream_task_ids == {"plan", "collect"}
        assert op.trigger_rule == "all_done_min_one_success"
        assert op.partial_exit_codes == (2,)          # 성공 winner commit 뒤 부분 실패 — 적재로 넘긴다
    # 정제 둘 다 exit 0·2 일 때만 적재(그 밖의 정제 실패면 upstream_failed). 적재는 exit 0 만 성공이다.
    load = dag.get_task("load")
    assert load.upstream_task_ids == {"normalize", "normalize_segment"}
    assert load.trigger_rule == "all_success"
    assert load.partial_exit_codes == ()
    # 판정은 leaf(verdict)가 정하고, 보고는 어떤 결말이든 돈다.
    assert [t.task_id for t in dag.leaves] == ["verdict"]
    assert dag.get_task("report").trigger_rule == "all_done"
    assert dag.get_task("report").upstream_task_ids == {"load"}


def test_partial_runs_close_failed(dag_module):
    # ASL DisclosureRawPartialCheck — raw·정제 부분 실패 런은 적재까지 돈 뒤 FAILED 다.
    from edge_batch import run_status
    assert run_status(dict.fromkeys(dag_module.STEPS, 0)) == "SUCCEEDED"
    assert run_status({**dict.fromkeys(dag_module.STEPS, 0), "normalize_segment": 2}) == "FAILED"
    assert run_status({**dict.fromkeys(dag_module.STEPS, 0), "collect": 1}) == "FAILED"


def test_scheduling_guards(dag_module):
    dag = dag_module.dag
    # 전환 전엔 SFN 이 같은 슬롯을 계획한다 — 배포만으로 켜지면 두 주체가 한 슬롯을 돌린다.
    assert dag.is_paused_upon_creation is True
    assert dag.catchup is False and dag.max_active_runs == 1
    assert dag.dagrun_timeout == timedelta(seconds=1500)
    collect = dag.get_task("collect")
    # 수집 창은 실행 시각(오늘)이 정한다 — 과거 슬롯 run 이 오늘 공시를 옛 run_id 로 라벨하면 안 된다.
    assert collect.same_day_only is True and collect.noop_on_reprocess is True
    plan = dag.get_task("plan")
    env = {e["name"]: e["value"] for e in plan.overrides["containerOverrides"][0]["environment"]}
    assert env["OPS_PIPELINE_TYPE"] == "disclosure" and env["OPS_ORCHESTRATOR"] == "AIRFLOW"
