"""장전 유니버스 DAG 계약 테스트(ALPHA-1408) — SFN 경로와 갈라지면 안 되는 것(슬롯·run_id·명령·전량 성공 게이트)과
terraform 값 인자의 전달 경로를 고정한다."""

from __future__ import annotations

import importlib
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT / "dags"))
KST = timezone(timedelta(hours=9))


@pytest.fixture(scope="module")
def dag_module():
    original = socket.socket.connect
    socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("parse-time network"))
    try:
        return importlib.import_module("edge_premarket")
    finally:
        socket.socket.connect = original


def test_slot_matches_terraform_schedule(dag_module):
    tf = (REPO / "infra/terraform/modules/data-pipeline/variables.tf").read_text()
    block = tf[tf.index('variable "premarket_schedule_expression"'):]
    minute, hour = re.search(r'default\s+= "cron\((\d+) (\d+) \? \* MON-FRI \*\)"', block).groups()
    assert "premarket_schedule_expression" not in (REPO / "infra/terraform/envs/dev/main.tf").read_text()
    assert dag_module.CRONS == (f"{minute} {hour} * * 1-5",)


def test_run_id_is_the_sfn_slot_run_id(dag_module):
    # 실제 dev 실행(2026-10-20 07:00 KST, 입력 scheduled_time 2026-10-19T22:00:00Z)의 run_id — 같아야 raw·canonical
    # 파티션과 유니버스 manifest 가 실행 주체와 무관하게 같은 자리에 놓인다.
    assert dag_module.utc_run_id(datetime(2026, 10, 20, 7, 0, tzinfo=KST)) == "20261019T220000Z"


def test_commands_match_sfn_definition(dag_module):
    from sfn_commands import dag_command, sfn_command
    for task_id, state in (("collect", "CollectKrxEtf"), ("normalize", "NormalizeEtf"),
                           ("universe", "BuildMinuteUniverse")):
        taskdef, args = sfn_command(state)
        op = dag_module.dag.get_task(task_id)
        assert op.task_definition.endswith(f"-{taskdef}"), task_id
        assert dag_command(op) == args, task_id


def test_any_step_failure_stops_the_chain(dag_module):
    # ASL Premarket*CheckResults: 수집이 실패하면 정제·유니버스를 돌리지 않는다(공시·장중과 다르다 — 어제 유니버스를
    # 유지하는 편이 반쪽 입력으로 갈아끼우는 것보다 낫다).
    dag = dag_module.dag
    assert dag.get_task("collect").upstream_task_ids == {"refuse_reprocess"}
    assert dag.get_task("normalize").upstream_task_ids == {"collect"}
    assert dag.get_task("universe").upstream_task_ids == {"normalize"}
    for task_id in dag_module.STEPS:
        op = dag.get_task(task_id)
        assert op.trigger_rule == "all_success" and op.partial_exit_codes == (), task_id
    assert [t.task_id for t in dag.leaves] == ["verdict"]


def test_out_of_ledger_lane_takes_no_ledger_guards(dag_module):
    # 원장에 이 run 의 기대 작업이 없다. 실행권은 작업 키 단위라 켜면 같은 CLI 의 시장 레인 작업 실행권을 잡는다.
    dag = dag_module.dag
    assert not {"plan", "report"} & set(dag.task_ids)
    for task_id in dag_module.STEPS:
        op = dag.get_task(task_id)
        assert op.exclusive is False and op.skip_if_succeeded is False, task_id
    assert dag.get_task("collect").same_day_only is True     # KRX 는 실행 시각의 PDF 를 준다
    assert dag.is_paused_upon_creation is True and dag.catchup is False and dag.max_active_runs == 1
    assert dag.dagrun_timeout == timedelta(seconds=1500)     # 07:30 교체 마감 = SFN TimeoutSeconds


def test_reprocess_conf_is_refused_before_collection(dag_module):
    # 원장 밖이라 "raw 가 있는 기존 슬롯" 확인이 없고, EdgeStep 은 재처리 run 의 당일 검사를 건너뛴다 — 받으면 오늘 KRX
    # 값이 과거 run_id 로 쓰이거나 옛 유니버스가 오늘 것을 덮는다.
    from types import SimpleNamespace
    from airflow.sdk.exceptions import AirflowFailException
    guard = dag_module.dag.get_task("refuse_reprocess")
    with pytest.raises(AirflowFailException, match="재처리"):
        guard.python_callable(dag_run=SimpleNamespace(conf={"reprocess_slot": "2026-10-19T07:00:00+09:00"}))
    guard.python_callable(dag_run=SimpleNamespace(conf={}))
    assert dag_module.dag.get_task("collect").trigger_rule == "all_success"


def test_lane_args_reach_airflow_from_the_same_output():
    # SFN 은 terraform 변수를 직접 넣고, DAG 는 Airflow env 로 받는다 — 그 env 가 같은 출력에서 와야 한다.
    airflow_main = (REPO / "infra/terraform/modules/airflow/main.tf").read_text()
    dev = (REPO / "infra/terraform/envs/dev/main.tf").read_text()
    assert re.search(r"^\s*EDGE_LANE_ARGS\s*=\s*jsonencode\(var\.lane_command_args\)$", airflow_main, re.M)
    assert re.search(r"^\s*lane_command_args\s*=\s*module\.data_pipeline\.airflow_lane_args$", dev, re.M)


def test_lane_arg_fails_instead_of_running_with_a_missing_value(monkeypatch):
    from airflow.sdk.exceptions import AirflowFailException
    from edge_batch import lane_arg
    monkeypatch.setenv("EDGE_LANE_ARGS", '{"krx_etf_deadline_sec": "660"}')
    assert lane_arg("krx_etf_deadline_sec") == "660"
    with pytest.raises(AirflowFailException):
        lane_arg("minute_universe_uri")
    monkeypatch.delenv("EDGE_LANE_ARGS")
    with pytest.raises(AirflowFailException):
        lane_arg("krx_etf_deadline_sec")
