"""Planner 테스트 (ALPHA-530) — 스펙 §9 시나리오 1~5.

실제 Ledger 를 FakeOpsDB 위에서 돌려 실행 전 원장 기록 + SFN 시작 멱등/충돌을 검증한다.
"""

from __future__ import annotations

import json
import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.db import stable_domain_id
from data_pipeline.ops import entry
from data_pipeline.ops import planner as planner_mod
from data_pipeline.ops import states
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


def test_news_tasks_are_due_on_non_trading_weekday():
    # WHY: 뉴스 SFN 은 공휴일에도 돈다(평일 크론) — kr_trading_calendar 를 하나라도 True 로
    #      복사해 오면 그날 실행 결과가 SKIPPED 뒤로 통째로 사라진다(ALPHA-181 함정).
    db = FakeOpsDB()
    plan_run(_ledger(db), state_machine_arn=_ARN, scheduled_time=_SCHED, sfn_client=FakeSfn(),
             pipeline_type="news", holidays=frozenset({"2026-07-24"}))
    for row in db.etasks.values():
        assert row["plan_status"] == states.PLAN_DUE, row["task_key"]


def test_due_slots_returns_all_past_news_slots_of_the_day(monkeypatch):
    # WHY: 뉴스는 하루 3슬롯이다. 최신 슬롯 하나만 돌려주면 15:30 이 지나는 순간 15:00 런이
    #      영영 대조되지 않는다(ALPHA-565 사각의 확대재생산) — 그날 지난 슬롯 전부가 나와야
    #      주기 reconcile 이 늦은 종결까지 판정한다. grace(30분)는 슬롯별로 계산된다.
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


def test_disclosure_lane_owns_the_four_disclosure_tasks(monkeypatch):
    # WHY(ALPHA-724): 컷오버의 본체는 **소유 레인 이동**이다. 두 레인의 CLI 가 글자 그대로 같아
    #      (`ingest-raw-disclosure`…) 같은 스텝을 둘이 동시에 소유하면 `by_cli` 가 먼저 온
    #      엔트리를 돌려줘 장중 런의 attempt 가 시장 레인 task_key 로 기록된다 — 장중 런은
    #      영구 MISSED 다. 그래서 "레인 하나"는 성능이 아니라 정체성 요구다.
    #      **중간 미등록 상태를 두지 않은 이유도 여기서 잠근다**: 미등록은 잊히면 조용히
    #      영구화되지만(공시가 원장 밖에서 도는데 화면에 흔적 0), 레인을 바로 옮기면 최악이
    #      배포 순서에 따른 MISSED 몇 건이고 그건 자가 해소된다(Rule 12 — 관대한 쪽 대신
    #      시끄러운 쪽). 레인이 비면 그 조용한 상태로 되돌아간 것이므로 여기서 실패해야 한다.
    assert catalog.DISCLOSURE_PIPELINE_TYPE in entry._LANE_STATE_MACHINE_ARN_ENV
    assert {e.task_key for e in catalog.entries(catalog.DISCLOSURE_PIPELINE_TYPE)} == {
        "DISCLOSURE_COLLECTION_DART", "NORMALIZE_DISCLOSURE",
        "NORMALIZE_DISCLOSURE_SEGMENT", "LOAD_DISCLOSURE"}
    # 시장 레인에는 하나도 남지 않았다 — 한쪽만 옮기면 `by_cli` 오귀속이 그대로 살아난다.
    assert not [e for e in catalog.entries(catalog.PIPELINE_TYPE)
                if "DISCLOSURE" in e.task_key]
    # 판정 임계는 **terraform 의 실제 값**에서 뽑아 대조한다 — 상수를 하드코딩하면 cron 에
    # 30분 슬롯을 더하거나 SFN 타임아웃을 낮춰도 통과한다(edge-review). 두 계약:
    #   ① deadline + Reconciler 주기 < 슬롯 간격. 주기를 빼먹으면 안 된다 — 판정을 찍는 건
    #      15분마다 도는 Reconciler 라 deadline 직후가 아니라 최대 그만큼 뒤다. 이 항이 없으면
    #      "한 슬롯의 결측이 다음 슬롯 예정 전에 드러난다"가 산술적으로 거짓인데 통과한다.
    #   ② stalled **<** SFN 타임아웃(같으면 안 된다). 판정이 `> threshold` 인데 SFN 이 정확히
    #      타임아웃에 실행을 죽이므로, 같게 두면 경과가 임계를 넘는 순간이 오지 않아
    #      **영원히 발화하지 않는다**(있으나 마나 한 신호를 계약으로 못박는 셈).
    slot_interval = test_ops_catalog.lane_slot_interval_seconds("disclosure")
    sfn_timeout = test_ops_catalog.lane_sfn_timeout_seconds("disclosure")
    reconcile_period = test_ops_catalog.reconcile_period_seconds()
    for e in catalog.entries(catalog.DISCLOSURE_PIPELINE_TYPE):
        assert e.deadline_offset_seconds + reconcile_period < slot_interval, e.task_key
        assert e.stalled_after_seconds < sfn_timeout, e.task_key
        # 크론이 MON-FRI 라 평일 공휴일에도 돈다 — True 면 그 실행 결과가 SKIPPED 뒤로 사라진다.
        assert e.kr_trading_calendar is False, e.task_key


def test_due_slots_disclosure_lane_follows_its_own_env(monkeypatch):
    # WHY(ALPHA-721): 레인마다 슬롯 집합이 다르므로 env 도 레인마다여야 한다. 뉴스 env 를
    #      공유하면 공시 슬롯이 뉴스 시각(15:00·15:30·23:50)으로 잡혀 **존재하지 않는 런**을
    #      매 주기 PLANNER_MISSING 으로 연다. 미주입 레인은 슬롯 0개여야 한다 — 스케줄이 아직
    #      Planner 를 안 타는 배포(이 PR 시점)에서 거짓 결측을 만들지 않는 안전 기본값이다.
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


def test_장중_수급_레인은_달력_플래그가_작업마다_다르다():
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
    * 적재 False — 창 인자 없이 도는 **canonical 전량 스캔**이라 공휴일에도 실일을 한다(앞
      슬롯이 실패해 남은 백로그를 줍는다). True 면 그 회수 실행에 attempt 가 안 붙어, 다시
      실패해도 원장에 자리조차 없다(Rule 12).
    """
    by_key = {e.task_key: e for e in catalog.entries(catalog.INVESTOR_INTRADAY_PIPELINE_TYPE)}
    assert set(by_key) == {
        "INVESTOR_INTRADAY_COLLECTION_KIS",
        "NORMALIZE_INVESTOR_INTRADAY",
        "LOAD_INVESTOR_INTRADAY",
    }, "레인 구성이 바뀌었다 — 달력 판단을 다시 하라"
    assert by_key["INVESTOR_INTRADAY_COLLECTION_KIS"].kr_trading_calendar is True
    assert by_key["NORMALIZE_INVESTOR_INTRADAY"].kr_trading_calendar is True
    # 창 없는 전량 스캔이라 공휴일에도 실일을 한다 — SKIPPED 뒤로 가리면 안 된다.
    assert by_key["LOAD_INVESTOR_INTRADAY"].kr_trading_calendar is False
    # 그 근거(창 인자 부재)가 terraform 에 그대로 있는지 함께 든다 — 나중에 `--from/--to` 를
    # 붙이면 전량 스캔이 아니게 되고, 그때는 이 레인도 공시처럼 전부 False 가 아니라 반대로
    # 적재를 True 로 되돌려야 한다. 근거가 코드에서 사라졌는데 값만 남는 것을 막는다.
    sm = (test_ops_catalog._TF_MODULE / "statemachine.tf").read_text(encoding="utf-8")
    assert "States.Array('load-investor-intraday', '--run-id', $.run_id)" in sm, (
        "load-investor-intraday 의 command 가 바뀌었다 — 창 인자가 붙었다면 "
        "'공휴일에도 전량 스캔으로 실일을 한다'는 False 의 근거가 사라진다")
