"""Individual and complete-portfolio finalized net-flow calculations."""
from datetime import date

from edge_analysis_v2.tools.execution import ToolInputError, ToolDataUnavailableError, ToolSourceDataError
from edge_analysis_v2.tools.fixture_data.common import available, decimal, holdings, instant, number


def constituent_ids(fixture):
    """Read latest known membership without requiring complete portfolio weights."""
    cutoff = instant(fixture['context']['analysis_at'])
    rows = [r for r in available(fixture.get('holdings', []), cutoff)
            if r['as_of_date'] <= cutoff.date().isoformat()]
    latest = max((r['as_of_date'] for r in rows), default=None)
    return {r['instrument_id'] for r in rows if r['as_of_date'] == latest
            and r['instrument_id'] != fixture['context']['etf_code']}


def weight_basis(fixture, portfolio):
    """Identify the selected basket and when all its weights became available."""
    cutoff = instant(fixture['context']['analysis_at'])
    stamps = [r['available_at'] for r in available(fixture['holdings'], cutoff)
              if r['as_of_date'] == portfolio['as_of_date']]
    stamps += [r['available_at'] for r in fixture.get('holdings_status', [])
               if r['as_of_date'] == portfolio['as_of_date'] and r.get('available_at')
               and instant(r['available_at']) <= cutoff]
    return dict(observed_weight_ratio=portfolio['observed_weight_ratio'],
                holdings_as_of=portfolio['as_of_date'], weight_basis='latest_snapshot_at_analysis',
                holdings_available_at=max(stamps, key=instant))


def observation(rows, instrument_id, day, investor, window):
    """Require one finalized integer-KRW observation, with safe actionable errors."""
    matches = [r for r in rows if r['instrument_id'] == instrument_id
               and r['date'] == day and r['investor'] == investor]
    detail = f'target={instrument_id}, date={day}, investor={investor}, window={window}. '
    action = 'Do not retry the same request; use a fully available window or report this data gap.'
    if not matches:
        raise ToolDataUnavailableError('MISSING_FLOW: missing finalized flow; ' + detail + action)
    if len(matches) != 1:
        raise ToolSourceDataError('DUPLICATE_FLOW: duplicate source observations; ' + detail + action)
    if matches[0].get('finalized') is not True:
        raise ToolDataUnavailableError('UNFINALIZED_FLOW: source observation is not finalized; ' + detail + action)
    if type(matches[0]['net_amount_krw']) is not int:
        raise ToolSourceDataError('INVALID_FLOW_AMOUNT: source flow must be integer KRW; ' + detail + action)
    return matches[0]


def input_tables(rows):
    """Pivot finalized observations by stock and date without filling missing facts.

    Args:
        rows: Time-filtered individual investor observations.

    Returns:
        Stock-keyed KRW tables; absent investor cells remain null.

    Raises:
        ValueError: Duplicate date/investor or unsupported investor.
    """
    columns = ["date", "foreign", "institution", "individual"]
    grouped = {}
    for row in rows:
        days = grouped.setdefault(row["instrument_id"], {})
        values = days.setdefault(row["date"], {})
        investor = row["investor"]
        if investor not in columns[1:]:
            raise ToolSourceDataError('INVALID_FLOW_INVESTOR: unknown investor observation in initial input. Do not retry this snapshot; repair source data.')
        if investor in values:
            # Preserve fail-fast for corrupt input; use the same safe diagnostic as direct tools.
            observation(rows, row['instrument_id'], row['date'], investor, 'initial input up to 30 sessions')
        values[investor] = row["net_amount_krw"]
    return {target: {"unit": "KRW", "metric": "net_amount", "columns": columns.copy(),
                     "rows": [[day] + [days[day].get(key) for key in columns[1:]] for day in sorted(days)]}
            for target, days in sorted(grouped.items())}


def calculate(fixture, investor, lookback_days, operation, direction, instrument_id=None):
    """Apply the latest known basket to exact historical finalized sessions.

    Args:
        fixture: Raw final daily flows, calendar and dated equity weights.
        investor: Foreign, institution or individual source column identity.
        lookback_days: Required finalized sessions, one through thirty.
        operation: Sum, frequency or latest-day streak.
        direction: Net buy or sell; none for a sum.
        instrument_id: Individual stock; omitted for full portfolio weighting.

    Returns:
        Window, scope and a single calculated fact; streak includes exactness.

    Raises:
        ValueError: Scope, completeness, times, numeric values or arguments fail.
    """
    if investor not in ("foreign", "institution", "individual") or type(lookback_days) is not int or not 1 <= lookback_days <= 30:
        raise ToolInputError('INVALID_FLOW_ARGUMENTS: investor must be foreign, institution or individual; lookback_days must be an integer from 1 to 30. Correct the arguments before retrying.')
    if operation not in ("sum", "frequency", "streak") or direction not in (("none",) if operation == "sum" else ("net_buy", "net_sell")):
        raise ToolInputError('INVALID_FLOW_ARGUMENTS: use sum with direction=none, or frequency/streak with net_buy/net_sell. Correct the arguments before retrying.')
    context = fixture["context"]
    cutoff = instant(context["analysis_at"])
    end = date.fromisoformat(context["flow_as_of_date"])
    if end >= cutoff.date():
        raise ToolSourceDataError('INVALID_FLOW_CUTOFF: flow cutoff must be a completed prior day. Do not retry this fixed analysis snapshot.')
    dates = [date.fromisoformat(d) for d in fixture.get("trading_dates", [])]
    if len(set(dates)) != len(dates):
        raise ToolSourceDataError('INVALID_FLOW_CALENDAR: duplicate trading date. Do not retry this fixed analysis snapshot; calendar repair is required.')
    dates = sorted(d for d in dates if d <= end)[-lookback_days:]
    if len(dates) != lookback_days or dates[-1] != end:
        raise ToolDataUnavailableError(f'INSUFFICIENT_CALENDAR: missing trading calendar window for {lookback_days} sessions ending {end.isoformat()}. Do not retry the same request; choose a covered window or report the gap.')
    rows = available(fixture.get("flow", []), cutoff)
    if instrument_id == context['etf_code']:
        raise ToolInputError('UNSUPPORTED_ETF_FLOW: ETF own investor flow is not used. Choose a constituent from get_etf_holdings or use sum_weighted_net_flow/calculate_weighted_flow without instrument_id.')
    if instrument_id is not None and (not isinstance(instrument_id, str) or instrument_id not in constituent_ids(fixture)):
        raise ToolInputError('UNSUPPORTED_FLOW_TARGET: individual outside latest constituent scope. Choose a constituent from get_etf_holdings; do not retry the same target.')
    portfolio = holdings(fixture) if instrument_id is None else None
    weights = portfolio['holdings'] if portfolio else [{'instrument_id': instrument_id, 'weight': 1}]
    if any(r['instrument_id'] == context['etf_code'] for r in weights):
        raise ToolSourceDataError('INVALID_HOLDINGS: ETF own flow cannot be a constituent of its weighted flow. Do not retry this snapshot; holdings repair is required.')
    values = []
    for day in dates:
        total = decimal(0)
        for weight in weights:
            if decimal(weight['weight']) == 0:
                continue
            value = observation(rows, weight['instrument_id'], day.isoformat(), investor,
                                f'{lookback_days} sessions ending {end.isoformat()}')['net_amount_krw']
            total += decimal(weight["weight"]) * value
        values.append(total)
    result = {"scope": "individual" if instrument_id else "weighted_constituents", "investor": investor, "start_date": dates[0].isoformat(), "end_date": end.isoformat(), "trading_days": lookback_days}
    if instrument_id:
        result["instrument_id"] = instrument_id
    else:
        result.update(weight_basis(fixture, portfolio))
    if operation == "sum":
        return result | {"amount_krw": number(sum(values))}
    sign = 1 if direction == "net_buy" else -1
    matched = [sign * v > 0 for v in values]
    if operation == "frequency":
        return result | {"direction": direction, "matched_days": sum(matched)}
    streak = 0
    for match in reversed(matched):
        if not match:
            break
        streak += 1
    return result | {"direction": direction, "streak_days": streak, "exact": streak < len(values)}
