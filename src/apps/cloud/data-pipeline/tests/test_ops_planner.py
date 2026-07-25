"""Planner 테스트 (ALPHA-530) — 스펙 §9 시나리오 1~5.

실제 Ledger 를 FakeOpsDB 위에서 돌려 실행 전 원장 기록 + SFN 시작 멱등/충돌을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.ops import states
from data_pipeline.ops import catalog
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
    # expected_task 도 중복 생성되지 않는다(등록 작업 수만큼만).
    assert len(db.etasks) == len(catalog.entries()) == 24


def test_non_trading_day_skips_price_tasks_no_attempt():
    """시나리오 2 — 비거래일: SKIPPED(NON_TRADING_DAY), attempt 생성 여지 없음."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
             holidays=frozenset({"2026-07-24"}))
    kr_tasks = {e.task_key for e in catalog.entries() if e.kr_trading_calendar}
    assert kr_tasks, "KR 달력 작업이 하나도 없다면 이 시나리오가 무의미하다"
    for row in db.etasks.values():
        if row["task_key"] not in kr_tasks:
            # 뉴스·공시·마스터처럼 KR 거래일과 무관한 작업은 휴장일에도 DUE 다 — SKIPPED 로
            # 찍으면 그날 실제로 돈 결과가 "휴장이라 안 했다"로 사라진다(ALPHA-181).
            assert row["plan_status"] == states.PLAN_DUE
            continue
        assert row["plan_status"] == states.PLAN_SKIPPED
        assert row["skip_reason"] == states.SKIP_NON_TRADING_DAY
        # 축 분리: SKIPPED 면 outcome/data_status 는 NULL(attempt 안 붙는다).
        assert row["task_outcome"] is None and row["data_status"] is None
    assert db.attempts == []


def test_non_kr_task_is_not_skipped_on_kr_holiday(monkeypatch):
    # WHY: `is_trading_day` 는 **KR 전용 달력**인데 `ingest_price_raw.DATASET` 은 fmp·kis 공통
    #      `price_daily` 다. dataset 문자열로 SKIP 을 가르면 KR 공휴일에 **미국 시장 수집까지**
    #      SKIPPED 로 계획되고, 그날 실제로 돈 FMP 수집의 결과(실패 포함)가 "휴장이라 안 했다"로
    #      기록돼 사라진다(SKIPPED 면 wrapper 가 attempt 를 안 만든다). 판정 축은 명시 필드다.
    import dataclasses

    from data_pipeline.ops import catalog

    kr = catalog.get("PRICE_COLLECTION_KIS")
    us = dataclasses.replace(kr, task_key="PRICE_COLLECTION_FMP", sfn_state_name="CollectFmpPrice",
                             source_vendor="fmp", kr_trading_calendar=False)
    monkeypatch.setattr(catalog, "entries", lambda: (kr, us))

    db = FakeOpsDB()
    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
             holidays=frozenset({"2026-07-24"}))
    rows = {row["task_key"]: row for row in db.etasks.values()}
    assert rows["PRICE_COLLECTION_KIS"]["plan_status"] == states.PLAN_SKIPPED
    assert rows["PRICE_COLLECTION_FMP"]["plan_status"] == states.PLAN_DUE   # 미국장은 열려 있다


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
