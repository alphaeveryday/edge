"""One batch of ten independent, deterministic 5%-grid searches."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from itertools import combinations

import numpy as np

from .market_data import fetch_prices, normalized_prices, expected_sessions
from .recommendation import selected_accounts, allocation_error

PRICE_TIMEOUT_SECONDS = 65


def grid(size):
    # Every supplied ETF is held: twenty 5% units, at least one unit per ETF.
    return np.array([np.diff((0, *cuts, 20)) / 20
                     for cuts in combinations(range(1, 20), size - 1)])


def search(ids, frame, current):
    allocation = grid(len(ids))
    prices = frame[ids].to_numpy()
    baseline = sum(frame[key].to_numpy() * value for key, value in current.items())
    turnover = np.zeros(len(allocation))
    for key in set(current) | set(ids):
        target = allocation[:, ids.index(key)] if key in ids else 0
        turnover += np.abs(target - current.get(key, 0)) / 2
    accepted = []
    for start in range(0, len(allocation), 256):
        batch = allocation[start:start + 256]
        # Avoid nested BLAS thread pools inside the ten parallel searches.
        values = np.einsum('ij,kj->ik', batch, prices)
        ahead = values[:, 1:] > baseline[None, 1:] + 1e-10
        duration = ahead.mean(axis=1)
        recent = ahead[:, -10:].all(axis=1)
        valid = np.flatnonzero((duration >= .5) & recent)
        for i in valid:
            accepted.append({'weights': batch[i].tolist(), 'duration_pct': float(duration[i] * 100),
                             'excess_pp': float((values[i, -1] - baseline[-1]) * 100),
                             'preservation_pct': float((1 - turnover[start + i]) * 100)})
    return accepted


def rank(groups, ids_by_group):
    maximum = max((row['excess_pp'] for group in groups for row in group), default=0)
    winners = []
    for number, (ids, group) in enumerate(zip(ids_by_group, groups), 1):
        if not group:
            continue
        for row in group:
            row['score'] = .4 * row['duration_pct'] + .4 * row['excess_pp'] / maximum * 100 + .2 * row['preservation_pct']
        best = max(group, key=lambda r: (r['score'], r['excess_pp'], r['preservation_pct'], tuple(r['weights'])))
        winners.append({'candidate_id': f'candidate-{number}',
                        'allocations': [{'instrument_id': key, 'weight_pct': round(weight * 100)} for key, weight in zip(ids, best['weights'])],
                        **{key: round(best[key], 6) for key in ('score', 'duration_pct', 'excess_pp', 'preservation_pct')},
                        'recent_outperform_days': 10})
    winners.sort(key=lambda r: (-r['score'], -r['excess_pp'], r['candidate_id']))
    return [dict(row, rank=i) for i, row in enumerate(winners, 1)]


class Optimizer:
    def __init__(self, market, request=None):
        self.market = market
        self.request = request
        self.result = None

    async def tool(self, args):
        if self.result is not None:
            return self.result
        groups = args.get('combinations')
        if not isinstance(groups, list) or len(groups) != 10:
            raise ValueError('Submit exactly ten ETF combinations in one call')
        if any(not isinstance(group, list) or not 1 <= len(group) <= 5 or
               any(not isinstance(key, str) or key == 'CASH:KRW' or key not in self.market.catalog for key in group) or
               len(set(group)) != len(group) for group in groups):
            raise ValueError('Each combination needs 1..5 distinct registered ETF IDs, without cash')
        groups = [sorted(group) for group in groups]
        if self.request and self.request.planning and any(len(group) < 2 for group in groups):
            raise ValueError('Planning requires at least two distinct ETFs in every combination; pair a representative ETF with a researched alternative')
        if len({tuple(group) for group in groups}) != 10:
            raise ValueError('The ten ETF combinations must be distinct')
        ids = set().union(*map(set, groups))
        if len(ids) > 20:
            raise ValueError('Use at most twenty distinct candidate ETFs per batch')
        if self.request:
            accounts = selected_accounts(self.request)
            if any(not any(allocation_error(self.market.catalog[key], account, self.request.preferences.tax_profile) is None
                           for account in accounts) for key in ids):
                raise ValueError('Each candidate ETF needs an eligible selected account; replace ineligible ETFs before submitting')
        self.result = {'status': 'unavailable', 'candidates': [], 'reason': 'CURRENT_WEIGHTS_REQUIRED'}
        if not self.market.current:
            return self.result
        ids |= set(self.market.current)
        needed = {key: self.market.catalog[key]['ticker'] + ('.KS' if self.market.catalog[key]['market'] == 'KR' else '')
                  for key in ids - {'CASH:KRW'}}
        if any(self.market.catalog[key]['market'] == 'US' for key in ids - {'CASH:KRW'}):
            needed['FX'] = 'KRW=X'

        async def fetch(key, symbol):
            if key not in self.market.records:
                async with self.market.semaphore:
                    self.market.records[key] = self.market.loader(symbol, self.market.as_of) if self.market.loader else await fetch_prices(symbol, self.market.as_of)
        try:
            try:
                async with asyncio.timeout(PRICE_TIMEOUT_SECONDS):
                    await asyncio.gather(*(fetch(key, symbol) for key, symbol in needed.items()), return_exceptions=True)
            except TimeoutError:
                pass  # Completed records remain usable; incomplete groups are excluded below.
            failed = set(needed) - set(self.market.records)
            if 'FX' in failed:
                failed.update(key for key in ids - {'CASH:KRW'} if self.market.catalog[key]['market'] == 'US')
            eligible = [group if not set(group).intersection(failed) else [] for group in groups]
            if set(self.market.current).intersection(failed):
                raise ValueError('BASELINE_PRICE_UNAVAILABLE')
            # Reject short/gapped histories individually before establishing one shared window.
            for group in eligible:
                try:
                    if group:
                        normalized_prices(set(group) | set(self.market.current), self.market.records, self.market.as_of)
                except (KeyError, ValueError):
                    group.clear()
            if not any(eligible):
                raise ValueError('NO_COMPARABLE_PRICE_HISTORY')
            # One common endpoint must not let a short-history candidate invalidate its peers.
            while True:
                valid_ids = set(self.market.current).union(*(set(group) for group in eligible))
                keys = valid_ids - {'CASH:KRW'}
                if any(self.market.catalog[key]['market'] == 'US' for key in keys):
                    keys = keys | {'FX'}
                end = min(self.market.records[key]['frame'].index[-1] for key in keys)
                records = {key: {**value, 'frame': value['frame'].loc[:end]} for key, value in self.market.records.items()}
                rejected = False
                for group in eligible:
                    if not group:
                        continue
                    try:
                        normalized_prices(set(group) | set(self.market.current), records, self.market.as_of)
                    except (KeyError, ValueError):
                        group.clear()
                        rejected = True
                if not any(eligible):
                    raise ValueError('NO_COMMON_PRICE_HISTORY')
                if not rejected:
                    break
            frame, sources = normalized_prices(valid_ids, records, self.market.as_of)
            trading_days = set().union(*(set(expected_sessions(key, frame.index[0], frame.index[-1])) for key in valid_ids - {'CASH:KRW'}))
            if trading_days:
                frame = frame.loc[frame.index.isin(trading_days)]
                frame = frame / frame.iloc[0]
            # NumPy releases the GIL; all ten searches are scheduled together, sharing prices.
            with ThreadPoolExecutor(max_workers=10) as pool:
                loop = asyncio.get_running_loop()
                results = await asyncio.gather(*(loop.run_in_executor(pool, search, group, frame, self.market.current) if group
                                                else asyncio.sleep(0, result=[]) for group in eligible))
            candidates = rank(results, eligible)
            self.result = {'status': 'available' if candidates else 'no_match', 'candidates': candidates,
                           'start_date': str(frame.index[0].date()), 'end_date': str(frame.index[-1].date()),
                           'sources': sources, 'submitted_combinations': 10, 'tested_combinations': sum(bool(group) for group in eligible)}
            # Reuse the identical comparison window for final publication.
            baseline = sum(frame[key] * value for key, value in self.market.current.items())
            for candidate in candidates:
                target = {row['instrument_id']: row['weight_pct'] / 100 for row in candidate['allocations']}
                values = sum(frame[key] * weight for key, weight in target.items())
                history = {'points': [{'date': str(day.date()), 'recommended': round(float(values.loc[day] * 1000000), 2),
                                       'current': round(float(baseline.loc[day] * 1000000), 2)} for day in frame.index],
                           'interval': '1d', 'currency': 'KRW', 'initial_krw': 1000000,
                           'basis': '최근 1년 · 100만 원을 넣었다면 (원)',
                           'method': '시작 비중으로 매수 후 유지 · 분배금 재투자 · 환율 반영 · 세금·매매비용 제외',
                           'source_url': 'https://finance.yahoo.com/', 'sources': sources,
                           'start_date': self.result['start_date'], 'end_date': self.result['end_date']}
                self.market.results[tuple(sorted(target.items()))] = {'status': 'available', 'history': history,
                    'return_1y_pct': round(float((values.iloc[-1] - 1) * 100), 2)}
        except (ValueError, KeyError, TimeoutError):
            self.result = {'status': 'unavailable', 'candidates': [], 'reason': 'COMPARISON_DATA_UNAVAILABLE'}
        return self.result

    def validate(self, final):
        if self.result is None:
            raise ValueError('Call optimize_portfolios with ten combinations before publishing')
        candidates = self.result['candidates']
        if not candidates:
            if final.recommendation is not None or final.selected_candidate_id is not None or final.return_forecast is not None:
                raise ValueError('No eligible candidate: recommendation, selected_candidate_id and return_forecast must be null')
            return None
        if final.recommendation is None and final.selected_candidate_id is None and final.return_forecast is None:
            return None  # Backtest eligibility alone does not establish a four-star recommendation.
        candidate = next((row for row in candidates if row['candidate_id'] == final.selected_candidate_id), None)
        if candidate is None or final.recommendation is None:
            raise ValueError('Select an eligible candidate ID and its exact allocation')
        actual = {}
        for row in final.recommendation.allocations:
            actual[row.instrument_id] = actual.get(row.instrument_id, Decimal(0)) + Decimal(str(row.weight_pct))
        expected = {row['instrument_id']: row['weight_pct'] for row in candidate['allocations']}
        if self.request and self.request.planning and len(actual) < 2:
            raise ValueError('Planning requires at least two distinct ETFs')
        if actual != expected:
            raise ValueError(f'Final allocation must exactly match {candidate["candidate_id"]}: {expected}; use candidate_id, not rank, to identify the candidate')
        return candidate


TOOL = {'type': 'function', 'function': {'name': 'optimize_portfolios',
    'description': 'ETF 조합 10개를 한 번에 병렬 최적화한다; 현금 0%, 5% 단위, 우세 거래일 50% 이상 및 최근 10거래일 모두 우세한 후보만 40/40/20 점수순으로 반환한다; 분석당 한 배치, 조합당 최대 5종, 전체 후보 ETF 최대 20종.',
    'parameters': {'type': 'object', 'properties': {'combinations': {'type': 'array', 'minItems': 10, 'maxItems': 10,
        'items': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'items': {'type': 'string'}}}},
        'required': ['combinations'], 'additionalProperties': False}}}
