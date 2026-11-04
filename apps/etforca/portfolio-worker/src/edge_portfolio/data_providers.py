"""Direct public feeds for failures that generic page extraction cannot recover."""
import json
import asyncio
import logging
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pandas as pd

log = logging.getLogger(__name__)


def load_fallback_prices(symbol, as_of):
    import FinanceDataReader as fdr
    from .catalog import instruments
    from .market_data import positive_price
    if symbol == 'KRW=X':
        feed, currency, url = 'FRED:DEXKOUS', 'KRW', 'https://fred.stlouisfed.org/series/DEXKOUS'
    elif re.fullmatch(r'[0-9A-Z]{6}\.K[SQ]', symbol):
        feed, currency = 'NAVER:' + symbol[:-3], 'KRW'
        url = 'https://fchart.stock.naver.com/sise.nhn?timeframe=day&count=6000&requestType=0&symbol=' + symbol[:-3]
    else:
        feed, currency = 'YAHOO:' + symbol, 'USD'
        url = 'https://finance.yahoo.com/quote/' + symbol + '/history/'
    # Windows mktime rejects local midnight before epoch UTC in FDR's Yahoo adapter.
    frame = fdr.DataReader(feed, '1970-01-02', as_of.isoformat())
    if symbol == 'KRW=X':
        frame = frame.rename(columns={'DEXKOUS': 'Close'})
    if frame.empty or 'Close' not in frame:
        raise ValueError('PRICE_UNAVAILABLE')
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame = frame[~frame.index.duplicated(keep='last')].sort_index()
    frame = frame.loc[frame.index < pd.Timestamp(as_of)].dropna(subset=['Close'])
    if frame.empty or (as_of - frame.index[-1].date()).days > 7:
        raise ValueError('PRICE_STALE')
    close = frame['Close'][frame['Close'].map(positive_price)]
    if close.empty:
        raise ValueError('PRICE_INVALID')
    if (as_of - close.index[-1].date()).days > 7:
        raise ValueError('PRICE_STALE')
    if feed.startswith('NAVER:') and instruments().get('KR:' + symbol[:-3], {}).get('kind') == 'etf':
        # NAVER ETF chart Close already adjusts for distributions; never add cash again.
        frame['Adj Close'] = frame['Close']
    basis = 'total_return' if 'Adj Close' in frame else 'price'
    if basis == 'total_return':
        adjusted = frame['Adj Close'][frame['Adj Close'].map(positive_price)]
        if adjusted.empty or (as_of - adjusted.index[-1].date()).days > 7:
            basis = 'price'
    if basis == 'price':
        frame['Adj Close'] = frame['Close']
    return {'frame': frame, 'currency': currency, 'symbol': symbol, 'source_url': url,
            'return_basis': basis, 'provider': 'FinanceDataReader:' + feed,
            'price_recovery': {'status': 'repaired', 'method': 'alternate_feed', 'dates': []}}


async def fetch_official_source(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'www.samsungfund.com', 'm.samsungfund.com'} or parsed.path != '/etf/product/view.do':
        return None
    fund = parse_qs(parsed.query).get('id', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9]{6}', fund):
        return None
    api = f'https://www.samsungfund.com/api/v1/kodex/product/{fund}.do'
    try:
        async with asyncio.timeout(12), httpx.AsyncClient(timeout=12) as client:
            response = await client.get(api)
            response.raise_for_status()
            data = response.json()
        info, holdings = data['info'], data['pdf']
        product = info['product']
        def day(value):
            value = str(value or '')
            return f'{value[:4]}-{value[4:6]}-{value[6:8]}' if re.fullmatch(r'\d{8}', value) else value
        lines = [f'URL: {url}', f'공식 상품 데이터: {api}',
                 f"상품 {product.get('fNm', product.get('fundFnm', ''))} / 종목코드 {product['stkTicker']} / 상품 기준일 {day(product.get('gijunYMD'))}"]
        for key in ('kodexinfoidxProperty', 'idxProperty', 'fee', 'totFee', 'totalFee', 'fundFee', 'dividend', 'listD'):
            if product.get(key) is not None:
                lines.append(f'{key}: {product[key]}')
        lines.append('분배금 지급 내역: 아래 기준일과 지급일은 분배락일과 구분합니다; 이력 없음만으로 무분배를 확정하지 않습니다.')
        for row in info.get('divideList', []):
            lines.append(f"기준일 {day(row.get('BASIC_D'))}; 지급일 {day(row.get('PAY_D'))}; 주당 분배금(원) {row.get('DIVID_A')}; 과세 분배금(원) {row.get('TAX_DIVID_A')}")
        rows = holdings.get('list', [])
        lines.append(f"구성종목 기준일 {day(holdings.get('gijunYMD'))}; 공개 총 {holdings.get('totalCnt')}개 중 응답 {len(rows)}개; 비중 미제공은 0%가 아닙니다.")
        for row in rows:
            lines.append(json.dumps({key: row.get(key) for key in ('secNm', 'itmNo', 'ratio', 'evalA')}, ensure_ascii=False))
        if holdings.get('pdfExcelDownloadUrl'):
            lines.append('전체 구성종목 다운로드: ' + str(holdings['pdfExcelDownloadUrl']))
        return '\n'.join(lines)
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.warning('Official ETF feed unavailable: %s', type(error).__name__)
        return None  # The caller still attempts its existing web extraction path.
