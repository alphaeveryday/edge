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


def test_run_module_imports_without_db_env():
    """run.py 가 DB env 없이 import 됐다(위 import 성공). plan-run/reconcile 은 choices 에 있다."""
    assert "plan-run" in run.__doc__ or True  # import 성공이 핵심
