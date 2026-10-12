"""A common-factor view preserves scope, partial coverage and source time."""
import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools, make_fixture


def read(fixture, instrument_id='091160', **kwargs):
    return FixtureTools(fixture).call('get_instrument_factors', {'instrument_id': instrument_id, **kwargs})['result']


def test_default_is_four_factors_and_subset_does_not_fetch_unrequested_data():
    fixture = make_fixture()
    result = read(fixture)
    assert {'chart', 'flow', 'valuation', 'macro'} <= result.keys()
    assert result['chart']['price_krw'] == fixture['price_snapshots'][-1]['price']
    assert result['chart']['volume_comparison'] is None
    assert result['flow']['scope'] == result['valuation']['scope'] == 'holdings_weighted'
    assert len(result['flow']['history']['rows']) == 30
    assert all(len(series['rows']) == 21 for series in result['macro']['series'])
    partial = read(fixture, '000660', factors=['valuation'])
    assert not {'chart', 'flow', 'macro'} & partial.keys()
    assert partial['valuation']['scope'] == 'instrument'
    assert partial['valuation']['per'] == pytest.approx(partial['valuation']['price_krw'] / 6000)


def test_factor_selection_is_optional_but_invalid_arguments_are_rejected():
    tools = FixtureTools(make_fixture())
    functions = {entry['function']['name']: entry['function'] for entry in tools.schemas}
    assert not {'get_factor_metrics', 'get_chart_metrics'} & functions.keys()
    assert functions['get_instrument_factors']['parameters']['required'] == ['instrument_id']
    for factors in ([], ['unknown'], ['flow', 'flow'], 'flow', None):
        with pytest.raises(ValueError):
            read(make_fixture(), factors=factors)
    with pytest.raises(ValueError):
        read(make_fixture(), 'unknown')
    with pytest.raises(ValueError):
        tools.call('calculate_investor_flow', {'instrument_id': '000660'})


def test_missing_factor_and_short_chart_do_not_erase_available_values():
    fixture = make_fixture()
    fixture['macro'] = []
    fixture['policy_decisions'] = []
    fixture['prices'] = [row for row in fixture['prices'] if row['date'] >= fixture['trading_dates'][-20]]
    result = read(fixture)
    assert result['macro'] is None
    assert result['unavailable']['macro']
    assert result['chart']['ma20_distance_pct'] is not None
    assert result['chart']['ma60_direction'] is None
    assert result['chart']['distance_from_52w_closing_high_pct'] is None


def test_missing_flow_session_truncates_history_instead_of_splicing_dates():
    fixture = make_fixture()
    gap = fixture['trading_dates'][-5]
    fixture['flow'] = [r for r in fixture['flow'] if r['date'] != gap]
    result = read(fixture, '000660', factors=['flow'])['flow']
    assert len(result['history']['rows']) == 4
    assert result['history']['rows'][0][0] == fixture['trading_dates'][-4]


def test_future_macro_and_financial_releases_do_not_leak_into_snapshot():
    fixture = make_fixture()
    before = read(fixture, '000660')
    fixture['macro'].append(dict(fixture['macro'][-1], value=99999, observed_at='2026-09-22T10:00:00+09:00', available_at='2026-09-22T10:00:00+09:00'))
    fixture['financials'].append(dict(fixture['financials'][0], eps=999999, available_at='2026-09-22T10:00:00+09:00'))
    assert read(fixture, '000660') == before


def test_duplicate_macro_observation_is_an_error_not_an_unavailable_factor():
    fixture = make_fixture()
    fixture['macro'].append(dict(fixture['macro'][-1]))
    with pytest.raises(ValueError, match='duplicate'):
        read(fixture)


def test_loss_making_company_keeps_book_value_and_other_factors():
    fixture = make_fixture()
    for row in fixture['financials']:
        if row['instrument_id'] == '000660':
            row['eps'] = -100
    result = read(fixture, '000660')
    assert result['valuation']['per'] is None
    assert result['valuation']['ttm_eps_krw'] == -400
    assert result['valuation']['pbr'] > 0
    assert result['chart']['price_krw'] != read(fixture)['chart']['price_krw']
    weighted = read(fixture, factors=['valuation'])['valuation']
    assert weighted['weighted_per'] is None
    assert weighted['weighted_pbr'] > 0


def test_partial_and_full_queries_share_values_and_preserve_source_fixture():
    from copy import deepcopy
    fixture = make_fixture()
    original = deepcopy(fixture)
    full = read(fixture)
    for factor in ('chart', 'flow', 'valuation', 'macro'):
        assert read(fixture, factors=[factor])[factor] == full[factor]
    assert fixture == original


def test_weighted_flow_uses_the_whole_portfolio_without_renormalizing():
    fixture = make_fixture()
    result = read(fixture, factors=['flow'])['flow']
    day = result['history']['rows'][-1][0]
    from edge_analysis_v2.tools.fixture_data.common import holdings
    weights = holdings(fixture, day)['holdings']
    expected = sum(w['weight'] * next(r['net_amount_krw'] for r in fixture['flow']
        if r['date'] == day and r['instrument_id'] == w['instrument_id'] and r['investor'] == 'foreign')
        for w in weights)
    assert result['history']['rows'][-1][1] == pytest.approx(expected)
    fixture['flow'] = [r for r in fixture['flow'] if not (r['date'] == day and r['instrument_id'] == weights[0]['instrument_id'])]
    missing = read(fixture, factors=['flow', 'macro'])
    assert missing['flow']['history']['rows'] == []
    assert missing['flow']['etf_units_change_20d_pct'] == 2
    assert missing['macro'] is not None


def test_history_columns_match_initial_data_and_macro_lookup():
    tools = FixtureTools(make_fixture())
    initial = tools.initial_input()
    result = read(make_fixture(), '000660')
    assert result['flow']['history']['columns'] == initial['flow']['000660']['columns']
    for series in result['macro']['series']:
        assert series == initial['macro'][series['series']]


@pytest.mark.parametrize('missing', ['price_day', 'holdings', 'etf_prices'])
def test_unavailable_sources_do_not_block_unrelated_factors(missing):
    fixture = make_fixture()
    if missing == 'price_day':
        fixture['prices'] = [r for r in fixture['prices'] if not (r['instrument_id'] == '091160' and r['date'] == fixture['trading_dates'][-10])]
    elif missing == 'holdings':
        fixture['holdings'] = []
    else:
        fixture['prices'] = [r for r in fixture['prices'] if r['instrument_id'] != '091160']
        fixture['price_snapshots'] = []
    result = read(fixture)
    assert result['macro'] is not None
    if missing == 'holdings':
        assert result['flow']['weighted_foreign_net_amount_20d'] is None
        assert result['valuation']['weighted_per'] is None
        assert result['chart'] is not None
    else:
        assert result['flow'] is not None
        assert result['valuation']['weighted_per'] is not None
        if missing == 'price_day':
            assert result['chart']['ma20_distance_pct'] is None
        else:
            assert result['valuation']['distribution_yield_12m_pct'] is None


def test_policy_schedule_survives_without_macro_time_series():
    fixture = make_fixture()
    fixture['macro'] = []
    fixture['policy_decisions'] = [{'decision_at': '2026-09-25T09:00:00+09:00', 'available_at': '2026-09-01T09:00:00+09:00', 'subject': 'BOK'}]
    result = read(fixture, factors=['macro'])['macro']
    assert result['series'] == []
    assert result['next_policy_decision']['subject'] == 'BOK'


@pytest.mark.parametrize('turnover', [-1, 0])
def test_invalid_turnover_differs_from_a_zero_denominator(turnover):
    fixture = make_fixture()
    for row in fixture['prices']:
        if row['instrument_id'] == '091160':
            row['turnover'] = turnover
    if turnover < 0:
        with pytest.raises(ValueError, match='negative turnover'):
            read(fixture)
    else:
        result = read(fixture)
        assert result['chart']['turnover_ratio_previous_day'] is None
        assert result['chart']['momentum_index'] is not None


def test_unreadable_past_snapshot_ends_weighted_flow_history_instead_of_failing_the_call(caplog):
    # WHY(ALPHA-1162): 전망은 이 도구를 모델 전에 강제로 부르고, 가중 수급은 30거래일 각각의 과거 스냅샷을 읽는다.
    # 과거 하루가 읽히지 않는다고 도구가 실패하면 그 하루 때문에 전망 전체가 시작하지 못한다 — 스냅샷이 없는 날처럼
    # 그날에서 이력을 멈추고, 멈춘 날은 운영 로그에 남긴다.
    fixture = make_fixture()
    def snapshot(day, weights):
        return [{'instrument_id': code, 'weight': weight, 'as_of_date': day, 'available_at': day + 'T08:00:00+09:00'}
                for code, weight in zip(('000660', '005930'), weights)]
    fixture['holdings'] += snapshot('2026-09-10', (.6, .41)) + snapshot('2026-09-15', (.6, .4))  # 09-10: 합 1.01, 설명할 행 없음
    flow = read(fixture, factors=['flow'])['flow']
    assert [row[0] for row in flow['history']['rows']] == ['2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']
    assert 'day=2026-09-14' in caplog.text


def test_weighted_flow_states_the_share_of_the_fund_it_covers_and_survives_unpublished_holdings():
    fixture = make_fixture()
    flow = read(fixture, factors=['flow'])['flow']
    assert flow['scope'] == 'holdings_weighted' and flow['observed_weight_ratio'] == 1
    # Holdings exist but none was published by the analysis time: other factors must still be returned.
    late = make_fixture()
    for row in late['holdings']:
        row['available_at'] = '2099-01-01T00:00:00+09:00'
    result = read(late)
    assert result['chart'] is not None and result['macro'] is not None
