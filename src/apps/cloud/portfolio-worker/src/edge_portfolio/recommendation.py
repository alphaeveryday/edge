"""Validate model choices; derive amounts, deltas and totals in code."""
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from .catalog import directory
from .contracts import validate_section


def search_etfs(query):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 80:
        raise ValueError('INVALID_CATALOG_QUERY')
    q = query.casefold().strip()
    rows = directory()['instruments']
    exact = [r for r in rows if q in (r['id'].casefold(), r['ticker'].casefold())]
    matches = exact or [r for r in rows if q in r['name'].casefold() or q in r['ticker'].casefold()]
    return [{'instrument_id': r['id'], 'name': r['name'], 'ticker': r['ticker'], 'market': r['market']} for r in matches[:8]]


def publish_recommendation(final, request, metrics, sources):
    by_id = {r['id']: r for r in directory()['instruments']}
    by_id['CASH:KRW'] = {'name': '현금', 'ticker': 'KRW', 'kind': 'cash', 'market': 'KR'}
    allowed_accounts = set(request.preferences.accounts) | {h.account for h in request.holdings}
    tax = request.preferences.tax_profile
    if tax:
        allowed_accounts.update(tax.recommended_accounts)
    rows = []
    for allocation in final.recommendation.allocations:
        item = by_id.get(allocation.instrument_id)
        if item is None:
            raise ValueError('Recommendation must use a registered ETF or CASH:KRW; use search_etfs')
        if allocation.account not in allowed_accounts:
            raise ValueError('Recommendation account was not selected by the user')
        if allocation.account != '일반 계좌' and item['market'] == 'US':
            raise ValueError('US-listed ETFs cannot be allocated directly to ISA/pension accounts')
        if tax:
            subtype = next((a.subtype for a in tax.accounts if a.account == allocation.account), '')
            if item['kind'] != 'cash' and subtype in ('연금저축보험', '연금저축신탁', '신탁형', '일임형'):
                raise ValueError('Account subtype does not support the proposed direct ETF trade')
        rows.append({'instrument_id': allocation.instrument_id, 'name': item['name'], 'ticker': item['ticker'],
                     'kind': item['kind'], 'account': allocation.account, 'weight_pct': float(allocation.weight_pct), 'amount_krw': None})
    total = Decimal(metrics['total_krw']) if metrics['total_krw'] is not None else None
    if total is not None:
        exact = [total * a.weight_pct / 100 for a in final.recommendation.allocations]
        amounts = [int(v.to_integral_value(rounding=ROUND_FLOOR)) for v in exact]
        remainder = int(total) - sum(amounts)
        for i in sorted(range(len(rows)), key=lambda i: exact[i] - amounts[i], reverse=True)[:remainder]:
            amounts[i] += 1
        for row, amount in zip(rows, amounts):
            row['amount_krw'] = str(amount)

    def evaluation(value):
        scores = value.scores.model_dump() if value.scores else None
        overall = float((sum(Decimal(str(v)) for v in scores.values()) / 3).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)) if scores else None
        return {'scores': scores, 'overall': overall, 'reason': validate_section('portfolio', value.reason.model_dump(), sources),
                'method': '확인한 근거에 대한 AI 정성 평가이며, 수익 예측이나 검증된 성과 점수가 아니에요.'}

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
        if before is not None and abs(before - after) < .005:
            continue
        item = by_id.get(key[0], {'name': next((h['name'] for h in metrics['holdings'] if (h['instrument_id'], h['account']) == key), '기존 종목'), 'ticker': ''})
        actions.append({'instrument_id': key[0], 'name': item['name'], 'ticker': item['ticker'], 'account': key[1],
                        'before_pct': before, 'after_pct': after,
                        'action': '비중 설정' if before is None else '새로 담기' if before == 0 else '줄이기' if before > after else '늘리기'})
    return {'schema_version': 2, 'current_evaluation': evaluation(final.current_evaluation),
            'recommendation': {'title': final.recommendation.title, 'allocations': rows,
                               'evaluation': evaluation(final.recommendation.evaluation), 'actions': actions,
                               'total_krw': metrics['total_krw'], 'expected_return_1y': None, 'dividend_yield_1y': None,
                               'historical_performance': None, 'unavailable_reason': metrics['unavailable_reason']},
            'insights': [{'title': item.title, 'content': validate_section('question', item.content.model_dump(), sources)} for item in final.insights]}
