"""Replay advances visibility without rewriting previously observed market facts."""
import pytest
from edge_analysis_v2.storage.factors import project_factor_metrics

from edge_analysis_v2.tools.fixture_data import FixtureTools, make_replay_fixture


def test_raw_history_and_article_identity_are_immutable_between_cutoffs():
    early = make_replay_fixture("2026-09-14T08:30:00+09:00")
    late = make_replay_fixture("2026-09-18T08:30:00+09:00")
    for key in ("news", "flow", "prices", "macro", "financials", "holdings", "etf_units", "distributions"):
        assert early[key] == late[key]
    early["news"][0]["title"] = "mutated caller"
    assert make_replay_fixture("2026-09-14T08:30:00+09:00")["news"][0]["title"] != "mutated caller"


def test_followup_becomes_visible_without_replacing_the_original_news():
    first = FixtureTools(make_replay_fixture("2026-09-14T08:30:00+09:00")).initial_input()
    next_day = FixtureTools(make_replay_fixture("2026-09-15T08:30:00+09:00")).initial_input()
    assert {r["news_id"] for r in first["news"]} == {"n1", "n2"}
    assert {r["news_id"] for r in next_day["news"]} == {"n1", "n2", "n3"}
    assert first["news"] == [r for r in next_day["news"] if r["news_id"] != "n3"]


@pytest.mark.parametrize("day", range(14, 19))
def test_all_factors_work_each_morning_without_future_observations(day):
    at = f"2026-09-{day}T08:30:00+09:00"
    fixture = make_replay_fixture(at)
    tools = FixtureTools(fixture)
    assert fixture["price_snapshots"] == []
    result = tools.call('get_instrument_factors', {'instrument_id': fixture['context']['etf_code']})
    for cards in project_factor_metrics(result, fixture['context']['etf_code']).values():
        assert cards
        assert all(r["observed_at"] <= at for r in cards)


def test_intraday_snapshots_are_fixed_and_prior_facts_do_not_change():
    early = make_replay_fixture("2026-09-14T10:00:00+09:00")
    late = make_replay_fixture("2026-09-14T14:00:00+09:00")
    assert late["price_snapshots"][:len(early["price_snapshots"])] == early["price_snapshots"]
    assert early["context"]["flow_as_of_date"] == late["context"]["flow_as_of_date"] == "2026-09-11"
    early_news = FixtureTools(early).initial_input()["news"]
    late_news = FixtureTools(late).initial_input()["news"]
    assert {row["news_id"] for row in early_news} == {"n1", "n2"}
    assert {row["news_id"] for row in late_news} == {"n1", "n2", "n3"}


def test_unusual_flow_is_first_visible_after_its_finalization():
    before = FixtureTools(make_replay_fixture("2026-09-15T14:00:00+09:00")).initial_input()["flow"]
    after = FixtureTools(make_replay_fixture("2026-09-16T08:30:00+09:00")).initial_input()["flow"]
    assert not any(r[0] == "2026-09-15" for body in before.values() for r in body["rows"])
    assert any(r[0] == "2026-09-15" and any(abs(v) >= 600000000 for v in r[1:])
               for body in after.values() for r in body["rows"])
