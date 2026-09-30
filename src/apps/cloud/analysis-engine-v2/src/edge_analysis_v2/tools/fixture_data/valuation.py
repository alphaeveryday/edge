"""Released quarterly per-share fundamentals and equity-weighted ratios."""
import re

from edge_analysis_v2.tools.fixture_data.common import available, decimal, holdings, instant, number


def calculate(fixture, instrument_id):
    """Calculate a constituent's TTM PER and latest PBR from available releases."""
    if instrument_id not in {r["instrument_id"] for r in holdings(fixture)["holdings"]}:
        raise ValueError("instrument outside current holdings")
    cutoff = instant(fixture["context"]["analysis_at"])
    prices = sorted([r for r in available(fixture.get("prices", []), cutoff) if r["instrument_id"] == instrument_id and r["date"] <= cutoff.date().isoformat()], key=lambda r: r["date"])
    if not prices or len({r["date"] for r in prices}) != len(prices):
        raise ValueError("missing or duplicate valuation price")
    periods = {}
    rows = [r for r in available(fixture.get("financials", []), cutoff) if r["instrument_id"] == instrument_id]
    for row in sorted(rows, key=lambda r: instant(r["available_at"])):
        match = re.fullmatch(r"(\d{4})-Q([1-4])", row["period"])
        if not match:
            raise ValueError("invalid fiscal quarter")
        index = int(match[1])*4+int(match[2])-1
        if index in periods and periods[index]["available_at"] == row["available_at"]:
            raise ValueError("conflicting financial release")
        periods[index] = row
    selected = sorted(periods)[-4:]
    if len(selected) != 4 or selected != list(range(selected[-1]-3, selected[-1]+1)):
        raise ValueError("four consecutive released quarters required")
    eps = sum(decimal(periods[index]["eps"]) for index in selected)
    bps = decimal(periods[selected[-1]]["bps"])
    price = decimal(prices[-1]["close"])
    if min(eps, bps, price) <= 0:
        raise ValueError("positive TTM EPS, BPS and price required")
    observed = max([prices[-1]["available_at"]]+[periods[index]["available_at"] for index in selected], key=instant)
    return {"instrument_id": instrument_id, "per": number(price/eps), "pbr": number(price/bps), "price": number(price), "price_date": prices[-1]["date"], "ttm_eps": number(eps), "bps": number(bps), "periods": [periods[index]["period"] for index in selected], "observed_at": observed}


def weighted(fixture):
    """Weight all constituent ratios without imputing or dropping missing stocks."""
    portfolio = holdings(fixture)
    values = [calculate(fixture, r["instrument_id"]) | {"weight": r["weight"]} for r in portfolio["holdings"]]
    return {"as_of_date": portfolio["as_of_date"], "weighted_per": number(sum(decimal(v["weight"])*decimal(v["price"])/decimal(v["ttm_eps"]) for v in values)), "weighted_pbr": number(sum(decimal(v["weight"])*decimal(v["price"])/decimal(v["bps"]) for v in values)), "constituents": values, "observed_at": max((v["observed_at"] for v in values), key=instant)}


def metrics(fixture):
    """Expose only currently implemented complete valuation cards."""
    result = weighted(fixture)
    return [{"key": key, "value": result[key], "observed_at": result["observed_at"]} for key in ("weighted_per", "weighted_pbr")]
