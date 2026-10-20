"""Whole-won target amounts and funded transitions, without model arithmetic."""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import re

from .contracts import Allocation

NO_CONTRIBUTION = re.compile(r'\s*(?:이번에는\s*)?(?:추가\s*)?(?:납입|입금|투자)(?:금)?\s*없이(?:\s*계산(?:해\s*주세요|해줘|해주세요)?)?[.!?]?\s*')
FUNDING_CALCULATION_SUFFIX = r'(?:으로|로)\s*계산(?:해\s*주세요|해주세요|해줘)'


def projection_question(clause):
    periods = r'(?:\d+\s*+년\s*+(?:(?:뒤|후)(?:에)?)?\s*+(?:[,·/]|및|그리고)?\s*+)+'
    words = '금액|자산|원금|이자|과|와|은|는|이|가|을|를|도|몇|원|얼마나|얼마|가정별로|가정별|수익률별로|계산해|계산|알려|보여|주세요|줘|해|요|될까요|되나요|인가요|예상|의'.split('|')
    projection = r'(?:' + '|'.join(sorted(words, key=len, reverse=True)) + r'|\s)++'
    assumption = r'(?:(?:연|연간)?\s*+-?\d+(?:\.\d+)?\s*+%\s*+(?:수익률)?(?:을|로)?\s*+(?:가정하면|가정할 때|가정해서)?\s*+)?'
    subject = r'(?:(?:원금|현재\s*+자산|현재\s*+평가금액|보유금액)(?:은|는|이|가)?\s*+)?'
    unchanged = r'(?:(?:같은|동일한?)\s*+조건으로\s*+)?'
    return bool(re.fullmatch(r'\s*+' + unchanged + subject + assumption + periods + projection, clause))


def projection_context(value, decision):
    if not decision or not decision.scenario_requested:
        return value
    clauses = re.split(r'((?<!\d)\.|\.(?!\d)|[!?;\n])', value)
    for index, clause in enumerate(clauses):
        if projection_question(clause):
            clauses[index] = ''  # A complete projection question, never a funding schedule.
    return ''.join(clauses)


def funding_context(text, decision):
    remainder = projection_context(text, decision).strip(' .!?;\n')
    if decision and decision.scenario_requested:
        from .scenarios import stated_principal
        if stated_principal(text) is not None:
            clauses = re.split(r'((?<!\d)\.|\.(?!\d)|[!?;\n])', remainder)
            for index in range(0, len(clauses), 2):
                if stated_principal(clauses[index]) is not None:
                    clauses[index] = ''
            remainder = ''.join(clauses).strip(' .!?;\n')
    return remainder


def zero_contribution(text, decision):
    if NO_CONTRIBUTION.fullmatch(text):
        return text
    remainder = funding_context(text, decision)
    return remainder if NO_CONTRIBUTION.fullmatch(remainder) else None


def zero_funding_quote(request, decision):
    questions = getattr(request, 'funding_questions', None) or [request.question]
    quote = zero_contribution(questions[-1], decision)
    if quote:
        return quote
    if len(questions) > 1 and not funding_context(questions[-1], decision):
        return zero_contribution(questions[0], decision)
    return None


def execution_tool(request, args, decision, catalog):
    from .recommendation import selected_accounts, allocation_error
    if set(args) != {'allocations'} or not isinstance(args['allocations'], list) or not 1 <= len(args['allocations']) <= 20:
        raise ValueError('INVALID_EXECUTION_ALLOCATION')
    rows = [Allocation.model_validate(row).model_dump() for row in args['allocations']]
    for row in rows:
        item = catalog.get(row['instrument_id'])
        if item is None or row['account'] not in selected_accounts(request):
            raise ValueError('UNREGISTERED_OR_UNSELECTED_EXECUTION_ALLOCATION')
        if error := allocation_error(item, row['account'], request.preferences.tax_profile):
            raise ValueError(error)
    result = plan_execution(request, rows, decision)
    if any(reason.startswith('ACCOUNT_') for reason in result['reasons']):
        result['instruction'] = ('계좌별 before_krw와 remaining_contribution_krw, max_weight_pct를 확인하세요. '
            '계좌 내 모든 종목 비중의 합은 max_weight_pct를 넘지 않도록 소수점 8자리까지 그대로 사용하세요. '
            '남은 납입 한도가 0인 계좌에는 새 돈을 넣지 말고, 기존 계좌 안에서 종목을 교체하는 배치를 먼저 계산하세요. '
            '동일 종목을 여러 계좌에 나눠도 됩니다. 같은 실패 배치를 반복하지 말고 계좌 배치나 후보 비중을 수정하세요. '
            '납입 여력이 확인되지 않은 금액 계획은 조건부로 설명하되, 추천 비중과 결론은 완성하세요.')
    for row in result['rows']:
        row['name'] = catalog.get(row['instrument_id'], {}).get('name', row['instrument_id'])
    return result


TOOL = {'type': 'function', 'function': {'name': 'calculate_rebalance',
    'description': '전체 포트폴리오 목표 비중에서 실제 매수·매도 금액, 추가 자금, 매도 금지·계좌 제약을 계산한다. 자금은 고객 원문에서만 읽으며 후보 승인과 별개다.',
    'parameters': {'type': 'object', 'properties': {'allocations': {'type': 'array', 'minItems': 1, 'maxItems': 20,
        'items': Allocation.model_json_schema()}}, 'required': ['allocations'], 'additionalProperties': False}}}


def parse_funding(text):
    number = r'([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)'
    matches = [match for match in re.finditer(r'(?<![0-9A-Za-z.,+\-])(?:' + number + r'\s*억\s*)?(?:' + number + r'\s*만\s*)?(?:' + number + r'\s*)?원', text)
               if any(match.groups())]
    if len(matches) != 1 or any(word in text for word in ('또는', '정도', '약', '~', '-', '이하', '이상')):
        return None
    match = matches[0]
    prefix, suffix = text[:match.start()].strip(), text[match.end():].strip()
    if prefix not in {'매월', '매월 말', '매달', '매달 꾸준히', '월마다', '월', '추가', '추가로', '추가 투자금', '일시', '일시금', '목돈', '한 번'} or suffix not in {'', '씩'}:
        return None  # Require a complete commitment quote; do not ignore qualifiers or payment counts.
    amount = sum((Decimal(value.replace(',', '')) * unit for value, unit in zip(match.groups(), (100000000, 10000, 1)) if value), Decimal(0))
    if amount <= 0 or amount > 10**13 or amount != amount.to_integral_value():
        return None
    monthly = any(word in text for word in ('매월', '매달', '월마다')) or bool(re.search(r'(^|\s)월\s*\d', text))
    once = any(word in text for word in ('추가', '일시', '목돈', '한 번'))
    if monthly:
        return {'monthly_krw': amount, 'additional_krw': Decimal(0)}
    return {'monthly_krw': None, 'additional_krw': amount} if once else None


def allocate_amounts(total, weights):
    exact = [total * weight / 100 for weight in weights]
    amounts = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    remainder = int(total) - sum(amounts)
    for index in sorted(range(len(weights)), key=lambda i: (exact[i] - amounts[i], -i), reverse=True)[:remainder]:
        amounts[index] += 1
    return amounts


def validate_funding(funding, text):
    if not funding.source.strip():
        raise ValueError('CUSTOMER_FUNDING_SOURCE: quote the customer input')
    if funding.source not in text:
        funding.source = text
    amounts = set()
    for match in re.finditer(r'(?<![0-9A-Za-z.,+\-])[\d영일이삼사오육칠팔구십백천만억][\d,.\s영일이삼사오육칠팔구십백천만억]*원', funding.source):
        total, section, number = Decimal(0), Decimal(0), None
        normalized = match[0].translate(str.maketrans('영일이삼사오육칠팔구', '0123456789'))
        for token in re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?|[십백천만억]', normalized):
            if token[0].isdigit():
                if number is not None:
                    break
                number = Decimal(token.replace(',', ''))
            elif token in '십백천':
                section += (number if number is not None else 1) * {'십': 10, '백': 100, '천': 1000}[token]
                number = None
            else:
                total += (section + number if number is not None else section or 1) * {'만': 10000, '억': 100000000}[token]
                section, number = Decimal(0), None
        else:
            amounts.add(total + section + (number or 0))
    components = funding.monthly_components_krw
    if components and (any(Decimal(value) not in amounts for value in components) or sum(components) != funding.monthly_krw):
        raise ValueError('CUSTOMER_FUNDING_AMOUNT: monthly components must match quoted amounts and sum to monthly_krw')
    if funding.estimated_fields and not any(value.strip() for value in funding.assumptions):
        raise ValueError('CUSTOMER_FUNDING_ASSUMPTION: explain estimated payment amounts')
    if any(value is not None and (value != 0 or name == 'principal_krw') and Decimal(value) not in amounts and name not in funding.estimated_fields
           and not (name == 'monthly_krw' and components)
           for name, value in ((name, getattr(funding, name)) for name in ('additional_krw', 'monthly_krw', 'principal_krw'))):
        raise ValueError('CUSTOMER_FUNDING_AMOUNT: preserve the quoted won amount and units')


def funding_assumptions(funding):
    labels = {'additional_krw': '추가 투자금', 'monthly_krw': '월 납입금'}
    return [*funding.assumptions, *(f'{labels[name]} {getattr(funding, name):,}원으로 가정한 참고 계산이에요.'
            for name in dict.fromkeys(funding.estimated_fields) if getattr(funding, name) is not None)]


def resolve_funding(request, decision=None):
    if decision and decision.funding is not None:
        funding = decision.funding
        return {'additional_krw': Decimal(funding.additional_krw),
                'monthly_krw': Decimal(funding.monthly_krw) if funding.monthly_krw is not None else None}
    if zero_funding_quote(request, decision):
        return {'additional_krw': Decimal(0), 'monthly_krw': None}
    text = decision.funding_text if decision and decision.funding_text else request.preferences.contribution
    funding = parse_funding(text) if text else None
    if funding:
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)
        questions = getattr(request, 'funding_questions', None) or [request.question]
        preferences = request.preferences.model_dump()
        if len(questions) > 1 and text in questions[-1]:
            from .scenarios import principal_statements
            if principal_statements(questions[0]) is not None:
                questions = questions[-1:]  # Supersede only a fully understood old commitment.
            if parse_funding(preferences['contribution']) is not None:
                preferences['contribution'] = ''
        sources = [*questions, *strings(preferences)]
        commitments = {re.sub(r'\s+', '', match[0]) for source in sources for match in re.finditer(
            r'(?:매월|매달(?:\s+꾸준히)?|월마다|월|추가로?|일시금?|목돈|한 번)\s*[0-9][0-9,.\s억만천백십]*원(?:씩)?', source)}
        occurrences = [(source[:match.start()], source[match.end():]) for source in sources
                       for match in re.finditer(re.escape(text), source)]
        def duration_context(value):
            return projection_context(value, decision)
        def has_duration(before, after):
            context = duration_context(before + after)
            return bool(re.search(r'\d+(?:\.\d+)?\s*(?:년|개월|달|월간|회|번|일|주|분기)|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇)\s*(?:개월|달|년|회)|동안|까지|이후|부터', context))
        def complete_funding_clause(after):
            clauses = [part.strip() for part in re.split(r'[.!?;\n]', duration_context(after)) if part.strip()]
            tail = clauses[0] if clauses else ''
            plain = r'(?:씩)?\s*(?:(?:을|를)?\s*(?:넣|납입|투자|입금|추가)(?:을게요|할게요|합니다|해요|하겠습니다|하겠어요|겠습니다|습니다|어요|기|할 예정입니다|을 예정입니다|할 계획입니다|을 계획입니다)?)?'
            names = '|'.join(re.escape(name) for h in request.holdings for name in (h.name, (h.instrument_id or '').split(':')[-1]) if name)
            no_sale = r'(?:(?:' + (names or r'(?!)') + r'|보유 종목|기존 종목)(?:는|은|을|를)?\s*)?(?:팔지|매도하지)\s*않(?:을게요|겠습니다|아요|습니다)'
            combined = r'(?:을|를)?\s*(?:넣|납입하|투자하|입금하|추가하)(?:고|며|으며)\s*' + no_sale
            return bool(re.fullmatch(plain + r'(?:\s*매도\s*금지)?|' + combined + '|' + FUNDING_CALCULATION_SUFFIX, tail)) and all(
                re.fullmatch(no_sale + r'|매도\s*금지', clause) for clause in clauses[1:])
        def unconditional_prefix(before):
            prefix = duration_context(before).strip(' .!?;\n')
            if prefix in {'', '저는', '저희는', '일단'}:
                return True
            if decision and decision.scenario_requested and not re.search(r'(?<!\d)\.|\.(?!\d)|[!?;\n]', prefix):
                from .scenarios import stated_principal
                if stated_principal(prefix) is not None:
                    return True
            names = '|'.join(re.escape(name) for h in request.holdings for name in (h.name, (h.instrument_id or '').split(':')[-1]) if name)
            # Accept a complete allocation clause, never an arbitrary clause that
            # could make the quoted funding contingent on income or a future event.
            asset = r'(?:' + (names or r'(?!)') + r'|[A-Z]{1,10})\s*\d+(?:\.\d+)?\s*%'
            return bool(re.fullmatch(asset + r'(?:\s*,\s*' + asset + r')*\s*(?:로 구성하고|로 하고|를 목표로 하고)', prefix))
        funding_contexts = [duration_context(value) for value in sources]
        scoped_restrictions = any(has_duration(source, '') or re.search(r'[가-힣]+면|경우|조건|가정|후에|뒤에|전에는', source) for source in funding_contexts)
        # A separate free-form note may qualify the commitment. Without a
        # complete verified funding clause there, do not infer its independence.
        separate_note = request.preferences.note.strip()
        if separate_note and text not in separate_note and not re.fullmatch(r'매도\s*금지', separate_note):
            scoped_restrictions = True
        ambiguous = scoped_restrictions or len(commitments) > 1 or any(any(word in source for word in ('또는', '미정', '아마', '가능하면', '취소', '철회', '중단', '보류'))
            or re.search(r'(?:자금|돈|예산).{0,12}(?:없|부족|미확보)|(?:납입|입금|투자|추가)(?:금|금액)?\s*(?:을|를|은|는)?\s*(?:없|하지\s*(?:않|못)|중단|못|안\s*(?:하|합|할|해|넣|낼|내))', source) for source in funding_contexts)
        if ambiguous or not occurrences or any(re.search(r'(?:약|대략|최대|최소|아마|가능하면)\s*$', before)
                or re.match(r'\s*(?:정도|내외|가량|쯤|이상|이하|미만|초과|전후|예상|한도|또는|[~±-])', after)
                or not complete_funding_clause(after)
                or not unconditional_prefix(before)
                or has_duration(before, after) for before, after in occurrences):
            funding = None
    if funding is None and not (decision and decision.funding_text) and not any(word in text for word in ('매달', '매월', '원')):
        funding = {'additional_krw': Decimal(0), 'monthly_krw': None}
    return funding


def plan_execution(request, allocations, decision=None):
    funding = resolve_funding(request, decision)
    rules = [rule for rule in decision.constraints if rule.kind == 'no_sell'] if decision else []
    no_sell_ids = set().union(*(set(rule.instrument_ids) for rule in rules)) if rules and all(rule.instrument_ids for rule in rules) else set()
    delay = decision.funding.start_delay_months if decision and decision.funding is not None else 0
    result = build_execution(request, allocations, no_sell=bool(rules), no_sell_ids=no_sell_ids,
                             start_delay_months=delay, **(funding or {}))
    if decision and decision.funding is not None:
        interpreted = decision.funding
        result['assumptions'].extend(funding_assumptions(interpreted))
        if interpreted.assumptions and result['status'] == 'ready':
            result['status'] = 'conditional'
        if interpreted.max_months is not None and result['months'] is not None and result['months'] - delay > interpreted.max_months:
            result.update(status='infeasible', reasons=['FUNDING_DURATION_EXCEEDED'], rows=[])
    definite_failure = any(reason == 'NO_SELL_ZERO_TARGET' or reason.startswith('ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:') for reason in result['reasons'])
    if funding is None and not definite_failure:
        result.update(status='needs_input', reasons=['ADDITIONAL_FUNDING_REQUIRED'], rows=[])
    return result


def build_execution(request, allocations, *, additional_krw=0, monthly_krw=None, no_sell=False, no_sell_ids=(), start_delay_months=0):
    additional = Decimal(str(additional_krw))
    monthly = Decimal(str(monthly_krw)) if monthly_krw is not None else None
    if any(not value.is_finite() or value < 0 or value > 10**13 or value != value.to_integral_value()
           for value in [additional, *([monthly] if monthly is not None else [])]):
        raise ValueError('INVALID_FUNDING_AMOUNT')
    result = {'status': 'needs_input', 'reasons': [], 'rows': [], 'accounts': [], 'months': None,
              'required_additional_krw': None, 'additional_krw': str(additional),
              'allocation_scope': 'total_portfolio', 'monthly_krw': str(monthly) if monthly is not None else None,
              'assumptions': ['입력 평가금액 기준이며 거래 수수료·세금·주 단위 주문 반올림은 제외한 금액 계획이에요.']}
    if request.input_mode != 'amount' or any(h.amount_krw is None or not h.instrument_id for h in request.holdings):
        return {**result, 'reasons': ['CURRENT_AMOUNTS_REQUIRED']}
    current = {}
    for holding in request.holdings:
        key = (holding.instrument_id, holding.account)
        current[key] = current.get(key, Decimal(0)) + holding.amount_krw
    total = sum(current.values(), Decimal(0))
    target = {(row['instrument_id'], row['account']): Decimal(str(row['weight_pct'])) for row in allocations}
    if len(target) != len(allocations) or not target or sum(target.values()) != 100 or any(not w.is_finite() or w <= 0 for w in target.values()):
        raise ValueError('INVALID_EXECUTION_ALLOCATION')
    required = Decimal(0)
    if no_sell:
        protected = {}
        lower = Decimal(0)
        for key, value in current.items():
            if key[0] == 'CASH:KRW' or no_sell_ids and key[0] not in no_sell_ids:
                continue
            if key not in target:
                return {**result, 'status': 'infeasible', 'reasons': ['NO_SELL_ZERO_TARGET']}
            protected[key] = value
            lower = max(lower, (value - 1) * 100 / target[key] - total)
            required = max(required, value * 100 / target[key] - total)
        required = required.to_integral_value(rounding=ROUND_CEILING)
        def preserves(funding):
            amounts = dict(zip(target, allocate_amounts(total + funding, list(target.values()))))
            return all(amounts[key] >= value for key, value in protected.items())
        # Largest-remainder rounding can save up to one won per holding. Search that
        # bounded interval instead of assuming monotonic apportionment.
        if required - lower <= 10000:
            required = Decimal(next(value for value in range(int(lower.to_integral_value(rounding=ROUND_CEILING)), int(required) + 1) if preserves(value)))
        else:
            # shortcut: tiny weights use a safe bound; exact minima need a bounded apportionment solver.
            result['required_additional_is_minimum'] = False
            result['assumptions'].append('아주 작은 목표 비중은 반올림에 따른 최소금액 대신 매도 없이 가능한 추가 자금의 상한을 계산했어요.')
        result['required_additional_krw'] = str(required)
        if not preserves(additional) or not additional and monthly:
            if not monthly:
                return {**result, 'status': 'infeasible', 'reasons': ['ADDITIONAL_FUNDING_REQUIRED']}
            months = int(((required - additional) / monthly).to_integral_value(rounding=ROUND_CEILING))
            months = max(1, months)
            if result.get('required_additional_is_minimum') is False:
                # Check every supported finite funding duration before rejecting it on a conservative bound.
                first = max(1, int(((lower - additional) / monthly).to_integral_value(rounding=ROUND_CEILING)))
                months = next((n for n in range(first, min(months, 600) + 1) if preserves(additional + n * monthly)), months)
            while not preserves(additional + months * monthly):
                months += 1
            additional += months * monthly
            result.update(status='conditional', months=months, additional_krw=str(additional))
            result['assumptions'].append('가격·환율·분배금이 변하지 않고 매월 같은 금액을 넣는 조건부 계산이며 달성 시점을 보장하지 않아요.')
    if not additional and monthly:
        additional = monthly
        result.update(status='conditional', months=1, additional_krw=str(additional))
        result['assumptions'].append('별도 목돈 없이 첫 월 납입금을 넣는 시점의 계획이며 그때까지 가격이 변하지 않는다고 가정해요.')
    if result['months'] is not None and start_delay_months:
        result['months'] += start_delay_months
        result['assumptions'].append(f'월 납입은 {start_delay_months}개월 뒤부터 시작하는 계획이에요.')
    if total + additional <= 0:
        return {**result, 'reasons': ['INVESTMENT_AMOUNT_REQUIRED']}
    amounts = dict(zip(target, allocate_amounts(total + additional, list(target.values()))))
    for key in dict.fromkeys([*current, *target]):
        before, after = int(current.get(key, 0)), amounts.get(key, 0)
        delta = after - before
        cash = key[0] == 'CASH:KRW'
        result['rows'].append({'instrument_id': key[0], 'account': key[1], 'before_krw': str(before),
            'target_krw': str(after), 'buy_krw': str(max(delta, 0) if not cash else 0),
            'sell_krw': str(max(-delta, 0) if not cash else 0),
            'cash_used_krw': str(-delta if cash else 0)})
    reasons = []
    tax = request.preferences.tax_profile
    known_accounts = {holding.account for holding in request.holdings}
    if tax:
        known_accounts.update(account.account for account in tax.accounts if account.opened)
    for account in sorted({key[1] for key in current} | {key[1] for key in target}):
        before = sum(value for key, value in current.items() if key[1] == account)
        after = sum(value for key, value in amounts.items() if key[1] == account)
        deposit, withdrawal = max(Decimal(0), after - before), max(Decimal(0), before - after)
        profile = next((row for row in tax.accounts if row.account == account), None) if tax else None
        remaining = Decimal(profile.remaining_man) * 10000 if profile and profile.remaining_man else None
        result['accounts'].append({'account': account, 'before_krw': str(before), 'target_krw': str(after),
                                   'deposit_krw': str(deposit), 'withdrawal_krw': str(withdrawal),
                                   'remaining_contribution_krw': str(remaining) if remaining is not None else None,
                                   'max_weight_pct': format(min(Decimal(100), ((before + remaining) / (total + additional) * 100)
                                       .quantize(Decimal('.00000001'), rounding=ROUND_FLOOR)), 'f')
                                       if remaining is not None and account != '일반 계좌' and (result['months'] or 0) <= 1 else None})
        if account == '일반 계좌':
            continue
        if account not in known_accounts and after > 0:
            reasons.append('ACCOUNT_OPENING_REQUIRED:' + account)
        if withdrawal:
            reasons.append('ACCOUNT_WITHDRAWAL_REVIEW_REQUIRED:' + account)
        if deposit:
            if profile is None or not profile.remaining_man or (result['months'] or 0) > 1:
                reasons.append('ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED:' + account)
            elif deposit > Decimal(profile.remaining_man) * 10000:
                reasons.append('ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:' + account)
    if no_sell and any(int(row['sell_krw']) for row in result['rows'] if not no_sell_ids or row['instrument_id'] in no_sell_ids):
        reasons.append('NO_SELL_VIOLATION')
    result['reasons'] = reasons
    if reasons:
        result['status'] = 'infeasible' if any('EXCEEDED' in reason or reason == 'NO_SELL_VIOLATION' for reason in reasons) else 'needs_input'
    elif result['status'] != 'conditional':
        result['status'] = 'ready'
    return result
