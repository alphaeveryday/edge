"""One immutable synthetic source history for chronological replay."""
from copy import deepcopy
from datetime import timedelta
from functools import lru_cache

from .common import decimal, instant, number
from .demo import make_fixture


@lru_cache(maxsize=1)
def _source():
    """Build the fixed source pool once; callers only receive deep copies."""
    data = make_fixture("followup", "2026-09-18T15:00:00+09:00")
    dates = data["trading_dates"]
    for row in data["news"]:
        at = "2026-09-14T12:00:00+09:00" if row["news_id"] == "n3" else "2026-09-14T07:00:00+09:00"
        row.update(published_at=at, available_at=at)
        row["title"] = row["title"].replace("SK하이닉스과", "SK하이닉스와")
        row["body"] = row["body"].replace("SK하이닉스이", "SK하이닉스가")
    data["flow"] = []
    for target in ("000660", "005930"):
        for index, day in enumerate(dates[-60:]):
            for investor, base in (("foreign", 100000000), ("institution", 50000000), ("individual", -150000000)):
                amount = base*(1 if index % 6 else -1)
                if day == "2026-09-15":
                    amount = base*12
                data["flow"].append({"instrument_id": target, "date": day, "investor": investor, "net_amount_krw": amount, "available_at": day+"T18:00:00+09:00", "finalized": True})
    data["etf_units"] = [{"instrument_id": "091160", "date": day, "units": 1000000+index*1000, "available_at": day+"T18:00:00+09:00"} for index, day in enumerate(dates[-30:])]
    series = {row["series"]: row for row in data["macro"]}
    data["macro"] = []
    for name, last in series.items():
        for index, day in enumerate(dates[-30:]):
            value = decimal(last["value"])+(index-29)*decimal(".01" if last["unit"] == "percent" else ".1")
            data["macro"].append(last | {"value": number(value), "observed_at": day+"T16:00:00+09:00", "available_at": day+"T18:00:00+09:00"})
    data["policy_decisions"][0]["available_at"] = "2026-09-11T08:00:00+09:00"
    data["price_snapshots"] = []
    etf_prices = {row["date"]: row for row in data["prices"] if row["instrument_id"] == "091160"}
    for day in range(14, 19):
        label = f"2026-09-{day}"
        close = etf_prices[label]["close"] if label in etf_prices else etf_prices["2026-09-17"]["close"]+10
        for hour in range(10, 15):
            for minute in range(5):
                at = instant(f"{label}T{hour:02}:00:00+09:00")-timedelta(minutes=4-minute)
                price = close-24+(hour-10)*5+minute
                data["price_snapshots"].append({"instrument_id": "091160", "price": price, "high": price+5, "low": close-50, "observed_at": at.isoformat(), "available_at": at.isoformat()})
    data["metadata"]["description"] = "고정된 가상 원자료의 공개시각을 따라 재생합니다. 실제 시장·기사·재무 사실이 아닙니다."
    return data


def make_replay_fixture(analysis_at):
    """Select an analysis cutoff without changing earlier facts.

    Args:
        analysis_at: An explicit timestamp on September 14 through 18, 2026.

    Returns:
        Independent context and raw source pool. Existing tools enforce cutoff.

    Raises:
        ValueError: The requested Korean trading date is outside this fixture.
    """
    cutoff = instant(analysis_at)
    if not "2026-09-14" <= cutoff.date().isoformat() <= "2026-09-18":
        raise ValueError("replay supports September 14 through 18 only")
    data = deepcopy(_source())
    eligible_dates = [day for day in data["trading_dates"] if day < cutoff.date().isoformat()]
    data["context"] = {"etf_code": "091160", "analysis_at": analysis_at, "flow_as_of_date": max(eligible_dates)}
    data["price_snapshots"] = [row for row in data["price_snapshots"] if instant(row["observed_at"]).date() == cutoff.date() and instant(row["available_at"]) <= cutoff]
    return data
