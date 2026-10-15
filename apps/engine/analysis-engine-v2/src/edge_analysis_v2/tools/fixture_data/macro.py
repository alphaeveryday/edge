"""Published macro observations with exact comparison instants and units."""
from datetime import timedelta, timezone

from edge_analysis_v2.tools.fixture_data.common import available, decimal, instant, number, observed, table

SERIES = {"usd_krw": "KRW_per_USD", "kr_10y_yield": "percent", "us_10y_yield": "percent", "kr_cpi_yoy": "percent", "brent_spot_usd": "USD_per_barrel", "commodity": "index"}


def observations(fixture, series):
    """Read unique time-eligible numeric observations in their registered unit."""
    if series not in SERIES:
        raise ValueError("unknown macro series")
    cutoff = instant(fixture["context"]["analysis_at"])
    # observed_at may be a date-only observation day (sources that publish no time) — see common.observed.
    rows = sorted([r for r in available(fixture.get("macro", []), cutoff) if r["series"] == series
                   and observed(r["observed_at"]) <= cutoff], key=lambda r: observed(r["observed_at"]))
    if len({observed(r["observed_at"]) for r in rows}) != len(rows):
        raise ValueError("duplicate macro instant")
    for row in rows:
        if row["unit"] != SERIES[series]:
            raise ValueError("macro unit mismatch")
        value = decimal(row["value"])
        if series in ("usd_krw", "brent_spot_usd", "commodity") and value <= 0:
            raise ValueError("positive macro price required")
    return rows


def read(fixture, series):
    """Expose the latest twenty-one raw same-series observations for exploration."""
    rows = [r | {"at": r["observed_at"]} for r in observations(fixture, series)[-21:]]
    columns = ["at", "value", "available_at"] + (["reference_period"] if series == "kr_cpi_yoy" else [])
    return {"series": series, "unit": SERIES[series], **table(rows, columns)}


def compare(fixture, series, previous_at, current_at, operation):
    """Compare exact same-series observations without deriving impact direction."""
    previous, current = observed(previous_at), observed(current_at)
    if previous >= current or operation not in ("difference", "percent_change"):
        raise ValueError("ordered instants and known operation required")
    rows = {observed(r["observed_at"]): r for r in observations(fixture, series)}
    if previous not in rows or current not in rows:
        raise ValueError("exact macro observation unavailable")
    # A date-only observation is identified by its date — a caller-made end-of-day time would come back as
    # evidence the source never gave, so only the source's own string is accepted for such rows.
    for requested, row in ((previous_at, rows[previous]), (current_at, rows[current])):
        if len(row["observed_at"]) == 10 and requested != row["observed_at"]:
            raise ValueError("date-only macro observation must be requested by its date")
    p, c = decimal(rows[previous]["value"]), decimal(rows[current]["value"])
    if operation == "percent_change":
        if p <= 0:
            raise ValueError("positive percent-change denominator required")
        change, unit = 100*(c/p-1), "percent"
    else:
        change, unit = c-p, "percentage_points" if SERIES[series] == "percent" else SERIES[series]
    return {"series": series, "previous_at": previous_at, "previous_value": number(p), "current_at": current_at, "current_value": number(c), "change": number(change), "change_unit": unit}


def metrics(fixture):
    """Return only macro cards with sufficient published source observations."""
    result = []
    for series, key in (("usd_krw", "usd_krw"), ("kr_10y_yield", "kr_treasury_10y_yield"), ("us_10y_yield", "us_treasury_10y_yield"), ("brent_spot_usd", "brent_spot_usd")):
        rows = observations(fixture, series)
        if rows:
            row = rows[-1]
            result.append({"key": key, "value": number(row["value"]), "observed_at": row["observed_at"], "subject": row.get("subject", series)})
    rows = observations(fixture, "commodity")[-21:]
    calendar = fixture.get("macro_trading_dates", {}).get("commodity", [])
    if len(rows) == 21 and calendar:
        end = observed(rows[-1]["observed_at"]).date().isoformat()
        expected = sorted(d for d in calendar if d <= end)[-21:]
        if [observed(r["observed_at"]).date().isoformat() for r in rows] != expected:
            raise ValueError("missing commodity trading session")
        result.append({"key": "commodity_return_20d_pct", "value": number(100*(decimal(rows[-1]["value"])/decimal(rows[0]["value"])-1)), "observed_at": rows[-1]["observed_at"], "subject": rows[-1].get("subject", "commodity")})
    cutoff = instant(fixture["context"]["analysis_at"])
    events = sorted([r for r in available(fixture.get("policy_decisions", []), cutoff) if instant(r["decision_at"]) > cutoff], key=lambda r: instant(r["decision_at"]))
    if events:
        event = events[0]
        kst = timezone(timedelta(hours=9))
        days = (instant(event["decision_at"]).astimezone(kst).date()-cutoff.astimezone(kst).date()).days
        result.append({"key": "days_until_policy_decision", "value": days, "observed_at": event["available_at"], "subject": event["subject"]})
    return result
