"""Only listed ETFs pay for high-effort movement explanations, and one switch turns that off."""
import re

from edge_analysis_v2.analysis import effort


def test_listed_etf_thinks_harder_and_others_stay_cheap(monkeypatch):
    monkeypatch.setattr(effort, 'HIGH_EFFORT_ENABLED', True)
    monkeypatch.setattr(effort, 'HIGH_EFFORT_ETFS', frozenset({'396500'}))
    assert effort.movement_effort('396500') == 'high'
    assert effort.movement_effort('069500') == 'low'


def test_switch_off_returns_every_movement_to_low(monkeypatch):
    monkeypatch.setattr(effort, 'HIGH_EFFORT_ENABLED', False)
    monkeypatch.setattr(effort, 'HIGH_EFFORT_ETFS', frozenset({'396500'}))
    assert effort.movement_effort('396500') == 'low'


def test_list_holds_request_shaped_codes():
    # A code the request schema cannot carry would never match and silently stay at low.
    assert all(re.fullmatch(r'[A-Z0-9]{6}', code) for code in effort.HIGH_EFFORT_ETFS)
