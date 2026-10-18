"""Completed daily quotes in a cancellable process, bounded independently of research."""
import asyncio
import json
import math
import statistics
import subprocess
import sys
from datetime import date


def daily_quote(instrument_id, edition):
    """Use the last two completed exchange dates, never today's intraday bar."""
    import yfinance as yf
    market, ticker = instrument_id.split(':', 1)
    symbol = ticker + '.KS' if market == 'KR' else ticker
    frame = yf.Ticker(symbol).history(period='1mo', end=edition.isoformat(), auto_adjust=False, actions=False, timeout=12)
    rows = [(stamp.date(), float(close)) for stamp, close in frame['Close'].items()
            if stamp.date() < edition and math.isfinite(float(close)) and float(close) > 0]
    if len(rows) < 2 or (edition - rows[-1][0]).days > 7:
        return None
    previous, latest = rows[-2:]
    # Baseline excludes the latest move: the move under test must not widen its own threshold.
    prior = [(b / a - 1) * 100 for (_, a), (_, b) in zip(rows[:-1], rows[1:-1])]
    return {'id': 'price', 'url': f'https://finance.yahoo.com/quote/{symbol}/history/',
            'excerpt': json.dumps({'trading_date': latest[0].isoformat(), 'previous_date': previous[0].isoformat(),
                                  'close': latest[1], 'change_pct': round((latest[1] / previous[1] - 1) * 100, 2),
                                  # Context for the reader, not a cost basis: the newsletter never knows purchase prices.
                                  'week_pct': round((latest[1] / rows[-6][1] - 1) * 100, 2) if len(rows) >= 6 else None,
                                  'period_from': rows[0][0].isoformat(), 'period_pct': round((latest[1] / rows[0][1] - 1) * 100, 2),
                                  # Typical daily move of the window; F (exception) compares today's move against it.
                                  'sigma_pct': round(statistics.stdev(prior), 2) if len(prior) >= 3 else None,
                                  'currency': 'KRW' if market == 'KR' else 'USD', 'basis': 'unadjusted_close'})}



async def load_quote(instrument_id, edition):
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'edge_portfolio.newsletter_quote', instrument_id, edition.isoformat(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    try:
        async with asyncio.timeout(25):
            stdout, _ = await process.communicate()
        if process.returncode:
            raise ValueError('NEWSLETTER_QUOTE_UNAVAILABLE')
        return json.loads(stdout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


if __name__ == '__main__':
    print(json.dumps(daily_quote(sys.argv[1], date.fromisoformat(sys.argv[2])), allow_nan=False))
