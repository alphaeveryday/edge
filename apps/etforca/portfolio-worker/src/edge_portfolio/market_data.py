"""Daily, KRW total-return comparisons; prices and arithmetic never come from the model."""
import asyncio
import logging
import math
import json
import sys
import subprocess
import tempfile
from contextlib import asynccontextmanager
from threading import BoundedSemaphore
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from functools import lru_cache
from numbers import Real
from urllib.parse import quote

import pandas as pd
import yfinance as yf
import exchange_calendars as calendars

from .catalog import directory

log = logging.getLogger(__name__)
# The analysis process and price libraries share one container memory limit.
_PRICE_SLOTS = BoundedSemaphore(1)
# KRX notice (2026 local election / Constitution Day), absent in calendars 4.13.2:
# https://stock.mk.co.kr/news/disclosure/template/1029043
KRX_EXTRA_CLOSURES = pd.DatetimeIndex(['2026-06-03', '2026-07-17'])
PRICE_FAILURES = {'PRICE_UNAVAILABLE', 'PRICE_STALE', 'PRICE_INVALID', 'PRICE_PROVIDER_UNAVAILABLE',
                  'PRICE_PROVIDER_TIMEOUT', 'PRICE_COLLECTION_TIMEOUT', 'PRICE_QUEUE_TIMEOUT', 'PRICE_PROVIDER_RATE_LIMIT', 'PRICE_RESPONSE_INVALID',
                  'MISSING_TRADING_SESSIONS', 'MISSING_FX_SESSIONS', 'NO_COMMON_PRICE_HISTORY',
                  'UNSUPPORTED_CURRENCY', 'INSUFFICIENT_COMPARISON_SESSIONS', 'TOTAL_RETURN_UNAVAILABLE'}
TRANSIENT_PRICE_FAILURES = {'PRICE_UNAVAILABLE', 'PRICE_PROVIDER_UNAVAILABLE', 'PRICE_PROVIDER_TIMEOUT',
                            'PRICE_COLLECTION_TIMEOUT', 'PRICE_QUEUE_TIMEOUT',
                            'PRICE_PROVIDER_RATE_LIMIT', 'PRICE_RESPONSE_INVALID'}


def price_failure(error):
    if isinstance(error, TimeoutError) or type(error).__name__ in {'Timeout', 'ReadTimeout', 'ConnectTimeout'}:
        return 'PRICE_PROVIDER_TIMEOUT'
    if type(error).__name__ == 'YFRateLimitError':
        return 'PRICE_PROVIDER_RATE_LIMIT'
    return str(error) if isinstance(error, ValueError) and str(error) in PRICE_FAILURES else 'PRICE_PROVIDER_UNAVAILABLE'


def expected_sessions(instrument_id, start, end):
    return _market_sessions(instrument_id.startswith('KR:'), start, end)


@lru_cache(maxsize=128)
def _market_sessions(is_kr, start, end):
    calendar = _session_calendar(is_kr, start.year, end.year)
    sessions = calendar.sessions_in_range(start, end).tz_localize(None)
    return sessions.difference(KRX_EXTRA_CLOSURES) if is_kr else sessions


@lru_cache(maxsize=16)
def _session_calendar(is_kr, first_year, last_year):
    return calendars.get_calendar('XKRX' if is_kr else 'XNYS', start=f'{first_year - 1}-12-01', end=f'{last_year + 1}-02-01')


def year_before(day, years=1):
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def positive_price(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def repair_prices(ticker, frame, symbol, as_of, currency, *, since=None):
    columns = ['Adj Close', 'Close']
    instrument = ('KR:' if symbol.endswith('.KS') else 'US:') + symbol
    start = pd.Timestamp(since) if since is not None else max(frame.index[0], pd.Timestamp(year_before(as_of, 2)))
    expected = expected_sessions(instrument, start, as_of - timedelta(days=1))
    valid = frame[columns].map(positive_price).all(axis=1)
    missing = expected.difference(frame.index[valid])
    if missing.empty:
        return frame, {'status': 'not_needed', 'dates': []}
    try:
        repaired = ticker.history(start=start.date().isoformat(), end=as_of.isoformat(), interval='1d',
                                  auto_adjust=False, actions=True, repair=True, timeout=12)
        repaired.index = pd.to_datetime([i.date() for i in repaired.index])
        repaired = repaired[~repaired.index.duplicated(keep='last')].sort_index()
        if ticker.history_metadata.get('currency') != currency:
            return frame, {'status': 'rejected', 'reason': 'REPAIR_CURRENCY_MISMATCH', 'dates': []}
        if not set(columns) <= set(repaired.columns):
            return frame, {'status': 'unavailable', 'reason': 'PRICE_RESPONSE_INVALID', 'dates': []}
        anchors = frame.index[valid].intersection(repaired.index)
        overlap = frame.index.intersection(repaired.index)
        if len(anchors) < 2 or not all(not positive_price(a) or (positive_price(b) and math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-8))
                for a, b in zip(frame.loc[overlap, columns].to_numpy().flat, repaired.loc[overlap, columns].to_numpy().flat)):
            return frame, {'status': 'rejected', 'reason': 'REPAIR_BASIS_MISMATCH', 'dates': []}
        result = frame.copy()
        recovered = []
        for day in missing.intersection(repaired.index):
            if not repaired.loc[day, columns].map(positive_price).all():
                continue
            if day in result.index:
                for column in columns:
                    if not positive_price(result.loc[day, column]):
                        result.loc[day, column] = repaired.loc[day, column]
            else:
                result.loc[day] = repaired.loc[day].reindex(result.columns)
            recovered.append(day.date().isoformat())
        return result.sort_index(), {'status': 'repaired' if len(recovered) == len(missing) else 'partial',
            'dates': recovered, 'unresolved_dates': [d.date().isoformat() for d in missing if d.date().isoformat() not in recovered],
            'method': 'Yahoo Finance intraday reconstruction; original valid prices and dividend events retained'}
    except Exception as error:
        # Retain the original history: a defect outside the common window need not block it.
        return frame, {'status': 'unavailable', 'reason': price_failure(error), 'dates': []}


def load_prices(symbol, as_of, *, on_raw=None, repair_since=None, history_start=None):
    yf.set_tz_cache_location(str(Path(tempfile.gettempdir()) / 'portfolio-yfinance'))
    ticker = yf.Ticker(symbol)
    actions_day = datetime.now(ZoneInfo('Asia/Seoul')).date()
    window = {'start': history_start.isoformat()} if history_start else {'period': 'max'}
    frame = ticker.history(**window, end=(max(as_of, actions_day) + timedelta(days=1)).isoformat(),
                           interval='1d', auto_adjust=False, actions=True, timeout=12)
    metadata = ticker.history_metadata
    if frame.empty or not {'Adj Close', 'Close'} <= set(frame.columns):
        raise ValueError('PRICE_UNAVAILABLE')
    frame.index = pd.to_datetime([i.date() for i in frame.index])
    frame = frame[~frame.index.duplicated(keep='last')].sort_index()
    # Inspect current-session actions without publishing its unfinished prices.
    pending_splits = [str(stamp.date()) for stamp, ratio in frame.get('Stock Splits', pd.Series(dtype=float)).items()
                      if stamp.date() >= as_of and positive_price(ratio)]
    frame = frame.loc[frame.index < pd.Timestamp(as_of)]
    if frame.empty or (as_of - frame.index[-1].date()).days > 7:
        raise ValueError('PRICE_STALE')
    held_since = repair_since
    repair_since = min(repair_since, year_before(frame.index[-1].date())) if repair_since is not None else year_before(as_of, 2)
    repair_start = max(frame.index[0].date(), repair_since)
    if held_since is not None:
        repair_start = min(held_since, repair_start)
    if history_start:
        repair_since = repair_start = max(history_start, repair_start)
    original = {'frame': frame, 'currency': metadata.get('currency'), 'symbol': symbol, 'repair_since': repair_since.isoformat(),
                'history_complete': history_start is None,
                'pending_splits': pending_splits,
                'source_url': f'https://finance.yahoo.com/quote/{quote(symbol, safe="")}/history/'}
    if on_raw:
        on_raw(original)
    frame, recovery = repair_prices(ticker, frame, symbol, as_of, metadata.get('currency'), since=repair_start)
    # Validate values inside the chosen comparison window, not unrelated older history.
    return {**original, 'frame': frame, 'price_recovery': recovery}


def price_status(record, as_of):
    columns = [record['frame'][name] for name in ('Adj Close', 'Close')]
    valid = [column[column.map(positive_price)] for column in columns]
    if any(column.empty for column in valid):
        return 'PRICE_INVALID'
    if any((as_of - column.index[-1].date()).days > 7 for column in valid):
        return 'PRICE_STALE'
    return None


def with_actual_prices(returns, actual):
    frame = actual['frame'].copy()
    frame['Adj Close'] = returns['frame']['Adj Close'].reindex(frame.index)
    return {**actual, 'frame': frame, 'return_basis': returns['return_basis'],
            'return_source_url': returns['source_url']}


@asynccontextmanager
async def price_slot():
    # Admission is separate from provider timeouts; a busy worker is not a bad source.
    while not _PRICE_SLOTS.acquire(blocking=False):
        await asyncio.sleep(.05)
    try:
        yield
    finally:
        _PRICE_SLOTS.release()


async def fetch_prices(symbol, as_of, *, on_raw=None, on_primary=None, provider_timeout=None, repair_since=None, history_start=None):
    async with price_slot():
        try:
            options = {k: v for k, v in {'repair_since': repair_since, 'history_start': history_start}.items() if v is not None}
            original = await asyncio.wait_for(_fetch_prices(symbol, as_of, on_raw=on_raw, **options), timeout=provider_timeout)
            if on_primary:
                on_primary(original)
            primary_failure = price_status(original, as_of)
            if primary_failure is None:
                return original
        except (ValueError, TimeoutError) as error:
            primary_failure = price_failure(error)
        record = await asyncio.wait_for(_fetch_prices(symbol, as_of, on_raw=on_raw, provider='fallback'), timeout=provider_timeout)
        record['primary_failure'] = primary_failure
        return record


async def _fetch_prices(symbol, as_of, *, on_raw=None, provider='yahoo', repair_since=None, history_start=None):
    with tempfile.TemporaryDirectory(prefix='portfolio-prices-') as directory:
        options = {k: v for k, v in {'repair_since': repair_since, 'history_start': history_start}.items() if v is not None}
        return await _fetch_prices_with_checkpoint(symbol, as_of, Path(directory) / 'original.json', on_raw, provider, **options)


async def _fetch_prices_with_checkpoint(symbol, as_of, checkpoint, on_raw=None, provider='yahoo', *, repair_since=None, history_start=None):
    """A cancellable process bounds library calls that can block beyond HTTP timeout."""
    options = [repair_since.isoformat() if repair_since else '-', history_start.isoformat()] if history_start else ([repair_since.isoformat()] if repair_since else [])
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'edge_portfolio.price_fetch', symbol, as_of.isoformat(), str(checkpoint), provider, *options,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    try:
        repair_failure = None
        try:
            async with asyncio.timeout(25):
                stdout, _ = await process.communicate()
        except TimeoutError:
            if not checkpoint.is_file():
                raise
            stdout = checkpoint.read_bytes()
            repair_failure = 'PRICE_PROVIDER_TIMEOUT'
        if process.returncode and checkpoint.is_file():
            stdout = checkpoint.read_bytes()
            repair_failure = 'PRICE_PROVIDER_UNAVAILABLE'
        record = decode_prices(stdout)
        if process.returncode and not repair_failure:
            raise ValueError('PRICE_PROVIDER_UNAVAILABLE')
        if repair_failure:
            record['price_recovery'] = {'status': 'unavailable', 'reason': repair_failure, 'dates': []}
        return record
    except asyncio.CancelledError:
        if on_raw and checkpoint.is_file():
            try:
                record = decode_prices(checkpoint.read_bytes())
                record['price_recovery'] = {'status': 'unavailable', 'reason': 'PRICE_REPAIR_INTERRUPTED', 'dates': []}
                on_raw(record)
            except Exception as error:
                log.warning('Price checkpoint unavailable: %s', type(error).__name__)
        raise  # Preserve whole-analysis cancellation; the collector may use saved prices.
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def decode_prices(body):
    try:
        record = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise ValueError('PRICE_RESPONSE_INVALID') from error
    if not isinstance(record, dict):
        raise ValueError('PRICE_RESPONSE_INVALID')
    if 'error' in record:
        raise ValueError(record['error'] if record['error'] in PRICE_FAILURES else 'PRICE_PROVIDER_UNAVAILABLE')
    try:
        record['frame'] = pd.DataFrame(record['frame']).set_index('date')
        record['frame'].index = pd.to_datetime(record['frame'].index)
        if not {'Adj Close', 'Close'} <= set(record['frame'].columns):
            raise ValueError('PRICE_RESPONSE_INVALID')
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('PRICE_RESPONSE_INVALID') from error
    return record


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


def comparison_bounds(ids, records, as_of, *, column=None):
    keys = ids - {'CASH:KRW'}
    column = column or comparison_column(records, ids)
    required = [records[key]['frame'][column] for key in keys]
    needs_fx = any(records[key]['currency'] == 'USD' for key in keys)
    if needs_fx:
        required.append(records['FX']['frame']['Close'])
    required = [series.where(series.map(positive_price)) for series in required]
    if required:
        if any(s.dropna().empty for s in required):
            raise ValueError('PRICE_UNAVAILABLE')
        start = max(s.first_valid_index() for s in required)
        end = min(s.last_valid_index() for s in required)
        if start >= end:
            raise ValueError('NO_COMMON_PRICE_HISTORY')
        if (as_of - end.date()).days > 7:
            raise ValueError('PRICE_STALE')
        return start, end
    end = pd.Timestamp(as_of - timedelta(days=1))
    return pd.Timestamp(year_before(end.date())), end


def model_price_payload(value):
    if isinstance(value, dict):
        return {key: item[:20] if key == 'dates' and isinstance(item, list) else model_price_payload(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [model_price_payload(item) for item in value]
    return value


def carry_missing_prices(prices, expected, start, end):
    prices = prices.where(prices.map(positive_price))
    expanded = prices.reindex(prices.index.union(expected).sort_values())
    missing = expanded.index[expanded.isna().any(axis=1)]
    lower = min(start, expected[0]) if len(expected) else start
    dates = [str(day.date()) for day in missing if lower <= day <= end]
    filled = expanded.ffill()
    if not filled.loc[start:end].map(positive_price).all().all():
        raise ValueError('PRICE_INVALID')
    return filled, {'method': 'previous_observation', 'count': len(dates), 'dates': dates}


def normalized_prices(ids, records, as_of, bounds=None, *, column=None):
    series, urls = {}, []
    fx_fill_dates = set()
    keys = ids - {'CASH:KRW'}
    needs_fx = any(records[key]['currency'] == 'USD' for key in keys)
    column = column or comparison_column(records, ids)
    start, end = bounds or comparison_bounds(ids, records, as_of, column=column)
    for key in keys:
        record = records[key]
        if not record['frame']['Close'].map(positive_price).any():
            raise ValueError('PRICE_INVALID')
        sessions = expected_sessions(key, start - pd.Timedelta(days=30), end)
        prices = record['frame'][[column]].rename(columns={column: 'Adj Close'})
        event_only = prices.isna().all(axis=1) & ~prices.index.isin(sessions)
        prices = prices.loc[~event_only]
        values = prices['Adj Close']
        expected = sessions[sessions >= start]
        preceding = sessions[sessions <= start]
        if len(preceding) and preceding[-1] >= values.first_valid_index():
            expected = expected.union(preceding[-1:])
        prices, price_fill = carry_missing_prices(prices, expected, start, end)
        values = prices['Adj Close']
        values = values.reindex(values.index.union(pd.DatetimeIndex([start, end]))).ffill()
        if record['currency'] == 'USD':
            fx = records['FX']['frame']['Close']
            fx_expected = sessions[sessions >= start]
            if start not in fx.dropna().index and len(preceding) and preceding[-1] >= fx.first_valid_index():
                fx_expected = fx_expected.union(preceding[-1:])
            fx_frame, fx_fill = carry_missing_prices(fx.to_frame(), fx_expected, start, end)
            fx = fx_frame['Close']
            fx_fill_dates.update(fx_fill['dates'])
            index = values.index.union(fx.index).union(pd.DatetimeIndex([start, end]))
            # Carry only earlier observations; never invent pre-inception prices.
            carried_prices = values.reindex(index).ffill().loc[start:end]
            carried_fx = fx.reindex(index).ffill().loc[start:end]
            if not all(s.map(positive_price).all() for s in (carried_prices, carried_fx)):
                raise ValueError('PRICE_INVALID')
            values = carried_prices * carried_fx
        elif record['currency'] != 'KRW':
            raise ValueError('UNSUPPORTED_CURRENCY')
        series[key] = values
        urls.append({'instrument_id': key, 'url': record.get('return_source_url', record['source_url']), 'price_recovery': record.get('price_recovery'), 'price_fill': price_fill})
    if needs_fx:
        urls.append({'instrument_id': 'USD/KRW', 'url': records['FX']['source_url'], 'price_recovery': records['FX'].get('price_recovery'),
                     'price_fill': {'method': 'previous_observation', 'count': len(fx_fill_dates), 'dates': sorted(fx_fill_dates)}})
    if series:
        union = pd.DatetimeIndex(sorted(set().union(*(s.index for s in series.values()))))
        frame = pd.DataFrame({key: s.reindex(union).ffill() for key, s in series.items()})
        frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
        if len(frame) < 2 or frame.isna().any().any():
            raise ValueError('NO_COMMON_PRICE_HISTORY')
        if not frame.map(positive_price).all().all():
            raise ValueError('PRICE_INVALID')
        if any((end.date() - records[key]['frame'].index[-1].date()).days > 7 for key in ids - {'CASH:KRW'}):
            raise ValueError('PRICE_STALE')
    else:
        frame = pd.DataFrame(index=pd.bdate_range(start, end).union(pd.DatetimeIndex([start, end])))
    frame['CASH:KRW'] = 1.0
    return frame / frame.iloc[0], urls


def history_basis(start, end, records=None):
    return f'{str(start)[:10]} ~ {str(end)[:10]} · 공통 자료 기간 · 분배금 전액 재투자 · 100만 원을 넣었다면 (원)'


def comparison_column(records, ids=None):
    if any(record.get('return_basis') == 'price' for key, record in records.items()
           if key != 'FX' and (ids is None or key in ids)):
        raise ValueError('TOTAL_RETURN_UNAVAILABLE')
    return 'Adj Close'


def comparison_method(records):
    basis = '수정 종가 기준 분배금 재투자'
    return f'시작 비중으로 매수 후 유지 · {basis} · 환율 반영 · 세금·매매비용 제외 · 결측일·휴장일 직전 관측 가격 유지'


def history_returns(history):
    points = history['points']
    period = round((points[-1]['recommended'] / points[0]['recommended'] - 1) * 100, 2)
    cutoff = year_before(date.fromisoformat(points[-1]['date']))
    # A short common period is not annualized or mislabeled as one year.
    annual = None
    if date.fromisoformat(points[0]['date']) <= cutoff:
        base = next(point for point in reversed(points) if date.fromisoformat(point['date']) <= cutoff)
        annual = round((points[-1]['recommended'] / base['recommended'] - 1) * 100, 2)
    return {'return_period_pct': period, 'return_1y_pct': annual}


def comparison(target, current, records, as_of, *, bounds=None):
    active = set(target) | set(current or {}) | {'FX'}
    records = {key: record for key, record in records.items() if key in active}
    frame, urls = normalized_prices(set(target) | set(current or {}), records, as_of, bounds=bounds)

    def value(allocation):
        return sum(frame[key] * weight * 1000000 for key, weight in allocation.items())

    recommended = value(target)
    existing = value(current) if current else None
    points = [{'date': day.date().isoformat(), 'recommended': round(float(recommended.loc[day]), 2),
               'current': round(float(existing.loc[day]), 2) if existing is not None else None} for day in frame.index]
    return {'points': points, 'interval': '1d', 'currency': 'KRW', 'initial_krw': 1000000,
            'basis': history_basis(points[0]['date'], points[-1]['date'], records),
            'method': comparison_method(records),
            'source_url': urls[0]['url'] if urls else 'https://finance.yahoo.com/', 'sources': urls,
            'start_date': points[0]['date'], 'end_date': points[-1]['date']}


class MarketData:
    def __init__(self, request, as_of=None, loader=None):
        self.as_of = as_of or date.today()
        self.loader = loader
        self.records = {}
        self.failures = {}
        self.results = {}
        self.queried = set()
        self.calls = 0
        # Repair imports SciPy/sklearn in each child; three leave no usable 512 MiB headroom.
        self.semaphore = asyncio.Semaphore(2)
        self.catalog = {r['id']: r for r in directory()['instruments']}
        total = sum(h.amount_krw or 0 for h in request.holdings)
        self.current = None
        self.baseline_kind = 'unknown_weights'
        if request.input_mode != 'names' and total and all(h.instrument_id for h in request.holdings):
            self.current = weights([{'instrument_id': h.instrument_id, 'weight_pct': float(h.amount_krw / total * 100)} for h in request.holdings])
            self.baseline_kind = 'current_holdings'
        elif request.planning and not request.holdings:
            self.current = {'CASH:KRW': 1}
            self.baseline_kind = 'new_cash'

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
                    self.failures.pop(instrument, None)
                    async with self.semaphore:
                        try:
                            self.records[instrument] = self.loader(symbol, self.as_of) if self.loader else await fetch_prices(symbol, self.as_of,
                                on_raw=lambda record: self.records.__setitem__(instrument, record))
                            self.failures.pop(instrument, None)
                        except Exception as error:
                            self.failures[instrument] = price_failure(error)
                            log.warning('Price provider failed for %s: %s', instrument, self.failures[instrument])
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
                for instrument in needed:
                    if instrument not in self.records:
                        self.failures.setdefault(instrument, 'PRICE_PROVIDER_TIMEOUT')
            if self.current:
                try:
                    history = comparison(target, self.current, self.records, self.as_of)
                except (KeyError, ValueError):
                    history = comparison(target, None, self.records, self.as_of)
                    history['current_unavailable_reason'] = '현재 구성의 유효한 공통 가격·환율 자료를 확보하지 못해 비교에서 제외했어요.'
            else:
                history = comparison(target, None, self.records, self.as_of)
            result = {'status': 'available', 'history': history, **history_returns(history)}
        except Exception as error:
            # Optional market data failure must not destroy the research report.
            log.warning('Market history unavailable: %s', type(error).__name__)
            reason = self.failures.get(error.args[0], 'PRICE_PROVIDER_UNAVAILABLE') if isinstance(error, KeyError) else price_failure(error)
            result = {'status': 'unavailable', 'reason': reason}
        result['failures'] = {i: self.failures[i] for i in needed if i in self.failures}
        if result['status'] == 'available' and not result['failures'] and 'current_unavailable_reason' not in result['history']:
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
        return dividend_observation(self.records.get(instrument_id), instrument_id)


def dividend_observation(record, instrument_id):
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
    price = frame['Close'].iloc[-1]
    if not positive_price(price):
        return None
    price = float(price)
    return {'instrument_id': instrument_id, 'trailing_yield_pct': round(total / price * 100, 4),
            'as_of': end.isoformat(), 'period_start': start.isoformat(), 'dividends_per_share': total,
            'price': price, 'currency': record['currency'], 'source_url': record['source_url'],
            'method': 'Yahoo Finance 최근 12개월 분배금 합계 ÷ 최근 종가 · 분배락일 기준',
            'events': [{'date': day.date().isoformat(), 'amount': float(value)} for day, value in values.items() if value > 0],
            'evidence': []}


TOOL = {'type': 'function', 'function': {'name': 'read_portfolio_history',
        'description': '등록 ETF와 현금의 최대 가용 공통 기간 일별 원화 총수익률을 계산한다. 시작·종료일을 확인하며 기간 수익률과 최근 1년 수익률을 구분한다. 최종 추천 비중 확정 후 호출한다. 최대 3개 구성.',
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
    queried = tuple(sorted(target.items())) in market.queried
    # An ungrounded optional forecast must not discard the validated allocation.
    forecast_evidence = cited(final.return_forecast.evidence, sources) if final.return_forecast and queried else []
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
        plan['historical_performance'] = {**result['history'], 'baseline_kind': market.baseline_kind}
        plan['historical_return_1y'] = result['return_1y_pct']
        plan['historical_return_period'] = result.get('return_period_pct')
        plan['unavailable_reason'] = ''
        if final.return_forecast and queried:
            plan['expected_return_1y'] = final.return_forecast.percent
            plan['return_forecast'] = {'reason': final.return_forecast.reason, 'evidence': forecast_evidence,
                                       'method': '표시된 공통 기간의 과거 성과와 시장 근거를 고려한 AI 추정 · 수익 보장 아님'}
    else:
        plan['unavailable_reason'] = '비교할 공통 기간의 유효한 일별 가격·환율을 확보하지 못해 과거 성과를 표시하지 못했어요.'
        plan['market_data_failure'] = result['reason']
