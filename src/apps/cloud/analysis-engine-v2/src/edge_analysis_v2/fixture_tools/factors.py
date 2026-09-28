"""Assemble deterministic detail cards independently from agent judgments."""
from . import chart, etf, flow, macro, valuation
from .common import available, instant

FORMULA_LATEX = (
    r"M_n=\frac1n\sum_{j=0}^{n-1}C_j;\ d_{20}=100(P/M_{20}-1);\ "
    r"N_{20}=\sum_{d=D-19}^{D}[C_d>\max(C_{d-20},\ldots,C_{d-1})];\ "
    r"d_{52w}=100(P/\max C_{[T-364d,T)}-1);\ "
    r"A_{ratio}=A_D/(\frac1{20}\sum_{j=1}^{20}A_{D-j});\ "
    r"TR_d=\max(H_d-L_d,|H_d-C_{d-1}|,|L_d-C_{d-1}|);\ "
    r"ATR_d=(13ATR_{d-1}+TR_d)/14;\ ATR\%=100ATR_D/C_D;\ "
    r"R_{20}=100(X_D/X_{D-20}-1);\ "
    r"PER_i=P_i/\sum_{q=1}^{4}EPS_{i,q};\ PBR_i=P_i/BPS_i;\ "
    r"\bar x=\sum_iw_ix_i;\ F_d=\sum_iw_{i,d}f_{i,d};\ S_{20}=\sum_{d=D-19}^{D}F_d;\ "
    r"streak=\max\{k:\forall j\in[0,k),F_{D-j}>0\}"
    r";\ distribution\%=100\sum_{T-1y<t\le T}cash_t/P;\ units\%=100(U_D/U_{D-20}-1)"
)


def metrics(fixture, type):
    """Return one factor's numerical cards without assigning a sticker."""
    readers = {"차트": chart.metrics, "매크로": macro.metrics, "밸류": valuation.metrics}
    if type in readers:
        cards = readers[type](fixture)
        if type == "밸류":
            distribution = etf.distribution_yield(fixture)
            if distribution:
                cards.append(distribution)
        return {"type": type, "metrics": cards}
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
    units = etf.units_change(fixture)
    if units:
        cards.append(units)
    return {"type": type, "metrics": cards}
