"""장전 유니버스 레인 실행 누락 감시(ALPHA-1431).

WHY: 장전 레인은 원장 밖이고 Airflow 로 옮기면 SFN 알람 셋이 영영 안 운다. 이 판정이 유일한
신호라, 거짓 침묵(못 본 것을 정상으로 접음)도 거짓 알람(주말·휴장일·첫 빈 페이지)도 안 된다.
"""

from __future__ import annotations

import pathlib
from datetime import datetime

import pytest

from data_pipeline.ops import entry, premarket_watch
from data_pipeline.ops.planner import KST

THU = "2026-10-22"  # 목요일 거래일
HOLIDAY = "2026-10-09"  # 금요일 한글날


class FakeLogs:
    def __init__(self, pages=None, error=None):
        self.pages = pages if pages is not None else [{"events": []}]
        self.error = error
        self.calls = []

    def get_paginator(self, name):
        assert name == "filter_log_events"
        return self

    def paginate(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return iter(self.pages)


def _at(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=KST)


def _check(now, logs, holidays=frozenset({HOLIDAY})):
    return premarket_watch.check(now, logs_client_fn=lambda: logs, log_group="/ecs/x",
                                 holidays=holidays)


def test_success_on_a_later_page_is_ok():
    # FilterLogEvents 는 일치가 없는 구간을 빈 페이지+nextToken 으로 돌려준다 — 첫 페이지만 보면 거짓 알람.
    logs = FakeLogs([{"events": []}, {"events": [{"message": "x INFO universe 무변경 — ..."}]}])
    assert _check(_at(THU, "07:40"), logs) == "ok"


def test_no_success_by_cutoff_is_missing_and_logs_alarm_phrase(caplog):
    with caplog.at_level("ERROR", logger=premarket_watch.__name__):
        assert _check(_at(THU, "07:31"), FakeLogs()) == "missing"
    assert [r.getMessage().split(" ")[0] for r in caplog.records] == [premarket_watch.MISSING_LOG]


def test_query_ends_at_cutoff_so_a_late_success_still_alarms():
    # "07:30 까지 끝나지 않음"도 잡아야 한다 — 08:00 에 봐도 조회 끝은 07:30, 시작은 그날 00:00(KST).
    logs = FakeLogs()
    _check(_at(THU, "08:00"), logs)
    (call,) = logs.calls
    assert call["startTime"] == int(_at(THU, "00:00").timestamp() * 1000)
    assert call["endTime"] == int(_at(THU, "07:30").timestamp() * 1000)


@pytest.mark.parametrize("now", [_at(THU, "07:29"), _at(THU, "09:00"),
                                 _at(HOLIDAY, "07:40"), _at("2026-10-24", "07:40")])
def test_outside_window_or_non_trading_day_does_not_query(now):
    # 07:30 전·09:00 이후·휴장일(한글날)·토요일 — 장전이 안 도는 게 정상이니 묻지도 않는다.
    logs = FakeLogs()
    assert _check(now, logs) == "skip"
    assert logs.calls == []


def test_query_error_is_its_own_alarm_not_silence(caplog):
    # 권한·네트워크 오류를 "성공 없음"이나 "정상"으로 접으면 감시가 죽은 줄 모른다.
    with caplog.at_level("ERROR", logger=premarket_watch.__name__):
        assert _check(_at(THU, "07:40"), FakeLogs(error=RuntimeError("AccessDenied"))) == "failed"
    assert [r.getMessage().split(" ")[0] for r in caplog.records] == [premarket_watch.WATCH_FAILED_LOG]


def test_unset_log_group_disables_the_watch(monkeypatch):
    monkeypatch.delenv("OPS_PREMARKET_LOG_GROUP", raising=False)
    assert premarket_watch.run_from_env(_at(THU, "07:40"), lambda: pytest.fail("조회하면 안 된다")) == "skip"


def test_cli_exit_code_follows_verdict(monkeypatch):
    monkeypatch.setenv("OPS_PREMARKET_LOG_GROUP", "/ecs/x")
    monkeypatch.setenv("OPS_KR_HOLIDAYS", HOLIDAY)
    monkeypatch.setenv("OPS_SCHEDULED_TIME", "2026-10-21T22:40:00Z")  # 10-22 07:40 KST
    monkeypatch.setattr(entry.aws, "logs_client", lambda: FakeLogs())
    assert entry.premarket_watch_cli() == 1
    monkeypatch.setenv("OPS_SCHEDULED_TIME", "2026-10-09T22:40:00Z")  # 10-10 토요일
    assert entry.premarket_watch_cli() == 0


def test_success_pattern_matches_the_step_log_lines():
    # 문구를 한쪽만 바꾸면 매 거래일 거짓 알람이다.
    src = (pathlib.Path(premarket_watch.__file__).resolve().parents[1]
           / "steps/build_minute_universe.py").read_text(encoding="utf-8")
    assert 'logger.info("universe 무변경 — ' in src and 'logger.info("universe 갱신: ' in src
    assert premarket_watch.SUCCESS_PATTERN == '?"universe 무변경" ?"universe 갱신"'


def test_alarm_filter_counts_both_error_phrases():
    # 문구를 한쪽만 바꾸면 필터가 아무것도 세지 않고 알람은 영영 조용하다.
    tf = (pathlib.Path(__file__).resolve().parents[4]
          / "infra/terraform/modules/data-pipeline/ops_ledger.tf").read_text(encoding="utf-8")
    assert (f'pattern        = "?\\"{premarket_watch.MISSING_LOG}\\" ?\\"{premarket_watch.WATCH_FAILED_LOG}\\""'
            in tf)


def test_periodic_reconcile_runs_the_watch_even_when_the_lock_is_taken(monkeypatch):
    # 장전 감시는 원장을 안 쓴다 — 락 경합으로 그 주기를 건너뛰면 07:30~07:45 첫 판정을 놓친다.
    # DAG 가 run_key 를 지목한 보고 호출은 주기 실행이 아니라 감시를 안 한다(중복 ERROR 방지).
    from opsfakes import FakeOpsDB
    from data_pipeline.ops.ledger import Ledger
    from data_pipeline.config import DbConfig

    seen = []
    monkeypatch.setattr(premarket_watch, "run_from_env", lambda now, fn: seen.append(now) or "skip")
    db = FakeOpsDB(advisory_grants=False)
    ledger = Ledger(DbConfig(host="h", port=5432, name="n", user="u", password="p"),
                    connect_fn=db.connect)
    monkeypatch.setattr(entry, "ledger_from_settings", lambda _s: ledger)
    monkeypatch.delenv("OPS_RUN_KEY", raising=False)
    assert entry.reconcile_cli(object()) == 0
    assert len(seen) == 1
    monkeypatch.setenv("OPS_RUN_KEY", "investor-intraday:2026-09-22T09:35")
    entry.reconcile_cli(object())
    assert len(seen) == 1
