"""Exact cash ledger; target weights use opening, pre-trade NAV and whole shares."""
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_FLOOR, DecimalException
from math import isfinite, sqrt
from statistics import mean, stdev


def number(value):
    try:
        result = D(str(value))
    except DecimalException as exc:
        raise ValueError('Numeric value required') from exc
    if not result.is_finite():
        raise ValueError('Finite number required')
    return result


@dataclass(frozen=True)
class Decision:
    action: str
    weight: D | None = None

    def __post_init__(self):
        if self.action not in ('HOLD', 'TARGET'):
            raise ValueError('Only HOLD or TARGET allowed')
        if self.action == 'HOLD' and self.weight is not None:
            raise ValueError('HOLD cannot contain a target')
        if self.action == 'TARGET' and (self.weight is None or not 0 <= number(self.weight) <= 1):
            raise ValueError('Target weight must be in [0,1]')


@dataclass(frozen=True)
class Portfolio:
    cash: D
    shares: int
    receivable: D = D('0')

    def __post_init__(self):
        if number(self.cash) < 0 or number(self.receivable) < 0 or type(self.shares) is not int or self.shares < 0:
            raise ValueError('Cash-only long portfolio required')

    def nav(self, price):
        return self.cash + self.shares * number(price) + self.receivable


def execute(portfolio, decision, opening, fee_bps, slippage_bps):
    opening, fee_bps, slippage_bps = map(number, (opening, fee_bps, slippage_bps))
    if opening <= 0 or not 0 <= fee_bps < 10000 or not 0 <= slippage_bps < 10000:
        raise ValueError('Invalid execution prices/costs')
    empty = dict(quantity=0, price=str(opening), notional='0', fee='0', slippage='0')
    if decision.action == 'HOLD':
        return portfolio, empty
    # Receivables count in NAV but cannot finance purchases before payment.
    desired = int((portfolio.nav(opening) * number(decision.weight) / opening).to_integral_value(rounding=ROUND_FLOOR))
    quantity = desired - portfolio.shares
    if quantity == 0:
        return portfolio, empty
    price = opening * (1 + (1 if quantity > 0 else -1) * slippage_bps / 10000)
    if quantity > 0:
        affordable = int(portfolio.cash / (price * (1 + fee_bps / 10000)))
        quantity = min(quantity, affordable)
    fee = abs(quantity) * price * fee_bps / 10000
    cash = portfolio.cash - quantity * price - fee
    result = Portfolio(cash, portfolio.shares + quantity, portfolio.receivable)
    return result, dict(quantity=quantity, price=str(price), notional=str(abs(quantity) * price),
                        fee=str(fee), slippage=str(abs(quantity) * abs(price - opening)))


def metrics(nav, notionals, calendar_days, annual_risk_free=0.0):
    if len(nav) != len(notionals) + 1 or len(calendar_days) != len(notionals):
        raise ValueError('Initial NAV and every daily observation required')
    nav = list(map(float, nav))
    if any(not isfinite(x) or x <= 0 for x in nav):
        raise ValueError('Positive finite NAV required')
    returns = [b/a-1 for a,b in zip(nav,nav[1:])]
    elapsed = sum(calendar_days)
    peak, drawdowns = nav[0], []
    for value in nav[1:]:
        peak = max(peak, value)
        drawdowns.append(value/peak-1)
    volatility = stdev(returns) if len(returns) > 1 else 0
    sharpe = (mean(returns) - annual_risk_free/252)/volatility*sqrt(252) if volatility > 1e-12 else None
    return dict(cumulative_return=nav[-1]/nav[0]-1,
                annual_return=(nav[-1]/nav[0])**(365.25/elapsed)-1 if elapsed else None,
                turnover=sum(float(n)/v for n,v in zip(notionals,nav)),
                sharpe=sharpe, sharpe_note=None if sharpe is not None else 'Insufficient or constant daily returns',
                mdd=min(drawdowns,default=0), drawdowns=drawdowns)
