"""Small reproducible synthetic market cases, never production market records."""
from datetime import timedelta

from edge_analysis_v2.tools.fixture_data.common import decimal, instant, number


def build_demo_fixture(scenario="baseline", analysis_at="2026-09-21T10:00:00+09:00"):
    """Build a complete two-equity case for agent and persistence integration.

    Args:
        scenario: Baseline, quiet, unusual_flow, competing_signals or followup.
        analysis_at: Fixed analysis cutoff with explicit timezone.

    Returns:
        Independent synthetic raw observations for every implemented category.
    """
    if scenario not in ("baseline", "quiet", "unusual_flow", "competing_signals", "followup"):
        raise ValueError("unknown synthetic scenario")
    cutoff = instant(analysis_at)
    day = cutoff.date()-timedelta(days=400)
    dates = []
    while day < cutoff.date():
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    data = {"context": {"etf_code": "DEMO_ETF", "analysis_at": analysis_at, "flow_as_of_date": dates[-1]}, "trading_dates": dates, "holdings": [], "flow": [], "prices": [], "price_snapshots": [], "macro": [], "financials": [], "news": [], "macro_trading_dates": {"commodity": dates}, "policy_decisions": []}
    for target, weight in (("DEMO_CHIP", .6), ("DEMO_MEMORY", .4)):
        data["holdings"].append({"instrument_id": target, "weight": weight, "as_of_date": dates[0], "available_at": dates[0]+"T08:00:00+09:00"})
        for i, day in enumerate(dates[-30:]):
            for investor, base in (("foreign", 100000000), ("institution", 50000000), ("individual", -150000000)):
                amount = base*(1 if i % 6 else -1)
                if scenario == "quiet":
                    amount = int(base/10000)*(1 if i % 2 else -1)
                if scenario == "unusual_flow" and i == 29:
                    amount *= 12
                data["flow"].append({"instrument_id": target, "date": day, "investor": investor, "net_amount_krw": amount, "available_at": day+"T18:00:00+09:00", "finalized": True})
        # Quarters precede the replay date, avoiding accidental future fiscal data.
        quarter = (cutoff.year*4+(cutoff.month-1)//3)-2
        for index in range(quarter-3, quarter+1):
            year, q = divmod(index, 4)
            data["financials"].append({"instrument_id": target, "period": f"{year}-Q{q+1}", "eps": 1500 if target == "DEMO_CHIP" else 1000, "bps": 50000, "available_at": dates[-20]+"T18:00:00+09:00"})
    for target, scale in (("DEMO_ETF", 1), ("DEMO_CHIP", 5), ("DEMO_MEMORY", 3)):
        for i, day in enumerate(dates):
            close = scale*(10000+i*10+(i % 7)*5)
            if scenario == "quiet":
                close = scale*(10000+(5 if i % 2 else -5))
            if scenario == "competing_signals" and i > len(dates)-8:
                close -= scale*(i-(len(dates)-8))*80
            data["prices"].append({"instrument_id": target, "date": day, "open": close-10, "high": close+50, "low": close-50, "close": close, "volume": 1000000, "turnover": close*1000000, "available_at": day+"T18:00:00+09:00"})
    last = [r for r in data["prices"] if r["instrument_id"] == "DEMO_ETF"][-1]["close"]
    if cutoff.hour >= 9:
        for index in range(5):
            at = cutoff-timedelta(minutes=4-index)
            value = last+100+index*10
            if scenario == "quiet":
                value = last
            data["price_snapshots"].append({"instrument_id": "DEMO_ETF", "price": value, "high": value+20, "low": last-20, "observed_at": at.isoformat(), "available_at": at.isoformat()})
    for series, base, unit in (("usd_krw", 1340, "KRW_per_USD"), ("kr_10y_yield", 3.2, "percent"), ("us_10y_yield", 4.1, "percent"), ("kr_cpi_yoy", 2.1, "percent"), ("brent_spot_usd", 75, "USD_per_barrel"), ("commodity", 100, "index")):
        for i, day in enumerate(dates[-21:]):
            value = decimal(base) if scenario == "quiet" else decimal(base)+(i-20)*decimal(".01" if unit == "percent" else ".1")
            data["macro"].append({"series": series, "value": number(value), "unit": unit, "subject": "산업 원자재 지수" if series == "commodity" else series, "observed_at": day+"T16:00:00+09:00", "available_at": day+"T18:00:00+09:00"})
    published = cutoff-timedelta(minutes=60)
    if scenario == "quiet":
        published = cutoff-timedelta(days=7)
    data["policy_decisions"] = [{"decision_at": (cutoff+timedelta(days=10)).isoformat(), "available_at": published.isoformat(), "subject": "한국은행"}]
    articles = [("n1", "AI 서버 고객사, 가상칩과 2조원 규모 HBM 공급 계약", "가상칩이 AI 서버 고객사와 2조원 규모 HBM 공급 계약을 체결했다. 납품은 다음 분기 시작하며 고부가 메모리 매출 확대가 기대된다.", "contract", "signed"), ("n2", "가상메모리 평택 생산라인 장비 점검으로 사흘 가동 중단", "가상메모리는 평택 생산라인의 장비 점검으로 사흘 동안 가동을 중단한다고 발표했다. 일부 출하 일정 지연이 예상된다.", "shutdown", "announced")]
    if scenario == "followup":
        articles.append(("n3", "가상칩 HBM 공급 계약 규모 2조원에서 3조원으로 확대", "AI 서버 고객사가 추가 물량을 주문해 가상칩 HBM 공급 계약 규모가 2조원에서 3조원으로 확대됐다. 증액분은 내년 상반기 납품한다.", "contract", "expanded"))
    for index, (identity, title, body, thread, stage) in enumerate(articles):
        at = (published+timedelta(minutes=index)).isoformat()
        data["news"].append({"news_id": identity, "title": "[가상] "+title, "body": body, "published_at": at, "available_at": at, "thread_id": thread, "stage": stage, "event_id": identity})
    data["distribution_history_start"] = dates[0]
    data["distributions"] = [{"instrument_id": "DEMO_ETF", "paid_at": day+"T09:00:00+09:00", "amount_per_unit": 100, "available_at": day+"T09:00:00+09:00"} for day in (dates[-20], dates[-80], dates[-140])]
    data["etf_units"] = [{"instrument_id": "DEMO_ETF", "date": day, "units": 1000000+i*1000, "available_at": day+"T18:00:00+09:00"} for i, day in enumerate(dates[-21:])]
    return data


def make_fixture(scenario="baseline", analysis_at=None):
    """Return explicitly synthetic data with familiar Korean instrument labels."""
    data = build_demo_fixture(scenario, analysis_at or "2026-09-21T10:00:00+09:00")
    identities = {"DEMO_ETF": "091160", "DEMO_CHIP": "000660", "DEMO_MEMORY": "005930"}
    data["context"]["etf_code"] = identities[data["context"]["etf_code"]]
    data["metadata"] = {"synthetic": True, "description": "시장·기사·재무는 기능 검증용 가상 자료입니다. 실존 종목의 실제 사실이 아닙니다."}
    data["instruments"] = [{"instrument_id": "091160", "name": "KODEX 반도체"}, {"instrument_id": "000660", "name": "SK하이닉스"}, {"instrument_id": "005930", "name": "삼성전자"}]
    for collection in ("holdings", "flow", "prices", "price_snapshots", "financials", "distributions", "etf_units"):
        for row in data[collection]:
            row["instrument_id"] = identities[row["instrument_id"]]
    for row in data["news"]:
        for key in ("title", "body"):
            row[key] = row[key].replace("[가상] ", "").replace("가상칩", "SK하이닉스").replace("가상메모리", "삼성전자").replace("AI 서버 고객사", "델테크놀로지스")
    return data
