"""Price-derived indicators; intraday values never compound provisional state."""
from datetime import date, timedelta

from .common import available, decimal, instant, number


def history(fixture, instrument_id=None):
    """Read continuous completed daily prices from the registered calendar."""
    context = fixture["context"]
    cutoff = instant(context["analysis_at"])
    target = instrument_id or context["etf_code"]
    rows = sorted([r for r in available(fixture.get("prices", []), cutoff) if r["instrument_id"] == target and date.fromisoformat(r["date"]) < cutoff.date()], key=lambda r: r["date"])
    if not rows or len({r["date"] for r in rows}) != len(rows):
        raise ValueError("missing or duplicate price history")
    expected = sorted(d for d in fixture["trading_dates"] if rows[0]["date"] <= d < cutoff.date().isoformat())
    if [r["date"] for r in rows] != expected:
        raise ValueError("missing expected daily price")
    for row in rows:
        low, close, high = [decimal(row[k]) for k in ("low", "close", "high")]
        if not 0 < low <= close <= high:
            raise ValueError("invalid OHLC price domain")
    return rows


def snapshots(fixture):
    """Read latest distinct provisional observations or the latest finalized close."""
    context = fixture["context"]
    cutoff = instant(context["analysis_at"])
    source = fixture.get("price_snapshots", [fixture["price_snapshot"]] if "price_snapshot" in fixture else [])
    rows = sorted([r for r in available(source, cutoff, "observed_at") if r["instrument_id"] == context["etf_code"]], key=lambda r: instant(r["observed_at"]))
    if len({instant(r["observed_at"]) for r in rows}) != len(rows):
        raise ValueError("duplicate price snapshot")
    for row in rows:
        if not decimal(0) < decimal(row["low"]) <= decimal(row["price"]) <= decimal(row["high"]):
            raise ValueError("invalid snapshot price domain")
        if instant(row["observed_at"]).date() != cutoff.date():
            raise ValueError("snapshot must belong to analysis day")
    return rows[-5:]


def rma(values):
    """Seed a fourteen-period Wilder average and carry its finalized state."""
    if len(values) < 14:
        raise ValueError("missing fourteen changes")
    average = sum(values[:14]) / 14
    for value in values[14:]:
        average = (13 * average + value) / 14
    return average


def indicator_values(rows, snapshot=None):
    """Calculate RSI14 and inverted Williams%R14 at one observation."""
    closes = [decimal(r["close"]) for r in rows]
    changes = [b-a for a, b in zip(closes, closes[1:])]
    gain = rma([max(v, decimal(0)) for v in changes])
    loss = rma([max(-v, decimal(0)) for v in changes])
    if snapshot:
        price = decimal(snapshot["price"])
        change = price-closes[-1]
        gain = (13*gain+max(change, decimal(0)))/14
        loss = (13*loss+max(-change, decimal(0)))/14
        highs = [decimal(r["high"]) for r in rows[-13:]]+[decimal(snapshot["high"])]
        lows = [decimal(r["low"]) for r in rows[-13:]]+[decimal(snapshot["low"])]
        observed = snapshot["observed_at"]
    else:
        price = closes[-1]
        highs = [decimal(r["high"]) for r in rows[-14:]]
        lows = [decimal(r["low"]) for r in rows[-14:]]
        observed = rows[-1]["available_at"]
    high, low = max(highs), min(lows)
    return {"momentum": number(100*gain/(gain+loss)) if gain+loss else None,
            "bottom": number(100*(high-price)/(high-low)) if high != low else None,
            "observed_at": observed}


def indicators(fixture):
    """Return current ETF indicators from finalized history plus current price."""
    rows, points = history(fixture), snapshots(fixture)
    return indicator_values(rows, points[-1] if points else None)


def transition(fixture, indicator):
    """Evaluate 80/20 boundaries from independently calculated observations."""
    if indicator not in ("momentum", "bottom"):
        raise ValueError("unknown indicator")
    rows = history(fixture)
    values = [indicator_values(rows, point) for point in snapshots(fixture)]
    observations = {"columns": ["at", "value"], "rows": [[v["observed_at"], v[indicator]] for v in values]}
    changes = None
    if len(values) >= 2 and all(v[indicator] is not None for v in values[-2:]):
        previous, current = [v[indicator] for v in values[-2:]]
        changes = []
        for name, before, after in (("upper", previous >= 80, current >= 80), ("lower", previous <= 20, current <= 20)):
            if before != after:
                changes.append({"boundary": name, "action": "entered" if after else "exited"})
    held = None
    if len(values) == 5 and all(v[indicator] is not None for v in values):
        held = "upper" if all(v[indicator] >= 80 for v in values) else "lower" if all(v[indicator] <= 20 for v in values) else "none"
    return {"indicator": indicator, "observations": observations, "transitions": changes, "held_zone": held}


METRICS = ('ma20_distance_pct', 'ma60_direction', 'new_closing_high_count_20d',
           'distance_from_52w_closing_high_pct', 'turnover_ratio_previous_day', 'atr14_pct')


def selected_metrics(fixture, selected):
    """Return requested chart values and the exact price used to interpret them."""
    values = metrics(fixture, selected)
    points = snapshots(fixture)
    latest = points[-1] if points else history(fixture)[-1]
    return {'instrument_id':fixture['context']['etf_code'],
            'price':latest['price'] if points else latest['close'],
            'price_at':latest['observed_at'] if points else latest['date'], 'metrics':values}


def metrics(fixture, selected=None):
    """Calculate detailed chart cards using each card's explicit observation time."""
    rows, points = history(fixture), snapshots(fixture)
    if selected is None and len(rows) < 60:
        raise ValueError("missing sixty completed prices")
    requested = set(METRICS if selected is None else selected)
    if not requested or not requested <= set(METRICS) or (selected is not None and len(requested) != len(selected)):
        raise ValueError('choose unique supported chart metrics')
    closes = [decimal(r["close"]) for r in rows]
    price = decimal(points[-1]["price"]) if points else closes[-1]
    observed = points[-1]["observed_at"] if points else rows[-1]["available_at"]
    # Before market open the latest close is already in the finalized window.
    current_window = closes+[price] if points else closes
    result = []
    if 'ma20_distance_pct' in requested:
        if len(current_window) < 20:
            raise ValueError('twenty prices required for 20-day average')
        average20 = sum(current_window[-20:])/20
        result.append({"key": "ma20_distance_pct", "value": number(100*(price/average20-1)), "observed_at": observed})
    if 'ma60_direction' in requested and len(current_window) >= 61:
        average60 = sum(current_window[-60:])/60
        previous60 = sum(current_window[-61:-1])/60
        result.append({"key": "ma60_direction", "value": "상승" if average60 > previous60 else "하락" if average60 < previous60 else "횡보", "observed_at": observed})
    if 'new_closing_high_count_20d' in requested:
        if len(closes) < 40:
            raise ValueError('forty completed prices required for twenty high comparisons')
        count = sum(closes[i] > max(closes[i-20:i]) for i in range(len(closes)-20, len(closes)))
        result.append({"key": "new_closing_high_count_20d", "value": count, "observed_at": rows[-1]["available_at"]})
    start = instant(fixture["context"]["analysis_at"]).date()-timedelta(days=364)
    if 'distance_from_52w_closing_high_pct' in requested and date.fromisoformat(rows[0]["date"]) <= start:
        high = max(decimal(r["close"]) for r in rows if date.fromisoformat(r["date"]) >= start)
        result.append({"key": "distance_from_52w_closing_high_pct", "value": number(100*(price/high-1)), "observed_at": observed})
    if 'turnover_ratio_previous_day' in requested:
        if len(rows) < 21:
            raise ValueError('twenty-one completed turnover observations required')
        amounts = [decimal(r['turnover']) for r in rows[-21:]]
        if any(v < 0 for v in amounts):
            raise ValueError('negative turnover')
        if sum(amounts[:-1]) > 0:
            result.append({'key':'turnover_ratio_previous_day', 'value':number(amounts[-1]/(sum(amounts[:-1])/20)), 'observed_at':rows[-1]['available_at']})
    if 'atr14_pct' in requested:
        ranges = [max(decimal(r["high"])-decimal(r["low"]), abs(decimal(r["high"])-closes[i-1]), abs(decimal(r["low"])-closes[i-1])) for i, r in enumerate(rows) if i]
        result.append({"key": "atr14_pct", "value": number(100*rma(ranges)/closes[-1]), "observed_at": rows[-1]["available_at"]})
    if selected is not None and requested != {r['key'] for r in result}:
        raise ValueError('requested metric lacks sufficient history or a valid denominator')
    return result
