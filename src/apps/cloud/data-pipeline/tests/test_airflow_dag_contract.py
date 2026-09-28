"""Airflow DAG(src/apps/cloud/airflow/dags)가 복제한 업무 계약을 업무 코드 정의와 대조한다(ALPHA-1088).

DAG 는 Airflow 환경에 업무 패키지를 설치하지 않으려고 상수·env 이름을 복제한다. 어느 한쪽만 바뀌면
두 쪽 테스트가 모두 초록인 채로 재시도 분류(exit 75)·attempt 참조·중복 가드가 조용히 끊긴다.
Airflow 가 없는 이 환경에서도 돌도록 DAG 소스를 **텍스트로** 읽는다(import 하지 않는다).
"""

from __future__ import annotations

import re
from pathlib import Path

import ast

from data_pipeline import db
from data_pipeline.ops import catalog, states, wrapper

DAGS = Path(__file__).resolve().parents[2] / "airflow" / "dags"
EDGE_BATCH = (DAGS / "edge_batch.py").read_text(encoding="utf-8")
DAG = (DAGS / "edge_investor_intraday.py").read_text(encoding="utf-8")
WRAPPER = Path(wrapper.__file__).read_text(encoding="utf-8")
ENTRY = (Path(wrapper.__file__).parent / "entry.py").read_text(encoding="utf-8")


def _const(name: str, source: str) -> str:
    match = re.search(rf"^{name} = (.+)$", source, re.M)
    assert match, f"{name} 이 DAG 소스에 없다"
    return match.group(1).strip()


def test_step_not_run_exit_matches_wrapper():
    assert int(_const("STEP_NOT_RUN_EXIT", EDGE_BATCH)) == wrapper.STEP_NOT_RUN_EXIT


def test_hold_contract_matches_the_ledger():
    # 76 을 업무 실패로 읽으면 보류가 "실패"로 접히고, 종류 이름이 어긋나면 report 가 원장 기록을 거부하거나
    # (entry 검증) 원장 게이트가 ECS 보류를 못 본다(StepLock.blocking 의 kind 조건).
    assert int(_const("STEP_HELD_EXIT", EDGE_BATCH)) == wrapper.STEP_HELD_EXIT
    for name in ("HOLD_OPEN_ATTEMPT", "HOLD_ECS_STATE_UNKNOWN", "HOLD_RESULT_UNKNOWN"):
        assert _const(name, EDGE_BATCH).strip('"') == getattr(states, name)


def test_dag_task_keys_are_catalog_keys_of_the_lane():
    # report 가 보류를 원장에 옮길 때 쓰는 이름 — 틀리면 entry 가 거부해 report 가 실패한다.
    keys = ast.literal_eval(re.search(r"^TASK_KEYS = (\{.*?\})$", DAG, re.M | re.S).group(1))
    assert sorted(keys.values()) == sorted(
        e.task_key for e in catalog.entries(catalog.INVESTOR_INTRADAY_PIPELINE_TYPE))


def test_pipeline_run_id_material_matches_stable_domain_id():
    # 구분자(\x01)는 화면에 안 보인다 — 손으로 옮기다 틀린 전력이 있다(학습 문서 §32).
    assert _const("_PIPELINE_ID", EDGE_BATCH).strip('"') == db.PIPELINE_ID
    assert '"\\x01".join([_PIPELINE_ID, run_key(lane, slot)])' in EDGE_BATCH
    assert db.stable_domain_id("run", "investor-intraday:2026-09-22T09:35") == "run_c52a16e3d7f98774b0fe384f5d"


def test_env_names_the_dag_sends_are_the_ones_the_pipeline_reads():
    sent = {"OPS_EXCLUSIVE_STEP", "OPS_SKIP_IF_SUCCEEDED", "OPS_ORCHESTRATOR_ATTEMPT_REF"}
    for name in sent:
        assert f'"{name}"' in EDGE_BATCH, f"DAG 가 {name} 을 보내지 않는다"
        assert f'"{name}"' in WRAPPER, f"wrapper 가 {name} 을 읽지 않는다"
    for name in ("OPS_ORCHESTRATOR", "OPS_ORCHESTRATOR_RUN_REF", "OPS_SCHEDULED_TIME", "OPS_REPROCESS",
                 "OPS_RUN_KEY", "OPS_ORCHESTRATION_STATUS", "OPS_CLUSTER_ARN", "OPS_EXECUTION_HOLDS"):
        assert f'"{name}"' in DAG, f"DAG 가 {name} 을 보내지 않는다"
        assert f'"{name}"' in ENTRY, f"entry 가 {name} 을 읽지 않는다"
