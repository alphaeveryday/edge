"""Planner 테스트 (ALPHA-530) — 스펙 §9 시나리오 1~5.

실제 Ledger 를 FakeOpsDB 위에서 돌려 실행 전 원장 기록 + SFN 시작 멱등/충돌을 검증한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.ops import entry
from data_pipeline.ops import planner as planner_mod
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
    assert len(db.etasks) == len(catalog.entries()) == 27


def test_same_day_different_slots_are_separate_runs():
    # WHY: run_key 의 계약은 "이 **슬롯**은 한 번만 계획된다"지 "하루 한 번"이 아니다(ALPHA-564).
    #      날짜로 키를 만들면 DB UNIQUE(run_key) 가 하루 1런을 못박아, 하루 여러 번 도는
    #      레인(뉴스 15:00·15:30·23:50, iNAV 15분)의 2회차부터가 1회차 슬롯에 흡수되고
    #      수동 실행은 원장에 자리가 없다 — 실제로 2026-07-26 에 관측이 막혔다.
    db = FakeOpsDB()
    ledger = _ledger(db)
    early = plan_run(ledger, state_machine_arn=_ARN,
                     scheduled_time=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc),  # KST 15:00
                     sfn_client=FakeSfn())
    late = plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED,             # KST 15:40
                    sfn_client=FakeSfn())

    assert early.created is True and late.created is True
    assert early.run_key == f"{PIPELINE_TYPE}:2026-07-24T15:00"
    assert late.run_key == f"{PIPELINE_TYPE}:2026-07-24T15:40"
    assert early.pipeline_run_id != late.pipeline_run_id
    assert early.execution_name != late.execution_name  # SFN 이름도 갈려야 실행이 안 뭉친다
    assert len(db.runs) == 2
    assert len(db.etasks) == 2 * len(catalog.entries())  # 슬롯마다 자기 기대작업을 갖는다


def test_same_slot_recall_is_still_idempotent_within_the_minute():
    # WHY: 슬롯을 분 단위로 쪼개면서 "Planner 재기동 무해"라는 원 성질을 잃으면 안 된다.
    #      같은 분 안의 재호출(재기동·중복 트리거)은 여전히 run 1개여야 한다.
    db = FakeOpsDB()
    ledger = _ledger(db)
    r1 = plan_run(ledger, state_machine_arn=_ARN,
                  scheduled_time=datetime(2026, 7, 24, 6, 40, 3, tzinfo=timezone.utc),
                  sfn_client=FakeSfn())
    r2 = plan_run(ledger, state_machine_arn=_ARN,
                  scheduled_time=datetime(2026, 7, 24, 6, 40, 57, tzinfo=timezone.utc),
                  sfn_client=FakeSfn())
    assert r1.run_key == r2.run_key
    assert r1.created is True and r2.created is False
    assert len(db.runs) == 1


def test_due_slot_key_matches_what_planner_planned():
    # WHY: 키 형식이 Planner 와 Reconciler 두 곳에서 각자 조립되면, 어긋나는 순간 Reconciler 가
    #      **있지도 않은 슬롯**을 찾아 PLANNER_MISSING 오탐을 낸다(원장이 거짓 경보를 내는 축).
    #      _due_slot 은 여태 테스트가 없었다 — 형식을 바꾸는 이 변경에서 그 합치를 못박는다.
    db = FakeOpsDB()
    planned = plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED,
                       sfn_client=FakeSfn())
    # 그 슬롯이 지난 시각에 Reconciler 가 찾는 키.
    due = entry._due_slot(datetime(2026, 7, 24, 16, 30, tzinfo=planner_mod.KST))

    assert due is not None
    assert due[0] == planned.run_key


def test_manual_run_gets_its_own_slot_outside_the_schedule_minute():
    # WHY: 수동 실행은 OPS_SCHEDULED_TIME 없이 돌아 실행 분이 슬롯이 된다. 스케줄 분과 다르면
    #      자기 슬롯을 가지므로 (1) 스케줄 런의 자리를 뺏지 않고 (2) _due_slot 이 그 키를 만들지
    #      않아 결측 판정 대상도 아니다 — 거짓 PLANNER_MISSING 이 안 난다.
    db = FakeOpsDB()
    manual = plan_run(_ledger(db), state_machine_arn=_ARN,
                      scheduled_time=datetime(2026, 7, 24, 2, 51, tzinfo=timezone.utc),  # KST 11:51
                      sfn_client=FakeSfn())
    due = entry._due_slot(datetime(2026, 7, 24, 16, 30, tzinfo=planner_mod.KST))

    assert manual.run_key == f"{PIPELINE_TYPE}:2026-07-24T11:51"
    assert due is not None and due[0] != manual.run_key


def test_manual_run_slot_comes_from_the_wall_clock_not_a_default(monkeypatch):
    # WHY: "수동 실행은 실행 분이 슬롯이 된다"가 위 두 테스트가 기대는 계약인데, 정작 그 값을
    #      만드는 건 `entry._scheduled_time()` 의 **env 부재 경로**다(EventBridge 만 넣는 값이라
    #      수동 실행엔 없다). 그 경로가 고정 기본값이나 UTC naive 를 돌려주면 수동 실행이 엉뚱한
    #      슬롯을 잡는데, plan_run 에 시각을 직접 넘기는 테스트로는 절대 안 드러난다.
    monkeypatch.delenv("OPS_SCHEDULED_TIME", raising=False)
    before = datetime.now(timezone.utc)
    got = entry._scheduled_time()

    assert got.tzinfo is not None                      # naive 면 KST 환산이 9시간 어긋난다
    assert before <= got <= datetime.now(timezone.utc)  # 고정 기본값이 아니라 실제 지금

    # env 가 있으면(스케줄 실행) 그 값이 이기고, 그것이 슬롯 키가 된다.
    monkeypatch.setenv("OPS_SCHEDULED_TIME", "2026-07-24T06:40:00Z")
    assert planner_mod.slot_run_key(
        entry._scheduled_time().astimezone(planner_mod.KST)
    ) == f"{PIPELINE_TYPE}:2026-07-24T15:40"


def test_manual_run_on_the_schedule_minute_is_absorbed_not_duplicated():
    # WHY: 위 성질의 **경계**다. 수동 실행이 하필 스케줄 분(15:40)에 걸리면 같은 슬롯이라
    #      흡수된다 — 이건 결함이 아니라 슬롯 멱등의 정의다. 대신 흡수를 **조용히** 하면 안 된다:
    #      운영자는 "돌렸다"고 믿는데 새로 도는 건 없기 때문이다(2026-07-26 에 중단된 실행을
    #      가리키며 LAUNCHED 로 보고돼 실제로 겪은 함정). created=False 가 그 사실을 드러내고
    #      CLI 로그가 그것을 찍는다 — 그 계약을 여기서 잠근다(Rule 12).
    db = FakeOpsDB()
    ledger = _ledger(db)
    scheduled = plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED,
                         sfn_client=FakeSfn())
    manual = plan_run(ledger, state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn())

    assert manual.run_key == scheduled.run_key
    assert scheduled.created is True
    assert manual.created is False       # 흡수됐다는 사실이 반환값에 드러난다
    assert len(db.runs) == 1


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
    assert a.execution_name == b.execution_name == "etf-daily-2026-07-24T15-40"
    assert a.input_hash == b.input_hash
    assert s1.start_calls[0]["input"] == s2.start_calls[0]["input"]
    # run_id 는 run_key 에서 결정적으로 파생 → execution_name 멱등의 근거.
    assert a.pipeline_run_id == stable_domain_id("run", f"{PIPELINE_TYPE}:2026-07-24T15:40")


def test_idempotent_recall_same_running_execution():
    """시나리오 4 — 같은 RUNNING execution 에 대한 멱등 재호출 → LAUNCHED."""
    db = FakeOpsDB()
    run_key = f"{PIPELINE_TYPE}:2026-07-24T15:40"
    rid = stable_domain_id("run", run_key)
    same_input = json.dumps({"mode": "incremental", "run_id": rid},
                            sort_keys=True, separators=(",", ":"))
    sfn = FakeSfn(already_exists=True,
                  describe={"input": same_input, "status": "RUNNING",
                            "executionArn": "arn:...:execution:sm:etf-daily-2026-07-24T15-40"})
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
