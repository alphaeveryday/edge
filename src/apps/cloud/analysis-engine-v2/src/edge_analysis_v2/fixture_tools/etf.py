"""ETF cash distributions and issued units, separate from constituent metrics."""
from datetime import date

from . import chart
from .common import available, decimal, instant, number


def distribution_yield(fixture):
    """Calculate trailing calendar-year paid distributions over current ETF price."""
    context = fixture["context"]
    cutoff = instant(context["analysis_at"])
    try:
        start = cutoff.replace(year=cutoff.year-1)
    except ValueError:
        start = cutoff.replace(year=cutoff.year-1, day=28)
    coverage = fixture.get("distribution_history_start")
    if coverage is None or date.fromisoformat(coverage) > start.date():
        return None
    rows = [r for r in available(fixture.get("distributions", []), cutoff, "paid_at") if r["instrument_id"] == context["etf_code"] and instant(r["paid_at"]) > start]
    if len({instant(r["paid_at"]) for r in rows}) != len(rows):
        raise ValueError("duplicate ETF distribution payment")
    amounts = [decimal(r["amount_per_unit"]) for r in rows]
    if any(v < 0 for v in amounts):
        raise ValueError("negative distribution per unit")
    points = chart.snapshots(fixture)
    if points:
        price, observed = decimal(points[-1]["price"]), points[-1]["observed_at"]
    else:
        prices = sorted([r for r in available(fixture.get("prices", []), cutoff)
            if r["instrument_id"] == context["etf_code"] and r["date"] < cutoff.date().isoformat()], key=lambda r: r["date"])
        if len({r["date"] for r in prices}) != len(prices):
            raise ValueError("duplicate ETF price")
        if not prices:
            return None
        price_row = prices[-1]
        price, observed = decimal(price_row["close"]), price_row["available_at"]
    if price <= 0:
        raise ValueError("positive ETF price required")
    return {"key": "distribution_yield_12m_pct", "value": number(100*sum(amounts)/price), "observed_at": max([observed]+[r["available_at"] for r in rows], key=instant)}


def units_change(fixture):
    """Calculate twenty finalized-session intervals only with all twenty-one rows."""
    if "etf_units" not in fixture:
        return None
    context = fixture["context"]
    cutoff = instant(context["analysis_at"])
    dates = sorted(d for d in fixture["trading_dates"] if date.fromisoformat(d) < cutoff.date())[-21:]
    rows = sorted([r for r in available(fixture["etf_units"], cutoff) if r["instrument_id"] == context["etf_code"] and r["date"] in dates], key=lambda r: r["date"])
    if len({r["date"] for r in rows}) != len(rows) or any(type(r["units"]) is not int or r["units"] <= 0 for r in rows):
        raise ValueError("duplicate or invalid ETF units")
    if len(dates) != 21 or [r["date"] for r in rows] != dates:
        return None
    return {"key": "etf_units_change_20d_pct", "value": number(100*(decimal(rows[-1]["units"])/rows[0]["units"]-1)), "observed_at": rows[-1]["available_at"]}
