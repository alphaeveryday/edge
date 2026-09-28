"""Assemble deterministic detail cards independently from agent judgments."""
from . import chart, flow, macro, valuation
from .common import available, instant


def metrics(fixture, type):
    """Return one factor's numerical cards without assigning a sticker."""
    readers = {"차트": chart.metrics, "매크로": macro.metrics, "밸류": valuation.metrics}
    if type in readers:
        return {"type": type, "metrics": readers[type](fixture)}
    if type != "수급":
        raise ValueError("unknown factor type")
    context = fixture["context"]
    rows = [r for r in available(fixture.get("flow", []), instant(context["analysis_at"])) if r["date"] == context["flow_as_of_date"]]
    if not rows:
        raise ValueError("missing finalized flow observation time")
    observed = max((r["available_at"] for r in rows), key=instant)
    cards = []
    for investor in ("institution", "foreign"):
        amount = flow.calculate(fixture, investor, 20, "sum", "none")["amount_krw"]
        streak = flow.calculate(fixture, investor, 30, "streak", "net_buy")
        cards.append({"key": f"weighted_{investor}_net_amount_20d", "value": amount, "observed_at": observed})
        # A lower bound must not be displayed as an exact streak card.
        if streak["exact"]:
            cards.append({"key": f"weighted_{investor}_net_buy_streak", "value": streak["streak_days"], "observed_at": observed})
    return {"type": type, "metrics": cards}
