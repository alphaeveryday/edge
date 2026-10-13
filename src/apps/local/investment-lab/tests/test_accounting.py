from decimal import Decimal as D
import pytest
from investment_lab.accounting import Portfolio, execute, metrics, Decision
from investment_lab.accounting import number


def test_hold_preserves_units_while_target_rebalances():
    p = Portfolio(D('500'), 5)
    assert execute(p, Decision('HOLD'), D('200'), D('0'), D('0'))[0] == p
    changed, trade = execute(p, Decision('TARGET', D('.5')), D('200'), D('0'), D('0'))
    assert changed.shares == 3
    assert changed.cash == 900
    assert trade['quantity'] == -2


def test_full_investment_cannot_borrow_to_pay_fees_or_slippage():
    p, trade = execute(Portfolio(D('1000'), 0), Decision('TARGET', D('1')), D('100'), D('100'), D('100'))
    assert p.shares == 9
    assert p.cash == D('81.91')
    assert trade['fee'] == '9.09'


@pytest.mark.parametrize('weight', ['-0.1', '1.1', 'NaN', 'Infinity'])
def test_no_short_or_leveraged_decisions(weight):
    with pytest.raises(ValueError):
        Decision('TARGET', D(weight))


def test_drawdown_includes_initial_capital_and_constant_returns_have_no_sharpe():
    report = metrics([100, 90, 81], [0, 0], [1, 1])
    assert report['mdd'] == pytest.approx(-.19)
    assert report['sharpe'] is None
    assert report['cumulative_return'] == pytest.approx(-.19)


def test_turnover_counts_both_buys_and_sells_over_prior_nav():
    report = metrics([100, 110, 100], [50, 55], [1, 1])
    assert report['turnover'] == pytest.approx(1)


@pytest.mark.parametrize('value',[None,'',True,'not-a-number'])
def test_missing_cost_assumptions_are_explicit_validation_errors(value):
    with pytest.raises(ValueError):
        number(value)
