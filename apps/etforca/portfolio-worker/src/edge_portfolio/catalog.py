"""ETF analysis directory, held-stock analysis catalog and expanded dashboard securities directory."""
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


def analysis_catalog(request):
    """분석이 고를 수 있는 종목: ETF 전부와 이 고객이 넣은 개별주뿐이다. 새 개별주는 목록에 없어 비교·결정·실행표·추천 어디서도 쓸 수 없다."""
    rows = {r['id']: r for r in directory()['instruments']}
    every = instruments()
    rows.update({h.instrument_id: every[h.instrument_id] for h in request.holdings
                 if h.kind == 'stock' and every.get(h.instrument_id, {}).get('kind') == 'stock'})
    return rows


def canonicalize(request):
    rows = directory()['instruments']
    stocks = [r for r in directory(include_stocks=True)['instruments'] if r['kind'] == 'stock']
    by_id = instruments()
    holdings = []
    for holding in request.holdings:
        if holding.instrument_id == 'CASH:KRW' or holding.kind == 'cash' and holding.name == '현금':
            holdings.append(holding.model_copy(update={'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW'}))
            continue
        if holding.kind == 'cash':
            raise ValueError('ETF_OR_CASH_ONLY')
        item = by_id.get(holding.instrument_id)
        if item is None and holding.instrument_id is None:
            # 이름만 온 옛 입력은 ETF에서 먼저 찾고, 없을 때만 개별주에서 찾는다.
            for pool in (rows, stocks):
                candidates = [r for r in pool if holding.name.casefold() in (r['ticker'].casefold(), r['name'].casefold(), f"{r['name']} ({r['ticker']})".casefold())]
                if candidates:
                    break
            item = candidates[0] if len(candidates) == 1 else None
        if item is None:
            raise ValueError('SELECT_LISTED_INSTRUMENT')
        holdings.append(holding.model_copy(update={'name': f"{item['name']} ({item['ticker']})", 'kind': 'stock' if item['kind'] == 'stock' else 'etf', 'instrument_id': item['id']}))
    return request.model_copy(update={'holdings': holdings})
