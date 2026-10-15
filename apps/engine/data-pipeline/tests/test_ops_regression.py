"""회귀 — 원장 미설정 환경에서도 기존 태스크가 죽지 않는다 (ALPHA-530, 스펙 §6·§9 시나리오 23).

이 모듈이 import 되는 것 자체가 회귀 검사다: run.py 와 ops 전체가 DB env 없이 import 돼야 한다
(DbConfig 를 import 시점에 강제 생성하지 않는 lazy 성질). import 가 실패하면 수집·정제 태스크가
모듈 로드만으로 전멸한다.
"""

from __future__ import annotations

from data_pipeline import run  # noqa: F401  (import 자체가 회귀 검사)
from data_pipeline.config import DbConfig
from data_pipeline.ops.entry import ledger_from_settings


class _NoDb:
    db = None


class _WithDb:
    db = DbConfig(password="x")


def test_ledger_is_none_without_db_config():
    """원장 DB 미설정 → Ledger 없음(instrument 는 투명 통과)."""
    assert ledger_from_settings(_NoDb()) is None


def test_ledger_present_with_db_config():
    assert ledger_from_settings(_WithDb()) is not None


def test_plan_run_and_reconcile_are_registered_cli_steps():
    """plan-run·reconcile 이 실제 argparse choices 에 등록돼 있다(진입점 계약 — Rule 9).

    run.py 는 sys.exit 하는 __main__ 뿐이라 파서를 직접 못 불러오므로, choices 리터럴을 소스에서
    확인한다. 이름이 바뀌거나 빠지면 이 테스트가 깨진다(or True 같은 공허한 통과 금지)."""
    import inspect

    src = inspect.getsource(run)
    assert '"plan-run"' in src and '"reconcile"' in src
    assert "ops_entry.plan_run_cli" in src and "ops_entry.reconcile_cli" in src
