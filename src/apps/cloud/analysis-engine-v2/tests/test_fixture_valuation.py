"""Valuation uses released earnings and complete holdings, never subset averages."""
import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools


def valuation_fixture():
    return {"context": {"etf_code": "ETF", "analysis_at": "2026-09-21T10:00:00+09:00", "flow_as_of_date": "2026-09-18"},
            "holdings": [{"instrument_id": name, "weight": w, "as_of_date": "2026-09-18", "available_at": "2026-09-18T18:00:00+09:00"} for name, w in (("A", .6), ("B", .4))],
            "prices": [{"instrument_id": name, "date": "2026-09-18", "close": price, "available_at": "2026-09-18T18:00:00+09:00"} for name, price in (("A", 100), ("B", 200))],
            "financials": [{"instrument_id": name, "period": period, "eps": 2.5, "bps": 50, "available_at": "2026-08-15T18:00:00+09:00"} for name in ("A", "B") for period in ("2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")]}


def test_weighted_ratios_are_not_largest_holding_proxy():
    result = FixtureTools(valuation_fixture()).call("calculate_weighted_valuation", {})["result"]
    assert result["weighted_per"] == 14
    assert result["weighted_pbr"] == 2.8
    assert len(result["constituents"]) == 2


def test_partial_portfolio_does_not_block_known_company_valuation():
    fixture = valuation_fixture()
    fixture['holdings'][1]['weight'] = .397
    fixture['holdings_status'] = [{'as_of_date': '2026-09-18', 'input_count': 2, 'valid_count': 2}]
    tools = FixtureTools(fixture)
    assert tools.call('calculate_valuation', {'instrument_id': 'A'})['result']['per'] == 10
    # 99.7% of the weight is observed: the ETF ratio is stated for that share, never as the complete fund.
    weighted = tools.call('calculate_weighted_valuation', {})['result']
    assert weighted['coverage']['weight'] == pytest.approx(.997)
    # Below 70% the whole-ETF ratio is refused while the single-company ratio stays available.
    fixture['holdings'][1]['weight'] = .05
    scarce = FixtureTools(fixture)
    assert scarce.call('calculate_valuation', {'instrument_id': 'A'})['result']['per'] == 10
    with pytest.raises(ValueError, match='below the 70% coverage'):
        scarce.call('calculate_weighted_valuation', {})


def test_missing_financial_quarter_has_actionable_safe_error():
    from edge_analysis_v2.tools.execution import ToolInputError
    fixture = valuation_fixture()
    fixture['financials'].pop()
    # Three quarters cannot make a TTM EPS, but the latest released book value still gives a PBR.
    result = FixtureTools(fixture).call('calculate_valuation', {'instrument_id': 'B'})['result']
    assert result['per'] is None and 'MISSING_QUARTERS' in result['unavailable']['per']
    assert result['pbr'] == 4 and result['bps_period'] == '2026-Q1' and result['periods'] == []
    fixture['financials'] = [row for row in fixture['financials'] if row['instrument_id'] != 'B']
    with pytest.raises(ToolInputError, match='MISSING_QUARTERS'):
        FixtureTools(fixture).call('calculate_valuation', {'instrument_id': 'B'})


@pytest.mark.parametrize("mode", ["missing", "future", "loss", "zero_bps", "gap"])
def test_invalid_financial_domain_never_becomes_neutral(mode):
    fixture = valuation_fixture()
    if mode == "missing":
        fixture["financials"].pop()
    elif mode == "future":
        fixture["financials"][-1]["available_at"] = "2026-09-22T18:00:00+09:00"
    elif mode == "loss":
        fixture["financials"][-1]["eps"] = -100
    elif mode == "zero_bps":
        fixture["financials"][-1]["bps"] = 0
    else:
        fixture["financials"][-1]["period"] = "2026-Q3"
    tools = FixtureTools(fixture)
    # One ratio of company B (40% of the fund) cannot be computed. The other ratio is still the fund's;
    # the affected one is withheld with its coverage and reason, never averaged over the remaining 60%.
    gone, kept = ("pbr", "per") if mode == "zero_bps" else ("per", "pbr")
    result = tools.call("calculate_weighted_valuation", {})["result"]
    assert result["weighted_" + gone] is None and result["weighted_" + kept] is not None
    assert result["ratio_coverage"][gone]["weight"] == pytest.approx(.6)
    single = tools.call("calculate_valuation", {"instrument_id": "B"})["result"]
    expected = "RATIO_NOT_APPLICABLE" if mode in ("loss", "zero_bps") else "MISSING_QUARTERS"
    assert single[gone] is None and expected in single["unavailable"][gone]
    if mode == "gap":
        # The factor screen additionally refuses a quarter that has not ended yet.
        with pytest.raises(ValueError, match="ends after analysis time"):
            tools.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})
        return
    bundle = tools.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]
    assert bundle["weighted_" + gone] is None and bundle["weighted_" + kept] == pytest.approx(result["weighted_" + kept])


def test_derived_q4_is_declared_and_withheld_from_the_bare_card():
    # Policy (2026-09-30): FY−9M Q4 EPS is an approximation. Every consumer path that shows a PER must say so:
    # calculate/weighted (approximate·derived_periods) and the factor screen (eps_approximate·weighted_per_approximate).
    fixture = valuation_fixture()
    for row in fixture["financials"]:
        if row["period"] == "2025-Q4":
            row["eps_derivation"] = "FY_MINUS_9M"
    tools = FixtureTools(fixture)
    single = tools.call("calculate_valuation", {"instrument_id": "A"})["result"]
    assert single["approximate"] is True and single["derived_periods"] == ["2025-Q4"]
    weighted = tools.call("calculate_weighted_valuation", {})["result"]
    assert weighted["approximate"] is True and weighted["derived_constituents"] == ["A", "B"]
    assert weighted["coverage"] == {"constituents": 2, "weight": 1}
    screen = tools.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]
    assert screen["weighted_per"] is not None and screen["weighted_per_approximate"] is True
    company = tools.call("get_instrument_factors", {"instrument_id": "A", "factors": ["valuation"]})["result"]["valuation"]
    assert company["eps_approximate"] is True and company["eps_derived_periods"] == ["2025-Q4"]
    plain = FixtureTools(valuation_fixture())
    assert plain.call("calculate_valuation", {"instrument_id": "A"})["result"]["approximate"] is False
    assert plain.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]["weighted_per_approximate"] is False


def test_ttm_never_double_counts_or_skips_a_quarter():
    # A hole in the latest quarter (BPS blocked) makes PBR unavailable instead of sliding to an older
    # quarter's book value, while PER still uses the same latest four quarters; a re-released quarter is one period.
    fixture = valuation_fixture()
    fixture["financials"].append({"instrument_id": "A", "period": "2025-Q2", "eps": 2.5, "bps": 50, "available_at": "2025-08-15T18:00:00+09:00"})
    for row in fixture["financials"]:
        if row["instrument_id"] == "A" and row["period"] == "2026-Q2":
            row["bps"] = None
    holed = FixtureTools(fixture).call("calculate_valuation", {"instrument_id": "A"})["result"]
    assert holed["pbr"] is None and "MISSING_BPS" in holed["unavailable"]["pbr"]
    assert holed["per"] == 10 and holed["periods"] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"]
    for row in fixture["financials"]:
        if row["instrument_id"] == "A" and row["period"] == "2026-Q1":
            row["eps"] = None
    with pytest.raises(ValueError, match="MISSING_FINANCIAL_VALUE"):
        FixtureTools(fixture).call("calculate_valuation", {"instrument_id": "A"})
    fixture = valuation_fixture()
    fixture["financials"].append({"instrument_id": "A", "period": "2026-Q2", "eps": 9, "bps": 50, "available_at": "2026-09-01T18:00:00+09:00"})
    result = FixtureTools(fixture).call("calculate_valuation", {"instrument_id": "A"})["result"]
    assert result["periods"] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"] and result["ttm_eps"] == 16.5


def test_tools_whose_results_changed_for_alpha_1130_carry_new_definition_versions():
    # WHY: the audit store keys immutable definitions by function:version. Approximation flags, coverage and
    # date-only macro handling change these results, so keeping the old version would file pre- and post-change
    # executions under one definition and make saved evidence non-reproducible.
    versions = {d['function_name']: d['version'] for d in FixtureTools(valuation_fixture()).definitions}
    assert versions['get_issue_evidence'] == 'v2'  # Citation description changed; stored definitions are immutable.
    assert {name: versions[name] for name in ('get_macro_observations', 'compare_macro_observations',
            'calculate_valuation', 'calculate_weighted_valuation', 'get_instrument_factors')} == {
        'get_macro_observations': 'v3', 'compare_macro_observations': 'v2', 'calculate_valuation': 'v4',
        'calculate_weighted_valuation': 'v4', 'get_instrument_factors': 'v4'}


def test_a_weighted_ratio_over_part_of_the_fund_is_an_average_of_that_part_not_a_shrunken_number():
    # WHY: with 80% of the weight observed, summing w*x without dividing by the observed weight would report a
    # PER 20% too low and present it as the fund's. The figure must equal the fully observed one for the same
    # proportions, and say how much of the fund it describes.
    full = FixtureTools(valuation_fixture()).call("calculate_weighted_valuation", {})["result"]
    fixture = valuation_fixture()
    for row in fixture["holdings"]:
        row["weight"] = row["weight"] * 0.8
    tools = FixtureTools(fixture)
    part = tools.call("calculate_weighted_valuation", {})["result"]
    assert part["weighted_per"] == pytest.approx(full["weighted_per"]) and part["weighted_pbr"] == pytest.approx(full["weighted_pbr"])
    assert part["coverage"]["weight"] == pytest.approx(0.8)
    screen = tools.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]
    assert screen["weighted_per"] == pytest.approx(full["weighted_per"]) and screen["observed_weight_ratio"] == pytest.approx(0.8)
    for row in fixture["holdings"]:
        row["weight"] = row["weight"] * 0.5   # 40% observed: not the fund
    below = FixtureTools(fixture)
    with pytest.raises(ValueError, match="below the 70% coverage"):
        below.call("calculate_weighted_valuation", {})
    assert below.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"] is None


def test_quarterly_reports_without_book_value_do_not_block_either_ratio():
    # WHY: first- and third-quarter reports usually carry no BPS. Demanding EPS and BPS in all four quarters
    # refused 8 of the 10 holdings of a real fund, so no outlook could close on a valuation range.
    fixture = valuation_fixture()
    for row in fixture["financials"]:
        if row["period"] in ("2025-Q3", "2026-Q1"):
            row["bps"] = None
    tools = FixtureTools(fixture)
    single = tools.call("calculate_valuation", {"instrument_id": "A"})["result"]
    assert (single["per"], single["pbr"], single["bps_period"], single["unavailable"]) == (10, 2, "2026-Q2", {})
    weighted = tools.call("calculate_weighted_valuation", {})["result"]
    assert weighted["weighted_per"] == 14 and weighted["weighted_pbr"] == 2.8


def test_a_whole_fund_ratio_is_withheld_when_the_companies_that_have_it_cover_too_little():
    fixture = valuation_fixture()
    for row in fixture["financials"]:
        if row["instrument_id"] == "B" and row["period"] == "2025-Q4":
            row["eps"] = None        # B (40%) has no TTM EPS
    result = FixtureTools(fixture).call("calculate_weighted_valuation", {})["result"]
    assert result["weighted_per"] is None and result["ratio_coverage"]["per"] == {"constituents": 1, "weight": .6}
    assert result["weighted_pbr"] == 2.8 and result["missing_constituents"] == []
    for row in fixture["financials"]:
        if row["instrument_id"] == "B" and row["period"] == "2026-Q2":
            row["bps"] = None        # now B has neither ratio
    with pytest.raises(ValueError, match="INSUFFICIENT_RATIO_COVERAGE"):
        FixtureTools(fixture).call("calculate_weighted_valuation", {})


def test_the_two_fund_ratio_tools_agree_and_corrupt_data_stops_them_instead_of_shrinking_the_fund():
    # WHY: one run can call both tools; a card built from one and a sentence citing the other must not disagree.
    fixture = valuation_fixture()
    fixture["holdings"] = [{"instrument_id": n, "weight": w, "as_of_date": "2026-09-18", "available_at": "2026-09-18T18:00:00+09:00"}
                           for n, w in (("A", .5), ("B", .3), ("C", .2))]
    fixture["prices"].append({"instrument_id": "C", "date": "2026-09-18", "close": 300, "available_at": "2026-09-18T18:00:00+09:00"})
    fixture["financials"] += [{"instrument_id": "C", "period": p, "eps": None if p == "2026-Q1" else 2.5, "bps": 50,
                               "available_at": "2026-08-15T18:00:00+09:00"} for p in ("2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")]
    tools = FixtureTools(fixture)
    direct = tools.call("calculate_weighted_valuation", {})["result"]
    bundle = tools.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]
    assert direct["weighted_per"] == bundle["weighted_per"] == 13.75      # A and B hold 80% of the fund
    assert direct["weighted_pbr"] == pytest.approx(bundle["weighted_pbr"]) and bundle["ratio_coverage"] == {"per": .8, "pbr": 1}
    # C's later release has no usable EPS, so it must not date the PER that was computed without it.
    late = "2026-09-20T18:00:00+09:00"
    for row in fixture["financials"]:
        if row["instrument_id"] == "C" and row["period"] == "2026-Q1":
            row["available_at"] = late
    stamped = FixtureTools(fixture).call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]
    assert stamped["ratio_observed_at"]["per"] != late and stamped["ratio_observed_at"]["pbr"] == late
    fixture["prices"].append(dict(fixture["prices"][-1]))                 # a duplicate price row for C
    with pytest.raises(ValueError, match="CONFLICTING_PRICE"):
        FixtureTools(fixture).call("calculate_weighted_valuation", {})


def test_exactly_seventy_percent_of_the_fund_is_enough_despite_how_weights_are_stored():
    fixture = valuation_fixture()
    fixture["holdings"] = [{"instrument_id": n, "weight": w / 100, "as_of_date": "2026-09-18", "available_at": "2026-09-18T18:00:00+09:00"}
                           for n, w in (("A", 1.00), ("B", 2.59), ("C", 66.41), ("D", 30.0))]   # A+B+C sum to 0.6999999999999999 as doubles
    for name in ("C", "D"):
        fixture["prices"].append({"instrument_id": name, "date": "2026-09-18", "close": 100, "available_at": "2026-09-18T18:00:00+09:00"})
    fixture["financials"] += [{"instrument_id": "C", "period": p, "eps": 2.5, "bps": 50, "available_at": "2026-08-15T18:00:00+09:00"}
                              for p in ("2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")]
    result = FixtureTools(fixture).call("calculate_weighted_valuation", {})["result"]
    assert result["weighted_per"] is not None and result["ratio_coverage"]["per"]["weight"] == .7
    assert result["missing_constituents"][0]["instrument_id"] == "D"
