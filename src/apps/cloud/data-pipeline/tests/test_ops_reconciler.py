"""Reconciler 증거규칙 테스트 (ALPHA-530) — 스펙 §9 시나리오 7·9·10·11·12·13·14·15·16·19·20."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from data_pipeline.config import DbConfig
from data_pipeline.ops import states
from data_pipeline.ops.ledger import Ledger
from data_pipeline.ops.reconciler import detect_planner_missing, reconcile_run

from opsfakes import FakeEcs, FakeOpsDB, FakeSfn

_DB = DbConfig(password="x")
_RID = "run_X"
_RUN_KEY = "etf-daily:2026-07-24"
_NOW = datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc)
_PAST = "2026-07-24T01:00:00+00:00"
_OLD = "2026-07-24T00:00:00+00:00"
_FUTURE = "2026-07-24T20:00:00+00:00"


def _ledger(db):
    return Ledger(db=_DB, connect_fn=db.connect)


def _seed(db, tasks, *, hard_deadline=None):
    db.runs[_RUN_KEY] = db.runs_by_id[_RID] = {
        "pipeline_run_id": _RID, "run_key": _RUN_KEY, "execution_name": "etf-daily-2026-07-24",
        "expected_execution_arn": "arn:exec", "sfn_execution_arn": None,
        "launch_status": "LAUNCHED", "orchestration_status": None,
        "hard_deadline_at": hard_deadline, "trading_date": "2026-07-24"}
    for t in tasks:
        row = {"pipeline_run_id": _RID, "stage": "raw", "plan_status": "DUE",
               "task_outcome": "PENDING", "data_status": "UNKNOWN", "required": True,
               "eligible_at": None, "deadline_at": None, "missed_at": None,
               "fulfilled_at": None, "blocked_at": None, "outcome_reason": None,
               "current_attempt_id": None, "completeness": None, **t}
        db.etasks[(_RID, t["task_key"])] = db.etasks_by_id[t["expected_task_id"]] = row


def _entered(state, *, arn=None, succeeded=False, exit_code=0, submit_failed=False):
    """실 SFN history 모양(id/previousEventId 체인)으로 이벤트를 만든다 — execution_evidence 가
    Parallel 브랜치를 previousEventId 로 귀속하므로 체인이 있어야 arn/exit_code 가 붙는다."""
    events = [{"id": 1, "previousEventId": 0, "type": "TaskStateEntered",
               "stateEnteredEventDetails": {"name": state}}]
    nid = 2
    if arn:
        events.append({"id": nid, "previousEventId": nid - 1, "type": "TaskSubmitted",
                       "taskSubmittedEventDetails": {"output": json.dumps({"TaskArn": arn})}})
        nid += 1
    if succeeded:
        # ecs runTask.sync TaskSucceeded output 은 컨테이너 exit code 를 담는다 — 그게 성패 정본.
        out = json.dumps({"Containers": [{"ExitCode": exit_code}]})
        events.append({"id": nid, "previousEventId": nid - 1, "type": "TaskSucceeded",
                       "taskSucceededEventDetails": {"output": out}})
        nid += 1
    if submit_failed:
        events.append({"id": nid, "previousEventId": nid - 1, "type": "TaskSubmitFailed",
                       "taskSubmitFailedEventDetails": {}})
    return events


def _multi(state, occs):
    """occs=[(arn, exit_code|None), ...] — 같은 state 를 여러 번 진입시킨 history(재시도 재현)."""
    events, nid = [], 1
    for arn, exit_code in occs:
        events.append({"id": nid, "previousEventId": nid - 1, "type": "TaskStateEntered",
                       "stateEnteredEventDetails": {"name": state}})
        nid += 1
        if arn:
            events.append({"id": nid, "previousEventId": nid - 1, "type": "TaskSubmitted",
                           "taskSubmittedEventDetails": {"output": json.dumps({"TaskArn": arn})}})
            nid += 1
        if exit_code is not None:
            out = json.dumps({"Containers": [{"ExitCode": exit_code}]})
            events.append({"id": nid, "previousEventId": nid - 1, "type": "TaskSucceeded",
                           "taskSucceededEventDetails": {"output": out}})
            nid += 1
    return events


def _reconcile(db, *, history=None, ecs=None, status="RUNNING", stalled_after_seconds=None):
    return reconcile_run(
        _ledger(db), run_key=_RUN_KEY, now=_NOW,
        sfn_client=FakeSfn(history=history or [], describe={"status": status}),
        ecs_client=ecs or FakeEcs(), stalled_after_seconds=stalled_after_seconds)


def test_missed_when_state_not_entered_eligible_and_past_deadline():
    """시나리오 10 — 미진입 + eligible + deadline 초과 → MISSED (실행이 끝난 뒤)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    _reconcile(db, status="SUCCEEDED")
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_MISSED
    assert db.etasks_by_id["e1"]["missed_at"] == "SET"
    assert len(db.open_issues(states.ISSUE_MISSED)) == 1


def test_running_execution_defers_missed_until_it_ends():
    # WHY: deadline 은 작업별 **잠정** 오프셋이고(카탈로그: 스테이지별 SLA 가 코드에 없다) SFN
    #      타임아웃은 6시간이다 — 정상 실행 중에도 뒤 스테이지의 deadline 은 자주 지난다.
    #      "아직 차례가 아니다"를 "아예 시작되지 않았다"로 기록하면 MISSED 가 무의미해지고,
    #      `missed_at` 은 COALESCE 라 나중에 성공해도 **지워지지 않는다**(영구 거짓 양성).
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    summary = _reconcile(db, status="RUNNING")
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_PENDING
    assert db.etasks_by_id["e1"]["missed_at"] is None
    assert db.open_issues(states.ISSUE_MISSED) == []
    assert summary["deadline_pending"] == ["PRICE_COLLECTION_KIS"]


def test_blocked_not_missed_when_never_eligible_past_hard_deadline():
    """시나리오 11 — 끝까지 eligible 하지 못함(upstream 미완) → MISSED 아니라 BLOCKED."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "LOAD_PRICE_DAILY", "expected_task_id": "e1", "stage": "feature",
                "eligible_at": None, "deadline_at": _PAST}], hard_deadline=_PAST)
    _reconcile(db)
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_BLOCKED
    assert db.etasks_by_id["e1"]["blocked_at"] == "SET"
    assert len(db.open_issues(states.ISSUE_MISSED)) == 0


def test_running_over_time_is_stalled_execution_status_preserved():
    """시나리오 12 — RUNNING 시간 초과: STALLED(health)만, execution_status 유지."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    db.attempts.append({"attempt_id": "a1", "etid": "e1", "arn": "arn:task/kis",
                        "status": states.EXEC_RUNNING, "exit_code": None, "source": "WRAPPER",
                        "started_at": _OLD})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "RUNNING"}}))
    assert db.attempts[0]["status"] == states.EXEC_RUNNING     # 뒤집지 않는다
    assert len(db.open_issues(states.ISSUE_STALLED)) == 1


def test_stalled_threshold_comes_from_the_catalog_entry(monkeypatch):
    # WHY: 작업마다 정상 실행 시간이 다르다 — LLM 스텝(tag-news·assemble-events)은 전역 기본
    #      1시간을 정상적으로 넘고 SFN 타임아웃은 6시간이다. 전역 상수로 판정하면 **정상 실행
    #      중인 attempt 에 STALLED 가 붙고**, STALLED 는 resolve 경로가 없어 영구 OPEN 이다.
    import dataclasses

    from data_pipeline.ops import catalog

    entry = catalog.get("PRICE_COLLECTION_KIS")
    monkeypatch.setitem(catalog.CATALOG, "PRICE_COLLECTION_KIS",
                        dataclasses.replace(entry, stalled_after_seconds=86400))
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    db.attempts.append({"attempt_id": "a1", "etid": "e1", "arn": "arn:task/kis",
                        "status": states.EXEC_RUNNING, "exit_code": None, "source": "WRAPPER",
                        "started_at": _OLD})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "RUNNING"}}))
    assert db.open_issues(states.ISSUE_STALLED) == []   # 이 작업엔 아직 정상 범위다

    # 호출부가 명시하면 그게 이긴다(운영 오버라이드) — 인자를 받아놓고 조용히 무시하면
    # "임계를 낮춰 조사한다"가 아무 효과 없이 통과한다(Codex #271).
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "RUNNING"}}),
               stalled_after_seconds=1)
    assert len(db.open_issues(states.ISSUE_STALLED)) == 1


def test_confirmed_ecs_stopped_transitions_running_attempt():
    """시나리오 13·7 — ECS STOPPED 증거가 있을 때만 상태 확정(종료 UPDATE 누락 복구)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    db.attempts.append({"attempt_id": "a1", "etid": "e1", "arn": "arn:task/kis",
                        "status": states.EXEC_RUNNING, "exit_code": None, "source": "WRAPPER",
                        "started_at": _OLD})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "STOPPED", "exitCode": 0}}))
    assert db.attempts[0]["status"] == states.EXEC_SUCCEEDED


def test_ledger_gap_backfills_attempt_and_opens_issue():
    """시나리오 14 — ECS ARN 있고 attempt 없음 → backfill + LEDGER_GAP."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis"),
               ecs=FakeEcs(tasks={"arn:task/kis": {"lastStatus": "RUNNING"}}))
    assert any(a["etid"] == "e1" and a["arn"] == "arn:task/kis" for a in db.attempts)
    assert len(db.open_issues(states.ISSUE_LEDGER_GAP)) == 1


def test_evidence_lost_when_entered_but_no_ecs_task_confirmed():
    """시나리오 15 — 진입했으나 ECS 생성 확인 불가 → MISSED 단정 안 함, EVIDENCE_LOST."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    _reconcile(db, history=_entered("CollectKisPrice"))  # arn 없음
    assert db.etasks_by_id["e1"]["task_outcome"] != states.OUTCOME_MISSED
    assert len(db.open_issues(states.ISSUE_EVIDENCE_LOST)) == 1


def test_failed_to_start_no_phantom_attempt():
    """시나리오 9 — RunTask submit 실패 + ARN 없음 → outcome FAILED/FAILED_TO_START, attempt 없음."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_entered("CollectKisPrice", submit_failed=True))
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_FAILED
    assert db.etasks_by_id["e1"]["outcome_reason"] == states.REASON_FAILED_TO_START
    assert [a for a in db.attempts if a["etid"] == "e1"] == []


def test_missed_unlatches_to_fulfilled_on_late_success():
    """시나리오 16 — MISSED 후 늦은 성공 → FULFILLED, missed_at 보존, MISSED 이슈 RESOLVED."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "task_outcome": states.OUTCOME_MISSED, "missed_at": "SET", "eligible_at": _OLD}])
    db.issues.append({"issue_id": "iss1", "issue_type": states.ISSUE_MISSED,
                      "dedupe_key": f"missed:{_RID}:PRICE_COLLECTION_KIS", "status": "OPEN",
                      "occurrence_count": 1, "scope": "task", "scope_key": "e1", "evidence": None})
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis", succeeded=True))
    row = db.etasks_by_id["e1"]
    assert row["task_outcome"] == states.OUTCOME_FULFILLED
    assert row["missed_at"] == "SET"                            # 보존
    assert db.issues[0]["status"] == "RESOLVED"


def test_hard_deadline_terminates_eligible_pending_as_missed():
    """시나리오 20 — hard deadline 뒤 eligible-but-PENDING 은 MISSED 로 종결(무한 PENDING 금지)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _FUTURE}], hard_deadline=_PAST)
    _reconcile(db)
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_MISSED


def test_task_succeeded_but_nonzero_exit_is_failed_not_fulfilled():
    """ecs runTask.sync 의 TaskSucceeded 는 exit0 이 아니다 — exit≠0 이면 FAILED(edge-review)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_entered("CollectKisPrice", arn="arn:task/kis",
                                    succeeded=True, exit_code=1))
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_FAILED
    assert any(a["etid"] == "e1" and a["status"] == states.EXEC_FAILED for a in db.attempts)


def test_evidence_failure_does_not_declare_missed():
    """증거 조회 실패 시 미실행으로 단정하지 않는다 — 빈 evidence 로 MISSED 금지(edge-review)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    summary = reconcile_run(_ledger(db), run_key=_RUN_KEY, now=_NOW,
                            sfn_client=FakeSfn(describe_error=True), ecs_client=FakeEcs())
    assert summary["evidence_ok"] is False
    assert db.etasks_by_id["e1"]["task_outcome"] != states.OUTCOME_MISSED


def test_launch_unconfirmed_when_planning_run_has_no_sfn_evidence():
    """Planner 가 pipeline_run 은 남겼는데 SFN 실행 미확인 → LAUNCH_UNCONFIRMED(fail-loud)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1"}])
    db.runs[_RUN_KEY]["launch_status"] = states.LAUNCH_PLANNING
    reconcile_run(_ledger(db), run_key=_RUN_KEY, now=_NOW,
                  sfn_client=FakeSfn(describe_error=True), ecs_client=FakeEcs())
    assert len(db.open_issues(states.ISSUE_LAUNCH_UNCONFIRMED)) == 1


def test_reconcile_does_not_overwrite_planner_launch_conflict():
    """Planner 가 낸 LAUNCH_CONFLICT 를 reconcile 이 뒤엎지 않는다 — 다른 실행 history 채택 금지."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1",
                "eligible_at": _OLD, "deadline_at": _PAST}])
    db.runs[_RUN_KEY]["launch_status"] = states.LAUNCH_CONFLICT
    summary = _reconcile(db)
    assert summary.get("launch_conflict") is True
    assert db.etasks_by_id["e1"]["task_outcome"] != states.OUTCOME_MISSED  # 판정 안 함


def test_reconcile_rejects_foreign_execution_by_input_hash():
    """locator ARN 에 다른 입력의 실행이 있으면(해시 불일치) 증거를 채택하지 않고 CONFLICT."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD,
                "deadline_at": _PAST}])
    db.runs[_RUN_KEY]["input_hash"] = "OUR_HASH"      # sfn_execution_arn 은 None(확정 전)
    sfn = FakeSfn(describe={"input": json.dumps({"mode": "backfill"}), "status": "RUNNING"})
    summary = reconcile_run(_ledger(db), run_key=_RUN_KEY, now=_NOW, sfn_client=sfn,
                            ecs_client=FakeEcs())
    assert summary.get("launch_conflict") is True
    assert len(db.open_issues(states.ISSUE_LAUNCH_CONFLICT)) == 1
    assert db.etasks_by_id["e1"]["task_outcome"] != states.OUTCOME_MISSED


def test_retry_reconstructs_both_attempts_latest_wins():
    """#2 — 재시도 히스토리 복원. 같은 state 두 번(첫 exit1·둘째 exit0) → attempt 2행(각 ARN)이
    각각 FAILED·SUCCEEDED 로 보존되고, 앞 exit code 가 뒤 시도를 오염시키지 않으며, outcome 은
    최신 occurrence 로 FULFILLED. (예전엔 state 별 뭉침이라 앞 exit1 이 뒤를 오판했다.)"""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_multi("CollectKisPrice", [("arn:task/1", 1), ("arn:task/2", 0)]))
    attempts = {a["arn"]: a["status"] for a in db.attempts if a["etid"] == "e1"}
    assert attempts == {"arn:task/1": states.EXEC_FAILED, "arn:task/2": states.EXEC_SUCCEEDED}
    assert db.etasks_by_id["e1"]["task_outcome"] == states.OUTCOME_FULFILLED


def test_retry_running_second_attempt_not_misjudged_by_stale_exit():
    """#2 핵심 — 첫 시도 exit1, 둘째 아직 실행 중(exit 없음): 둘째를 stale exit1 로 FAILED 오판하지
    않는다. 둘째 attempt 는 RUNNING, 작업 outcome 은 아직 확정 안 함(FAILED 아님)."""
    db = FakeOpsDB()
    _seed(db, [{"task_key": "PRICE_COLLECTION_KIS", "expected_task_id": "e1", "eligible_at": _OLD}])
    _reconcile(db, history=_multi("CollectKisPrice", [("arn:task/1", 1), ("arn:task/2", None)]))
    attempts = {a["arn"]: a["status"] for a in db.attempts if a["etid"] == "e1"}
    assert attempts["arn:task/1"] == states.EXEC_FAILED
    assert attempts["arn:task/2"] == states.EXEC_RUNNING     # stale exit1 로 FAILED 오판 안 함
    assert db.etasks_by_id["e1"]["task_outcome"] != states.OUTCOME_FAILED


def test_planner_missing_when_slot_has_no_run():
    """시나리오 19 — schedule 상 있어야 할 run_key 부재 → PLANNER_MISSING, 생기면 RESOLVE."""
    db = FakeOpsDB()
    ledger = _ledger(db)
    missing = detect_planner_missing(ledger, expected_run_keys=["etf-daily:2026-07-25"])
    assert missing == ["etf-daily:2026-07-25"]
    assert len(db.open_issues(states.ISSUE_PLANNER_MISSING)) == 1
    # 늦게 run 이 생기면 거짓 경보를 닫는다(비래치).
    db.runs["etf-daily:2026-07-25"] = db.runs_by_id["r25"] = {
        "pipeline_run_id": "r25", "run_key": "etf-daily:2026-07-25", "execution_name": "x",
        "expected_execution_arn": None, "sfn_execution_arn": None, "launch_status": "LAUNCHED",
        "orchestration_status": None, "hard_deadline_at": None, "trading_date": "2026-07-25"}
    detect_planner_missing(ledger, expected_run_keys=["etf-daily:2026-07-25"])
    assert len(db.open_issues(states.ISSUE_PLANNER_MISSING)) == 0
