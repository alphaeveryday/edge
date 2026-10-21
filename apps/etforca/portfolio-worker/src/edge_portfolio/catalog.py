"""ETF analysis directory and expanded dashboard securities directory."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=2)
def directory(include_stocks=False):
    root = Path(__file__).parent / 'data'
    data = json.loads((root / 'etf_catalog.json').read_text(encoding='utf8'))
    if include_stocks:
        stocks = json.loads((root / 'stock_catalog.json').read_text(encoding='utf8'))
        data = {**data, 'sources': data['sources'] + stocks['sources'],
                'stock_fetched_at': stocks['fetched_at'],
                'counts': {market: data['counts'][market] + stocks['counts'][market] for market in ('KR', 'US')},
                'instruments': data['instruments'] + stocks['instruments']}
    return data


@lru_cache(maxsize=1)
def instruments():
    return {row['id']: row for row in directory(include_stocks=True)['instruments']}


def price_symbol(instrument_id):
    if instrument_id == 'FX':
        return 'KRW=X'
    item = instruments().get(instrument_id, {})
    return item.get('symbol') or instrument_id[3:] + ('.KS' if instrument_id.startswith('KR:') else '')


def canonicalize(request):
    rows = directory()['instruments']
    by_id = {r['id']: r for r in rows}
    holdings = []
    for holding in request.holdings:
        if holding.instrument_id == 'CASH:KRW' or holding.kind == 'cash' and holding.name == '현금':
            holdings.append(holding.model_copy(update={'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW'}))
            continue
        if holding.kind == 'cash':
            raise ValueError('ETF_OR_CASH_ONLY')
        item = by_id.get(holding.instrument_id)
        if item is None and holding.instrument_id is None:
            candidates = [r for r in rows if holding.name.casefold() in (r['ticker'].casefold(), r['name'].casefold(), f"{r['name']} ({r['ticker']})".casefold())]
            item = candidates[0] if len(candidates) == 1 else None
        if item is None:
            raise ValueError('SELECT_LISTED_ETF')
        holdings.append(holding.model_copy(update={'name': f"{item['name']} ({item['ticker']})", 'kind': 'etf', 'instrument_id': item['id']}))
    return request.model_copy(update={'holdings': holdings})
