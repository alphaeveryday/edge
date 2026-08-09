"""config — 잘못/누락 설정은 조용한 기본값이 아니라 fail-loud (AGENTS Rule 12)."""

from datetime import datetime

import pytest

from edge_analysis.config import (
    KST,
    PgConfig,
    PipelineError,
    Settings,
    load_settings,
    parse_trade_date,
)


def _set_valid_env(monkeypatch):
    """로드 성공에 필요한 최소 환경을 세팅한다."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    for name in ("PGSCHEMA", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_load_settings_builds_validated_settings(monkeypatch):
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("PGHOST", "db.internal")

    settings = load_settings(trade_date="2026-07-14", request_id="r1")

    assert isinstance(settings, Settings)
    assert isinstance(settings.pg, PgConfig)
    assert settings.pg.host == "db.internal"
    assert settings.deepseek_api_key == "sk-test"
    assert settings.request_id == "r1"  # manual CLI correlation id is never rewritten


def test_load_settings_without_api_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(PipelineError):
        load_settings(trade_date="2026-07-14")


def test_load_settings_with_injected_schema_raises(monkeypatch):
    _set_valid_env(monkeypatch)
    monkeypatch.setenv("PGSCHEMA", "bad; DROP")

    with pytest.raises(PipelineError):
        load_settings(trade_date="2026-07-14")


def test_parse_trade_date_defaults_to_today_when_empty():
    # 트리거 없는 날에도 오늘(KST)로 돌아야 그날 설명이 난다.
    assert parse_trade_date(None) == datetime.now(KST).date()


def test_parse_trade_date_rejects_malformed_value():
    with pytest.raises(PipelineError):
        parse_trade_date("2026/07/14")
