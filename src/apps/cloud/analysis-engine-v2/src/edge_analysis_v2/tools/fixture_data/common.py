"""Small deterministic helpers for fixture-bound calculations."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal


def instant(value):
    """Parse an explicit source timestamp in the Korean market timezone."""
    value = datetime.fromisoformat(value)
    if value.utcoffset() is None:
        raise ValueError("timestamp requires offset")
    return value.astimezone(timezone(timedelta(hours=9)))


def decimal(value):
    """Require a finite numeric value without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("numeric value required")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("finite value required")
    return result


def number(value):
    """Serialize computed decimals without presentation rounding."""
    value = decimal(value)
    return int(value) if value == value.to_integral_value() else float(value)


def available(rows, cutoff, time_key=None):
    """Select only data actually available at the fixed analysis instant."""
    return [r for r in rows if instant(r["available_at"]) <= cutoff
            and (time_key is None or instant(r[time_key]) <= cutoff)]


def holdings(fixture, day=None, *, require_complete=True):
    """Read one complete equity portfolio without renormalizing missing weights."""
    cutoff = instant(fixture["context"]["analysis_at"])
    if day is not None:
        cutoff = min(cutoff, instant(day + "T23:59:59.999999+09:00"))
    day = day or cutoff.date().isoformat()
    date.fromisoformat(day)
    rows = [r for r in available(fixture.get("holdings", []), cutoff) if r["as_of_date"] <= day]
    latest = max((r["as_of_date"] for r in rows), default=None)
    rows = [r for r in rows if r["as_of_date"] == latest]
    weights = [decimal(r["weight"]) for r in rows]
    if not rows or len({r["instrument_id"] for r in rows}) != len(rows) or any(w < 0 for w in weights) or not 0 < sum(weights) <= 1:
        raise ValueError("complete positive equity weights summing to one required")
    statuses = [r for r in fixture.get('holdings_status', []) if r['as_of_date'] == latest]
    complete = sum(weights) == 1
    if statuses:
        if len(statuses) != 1 or statuses[0]['valid_count'] != len(rows):
            raise ValueError('holdings status does not match observed rows')
        complete = complete and statuses[0]['input_count'] == len(rows)
    if not complete and (require_complete or not statuses):
        raise ValueError('complete positive equity weights summing to one required')
    result = {"as_of_date": latest, "holdings": [{"instrument_id": r["instrument_id"], "weight": number(r["weight"])} for r in rows]}
    if statuses:
        result.update(coverage='full' if complete else 'partial', observed_weight_ratio=number(sum(weights)))
    return result


def table(rows, columns):
    """Encode homogeneous raw observations as columns and rows."""
    return {"columns": columns, "rows": [[r.get(c) for c in columns] for r in rows]}
