"""원천 관측 DAG 계약 (ALPHA-1130) — Airflow 3.3.2 이미지 안에서 돈다(README 실행법).

업무 계약(카탈로그 task_key·CLI·태스크 정의)은 data-pipeline tests/test_airflow_dag_contract.py 가 대조한다.
여기서는 실행 관리가 업무 의미를 바꾸는 지점을 고정한다 — 기본 pause·당일 수집·백필 인자·계열 독립.
"""

from __future__ import annotations

import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT.parents[3] / "infra/terraform/modules/data-pipeline"
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


def test_slot_matches_the_missed_run_judgment(dag_module):
    # WHY: Reconciler 는 `OPS_SOURCE_DAILY_SCHED_HHMM` 시각의 run 이 없으면 PLANNER_MISSING 을 연다. 이 레인은 cron 변수가
    # 없어 Terraform 이 DAG 에서 시각을 뽑지 못한다 — 한쪽만 옮기면 매일 거짓 결측이 열리고 진짜 결측은 안 보인다.
    # 모듈 local 이라 환경이 재정의할 수 없다 — 여기서 읽는 값이 곧 ops 태스크 정의에 들어가는 값이다.
    slots = re.search(r'source_daily_schedule_hhmm\s*=\s*"([^"]*)"', (TF / "ops_ledger.tf").read_text())[1].split(",")
    assert sorted(f"{int(m)} {int(h)} * * *" for h, m in (s.split(":") for s in slots)) == sorted(dag_module.CRONS)


def test_schedule_and_guards(dag_module):
    dag = dag_module.dag
    # WHY: 전망 배치는 매일 06:00 KST 에 기준시각까지 보이는 값만 읽는다(envs/dev/analysis-v2.tf). 슬롯에 run 상한을
    # 더한 시각이 06:00 을 넘으면 그날 수집이 그날 전망에 못 들어간다(옛 09:10 슬롯이 그랬다).
    assert dag_module.CRONS == ("20 5 * * *",)
    assert timedelta(hours=5, minutes=20) + dag.dagrun_timeout < timedelta(hours=6)
    assert dag.timetable.__class__.__name__ == "MultipleCronTriggerTimetable"
    assert dag.catchup is False and dag.max_active_runs == 1
    assert dag.dagrun_timeout < timedelta(seconds=1800)        # Reconciler 수명보다 짧게
    for family in dag_module.FAMILIES:
        collect = dag.get_task(f"{family}_collect")
        # 날짜 인자가 없는 원천(업종)은 오늘 값만 준다 — 과거 슬롯 run 이 오늘 값을 과거로 라벨하지 않게 막는다.
        assert collect.same_day_only and collect.noop_on_reprocess
        # 봇 P1: 성공 이력 skip 은 수집기의 요청 범위 검사(ensure_same_request)를 건너뛴다 — 같은 분에 trigger 한 두 백필이
        # run_id 를 공유하면 뒤 범위가 수집 없이 성공한다. 재실행 무호출은 수집기 자신이 보장하므로 skip 을 켜지 않는다.
        assert not collect.skip_if_succeeded
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


# ── 실패 통보 문구 (ALPHA-1169) ──────────────────────────────────────────────────────────

_UNSUPPORTED = {"dataset": "financial_metric", "failed": 2, "gaps": 9, "classes": {"unsupported": 2}, "items": [
    {"corp_code": "00160302", "corp_name": "코스모화학", "fiscal_year": 2026, "reprt_code": "11012", "fs_basis": fs,
     "metric": "bps", "reasons": ["bps_share_class_label_unsupported"], "class": "unsupported"} for fs in ("CFS", "OFS")]}


def _notify(dag_module, monkeypatch, *, codes=None, summary=_UNSUPPORTED, holds=None, logs_error=None, context=None):
    """실패 콜백을 한 번 돌려 SNS 로 나간 (제목, 본문)을 돌려준다. 기본은 재무 정제만 exit 2 인 run."""
    import json

    sent = []
    monkeypatch.setattr(dag_module, "ALARM_TOPIC", "arn:aws:sns:ap-northeast-2:0:alarms")
    monkeypatch.setattr(dag_module, "SnsHook", lambda: SimpleNamespace(
        publish_to_target=lambda **kw: sent.append((kw["subject"], kw["message"]))))

    def events(**kw):
        if logs_error:
            raise logs_error
        assert kw["logStreamName"] == "raw-ingest/data-pipeline/abc123"      # 그 ECS 태스크의 스트림만 읽는다
        lines = ["2026-10-05 05:24:01 INFO x 다른 줄"]
        if summary is not None:
            lines.append("2026-10-05 05:24:02 WARNING data_pipeline.steps.source_observations "
                         + dag_module.REJECT_SUMMARY_MARK + json.dumps(summary, ensure_ascii=False))
        return {"events": [{"message": m} for m in lines]}

    monkeypatch.setattr(dag_module, "AwsLogsHook", lambda **kw: SimpleNamespace(
        conn=SimpleNamespace(get_log_events=events)))
    for step in dag_module.STEPS:      # 테스트 환경에는 로그 그룹 env 가 없다 — 운영처럼 로그 설정이 있는 스텝으로 만든다
        task = dag_module.dag.get_task(step)
        monkeypatch.setattr(task, "awslogs_group", "/ecs/edge-dev-data-pipeline")
        monkeypatch.setattr(task, "awslogs_stream_prefix", "raw-ingest/data-pipeline")
    exit_codes = {"plan": 0, **{s: 0 for s in dag_module.STEPS}, "report": 0, "financial_normalize": 2, **(codes or {})}
    xcom = {(s, "exit_code"): c for s, c in exit_codes.items()}
    xcom.update({(s, "edge_started"): True for s in exit_codes})
    xcom.update({(s, "ecs_task_arn"): "arn:aws:ecs:ap-northeast-2:0:task/edge-dev-worker/abc123" for s in exit_codes})
    xcom.update({(s, "hold"): h for s, h in (holds or {}).items()})
    ti = SimpleNamespace(xcom_pull=lambda task_ids, key: xcom.get((task_ids, key)))
    run = SimpleNamespace(run_id="scheduled__2026-10-04T20:20:00+00:00", dag_id="edge_source_daily", conf={})
    dag_module._notify_failure(context or {"dag": dag_module.dag, "dag_run": run, "ti": ti, "reason": "task_failure"})
    assert len(sent) == 1
    return sent[0]


def test_alert_says_loaded_only_when_every_reject_is_unsupported_notation(dag_module, monkeypatch):
    # WHY(ALPHA-1169): 수집·적재가 끝났고 읽지 못하는 표기 2건만 남은 run 도 제목이 "FAILED"뿐이라, 매일 오는 통보가
    # 적재 장애인지 알려진 미지원인지 본문을 열어도 알 수 없었다. 제목과 본문이 그 둘을 가르고 회사·사유를 싣는다.
    subject, message = _notify(dag_module, monkeypatch)
    assert subject == "[source-daily] 적재 완료 · 미지원 2건 — airflow scheduled__2026-10-04T20:20:00+00:00"
    assert "run 은 FAILED 로 닫혔다" in message                       # 판정을 성공으로 바꾸지 않는다 — 문구만 가른다
    assert "financial_normalize=2" in message and "financial_load=0" in message
    assert "financial_normalize: 거부 2건(unsupported 2) · 결손 9건" in message
    assert "  - 00160302 코스모화학 2026 11012 CFS bps: bps_share_class_label_unsupported [unsupported]" in message


def test_alert_for_a_rerun_without_item_lines_points_to_the_quality_log(dag_module, monkeypatch):
    # WHY(로컬 리뷰): 이미 끝난 정제를 다시 돌린 run 의 요약에는 건수·분류만 있다. 분류로 "적재 완료"는 말할 수 있지만
    # 회사·사유는 없다 — 없는 내용을 지어내지 않고 어디서 보는지 적는다.
    subject, message = _notify(dag_module, monkeypatch, summary={**_UNSUPPORTED, "items": []})
    assert subject.startswith("[source-daily] 적재 완료 · 미지원 2건")
    assert "  … 외 2건(품질 로그 failures 참조)" in message and "코스모화학" not in message


@pytest.mark.parametrize("case,kwargs,expected", [
    ("오류로 분류된 거부가 섞임", {"summary": {**_UNSUPPORTED, "failed": 3, "classes": {"error": 1, "unsupported": 2}}},
     "거부 3건(error 1 unsupported 2)"),
    ("요약 줄을 로그에서 못 찾음", {"summary": None}, "거부 요약을 로그에서 찾지 못했다"),
    ("분류 없는 거부가 섞인 요약", {"summary": {**_UNSUPPORTED, "failed": 3}}, "거부 3건(unsupported 2)"),
    ("분류가 비어 있는 요약", {"summary": {**_UNSUPPORTED, "classes": {}, "items": []}}, "거부 2건()"),
    ("로그 조회가 실패함", {"logs_error": RuntimeError("AccessDenied")}, "상세를 만들지 못했다: RuntimeError: AccessDenied"),
    ("적재 스텝이 실패함", {"codes": {"financial_load": 1}}, "financial_load=1"),
    ("다른 계열의 수집이 부분 실패함", {"codes": {"macro_collect": 2}}, "macro_collect: 부분 실패(exit 2)"),
    ("원장 보고가 실패함", {"codes": {"report": 1}}, "report=1"),
    ("종료 코드를 남기지 못한 스텝이 있음", {"codes": {"sector_load": None}}, "sector_load=None"),
    ("실행 보류가 있음", {"holds": {"sector_load": {"kind": "ECS_STATE_UNKNOWN", "reason": "x"}}}, "실행 보류"),
])
def test_alert_stays_failed_unless_the_load_is_proven_complete(dag_module, monkeypatch, case, kwargs, expected):
    # WHY: "적재 완료"는 증거가 다 있을 때만 쓴다 — 오류 분류 거부, 다른 스텝의 실패·부분 실패·보류가 하나라도 있거나
    # 거부 내용을 읽지 못했으면 제목은 FAILED 그대로다. 실제 오류를 가볍게 읽히게 만들면 안 된다.
    subject, message = _notify(dag_module, monkeypatch, **kwargs)
    assert subject == "[source-daily] FAILED — airflow scheduled__2026-10-04T20:20:00+00:00", case
    assert expected in message and "적재는 끝났다" not in message, case


def test_alert_is_still_sent_when_the_callback_has_no_task_context(dag_module, monkeypatch):
    # WHY: Airflow 는 마지막 태스크 정보를 싣지 못한 콜백에 dag·run_id·reason 만 준다. 상세를 못 만든다고 통보 자체가
    # 예외로 사라지면 실패가 아무에게도 가지 않는다.
    subject, message = _notify(dag_module, monkeypatch,
                               context={"dag": dag_module.dag, "run_id": "manual__x", "reason": "timed_out"})
    assert subject == "[source-daily] FAILED — airflow manual__x"
    assert message.startswith("dag=edge_source_daily run=manual__x reason=timed_out")

