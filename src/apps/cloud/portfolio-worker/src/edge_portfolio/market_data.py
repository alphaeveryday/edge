"""Daily, KRW total-return comparisons; prices and arithmetic never come from the model."""
import asyncio
import logging
import math
import json
import sys
import subprocess
import tempfile
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

import pandas as pd
import yfinance as yf
import exchange_calendars as calendars

from .catalog import directory

log = logging.getLogger(__name__)
# KRX notice (2026 local election / Constitution Day), absent in calendars 4.13.2:
# https://stock.mk.co.kr/news/disclosure/template/1029043
KRX_EXTRA_CLOSURES = pd.DatetimeIndex(['2026-06-03', '2026-07-17'])


def expected_sessions(instrument_id, start, end):
    is_kr = instrument_id.startswith('KR:')
    calendar = calendars.get_calendar('XKRX' if is_kr else 'XNYS', start=start - pd.Timedelta(days=30), end=end + pd.Timedelta(days=30))
    sessions = calendar.sessions_in_range(start, end).tz_localize(None)
    return sessions.difference(KRX_EXTRA_CLOSURES) if is_kr else sessions


def year_before(day, years=1):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def load_prices(symbol, as_of):
    yf.set_tz_cache_location(str(Path(tempfile.gettempdir()) / 'portfolio-yfinance'))
    ticker = yf.Ticker(symbol)
    frame = ticker.history(start=(year_before(as_of) - timedelta(days=10)).isoformat(), end=as_of.isoformat(),
                           interval='1d', auto_adjust=False, actions=True, timeout=12)
    metadata = ticker.history_metadata
    if frame.empty or not {'Adj Close', 'Close'} <= set(frame.columns):
        raise ValueError('PRICE_UNAVAILABLE')
    frame.index = pd.to_datetime([i.date() for i in frame.index])
    frame = frame[~frame.index.duplicated(keep='last')].sort_index()
    frame = frame.loc[frame.index < pd.Timestamp(as_of)]
    if frame.empty or (as_of - frame.index[-1].date()).days > 7:
        raise ValueError('PRICE_STALE')
    if not frame[['Adj Close', 'Close']].map(lambda v: math.isfinite(v) and v > 0).all().all():
        raise ValueError('PRICE_INVALID')
    return {'frame': frame, 'currency': metadata.get('currency'), 'symbol': symbol,
            'source_url': f'https://finance.yahoo.com/quote/{quote(symbol, safe="")}/history/'}


async def fetch_prices(symbol, as_of):
    """A cancellable process bounds library calls that can block beyond HTTP timeout."""
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'edge_portfolio.price_fetch', symbol, as_of.isoformat(),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    try:
        async with asyncio.timeout(25):
            stdout, _ = await process.communicate()
        if process.returncode:
            raise ValueError('PRICE_PROVIDER_UNAVAILABLE')
        record = json.loads(stdout)
        record['frame'] = pd.DataFrame(record['frame']).set_index('date')
        record['frame'].index = pd.to_datetime(record['frame'].index)
        return record
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def weights(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20 or any(not isinstance(row, dict) for row in rows):
        raise ValueError('INVALID_MARKET_WEIGHTS')
    result = {}
    for row in rows:
        instrument = row.get('instrument_id')
        value = row.get('weight_pct')
        if not instrument or value is None or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError('INVALID_MARKET_WEIGHTS')
        result[instrument] = result.get(instrument, Decimal(0)) + Decimal(str(value))
    if not result or len(result) > 20 or abs(sum(result.values()) - 100) > Decimal('.1'):
        raise ValueError('INVALID_MARKET_WEIGHTS')
    total = sum(result.values())
    return {k: float(v / total) for k, v in result.items()}


def normalized_prices(ids, records, as_of):
    series, urls = {}, []
    for key in ids - {'CASH:KRW'}:
        record = records[key]
        values = record['frame']['Adj Close']
        if record['currency'] == 'USD':
            fx = records['FX']['frame']['Close']
            start = pd.Timestamp(year_before(min(values.index[-1], fx.index[-1]).date()))
            expected_fx = expected_sessions(key, start, min(values.index[-1], fx.index[-1]))
            if len(expected_fx.difference(fx.dropna().index)):
                raise ValueError('MISSING_FX_SESSIONS')
            index = values.index.union(fx.index)
            # No backward filling: a future FX quote must never value an earlier day.
            values = values.reindex(index).ffill(limit=5) * fx.reindex(index).ffill(limit=5)
            values = values.loc[:min(record['frame'].index[-1], fx.index[-1])]
        elif record['currency'] != 'KRW':
            raise ValueError('UNSUPPORTED_CURRENCY')
        series[key] = values
        urls.append({'instrument_id': key, 'url': record['source_url']})
    if 'FX' in records:
        urls.append({'instrument_id': 'USD/KRW', 'url': records['FX']['source_url']})
    if series:
        end = min(s.last_valid_index() for s in series.values())
        start = pd.Timestamp(year_before(end.date()))
        for key in ids - {'CASH:KRW'}:
            expected = expected_sessions(key, start, end)
            raw = records[key]['frame']['Adj Close']
            if len(expected.difference(raw.dropna().index)):
                raise ValueError('MISSING_TRADING_SESSIONS')
        union = pd.DatetimeIndex(sorted(set().union(*(s.index for s in series.values()))))
        frame = pd.DataFrame({key: s.reindex(union).ffill(limit=5) for key, s in series.items()})
        frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
        if len(frame) < 200 or (frame.index[0] - start).days > 7 or frame.isna().any().any():
            raise ValueError('ONE_YEAR_HISTORY_INCOMPLETE')
        if any((end.date() - records[key]['frame'].index[-1].date()).days > 7 for key in ids - {'CASH:KRW'}):
            raise ValueError('PRICE_STALE')
    else:
        end = pd.Timestamp(as_of - timedelta(days=1))
        frame = pd.DataFrame(index=pd.bdate_range(year_before(end.date()), end))
    frame['CASH:KRW'] = 1.0
    return frame / frame.iloc[0], urls


def comparison(target, current, records, as_of):
    frame, urls = normalized_prices(set(target) | set(current or {}), records, as_of)

    def value(allocation):
        return sum(frame[key] * weight * 1000000 for key, weight in allocation.items())

    recommended = value(target)
    existing = value(current) if current else None
    points = [{'date': day.date().isoformat(), 'recommended': round(float(recommended.loc[day]), 2),
               'current': round(float(existing.loc[day]), 2) if existing is not None else None} for day in frame.index]
    return {'points': points, 'interval': '1d', 'currency': 'KRW', 'initial_krw': 1000000,
            'basis': '최근 1년 · 100만 원을 넣었다면 (원)',
            'method': '시작 비중으로 매수 후 유지 · 수정 종가 기준 분배금 재투자 · 미국 상장 ETF 환율 반영 · 세금·매매비용 제외 · 휴장일 직전 가격 유지',
            'source_url': 'https://finance.yahoo.com/', 'sources': urls,
            'start_date': points[0]['date'], 'end_date': points[-1]['date']}


class MarketData:
    def __init__(self, request, as_of=None, loader=None):
        self.as_of = as_of or date.today()
        self.loader = loader
        self.records = {}
        self.results = {}
        self.queried = set()
        self.calls = 0
        self.semaphore = asyncio.Semaphore(3)
        self.catalog = {r['id']: r for r in directory()['instruments']}
        total = sum(h.amount_krw or 0 for h in request.holdings)
        self.current = None
        if request.input_mode != 'names' and total and all(h.instrument_id for h in request.holdings):
            self.current = weights([{'instrument_id': h.instrument_id, 'weight_pct': float(h.amount_krw / total * 100)} for h in request.holdings])

    async def get(self, rows):
        target = weights(rows)
        key = tuple(sorted(target.items()))
        if key in self.results:
            return self.results[key]
        self.calls += 1
        if self.calls > 3:
            return {'status': 'unavailable', 'reason': 'MARKET_DATA_LIMIT'}
        ids = set(target) | set(self.current or {})
        if any(i != 'CASH:KRW' and i not in self.catalog for i in ids):
            raise ValueError('UNREGISTERED_INSTRUMENT')
        needed = {i: self.catalog[i]['ticker'] + ('.KS' if self.catalog[i]['market'] == 'KR' else '') for i in ids - {'CASH:KRW'}}
        if any(self.catalog[i]['market'] == 'US' for i in ids - {'CASH:KRW'}):
            needed['FX'] = 'KRW=X'
        try:
            async def fetch(instrument, symbol):
                if instrument not in self.records:
                    async with self.semaphore:
                        try:
                            self.records[instrument] = self.loader(symbol, self.as_of) if self.loader else await fetch_prices(symbol, self.as_of)
                        except Exception as error:
                            log.warning('Price provider failed for %s: %s', instrument, type(error).__name__)
            primary = set(target) - {'CASH:KRW'}
            if any(self.catalog[i]['market'] == 'US' for i in primary):
                primary.add('FX')
            try:
                async with asyncio.timeout(65):
                    await asyncio.gather(*(fetch(i, needed[i]) for i in sorted(primary)))
                    await asyncio.gather(*(fetch(i, symbol) for i, symbol in needed.items() if i not in primary))
            except TimeoutError:
                # A slow optional comparison must not discard ready recommendation data.
                log.warning('Price collection deadline; publishing only complete series')
            history = comparison(target, None, self.records, self.as_of)
            if self.current:
                try:
                    history = comparison(target, self.current, self.records, self.as_of)
                except (KeyError, ValueError):
                    history['current_unavailable_reason'] = '현재 구성은 최근 1년 자료가 부족해 비교에서 제외했어요.'
            result = {'status': 'available', 'history': history,
                      'return_1y_pct': round((history['points'][-1]['recommended'] / 1000000 - 1) * 100, 2)}
        except Exception as error:
            # Optional market data failure must not destroy the research report.
            log.warning('Market history unavailable: %s', type(error).__name__)
            result = {'status': 'unavailable', 'reason': str(error)[:120] if isinstance(error, ValueError) else 'PRICE_PROVIDER_UNAVAILABLE'}
        self.results[key] = result
        return result

    async def tool(self, args):
        result = await self.get(args.get('allocations', []))
        if result['status'] != 'available':
            return result
        self.queried.add(tuple(sorted(weights(args['allocations']).items())))
        return {k: v for k, v in result.items() if k != 'history'} | {
            'start_date': result['history']['start_date'], 'end_date': result['history']['end_date'],
            'method': result['history']['method'], 'sources': result['history']['sources'],
            'dividends': [self.dividends(i) for i in weights(args['allocations']) if i != 'CASH:KRW'],
            'instruction': '과거 수익률은 미래 예상 수익률이 아닙니다. 시장 근거를 대조해 추정 근거를 설명하고 양수로 보정하지 마세요.'}

    def dividends(self, instrument_id):
        record = self.records.get(instrument_id)
        if record is None or 'Dividends' not in record['frame']:
            return None
        frame = record['frame']
        end = frame.index[-1].date()
        start = year_before(end)
        if frame.index[0].date() > start + timedelta(days=7):
            return None
        if len(expected_sessions(instrument_id, pd.Timestamp(start), pd.Timestamp(end)).difference(frame['Close'].dropna().index)):
            return None
        recent = frame.loc[(frame.index.date > start) & (frame.index.date <= end)]
        values = recent['Dividends']
        if values.isna().any() or not values.map(lambda value: math.isfinite(value) and value >= 0).all():
            return None
        total = float(values.sum())
        price = float(frame['Close'].iloc[-1])
        return {'instrument_id': instrument_id, 'trailing_yield_pct': round(total / price * 100, 4),
                'as_of': end.isoformat(), 'period_start': start.isoformat(), 'dividends_per_share': total,
                'price': price, 'currency': record['currency'], 'source_url': record['source_url'],
                'method': 'Yahoo Finance 최근 12개월 분배금 합계 ÷ 최근 종가 · 분배락일 기준',
                'events': [{'date': day.date().isoformat(), 'amount': float(value)} for day, value in values.items() if value > 0],
                'evidence': []}


TOOL = {'type': 'function', 'function': {'name': 'read_portfolio_history',
        'description': '등록 ETF와 현금의 추천 비중으로 최근 1년 일별 원화 총수익률을 계산한다. 최종 추천 비중 확정 후 호출한다. 최대 3개 구성.',
        'parameters': {'type': 'object', 'properties': {'allocations': {'type': 'array', 'minItems': 1, 'maxItems': 20,
            'items': {'type': 'object', 'properties': {'instrument_id': {'type': 'string'}, 'weight_pct': {'type': 'number'}},
                      'required': ['instrument_id', 'weight_pct'], 'additionalProperties': False}}},
            'required': ['allocations'], 'additionalProperties': False}}}


def cited(evidence, sources):
    lookup = {(s['id'], p['id']): (p['text'], s.get('url', '')) for s in sources for p in s.get('passages', [])}
    if any((e.source_id, e.passage_id) not in lookup for e in evidence):
        raise ValueError('Market estimates must cite registered passages')
    return [{'source_id': e.source_id, 'passage_id': e.passage_id,
             'quote': lookup[e.source_id, e.passage_id][0], 'url': lookup[e.source_id, e.passage_id][1]} for e in evidence]


async def enrich_report(published, final, market, sources):
    plan = published['recommendation']
    dividends = {}
    target = weights(plan['allocations'])
    forecast_evidence = cited(final.return_forecast.evidence, sources) if final.return_forecast else []
    queried = tuple(sorted(target.items())) in market.queried
    if final.return_forecast and not queried:
        raise ValueError('Call read_portfolio_history with the final allocation before writing return_forecast')
    result = await market.get(plan['allocations'])
    plan['target_return_1y'] = 7
    # Web research informs the analysis, but numeric yields use identified cash events.
    for instrument in sorted(set(target) - {'CASH:KRW'}):
        if instrument not in dividends:
            observation = market.dividends(instrument)
            if observation:
                dividends[instrument] = observation
    plan['dividend_observations'] = list(dividends.values())
    covered = all(key == 'CASH:KRW' or key in dividends for key in target)
    plan['dividend_yield_1y'] = round(sum(weight * dividends[key]['trailing_yield_pct'] for key, weight in target.items() if key != 'CASH:KRW'), 2) if covered else None
    plan['dividend_basis'] = '최근 12개월 세전 분배금 수익률 · 추천 비중 가중 평균 · 미래 배당 보장 아님' if covered else '추천 ETF의 최근 12개월 배당률을 웹 자료에서 모두 확인하지 못했어요.'
    if result['status'] == 'available':
        plan['historical_performance'] = result['history']
        plan['historical_return_1y'] = result['return_1y_pct']
        plan['unavailable_reason'] = ''
        if final.return_forecast and queried:
            plan['expected_return_1y'] = final.return_forecast.percent
            plan['return_forecast'] = {'reason': final.return_forecast.reason, 'evidence': forecast_evidence,
                                       'method': '최근 1년 과거 성과와 시장 근거를 고려한 AI 추정 · 수익 보장 아님'}
    else:
        plan['unavailable_reason'] = '최근 1년 전체의 일별 가격·환율을 확보하지 못해 과거 성과를 표시하지 못했어요.'
        plan['market_data_failure'] = result['reason']
