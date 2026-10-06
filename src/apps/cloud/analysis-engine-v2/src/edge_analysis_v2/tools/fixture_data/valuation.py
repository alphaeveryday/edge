"""Released quarterly per-share fundamentals and equity-weighted ratios."""
import re
from edge_analysis_v2.tools.execution import ToolInputError

from edge_analysis_v2.tools.fixture_data.common import MIN_WEIGHT_COVERAGE, available, decimal, holdings, instant, number


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
    # Each ratio needs only its own inputs: TTM EPS for PER, the latest quarter's BPS for PBR. Quarterly
    # reports often carry no BPS, so demanding both in all four quarters refused nearly every company.
    # The window never slides: a hole in one of the latest four quarters makes that ratio unavailable.
    price = decimal(prices[-1]["close"])
    latest = periods[selected[-1]]
    unavailable = {}
    eps = bps = None
    if any(periods[index].get("eps") is None for index in selected):
        unavailable["per"] = "MISSING_EPS: a released quarter in the latest four has no EPS"
    else:
        eps = sum(decimal(periods[index]["eps"]) for index in selected)
        if eps <= 0:
            unavailable["per"], eps = "RATIO_NOT_APPLICABLE: TTM EPS is not positive; do not interpret as neutral", None
    if latest.get("bps") is None:
        unavailable["pbr"] = "MISSING_BPS: the latest released quarter has no per-share book value"
    else:
        bps = decimal(latest["bps"])
        if bps <= 0:
            unavailable["pbr"], bps = "RATIO_NOT_APPLICABLE: BPS is not positive; do not interpret as neutral", None
    if price <= 0:
        raise ToolInputError("RATIO_NOT_APPLICABLE: positive price required; do not interpret as neutral")
    if eps is None and bps is None:
        code = "RATIO_NOT_APPLICABLE" if all("RATIO_NOT_APPLICABLE" in reason for reason in unavailable.values()) else "MISSING_FINANCIAL_VALUE"
        raise ToolInputError(code + ": neither PER nor PBR can be calculated (" + "; ".join(unavailable.values()) + "); investigate public filings")
    observed = max([prices[-1]["available_at"]]+[periods[index]["available_at"] for index in selected], key=instant)
    # Q4 EPS from DART is FY−9M (weighted-share approximation); the result says so, never hides it.
    derived = [periods[index]["period"] for index in selected if periods[index].get("eps_derivation") == "FY_MINUS_9M"] if eps is not None else []
    return {"instrument_id": instrument_id, "per": number(price/eps) if eps is not None else None,
            "pbr": number(price/bps) if bps is not None else None, "unavailable": unavailable,
            "price": number(price), "price_date": prices[-1]["date"],
            "ttm_eps": number(eps) if eps is not None else None, "bps": number(bps) if bps is not None else None,
            "bps_period": latest["period"] if bps is not None else None,
            "periods": [periods[index]["period"] for index in selected], "derived_periods": derived,
            "approximate": bool(derived), "observed_at": observed}


def weighted(fixture):
    """Weight each ratio over the constituents that have it, when they cover enough of the fund."""
    portfolio = holdings(fixture)
    values, missing = [], []
    for row in portfolio["holdings"]:
        try:
            values.append(calculate(fixture, row["instrument_id"]) | {"weight": row["weight"]})
        except ToolInputError as error:
            missing.append({"instrument_id": row["instrument_id"], "weight": row["weight"], "reason": str(error).split(":")[0]})
    result = {"as_of_date": portfolio["as_of_date"], "constituents": values, "missing_constituents": missing, "ratio_coverage": {},
              "coverage": {"constituents": len(portfolio["holdings"]), "weight": number(sum(decimal(r["weight"]) for r in portfolio["holdings"]))}}
    for metric in ("per", "pbr"):
        # The 70% rule applies to the weight that actually has this ratio, not to the holdings list:
        # an average over a minority of the fund is not the fund's ratio.
        usable = [v for v in values if v[metric] is not None]
        covered = sum(decimal(v["weight"]) for v in usable)
        enough = covered >= MIN_WEIGHT_COVERAGE
        result["weighted_" + metric] = number(sum(decimal(v["weight"])*decimal(v[metric]) for v in usable)/covered) if enough else None
        result["ratio_coverage"][metric] = {"constituents": len(usable), "weight": number(covered)}
    if result["weighted_per"] is None and result["weighted_pbr"] is None:
        raise ToolInputError("INSUFFICIENT_RATIO_COVERAGE: constituents with a PER cover "
            + str(result["ratio_coverage"]["per"]["weight"]) + " and with a PBR " + str(result["ratio_coverage"]["pbr"]["weight"])
            + " of the fund, below the 70% needed for a whole-ETF ratio; use calculate_valuation per company and state the gap")
    per_rows = [v for v in values if v["per"] is not None] if result["weighted_per"] is not None else []
    result["approximate"] = any(v["approximate"] for v in per_rows)
    result["derived_constituents"] = [v["instrument_id"] for v in per_rows if v["approximate"]]
    result["observed_at"] = max((v["observed_at"] for v in values), key=instant)
    return result


def metrics(fixture):
    """Expose only currently implemented complete valuation cards.

    A card carries no derivation note, so a PER built on an approximated Q4 EPS is withheld here;
    it stays available through ``calculate``/``weighted``, whose results say ``approximate``.
    """
    result = weighted(fixture)
    keys = ("weighted_pbr",) if result["approximate"] else ("weighted_per", "weighted_pbr")
    return [{"key": key, "value": result[key], "observed_at": result["observed_at"]} for key in keys if result[key] is not None]
