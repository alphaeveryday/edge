"""A window's return split into the market's share and the rest, by the market model."""
from edge_analysis_v2.tools.execution import ToolInputError, ToolDataUnavailableError
from edge_analysis_v2.tools.fixture_data.flow import constituent_ids
from edge_analysis_v2.tools.fixture_data.common import available, decimal, holdings, instant, number, table

# ponytail: a KOSPI 200 tracker stands in for the market because market_series holds no index values;
# read the index itself once its daily values are ingested.
MARKET_PROXY = {'instrument_id': '069500', 'name': 'KODEX 200'}
BETA_RETURNS, MIN_BETA_RETURNS = 60, 20
FORMULA_LATEX = r'r=100(P_e/P_s-1);\ \beta=\frac{\mathrm{cov}(r_i,r_m)}{\mathrm{var}(r_m)};\ M=\beta r_m;\ A=r-M'


def _closes(rows, cutoff):
    return {r['date']: decimal(r['close']) for r in available(rows, cutoff) if r['date'] < cutoff.date().isoformat()}


def _percent(value):
    return number(round(100 * value, 4))


def _sessions(market, own, until=None):
    """Pairs of sessions that follow each other and have a close on both sides.

    With a day missing from the market series, Monday to Wednesday was taken as one daily return: 21% shown
    for a 10% day, and counted among the daily returns of the beta.
    """
    calendar = sorted(d for d in set(market) | set(own) if until is None or d <= until)
    return [(a, b) for a, b in zip(calendar, calendar[1:]) if all(d in series for d in (a, b) for series in (market, own))]


def _measure(market, own, start_date, end_date):
    """One instrument over one window; beta from up to sixty daily returns ending on the start date."""
    if start_date not in own or end_date not in own:
        return None
    change, market_return = own[end_date] / own[start_date] - 1, market[end_date] / market[start_date] - 1
    days = _sessions(market, own, start_date)[-BETA_RETURNS:]
    x = [market[b] / market[a] - 1 for a, b in days]
    y = [own[b] / own[a] - 1 for a, b in days]
    result = {'return_pct': _percent(change), 'beta': None, 'market_share_pct': None,
              'excess_return_pct': None, 'beta_observations': len(x)}
    if len(x) >= MIN_BETA_RETURNS:
        mean_x, mean_y = sum(x) / len(x), sum(y) / len(y)
        variance = sum((v - mean_x) ** 2 for v in x)
        if variance > 0:
            beta = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y)) / variance
            result |= {'beta': number(round(beta, 4)), 'market_share_pct': _percent(beta * market_return),
                       'excess_return_pct': _percent(change - beta * market_return)}
    return result


def _series(fixture, target, cutoff):
    return _closes([r for r in fixture.get('prices', []) if r['instrument_id'] == target], cutoff)


def window(fixture, start_date, end_date):
    """Measure the ETF and every constituent from the start close to the end close.

    Raises:
        ToolInputError: Either date has no market close, or the dates are not in order.
    """
    cutoff = instant(fixture['context']['analysis_at'])
    market = _closes(fixture.get('market_prices', []), cutoff)
    if not market:
        raise ToolDataUnavailableError('No completed market prices in this snapshot. Do not retry; use other available factors or external evidence.')
    if not start_date < end_date or start_date not in market or end_date not in market:
        raise ToolInputError('start_date와 end_date는 종가가 확정된 거래일이어야 하고 start_date가 앞서야 합니다. '
                             f'확정된 거래일은 {min(market, default="없음")}부터 {max(market, default="없음")}까지입니다.')
    measure = lambda target: _measure(market, _series(fixture, target, cutoff), start_date, end_date)
    etf = fixture['context']['etf_code']
    names = {i['instrument_id']: i['name'] for i in fixture.get('instruments', [])}
    portfolio = holdings(fixture, require_complete=False)
    rows = [{'instrument_id': h['instrument_id'], 'name': names.get(h['instrument_id']), 'weight': h['weight'], **measured,
             # Percentage points of the ETF's own return that this holding accounts for at its current weight.
             'contribution_pct': number(round(decimal(h['weight']) * decimal(measured['return_pct']), 4))}
            for h in portfolio['holdings'] if (measured := measure(h['instrument_id']))]
    rows.sort(key=lambda r: (r['excess_return_pct'] is None, r['excess_return_pct'] or 0))
    measured = {r['instrument_id'] for r in rows}
    # A holding listed after the start date, or with a gap in its prices, was left out without a word.
    missing = [{'instrument_id': h['instrument_id'], 'name': names.get(h['instrument_id']), 'weight': h['weight']}
               for h in portfolio['holdings'] if h['instrument_id'] not in measured]
    return {'start_date': start_date, 'end_date': end_date,
            'market': MARKET_PROXY | {'return_pct': _percent(market[end_date] / market[start_date] - 1)},
            'etf': {'instrument_id': etf, **(measure(etf) or {})},
            'holdings_as_of': portfolio['as_of_date'],
            **({'not_measured': {'reason': '구간의 시작일이나 종료일에 종가가 없는 구성종목입니다. 수익률과 기여도를 알 수 없습니다.',
                                 'constituents': missing}} if missing else {}),
            'constituents': table(rows, ['instrument_id', 'name', 'weight', 'return_pct', 'beta', 'market_share_pct',
                                         'excess_return_pct', 'contribution_pct', 'beta_observations'])}


def daily(fixture, lookback_days, instrument_id=None):
    """Each of the latest sessions measured as its own one-day window, so a day here equals window() on that day.

    Raises:
        ToolInputError: The instrument is neither the ETF nor a constituent with prices.
    """
    cutoff = instant(fixture['context']['analysis_at'])
    market = _closes(fixture.get('market_prices', []), cutoff)
    target = instrument_id or fixture['context']['etf_code']
    if not isinstance(target, str) or target not in {fixture['context']['etf_code']} | constituent_ids(fixture):
        raise ToolInputError('Choose this ETF or a current constituent from get_etf_holdings.')
    own = _series(fixture, target, cutoff)
    days = _sessions(market, own)[-lookback_days:]
    if not days:
        raise ToolDataUnavailableError('No paired completed prices for this target in the snapshot. Do not retry; use other available factors or external evidence.')
    rows = [{'date': b, 'market_return_pct': _percent(market[b] / market[a] - 1), **_measure(market, own, a, b)}
            for a, b in days]
    return {'instrument_id': target, 'market': MARKET_PROXY,
            **table(rows, ['date', 'return_pct', 'market_return_pct', 'market_share_pct', 'excess_return_pct', 'beta'])}
