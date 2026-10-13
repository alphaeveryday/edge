"""Validate model choices; derive amounts, deltas and totals in code."""
from decimal import Decimal, ROUND_HALF_UP
import re

from .catalog import directory
from .contracts import validate_section
from .execution import allocate_amounts, plan_execution


def search_etfs(query, offset=0):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 80:
        raise ValueError('INVALID_CATALOG_QUERY')
    q = query.casefold().strip()
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset > 200:
        raise ValueError('INVALID_CATALOG_OFFSET')
    rows = directory()['instruments']
    exact = [r for r in rows if q in (r['id'].casefold(), r['ticker'].casefold())]
    normalize = lambda value: re.sub(r'[\s_\-]', '', value.casefold())
    matches = exact or [r for r in rows if normalize(q) in normalize(r['name']) or q in r['ticker'].casefold()]
    return [{'instrument_id': r['id'], 'name': r['name'], 'ticker': r['ticker'], 'market': r['market']} for r in matches[offset:offset + 8]]


def selected_accounts(request):
    accounts = set(request.preferences.accounts) | {h.account for h in request.holdings}
    if request.preferences.tax_profile:
        accounts.update(request.preferences.tax_profile.recommended_accounts)
    return accounts


def allocation_error(item, account, tax):
    if account != '일반 계좌' and item['market'] == 'US':
        return 'US-listed ETFs cannot be allocated directly to ISA/pension accounts'
    if tax:
        subtype = next((a.subtype for a in tax.accounts if a.account == account), '')
        if item['kind'] != 'cash' and subtype in ('연금저축보험', '연금저축신탁', '신탁형', '일임형'):
            return 'Account subtype does not support the proposed direct ETF trade'
    return None


def publish_recommendation(final, request, metrics, sources, decision=None):
    def evaluation(value, current=False):
        scores = value.scores.model_dump() if value.scores else None
        overall = float((sum(Decimal(str(v)) for v in scores.values()) / 3).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)) if scores else None
        input_only = current and scores is None and (decision and decision.kind == 'scenario' or not sources or bool(request.holdings) and all(h.kind == 'cash' for h in request.holdings))
        return {'scores': scores, 'overall': overall, 'rubric_version': 1, 'reason': validate_section('question' if input_only else 'portfolio', value.reason.model_dump(), sources),
                'method': '시장 주도성·수익성·안정성 세 항목의 평균이에요. ETF 구성 종목·과거 가격·보수율을 확인해 평가하며, 사용자 선호는 점수에 넣지 않아요.'}

    if final.recommendation is None:
        return {'schema_version': 3, 'current_evaluation': evaluation(final.current_evaluation, current=True), 'recommendation': None,
                'insights': [{'title': item.title, 'content': validate_section('question', item.content.model_dump(), sources)} for item in final.insights]}
    by_id = {r['id']: r for r in directory()['instruments']}
    by_id['CASH:KRW'] = {'name': '현금', 'ticker': 'KRW', 'kind': 'cash', 'market': 'KR'}
    allowed_accounts = selected_accounts(request)
    tax = request.preferences.tax_profile
    rows = []
    for allocation in final.recommendation.allocations:
        item = by_id.get(allocation.instrument_id)
        if item is None:
            raise ValueError('Recommendation must use a registered ETF or CASH:KRW; use search_etfs')
        if item['kind'] == 'cash':
            raise ValueError('Recommended cash weight must be zero')
        if allocation.account not in allowed_accounts:
            raise ValueError('Recommendation account was not selected by the user')
        if error := allocation_error(item, allocation.account, tax):
            raise ValueError(error)
        rows.append({'instrument_id': allocation.instrument_id, 'name': item['name'], 'ticker': item['ticker'],
                     'kind': item['kind'], 'account': allocation.account, 'weight_pct': float(allocation.weight_pct), 'amount_krw': None})
    total = Decimal(metrics['total_krw']) if metrics['total_krw'] is not None else None
    execution = plan_execution(request, rows, decision)
    if execution['reasons'] == ['ADDITIONAL_FUNDING_REQUIRED']:
        total = None
    for row in execution['rows']:
        row['name'] = by_id.get(row['instrument_id'], {}).get('name', row['instrument_id'])
    execution_amounts = {(row['instrument_id'], row['account']): row['target_krw'] for row in execution['rows']}
    if execution_amounts:
        total = sum((Decimal(value) for value in execution_amounts.values()), Decimal(0))
    if total is not None:
        amounts = allocate_amounts(total, [a.weight_pct for a in final.recommendation.allocations])
        for row, amount in zip(rows, amounts):
            row['amount_krw'] = str(amount)

    current = {}
    known = bool(request.holdings) and all(h['weight_pct'] is not None for h in metrics['holdings'])
    for h in metrics['holdings']:
        key = (h['instrument_id'], h['account'])
        current[key] = current.get(key, 0) + (h['weight_pct'] or 0)
    targets = {(h['instrument_id'], h['account']): h for h in rows}
    actions = []
    for key in dict.fromkeys([*current, *targets]):
        target = targets.get(key)
        before = current.get(key, 0) if known or not request.holdings else None
        after = target['weight_pct'] if target else 0
        transition = next((row for row in execution['rows'] if (row['instrument_id'], row['account']) == key), None)
        money_changes = transition and execution['status'] in {'ready', 'conditional'} and any(int(transition[field]) for field in ('buy_krw', 'sell_krw', 'cash_used_krw'))
        if before is not None and abs(before - after) < .005 and not money_changes:
            continue
        item = by_id.get(key[0], {'name': next((h['name'] for h in metrics['holdings'] if (h['instrument_id'], h['account']) == key), '기존 종목'), 'ticker': ''})
        actions.append({'instrument_id': key[0], 'name': item['name'], 'ticker': item['ticker'], 'account': key[1],
                        'before_pct': before, 'after_pct': after,
                        'action': '비중 설정' if before is None else '새로 담기' if before == 0 else '줄이기' if before > after else '늘리기'})
        if transition and execution['status'] in {'ready', 'conditional'}:
            actions[-1].update({field: transition[field] for field in ('buy_krw', 'sell_krw', 'cash_used_krw')})
            actions[-1]['action'] = ('현금 투입' if int(transition['cash_used_krw']) > 0 else
                '전량 매도' if int(transition['sell_krw']) and int(transition['target_krw']) == 0 else
                '일부 매도' if int(transition['sell_krw']) else
                '새로 담기' if int(transition['buy_krw']) and int(transition['before_krw']) == 0 else
                '추가 매수' if int(transition['buy_krw']) else '보유 금액 유지')
    return {'schema_version': 3, 'current_evaluation': evaluation(final.current_evaluation, current=True),
            'recommendation': {'title': final.recommendation.title, 'allocations': rows,
                               'evaluation': evaluation(final.recommendation.evaluation), 'actions': actions,
                               'total_krw': str(total) if total is not None else None, 'execution': execution,
                               'expected_return_1y': None, 'dividend_yield_1y': None,
                               'historical_performance': None, 'unavailable_reason': metrics['unavailable_reason']},
            'insights': [{'title': item.title, 'content': validate_section('question', item.content.model_dump(), sources)} for item in final.insights]}
