"""Released financial observations and explicitly conditional valuation math."""
from copy import deepcopy

from .common import available, decimal, holdings, instant, number, table

COLUMNS = ['observation_id', 'instrument_id', 'metric', 'value', 'unit', 'period',
           'kind', 'author', 'published_at', 'available_at', 'news_id']


def visible(fixture):
    """Select released observations and reject ambiguous source identities."""
    cutoff = instant(fixture['context']['analysis_at'])
    rows = available(fixture.get('financial_observations', []), cutoff, 'published_at')
    if len({r['observation_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate financial observation ID')
    for row in rows:
        if any(row.get(key) is None for key in COLUMNS) or row['kind'] not in ('actual', 'estimate'):
            raise ValueError('complete financial observation and kind required')
        decimal(row['value'])
    return sorted(rows, key=lambda r:(instant(r['published_at']), r['observation_id']))


def read(fixture, instrument_id):
    """Return source values without silently replacing old forecasts.

    Args:
        fixture: Observations bounded by one analysis cutoff.
        instrument_id: A current ETF constituent.

    Returns:
        Table of actuals and forecasts with issuer, period and source identity.
    """
    if instrument_id not in {r['instrument_id'] for r in holdings(fixture)['holdings']}:
        raise ValueError('instrument outside current holdings')
    return table([r for r in visible(fixture) if r['instrument_id'] == instrument_id], COLUMNS)


def select(fixture, identity):
    """Resolve one released source row rather than accepting an invented number."""
    row = next((r for r in visible(fixture) if r['observation_id'] == identity), None)
    if row is None:
        raise ValueError('financial observation unavailable at analysis time')
    read(fixture, row['instrument_id'])
    return deepcopy(row)


def compare(fixture, previous_id, current_id):
    """Compare same-period sources; retain estimates as estimates.

    Args:
        fixture: Time-bounded source observations.
        previous_id: Earlier source observation ID.
        current_id: Later source observation ID for the same financial quantity.

    Returns:
        Source rows, arithmetic difference and relative change when defined.
    """
    previous, current = select(fixture, previous_id), select(fixture, current_id)
    if previous_id == current_id or any(previous[k] != current[k] for k in ('instrument_id','metric','unit','period')):
        raise ValueError('distinct same-instrument metric, unit and period required')
    if instant(previous['published_at']) > instant(current['published_at']):
        raise ValueError('previous observation was published after current')
    before, after = decimal(previous['value']), decimal(current['value'])
    return {'previous':previous, 'current':current, 'difference':number(after-before),
            'percent_change':number(100*(after-before)/before) if before > 0 else None}


def valuation_range(fixture, eps_id, per_low, per_high):
    """Compute a conditional equity range, not an ETF target or probability.

    Args:
        fixture: Financial sources and prices available at the analysis cutoff.
        eps_id: Annual KRW EPS source observation ID.
        per_low: Positive lower multiple assumption, justified by the analyst.
        per_high: Upper multiple assumption at least equal to the lower.

    Returns:
        Source EPS, assumptions, observed price and conditional prices/returns.
    """
    eps = select(fixture, eps_id)
    low, high, value = decimal(per_low), decimal(per_high), decimal(eps['value'])
    if eps['metric'] != 'eps' or eps['unit'] != 'KRW_per_share' or not (len(eps['period']) == 4 and eps['period'].isdigit()):
        raise ValueError('annual KRW EPS observation required')
    if not 0 < low <= high or value <= 0:
        raise ValueError('positive EPS and ordered positive multiples required')
    cutoff = instant(fixture['context']['analysis_at'])
    rows = sorted([r for r in available(fixture.get('prices', []), cutoff)
                   if r['instrument_id'] == eps['instrument_id'] and r['date'] < cutoff.date().isoformat()],
                  key=lambda r:r['date'])
    if not rows or len({r['date'] for r in rows}) != len(rows) or decimal(rows[-1]['close']) <= 0:
        raise ValueError('unique positive completed closing price required')
    price = decimal(rows[-1]['close'])
    return {'eps_observation':eps, 'per_assumptions':{'low':number(low),'high':number(high)},
            'current_price':number(price), 'price_date':rows[-1]['date'],
            'current_per':number(price/value),
            'price_low':number(value*low), 'price_high':number(value*high),
            'return_low_pct':number(100*(value*low/price-1)),
            'return_high_pct':number(100*(value*high/price-1))}
