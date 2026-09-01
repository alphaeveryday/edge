"""Planner 테스트 (ALPHA-530) — 스펙 §9 시나리오 1~5.

실제 Ledger 를 FakeOpsDB 위에서 돌려 실행 전 원장 기록 + SFN 시작 멱등/충돌을 검증한다.
"""

from __future__ import annotations

import json
import dataclasses
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from pathlib import Path

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.ops import entry
from data_pipeline.ops import planner as planner_mod
from data_pipeline.ops import states
from data_pipeline.minute import states as minute_states
from data_pipeline.ops import catalog
from data_pipeline.ops import contracts
from data_pipeline.ops.catalog import PIPELINE_TYPE
from data_pipeline.ops.ledger import Ledger
from data_pipeline.ops.planner import plan_run

import test_ops_catalog          # terraform 실제 값 파서 재사용(중복 파서 금지)
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
    # expected_task 도 중복 생성되지 않는다(자기 레인의 등록 작업 수만큼만 — 카탈로그는 전 레인
    # 27이지만 시장 일일런 기대는 17 이다. 뉴스 6·공시 4는 자기 레인 런이 계획한다).
    assert len(db.etasks) == len(catalog.entries(PIPELINE_TYPE)) == 17


def test_same_day_different_slots_are_separate_runs():
    # WHY: run_key 의 계약은 "이 **슬롯**은 한 번만 계획된다"지 "하루 한 번"이 아니다(ALPHA-564).
    #      날짜로 키를 만들면 DB UNIQUE(run_key) 가 하루 1런을 못박아, 하루 여러 번 도는
    #      레인(뉴스 00:10·08:10, iNAV 15분)의 2회차부터가 1회차 슬롯에 흡수되고
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
    assert len(db.etasks) == 2 * len(catalog.entries(PIPELINE_TYPE))  # 슬롯마다 자기 기대작업


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
    db = FakeOpsDB()
    planned = plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED,
                       sfn_client=FakeSfn())
    # 그 슬롯이 지난 시각에 Reconciler 가 찾는 키.
    due = entry._due_slots(datetime(2026, 7, 24, 16, 30, tzinfo=planner_mod.KST))

    assert [key for key, _ in due] == [planned.run_key]   # 뉴스 env 미주입 — 시장 슬롯만


def test_manual_run_gets_its_own_slot_outside_the_schedule_minute():
    # WHY: 수동 실행은 OPS_SCHEDULED_TIME 없이 돌아 실행 분이 슬롯이 된다. 스케줄 분과 다르면
    #      자기 슬롯을 가지므로 (1) 스케줄 런의 자리를 뺏지 않고 (2) _due_slots 가 그 키를 만들지
    #      않아 결측 판정 대상도 아니다 — 거짓 PLANNER_MISSING 이 안 난다.
    db = FakeOpsDB()
    manual = plan_run(_ledger(db), state_machine_arn=_ARN,
                      scheduled_time=datetime(2026, 7, 24, 2, 51, tzinfo=timezone.utc),  # KST 11:51
                      sfn_client=FakeSfn())
    due = entry._due_slots(datetime(2026, 7, 24, 16, 30, tzinfo=planner_mod.KST))

    assert manual.run_key == f"{PIPELINE_TYPE}:2026-07-24T11:51"
    assert due and all(key != manual.run_key for key, _ in due)


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


def test_krx_contract_is_snapshotted_with_interpreted_expected_as_of():
    """WHY: 비거래일 슬롯 날짜를 expected-as-of로 쓰면 직전 거래일 데이터가 거짓 STALE이 된다."""
    db = FakeOpsDB()
    sunday = datetime(2026, 7, 26, 6, 40, tzinfo=timezone.utc)
    plan_run(
        _ledger(db), state_machine_arn=_ARN, scheduled_time=sunday, sfn_client=FakeSfn(),
        holidays=frozenset({"2026-07-24"}),
    )

    row = db.etasks[(
        next(iter(db.runs_by_id)), "ETF_HOLDINGS_COLLECTION_KRX"
    )]
    assert row["expected_as_of_date"] == "2026-07-23"
    assert row["dataset_contract_key"] == contracts.ETF_HOLDINGS_KRX_EOD
    assert row["dataset_contract_version"] == "1"
    assert row["dataset_contract_snapshot"]["expected_as_of"] == "2026-07-23"
    assert row["freshness_status"] == states.FRESHNESS_UNKNOWN
    assert row["freshness_reason"] == states.FRESHNESS_EVIDENCE_MISSING
    assert row["observed_at"] is None


def test_uncontracted_tasks_remain_freshness_not_applicable():
    """WHY: 계약 미연결의 NULL과 증거 부족 UNKNOWN을 섞으면 적용 범위를 알 수 없다."""
    db = FakeOpsDB()
    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn())
    row = next(row for row in db.etasks.values() if row["task_key"] == "NORMALIZE_ETF")
    assert row["dataset_contract_key"] is None
    assert row["freshness_status"] is None
    assert row["freshness_reason"] is None


def test_planner_fails_loud_when_catalog_required_conflicts_with_contract(monkeypatch):
    """WHY: additive 전환 중 required가 둘이면 불일치를 조용히 선택해선 안 된다."""
    krx = catalog.get("ETF_HOLDINGS_COLLECTION_KRX")
    assert krx is not None
    monkeypatch.setattr(
        catalog, "entries",
        lambda pipeline_type=None: (dataclasses.replace(krx, required=False),),
    )

    with pytest.raises(ValueError, match="required"):
        plan_run(
            _ledger(FakeOpsDB()), state_machine_arn=_ARN,
            scheduled_time=_SCHED, sfn_client=FakeSfn(),
        )


def test_required_conflict_fails_loud_even_on_idempotent_replan(monkeypatch):
    """WHY: 검사가 created=True 경로에만 있으면, required 가 어긋난 빌드가 이미 계획된
    슬롯을 재호출할 때(created=False) 검사 없이 SFN 시작까지 진행한다 — 우회로다."""
    db = FakeOpsDB()
    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn())

    krx = catalog.get("ETF_HOLDINGS_COLLECTION_KRX")
    monkeypatch.setattr(
        catalog, "entries",
        lambda pipeline_type=None: (dataclasses.replace(krx, required=False),),
    )

    with pytest.raises(ValueError, match="required"):
        plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED,
                 sfn_client=FakeSfn())


def test_contracted_task_skipped_by_calendar_keeps_freshness_null(monkeypatch):
    """WHY: 계약 연결 + SKIPPED 조합에 UNKNOWN 을 쓰면 DB 의 freshness applicability
    CHECK 와 충돌하고, "관측 대상 아님"과 "증거 없음"이 화면에서 섞인다(완료조건 ③)."""
    krx = catalog.get("ETF_HOLDINGS_COLLECTION_KRX")
    monkeypatch.setattr(
        catalog, "entries",
        lambda pipeline_type=None: (dataclasses.replace(krx, kr_trading_calendar=True),),
    )
    db = FakeOpsDB()
    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
             holidays=frozenset({"2026-07-24"}))

    row = next(iter(db.etasks.values()))
    assert row["plan_status"] == states.PLAN_SKIPPED
    assert row["dataset_contract_key"] == contracts.ETF_HOLDINGS_KRX_EOD
    assert row["freshness_status"] is None
    assert row["freshness_reason"] is None


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
    monkeypatch.setattr(catalog, "entries", lambda pipeline_type=None: (kr, us))

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


# ── 뉴스 레인 (ALPHA-591) ─────────────────────────────────────────────────────


def test_news_lane_plans_only_news_tasks_with_its_own_key():
    # WHY: 레인 축의 핵심 계약이다 — 뉴스 슬롯이 시장 21작업을 기대에 실으면 매 뉴스런이
    #      21건 MISSED 를 열고, 반대로 시장 런에 뉴스 6작업이 실리면 매 일일런이 6건 MISSED 다
    #      (ALPHA-553 PR2 가 카탈로그에서 뉴스를 뺐던 바로 그 이유). run_key 접두가 달라야
    #      같은 분(15:00)의 시장 수동 런과도 안 충돌한다.
    db = FakeOpsDB()
    result = plan_run(_ledger(db), state_machine_arn=_ARN,
                      scheduled_time=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc),  # KST 15:00
                      sfn_client=FakeSfn(), pipeline_type="news")

    assert result.run_key == "news:2026-07-24T15:00"
    assert result.execution_name == "news-2026-07-24T15-00"   # 콜론 없는 charset — 멱등 Name 가능
    planned = {row["task_key"] for row in db.etasks.values()}
    assert planned == {e.task_key for e in catalog.entries("news")}
    assert len(planned) == 6
    assert db.runs[result.run_key]["pipeline_type"] == "news"


def test_news_and_market_same_minute_are_separate_runs():
    # WHY: 슬롯 키에 레인 접두가 없으면 DB UNIQUE(run_key) 가 같은 분의 두 레인을 한 런으로
    #      합쳐 뒤에 온 레인이 계획을 뺏긴다(created=False + 남의 기대작업).
    db = FakeOpsDB()
    ledger = _ledger(db)
    market = plan_run(ledger, state_machine_arn=_ARN,
                      scheduled_time=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc),
                      sfn_client=FakeSfn())
    news = plan_run(ledger, state_machine_arn=_ARN,
                    scheduled_time=datetime(2026, 7, 24, 6, 0, tzinfo=timezone.utc),
                    sfn_client=FakeSfn(), pipeline_type="news")
    assert market.created is True and news.created is True
    assert market.run_key != news.run_key
    assert len(db.runs) == 2


def test_news_tasks_are_due_on_any_non_trading_day():
    # WHY: 뉴스 SFN 은 비거래일에도 돈다 — kr_trading_calendar 를 하나라도 True 로 복사해 오면
    #      그날 실행 결과가 SKIPPED 뒤로 통째로 사라진다(ALPHA-181 함정).
    #      ⭐ 축이 둘이다(ALPHA-874): **평일 공휴일**과 **주말**. 크론이 주 7일이 되면서 주말이
    #      실제로 들어오는데, `is_trading_day` 는 토·일을 무조건 비거래일로 본다 — 그래서 크론을
    #      넓히는 것만으로는 부족하고 이 False 가 함께 성립해야 토요일 런이 실일을 한다.
    for label, sched, holidays in [
        ("평일 공휴일", _SCHED, frozenset({"2026-07-24"})),
        # 2026-08-01(토) 15:00 KST = 06:00 UTC.
        ("주말", datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc), frozenset()),
    ]:
        db = FakeOpsDB()
        plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=sched, sfn_client=FakeSfn(),
                 pipeline_type="news", holidays=holidays)
        assert db.etasks, label
        for row in db.etasks.values():
            assert row["plan_status"] == states.PLAN_DUE, f"{label}/{row['task_key']}"


def test_due_slots_returns_all_past_news_slots_of_the_day(monkeypatch):
    # WHY: 뉴스는 하루 여러 슬롯이다. 최신 슬롯 하나만 돌려주면 뒤 슬롯이 지나는 순간 앞 런이
    #      영영 대조되지 않는다(ALPHA-565 사각의 확대재생산) — 그날 지난 슬롯 전부가 나와야
    #      주기 reconcile 이 늦은 종결까지 판정한다. grace(30분)는 슬롯별로 계산된다.
    #      ⚠️ 아래 3슬롯은 **합성 픽스처**다(운영은 ALPHA-905 이후 00:10·08:10 둘). 여기서
    #      운영 값을 쓰면 "지났지만 grace 전" 축이 사라져 한 슬롯만 걸리는 표가 된다 — 이
    #      함수는 env 를 읽어 슬롯 수에 무관하므로, 축을 더 많이 태우는 쪽을 고정한다.
    monkeypatch.setenv("OPS_NEWS_SCHED_HHMM", "15:00,15:30,23:50")
    due = entry._due_slots(datetime(2026, 7, 24, 15, 45, tzinfo=planner_mod.KST))

    assert due == [
        (f"{PIPELINE_TYPE}:2026-07-24T15:40", False),   # 15:45 는 grace(30분) 전
        ("news:2026-07-24T15:00", True),                # grace 지남 — 결측 판정 대상
        ("news:2026-07-24T15:30", False),               # 지났지만 grace 전
    ]

    # 자정 직후(다음 날) — 전날 3슬롯 전부가 여전히 대조 대상이다(23:50 런은 자정을 넘겨 끝난다).
    due = entry._due_slots(datetime(2026, 7, 25, 0, 10, tzinfo=planner_mod.KST))
    assert [key for key, _ in due if key.startswith("news:")] == [
        "news:2026-07-24T15:00", "news:2026-07-24T15:30", "news:2026-07-24T23:50",
    ]


def test_malformed_news_sched_env_fails_loud(monkeypatch):
    # WHY: 불량 항목을 조용히 제외하면 그 슬롯의 run_key 가 영영 안 만들어져 PLANNER_MISSING
    #      탐지가 관대한 방향으로 축소된다 — Planner 미기동이 아무 이슈 없이 지나간다(Rule 12).
    #      부분 손상("15-30")이 나머지 슬롯만으로 성공 종료하면 안 된다.
    monkeypatch.setenv("OPS_NEWS_SCHED_HHMM", "15:00,15-30,23:50")
    with pytest.raises(SystemExit, match="15-30"):
        entry._lane_sched_hhmms("OPS_NEWS_SCHED_HHMM")
    # 빈 **항목**도 손상이다("15:00, ,23:50" 의 가운데 슬롯이 소리 없이 사라진다) — 값 전체가
    # 빈 것(미주입·컷오버 전)과 다르다.
    monkeypatch.setenv("OPS_NEWS_SCHED_HHMM", "15:00, ,23:50")
    with pytest.raises(SystemExit):
        entry._lane_sched_hhmms("OPS_NEWS_SCHED_HHMM")
    monkeypatch.setenv("OPS_NEWS_SCHED_HHMM", "  ")
    assert entry._lane_sched_hhmms("OPS_NEWS_SCHED_HHMM") == []


def test_due_slots_without_news_env_has_no_news_slots(monkeypatch):
    # WHY: env 미주입(뉴스 스케줄이 아직 Planner 를 안 타는 배포)에 뉴스 슬롯을 지어내면
    #      존재할 수 없는 run 을 찾아 매 주기 거짓 PLANNER_MISSING 을 연다.
    monkeypatch.delenv("OPS_NEWS_SCHED_HHMM", raising=False)
    due = entry._due_slots(datetime(2026, 7, 24, 16, 30, tzinfo=planner_mod.KST))
    assert all(not key.startswith("news:") for key, _ in due)


def test_plan_run_cli_unknown_lane_fails_loud(monkeypatch):
    # WHY: 모르는 레인을 조용히 시장 레인으로 계획하면 하지도 않을 작업 21개가 기대에 실려
    #      매 런 MISSED 를 연다 — 원장이 관대해지는 쪽이 아니라 시끄러운 쪽으로 틀려야 한다.
    monkeypatch.setenv("OPS_PIPELINE_TYPE", "minute-bars")
    with pytest.raises(SystemExit, match="minute-bars"):
        entry.plan_run_cli(object())


def test_plan_run_cli_news_lane_requires_news_arn(monkeypatch):
    # WHY: 뉴스 레인이 시장 ARN 으로 폴백하면 뉴스 기대를 걸고 **시장 SFN 을 기동**한다 —
    #      기대와 실행이 어긋난 런이 원장에 남는다. ARN 부재는 시작 전에 fail-loud 다.
    monkeypatch.setenv("OPS_PIPELINE_TYPE", "news")
    monkeypatch.delenv("OPS_NEWS_STATE_MACHINE_ARN", raising=False)
    monkeypatch.setenv("OPS_STATE_MACHINE_ARN", _ARN)   # 시장 ARN 이 있어도 폴백하면 안 된다
    with pytest.raises(SystemExit, match="OPS_NEWS_STATE_MACHINE_ARN"):
        entry.plan_run_cli(object())


def test_plan_run_cli_disclosure_lane_requires_its_own_arn(monkeypatch):
    # WHY(ALPHA-721): 뉴스 레인과 같은 이유다 — 다른 레인 ARN 으로 폴백하면 공시 기대를 걸고
    #      **남의 SFN 을 기동**해 기대와 실행이 어긋난 런이 원장에 남는다. 레인이 셋이 되면서
    #      분기가 표로 바뀌었으므로, 표에 있으나 env 가 빈 경로를 여기서 고정한다.
    monkeypatch.setenv("OPS_PIPELINE_TYPE", "disclosure")
    monkeypatch.delenv("OPS_DISCLOSURE_STATE_MACHINE_ARN", raising=False)
    monkeypatch.setenv("OPS_STATE_MACHINE_ARN", _ARN)      # 있어도 폴백하면 안 된다
    monkeypatch.setenv("OPS_NEWS_STATE_MACHINE_ARN", _ARN)
    with pytest.raises(SystemExit, match="OPS_DISCLOSURE_STATE_MACHINE_ARN"):
        entry.plan_run_cli(object())


def test_exactly_one_ledger_owns_disclosure(monkeypatch):
    # WHY(ALPHA-875 → 987): 불변식은 그대로다 — **정확히 한 원장만 공시를 소유한다.**
    #      875 는 1분 원장이 소유하는 상태를 잠갔고, 987 이 저녁 배치로 되돌리며 방향만
    #      뒤집혔다: 이제 ops 원장(SFN 슬롯)이 소유하고 1분 레인은 비어야 한다.
    #
    #      둘 다 소유하면 이중 수집(두 레인이 같은 창을 각자 긁어 DART 일 한도 "020"),
    #      둘 다 안 소유하면 724 가 막으려던 조용한 전건 결손이다. 그래서 양쪽을 함께
    #      단언한다 — 카탈로그(코드)와 terraform 토글이 **한 커밋 안에서** 일치해야
    #      컷오버가 반쪽으로 착지할 수 없다.
    # ops 원장: 공시 레인 4작업이 있다(소유자).
    assert {e.task_key for e in catalog.entries(catalog.DISCLOSURE_PIPELINE_TYPE)} == {
        "DISCLOSURE_COLLECTION_DART", "NORMALIZE_DISCLOSURE",
        "NORMALIZE_DISCLOSURE_SEGMENT", "LOAD_DISCLOSURE",
    }
    assert not [e for e in catalog.entries(catalog.PIPELINE_TYPE)
                if "DISCLOSURE" in e.task_key]
    # 1분 원장: 어휘·상수는 남는다(875 가 박은 상수 — 롤백 경로). 소유 여부는 이게 아니라
    # terraform 토글이 정한다.
    assert minute_states.DATASET_DISCLOSURE_MINUTE in minute_states.MINUTE_DATASETS
    allowed = minute_states.SOURCE_GROUPS_BY_DATASET[minute_states.DATASET_DISCLOSURE_MINUTE]
    assert allowed == frozenset({"dart"})
    # ⚠️ 실제 소유 스위치는 terraform 토글이다(875 리뷰 실증: 상수만 봐선 레인을 꺼도
    # 아무 테스트도 안 깨진다). dev 가 **비워야**(미편입) 1분 레인이 공시 세션을 계획하지
    # 않는다 — 카탈로그 4엔트리와 이 값이 어긋나면 이중 수집 또는 전건 결손이다.
    # 모듈 기본값은 "dart"(레거시)로 남아 있어 **dev override 를 본다** — 875 는 기본값을
    # 스위치로 썼지만 987 은 envs/dev/main.tf 가 명시 override 로 비운다.
    import re
    root = next((p for p in Path(__file__).resolve().parents
                 if (p / "infra/terraform/envs/dev/main.tf").exists()), None)
    if root is None:
        pytest.skip("envs/dev/main.tf 를 찾을 수 없음 — 저장소 체크아웃에서만 도는 계약 검사")
    dev_tf = (root / "infra/terraform/envs/dev/main.tf").read_text(encoding="utf-8")
    # 주석을 걷고 매칭한다 — 안 걷으면 두 인자를 주석 처리(배선 해제의 가장 흔한 형태)해도
    # 주석 속 문자열이 계속 매칭돼 이 단언이 통과한다(test_ops_catalog._strip_hcl_comments
    # 와 같은 규율). 착지값 검사라 값 안에 //·# 가 없어 줄 선두 판정으로 충분하다.
    dev_tf = re.sub(r"/\*.*?\*/", "", "\n".join(
        ln for ln in dev_tf.splitlines() if not ln.lstrip().startswith(("#", "//"))),
        flags=re.S)  # re.S 없으면 여러 줄 /* … */ 이 안 걷혀 주석 속 대입문이 매칭된다
    toggle = re.search(
        r'^\s*minute_session_disclosure_source_group\s*=\s*"([^"]*)"', dev_tf, re.M)
    assert toggle, ("dev 가 minute_session_disclosure_source_group 를 명시하지 않는다 — "
                    "모듈 기본값(dart)이 적용돼 1분 레인이 공시를 다시 소유한다(이중 수집)")
    assert toggle.group(1) == "", (
        f"1분 레인 토글이 비어 있지 않다: {toggle.group(1)!r} — 카탈로그가 공시를 소유하는 "
        "지금 이 값이 차 있으면 두 레인이 같은 창을 긁는다")
    # 스케줄도 함께 — ENABLED 가 아니면 카탈로그 기대(슬롯)가 영구 MISSED 다.
    sched = re.search(r'^\s*disclosure_schedule_state\s*=\s*"([^"]*)"', dev_tf, re.M)
    assert sched and sched.group(1) == "ENABLED", (
        "disclosure_schedule_state 가 ENABLED 가 아니다 — 카탈로그 4엔트리가 돌지 않는 "
        "슬롯을 기대한다(매 거래일 전건 MISSED)")
    # ARN 표: 배치 레인이 실제 기동 경로다.
    assert catalog.DISCLOSURE_PIPELINE_TYPE in entry._LANE_STATE_MACHINE_ARN_ENV


def test_due_slots_disclosure_lane_follows_its_own_env(monkeypatch):
    # WHY(ALPHA-721): 레인마다 슬롯 집합이 다르므로 env 도 레인마다여야 한다. 뉴스 env 를
    #      공유하면 공시 슬롯이 뉴스 시각으로 잡혀 **존재하지 않는 런**을
    #      매 주기 PLANNER_MISSING 으로 연다. 미주입 레인은 슬롯 0개여야 한다 — 스케줄이 아직
    #      Planner 를 안 타는 배포(이 PR 시점)에서 거짓 결측을 만들지 않는 안전 기본값이다.
    #      ⚠️ 아래 뉴스 값은 **합성 픽스처**다(운영은 00:10·08:10) — 이 테스트가 가리는 축은
    #      "레인이 남의 env 를 안 본다"이지 뉴스 슬롯의 실제 값이 아니다.
    monkeypatch.setenv("OPS_NEWS_SCHED_HHMM", "15:00,15:30,23:50")
    monkeypatch.delenv("OPS_DISCLOSURE_SCHED_HHMM", raising=False)
    due = entry._due_slots(datetime(2026, 7, 24, 16, 30, tzinfo=planner_mod.KST))
    assert all(not key.startswith("disclosure:") for key, _ in due)

    monkeypatch.setenv("OPS_DISCLOSURE_SCHED_HHMM", "09:00,10:00,16:00,17:00")
    due = entry._due_slots(datetime(2026, 7, 24, 16, 20, tzinfo=planner_mod.KST))
    assert [key for key, _ in due if key.startswith("disclosure:")] == [
        "disclosure:2026-07-24T09:00", "disclosure:2026-07-24T10:00",
        "disclosure:2026-07-24T16:00",   # 17:00 은 아직 예정 전이라 빠진다
    ]
    # grace(30분)는 슬롯별이다 — 16:20 기준 16:00 은 아직 유예 중이고 09:00·10:00 은 한참 지났다.
    # 시간당 슬롯이라 유예가 간격보다 짧다: 한 슬롯의 결측 판정이 다음 슬롯 예정 전에 열린다.
    grace = {key: g for key, g in due if key.startswith("disclosure:")}
    assert grace["disclosure:2026-07-24T09:00"] is True
    assert grace["disclosure:2026-07-24T16:00"] is False


def test_malformed_disclosure_sched_env_fails_loud(monkeypatch):
    # WHY(ALPHA-721): 새 env 도 뉴스와 같은 fail-loud 여야 한다. 레인마다 복사하지 않고
    #      `_lane_sched_hhmms(env_name)` 하나를 쓰는 이유가 이것이다 — 복제하면 한쪽만
    #      관대해지는 순간 그 레인의 PLANNER_MISSING 탐지가 조용히 사라진다.
    monkeypatch.setenv("OPS_DISCLOSURE_SCHED_HHMM", "09:00,10-00")
    with pytest.raises(SystemExit, match="OPS_DISCLOSURE_SCHED_HHMM"):
        entry._lane_sched_hhmms("OPS_DISCLOSURE_SCHED_HHMM")


def test_due_slots_weekend_is_per_lane(monkeypatch):
    # WHY(ALPHA-874): 뉴스 크론만 주 7일이고 시장·공시는 MON-FRI 다. 주말 건너뛰기가 레인
    #      무관 상수면 어느 쪽이든 반드시 틀린다 — 상수 "건너뛴다"(종전)면 뉴스 주말 런의
    #      PLANNER_MISSING 탐지가 0 이 되고(조용한 축소), 상수 "안 건너뛴다"면 평일 전용
    #      레인이 매 토·일 거짓 결측을 연다. **같은 순간에 두 레인이 다른 날을 물어야** 그
    #      플래그가 실제로 레인별로 읽히고 있다는 뜻이다.
    monkeypatch.setenv("OPS_NEWS_SCHED_HHMM", "15:00,15:30,23:50")
    monkeypatch.setenv("OPS_NEWS_SCHED_WEEKEND", "true")
    monkeypatch.setenv("OPS_DISCLOSURE_SCHED_HHMM", "09:00,10:00")
    monkeypatch.delenv("OPS_DISCLOSURE_SCHED_WEEKEND", raising=False)
    monkeypatch.delenv("OPS_DAILY_SCHED_WEEKEND", raising=False)

    sat = datetime(2026, 8, 1, 15, 45, tzinfo=planner_mod.KST)
    assert sat.weekday() == 5, "픽스처가 토요일이 아니면 이 테스트는 아무것도 안 가린다"
    due = entry._due_slots(sat)

    # 뉴스는 그 토요일 **자신의** 지난 슬롯을 문다(23:50 은 아직 예정 전이라 빠진다).
    assert [k for k, _ in due if k.startswith("news:")] == [
        "news:2026-08-01T15:00", "news:2026-08-01T15:30",
    ]
    # 평일 전용 레인은 토요일 슬롯을 지어내지 않고 직전 금요일(07-31)로 떨어진다 — 오탐 0.
    assert [k for k, _ in due if k.startswith("disclosure:")] == [
        "disclosure:2026-07-31T09:00", "disclosure:2026-07-31T10:00",
    ]
    assert [k for k, _ in due if k.startswith(f"{PIPELINE_TYPE}:")] == [
        f"{PIPELINE_TYPE}:2026-07-31T15:40",
    ]
    # grace(30분)는 주말에도 슬롯별로 계산된다 — 요일 축이 붙었다고 판정이 무뎌지면 안 된다.
    grace = dict(due)
    assert grace["news:2026-08-01T15:00"] is True
    assert grace["news:2026-08-01T15:30"] is False


def test_malformed_weekend_env_fails_loud(monkeypatch):
    # WHY(ALPHA-874): 값이 있는데 못 읽었을 때 조용히 평일 전용으로 떨어지면 그 레인 주말 런의
    #      결측 탐지가 소리 없이 사라진다 — `_lane_sched_hhmms` 와 정확히 같은 축이다(Rule 12).
    #      terraform 이 `tostring(bool)` 로 넣으므로 우리 표기는 소문자 둘뿐이다.
    monkeypatch.setenv("OPS_NEWS_SCHED_WEEKEND", "True")
    with pytest.raises(SystemExit, match="OPS_NEWS_SCHED_WEEKEND"):
        entry._lane_sched_weekend("OPS_NEWS_SCHED_WEEKEND")
    # 미주입은 손상이 아니라 **종전 동작**(평일 전용)이다 — 플래그를 아직 안 넣은 배포가
    # 여기서 죽으면 안 된다. HH:MM 의 "미주입 = 판정 없음" 과 같은 결.
    monkeypatch.delenv("OPS_NEWS_SCHED_WEEKEND", raising=False)
    assert entry._lane_sched_weekend("OPS_NEWS_SCHED_WEEKEND") is False
    monkeypatch.setenv("OPS_NEWS_SCHED_WEEKEND", "false")
    assert entry._lane_sched_weekend("OPS_NEWS_SCHED_WEEKEND") is False
    monkeypatch.setenv("OPS_NEWS_SCHED_WEEKEND", "true")
    assert entry._lane_sched_weekend("OPS_NEWS_SCHED_WEEKEND") is True


def _ledger_tf() -> str:
    """주석을 걷어낸 ops_ledger.tf — 배선을 뗄 때 가장 흔한 형태가 삭제가 아니라 주석 처리라,
    원문을 훑으면 주석에 남은 옛 줄로 단언이 초록을 유지한다(`_strip_hcl_comments` 규율)."""
    return test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "ops_ledger.tf").read_text(encoding="utf-8"))


def test_news_cron_runs_every_day_of_week():
    # WHY(ALPHA-874): 수집 창이 `[어제, 오늘]` 2일이라 어떤 날은 그날이나 다음 날에 런이
    #      있어야 덮인다. MON-FRI 면 일요일은 월요일 런이 덮지만 **토요일은 토·일 모두 런이
    #      없어 매주 통째로 빈다**(2026-08-01 raw 파티션 0 실증). 요일을 되돌리는 변경은
    #      코드 어디에서도 안 깨지고 조용히 그 구멍을 되살리므로 여기서 붙든다.
    #      ⚠️ 이 화이트리스트는 커버리지가 아니라 **정책**을 고정한다 — `MON-SAT` 도 거부한다.
    #      토요일만 더하는 게 사실 최소 수정이지만 원장의 주말 플래그가 이진이라 표현할 수 없고
    #      (variables.tf 주석), 그 표기는 terraform plan 에서 죽는다. 여기서도 같이 막아 둔다.
    #      ⚠️ **일(day-of-month)까지 함께 든다.** 요일만 보면 `cron(0 15 1 * ? *)`(매달 1일만)
    #      이 통과하는데, 그건 요일 필드가 `?` 라 주말 플래그가 true 로 잡혀 **매달 28일가량
    #      × 슬롯 수**의 PLANNER_MISSING 을 연다 — 뜰 런이 없어 영영 안 닫힌다.
    # ^\s*"키" = 로 앵커한다 — `_strip_hcl_comments` 는 줄 **선두** 주석만 걷으므로
    # (값 안의 `s3://` 를 자르지 않으려는 의도적 규율), 앵커 없이는 줄 뒤에 달린 인라인
    # 주석 속 가짜 cron 이 살아 있는 배선으로 세어진다(edge-review 2R).
    dom_dow = set(re.findall(r'^\s*"[^"]+"\s*=\s*"cron\(\S+ \S+ (\S+) \S+ (\S+) ',
                             _news_cron_block(), re.M))
    assert dom_dow and dom_dow <= {("*", "?"), ("?", "*")}, (
        f"뉴스 크론의 (일, 요일)이 {sorted(dom_dow)} — 매일 도는 형태가 아니다. 원장이 표현할 수 "
        "있는 값은 MON-FRI 와 주 7일 둘뿐이고, 둘 중 하나는 `?` 다(AWS 가 `*` 를 두 필드에 "
        "동시에 쓰는 것을 금지한다)")


def _news_cron_block() -> str:
    """주석을 걷어낸 `news_schedule_expressions` 기본값 본문(`_ledger_tf` 와 같은 규율)."""
    tf = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "variables.tf").read_text(encoding="utf-8"))
    block = re.search(r'variable\s+"news_schedule_expressions"\s*\{(.*?)^\}', tf, re.M | re.S)
    assert block, "news_schedule_expressions 를 못 찾았다 — 파서가 낡았다"
    return block.group(1)


def test_premarket_news_slot_depends_on_the_vendor_calendar_window():
    # WHY(ALPHA-893): 08:10 슬롯은 **09:00 KST 이전**이라 창 날짜가 UTC 로 뽑히면 그 런의 창이
    #      [D-2, D-1] 이 되어 그날 기사를 한 건도 안 가져온다 — 에러 없이 조용히 헛돈다
    #      (ALPHA-883 이 창을 벤더 달력으로 바꿔 성립시킨 슬롯이다). 두 사실이 **다른 층에**
    #      있어(크론은 terraform, 달력은 run.py) 한쪽만 되돌려도 아무것도 안 깨진다 —
    #      그 결합을 여기서 붙든다. 시각 리터럴을 못박지 않는 이유는 08:05·08:20 도 똑같이
    #      정당하기 때문이다. 지켜야 할 것은 "09:00 이전 슬롯이 있다면 그 창이 KST 여야 한다"다.
    from data_pipeline import run as run_mod

    slots = [(int(h), int(m)) for m, h in
             re.findall(r'^\s*"[^"]+"\s*=\s*"cron\((\d+) (\d+) ', _news_cron_block(), re.M)]
    assert slots, "뉴스 크론에서 슬롯 시각을 못 뽑았다 — 파서가 낡았다"

    early = [(h, m) for h, m in slots if (h, m) < (9, 0)]
    assert early, (
        "09:00 KST 이전 뉴스 슬롯이 사라졌다. 밤새 유입분을 장 시작 전에 배치 코퍼스로 확정한다는 "
        "ALPHA-893 의 결정이 배선에서 빠진 것이다 — 의도한 변경이면 이 단언과 variables.tf 주석을 "
        "함께 고쳐라")
    for hour, minute in early:
        at = datetime(2026, 7, 3, hour, minute, tzinfo=run_mod.KST)
        window = run_mod.default_window(
            at.astimezone(run_mod.window_calendar_tz("ingest-raw", "bigkinds")))
        assert window[1] == "2026-07-03", (
            f"{hour:02d}:{minute:02d} 슬롯의 창이 {window} — 그날(07-03)이 끝 날짜가 아니다. "
            "창 날짜가 벤더 달력(KST)이 아니라 프로세스 시계(UTC)로 뽑히면 이 슬롯은 "
            "8시간 전 런과 같은 창을 다시 긁고 그날 기사를 0건 가져온다(ALPHA-883)")


def _module_number(var_name: str) -> int:
    """모듈 변수의 **실제 배포값**(envs/dev override 가 있으면 그것, 없으면 모듈 default).

    ⚠️ 층이 셋이다 — 모듈 default · envs/dev 의 `module "data_pipeline"` 인자 · (런타임 env).
    모듈 default 만 읽으면 envs/dev 가 값을 덮어써도 가드가 못 본다(edge-review 3R). 여기서
    앞의 둘을 합쳐 "지금 dev 에 서는 값"을 돌려준다. 셋째(런타임 env)는 이 레인에 경로가 없다.
    """
    tf = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "variables.tf").read_text(encoding="utf-8"))
    block = re.search(rf'variable\s+"{var_name}"\s*\{{(.*?)^\}}', tf, re.M | re.S)
    assert block, f"{var_name} 를 모듈에서 못 찾았다 — 파서가 낡았다"
    value = int(re.search(r"^\s*default\s*=\s*(\d+)", block.group(1), re.M).group(1))

    env_tf = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE.parents[1] / "envs/dev/main.tf").read_text(encoding="utf-8"))
    override = re.search(rf"^\s*{var_name}\s*=\s*(\d+)", env_tf, re.M)
    return int(override.group(1)) if override else value


def test_day_close_slot_runs_after_the_day_it_closes_has_ended():
    # WHY(ALPHA-905): `day-close` 는 **하루를 닫는** 슬롯이다. 자정 **전**에 돌면 창의 '어제'는
    #      이미 앞 런이 닫은 날이고 '오늘'은 아직 안 끝난 날이라 — 닫는 대상이 없다. 그러면서
    #      비용은 최대다: 창이 `[어제, 오늘]` 2일인데 증분 커서가 없어 매 런이 창 전체를 다시
    #      긁으므로, 23:50 은 꽉 찬 두 날(~124p)을 긁고 00:10 은 꽉 찬 하루 + 10분(~62p)을 긁는다.
    #      **시각 리터럴(00:10)을 못박지 않는다** — 00:05·01:00 도 똑같이 정당하다. 지켜야 할
    #      것은 "day-close 가 그 레인의 **가장 이른** 슬롯이다"(= 하루가 시작하자마자 앞 날을
    #      닫는다)이고, 23:50 로 되돌리면 그게 깨진다. 이 관계가 없으면 슬롯을 되돌려도 요일·
    #      타임아웃·assemble 가드가 전부 초록이라 ~124p 재수집이 조용히 재발한다(edge-review).
    slots = {key: (int(h), int(m)) for key, m, h in
             re.findall(r'^\s*"([^"]+)"\s*=\s*"cron\((\d+) (\d+) ',
                        _news_cron_block(), re.M)}
    assert "day-close" in slots, f"day-close 슬롯이 사라졌다 — 현재 키: {sorted(slots)}"

    others = {k: v for k, v in slots.items() if k != "day-close"}
    assert others, "비교 대상 슬롯이 없다 — 레인에 슬롯이 하나뿐이면 이 계약은 무의미하다"
    assert slots["day-close"] < min(others.values()), (
        f"day-close 가 {slots['day-close']} 로 {min(others.values())} 보다 늦다. 자정 전에 돌면 "
        "닫을 하루가 아직 안 끝났고(앞 런이 닫은 날을 다시 긁을 뿐) 창의 두 날이 모두 꽉 차 "
        "긁는 양이 두 배가 된다 — day-close 는 그 레인의 가장 이른 슬롯이어야 한다")

    # ⚠️ "가장 이르다"만으로는 약하다(edge-review 검증 라운드) — day-close 를 08:09 로 두면
    #    08:10 보다 이르니 위 단언을 통과하는데, 두 배치가 1분 간격으로 시작해 같은 벤더를
    #    동시에 치고 '오늘 몫이 10분뿐'이라는 절감도 사라진다. `retry_policy` 0 의 유지 근거가
    #    기대고 있는 성질(**실행이 서로 겹치지 않는다**)을 그대로 단언한다 — 인접 슬롯 간격이
    #    한 실행의 상한보다 커야 한다. 시각 리터럴은 여전히 안 박는다(00:05·01:00 도 정당).
    timeout_sec = _module_number("news_state_machine_timeout_seconds")
    ordered = sorted(h * 3600 + m * 60 for h, m in slots.values())
    gaps = [b - a for a, b in zip(ordered, ordered[1:])] + [86400 - ordered[-1] + ordered[0]]
    assert min(gaps) > timeout_sec, (
        f"인접 슬롯 최소 간격 {min(gaps)}초 ≤ 실행 상한 {timeout_sec}초. 앞 런이 상한까지 끌면 "
        "다음 런과 겹쳐 서로 다른 run 의 AssembleEvents 가 동시에 돌고 prior-count·"
        "lifecycle_stage read-before-write 레이스가 되살아난다(재시도 0 의 유지 근거가 깨진다)")


def test_assemble_window_covers_what_the_collection_window_collected():
    # WHY(ALPHA-905): day-close 슬롯이 **00:10** 이라 assemble 은 언제나 다음 날짜에 돈다 —
    #      닫으려는 날(어제)을 읽으려면 assemble 창이 최소 하루는 소급해야 한다. 종전엔 이게
    #      23:50 런의 자정 crossing **보정**이라 없어도 대개 맞았지만, 이제는 **매 런의 전제**다.
    #      N=0 이면 창이 거의 빈 오늘 하루뿐이라 `read=0` 으로 **성공한다**(조용한 헛돎, Rule 12).
    #      literal 1 을 못박지 않는 이유: 진짜 계약은 "**수집이 담은 것을 조립이 덮는가**"이고,
    #      수집 창은 `DEFAULT_LOOKBACK_DAYS` 가 정한다. 그 상수가 2로 늘면 assemble 도 따라
    #      늘어야 하는데, 두 값이 **다른 층**(python 상수 vs terraform 변수)에 있어 한쪽만
    #      움직여도 아무것도 안 깨진다 — 그 결합을 여기서 붙든다.
    from data_pipeline import run as run_mod

    tf = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "variables.tf").read_text(encoding="utf-8"))
    block = re.search(r'variable\s+"assemble_window_days"\s*\{(.*?)^\}', tf, re.M | re.S)
    assert block, "assemble_window_days 를 못 찾았다 — 파서가 낡았다"
    assemble_days = _module_number("assemble_window_days")   # envs/dev override 반영
    # ⚠️ 기본값만 보면 사각이 남는다(edge-review) — 하한을 0 으로 되돌려도 기본값 1 이면 이
    #    테스트가 통과하고, 그 뒤 env 가 0 을 주입하면 plan 도 통과해 매일 read=0 이 된다.
    #    **허용 범위**(validation 하한)를 함께 본다.
    floor = int(re.search(r"^\s*condition\s*=.*?var\.assemble_window_days\s*>=\s*(\d+)",
                          block.group(1), re.M).group(1))

    for label, value in (("실효값", assemble_days), ("validation 하한", floor)):
        assert value >= run_mod.DEFAULT_LOOKBACK_DAYS, (
            f"assemble 창 {label} {value}일 < 수집 창 소급 {run_mod.DEFAULT_LOOKBACK_DAYS}일. "
            "수집이 담은 날짜를 조립이 못 읽는다 — 00:10 런은 닫으려던 어제를 통째로 건너뛰고 "
            "read=0 으로 성공한다(에러 없음)")

    # ⚠️ **변수 선언이 맞아도 배선이 끊기면 같은 결과다**(edge-review 2R). `--window-days` 가
    #    SFN 명령에서 빠지면 run.py 기본값은 "오늘 하루"라 00:10 런이 D+1 만 읽는다. 리터럴로
    #    박아도 위 두 값이 아무 의미가 없어진다 — 그래서 **끝단(실제 명령)까지** 센다.
    sfn = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "news_pipeline.tf").read_text(encoding="utf-8"))
    command = re.search(r"NewsAssembleEvents\s*=\s*merge\(.*?\"Command\.\$\"\s*=\s*\"([^\"]*)\"",
                        sfn, re.S)
    assert command, "NewsAssembleEvents 의 Command 를 못 찾았다 — 파서가 낡았다"
    assert "--window-days" in command.group(1), (
        f"NewsAssembleEvents 명령에 --window-days 가 없다: {command.group(1)}. "
        "미주입이면 assemble 창이 '오늘 하루'라 00:10 런이 닫으려던 어제를 안 읽는다")
    assert "${var.assemble_window_days}" in command.group(1), (
        f"NewsAssembleEvents 가 --window-days 를 변수가 아닌 값으로 넘긴다: {command.group(1)}. "
        "리터럴이면 위 default·validation 이 아무것도 강제하지 못한다")


def test_load_documents_receives_the_normalize_run_manifest_scope():
    # WHY(ALPHA-1031): loader 구현이 manifest를 지원해도 실제 SFN 명령이 run 계보를 넘기지 않으면
    #      정상 배치는 시작 전에 실패하거나, 범위 가드를 걷는 순간 canonical 풀스캔으로 퇴행한다.
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8"))
    assert (
        "States.Array('load-documents', '--run-id', $.run_id, "
        "'--input-run-id', $.run_id)" in sm
    ), "LoadDocuments가 현재 NormalizeNews run_id를 manifest 범위로 전달하지 않는다"


def test_premarket_news_slot_plus_timeout_lands_before_the_minute_lane_opens():
    # WHY(ALPHA-893): 뉴스 SFN 타임아웃을 묶던 불변식이 **바뀌었다**. 옛 상한 25분의 근거는
    #      "인접 슬롯 간격 30분(15:00·15:30)보다 짧아야 실행이 안 겹친다" 였는데 그 두 슬롯이
    #      내려가 최소 간격이 8시간(00:10→08:10)이 됐다. 그 자리를 대신하는 상한이 이것이다 —
    #      **09:00 전 슬롯은 자기 타임아웃을 다 써도 09:00 을 넘지 않아야 한다.** 넘으면 배치가
    #      1분 뉴스 워커와 같은 BigKinds 를 같은 IP 로 동시에 쳐서(minute/bigkinds_feed.py 는
    #      요청 형상을 배치와 공유한다) pacing 이 합산되고, 차단(ALPHA-645)은 재시도가 **연장**
    #      하는 종류라 그날 두 레인이 함께 죽는다. 두 값이 **다른 변수**(크론 vs 타임아웃)에
    #      있어 한쪽만 늘려도 아무것도 안 깨진다 — 그 결합을 여기서 붙든다.
    tf = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "variables.tf").read_text(encoding="utf-8"))
    block = re.search(
        r'variable\s+"news_state_machine_timeout_seconds"\s*\{(.*?)^\}', tf, re.M | re.S)
    assert block, "news_state_machine_timeout_seconds 를 못 찾았다 — 파서가 낡았다"
    timeout_sec = _module_number("news_state_machine_timeout_seconds")  # envs/dev override 반영

    # ⚠️ 분으로 내림하면 3001초(50분 1초)가 "50분"이 되어 09:00:01 착지를 통과시킨다
    #    (edge-review 3R) — **초 단위로** 센다.
    slots = [(int(h), int(m)) for m, h in
             re.findall(r'^\s*"[^"]+"\s*=\s*"cron\((\d+) (\d+) ', _news_cron_block(), re.M)]
    for hour, minute in [s for s in slots if s < (9, 0)]:
        end_sec = (hour * 60 + minute) * 60 + timeout_sec
        assert end_sec <= 9 * 3600, (
            f"{hour:02d}:{minute:02d} 슬롯 + 타임아웃 {timeout_sec}초 = "
            f"{end_sec // 3600:02d}:{end_sec // 60 % 60:02d}:{end_sec % 60:02d} 로 09:00 을 넘는다. "
            "배치가 1분 뉴스 레인과 같은 벤더를 동시에 치는 창이 열린다 — "
            "슬롯을 앞당기거나 타임아웃을 줄여라")


def test_every_sched_hhmm_env_has_a_weekend_sibling():
    # WHY(ALPHA-874): 슬롯 시각만 주입하고 요일 플래그를 빠뜨리면 그 레인은 미주입 기본값인
    #      **평일 전용**으로 조용히 굳는다 — 크론이 주말에도 도는 레인이면 주말 결측 탐지가
    #      0 이 되고 아무도 모른다. 레인이 늘 때 한쪽만 배선하는 것을 여기서 막는다.
    tf = _ledger_tf()
    lanes = set(re.findall(r"\bOPS_(\w+)_SCHED_HHMM\b", tf))
    assert lanes, "OPS_*_SCHED_HHMM 주입을 못 찾았다 — 파서가 낡았다"
    missing = {n for n in lanes if not re.search(rf"\bOPS_{n}_SCHED_WEEKEND\s*=", tf)}
    assert not missing, f"요일 플래그가 없는 레인: {sorted(missing)} — 주말 판정이 조용히 꺼진다"


def test_tf_weekend_flag_direction_and_per_lane_wiring():
    """WHY(ALPHA-874): 플래그가 **존재하는가**만 보면 그 값이 무엇인지는 아무도 안 잡는다.

    실제로 뚫린 구멍이다 — 리뷰 변이에서 `news_schedule_weekend` 를 `"false"` 상수로 바꾸거나
    요일 판정을 반전시켜도 전 스위트가 초록이었다. 그 배포는 이 티켓이 고치려는 상태(주말 결측
    탐지 0) 또는 그 반대(평일 전용 레인에 매 토·일 닫히지 않는 오탐 16건)를 그대로 만든다.
    파이썬 쪽은 변이가 전부 잡히는데 terraform 쪽만 비어 있던, Rule 12 의 그 실패 방향이다.

    그래서 값을 만드는 두 조각을 따로 든다: **방향표**(어느 요일 표기가 주말인가)와
    **레인별 배선**(각 레인이 표를 거쳐 자기 cron 을 읽는가). 표를 뒤집으면 앞이, 상수로
    박거나 남의 변수를 읽으면 뒤가 빨개진다.
    """
    tf = _ledger_tf()
    table = re.search(r"_day_weekend\s*=\s*\{(.*?)\n  \}", tf, re.S)
    assert table, "_day_weekend 표를 못 찾았다 — 파서가 낡았다"
    direction = dict(re.findall(r'"([^"]+)"\s*=\s*(true|false)', table.group(1)))
    # 표 **전체**를 든다 — 일부 행만 들면 안 든 행이 뒤집혀도 초록이다. 실제로 그랬다: `"?"` 만
    # 잠갔더니 크론을 `? * * *`(테스트가 허용하는 합법 편집)로 쓰고 `"*"` 를 false 로 뒤집는
    # 조합이 전 스위트 초록인 채 주말 결측 탐지를 0 으로 만들었다. 키 집합이 느는 것도 여기서
    # 걸려야 한다 — 표에 값을 더하는 것은 이진 플래그로 표현 가능한 범위를 넓히는 판단이다.
    assert direction == {
        "?|MON-FRI": "false", "*|?": "true", "?|*": "true",
    }, direction

    # 레인별 배선: 각 local 이 표를 거치고 **자기** 스케줄 변수를 읽는가.
    segments = re.split(r"\n  (?=\w+_schedule_weekend\s*=)", tf)
    wired = {m.group(1): seg for seg in segments
             if (m := re.match(r"\s*(\w+)_schedule_weekend\s*=", seg))}
    expected_source = {
        "daily": "var.schedule_expression",
        "news": "var.news_schedule_expressions",
        "disclosure": "var.disclosure_schedule_expressions",
        "investor_intraday": "var.investor_intraday_schedule_expressions",
    }
    assert set(wired) == set(expected_source), f"레인 구성이 바뀌었다: {sorted(wired)}"
    for lane, source in expected_source.items():
        body = wired[lane]
        assert "local._day_weekend[" in body, f"{lane}: 표를 안 거친다(상수로 박혔나?)"
        assert source in body, f"{lane}: {source} 가 아니라 남의 cron 을 읽는다"


def test_plan_run_cli_snapshots_only_three_etf_collectors(monkeypatch):
    """WHY: 세 수집기의 분모는 실행 결과가 아니라 공통 etf_map 정본이어야 누락을 잡는다.

    Planner CLI에서 provider를 빼먹으면 planner 모듈의 훅이 있어도 운영 snapshot은 0건이고,
    반대로 모든 작업에 주면 아직 entity grain이 다른 작업까지 잘못된 3종 분모로 판정된다.
    """
    captured = {}
    fake_ledger = object()
    monkeypatch.setenv("OPS_PIPELINE_TYPE", PIPELINE_TYPE)
    monkeypatch.setenv("OPS_STATE_MACHINE_ARN", _ARN)
    monkeypatch.setattr(entry, "ledger_from_settings", lambda settings: fake_ledger)

    def fake_plan_run(ledger, **kwargs):
        captured.update(kwargs)
        assert ledger is fake_ledger
        return SimpleNamespace(
            pipeline_run_id="run-1",
            launch_status=states.LAUNCH_LAUNCHED,
            created=True,
            trading_day=True,
        )

    monkeypatch.setattr(entry.planner, "plan_run", fake_plan_run)
    settings = SimpleNamespace(
        krx_etf=SimpleNamespace(
            source=SimpleNamespace(etf_map={"396500": "KR7396500001",
                                            "069500": "KR7069500007"})
        )
    )

    assert entry.plan_run_cli(settings) == 0
    provider = captured["universe_provider"]
    targets = {
        "NAV_COLLECTION_KIS",
        "ETF_PROFILE_COLLECTION_KIS",
        "ETF_HOLDINGS_COLLECTION_KRX",
    }
    snapshotted = {
        item.task_key for item in catalog.entries(PIPELINE_TYPE)
        if provider(item.task_key) is not None
    }
    assert snapshotted == targets
    for task_key in targets:
        assert provider(task_key) == {
            "entity_kind": "ticker",
            "entity_ids": ["069500", "396500"],
        }


def test_snapshot_created_when_universe_provided():
    """expectation_snapshot 이 provider 로 생성되고 expected_task 에 연결된다(스펙 §6)."""
    db = FakeOpsDB()
    ledger = _ledger(db)

    def universe(task_key):
        if task_key == "PRICE_COLLECTION_KIS":
            return {"universe_version": "v1", "as_of_date": "2026-07-23",
                    "entity_ids": ["005930", "000660"]}
        return None

    result = plan_run(
        ledger, state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
        universe_provider=universe,
    )
    assert len(db.snapshots) == 1
    assert db.snapshots[0]["entity_ids"] == json.dumps(["005930", "000660"], ensure_ascii=False)
    # LEFT JOIN이어야 snapshot 없는 나머지 작업도 wrapper 계측 대상에서 사라지지 않는다.
    assert ledger.find_expected_task(
        run_id=result.pipeline_run_id, task_key="PRICE_COLLECTION_KIS"
    )["expected_count"] == 2
    assert ledger.find_expected_task(
        run_id=result.pipeline_run_id, task_key="NORMALIZE_PRICE"
    )["expected_count"] is None


def test_장중_수급_레인_임계가_슬롯_간격과_타임아웃_안에_있다():
    """WHY(ALPHA-769): 공시 레인이 같은 축을 테스트로 드는데(위 테스트) 장중 수급 레인은
    카탈로그 주석에서 **손으로 계산만** 했다 — 주석은 슬롯이나 타임아웃이 바뀌어도 안 깨진다.

    임계는 terraform 의 실제 값에서 뽑는다. 두 계약은 공시와 같은 이유로 같은 형태다:
      ① deadline + Reconciler 주기 < 최소 슬롯 간격 — 판정을 찍는 건 15분마다 도는
         Reconciler 라, 주기를 빼면 "한 슬롯의 결측이 다음 슬롯 예정 전에 드러난다"가
         산술적으로 거짓인데 통과한다.
      ② stalled **<** SFN 타임아웃(같으면 안 된다) — 판정이 `> threshold` 인데 SFN 이 정확히
         타임아웃에 실행을 죽이므로, 같게 두면 경과가 임계를 넘는 순간이 오지 않아 영원히
         발화하지 않는다.
    """
    slot_interval = test_ops_catalog.lane_slot_interval_seconds("investor_intraday")
    sfn_timeout = test_ops_catalog.lane_sfn_timeout_seconds("investor_intraday")
    reconcile_period = test_ops_catalog.reconcile_period_seconds()
    entries = catalog.entries(catalog.INVESTOR_INTRADAY_PIPELINE_TYPE)
    assert entries, "장중 수급 레인 엔트리가 0개다"
    for e in entries:
        assert e.deadline_offset_seconds + reconcile_period < slot_interval, e.task_key
        assert e.stalled_after_seconds < sfn_timeout, e.task_key


def test_장중_수급_레인은_manifest_전환_후_세_작업_모두_거래일만_기대한다():
    """WHY(ALPHA-769): `kr_trading_calendar` 세 갈래가 이 레인 설계의 핵심 판단인데 어느
    테스트도 안 들고 있었다 — 세 값 중 무엇을 뒤집어도 전 스위트가 초록이었다(edge-review).

    **공시 레인은 전 엔트리 False 를 못박는다(위 테스트). 여기가 갈리는 지점이라 명시한다**
    (Rule 7 — 규약이 충돌하면 고르고 이유를 남긴다). 기준은 레인이 아니라 **그 작업이
    공휴일에 실제로 일을 하는가**다. ALPHA-181 함정은 "실제로 돌아 값을 만든 실행이 SKIPPED
    뒤로 사라지는 것"이지 "평일 cron 이면 무조건 False"가 아니다.

    * 수집·정제 True — 장중 투자자 추정은 **비거래일에 존재 자체를 하지 않는다.** 어댑터가
      `skip_reason` 으로 돌아서고(0건), 정제는 `--input-run-id` 로 그 런의 raw 만 읽어 진짜
      0건이다. False 로 두면 `empty_allowed=False` 라 **공휴일마다 UNKNOWN 이 2건씩** 쌓여
      (실측: `derive_data_status` 가 0건·완료·empty_allowed=False → UNKNOWN) 진짜 결손과 섞인다.
    * 적재 True — 현재 normalize manifest만 읽으므로 공휴일의 빈 manifest에는 실일이 없다.
      과거 백로그는 암묵적 전량 스캔으로 회수하지 않는다(ALPHA-1036).
    """
    by_key = {e.task_key: e for e in catalog.entries(catalog.INVESTOR_INTRADAY_PIPELINE_TYPE)}
    assert set(by_key) == {
        "INVESTOR_INTRADAY_COLLECTION_KIS",
        "NORMALIZE_INVESTOR_INTRADAY",
        "LOAD_INVESTOR_INTRADAY",
    }, "레인 구성이 바뀌었다 — 달력 판단을 다시 하라"
    assert by_key["INVESTOR_INTRADAY_COLLECTION_KIS"].kr_trading_calendar is True
    assert by_key["NORMALIZE_INVESTOR_INTRADAY"].kr_trading_calendar is True
    assert by_key["LOAD_INVESTOR_INTRADAY"].kr_trading_calendar is True
    # 그 근거(manifest run 전달)가 terraform 에 그대로 있는지 함께 든다. 배선이 빠지면 CLI가
    # fail-loud 해야 하지만, 카탈로그 달력 판단의 근거도 함께 사라진다.
    # ⚠️ **주석을 먼저 걷는다.** 배선을 뗄 때 가장 흔한 형태가 삭제가 아니라 주석 처리인데,
    # 원문을 그대로 훑으면 창 인자를 붙이면서 옛 줄을 주석에 남긴 변경이 그대로 통과한다 —
    # 근거는 사라졌는데 단언만 초록이다(`test_ops_catalog._strip_hcl_comments` 와 같은 규율).
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8"))
    assert (
        "States.Array('load-investor-intraday', '--run-id', $.run_id, "
        "'--input-run-id', $.run_id)" in sm
    ), "정상 장중 적재는 현재 normalize manifest run_id를 받아야 한다"


def test_장중_수급_정제_부분실패는_manifest_적재_후_SFN을_실패로_마감한다():
    """WHY(ALPHA-1036): exit 2는 성공 winner가 있는 부분 실패다. 즉시 막으면 정상 종목도
    적재되지 않고, 마지막 성공으로 닫으면 실패 종목이 전체 실행 상태에서 숨는다."""
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "investor_intraday_pipeline.tf").read_text(
            encoding="utf-8"))

    check = re.search(
        r"InvestorIntradayNormalizeCheckResults\s*=\s*\{(?P<body>.*?)\n\s*\}", sm, re.S)
    assert check
    continue_checks = sm.split("investor_intraday_normalize_continue_checks = [", 1)[1].split(
        "investor_intraday_feature_success_checks = [", 1)[0]
    assert re.search(
        r'Variable\s*=\s*"\$\.normalize_results\[\$\{index\}\]\.exit_code".*?'
        r'IsPresent\s*=\s*true.*?NumericEquals\s*=\s*2', continue_checks, re.S
    ), "TaskFailed에는 exit_code가 없으므로 존재 확인 뒤 exit 2를 비교해야 한다"
    assert 'Next = "InvestorIntradayNotifyNormalizePartial"' in check.group("body")
    assert 'Default = "InvestorIntradayNotifyFailure"' in check.group("body")

    notify = sm.split("InvestorIntradayNotifyNormalizePartial = {", 1)[1].split(
        "InvestorIntradayFeatureParallel = {", 1)[0]
    assert re.search(r'Next\s*=\s*"InvestorIntradayFeatureParallel"', notify)
    assert re.search(
        r'Catch\s*=\s*\[\{.*?ErrorEquals\s*=\s*\["States\.ALL"\].*?'
        r'ResultPath\s*=\s*"\$\.normalize_partial_notification_error".*?'
        r'Next\s*=\s*"InvestorIntradayFeatureParallel"', notify, re.S,
    ), "부분 실패 알림 장애가 이미 확정된 winner 적재를 막으면 안 된다"
    final = sm.split("InvestorIntradayRawPartialCheck = {", 1)[1].split(
        "InvestorIntradaySucceeded = {", 1)[0]
    assert "local.investor_intraday_raw_success_checks" in final
    assert "local.investor_intraday_normalize_success_checks" in final
    assert 'Default = "InvestorIntradayFailed"' in final


def test_공시_정제_부분실패는_dual_manifest_적재_후_SFN을_실패로_마감한다():
    """WHY(ALPHA-1044): 두 producer의 exit 2는 각 성공 winner manifest를 확정했다. 즉시
    막으면 성공 범위를 못 싣고, 성공으로 닫으면 다른 행의 격리가 실행 상태에서 숨는다."""
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "disclosure_pipeline.tf").read_text(
            encoding="utf-8"))

    continue_checks = sm.split("disclosure_normalize_continue_checks = [", 1)[1].split(
        "disclosure_feature_success_checks = [", 1)[0]
    assert re.search(
        r'Variable\s*=\s*"\$\.normalize_results\[\$\{index\}\]\.exit_code".*?'
        r'IsPresent\s*=\s*true.*?NumericEquals\s*=\s*2', continue_checks, re.S
    ), "ECS TaskFailed와 producer exit 2를 구분해야 한다"
    gate = sm.split("DisclosureNormalizeCheckResults = {", 1)[1].split(
        "DisclosureNotifyNormalizePartial = {", 1)[0]
    assert 'Next = "DisclosureNotifyNormalizePartial"' in gate
    assert 'Default = "DisclosureNotifyFailure"' in gate
    notify = sm.split("DisclosureNotifyNormalizePartial = {", 1)[1].split(
        "DisclosureFeatureParallel = {", 1)[0]
    assert re.search(r'Next\s*=\s*"DisclosureFeatureParallel"', notify)
    assert "normalize_partial_notification_error" in notify
    final = sm.split("DisclosureRawPartialCheck = {", 1)[1].split(
        "DisclosureSucceeded = {", 1)[0]
    assert "local.disclosure_raw_success_checks" in final
    assert "local.disclosure_normalize_success_checks" in final
    assert 'Default = "DisclosureFailed"' in final


def test_가격_정제_부분실패는_manifest_적재_후_시장_SFN을_실패로_마감한다():
    """WHY(ALPHA-1038): NormalizePrice exit 2를 즉시 막으면 성공 KR winner가 DB에 못 가고,
    성공으로 닫으면 부분 유실이 SFN 상태에서 숨는다. loader 실행과 최종 FAILED를 모두 고정한다."""
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8"))

    assert (
        "States.Array('load-price-daily', '--run-id', $.run_id, "
        "'--input-run-id', $.run_id)" in sm
    )
    continue_check = sm.split("normalize_price_continue_check = {", 1)[1].split(
        "normalize_continue_checks = concat", 1)[0]
    assert re.search(
        r'Variable\s*=\s*"\$\.normalize_results\[\$\{local\.normalize_price_index\}\]'
        r'\.exit_code".*?IsPresent\s*=\s*true.*?NumericEquals\s*=\s*2',
        continue_check, re.S,
    )
    gate = sm.split("NormalizeCheckResults = {", 1)[1].split(
        "NotifyNormalizePartial = {", 1)[0]
    assert 'Next = "NotifyNormalizePartial"' in gate
    assert 'Default = "NotifyFailure"' in gate
    notify = sm.split("NotifyNormalizePartial = {", 1)[1].split("LoadInstruments =", 1)[0]
    assert re.search(r'Next\s*=\s*"LoadInstruments"', notify)
    assert "normalize_partial_notification_error" in notify
    final = sm.split("RawPartialCheck = {", 1)[1].split("PipelineSucceeded =", 1)[0]
    assert "local.raw_ingest_success_checks" in final
    assert "local.normalize_success_checks" in final
    assert 'Default = "PipelineFailed"' in final


def test_EOD_수급_exit_0_2_1은_성공범위와_최종실패를_동시에_보존한다():
    """WHY(ALPHA-1040·1041): NormalizeInvestor 2를 즉시 막으면 성공 winner를 잃고, 2를
    성공으로 닫으면 유실이 숨는다. loader도 같은 계약이며 1은 절대 하류로 보내면 안 된다."""
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8"))

    assert (
        "States.Array('load-etf-flow', '--run-id', $.run_id, "
        "'--input-run-id', $.run_id)" in sm
    )
    normalize_continue = sm.split("normalize_investor_continue_check = {", 1)[1].split(
        "normalize_continue_checks = concat", 1)[0]
    assert re.search(
        r'Variable\s*=\s*"\$\.normalize_results\[\$\{local\.normalize_investor_index\}\]'
        r'\.status".*?StringEquals\s*=\s*"succeeded"', normalize_continue, re.S,
    )
    assert re.search(
        r'Variable\s*=\s*"\$\.normalize_results\[\$\{local\.normalize_investor_index\}\]'
        r'\.exit_code".*?NumericEquals\s*=\s*2', normalize_continue, re.S,
    )
    assert "NumericEquals = 1" not in normalize_continue

    loader_continue = sm.split("feature_etf_flow_continue_check = {", 1)[1].split(
        "feature_continue_checks = concat", 1)[0]
    assert re.search(
        r'Variable\s*=\s*"\$\.feature_results\[\$\{local\.feature_etf_flow_index\}\]'
        r'\.status".*?StringEquals\s*=\s*"succeeded"', loader_continue, re.S,
    )
    assert re.search(
        r'Variable\s*=\s*"\$\.feature_results\[\$\{local\.feature_etf_flow_index\}\]'
        r'\.exit_code".*?NumericEquals\s*=\s*2', loader_continue, re.S,
    )
    assert "NumericEquals = 1" not in loader_continue

    normalize_gate = sm.split("NormalizeCheckResults = {", 1)[1].split(
        "NotifyNormalizePartial = {", 1)[0]
    assert 'Next = "NotifyNormalizePartial"' in normalize_gate
    assert 'Default = "NotifyFailure"' in normalize_gate
    feature_gate = sm.split("FeatureCheckResults = {", 1)[1].split(
        "LoadPriceTriggers = merge", 1)[0]
    assert 'local.feature_continue_checks' in feature_gate
    assert 'Default = "NotifyFailure"' in feature_gate
    partial = sm.split("FeaturePartialCheck = {", 1)[1].split(
        "NotifyFeaturePartial = {", 1)[0]
    assert "local.feature_etf_flow_index" in partial
    assert 'Next = "NotifyFeaturePartial"' in partial
    final = sm.split("RawPartialCheck = {", 1)[1].split("PipelineSucceeded =", 1)[0]
    assert "local.normalize_success_checks" in final
    assert "local.feature_success_checks" in final
    assert 'Default = "PipelineFailed"' in final


def test_ETF_NAV_exit_0_2_1은_하류진행과_최종실패를_동시에_보존한다():
    """WHY(ALPHA-1042·1043): producer/consumer exit 2의 성공 범위는 이어 처리하되 SFN
    성공으로 세탁하면 안 되고, exit 1은 신뢰할 범위가 없어 하류를 막아야 한다."""
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8"))
    assert (
        "States.Array('load-etf-nav', '--run-id', $.run_id, "
        "'--input-run-id', $.run_id)" in sm
    )
    normalize = sm.split("normalize_etf_nav_continue_check = {", 1)[1].split(
        "normalize_continue_checks = concat", 1)[0]
    assert "NumericEquals = 2" in normalize and "NumericEquals = 1" not in normalize
    loader = sm.split("feature_etf_nav_continue_check = {", 1)[1].split(
        "feature_continue_checks = concat", 1)[0]
    assert "NumericEquals = 2" in loader and "NumericEquals = 1" not in loader
    partial = sm.split("FeaturePartialCheck = {", 1)[1].split(
        "NotifyFeaturePartial = {", 1)[0]
    assert "local.feature_etf_nav_index" in partial
    final = sm.split("RawPartialCheck = {", 1)[1].split("PipelineSucceeded =", 1)[0]
    assert "local.normalize_success_checks" in final
    assert "local.feature_success_checks" in final
    assert 'Default = "PipelineFailed"' in final


def test_가격_트리거는_DB_loader_뒤에서_manifest_범위를_처리하고_strict_마감한다():
    """WHY(ALPHA-1039): 트리거가 feature Parallel 안에 남으면 가격·holdings commit보다 먼저
    읽는 경합이 생긴다. 두 loader의 exit 0/2 뒤에는 실행하되, 어느 부분 실패도 전체 SFN
    성공으로 세탁하지 않아야 한다."""
    sm = test_ops_catalog._strip_hcl_comments(
        (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8"))

    assert 'j.state != "LoadPriceTriggers"' in sm
    feature_gate = sm.split("FeatureCheckResults = {", 1)[1].split(
        "LoadPriceTriggers = merge", 1)[0]
    assert 'local.feature_continue_checks' in feature_gate
    assert 'Next = "LoadPriceTriggers"' in feature_gate
    price_continue = sm.split("feature_price_continue_check = {", 1)[1].split(
        "feature_continue_checks = concat", 1)[0]
    assert "NumericEquals = 2" in price_continue

    trigger = sm.split("LoadPriceTriggers = merge", 1)[1].split(
        "LoadPriceTriggersCheckExitCode = {", 1)[0]
    assert (
        "States.Array('load-price-triggers', '--run-id', $.run_id, "
        "'--input-run-id', $.run_id)" in trigger
    )
    trigger_exit = sm.split("LoadPriceTriggersCheckExitCode = {", 1)[1].split(
        "RawPartialCheck = {", 1)[0]
    assert "NumericEquals = 0" in trigger_exit and "NumericEquals = 2" in trigger_exit
    assert 'Default = "NotifyFailure"' in trigger_exit
    partial_check = sm.split("FeaturePartialCheck = {", 1)[1].split(
        "NotifyFeaturePartial = {", 1)[0]
    assert "local.feature_price_index" in partial_check
    assert re.search(
        r'Variable\s*=\s*"\$\.ecs\.Containers\[0\]\.ExitCode".*?'
        r'NumericEquals\s*=\s*2', partial_check, re.S,
    )
    assert 'Next = "NotifyFeaturePartial"' in partial_check
    notify = sm.split("NotifyFeaturePartial = {", 1)[1].split(
        "RawPartialCheck = {", 1)[0]
    assert re.search(r'Next\s*=\s*"RawPartialCheck"', notify)
    assert "feature_partial_notification_error" in notify

    final = sm.split("RawPartialCheck = {", 1)[1].split("PipelineSucceeded =", 1)[0]
    assert "local.feature_success_checks" in final
    assert re.search(
        r'Variable\s*=\s*"\$\.ecs\.Containers\[0\]\.ExitCode".*?NumericEquals\s*=\s*0',
        final, re.S,
    )
