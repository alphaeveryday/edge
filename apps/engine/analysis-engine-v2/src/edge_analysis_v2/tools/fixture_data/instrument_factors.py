"""Common factor observations for one instrument at a fixed analysis cutoff."""
from calendar import monthrange
from datetime import date
import re

from edge_analysis_v2.tools.fixture_data import chart, etf, flow, macro
from edge_analysis_v2.tools.fixture_data.common import MIN_WEIGHT_COVERAGE, available, covers_whole_etf, decimal, holdings, instant, number
from edge_analysis_v2.tools.fixture_data.valuation import covered_weight
from edge_analysis_v2.tools.execution import ToolInputError

FORMULA_LATEX = (
    r"M_n=\frac1n\sum_{j=0}^{n-1}C_j;\ d_{20}=100(P/M_{20}-1);\ "
    r"N_{20}=\sum_{d=D-19}^{D}[C_d>\max(C_{d-20},\ldots,C_{d-1})];\ "
    r"d_{52w}=100(P/\max C_{[T-364d,T)}-1);\ "
    r"A_{ratio}=A_D/(\frac1{20}\sum_{j=1}^{20}A_{D-j});\ "
    r"TR_d=\max(H_d-L_d,|H_d-C_{d-1}|,|L_d-C_{d-1}|);\ "
    r"ATR_d=(13ATR_{d-1}+TR_d)/14;\ ATR\%=100ATR_D/C_D;\ "
    r"R_{20}=100(X_D/X_{D-20}-1);\ "
    r"PER_i=P_i/\sum_{q=1}^{4}EPS_{i,q};\ PBR_i=P_i/BPS_i;\ "
    r"\bar x=\sum_iw_ix_i;\ F_d=\sum_iw_i^{latest}f_{i,d};\ S_{20}=\sum_{d=D-19}^{D}F_d;\ "
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
        raise ToolInputError('choose unique supported factors')
    instruments = {row['instrument_id']: row for row in fixture.get('instruments', [])}
    scope = {fixture['context']['etf_code']} | set(instruments) | {row['instrument_id'] for row in fixture.get('holdings', [])}
    if not isinstance(instrument_id, str) or instrument_id not in scope:
        raise ToolInputError('unknown instrument_id; select the ETF or a constituent from get_etf_holdings.')
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
    portfolio, unavailable = None, None
    if weighted:
        try:
            portfolio = holdings(fixture)
            if any(row['instrument_id'] == context['etf_code'] for row in portfolio['holdings']):
                raise ToolInputError('INVALID_HOLDINGS: ETF own flow cannot be a constituent. Do not retry this snapshot; holdings repair is required.')
        except ToolInputError as error:
            unavailable = str(error)
    elif instrument_id not in flow.constituent_ids(fixture):
        return None, 'UNSUPPORTED_FLOW_TARGET: outside latest constituent scope; use get_etf_holdings to select a constituent.'
    source = available(fixture.get('flow', []), cutoff)
    investors = ('foreign', 'institution', 'individual')
    history, observed = [], []
    weights = portfolio['holdings'] if portfolio else [{'instrument_id': instrument_id, 'weight': 1}]
    for day in reversed(dates) if unavailable is None else []:
        values = []
        day_times = []
        complete = True
        for investor in investors:
            total = decimal(0)
            for weight in weights:
                if decimal(weight['weight']) == 0:
                    continue
                try:
                    row = flow.observation(source, weight['instrument_id'], day, investor,
                                           f'up to 30 sessions ending {end}')
                except ToolInputError as error:
                    unavailable = str(error)
                    complete = False
                    break
                total += decimal(weight['weight']) * row['net_amount_krw']
                day_times.append(row['available_at'])
            if not complete:
                break
            values.append(number(total))
        if not complete:
            break
        history.append([day] + values)
        observed.extend(day_times)
    units = etf.units_change(fixture) if weighted else None
    if not history and units is None:
        return None, unavailable or 'No complete finalized flow session or units history is available.'
    history.reverse()
    basis = flow.weight_basis(fixture, portfolio) if portfolio else dict(
        observed_weight_ratio=None, holdings_as_of=None, holdings_available_at=None,
        weight_basis='latest_snapshot_at_analysis')
    if weighted and history:
        observed.append(basis['holdings_available_at'])
    result = {'scope': 'holdings_weighted' if weighted else 'instrument', 'finalized_through': end, 'observed_at': max(observed, key=instant) if observed else None, 'unit': 'KRW', 'history': {'columns': ['date'] + list(investors), 'rows': history}}
    if weighted:
        result.update(basis)
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
    return result, unavailable


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
    result['ratio_coverage'], result['ratio_observed_at'] = {}, {}
    for metric in ('per', 'pbr'):
        # Same rule as calculate_weighted_valuation: average over the constituents that have the ratio,
        # stated only when they hold at least 70% of the fund.
        usable = [(row, value) for row, value in values if value and value[metric] is not None]
        share = covered_weight([row for row, _ in usable])
        result['weighted_' + metric] = (number(sum(decimal(row['weight']) * decimal(value[metric]) for row, value in usable) / share)
                                        if share >= MIN_WEIGHT_COVERAGE else None)
        result['ratio_coverage'][metric] = number(share)
        # A ratio is stamped by the constituents that went into it; a holding left out cannot date it.
        stamps = [value[metric + '_observed_at'] for _, value in usable if value[metric + '_observed_at']] if result['weighted_' + metric] is not None else []
        result['ratio_observed_at'][metric] = max(stamps, key=instant) if stamps else None
    # An approximated Q4 EPS in any constituent makes the weighted PER approximate — carried, never silent.
    result['weighted_per_approximate'] = (any(value['eps_approximate'] for _, value in values if value and value['per'] is not None)
                                          if result['weighted_per'] is not None else None)
    stamps = [stamp for stamp in result['ratio_observed_at'].values() if stamp]
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
    # Each ratio is stamped by its own inputs: a late restatement of an older quarter dates PER, not PBR.
    latest_published = latest.get('available_at')
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
        'per_observed_at': max(stamps, key=instant) if stamps else None,
        'pbr_observed_at': max([s for s in (price_at, latest_published) if s], key=instant) if price_at or latest_published else None,
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


TABLE_FACTORS = ('chart', 'flow', 'valuation')
TABLE_COLUMNS = {
    'chart': ['price_krw', 'ma20_distance_pct', 'momentum_index', 'bottom_index', 'new_closing_high_count_20d',
              'distance_from_52w_closing_high_pct'],
    'flow': ['foreign_net_5d_krw', 'foreign_net_20d_krw', 'institution_net_5d_krw', 'institution_net_20d_krw',
             'individual_net_5d_krw', 'individual_net_20d_krw'],
    'valuation': ['per', 'pbr', 'eps_approximate'],   # the single read marks a PER whose EPS has a derived quarter; so does the table
}


def table(fixture, instrument_ids=None, top_n=None, factors=TABLE_FACTORS):
    """Read the same factors for many instruments, one row per instrument.

    Args:
        fixture: Raw observations and fixed analysis context.
        instrument_ids: Instruments to read; the ETF code stands for all of its holdings. Omitted means the ETF's holdings.
        top_n: Keep only this many rows by holding weight.
        factors: Nonempty unique selection of chart, flow and valuation.

    Returns:
        columns and rows sorted by weight, each instrument's own values (not weighted), nulls where unavailable.

    Raises:
        ValueError: Unsupported arguments or an instrument outside this analysis.
    """
    if not isinstance(factors, (list, tuple)) or not factors or any(not isinstance(f, str) for f in factors) or len(set(factors)) != len(factors) or not set(factors) <= set(TABLE_FACTORS):
        raise ToolInputError('choose unique factors from chart, flow, valuation')
    if top_n is not None and (type(top_n) is not int or top_n < 1):
        raise ToolInputError('top_n must be a positive integer')
    etf_code = fixture['context']['etf_code']
    names = {row['instrument_id']: row.get('name') for row in fixture.get('instruments', [])}
    portfolio = holdings(fixture, require_complete=False)
    weights = {row['instrument_id']: row['weight'] for row in portfolio['holdings']}
    wanted = list(weights) if instrument_ids is None else instrument_ids
    if not isinstance(wanted, (list, tuple)) or not wanted or any(not isinstance(i, str) for i in wanted):
        raise ToolInputError('instrument_ids must be a nonempty list of codes')
    selected = []
    for identity in wanted:
        for code in (list(weights) if identity == etf_code else [identity]):
            if code not in weights and code not in names:
                raise ToolInputError('unknown instrument_id: ' + code + '; select the ETF or a constituent from get_etf_holdings.')
            if code not in selected:
                selected.append(code)
    selected.sort(key=lambda code: -decimal(weights.get(code, 0)))
    total = len(selected)
    selected = selected[:top_n]
    readers = {'chart': chart_data, 'flow': flow_data, 'valuation': valuation_data}
    columns = ['instrument_id', 'name', 'weight_pct'] + [c for f in factors for c in TABLE_COLUMNS[f]]
    rows, unavailable, through = [], {}, {}
    for code in selected:
        row = [code, names.get(code), number(decimal(weights[code]) * 100) if code in weights else None]
        for factor in factors:
            value, reason = readers[factor](fixture, code)
            if reason:
                unavailable.setdefault(code, {})[factor] = reason
            value = value or {}
            if factor == 'flow':
                history = value.get('history', {}).get('rows', [])
                for index in (1, 2, 3):
                    for days in (5, 20):
                        row.append(number(sum(decimal(day[index]) for day in history[-days:])) if len(history) >= days else None)
            else:
                row.extend(value.get(column) for column in TABLE_COLUMNS[factor])
            if value.get('finalized_through'):
                through[factor] = max(through.get(factor, ''), value['finalized_through'])
        rows.append(row)
    result = {'analysis_at': fixture['context']['analysis_at'], 'holdings_as_of': portfolio['as_of_date'],
              'scope': 'each row is that instrument alone; nothing is weighted or summed across rows',
              'finalized_through': through, 'instruments_matched': total, 'columns': columns, 'rows': rows}
    if unavailable:
        result['unavailable'] = unavailable
    return result
