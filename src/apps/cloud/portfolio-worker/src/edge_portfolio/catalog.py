"""Shared ETF directory for search and server-side instrument validation."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def directory():
    return json.loads((Path(__file__).parent / 'data' / 'etf_catalog.json').read_text(encoding='utf8'))


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
