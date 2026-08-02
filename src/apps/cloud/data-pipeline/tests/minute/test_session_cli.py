"""세션 계획·drain CLI 테스트 (ALPHA-698, 계획 §13).

의도: 이 두 진입점이 없으면 체인이 **가운데부터** 시작한다 — EOD QC 조차 손으로 DB 행을
넣어야 돌았다. 여기서 고정하는 건 셋이다.

- **재실행은 성공이다** — Premarket SFN 재시도·Worker 재기동이 정상 운영이라, 멱등 재계획을
  실패로 만들면 재시도가 곧 장애가 된다.
- **가격 세션에 universe 를 빠뜨리면 거부한다** — 기본값으로 흘리면 정규장 390 만 계획되고
  시간외 구간이 **아무 실패 신호 없이** 누락된다(ALPHA-684 와 같은 축).
- **"이미 넘어간 drain"도 성공이다** — DB 커밋 뒤 출력 전에 죽은 실행의 재시도가 정상
  운영이라, 그걸 실패로 내면 재시도가 EOD 흐름을 세운다. 방금 걸었는지는 exit code 가
  아니라 출력(`drain_requested`)이 말한다. 반면 **없는 세션은 거부한다** — 지목이 틀린
  것이고 재시도로 낫지 않는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
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

    def test_extended_universe_plans_720_windows(self, ledger_db, capsys):
        # ⚠️ 이 반례가 없으면 CLI 가 실수로 `plan_session_windows(universe=None)` 을 불러도
        # 통과한다(선언 없는 fixture 는 어느 쪽이든 390). 실제 시간외 universe 에서는
        # 720 중 330 window 가 조용히 누락된다.
        code = plan_session_cli(
            make_settings(), dataset="price_minute", source_group="toss",
            session_date="2026-07-31", universe=str(FIXTURES / "universe_extended.json"),
        )
        payload = json.loads(capsys.readouterr().out)
        assert (code, payload["window_count"]) == (0, 720)
        assert payload["windows"]["first"].endswith("08:00:00+09:00")
        assert payload["windows"]["last"].endswith("20:00:00+09:00")

    def test_unknown_dataset_is_rejected(self, ledger_db):
        # 오타를 뉴스로 흘리면 그 dataset 을 처리할 Worker 가 없어 하루가 통째로 안 도는데
        # 원장은 정상으로 보인다
        assert plan_session_cli(
            make_settings(), dataset="price_minut", source_group="toss",
            session_date="2026-07-31", universe=None,
        ) == 2
        assert ledger_db.sessions == {}

    def test_week_date_format_is_rejected(self, ledger_db):
        # `date.fromisoformat` 은 3.11+ 에서 이걸 2025-12-29 로 읽는다 — 다른 연도의
        # 세션이 조용히 생긴다
        assert plan_session_cli(
            make_settings(), dataset="news_minute", source_group="bigkinds",
            session_date="2026-W01-1", universe=None,
        ) == 2

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

    def test_second_drain_is_success_because_retry_is_normal(self, ledger_db, capsys):
        # ⚠️ DB 커밋 뒤 출력 전에 죽은 실행을 SFN 이 재시도하면 두 번째는 반드시 False 를
        # 받는다 — 그걸 실패로 내면 **정상 재시도가 EOD 흐름을 세운다**(693 의 FINALIZED
        # 재실행과 같은 축). 방금 걸었는지는 exit code 가 아니라 출력이 말한다.
        session_id = self._plan(capsys)
        drain_session_cli(make_settings(), session_id=session_id)
        capsys.readouterr()

        assert drain_session_cli(make_settings(), session_id=session_id) == 0
        payload = json.loads(capsys.readouterr().out)
        assert (payload["drain_requested"], payload["phase_before"]) == (False, "DRAINING")

    def test_unknown_session_is_rejected(self, ledger_db, capsys):
        # 재시도로 낫지 않는다 — 지목이 틀린 것이다
        assert drain_session_cli(make_settings(), session_id="msn_nope") == 2

    def test_missing_session_id_is_a_config_failure(self, ledger_db):
        assert drain_session_cli(make_settings(), session_id=None) == 2
