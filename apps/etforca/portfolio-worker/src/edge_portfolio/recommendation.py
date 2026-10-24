"""Validate model choices; derive amounts, deltas and totals in code."""
from decimal import Decimal, ROUND_HALF_UP
import re

from .catalog import analysis_catalog, directory, traits
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
    if item.get('kind') == 'stock' and account in ('연금저축', 'IRP'):
        return 'Individual stocks cannot be held in 연금저축 or IRP accounts'
    if account != '일반 계좌' and item['market'] == 'US':
        return 'US-listed ETFs cannot be allocated directly to ISA/pension accounts'
    if tax:
        subtype = next((a.subtype for a in tax.accounts if a.account == account), '')
        if item['kind'] != 'cash' and subtype in ('연금저축보험', '연금저축신탁', '신탁형', '일임형'):
            return 'Account subtype does not support the proposed direct ETF trade'
    return None


def held_stock_limit(rows, execution, metrics):
    """고객이 넣은 개별주는 유지하거나 줄일 수만 있다. 새로 사는 돈은 ETF로만 간다."""
    stocks = {row['instrument_id'] for row in rows if row['kind'] == 'stock'}
    if not stocks:
        return
    message = 'A held individual stock can be kept or reduced, never bought more; put new money into ETFs'
    if 'HELD_STOCK_BUY' in execution['reasons']:
        raise ValueError(message)
    if execution['rows'] or any(holding['weight_pct'] is None for holding in metrics['holdings']):
        return  # 금액표는 plan_execution 이 이미 검사했다. 현재 비중을 모르면(이름만 입력) 비교할 기준이 없다.
    # 금액표가 없으면(추가 투자금 미상 등) 비중으로 본다. 계좌를 합친 비중이 늘면 추가 매수를 뜻한다.
    before = {}
    for holding in metrics['holdings']:
        before[holding['instrument_id']] = before.get(holding['instrument_id'], 0) + (holding['weight_pct'] or 0)
    after = {}
    for row in rows:
        after[row['instrument_id']] = after.get(row['instrument_id'], 0) + row['weight_pct']
    if any(after[key] > before.get(key, 0) + .005 for key in stocks):
        raise ValueError(message)


def publish_recommendation(final, request, metrics, sources, decision=None):
    def evaluation(value, current=False):
        scores = value.scores.model_dump() if value.scores else None
        overall = float((sum(Decimal(str(v)) for v in scores.values()) / 3).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)) if scores else None
        input_only = current and scores is None and (decision and decision.scenario_requested or not sources or all(h.kind == 'cash' for h in request.holdings))
        return {'scores': scores, 'overall': overall, 'rubric_version': 1, 'reason': validate_section('question' if input_only else 'portfolio', value.reason.model_dump(), sources),
                'method': '시장 주도성·수익성·안정성 세 항목의 평균이에요. ETF 구성 종목·과거 가격·보수율을 확인해 평가하며(개별주는 과거 가격과 기업 자료로 봐요), 사용자 선호는 점수에 넣지 않아요.'}

    if final.recommendation is None:
        return {'schema_version': 3, 'current_evaluation': evaluation(final.current_evaluation, current=True), 'recommendation': None,
                'insights': [{'title': item.title, 'content': validate_section('question', item.content.model_dump(), sources)} for item in final.insights]}
    by_id = analysis_catalog(request)
    by_id['CASH:KRW'] = {'name': '현금', 'ticker': 'KRW', 'kind': 'cash', 'market': 'KR'}
    allowed_accounts = selected_accounts(request)
    tax = request.preferences.tax_profile
    rows = []
    for allocation in final.recommendation.allocations:
        item = by_id.get(allocation.instrument_id)
        if item is None:
            raise ValueError('Recommendation must use a registered ETF, a stock the customer already holds, or CASH:KRW; use search_etfs')
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
    held_stock_limit(rows, execution, metrics)
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


# ---- 8차 결과 칸 필드(report 최상위). 모델이 쓴 글(headline·answer·final_note·suggested_questions)과 코드가 정한 값을 묶는다 ----
MONEY = re.compile(r'(?<![\d.,])(?:(\d[\d,]*(?:\.\d+)?)\s*억\s*)?(?:(\d[\d,]*(?:\.\d+)?)\s*만\s*)?(?:(\d[\d,]*)\s*)?원')
CHIP_NOTES = {
    ('no_leverage', 'kept'): '레버리지·인버스는 새로 사지 않아요.',
    ('no_leverage', 'substituted'): '팔지 않기를 먼저 지켜 가진 레버리지는 그대로 두고, 매달 넣는 돈은 다른 종목에 넣어 몫을 줄여요.',
    ('some_bonds', 'kept'): '채권 ETF가 {bond}%예요.',
    ('some_bonds', 'substituted'): '팔지 않기를 먼저 지켜 채권 ETF는 {bond}%예요. {monthly_bond}',
    ('dividend', 'kept'): '배당 ETF가 {dividend}%예요.',
    ('dividend', 'substituted'): '팔지 않기를 먼저 지켜 배당 ETF는 {dividend}%예요. {monthly_dividend}',
    ('max_positions', 'kept'): '종목 {count}개예요.',
    ('max_positions', 'substituted'): '팔지 않기를 먼저 지켜 가진 {count}개를 모두 두고 새 종목은 더하지 않아요.',
    ('no_sell', 'kept'): '가진 종목은 팔지 않아요.',
    ('kr_listed_etf_only', 'kept'): '새로 담는 종목은 모두 국내 상장이에요.',
}
# 시안 v8-SH-types 의 판정 초안: 위에서부터 먼저 맞는 유형 하나(비중은 현금을 뺀 보유 기준)
# key 는 화면(portfolio-ui)의 카드 이름과 같다
TYPE_RULES = [('lev', 'leverage_pct', 20), ('stock', 'stock_pct', 40), ('chip', 'semi_ai_pct', 40),
              ('div', 'dividend_pct', 40), ('bond', 'bond_gold_pct', 30), ('pension', 'pension_pct', 50),
              ('kr', 'kr_pct', 50), ('us', 'us_pct', 70)]
# 카드에 보이는 [이름, 값] 쌍. 화면은 앞 3개만 쓴다
TYPE_STATS = {'lev': [('레버리지', 'leverage_pct', '%'), ('종목 수', 'positions', '개')],
              'stock': [('개별 주식', 'stock_pct', '%'), ('주식 수', 'stock_count', '개'), ('ETF', 'etf_count', '개')],
              'chip': [('반도체·AI', 'semi_ai_pct', '%'), ('종목 수', 'positions', '개')],
              'div': [('배당 ETF', 'dividend_pct', '%'), ('종목 수', 'positions', '개')],
              'bond': [('채권·금', 'bond_gold_pct', '%'), ('주식', 'equity_pct', '%')],
              'pension': [('연금 계좌', 'pension_pct', '%'), ('종목 수', 'positions', '개')],
              'kr': [('국내 비중', 'kr_pct', '%'), ('종목 수', 'positions', '개')],
              'us': [('미국 비중', 'us_pct', '%'), ('종목 수', 'positions', '개')],
              'bal': [('자산 종류', 'asset_kinds', '가지'), ('가장 큰 몫', 'largest_pct', '%')]}


def verdict_kind(request, execution):
    """판정 경계(계약 기본값 2번). '하나만'은 판 종목 수와 산 종목 수 가운데 큰 쪽이 하나일 때다(판 돈으로 하나를 사는 교체 포함)."""
    if not any(holding.kind != 'cash' for holding in request.holdings):
        return 'start'
    rows = [row for row in execution.get('rows', []) if row['instrument_id'] != 'CASH:KRW']
    sold = {row['instrument_id'] for row in rows if int(row['sell_krw'])}
    bought = {row['instrument_id'] for row in rows if int(row['buy_krw'])}
    if not sold and not bought:
        return 'keep'
    return 'one' if max(len(sold), len(bought)) == 1 else 'rebuild'


def clean_quote(text):
    """근거 문장에서 마크다운 머리표·링크·표 조각을 걷어 낸다. 표 행은 문장이 아니라 버린다."""
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text or '')
    text = re.sub(r'(?m)^\s*#+\s*', '', text).replace('**', '').replace('__', '')
    if text.count('|') >= 2 or re.fullmatch(r'[\s|:#*\-–—]*', text):
        return ''
    return re.sub(r'\s+', ' ', text).strip()


def analysis_item(title, section, urls):
    evidence = []
    for item in section.get('evidence', []):
        quote = clean_quote(item.get('quote'))
        if quote and all(quote != row['quote'] for row in evidence):
            evidence.append({'quote': quote, 'url': urls.get(item.get('source_id'))})
    return {'title': title, 'paragraphs': section['paragraphs'], 'evidence': evidence, 'limitations': section.get('limitations', [])}


def type_card(request, catalog, monthly, recommended=0):
    method = ['넣는 방법', '매달 꾸준히' if monthly else '한 번에 크게']
    if not any(holding.kind != 'cash' for holding in request.holdings):
        return {'key': 'start', 'stats': [method, *([['추천 종목', f'ETF {recommended}개']] if recommended else [])]}
    stats = {}
    rows = [holding for holding in request.holdings if holding.kind != 'cash' and holding.amount_krw]
    if request.input_mode == 'names' or not rows:
        return None  # 가진 종목의 비중을 모르면 유형을 정하지 않는다
    total = sum(holding.amount_krw for holding in rows)
    found = {holding.instrument_id: traits(catalog.get(holding.instrument_id, {'kind': holding.kind})) for holding in rows}
    def pct(test):
        return sum(holding.amount_krw for holding in rows if test(holding, found[holding.instrument_id])) * 100 / total
    korean = lambda h, t: not t & {'foreign', 'bond', 'gold'}
    stats.update({
        'leverage_pct': pct(lambda h, t: 'leverage' in t), 'stock_pct': pct(lambda h, t: 'stock' in t),
        'semi_ai_pct': pct(lambda h, t: 'semi_ai' in t), 'dividend_pct': pct(lambda h, t: 'dividend' in t),
        'bond_gold_pct': pct(lambda h, t: bool(t & {'bond', 'gold'})), 'pension_pct': pct(lambda h, t: h.account in ('연금저축', 'IRP')),
        'kr_pct': pct(korean), 'us_pct': pct(lambda h, t: 'us' in t and not t & {'bond', 'gold'})})
    stats['equity_pct'] = 100 - stats['bond_gold_pct']
    amounts = {}
    for holding in rows:
        amounts[holding.instrument_id] = amounts.get(holding.instrument_id, 0) + holding.amount_krw
    kinds = [lambda t: 'bond' in t, lambda t: 'gold' in t, lambda t: not t & {'foreign', 'bond', 'gold'},
             lambda t: 'us' in t and not t & {'bond', 'gold'}, lambda t: 'foreign' in t and not t & {'us', 'bond', 'gold'}]
    stats.update({'positions': len(amounts), 'stock_count': sum('stock' in found[key] for key in amounts),
                  'etf_count': sum('stock' not in found[key] for key in amounts),
                  'largest_pct': max(amounts.values()) * 100 / total,
                  'asset_kinds': sum(any(test(found[key]) for key in amounts) for test in kinds)})
    # 판정은 반올림 전 비중으로(19.6%는 레버리지 20% 이상이 아니다), 보이는 값만 정수로
    key = next((name for name, stat, minimum in TYPE_RULES if stats[stat] >= minimum), 'bal')
    shown = [[label, f'{round(float(stats[name]))}{unit}'] for label, name, unit in TYPE_STATS[key]]
    return {'key': key, 'stats': (shown + [method])[:3]}


def result_fields(published, sections, final, request, checks, catalog, sources):
    """verdict·answer·constraint_results·final_note·analysis·suggested_questions·type_card. 모두 선택 필드다."""
    fields = {}
    recommendation = published.get('recommendation')
    execution = (recommendation or {}).get('execution') or published.get('execution') or {}  # 저장 목표 실행은 최상위 execution
    urls = {source['id']: source.get('url') for source in sources}
    if recommendation:
        kind = verdict_kind(request, execution)
        if execution.get('rows') or kind == 'start':  # 실행표가 없으면(금액 모름) 판정할 수 없다
            fields['verdict'] = {'kind': kind, 'headline': final.headline}
        weights = {}
        for row in recommendation['allocations']:
            weights[row['instrument_id']] = weights.get(row['instrument_id'], 0) + row['weight_pct']
        def share(trait):
            return round(sum(weight for key, weight in weights.items() if trait in traits(catalog.get(key, {}))), 1)
        def monthly_into(trait, label):
            into = any(trait in traits(catalog.get(row['instrument_id'], {})) for row in execution.get('monthly_plan', []))
            # 매달 배분은 추천 구성 안에서만 한다. 구성에 그 종목이 없으면(팔지 않아 새로 살 돈이 없으면) 그렇게 말한다
            return f'매달 넣는 돈을 {label}에 넣어 늘려요.' if into else f'팔지 않으면 {label}를 새로 담을 돈이 없어 지금은 채우지 못해요.'
        values = {'bond': share('bond'), 'dividend': share('dividend'), 'count': len(weights),
                  'monthly_bond': monthly_into('bond', '채권 ETF'), 'monthly_dividend': monthly_into('dividend', '배당 ETF')}
        chips = {chip.kind for chip in getattr(request, 'constraints', [])}
        results = []
        for check in checks:
            status = {'pass': 'kept', 'substituted': 'substituted'}.get(check['status'])
            if check['kind'] in chips and status:
                note = CHIP_NOTES.get((check['kind'], status), '')
                results.append({'kind': check['kind'], 'status': status, 'note': note.format(**values)})
        fields['constraint_results'] = results
    if final.answer.strip():
        fields['answer'] = final.answer.strip()
    if final.final_note.strip():
        fields['final_note'] = final.final_note.strip()
    if final.suggested_questions:
        fields['suggested_questions'] = list(final.suggested_questions)
    current = published.get('current_evaluation') or {}
    items = []
    if current.get('reason'):
        items.append(analysis_item(f"왜 {current['overall']}점이에요?" if current.get('overall') is not None else '지금 구성은 어때요?', current['reason'], urls))
    if sections.get('opinion'):
        items.append(analysis_item('그래서 무엇을 하면 돼요?', sections['opinion'], urls))
    evaluation = (recommendation or {}).get('evaluation') or {}
    if evaluation.get('reason'):
        items.append(analysis_item(f"추천 구성은 왜 {evaluation['overall']}점이에요?", evaluation['reason'], urls))
    items += [analysis_item(insight['title'], insight['content'], urls) for insight in published.get('insights', [])]
    if sections.get('outlook'):
        items.append(analysis_item('앞으로 1년은요?', sections['outlook'], urls))
    fields['analysis'] = items
    card = type_card(request, catalog, execution.get('monthly_krw') if execution else getattr(request, 'monthly_krw', None),
                     len({row['instrument_id'] for row in (recommendation or {}).get('allocations', [])}))
    if card:
        fields['type_card'] = card
    return fields


def allowed_amounts(published, request):
    """answer 문장이 쓸 수 있는 금액: 최종 추천(2번 칸)·매달 넣을 돈(3번)·지난 기간(4번)·검산한 계산 결과 값과 그 합·차이."""
    values = {0}
    def add(*items):
        values.update(abs(int(Decimal(str(item)))) for item in items if item not in (None, ''))
    holdings = {}
    for holding in request.holdings:
        holdings[holding.instrument_id] = holdings.get(holding.instrument_id, 0) + (holding.amount_krw or 0)
    total = sum(holdings.values())
    add(total, *holdings.values())
    execution = ((published.get('recommendation') or {}).get('execution')) or published.get('execution') or {}
    fields = ('before_krw', 'target_krw', 'buy_krw', 'sell_krw', 'cash_used_krw')
    sums = {}
    for row in execution.get('rows', []):
        add(*(row[field] for field in fields))
        for field in fields:
            sums[(row['instrument_id'], field)] = sums.get((row['instrument_id'], field), 0) + int(row[field])
    add(*sums.values())
    for field in fields:  # 여러 종목을 합친 총 매수·매도액('총 300만 원을 옮기세요')
        add(sum(int(row[field]) for row in execution.get('rows', [])))
    for account in execution.get('accounts', []):
        add(*(account.get(field) for field in ('before_krw', 'target_krw', 'deposit_krw', 'withdrawal_krw')))
    monthly = {}
    for row in execution.get('monthly_plan', []):
        add(row['krw'])
        for key in (row['instrument_id'], row['account']):
            monthly[key] = monthly.get(key, 0) + int(row['krw'])
    add(execution.get('monthly_krw'), *monthly.values())
    scenarios = published.get('scenarios') or {}  # 계산만 묻는 질문은 검산한 계산 결과의 금액을 쓴다
    if scenarios.get('status') == 'available':
        add(scenarios.get('principal_krw'), scenarios.get('monthly_krw'))
        for row in scenarios.get('rows', []):
            add(row.get('future_krw'), row.get('paid_krw'))
            if row.get('future_krw') and row.get('paid_krw'):
                add(int(Decimal(row['future_krw'])) - int(Decimal(row['paid_krw'])))  # 불어난 돈
    past = published.get('past') or {}
    add(*past.values())
    if past.get('rec_end_krw') and past.get('cur_end_krw'):
        add(int(past['rec_end_krw']) - int(past['cur_end_krw']))
    add(*(int(past[name]) - total for name in ('rec_end_krw', 'cur_end_krw') if past.get(name)))  # 원금보다 늘거나 준 금액
    if past.get('rec_max_loss_krw') and past.get('cur_max_loss_krw'):
        add(int(past['rec_max_loss_krw']) - int(past['cur_max_loss_krw']))
    return values


def answer_amount_error(published, request):
    """계획 2단계 통과 조건: 답 문장의 금액은 2·4번 칸 값과 같아야 한다. 적은 단위(만)까지 반올림한 값이면 같다고 본다."""
    allowed = allowed_amounts(published, request)
    wrong = []
    # '3천만 원', '1억 5천만 원', '3백만 원'처럼 천·백을 쓴 표기를 숫자로 바꿔 같은 검사를 받게 한다
    # 앞 숫자를 뺀 '천만 원'·'1억 천만 원'은 1천으로 읽는다
    answer = re.sub(r'(?:(\d*)\s*(천)\s*)?(?:(\d*)\s*(백)\s*)?(\d+)?(?=\s*(?:만|원))',
                    lambda m: str((int(m[1] or 1) * 1000 if m[2] else 0) + (int(m[3] or 1) * 100 if m[4] else 0) + int(m[5] or 0)) if m[2] or m[4] else m[0],
                    published.get('answer', ''))
    for match in MONEY.finditer(answer):
        eok, man, won = match.groups()
        if not any(match.groups()):
            continue
        number = lambda value: Decimal(value.replace(',', '')) if value else Decimal(0)
        stated = number(eok) * 100000000 + number(man) * 10000 + number(won)
        # 적은 자리까지 반올림한 값만 같다고 본다. 억만 쓴 표기도 만 원 자리까지는 맞아야 한다
        unit = 1 if won else Decimal(10000) / (10 ** len(man.split('.')[1])) if man and '.' in man else 10000
        if not any(stated == (Decimal(value) / unit).to_integral_value(rounding=ROUND_HALF_UP) * unit for value in allowed):
            wrong.append(match.group().strip())
    if not wrong:
        return None
    return f"{', '.join(wrong)} 은(는) 최종 추천·매달 넣을 돈·지난 기간 칸에 없는 금액이에요. 쓸 수 있는 금액(원): " + ', '.join(f'{value:,}' for value in sorted(allowed)[:60])
