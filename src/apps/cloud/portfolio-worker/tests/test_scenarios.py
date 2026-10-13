import pytest

from edge_portfolio.scenarios import build_scenarios


@pytest.mark.parametrize('text,expected', [('원금은 1000만원입니다. 5년 뒤 얼마인가요?', 10000000),
    ('원금은 1000만원입니다. 5년 뒤 원금과 이자는 얼마인가요?', 10000000),
    ('제 원금은 1억 5000만원이에요.', 150000000), ('원금은 0원입니다.', 0),
    ('원금은 약 1000만원입니다.', None), ('원금은 1000만원이면 좋겠어요.', None),
    ('원금은 1000만원입니다. 원금은 2000만원입니다.', None), ('원금은 1000만원입니다. 취소합니다.', None),
    ('원금은 1000만원입니다. 원금은 2000만원으로 정정합니다.', None),
    ('원금은 1000만원입니다. 원금은 확인 필요합니다.', None),
    ('원금은 1000만원입니다. 현재 평가금액은 모릅니다.', None),
    ('원금은 1000만원입니다. 원금이 불명확합니다.', None)])
def test_followup_principal_requires_one_exact_standalone_statement(text, expected):
    from edge_portfolio.scenarios import stated_principal
    assert stated_principal(text) == expected


def test_known_principal_allows_scenarios_without_a_return_forecast():
    result = build_scenarios(1000000, {'annual_returns': [0], 'years': [5, 10, 15]}, monthly_krw=100000)
    assert [row['future_krw'] for row in result['rows']] == ['7000000', '13000000', '19000000']
    assert all(row['future_krw'] == row['paid_krw'] for row in result['rows'])
    assert '예측하거나 보장하지' in result['assumptions'][1]


def test_compound_growth_does_not_confuse_percent_with_money_or_linear_growth():
    result = build_scenarios(1000000, {'annual_returns': [10, -10], 'years': [2]})
    assert [row['future_krw'] for row in result['rows']] == ['1210000', '810000']
    assert build_scenarios(None, {})['status'] == 'needs_input'


def test_one_time_addition_is_not_repeated_each_month():
    result = build_scenarios(1000000, {'annual_returns': [0], 'years': [5]}, additional_krw=2000000)
    assert result['rows'][0]['future_krw'] == '3000000'


@pytest.mark.parametrize('args', [{'years': [0]}, {'years': [True]}, {'annual_returns': [float('nan')]}, {'principal_krw': 999}])
def test_model_cannot_supply_principal_or_unbounded_scenario_arguments(args):
    with pytest.raises(ValueError):
        build_scenarios(1000000, args)
def test_maximum_supported_horizon_and_return_do_not_overflow_decimal_precision():
    from edge_portfolio.scenarios import build_scenarios
    result = build_scenarios(20 * 10**13, {'annual_returns': [100], 'years': [50]})
    assert int(result['rows'][0]['future_krw']) == 20 * 10**13 * 2**50
