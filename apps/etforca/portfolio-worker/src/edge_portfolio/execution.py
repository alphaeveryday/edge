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
    if any(reason.startswith(('ACCOUNT_', 'MONTHLY_')) for reason in result['reasons']):
        result['instruction'] = ('기존 돈은 그 계좌 안에서만 바꿉니다. 계좌별 min_weight_pct 이상, max_weight_pct 이하로 '
            '계좌 안 모든 종목 비중의 합을 맞추고 소수점 8자리까지 그대로 사용하세요. 계좌 밖으로 옮길 수 있는 돈은 일반 계좌의 현금뿐이며, '
            '그 현금은 ISA·연금 계좌에 remaining_contribution_krw(올해 남은 연 한도) 안에서만 넣습니다. 매달 넣을 돈도 목표 비중대로 같은 한도 안에서 나뉘므로, 한도를 넘으면 일반 계좌 비중을 늘리세요. '
            '동일 종목을 여러 계좌에 나눠도 됩니다. 같은 실패 배치를 반복하지 말고 계좌 배치나 후보 비중을 수정하세요.')
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


def uses_text_funding(request, decision=None):
    """8차: 입력 칸의 월 납입(monthly_krw)이 글 해석보다 먼저다. 후속 질문이 납입을 새로 정하면(검증된 funding) 그 값이 먼저다.
    후속 질문은 이 경우 저장된 funding 을 물려받지 않는다(agent.py)."""
    if getattr(request, 'monthly_krw', None) is None:
        return True
    # 원금만 다시 말한 funding(monthly_krw=None)은 납입을 바꾼 것이 아니다. 0원이나 '추가 납입 없이'는 바꾼(멈춘) 것이다
    return bool(getattr(request, 'funding_questions', None) and decision and decision.funding is not None
                and (decision.funding.monthly_krw is not None or zero_funding_quote(request, decision)))


def resolve_funding(request, decision=None):
    funding = resolve_text_funding(request, decision)
    if uses_text_funding(request, decision):
        return funding
    return {'additional_krw': Decimal(0), **(funding or {}), 'monthly_krw': Decimal(request.monthly_krw)}


def resolve_text_funding(request, decision=None):
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


# 칩의 뜻(계약 기본값 1번): 채권 ETF 합계 10% 이상, 배당 ETF 합계 50% 이상
CHIP_MIN_PCT = {'some_bonds': ('bond', Decimal(10)), 'dividend': ('dividend', Decimal(50))}

# 2026년 연 납입 한도(만 원). 남은 한도를 입력하지 않으면 이 한도에서 올해 낸 돈을 뺀다.
# 연금저축과 IRP 는 합쳐서 1,800만 원이다
ANNUAL_LIMIT_MAN = {'ISA': 2000, '연금저축': 1800, 'IRP': 1800}
PENSION_ACCOUNTS = ('연금저축', 'IRP')


def plan_execution(request, allocations, decision=None):
    """8차: 추천은 입력한 보유 금액과 현금만으로 짠다. 글로 적은 추가 투자금·여러 달 계획은 목표 금액에 넣지 않고,
    월 납입은 계좌별 배분(monthly_plan)으로만 따로 낸다."""
    funding = resolve_funding(request, decision) or {}
    rules = [rule for rule in decision.constraints if rule.kind == 'no_sell'] if decision else []
    no_sell_ids = set().union(*(set(rule.instrument_ids) for rule in rules)) if rules and all(rule.instrument_ids for rule in rules) else set()
    result = build_execution(request, allocations, monthly_krw=funding.get('monthly_krw'), no_sell=bool(rules), no_sell_ids=no_sell_ids)
    if result['monthly_plan'] and decision and decision.funding is not None and uses_text_funding(request, decision):
        result['assumptions'].extend(funding_assumptions(decision.funding))  # 추정한 월 납입은 가정이라고 적는다
    return result


def contribution_room(request, account):
    """올해 그 계좌에 더 넣을 수 있는 돈(원). 일반 계좌는 한도가 없다(None)."""
    if account not in ANNUAL_LIMIT_MAN:
        return None
    tax = request.preferences.tax_profile
    profiles = {row.account: row for row in tax.accounts} if tax else {}
    names = PENSION_ACCOUNTS if account in PENSION_ACCOUNTS else (account,)
    stated = [Decimal(profiles[name].remaining_man) * 10000 for name in names if name in profiles and profiles[name].remaining_man]
    if stated and account not in PENSION_ACCOUNTS:
        return stated[0]  # 금융사에서 확인한 남은 한도가 먼저다
    paid = sum((Decimal(profiles[name].paid_man) * 10000 for name in names if name in profiles and profiles[name].paid_man), Decimal(0))
    # 연금저축과 IRP 는 한도 하나를 나눠 쓴다. 어느 한쪽에 적은 남은 한도도 둘이 함께 쓰는 한도로 본다
    return min([max(Decimal(0), ANNUAL_LIMIT_MAN[account] * 10000 - paid), *stated])


def limit_group(account):
    return 'PENSION' if account in PENSION_ACCOUNTS else account  # 연금저축·IRP 는 한도 하나를 나눠 쓴다


def split_monthly(monthly, target, room_left, stocks=()):
    """매달 넣을 돈을 목표 비중대로 나눈다. 보유 개별주에는 넣지 않고(더 사지 않는다), 한도 계좌(limit_group 별)는
    첫 달 몫이 올해 남은 한도 안이고 12개월 몫이 연 한도 안일 때만 받는다. 못 받는 계좌는 빼고 나머지에 다시 나눈다.
    ponytail: 한도를 넘는 계좌는 통째로 뺄 뿐 남은 만큼 잘라 넣지 않는다 — 한도가 빠듯한 고객이 늘면 계좌별로 자른다"""
    keys = [key for key in target if key[0] != 'CASH:KRW' and key[0] not in stocks]
    while keys:
        share = sum(target[key] for key in keys)
        plan = dict(zip(keys, allocate_amounts(monthly, [target[key] * 100 / share for key in keys])))
        used = {}
        for key, value in plan.items():
            used[limit_group(key[1])] = used.get(limit_group(key[1]), 0) + value
        annual = {'ISA': ANNUAL_LIMIT_MAN['ISA'] * 10000, 'PENSION': ANNUAL_LIMIT_MAN['연금저축'] * 10000}
        full = {group for group, value in used.items() if room_left.get(group) is not None
                and (value > room_left[group] or value * 12 > annual[group])}
        if not full:
            return [{'account': key[1], 'instrument_id': key[0], 'krw': str(value)} for key, value in plan.items()]
        keys = [key for key in keys if limit_group(key[1]) not in full]
    return []


def build_execution(request, allocations, *, monthly_krw=None, no_sell=False, no_sell_ids=()):
    monthly = Decimal(str(monthly_krw)) if monthly_krw is not None else None
    if monthly is not None and (not monthly.is_finite() or monthly < 0 or monthly > 10**13 or monthly != monthly.to_integral_value()):
        raise ValueError('INVALID_FUNDING_AMOUNT')
    result = {'status': 'needs_input', 'reasons': [], 'rows': [], 'accounts': [], 'months': None,
              'required_additional_krw': None, 'additional_krw': '0',
              'allocation_scope': 'total_portfolio', 'monthly_krw': str(monthly) if monthly is not None else None,
              'monthly_plan': [],
              'assumptions': ['입력 평가금액과 현금 기준이며 거래 수수료·세금·주 단위 주문 반올림은 제외한 금액 계획이에요.']}
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
    stocks = {holding.instrument_id for holding in request.holdings if holding.kind == 'stock'}
    # 매달 넣는 돈은 칩이 막은 종목(레버리지, 국내 상장이 아닌 것)에 넣지 않는다. 팔지 않기로 남긴 레버리지는 이렇게 몫이 줄어든다
    chips = {chip.kind for chip in getattr(request, 'constraints', [])}
    no_monthly = set(stocks)
    if chips & {'no_leverage', 'kr_listed_etf_only', *CHIP_MIN_PCT}:
        from .catalog import instruments, traits
        found = {key[0]: traits(instruments().get(key[0], {})) for key in target}
        for key in target:
            if 'no_leverage' in chips and 'leverage' in found[key[0]] or 'kr_listed_etf_only' in chips and instruments().get(key[0], {}).get('market') != 'KR':
                no_monthly.add(key[0])
        # 팔지 않기 때문에 채권·배당 비중을 못 채웠으면(substituted) 매달 넣는 돈을 그 종목에만 넣어 채운다
        short = set()
        for kind, (trait, minimum) in CHIP_MIN_PCT.items():
            keys = {key[0] for key in target if trait in found[key[0]]} - no_monthly
            if kind in chips and 'no_sell' in chips and keys and sum(w for key, w in target.items() if key[0] in keys) < minimum:
                short |= keys
        if short:
            no_monthly |= {key[0] for key in target} - short
    def monthly_plan(deposits):
        if monthly and not any(key[0] != 'CASH:KRW' and key[0] not in no_monthly for key in target) \
                and any(key[0] != 'CASH:KRW' and key[0] not in stocks for key in target):
            # 칩이 추천 구성의 모든 종목에 매달 넣는 것을 막았다(예: 레버리지만 가진 채 팔지 않기). 실행 불가가 아니다
            result['assumptions'].append('고른 조건 때문에 추천 구성 안에는 매달 넣을 종목이 없어요.')
            return []
        if monthly:
            room_left = {}
            for account in {key[1] for key in target}:
                room = contribution_room(request, account)
                if room is not None:  # 연금 두 계좌는 같은 한도에서 두 계좌에 넣은 돈을 함께 뺀다
                    group = limit_group(account)
                    room_left[group] = room - sum(value for name, value in deposits.items() if limit_group(name) == group)
            result['monthly_plan'] = split_monthly(monthly, target, room_left, no_monthly)
            if not result['monthly_plan']:
                # 금액은 알고 있다. 받을 계좌가 없을 뿐이니 입력을 다시 묻지 말고 배치를 고치게 한다
                return ['MONTHLY_CONTRIBUTION_LIMIT_EXCEEDED']
        return []
    if total <= 0:
        # 가진 돈이 없으면 지금 살 것은 없고, 월 납입이 있으면 그 배분이 곧 실행할 일이다
        if not monthly:
            return {**result, 'reasons': ['INVESTMENT_AMOUNT_REQUIRED']}
        reasons = monthly_plan({})
        return {**result, 'status': 'infeasible' if reasons else 'ready', 'reasons': reasons}
    amounts = dict(zip(target, allocate_amounts(total, list(target.values()))))
    if no_sell:
        protected = {key: value for key, value in current.items() if key[0] != 'CASH:KRW' and (not no_sell_ids or key[0] in no_sell_ids)}
        if any(key not in target for key in protected):
            return {**result, 'status': 'infeasible', 'reasons': ['NO_SELL_ZERO_TARGET']}
        required = max([value * 100 / target[key] - total for key, value in protected.items()], default=Decimal(0))
    for key in dict.fromkeys([*current, *target]):
        before, after = int(current.get(key, 0)), amounts.get(key, 0)
        delta = after - before
        cash = key[0] == 'CASH:KRW'
        result['rows'].append({'instrument_id': key[0], 'account': key[1], 'before_krw': str(before),
            'target_krw': str(after), 'buy_krw': str(max(delta, 0) if not cash else 0),
            'sell_krw': str(max(-delta, 0) if not cash else 0),
            'cash_used_krw': str(-delta if cash else 0)})
    # 고객이 넣은 개별주는 유지·축소만 한다. 종목 전체로 순매수가 생기면 실행할 수 없다.
    # 모든 실행 경로(추천 게시·리밸런싱 계산·저장안 실행)가 이 함수를 거친다. 허용 오차는 원 단위 반올림(행 수).
    net = {}
    for row in result['rows']:
        if row['instrument_id'] in stocks:
            net[row['instrument_id']] = net.get(row['instrument_id'], 0) + int(row['buy_krw']) - int(row['sell_krw'])
    if any(value > len(result['rows']) for value in net.values()):
        return {**result, 'status': 'infeasible', 'reasons': ['HELD_STOCK_BUY'], 'rows': [], 'accounts': []}
    # 8차 계좌 규칙: 기존 돈은 그 계좌 안에서만 바꾼다. 계좌 밖으로 나갈 수 있는 돈은 일반 계좌의 현금뿐이고,
    # 그 현금은 ISA·연금 계좌에 올해 남은 연 한도 안에서만 넣는다. 허용 오차는 그 계좌 행 수만큼의 원 단위 반올림.
    reasons = []
    tax = request.preferences.tax_profile
    known_accounts = {holding.account for holding in request.holdings}
    if tax:
        known_accounts.update(account.account for account in tax.accounts if account.opened)
    pension = [Decimal(0), 0]  # 연금저축+IRP 에 넣는 돈, 그 행 수
    deposits = {}
    for account in sorted({key[1] for key in current} | {key[1] for key in target}):
        before = sum(value for key, value in current.items() if key[1] == account)
        after = sum(value for key, value in amounts.items() if key[1] == account)
        movable = current.get(('CASH:KRW', account), Decimal(0)) if account == '일반 계좌' else Decimal(0)
        # 원 단위 반올림(행마다 1원)과 비중의 소수점 8자리 한 칸(행마다 총액의 1e-10)만큼 허용한다. 큰 금액에서도 경계를 따를 수 있게
        tolerance = sum(1 for key in target if key[1] == account) * (1 + total / 10**10)
        deposit, withdrawal = max(Decimal(0), after - before), max(Decimal(0), before - after)
        deposits[account] = deposit
        room = contribution_room(request, account)
        result['accounts'].append({'account': account, 'before_krw': str(before), 'target_krw': str(after),
            'deposit_krw': str(deposit), 'withdrawal_krw': str(withdrawal),
            'remaining_contribution_krw': str(room) if room is not None else None,
            'min_weight_pct': format(max(Decimal(0), (before - movable) / total * 100).quantize(Decimal('.00000001'), rounding=ROUND_FLOOR), 'f'),
            'max_weight_pct': format(min(Decimal(100), ((before + room) / total * 100).quantize(Decimal('.00000001'), rounding=ROUND_FLOOR)), 'f')
                if room is not None else None})
        if withdrawal > movable + tolerance:
            reasons.append('ACCOUNT_TRANSFER_NOT_ALLOWED:' + account)
        if room is not None and deposit > room + tolerance:
            reasons.append('ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:' + account)
        if account in PENSION_ACCOUNTS:
            pension[0] += deposit
            pension[1] += tolerance
        if account not in known_accounts and after > 0:
            result['assumptions'].append(f'{account} 계좌를 새로 열어 넣는 계획이에요.')
    if no_sell and any(int(row['sell_krw']) for row in result['rows'] if not no_sell_ids or row['instrument_id'] in no_sell_ids):
        reasons.append('NO_SELL_VIOLATION')  # 최종 고객 제약 검사(decision.check_constraints)와 같이 1원도 팔지 않는다. 넣을 새 돈은 현금뿐이다
        result['required_additional_krw'] = str(max(Decimal(0), required).to_integral_value(rounding=ROUND_CEILING))  # 비중을 고칠 때 참고
    # 두 연금 계좌는 한도 하나를 나눠 쓴다(contribution_room 이 두 계좌에 같은 값을 준다)
    if pension[0] > contribution_room(request, '연금저축') + pension[1]:
        reasons.append('ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:연금저축+IRP')
    reasons += monthly_plan(deposits)
    result['reasons'] = list(dict.fromkeys(reasons))
    result['status'] = 'infeasible' if reasons else 'ready'
    return result
