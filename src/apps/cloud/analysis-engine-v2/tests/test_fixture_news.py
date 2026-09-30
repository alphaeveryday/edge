"""News identity and time restrictions prevent future evidence from leaking."""
from copy import deepcopy

import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools


@pytest.fixture
def fixture():
    return {
        "context": {"etf_code": "ETF", "analysis_at": "2026-09-21T10:00:00+09:00", "flow_as_of_date": "2026-09-18"},
        "holdings": [{"instrument_id": "A", "weight": 1, "as_of_date": "2026-09-18", "available_at": "2026-09-18T18:00:00+09:00"}],
        "news": [{"news_id": str(i), "title": f"Title {i}", "body": "article", "published_at": f"2026-09-21T09:0{i}:00+09:00", "available_at": f"2026-09-21T09:0{i}:00+09:00", "thread_id": "t", "stage": "announced", "event_id": "e"} for i in range(2)],
    }


def test_duplicate_reports_collapse_only_by_event_identity(fixture):
    tools = FixtureTools(fixture)
    result = tools.call("search_news_threads", {})["result"]
    event = result["threads"][0]["stages"][0]["events"][0]
    assert event["news_id"] == "1"
    assert event["duplicate_count"] == 1


def test_evidence_omits_body_and_rejects_unavailable_id(fixture):
    tools = FixtureTools(fixture)
    result = tools.call("get_issue_evidence", {"news_ids": ["0"], "include_body": False})
    assert result["result"] == {"news": [{"news_id": "0", "title": "Title 0"}]}
    fixture["news"][0]["available_at"] = "2026-09-21T11:00:00+09:00"
    with pytest.raises(ValueError, match="unavailable"):
        FixtureTools(fixture).call("get_issue_evidence", {"news_ids": ["0"], "include_body": True})


def test_partial_weights_never_become_whole_etf(fixture):
    fixture["holdings"][0]["weight"] = 0.2
    with pytest.raises(ValueError, match="weight"):
        FixtureTools(fixture).call("get_etf_holdings", {})


def test_unknown_arguments_fail_and_raw_fixture_is_not_mutated(fixture):
    before = deepcopy(fixture)
    tools = FixtureTools(fixture)
    with pytest.raises(ValueError):
        tools.call("get_etf_holdings", {"invented": True})
    tools.initial_input()
    assert fixture == before
