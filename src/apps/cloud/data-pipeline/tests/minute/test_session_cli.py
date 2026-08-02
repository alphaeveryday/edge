"""세션 계획·drain CLI 테스트 (ALPHA-698, 계획 §13).

의도: 이 두 진입점이 없으면 체인이 **가운데부터** 시작한다 — EOD QC 조차 손으로 DB 행을
넣어야 돌았다. 여기서 고정하는 건 셋이다.

- **재실행은 성공이다** — Premarket SFN 재시도·Worker 재기동이 정상 운영이라, 멱등 재계획을
  실패로 만들면 재시도가 곧 장애가 된다.
- **가격 세션에 universe 를 빠뜨리면 거부한다** — 기본값으로 흘리면 정규장 390 만 계획되고
  시간외 구간이 **아무 실패 신호 없이** 누락된다(ALPHA-684 와 같은 축).
- **"이미 넘어간 drain"은 성공이 아니다** — SFN 이 그걸 "내가 방금 걸었다"로 읽으면
  뒤따르는 대기·QC 타이밍을 잘못 잡는다.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig, Settings
from data_pipeline.minute.session_cli import drain_session_cli, plan_session_cli

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSettings:
    """`Settings` 전체를 만들지 않는다 — 이 CLI 가 보는 건 `db` 하나다."""

    def __init__(self, db):
        self.db = db


def make_settings(db_ok=True):
    return FakeSettings(DbConfig(password="x") if db_ok else None)


@pytest.fixture
def ledger_db(monkeypatch):
    """CLI 가 만드는 MinuteLedger 가 가짜 커넥션을 쓰게 한다."""
    db = FakeMinuteDB()
    import data_pipeline.minute.session_cli as module

    original = module.MinuteLedger

    def _ledger(**kwargs):
        return original(db=kwargs["db"], connect_fn=db.connect)

    monkeypatch.setattr(module, "MinuteLedger", _ledger)
    return db


class TestPlan:
    def test_news_session_plans_regular_windows(self, ledger_db, capsys):
        code = plan_session_cli(
            make_settings(), dataset="news_minute", source_group="bigkinds",
            session_date="2026-07-31", universe=None,
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert (payload["created"], payload["window_count"]) == (True, 390)
        assert len(ledger_db.windows) == 390

    def test_replan_is_success_not_failure(self, ledger_db, capsys):
        # Premarket SFN 재시도·Worker 재기동이 정상 운영이다 — 멱등 재계획을 실패로
        # 만들면 재시도가 곧 장애가 된다.
        args = dict(dataset="news_minute", source_group="bigkinds",
                    session_date="2026-07-31", universe=None)
        assert plan_session_cli(make_settings(), **args) == 0
        capsys.readouterr()
        assert plan_session_cli(make_settings(), **args) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["created"] is False          # 무엇이 새로 생겼는지는 이 값이 말한다
        assert len(ledger_db.windows) == 390        # 중복 생성 0

    def test_price_session_without_universe_is_rejected(self, ledger_db):
        # ⚠️ 기본값으로 흘리면 정규장 390 만 계획되고 시간외 구간이 무신호로 누락된다
        assert plan_session_cli(
            make_settings(), dataset="price_minute", source_group="toss",
            session_date="2026-07-31", universe=None,
        ) == 2
        assert ledger_db.sessions == {}

    def test_price_session_uses_the_given_universe(self, ledger_db, capsys):
        code = plan_session_cli(
            make_settings(), dataset="price_minute", source_group="toss",
            session_date="2026-07-31", universe=str(FIXTURES / "universe_348.json"),
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        # 선언이 없는 universe 라 정규장 390 — 값은 파일이 정하고 CLI 는 읽기만 한다
        assert payload["window_count"] == 390
        session = next(iter(ledger_db.sessions.values()))
        assert session["universe_version"] == "univ-fixture-v1"

    def test_universe_on_a_news_session_is_rejected(self, ledger_db):
        # 조용히 무시하면 운영자는 그 파일이 반영된 줄 안다
        assert plan_session_cli(
            make_settings(), dataset="news_minute", source_group="bigkinds",
            session_date="2026-07-31", universe=str(FIXTURES / "universe_348.json"),
        ) == 2

    def test_bad_date_and_missing_args_are_rejected(self, ledger_db):
        assert plan_session_cli(
            make_settings(), dataset="news_minute", source_group="bigkinds",
            session_date="2026-13-99", universe=None,
        ) == 2
        assert plan_session_cli(
            make_settings(), dataset=None, source_group="bigkinds",
            session_date="2026-07-31", universe=None,
        ) == 2

    def test_no_db_is_a_config_failure(self):
        assert plan_session_cli(
            make_settings(db_ok=False), dataset="news_minute", source_group="bigkinds",
            session_date="2026-07-31", universe=None,
        ) == 2


class TestDrain:
    def _plan(self, capsys):
        plan_session_cli(
            make_settings(), dataset="news_minute", source_group="bigkinds",
            session_date="2026-07-31", universe=None,
        )
        return json.loads(capsys.readouterr().out)["session_id"]

    def test_drain_request_moves_the_phase(self, ledger_db, capsys):
        session_id = self._plan(capsys)
        assert drain_session_cli(make_settings(), session_id=session_id) == 0
        assert json.loads(capsys.readouterr().out)["drain_requested"] is True
        assert ledger_db.sessions[session_id]["phase"] == "DRAINING"

    def test_second_drain_is_not_reported_as_applied(self, ledger_db, capsys):
        # 무해하지만 무의미하다 — SFN 이 "내가 방금 걸었다"로 읽으면 뒤따르는 대기·QC
        # 타이밍을 잘못 잡는다.
        session_id = self._plan(capsys)
        drain_session_cli(make_settings(), session_id=session_id)
        capsys.readouterr()

        assert drain_session_cli(make_settings(), session_id=session_id) == 1
        assert json.loads(capsys.readouterr().out)["drain_requested"] is False

    def test_unknown_session_is_not_silently_ok(self, ledger_db, capsys):
        assert drain_session_cli(make_settings(), session_id="msn_nope") == 1

    def test_missing_session_id_is_a_config_failure(self, ledger_db):
        assert drain_session_cli(make_settings(), session_id=None) == 2
