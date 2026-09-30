"""Individual and complete-portfolio finalized net-flow calculations."""
from datetime import date

from edge_analysis_v2.tools.fixture_data.common import available, decimal, holdings, instant, number


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
        if investor not in columns[1:] or investor in values:
            raise ValueError("duplicate or unknown investor observation")
        values[investor] = row["net_amount_krw"]
    return {target: {"unit": "KRW", "metric": "net_amount", "columns": columns.copy(),
                     "rows": [[day] + [days[day].get(key) for key in columns[1:]] for day in sorted(days)]}
            for target, days in sorted(grouped.items())}


def calculate(fixture, investor, lookback_days, operation, direction, instrument_id=None):
    """Aggregate exact sessions before computing temporal facts.

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
        raise ValueError("invalid investor or lookback_days")
    if operation not in ("sum", "frequency", "streak") or direction not in (("none",) if operation == "sum" else ("net_buy", "net_sell")):
        raise ValueError("invalid operation or direction")
    context = fixture["context"]
    cutoff = instant(context["analysis_at"])
    end = date.fromisoformat(context["flow_as_of_date"])
    if end >= cutoff.date():
        raise ValueError("flow cutoff must be a completed prior day")
    dates = [date.fromisoformat(d) for d in fixture.get("trading_dates", [])]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate trading date")
    dates = sorted(d for d in dates if d <= end)[-lookback_days:]
    if len(dates) != lookback_days or dates[-1] != end:
        raise ValueError("missing trading calendar window")
    rows = available(fixture.get("flow", []), cutoff)
    values = []
    for day in dates:
        weights = holdings(fixture, day.isoformat())["holdings"]
        if instrument_id is not None:
            if instrument_id not in {r["instrument_id"] for r in weights}:
                raise ValueError("individual outside constituent scope")
            weights = [{"instrument_id": instrument_id, "weight": 1}]
        total = decimal(0)
        for weight in weights:
            matches = [r for r in rows if r["instrument_id"] == weight["instrument_id"] and r["date"] == day.isoformat() and r["investor"] == investor]
            if len(matches) != 1 or matches[0].get("finalized") is not True:
                raise ValueError("missing, duplicate or unfinalized flow")
            value = matches[0]["net_amount_krw"]
            if type(value) is not int:
                raise ValueError("source flow must be integer KRW")
            total += decimal(weight["weight"]) * value
        values.append(total)
    result = {"scope": "individual" if instrument_id else "weighted_constituents", "investor": investor, "start_date": dates[0].isoformat(), "end_date": end.isoformat(), "trading_days": lookback_days}
    if instrument_id:
        result["instrument_id"] = instrument_id
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
