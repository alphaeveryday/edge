"""Common factor observations for one instrument at a fixed analysis cutoff."""
from calendar import monthrange
from datetime import date
import re

from edge_analysis_v2.tools.fixture_data import chart, etf, macro
from edge_analysis_v2.tools.fixture_data.common import MIN_WEIGHT_COVERAGE, available, covers_whole_etf, decimal, holdings, instant, number
from edge_analysis_v2.tools.fixture_data.valuation import covered_weight

FORMULA_LATEX = (
    r"M_n=\frac1n\sum_{j=0}^{n-1}C_j;\ d_{20}=100(P/M_{20}-1);\ "
    r"N_{20}=\sum_{d=D-19}^{D}[C_d>\max(C_{d-20},\ldots,C_{d-1})];\ "
    r"d_{52w}=100(P/\max C_{[T-364d,T)}-1);\ "
    r"A_{ratio}=A_D/(\frac1{20}\sum_{j=1}^{20}A_{D-j});\ "
    r"TR_d=\max(H_d-L_d,|H_d-C_{d-1}|,|L_d-C_{d-1}|);\ "
    r"ATR_d=(13ATR_{d-1}+TR_d)/14;\ ATR\%=100ATR_D/C_D;\ "
    r"R_{20}=100(X_D/X_{D-20}-1);\ "
    r"PER_i=P_i/\sum_{q=1}^{4}EPS_{i,q};\ PBR_i=P_i/BPS_i;\ "
    r"\bar x=\sum_iw_ix_i;\ F_d=\sum_iw_{i,d}f_{i,d};\ S_{20}=\sum_{d=D-19}^{D}F_d;\ "
    r"streak=\max\{k:\forall j\in[0,k),F_{D-j}>0\}"
    r";\ distribution\%=100\sum_{T-1y<t\le T}cash_t/P;\ units\%=100(U_D/U_{D-20}-1)"
)
FORMULA_LATEX += (r";\ G=RMA_{14}(\max(\Delta C,0));\ L=RMA_{14}(\max(-\Delta C,0));\ "
    r"RSI=100G/(G+L);\ B=100(H_{14}-P)/(H_{14}-L_{14})")

FACTORS = ('chart', 'flow', 'valuation', 'macro')


def read(fixture, instrument_id, factors=FACTORS):
    """Read selected factors without interpreting their investment importance.

    Args:
        fixture: Raw observations and fixed analysis context.
        instrument_id: ETF or constituent in the available instrument universe.
        factors: Nonempty unique selection; omission returns all four factors.

    Returns:
        Available factor objects with explicit nulls for unavailable observations.

    Raises:
        ValueError: Unsupported arguments or inconsistent source observations.
    """
    if not isinstance(factors, (list, tuple)) or not factors or any(not isinstance(f, str) for f in factors) or len(set(factors)) != len(factors) or not set(factors) <= set(FACTORS):
        raise ValueError('choose unique supported factors')
    instruments = {row['instrument_id']: row for row in fixture.get('instruments', [])}
    scope = {fixture['context']['etf_code']} | set(instruments) | {row['instrument_id'] for row in fixture.get('holdings', [])}
    if not isinstance(instrument_id, str) or instrument_id not in scope:
        raise ValueError('unknown instrument_id')
    result = {'instrument_id': instrument_id, 'instrument_name': instruments.get(instrument_id, {}).get('name', instrument_id), 'analysis_at': fixture['context']['analysis_at']}
    readers = {'chart': chart_data, 'flow': flow_data, 'valuation': valuation_data, 'macro': macro_data}
    unavailable = {}
    for factor in factors:
        value, reason = readers[factor](fixture, instrument_id)
        result[factor] = value
        if reason:
            unavailable[factor] = reason
    if unavailable:
        result['unavailable'] = unavailable
    return result


def chart_data(fixture, instrument_id):
    """Read each calculable chart value without discarding a shorter history."""
    target = fixture | {'context': fixture['context'] | {'etf_code': instrument_id}}
    cutoff = instant(fixture['context']['analysis_at'])
    eligible = [r for r in available(fixture.get('prices', []), cutoff) if r['instrument_id'] == instrument_id and r['date'] < cutoff.date().isoformat()]
    if len({r['date'] for r in eligible}) != len(eligible):
        raise ValueError('duplicate price history')
    expected = sorted(d for d in fixture['trading_dates'] if d < cutoff.date().isoformat())
    if len(set(expected)) != len(expected):
        raise ValueError('duplicate trading date')
    by_date = {r['date']: r for r in eligible}
    for row in eligible:
        if row.get('turnover') is not None and decimal(row['turnover']) < 0:
            raise ValueError('negative turnover')
        chart.validate_price(row)
        if row['date'] not in expected:
            raise ValueError('invalid daily price')
    rows = []
    for day in reversed(expected):
        if day not in by_date:
            break
        rows.append(by_date[day])
    if not rows:
        return None, 'No contiguous completed price history ends at the latest session.'
    rows.reverse()
    target = target | {'prices': rows}
    points = chart.snapshots(target)
    latest = points[-1] if points else rows[-1]
    result = {key: None for key in chart.METRICS}
    result.update(price_krw=number(latest['price'] if points else latest['close']), observed_at=latest['observed_at'] if points else latest['available_at'], finalized_through=rows[-1]['date'], finalized_observed_at=rows[-1]['available_at'])
    count = len(rows)
    requirements = {'ma20_distance_pct': count + bool(points) >= 20, 'ma60_direction': count + bool(points) >= 61,
                    'new_closing_high_count_20d': count >= 40, 'distance_from_52w_closing_high_pct': (cutoff.date() - date.fromisoformat(rows[0]['date'])).days >= 364,
                    'turnover_ratio_previous_day': count >= 21 and all(r.get('turnover') is not None for r in rows[-21:]),
                    'atr14_pct': count >= 15 and all(r.get('high') is not None and r.get('low') is not None for r in rows)}
    for key, sufficient in requirements.items():
        if sufficient:
            cards = chart.metrics(target, [key]) if key != 'turnover_ratio_previous_day' or sum(decimal(r['turnover']) for r in rows[-21:-1]) > 0 else []
            for card in cards:
                result[key] = card['value']
    directions = {'상승': 'rising', '하락': 'falling', '횡보': 'flat'}
    result['ma60_direction'] = directions.get(result['ma60_direction'], result['ma60_direction'])
    indicators = chart.indicator_values(rows, points[-1] if points else None) if count >= 15 else {}
    result.update(momentum_index=indicators.get('momentum'), bottom_index=indicators.get('bottom'), volume_comparison=None)
    observations = [chart.indicator_values(rows, point) for point in points] if count >= 15 else []
    if not points and count >= 15:
        observations = [chart.indicator_values(rows[:end]) for end in range(max(15, count-4), count+1)]
    result['indicator_history'] = {'columns': ['at', 'momentum_index', 'bottom_index'], 'rows': [[r['observed_at'], r['momentum'], r['bottom']] for r in observations]}
    return result, None


def _below_coverage(fixture):
    """Whether published holdings exist but cover too little of the fund for a whole-ETF figure."""
    try:
        return not covers_whole_etf(holdings(fixture, require_complete=False))
    except ValueError:
        return False  # nothing published at the cutoff: the day loop reports that case itself


def flow_data(fixture, instrument_id):
    """Return up to thirty contiguous finalized sessions without filling gaps."""
    context = fixture['context']
    cutoff, end = instant(context['analysis_at']), context['flow_as_of_date']
    if date.fromisoformat(end) >= cutoff.date():
        raise ValueError('flow cutoff must be a completed prior day')
    dates = sorted(d for d in fixture['trading_dates'] if d <= end)[-30:]
    if len(set(fixture['trading_dates'])) != len(fixture['trading_dates']):
        raise ValueError('duplicate trading date')
    if not dates or dates[-1] != end:
        return None, 'Finalized flow date is absent from the trading calendar.'
    weighted = instrument_id == context['etf_code']
    if weighted and _below_coverage(fixture):
        return None, 'Observed constituent weights are below 70%; whole-ETF weighted flow unavailable.'
    source = available(fixture.get('flow', []), cutoff)
    investors = ('foreign', 'institution', 'individual')
    history, observed, covered = [], [], []
    for day in reversed(dates):
        if weighted and not any(r['as_of_date'] <= day for r in available(fixture.get('holdings', []), min(cutoff, instant(day + 'T23:59:59.999999+09:00')))):
            break
        portfolio = holdings(fixture, day, require_complete=False) if weighted else None
        if portfolio and not covers_whole_etf(portfolio):
            break
        weights = portfolio['holdings'] if weighted else [{'instrument_id': instrument_id, 'weight': 1}]
        values = []
        day_times = []
        complete = True
        for investor in investors:
            total = decimal(0)
            for weight in weights:
                if decimal(weight['weight']) == 0:
                    continue
                rows = [r for r in source if r['date'] == day and r['instrument_id'] == weight['instrument_id'] and r['investor'] == investor]
                if len(rows) > 1:
                    raise ValueError('duplicate investor flow observation')
                if not rows or not rows[0].get('finalized'):
                    complete = False
                    break
                if type(rows[0]['net_amount_krw']) is not int:
                    raise ValueError('source flow must be integer KRW')
                total += decimal(weight['weight']) * rows[0]['net_amount_krw']
                day_times.append(rows[0]['available_at'])
            values.append(number(total))
        if not complete:
            break
        history.append([day] + values)
        if portfolio:
            covered.append(decimal(portfolio['observed_weight_ratio']))
        observed.extend(day_times)
    units = etf.units_change(fixture) if weighted else None
    if not history and units is None:
        return None, 'No complete finalized flow session or units history is available.'
    history.reverse()
    result = {'scope': 'holdings_weighted' if weighted else 'instrument', 'finalized_through': end, 'observed_at': max(observed, key=instant) if observed else None, 'unit': 'KRW', 'history': {'columns': ['date'] + list(investors), 'rows': history}}
    if weighted:
        # Amounts are not scaled up: they describe this share of the fund's weight (lowest day shown).
        result['observed_weight_ratio'] = number(min(covered)) if covered else None
        for investor in ('foreign', 'institution'):
            values = [decimal(row[investors.index(investor)+1]) for row in history]
            result[f'weighted_{investor}_net_amount_20d'] = number(sum(values[-20:])) if len(values) >= 20 else None
            streak = 0
            for value in reversed(values):
                if value <= 0:
                    break
                streak += 1
            result[f'weighted_{investor}_net_buy_streak'] = streak if streak < len(values) else None
        result['etf_units_change_20d_pct'] = units['value'] if units else None
        result['units_observed_at'] = units['observed_at'] if units else None
    return result, None


def valuation_data(fixture, instrument_id):
    """Keep independently available ratios, including book value during losses."""
    if instrument_id != fixture['context']['etf_code']:
        value = company_valuation(fixture, instrument_id)
        return value, None if value else 'No price or released financial observations are available.'
    published_holdings = [r for r in available(fixture.get('holdings', []), instant(fixture['context']['analysis_at'])) if r['as_of_date'] <= instant(fixture['context']['analysis_at']).date().isoformat()]
    portfolio = holdings(fixture, require_complete=False) if published_holdings else {'as_of_date': None, 'holdings': []}
    if portfolio['holdings'] and not covers_whole_etf(portfolio):
        return None, 'Observed constituent weights are below 70%; whole-ETF weighted valuation unavailable.'
    values = [(row, company_valuation(fixture, row['instrument_id'])) for row in portfolio['holdings']]
    result = {'scope': 'holdings_weighted', 'holdings_as_of': portfolio['as_of_date'],
              'observed_weight_ratio': portfolio.get('observed_weight_ratio')}
    result['ratio_coverage'] = {}
    for metric in ('per', 'pbr'):
        # Same rule as calculate_weighted_valuation: average over the constituents that have the ratio,
        # stated only when they hold at least 70% of the fund.
        usable = [(row, value) for row, value in values if value and value[metric] is not None]
        share = covered_weight([row for row, _ in usable])
        result['weighted_' + metric] = (number(sum(decimal(row['weight']) * decimal(value[metric]) for row, value in usable) / share)
                                        if share >= MIN_WEIGHT_COVERAGE else None)
        result['ratio_coverage'][metric] = number(share)
    # An approximated Q4 EPS in any constituent makes the weighted PER approximate — carried, never silent.
    result['weighted_per_approximate'] = (any(value['eps_approximate'] for _, value in values if value and value['per'] is not None)
                                          if result['weighted_per'] is not None else None)
    stamps = [value['observed_at'] for _, value in values if value and value['observed_at']]
    result['observed_at'] = max(stamps, key=instant) if stamps else None
    distribution = etf.distribution_yield(fixture)
    result['distribution_yield_12m_pct'] = distribution['value'] if distribution else None
    result['distribution_observed_at'] = distribution['observed_at'] if distribution else None
    if not values and distribution is None:
        return None, 'No published holdings or distribution history is available.'
    return result, None


def company_valuation(fixture, instrument_id):
    """Read independent price, TTM EPS and latest BPS at the common cutoff.

    Args:
        fixture: Time-stamped source rows and fixed analysis context.
        instrument_id: Validated individual instrument identifier.

    Returns:
        Source observations and independently calculable ratios, or None.

    Raises:
        ValueError: Duplicate observations, invalid periods or nonpositive price.
    """
    cutoff = instant(fixture['context']['analysis_at'])
    prices = sorted([r for r in available(fixture.get('prices', []), cutoff)
        if r['instrument_id'] == instrument_id and r['date'] < cutoff.date().isoformat()], key=lambda r: r['date'])
    if len({r['date'] for r in prices}) != len(prices):
        raise ValueError('duplicate valuation price')
    target = fixture | {'context': fixture['context'] | {'etf_code': instrument_id}}
    points = chart.snapshots(target)
    latest_price = points[-1] if points else prices[-1] if prices else None
    price = decimal(latest_price['price'] if points else latest_price['close']) if latest_price else None
    if price is not None and price <= 0:
        raise ValueError('positive valuation price required')
    price_at = (latest_price['observed_at'] if points else latest_price['available_at']) if latest_price else None
    periods = {}
    for row in sorted(available(fixture.get('financials', []), cutoff), key=lambda r: instant(r['available_at'])):
        if row['instrument_id'] != instrument_id:
            continue
        match = re.fullmatch(r'(\d{4})-Q([1-4])', row['period'])
        if not match:
            raise ValueError('invalid fiscal quarter')
        year, quarter = map(int, match.groups())
        month = quarter * 3
        end = date(year, month, monthrange(year, month)[1])
        if end > cutoff.date():
            raise ValueError('financial period ends after analysis time')
        index = year * 4 + quarter - 1
        if index in periods and periods[index]['available_at'] == row['available_at']:
            raise ValueError('conflicting financial release')
        periods[index] = row | {'period_end': end.isoformat()}
    if price is None and not periods:
        return None
    selected = sorted(periods)[-4:]
    latest = periods[selected[-1]] if selected else {}
    complete = len(selected) == 4 and selected == list(range(selected[-1]-3, selected[-1]+1))
    eps = sum(decimal(periods[k]['eps']) for k in selected) if complete and all(periods[k].get('eps') is not None for k in selected) else None
    bps = decimal(latest['bps']) if latest.get('bps') is not None else None
    published = max((periods[k]['available_at'] for k in selected), key=instant) if selected else None
    stamps = [stamp for stamp in (price_at, published) if stamp]
    # Q4 EPS from DART is FY−9M (weighted-share approximation, ALPHA-1130): the screen says so rather than hiding it.
    derived = [periods[k]['period'] for k in selected if periods[k].get('eps_derivation') == 'FY_MINUS_9M'] if eps is not None else []
    return {'scope': 'instrument', 'price_krw': number(price) if price is not None else None,
        'price_observed_at': price_at, 'financials_published_at': published,
        'ttm_period_end': latest.get('period_end') if complete else None,
        'ttm_eps_krw': number(eps) if eps is not None else None,
        'eps_approximate': bool(derived), 'eps_derived_periods': derived,
        'bps_krw': number(bps) if bps is not None else None,
        'per': number(price/eps) if price is not None and eps is not None and eps > 0 else None,
        'pbr': number(price/bps) if price is not None and bps is not None and bps > 0 else None,
        'observed_at': max(stamps, key=instant) if stamps else None}


def macro_data(fixture, instrument_id):
    """Read the common macro series without asserting instrument exposure."""
    series = []
    for name in macro.SERIES:
        value = macro.read(fixture, name)
        if value['rows']:
            series.append(value)
    cards = macro.metrics(fixture)
    if not series and not cards:
        return None, 'No published macro observations or policy schedule are available.'
    result = {'series': series, **{card['key']: card['value'] for card in cards}, 'metric_observed_at': {card['key']: card['observed_at'] for card in cards}, 'metric_subjects': {card['key']: card['subject'] for card in cards if 'subject' in card}}
    cutoff = instant(fixture['context']['analysis_at'])
    events = sorted([r for r in available(fixture.get('policy_decisions', []), cutoff) if instant(r['decision_at']) > cutoff], key=lambda r: instant(r['decision_at']))
    result['next_policy_decision'] = {k: events[0][k] for k in ('decision_at', 'available_at', 'subject')} if events else None
    return result, None
