"""Released quarterly per-share fundamentals and equity-weighted ratios."""
import re
from edge_analysis_v2.tools.execution import ToolInputError

from edge_analysis_v2.tools.fixture_data.common import available, decimal, holdings, instant, number


def calculate(fixture, instrument_id):
    """Calculate a constituent's TTM PER and latest PBR from available releases."""
    if instrument_id not in {r["instrument_id"] for r in holdings(fixture, require_complete=False)["holdings"]}:
        raise ToolInputError("OUTSIDE_HOLDINGS: instrument outside current holdings; choose an observed constituent")
    cutoff = instant(fixture["context"]["analysis_at"])
    prices = sorted([r for r in available(fixture.get("prices", []), cutoff) if r["instrument_id"] == instrument_id and r["date"] <= cutoff.date().isoformat()], key=lambda r: r["date"])
    if not prices:
        raise ToolInputError("MISSING_PRICE: no database price before cutoff; investigate another public source")
    if len({r["date"] for r in prices}) != len(prices):
        raise ToolInputError("CONFLICTING_PRICE: duplicate valuation price; do not retry unchanged input")
    periods = {}
    rows = [r for r in available(fixture.get("financials", []), cutoff) if r["instrument_id"] == instrument_id]
    for row in sorted(rows, key=lambda r: instant(r["available_at"])):
        match = re.fullmatch(r"(\d{4})-Q([1-4])", row["period"])
        if not match:
            raise ToolInputError("INVALID_PERIOD: invalid fiscal quarter; source data requires repair")
        index = int(match[1])*4+int(match[2])-1
        if index in periods and periods[index]["available_at"] == row["available_at"]:
            raise ToolInputError("CONFLICTING_RELEASE: conflicting financial release; source data requires repair")
        periods[index] = row
    selected = sorted(periods)[-4:]
    if len(selected) != 4 or selected != list(range(selected[-1]-3, selected[-1]+1)):
        raise ToolInputError("MISSING_QUARTERS: four consecutive released quarters required in database; investigate public filings")
    if any(periods[index].get("eps") is None or periods[index].get("bps") is None for index in selected):
        # A released quarter with a hole (blocked BPS, unconfirmed share count) is not skipped over —
        # skipping would silently slide the window to older quarters.
        raise ToolInputError("MISSING_FINANCIAL_VALUE: released quarter without EPS or BPS; investigate public filings")
    eps = sum(decimal(periods[index]["eps"]) for index in selected)
    bps = decimal(periods[selected[-1]]["bps"])
    price = decimal(prices[-1]["close"])
    if min(eps, bps, price) <= 0:
        raise ToolInputError("RATIO_NOT_APPLICABLE: positive TTM EPS, BPS and price required; do not interpret as neutral")
    observed = max([prices[-1]["available_at"]]+[periods[index]["available_at"] for index in selected], key=instant)
    # Q4 EPS from DART is FY−9M (weighted-share approximation); the result says so, never hides it.
    derived = [periods[index]["period"] for index in selected if periods[index].get("eps_derivation") == "FY_MINUS_9M"]
    return {"instrument_id": instrument_id, "per": number(price/eps), "pbr": number(price/bps), "price": number(price), "price_date": prices[-1]["date"], "ttm_eps": number(eps), "bps": number(bps), "periods": [periods[index]["period"] for index in selected], "derived_periods": derived, "approximate": bool(derived), "observed_at": observed}


def weighted(fixture):
    """Weight all constituent ratios without imputing or dropping missing stocks."""
    portfolio = holdings(fixture)
    # Every holding must have a ratio: a constituent without BPS (preferred-share block, unconfirmed
    # share count) makes the whole weighted figure unavailable rather than a partial "ETF PBR".
    values = [calculate(fixture, r["instrument_id"]) | {"weight": r["weight"]} for r in portfolio["holdings"]]
    # Holdings may cover 70-100% of the fund. An average over them divides by the covered weight;
    # coverage.weight says how much of the fund the figure describes.
    covered = sum(decimal(v["weight"]) for v in values)
    return {"as_of_date": portfolio["as_of_date"], "weighted_per": number(sum(decimal(v["weight"])*decimal(v["price"])/decimal(v["ttm_eps"]) for v in values)/covered), "weighted_pbr": number(sum(decimal(v["weight"])*decimal(v["price"])/decimal(v["bps"]) for v in values)/covered), "constituents": values, "coverage": {"constituents": len(values), "weight": number(sum(decimal(v["weight"]) for v in values))}, "approximate": any(v["approximate"] for v in values), "derived_constituents": [v["instrument_id"] for v in values if v["approximate"]], "observed_at": max((v["observed_at"] for v in values), key=instant)}


def metrics(fixture):
    """Expose only currently implemented complete valuation cards.

    A card carries no derivation note, so a PER built on an approximated Q4 EPS is withheld here;
    it stays available through ``calculate``/``weighted``, whose results say ``approximate``.
    """
    result = weighted(fixture)
    keys = ("weighted_pbr",) if result["approximate"] else ("weighted_per", "weighted_pbr")
    return [{"key": key, "value": result[key], "observed_at": result["observed_at"]} for key in keys]
