"""Released quarterly per-share fundamentals and equity-weighted ratios."""
from decimal import Context
import re
from edge_analysis_v2.tools.execution import ToolInputError

from edge_analysis_v2.tools.fixture_data.common import MIN_WEIGHT_COVERAGE, available, decimal, holdings, instant, number

# A constituent without a usable ratio is left out of a fund average; corrupted source data is not.
ABSENT = ("MISSING_", "RATIO_NOT_APPLICABLE")


def calculate(fixture, instrument_id):
    """Calculate a constituent's TTM PER and latest PBR from available releases.

    Each ratio needs only its own inputs: four consecutive quarters of EPS for PER, the latest released
    quarter's BPS for PBR. Quarterly reports often carry no BPS, so demanding both in all four quarters
    refused nearly every company. The window never slides: a hole in the latest four quarters makes
    PER unavailable, and PBR never falls back to an older quarter's book value.
    """
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
    if not periods:
        raise ToolInputError("MISSING_QUARTERS: no released quarter in database; investigate public filings")
    price = decimal(prices[-1]["close"])
    if price <= 0:
        raise ToolInputError("INVALID_PRICE: the latest price is not positive; source data requires repair")
    selected = sorted(periods)[-4:]
    latest = periods[selected[-1]]
    unavailable, eps, bps = {}, None, None
    if len(selected) != 4 or selected != list(range(selected[-1]-3, selected[-1]+1)):
        unavailable["per"] = "MISSING_QUARTERS: four consecutive released quarters are required for TTM EPS"
    elif any(periods[index].get("eps") is None for index in selected):
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
    if eps is None and bps is None:
        # A loss-maker with no book value is "not applicable", not a data gap.
        codes = [reason.split(":")[0] for reason in unavailable.values()]
        code = "RATIO_NOT_APPLICABLE" if "RATIO_NOT_APPLICABLE" in codes else codes[0] if len(set(codes)) == 1 else "MISSING_FINANCIAL_VALUE"
        raise ToolInputError(code + ": neither PER nor PBR can be calculated (" + "; ".join(unavailable.values()) + "); investigate public filings")
    # Each ratio is stamped by its own inputs: a late restatement of an older quarter dates PER, not PBR.
    stamps = {"per": max([prices[-1]["available_at"]] + [periods[index]["available_at"] for index in selected], key=instant) if eps is not None else None,
              "pbr": max([prices[-1]["available_at"], latest["available_at"]], key=instant) if bps is not None else None}
    # Q4 EPS from DART is FY−9M (weighted-share approximation); the result says so, never hides it.
    derived = [periods[index]["period"] for index in selected if periods[index].get("eps_derivation") == "FY_MINUS_9M"] if eps is not None else []
    return {"instrument_id": instrument_id, "per": number(price/eps) if eps is not None else None,
            "pbr": number(price/bps) if bps is not None else None, "unavailable": unavailable,
            "price": number(price), "price_date": prices[-1]["date"],
            "ttm_eps": number(eps) if eps is not None else None, "bps": number(bps) if bps is not None else None,
            "bps_period": latest["period"] if bps is not None else None,
            "periods": [periods[index]["period"] for index in selected] if eps is not None else [],
            "derived_periods": derived, "approximate": bool(derived), "ratio_observed_at": stamps,
            "observed_at": max((s for s in stamps.values() if s), key=instant)}


def covered_weight(rows):
    """Sum of fund weights, judged at the 15 digits a stored double carries (see ``common.holdings``)."""
    return Context(prec=15).plus(sum(decimal(row["weight"]) for row in rows))


def weighted(fixture):
    """Weight each ratio over the constituents that have it, when they cover enough of the fund."""
    portfolio = holdings(fixture)
    values, missing = [], []
    for row in portfolio["holdings"]:
        try:
            values.append(calculate(fixture, row["instrument_id"]) | {"weight": row["weight"]})
        except ToolInputError as error:
            if not str(error).startswith(ABSENT):
                raise   # duplicate prices or conflicting releases must stop the fund figure, not shrink it
            missing.append({"instrument_id": row["instrument_id"], "weight": row["weight"], "reason": str(error).split(";")[0]})
    result = {"as_of_date": portfolio["as_of_date"], "constituents": values, "missing_constituents": missing, "ratio_coverage": {},
              "coverage": {"constituents": len(portfolio["holdings"]), "weight": number(covered_weight(portfolio["holdings"]))}}
    result["ratio_observed_at"] = {}
    for metric in ("per", "pbr"):
        # The 70% rule applies to the weight that actually has this ratio, not to the holdings list:
        # an average over a minority of the fund is not the fund's ratio.
        usable = [v for v in values if v[metric] is not None]
        covered = covered_weight(usable)
        enough = covered >= MIN_WEIGHT_COVERAGE
        result["weighted_" + metric] = number(sum(decimal(v["weight"])*decimal(v[metric]) for v in usable)/covered) if enough else None
        result["ratio_coverage"][metric] = {"constituents": len(usable), "weight": number(covered)}
        result["ratio_observed_at"][metric] = max((v["ratio_observed_at"][metric] for v in usable), key=instant) if enough else None
    published = [stamp for stamp in result["ratio_observed_at"].values() if stamp]
    if not published:
        raise ToolInputError("INSUFFICIENT_RATIO_COVERAGE: constituents with a PER cover "
            + str(result["ratio_coverage"]["per"]["weight"]) + " and with a PBR " + str(result["ratio_coverage"]["pbr"]["weight"])
            + " of the fund, below the 70% needed for a whole-ETF ratio; use calculate_valuation per company and state the gap")
    per_rows = [v for v in values if v["per"] is not None] if result["weighted_per"] is not None else []
    result["approximate"] = any(v["approximate"] for v in per_rows)
    result["derived_constituents"] = [v["instrument_id"] for v in per_rows if v["approximate"]]
    result["observed_at"] = max(published, key=instant)
    return result
