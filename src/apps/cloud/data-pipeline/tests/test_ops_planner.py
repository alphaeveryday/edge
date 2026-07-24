"""Planner 테스트 (ALPHA-530) — 스펙 §9 시나리오 1~5.

실제 Ledger 를 FakeOpsDB 위에서 돌려 실행 전 원장 기록 + SFN 시작 멱등/충돌을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.ops import states
from data_pipeline.ops.catalog import PIPELINE_TYPE
from data_pipeline.ops.ledger import Ledger
from data_pipeline.ops.planner import plan_run

from opsfakes import FakeOpsDB, FakeSfn

_DB = DbConfig(password="x")
_ARN = "arn:aws:states:ap-northeast-2:123456789012:stateMachine:edge-dev-data-pipeline"
# 2026-07-24 = 금요일(거래일). 06:40 UTC = KST 15:40.
_SCHED = datetime(2026, 7, 24, 6, 40, tzinfo=timezone.utc)


def _ledger(db):
    return Ledger(db=_DB, connect_fn=db.connect)


def test_duplicate_planner_run_creates_one_pipeline_run():
    """시나리오 1 — 같은 슬롯 Planner 중복 실행에도 pipeline_run 1개."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    r1 = plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn())
    r2 = plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn())
    assert r1.created is True and r2.created is False
    assert r1.pipeline_run_id == r2.pipeline_run_id
    assert len(db.runs) == 1
    # expected_task 도 중복 생성되지 않는다(3작업만).
    assert len(db.etasks) == 3


def test_non_trading_day_skips_price_tasks_no_attempt():
    """시나리오 2 — 비거래일: SKIPPED(NON_TRADING_DAY), attempt 생성 여지 없음."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
             holidays=frozenset({"2026-07-24"}))
    for row in db.etasks.values():
        assert row["plan_status"] == states.PLAN_SKIPPED
        assert row["skip_reason"] == states.SKIP_NON_TRADING_DAY
        # 축 분리: SKIPPED 면 outcome/data_status 는 NULL(attempt 안 붙는다).
        assert row["task_outcome"] is None and row["data_status"] is None
    assert db.attempts == []


def test_deterministic_execution_name_and_input():
    """시나리오 3 — 결정적 execution name·input·hash."""
    a = plan_run(_ledger(FakeOpsDB()), state_machine_arn=_ARN, scheduled_time=_SCHED,
                 sfn_client=(s1 := FakeSfn()))
    b = plan_run(_ledger(FakeOpsDB()), state_machine_arn=_ARN, scheduled_time=_SCHED,
                 sfn_client=(s2 := FakeSfn()))
    assert a.execution_name == b.execution_name == "etf-daily-2026-07-24"
    assert a.input_hash == b.input_hash
    assert s1.start_calls[0]["input"] == s2.start_calls[0]["input"]
    # run_id 는 run_key 에서 결정적으로 파생 → execution_name 멱등의 근거.
    assert a.pipeline_run_id == stable_domain_id("run", f"{PIPELINE_TYPE}:2026-07-24")


def test_idempotent_recall_same_running_execution():
    """시나리오 4 — 같은 RUNNING execution 에 대한 멱등 재호출 → LAUNCHED."""
    db = FakeOpsDB()
    run_key = f"{PIPELINE_TYPE}:2026-07-24"
    rid = stable_domain_id("run", run_key)
    same_input = json.dumps({"mode": "incremental", "run_id": rid},
                            sort_keys=True, separators=(",", ":"))
    sfn = FakeSfn(already_exists=True,
                  describe={"input": same_input, "status": "RUNNING",
                            "executionArn": "arn:...:execution:sm:etf-daily-2026-07-24"})
    result = plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=sfn)
    assert result.launch_status == states.LAUNCH_LAUNCHED
    assert result.conflict is False
    assert db.runs[run_key]["orchestration_status"] == states.ORCH_RUNNING


def test_execution_already_exists_different_input_is_conflict():
    """시나리오 5 — 다른 input 의 ExecutionAlreadyExists → LAUNCH_CONFLICT + 이슈."""
    db = FakeOpsDB()
    sfn = FakeSfn(already_exists=True,
                  describe={"input": json.dumps({"mode": "incremental", "run_id": "OTHER"}),
                            "status": "RUNNING", "executionArn": "arn:...:execution:sm:x"})
    result = plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=sfn)
    assert result.launch_status == states.LAUNCH_CONFLICT
    assert result.conflict is True
    assert len(db.open_issues(states.ISSUE_LAUNCH_CONFLICT)) == 1


def test_snapshot_created_when_universe_provided():
    """expectation_snapshot 이 provider 로 생성되고 expected_task 에 연결된다(스펙 §6)."""
    db = FakeOpsDB()

    def universe(task_key):
        if task_key == "PRICE_COLLECTION_KIS":
            return {"universe_version": "v1", "as_of_date": "2026-07-23",
                    "entity_ids": ["005930", "000660"]}
        return None

    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
             universe_provider=universe)
    assert len(db.snapshots) == 1
    assert db.snapshots[0]["entity_ids"] == json.dumps(["005930", "000660"], ensure_ascii=False)
