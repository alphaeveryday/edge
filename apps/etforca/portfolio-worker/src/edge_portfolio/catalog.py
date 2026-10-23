"""ETF analysis directory, held-stock analysis catalog and expanded dashboard securities directory."""
import json
import re
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


TRAITS = {
    # (국내 상장 이름, 미국 상장 이름)
    'leverage': (r'레버리지|인버스|[1-9]X(?![A-Za-z])',
                 r'\b[1-9](?:\.\d+)?x\b|\bLeveraged\b|\bInverse\b|\bUltraPro\b|^ProShares (?:Ultra|Short)|^Direxion Daily'),
    'bond': (r'채(?!굴)', r'\bBonds?\b|Treasur|Fixed Income|Municipal|\bMuni\b|T-Bill|\bTIPS\b|Mortgage|Aggregate|Investment Grade|High Yield'),
    'gold': (r'금현물|골드|금선물|금채굴|금은', r'\bGold\b'),
    'dividend': (r'배당|인컴|커버드콜|프리미엄|리츠', r'Dividend|Income|Yield|Covered Call|Premium|\bREIT'),
    'semi_ai': (r'반도체|AI|인공지능|엔비디아|하이닉스', r'Semiconductor|\bSemis?\b|\bSOX|\bAI\b|Artificial Intelligence'),
    'us': (r'미국|S&P|나스닥|다우|필라델피아', r'.'),
    'foreign': (r'차이나|중국|일본|인도|베트남|글로벌|선진국|신흥|유럽|아시아|월드|대만|미국|S&P|나스닥|다우|필라델피아', r'.'),
}


def traits(item):
    """종목 이름으로 정한 성격(레버리지·채권·배당 등). 조건 칩 검사와 유형 카드가 같은 뜻을 쓴다.
    ponytail: 이름 규칙이라 이름에 드러나지 않는 상품(예: 분배금을 주지만 이름에 '배당'이 없는 지수 ETF)은 놓친다 — 상품 속성 자료가 생기면 그것으로 바꾼다"""
    if item.get('kind') == 'cash':
        return set()
    name = item.get('name', '')
    kr = item.get('market') == 'KR'
    if item.get('kind') == 'stock':
        return {'stock'} if kr else {'stock', 'us', 'foreign'}
    found = {key for key, (local, us) in TRAITS.items() if re.search(local if kr else us, name, 0 if kr else re.I)}
    if kr and '혼합' in name:
        found.discard('bond')  # 주식과 섞은 상품은 채권 ETF로 세지 않는다
    return found


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
