"""Tests for settings loading and validation.

Bad or missing configuration must fail loudly rather than fall back to a silent
default (AGENTS Rule 12).
"""

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
    """Set the minimum environment for a successful load."""
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
    # A trigger-less day still runs for "today" (KST) instead of failing.
    assert parse_trade_date(None) == datetime.now(KST).date()


def test_parse_trade_date_rejects_malformed_value():
    with pytest.raises(PipelineError):
        parse_trade_date("2026/07/14")
