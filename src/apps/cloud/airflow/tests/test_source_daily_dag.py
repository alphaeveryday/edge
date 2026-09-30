"""원천 관측 DAG 계약 (ALPHA-1130) — Airflow 3.3.2 이미지 안에서 돈다(README 실행법).

업무 계약(카탈로그 task_key·CLI·태스크 정의)은 data-pipeline tests/test_airflow_dag_contract.py 가 대조한다.
여기서는 실행 관리가 업무 의미를 바꾸는 지점을 고정한다 — 기본 pause·당일 수집·백필 인자·계열 독립.
"""

from __future__ import annotations

import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dags"))
KST = timezone(timedelta(hours=9))


@pytest.fixture(scope="module")
def dag_module():
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        import edge_source_daily as module
    finally:
        socket.socket.connect = original
    return module


def test_new_lane_is_paused_until_someone_turns_it_on(dag_module):
    # WHY: 매크로 태스크 정의·키가 아직 없다. 배포만으로 정기 수집이 시작되면 안 된다.
    assert dag_module.dag.is_paused_upon_creation is True


def test_schedule_and_guards(dag_module):
    dag = dag_module.dag
    assert dag_module.CRONS == ("10 9 * * *",)
    assert dag.timetable.__class__.__name__ == "MultipleCronTriggerTimetable"
    assert dag.catchup is False and dag.max_active_runs == 1
    assert dag.dagrun_timeout < timedelta(seconds=1800)        # Reconciler 수명보다 짧게
    for family in dag_module.FAMILIES:
        collect = dag.get_task(f"{family}_collect")
        # 날짜 인자가 없는 원천(업종)은 오늘 값만 준다 — 과거 슬롯 run 이 오늘 값을 과거로 라벨하지 않게 막는다.
        assert collect.same_day_only and collect.noop_on_reprocess and collect.skip_if_succeeded
        assert dag.get_task(f"{family}_normalize").trigger_rule == "all_done_min_one_success"


def test_families_do_not_wait_for_each_other(dag_module):
    # WHY: 한 공급자 장애(예: EIA 키 없음)가 재무·업종 적재를 막으면 안 된다.
    dag = dag_module.dag
    for family in dag_module.FAMILIES:
        load = dag.get_task(f"{family}_load")
        others = {t for t in load.get_flat_relative_ids(upstream=True) if t.split("_")[0] != family}
        assert others == {"plan"}
    assert set(dag.get_task("report").upstream_task_ids) == {f"{f}_load" for f in dag_module.FAMILIES}


def _render(op, params):
    ctx = {"logical_date": datetime(2026, 10, 1, 9, 10, tzinfo=KST), "params": params,
           "dag_run": SimpleNamespace(conf={}, run_after=datetime(2026, 10, 1, 9, 10, tzinfo=KST)),
           "run_id": "r", "dag": op.dag, "ti": None}
    ctx.update(op.dag.user_defined_macros)
    return [op.render_template(c, ctx) for c in op.overrides["containerOverrides"][0]["command"]]


def test_backfill_params_reach_only_collectors_with_a_date_axis(dag_module):
    # WHY: 백필과 정기 수집은 같은 DAG 에서 인자로만 갈린다. 빈 값은 스텝이 정기 창으로 읽는다.
    dag = dag_module.dag
    params = {k: "" for k in ("macro_from", "macro_to", "financial_from", "financial_to")}
    params.update(macro_from="2026-01-01", macro_to="2026-06-30")
    macro = _render(dag.get_task("macro_collect"), params)
    assert macro[0] == "ingest-raw-macro" and macro[-4:] == ["--from", "2026-01-01", "--to", "2026-06-30"]
    financial = _render(dag.get_task("financial_collect"), params)
    assert financial[-4:] == ["--from", "", "--to", ""]
    sector = _render(dag.get_task("sector_collect"), params)
    assert "--from" not in sector          # 업종 마스터는 현재값뿐 — 백필 인자를 받지 않는다
    assert macro[2].startswith("run_") and macro[2] == sector[2] == financial[2]


def test_collect_on_a_past_slot_is_refused_before_any_ecs_call(dag_module):
    from airflow.sdk.exceptions import AirflowFailException

    op = dag_module.dag.get_task("sector_collect")
    past = datetime(2026, 1, 5, 9, 10, tzinfo=KST)
    ti = SimpleNamespace(xcom_push=lambda **k: None, xcom_pull=lambda **k: None, try_number=1, max_tries=2)
    with pytest.raises(AirflowFailException, match="소급 수집 불가"):
        op.execute({"ti": ti, "logical_date": past, "dag_run": SimpleNamespace(conf={}, run_after=past)})
