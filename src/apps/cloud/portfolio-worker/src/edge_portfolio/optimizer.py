"""Comparable batches of deterministic 10%-grid searches, filtered before ranking."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_CEILING
from itertools import combinations

import numpy as np

from .market_data import fetch_prices, normalized_prices, comparison_bounds, expected_sessions, history_basis, history_returns, price_failure
from .recommendation import selected_accounts, allocation_error
from .decision import Constraint, Decision, check_constraints
from .execution import plan_execution
from .market_data import comparison_method, comparison_column

PRICE_TIMEOUT_SECONDS = 65


def optimization_bounds(ids, records, as_of, *, column=None):
    start, end = comparison_bounds(ids, records, as_of, column=column)
    sessions = set().union(*(set(expected_sessions(key, start, end)) for key in ids - {'CASH:KRW'}))
    if len(sessions) < 11:
        raise ValueError('INSUFFICIENT_COMPARISON_SESSIONS')
    return start, end


def optimization_prices(ids, records, as_of, bounds=None):
    frame, sources = normalized_prices(ids, records, as_of, bounds)
    sessions = set().union(*(set(expected_sessions(key, frame.index[0], frame.index[-1])) for key in ids - {'CASH:KRW'}))
    if sessions:
        frame = frame.loc[frame.index.isin(sessions)]
    if len(frame) < 11:
        raise ValueError('INSUFFICIENT_COMPARISON_SESSIONS')
    return frame / frame.iloc[0], sources


def grid(size):
    # Every supplied ETF is held: ten 10% units, at least one unit per ETF.
    return np.array([np.diff((0, *cuts, 10)) / 10
                     for cuts in combinations(range(1, 10), size - 1)])


def search(ids, frame, current):
    if len(frame) < 11:
        return []  # Ten measured sessions plus the initial normalization date.
    allocation = grid(len(ids))
    prices = frame[ids].to_numpy()
    baseline = sum(frame[key].to_numpy() * value for key, value in current.items())
    turnover = np.zeros(len(allocation))
    for key in set(current) | set(ids):
        target = allocation[:, ids.index(key)] if key in ids else 0
        turnover += np.abs(target - current.get(key, 0)) / 2
    accepted = []
    chunk = max(1, min(256, 500000 // len(frame)))
    for start in range(0, len(allocation), chunk):
        batch = allocation[start:start + chunk]
        # Avoid nested BLAS thread pools inside the ten parallel searches.
        values = np.einsum('ij,kj->ik', batch, prices)
        ahead = values[:, 1:] > baseline[None, 1:] + 1e-10
        duration = ahead.mean(axis=1)
        recent = ahead[:, -10:].sum(axis=1)
        for i in range(len(batch)):
            accepted.append({'weights': batch[i].tolist(), 'duration_pct': float(duration[i] * 100),
                             'recent_outperform_days': int(recent[i]),
                             'excess_pp': float((values[i, -1] - baseline[-1]) * 100),
                             'max_drawdown_pct': float(np.min(values[i] / np.maximum.accumulate(values[i]) - 1) * 100),
                             'preservation_pct': float((1 - turnover[start + i]) * 100)})
    return accepted


def rank(groups, ids_by_group, objectives=()):
    excess = [row['excess_pp'] for group in groups for row in group]
    minimum, maximum = min([0, *excess]), max([0, *excess])
    span = maximum - minimum
    winners = []
    def priority(row, size):
        values = {'growth': row['excess_pp'], 'stability': row.get('max_drawdown_pct', 0),
                  'preserve': row['preservation_pct'], 'simplicity': -size, 'income': row.get('income_yield_pct')}
        ordered = tuple(part for key in objectives for part in (values[key] is not None, values[key] if values[key] is not None else 0))
        return (*ordered, row['score'], row['excess_pp'], row['preservation_pct'], tuple(row.get('weights', [])))
    for number, (ids, group) in enumerate(zip(ids_by_group, groups), 1):
        if not group:
            continue
        for row in group:
            relative_excess = (row['excess_pp'] - minimum) / span if span else 0
            row['score'] = .4 * row['duration_pct'] + .4 * relative_excess * 100 + .2 * row['preservation_pct']
        best = max(group, key=lambda r: priority(r, len(ids)))
        winners.append({'candidate_id': f'candidate-{number}',
                        'allocations': [{'instrument_id': key, 'weight_pct': round(weight * 100)} for key, weight in zip(ids, best['weights'])],
                        **{key: round(best[key], 6) for key in ('score', 'duration_pct', 'excess_pp', 'preservation_pct')},
                        'max_drawdown_pct': best.get('max_drawdown_pct'), 'income_yield_pct': best.get('income_yield_pct'),
                        'recent_outperform_days': best.get('recent_outperform_days', 0)})
    winners.sort(key=lambda r: priority(r, len(r['allocations'])), reverse=True)
    return [dict(row, rank=i) for i, row in enumerate(winners, 1)]


class Optimizer:
    def __init__(self, market, request=None):
        self.market = market
        self.request = request
        self.result = None
        self.decision = Decision()
        self.groups = []
        self.batches = set()
        self.attempts = {}
        self.revision = 0

    async def tool(self, args):
        result = await self._tool(args)
        result['search_progress'] = {'unique_combinations': len(self.groups),
            'unique_etfs': len({key for group in self.groups for key in group}),
            'remaining_batches': max(0, 4 - len(self.batches)), 'alternatives_explored': len(self.groups) >= 20}
        return result

    async def _tool(self, args):
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
        conflicts = self.policy_conflicts()
        if conflicts:
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CUSTOMER_POLICY_CONFLICT',
                           'policy_conflicts': conflicts, 'submitted_combinations': 0,
                           'search_scope': 'Conflict with service policy, not proof that all portfolios are impossible'}
            return self.result
        ids = set().union(*map(set, groups))
        if len(ids) > 20:
            raise ValueError('Use at most twenty distinct candidate ETFs per batch')
        if self.request:
            accounts = selected_accounts(self.request)
            if any(not any(allocation_error(self.market.catalog[key], account, self.request.preferences.tax_profile) is None
                           for account in accounts) for key in ids):
                raise ValueError('Each candidate ETF needs an eligible selected account; replace ineligible ETFs before submitting')
        batch = tuple(sorted(map(tuple, groups)))
        if batch in self.batches and self.result and not self.result.get('failures') and self.result['status'] != 'unavailable':
            return self.result
        if self.attempts.get(batch, 0) >= 2:
            return {**(self.result or {}), 'retry_reason': 'BATCH_RETRY_EXHAUSTED'}
        if batch not in self.batches and (len(self.batches) >= 4 or len(set().union(*map(set, self.groups), ids)) > 40):
            raise ValueError('SEARCH_RESOURCE_LIMIT: at most four distinct batches and forty ETFs; preserve constraints and report searched scope')
        self.attempts[batch] = self.attempts.get(batch, 0) + 1
        self.batches.add(batch)
        self.groups.extend(group for group in groups if group not in self.groups)
        groups = [list(group) for group in self.groups]
        ids = set().union(*map(set, groups))
        self.revision += 1
        self.market.queried.clear()  # Forecasts must cite a history read from this comparison revision.
        self.result = {'status': 'unavailable', 'candidates': [], 'reason': 'CURRENT_WEIGHTS_REQUIRED'}
        if not self.market.current:
            return self.result
        ids |= set(self.market.current)
        missing = sorted(ids - {'CASH:KRW'} - self.market.catalog.keys())
        if missing:
            self.result.update(reason='CURRENT_INSTRUMENT_UNAVAILABLE', missing_instrument_ids=missing)
            return self.result
        needed = {key: self.market.catalog[key]['ticker'] + ('.KS' if self.market.catalog[key]['market'] == 'KR' else '')
                  for key in ids - {'CASH:KRW'}}
        if any(self.market.catalog[key]['market'] == 'US' for key in ids - {'CASH:KRW'}):
            needed['FX'] = 'KRW=X'
        priority = set(self.market.current) - {'CASH:KRW'}
        if any(self.market.catalog[key]['market'] == 'US' for key in priority):
            priority.add('FX')
        ordered = sorted(needed, key=lambda key: (key not in priority, key != 'FX', key))

        async def fetch(key, symbol):
            if key not in self.market.records:
                self.market.failures.pop(key, None)
                async with self.market.semaphore:
                    try:
                        self.market.records[key] = self.market.loader(symbol, self.market.as_of) if self.market.loader else await fetch_prices(symbol, self.market.as_of,
                            on_raw=lambda record: self.market.records.__setitem__(key, record))
                        self.market.failures.pop(key, None)
                    except Exception as error:
                        self.market.failures[key] = price_failure(error)
        rejections = {}
        try:
            try:
                async with asyncio.timeout(PRICE_TIMEOUT_SECONDS):
                    await asyncio.gather(*(fetch(key, needed[key]) for key in ordered), return_exceptions=True)
            except TimeoutError:
                for key in needed:
                    if key not in self.market.records:
                        self.market.failures.setdefault(key, 'PRICE_PROVIDER_TIMEOUT')
            failed = set(needed) - set(self.market.records)
            if 'FX' in failed:
                failed.update(key for key in ids - {'CASH:KRW'} if self.market.catalog[key]['market'] == 'US')
            eligible = [group if not set(group).intersection(failed) else [] for group in groups]
            rejections.update({number: 'INSTRUMENT_PRICE_UNAVAILABLE' for number, group in enumerate(eligible) if not group})
            if set(self.market.current).intersection(failed):
                raise ValueError('BASELINE_PRICE_UNAVAILABLE')
            # Exclude too-short ranges first, then validate prices within one shared window.
            for number, group in enumerate(eligible):
                if not group:
                    continue
                try:
                    try:
                        optimization_bounds(set(group) | set(self.market.current), self.market.records, self.market.as_of, column='Adj Close')
                    except (KeyError, ValueError):
                        optimization_bounds(set(group) | set(self.market.current), self.market.records, self.market.as_of, column='Close')
                except (KeyError, ValueError) as error:
                    rejections[number] = price_failure(error)
                    group.clear()
            if not any(eligible):
                raise ValueError('NO_COMPARABLE_PRICE_HISTORY')
            available = [list(group) for group in eligible]
            # Removing unusable groups may expand the shared window, so revalidate survivors.
            while True:
                valid_ids = set(self.market.current).union(*(set(group) for group in eligible))
                column = comparison_column(self.market.records, valid_ids)
                rejected = False
                for number, group in enumerate(eligible):
                    if not group:
                        continue
                    try:
                        optimization_bounds(set(group) | set(self.market.current), self.market.records, self.market.as_of, column=column)
                    except (KeyError, ValueError) as error:
                        rejections[number] = price_failure(error)
                        available[number] = []
                        rejected = True
                if rejected:
                    eligible = [list(group) for group in available]
                    if not any(eligible):
                        raise ValueError('NO_COMMON_PRICE_HISTORY')
                    continue
                bounds = comparison_bounds(valid_ids, self.market.records, self.market.as_of)
                rejected = False
                for number, group in enumerate(eligible):
                    if not group:
                        continue
                    try:
                        normalized_prices(set(group) | set(self.market.current), self.market.records, self.market.as_of, bounds, column=column)
                    except (KeyError, ValueError) as error:
                        rejections[number] = price_failure(error)
                        available[number] = []
                        rejected = True
                if rejected:
                    eligible = [list(group) for group in available]
                    if not any(eligible):
                        raise ValueError('NO_COMMON_PRICE_HISTORY')
                    continue
                records = {key: {**value, 'frame': value['frame'].loc[:bounds[1]]} for key, value in self.market.records.items()}
                for number, group in enumerate(eligible):
                    if not group:
                        continue
                    try:
                        optimization_bounds(set(group) | set(self.market.current), records, self.market.as_of, column=column)
                    except (KeyError, ValueError) as error:
                        rejections[number] = price_failure(error)
                        group.clear()
                        available[number] = []
                        rejected = True
                if rejected:
                    if not any(eligible):
                        raise ValueError('NO_COMMON_PRICE_HISTORY')
                    continue
                break
            frame, sources = optimization_prices(valid_ids, self.market.records, self.market.as_of, bounds)
            # NumPy releases the GIL; all ten searches are scheduled together, sharing prices.
            with ThreadPoolExecutor(max_workers=10) as pool:
                loop = asyncio.get_running_loop()
                results = await asyncio.gather(*(loop.run_in_executor(pool, search, group, frame, self.market.current) if group
                                                else asyncio.sleep(0, result=[]) for group in eligible))
            service_count = sum(map(len, results))
            unknown_count = rejected_count = 0
            dividends_by_id = {}
            income_failures = {}
            if 'income' in self.decision.objectives:
                for ids_in_group, rows in zip(eligible, results):
                    if not rows:
                        continue
                    for key in ids_in_group:
                        if key not in dividends_by_id:
                            try:
                                dividends_by_id[key] = self.market.dividends(key)
                            except (KeyError, ValueError, TypeError, OverflowError):
                                dividends_by_id[key] = None
                            if dividends_by_id[key] is None:
                                income_failures[key] = 'DIVIDEND_DATA_UNAVAILABLE'
            for ids_in_group, rows in zip(eligible, results):
                dividends = [dividends_by_id.get(key) for key in ids_in_group]
                for row in rows[:]:
                    allocation = [{'instrument_id': key, 'weight_pct': round(weight * 100)} for key, weight in zip(ids_in_group, row['weights'])]
                    checks = self.vector_checks(allocation)
                    if any(check['status'] != 'pass' for check in checks):
                        unknown_count += any(check['status'] == 'unknown' for check in checks)
                        rejected_count += 1
                        rows.remove(row)
                        continue
                    row['income_yield_pct'] = sum(weight * dividend['trailing_yield_pct'] for weight, dividend in zip(row['weights'], dividends)) if all(dividends) else None
            candidates = rank(results, eligible, self.decision.objectives)
            if self.revision > 1:
                for candidate in candidates:
                    candidate['candidate_id'] = f'r{self.revision}-' + candidate['candidate_id']
            self.result = {'status': 'available' if candidates else 'no_match', 'candidates': candidates,
                           'revision': self.revision, 'batches': len(self.batches), 'objectives': self.decision.objectives,
                           'service_eligible_vectors': service_count, 'customer_rejected_vectors': rejected_count,
                           'customer_unknown_vectors': unknown_count,
                           'income_failures': income_failures,
                           'reason': None if candidates else 'CUSTOMER_CONSTRAINT_UNVERIFIED' if unknown_count else 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' if rejected_count else 'SERVICE_CRITERIA_NOT_MET',
                           'search_scope': 'Submitted combinations only; 10% grid, positive ETF weights, zero cash; no global impossibility claim',
                           'start_date': str(frame.index[0].date()), 'end_date': str(frame.index[-1].date()),
                           'sources': sources, 'submitted_combinations': len(groups), 'tested_combinations': sum(bool(group) for group in eligible),
                           'rejected_combinations': [{'combination': number + 1, 'reason': rejections.get(number, 'INSTRUMENT_PRICE_UNAVAILABLE')}
                               for number, group in enumerate(eligible) if not group]}
            # Reuse the identical comparison window for final publication.
            comparison_records = {key: record for key, record in self.market.records.items() if key in valid_ids}
            baseline = sum(frame[key] * value for key, value in self.market.current.items())
            for candidate in candidates:
                target = {row['instrument_id']: row['weight_pct'] / 100 for row in candidate['allocations']}
                values = sum(frame[key] * weight for key, weight in target.items())
                history = {'points': [{'date': str(day.date()), 'recommended': round(float(values.loc[day] * 1000000), 2),
                                       'current': round(float(baseline.loc[day] * 1000000), 2)} for day in frame.index],
                           'interval': '1d', 'currency': 'KRW', 'initial_krw': 1000000,
                           'basis': history_basis(self.result['start_date'], self.result['end_date'], comparison_records),
                           'method': comparison_method(comparison_records),
                           'source_url': sources[0]['url'] if sources else 'https://finance.yahoo.com/', 'sources': sources,
                           'start_date': self.result['start_date'], 'end_date': self.result['end_date']}
                self.market.results[tuple(sorted(target.items()))] = {'status': 'available', 'history': history, **history_returns(history)}
        except (ValueError, KeyError, TimeoutError) as error:
            reason = str(error) if isinstance(error, ValueError) and str(error) in {
                'BASELINE_PRICE_UNAVAILABLE', 'NO_COMPARABLE_PRICE_HISTORY', 'NO_COMMON_PRICE_HISTORY'
            } else price_failure(error)
            self.result = {'status': 'unavailable', 'candidates': [], 'reason': reason,
                           'rejected_combinations': [{'combination': number + 1, 'reason': reason} for number, reason in sorted(rejections.items())]}
        self.result['failures'] = {key: self.market.failures[key] for key in needed if key in self.market.failures}
        return self.result

    def vector_checks(self, rows):
        if not self.request:
            return []
        # Before account placement, test global constraints and necessary account feasibility.
        # The final recommendation still checks the exact accounts and execution amounts.
        rules = [rule for rule in self.decision.constraints if rule.kind not in {'accounts', 'no_sell'}]
        no_sell = [rule for rule in self.decision.constraints if rule.kind == 'no_sell']
        if self.decision.kind == 'new_money' and not any(not rule.instrument_ids for rule in no_sell):
            no_sell.append(Constraint(kind='no_sell', source=self.request.question or '추가 투자'))
        request = self.request.model_copy(update={'holdings': [h.model_copy(update={'account': '일반 계좌'}) for h in self.request.holdings]})
        checks = check_constraints(self.decision.model_copy(update={'constraints': rules}),
            [{**row, 'account': '일반 계좌'} for row in rows], request, self.market.catalog)
        allowed = selected_accounts(self.request)
        for rule in self.decision.constraints:
            if rule.kind == 'accounts':
                allowed &= set(rule.accounts)
        if no_sell:
            neutral = self.decision.model_copy(update={'kind': 'review', 'constraints': rules})
            plan = plan_execution(request, [{**row, 'account': '일반 계좌'} for row in rows], neutral)
            targets = {row['instrument_id']: Decimal(str(row['weight_pct'])) for row in rows}
            total = sum((Decimal(row['target_krw']) for row in plan['rows']), Decimal(0))
            for rule in no_sell:
                holdings = [h for h in self.request.holdings if h.kind != 'cash' and (not rule.instrument_ids or h.instrument_id in rule.instrument_ids)]
                if any(h.instrument_id and (not targets.get(h.instrument_id) or h.account not in allowed) for h in holdings):
                    status = 'fail'
                elif not holdings:
                    status = 'pass'
                elif self.request.input_mode == 'percent':
                    status = check_constraints(self.decision.model_copy(update={'constraints': [rule]}), rows, self.request, self.market.catalog)[0]['status']
                elif plan['status'] not in {'ready', 'conditional'} or self.decision.kind == 'new_money' and not (Decimal(plan['additional_krw']) or plan['monthly_krw']):
                    status = 'unknown'
                elif plan['monthly_krw'] and Decimal(plan['monthly_krw']) > 0:
                    status = 'pass'  # A necessary existence check; exact months/accounts are checked at publication.
                else:
                    amounts = {}
                    for holding in holdings:
                        amounts[holding.instrument_id] = amounts.get(holding.instrument_id, Decimal(0)) + holding.amount_krw
                    # Splitting an ETF across accounts changes largest-remainder rounding.
                    # Keep every potentially feasible split; never certify the final placement here.
                    status = 'pass' if all(amount <= (total * targets[key] / 100).to_integral_value(rounding=ROUND_CEILING) + max(0, len(allowed) - 1)
                                           for key, amount in amounts.items()) else 'fail'
                checks.append({'kind': 'no_sell', 'status': status})
        possible = all(any(allocation_error(self.market.catalog[row['instrument_id']], account, self.request.preferences.tax_profile) is None
                           for account in allowed) for row in rows)
        if not possible:
            checks.append({'kind': 'accounts', 'status': 'fail'})
        return checks

    def policy_conflicts(self):
        required = set()
        conflicts = []
        for rule in self.decision.constraints:
            if rule.kind == 'require' or rule.kind == 'min_weight' and rule.value > 0 and (rule.scope == 'each' or len(rule.instrument_ids) == 1):
                required.update(rule.instrument_ids)
            if rule.kind == 'min_weight' and rule.value > 0 and rule.instrument_ids == ['CASH:KRW'] or rule.kind == 'require' and 'CASH:KRW' in rule.instrument_ids:
                conflicts.append('CASH_REQUIRED_BUT_SERVICE_CASH_ZERO')
            if self.request and self.request.planning and rule.kind == 'max_positions' and rule.value < 2:
                conflicts.append('PLANNING_REQUIRES_TWO_ETFS')
        for rule in self.decision.constraints:
            if rule.kind == 'max_weight' and rule.value < 10 and required.intersection(rule.instrument_ids):
                conflicts.append('REQUIRED_ETF_BELOW_TEN_PERCENT_GRID')
            if rule.kind == 'exclude' and required.intersection(rule.instrument_ids):
                conflicts.append('REQUIRED_ETF_EXCLUDED')
        return sorted(set(conflicts))

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
    'description': 'ETF 조합 10개를 최적화한다. 서비스 기준 통과 후 고객 제약을 검사하고 목적 우선순위로 정렬한다. 다른 조합으로 재탐색 가능; 누적 전체 후보를 같은 공통 기간으로 재계산한다. 현금 0%, 10% 단위, 조합당 최대 5종, 배치당 20종, 전체 4배치·40종. 현재 revision의 후보만 선택한다.',
    'parameters': {'type': 'object', 'properties': {'combinations': {'type': 'array', 'minItems': 10, 'maxItems': 10,
        'items': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'items': {'type': 'string'}}}},
        'required': ['combinations'], 'additionalProperties': False}}}
