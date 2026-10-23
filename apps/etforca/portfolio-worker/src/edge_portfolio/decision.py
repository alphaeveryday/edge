"""Extract intent with the model; build and enforce customer constraint chips in code."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .catalog import traits
from .contracts import Section, StrictModel
from .execution import CHIP_MIN_PCT, allocate_amounts, plan_execution

# 8차: 지킬 조건은 입력의 칩(request.constraints)뿐이다. 글은 참고로만 모델에 넘긴다.
CHIP_LABELS = {'no_leverage': '레버리지 빼기', 'some_bonds': '채권도 조금', 'dividend': '배당 받기',
               'max_positions': '종목 {n}개 이하', 'no_sell': '갖고 있는 건 팔지 않기', 'kr_listed_etf_only': '국내 상장 ETF만'}
# 후보·추천이 통과한 검사 결과. substituted 는 '팔지 않기'를 먼저 지키느라 다른 칩을 대신 지킨 것이다
ACCEPTED = {'pass', 'model_review', 'substituted'}


class Constraint(StrictModel):
    # 옛 보고서의 decision 을 다시 읽을 수 있게 모델이 뽑던 종류도 남긴다. 새 분석은 칩 종류만 만든다
    kind: Literal['min_weight', 'max_weight', 'exclude', 'require', 'max_positions', 'accounts', 'markets', 'no_sell', 'unverified',
                  'no_leverage', 'some_bonds', 'dividend', 'kr_listed_etf_only']
    source: str = Field(min_length=1, max_length=1000)
    instrument_ids: list[str] = Field(default_factory=list, max_length=20)
    accounts: list[Literal['일반 계좌', 'ISA', '연금저축', 'IRP']] = Field(default_factory=list, max_length=4)
    markets: list[Literal['KR', 'US']] = Field(default_factory=list, max_length=2)
    value: Decimal | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    scope: Literal['each', 'combined'] | None = None

    @model_validator(mode='after')
    def sufficient_arguments(self):
        if self.kind in {'min_weight', 'max_weight', 'exclude', 'require'} and not self.instrument_ids:
            raise ValueError('Constraint needs registered instrument IDs; use unverified for unknown exposure')
        if self.kind in {'min_weight', 'max_weight', 'max_positions'} and self.value is None:
            raise ValueError('Constraint needs a numeric threshold')
        if self.kind in {'min_weight', 'max_weight'} and len(self.instrument_ids) > 1 and self.scope is None:
            raise ValueError('Multiple instrument weight limits must specify scope=each or combined')
        if self.kind == 'max_positions' and (self.value < 1 or self.value != self.value.to_integral_value()):
            raise ValueError('Position count must be a positive integer')
        if self.kind == 'accounts' and not self.accounts or self.kind == 'markets' and not self.markets:
            raise ValueError('Constraint needs allowed accounts or markets')
        return self


class Funding(StrictModel):
    """Model interpretation of customer money; arithmetic remains deterministic."""
    source: str = Field(default='', max_length=1000000)
    additional_krw: int = Field(default=0, ge=0, le=10**13)
    monthly_krw: int | None = Field(default=None, ge=0, le=10**13)
    monthly_components_krw: list[Annotated[int, Field(strict=True, ge=0, le=10**13)]] = Field(default_factory=list, max_length=20)
    principal_krw: int | None = Field(default=None, ge=0, le=20 * 10**13)
    max_months: int | None = Field(default=None, ge=1, le=600)
    start_delay_months: int = Field(default=0, ge=0, le=600)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    estimated_fields: list[Literal['additional_krw', 'monthly_krw']] = Field(default_factory=list, max_length=2)


class Decision(StrictModel):
    questions: list[str] = Field(default_factory=list, max_length=5)
    kind: Literal['review', 'rebalance', 'new_money', 'new_portfolio', 'scenario'] = 'review'
    execute_saved_target: bool = False
    scenario_requested: bool = False
    execution_requested: bool = False
    objectives: list[Literal['growth', 'stability', 'income', 'preserve', 'simplicity']] = Field(default_factory=list, max_length=5)
    constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    missing_inputs: list[str] = Field(default_factory=list, max_length=10)
    funding_text: str = Field(default='', max_length=100)
    funding: Funding | None = None
    # 후속 질문의 견주기('QLD를 팔면?')가 이번 계산에서만 풀어 보는 칩. 저장된 입력의 칩은 그대로 남는다
    relaxed_constraints: list[Literal['no_leverage', 'some_bonds', 'dividend', 'max_positions', 'no_sell', 'kr_listed_etf_only']] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def meaningful_missing_inputs(self):
        if any(not value.strip() for value in self.missing_inputs):
            raise ValueError('Missing inputs must identify the required customer information')
        return self


class QuestionSection(Section):
    decision: Decision


def validate_decision(decision, request, catalog, *, question_text=None):
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    text = '\n'.join([request.question, *strings(request.preferences.model_dump(mode='json'))])
    if getattr(request, 'monthly_krw', None) is not None:
        text += f'\n매달 넣을 돈 {request.monthly_krw}원'  # 숫자 칸의 월 납입도 모델이 인용할 수 있는 고객 입력이다
    original = request.question if question_text is None else question_text
    if any(not question.strip() or question not in text for question in decision.questions):
        decision.questions = [original] if original.strip() else []
    if decision.questions:
        covered = set()
        for question in decision.questions:
            start = original.find(question)
            while start >= 0:
                covered.update(range(start, start + len(question)))
                start = original.find(question, start + 1)
        if any(char.isalnum() and index not in covered for index, char in enumerate(original)):
            # Preserve all customer context without asking the model to copy it again.
            decision.questions = [original]
    decision.constraints = chip_constraints(request)  # 모델이 글에서 뽑은 조건은 버린다
    if not getattr(request, 'funding_questions', None):
        decision.relaxed_constraints = []  # 첫 분석은 고른 칩을 모두 지킨다. 견주기는 후속 질문에서만
    decision.constraints = [rule for rule in decision.constraints if rule.kind not in decision.relaxed_constraints]
    if decision.funding is None and decision.funding_text and decision.funding_text not in text:
        raise ValueError('CUSTOMER_FUNDING_SOURCE: quote explicit additional money, not current holdings')
    if decision.funding is not None:
        from .execution import validate_funding
        validate_funding(decision.funding, text)
    if len(set(decision.objectives)) != len(decision.objectives):
        raise ValueError('Objective priorities must be distinct')
    return decision


def chip_constraints(request):
    rules = []
    for chip in getattr(request, 'constraints', []):
        source = CHIP_LABELS[chip.kind].format(n=chip.value)
        rules.append(Constraint(kind=chip.kind, source=source, value=Decimal(chip.value) if chip.value is not None else None))
    return rules


def held_weights(request):
    """지금 종목별 비중(%, 현금 포함 전체 기준). 이름만 넣어 비중을 모르면 None."""
    total = sum(h.amount_krw or 0 for h in request.holdings)
    if not request.holdings:
        return {}
    if request.input_mode == 'names' or not total:
        return None
    held = {}
    for holding in request.holdings:
        held[holding.instrument_id] = held.get(holding.instrument_id, Decimal(0)) + Decimal(holding.amount_krw) * 100 / Decimal(total)
    return held


def chip_status(rule, weights, request, catalog, rows=()):
    """칩 하나의 결정적 판정. 부딪치면 보류하지 않고 '팔지 않기'를 먼저 지킨 뒤 남은 칩을 substituted 로 둔다."""
    held = held_weights(request)
    no_sell = any(chip.kind == 'no_sell' for chip in getattr(request, 'constraints', []))
    def has(key, trait):
        return trait in traits(catalog.get(key, {}))
    if rule.kind in {'no_leverage', 'kr_listed_etf_only'}:
        # 새로 사는 것만 막는다. 가진 것은 그대로 두거나 줄여도 된다(비중이 지금보다 늘면 새로 산 것)
        banned = [key for key, weight in weights.items() if weight > 0 and key != 'CASH:KRW'
                  and (has(key, 'leverage') if rule.kind == 'no_leverage' else catalog.get(key, {}).get('market') != 'KR')]
        if held is None:
            return 'unknown' if banned else 'pass'
        # 1원이라도 더 사면 새로 산 것이다. 실행표(build_execution)와 같은 원 단위 배분으로 견준다
        total = sum(h.amount_krw for h in request.holdings) if request.input_mode == 'amount' else None
        target, before = {}, {}
        if total and rows:
            for row, amount in zip(rows, allocate_amounts(total, [Decimal(str(row['weight_pct'])) for row in rows])):
                target[row['instrument_id']] = target.get(row['instrument_id'], 0) + amount
            for holding in request.holdings:
                before[holding.instrument_id] = before.get(holding.instrument_id, 0) + holding.amount_krw
        def bought(key):
            return target.get(key, 0) > before.get(key, 0) if target else weights[key] > held.get(key, 0)
        if any(bought(key) for key in banned):
            return 'fail'
        # 팔지 않기 때문에 가진 레버리지를 남겼다. 매달 넣는 돈을 넣지 않아 몫을 줄인다(split_monthly)
        return 'substituted' if rule.kind == 'no_leverage' and no_sell and any(held.get(key) for key in banned) else 'pass'
    if rule.kind in CHIP_MIN_PCT:
        trait, minimum = CHIP_MIN_PCT[rule.kind]
        if sum((weight for key, weight in weights.items() if has(key, trait)), Decimal(0)) >= minimum:
            return 'pass'
        # 팔지 않기로 묶인 다른 종목 때문에 채울 수 없을 때만 대신 지킨다
        if not no_sell or held is None:
            return 'fail'
        kept = {key for key in held if key != 'CASH:KRW'}
        chips = {chip.kind: chip.value for chip in request.constraints}
        def blocked(key):  # 다른 칩 때문에 더 살 수 없는 종목
            return ('kr_listed_etf_only' in chips and catalog.get(key, {}).get('market') != 'KR'
                    or 'no_leverage' in chips and has(key, 'leverage'))
        # 현금은 새 종목(종목 수 칩이 허락할 때)이나 더 살 수 있는 가진 같은 성격 종목에만 넣을 수 있다
        room = 'max_positions' not in chips or len(kept) < chips['max_positions'] or any(has(key, trait) and not blocked(key) for key in kept)
        best = sum((held[key] for key in kept if has(key, trait)), Decimal(0)) + (held.get('CASH:KRW', 0) if room else 0)
        return 'substituted' if best < minimum else 'fail'
    # max_positions: 서로 다른 종목 수(현금 제외). 팔지 않기로 이미 n개를 넘게 가졌으면 새 종목만 더하지 않는다
    count = sum(value > 0 for key, value in weights.items() if key != 'CASH:KRW')
    if count <= rule.value:
        return 'pass'
    kept = len({key for key in held or {} if key != 'CASH:KRW'})
    return 'substituted' if no_sell and held is not None and kept > rule.value and count <= kept else 'fail'


def check_constraints(decision, rows, request, catalog):
    weights = {}
    for row in rows:
        key = row['instrument_id']
        weights[key] = weights.get(key, Decimal(0)) + Decimal(str(row['weight_pct']))
    checks = []
    for number, rule in enumerate(decision.constraints, 1):
        if rule.kind in {'no_leverage', 'kr_listed_etf_only', 'some_bonds', 'dividend'} or rule.kind == 'max_positions' and getattr(request, 'constraints', None):
            checks.append({'constraint': number, 'kind': rule.kind, 'source': rule.source, 'status': chip_status(rule, weights, request, catalog, rows)})
            continue
        if rule.kind == 'unverified':
            # Preserve the condition for model judgment without certifying it in code.
            checks.append({'constraint': number, 'kind': rule.kind, 'source': rule.source, 'status': 'model_review'})
            continue
        actual = sum((weights.get(key, Decimal(0)) for key in set(rule.instrument_ids)), Decimal(0))
        passed = None
        if rule.kind == 'min_weight':
            passed = all(weights.get(key, 0) >= rule.value for key in rule.instrument_ids) if rule.scope == 'each' else actual >= rule.value
        elif rule.kind == 'max_weight':
            passed = all(weights.get(key, 0) <= rule.value for key in rule.instrument_ids) if rule.scope == 'each' else actual <= rule.value
        elif rule.kind == 'exclude':
            passed = actual == 0
        elif rule.kind == 'require':
            passed = all(weights.get(key, 0) > 0 for key in rule.instrument_ids)
        elif rule.kind == 'max_positions':
            passed = sum(value > 0 for value in weights.values()) <= rule.value
        elif rule.kind == 'markets':
            markets = [catalog.get(key, {}).get('market') for key, weight in weights.items() if weight > 0 and key != 'CASH:KRW']
            passed = all(market in rule.markets for market in markets) if all(markets) else None
        elif rule.kind == 'accounts':
            passed = all(row['account'] in rule.accounts for row in rows) if all('account' in row for row in rows) else None
        elif rule.kind == 'no_sell':
            if rows and all('account' in row for row in rows) and request.input_mode == 'amount':
                plan = plan_execution(request, rows, decision)
                protected = [h for h in request.holdings if h.kind != 'cash' and (not rule.instrument_ids or h.instrument_id in rule.instrument_ids)]
                if not protected:
                    passed = True
                elif plan['rows']:
                    passed = all(Decimal(row['sell_krw']) == 0 for row in plan['rows'] if not rule.instrument_ids or row['instrument_id'] in rule.instrument_ids)
                else:
                    passed = False if plan['status'] == 'infeasible' else None
                checks.append({'constraint': number, 'kind': rule.kind, 'source': rule.source,
                               'status': 'unknown' if passed is None else 'pass' if passed else 'fail'})
                continue
            total = sum(h.amount_krw or 0 for h in request.holdings)
            if request.input_mode != 'names' and total and all(h.instrument_id for h in request.holdings):
                current = {}
                account_weights = {}
                account_specific = bool(rows) and all('account' in row for row in rows)
                for row in rows:
                    key = (row['instrument_id'], row.get('account'))
                    account_weights[key] = account_weights.get(key, Decimal(0)) + Decimal(str(row['weight_pct']))
                for holding in request.holdings:
                    key = (holding.instrument_id, holding.account) if account_specific else holding.instrument_id
                    current[key] = current.get(key, Decimal(0)) + holding.amount_krw
                # A funding quote needs a separately verified execution plan before relaxing this check.
                targets = account_weights if account_specific else weights
                passed = all(targets.get(key, 0) * total >= amount * 100 for key, amount in current.items()
                             if (key[0] if account_specific else key) != 'CASH:KRW'
                             and (not rule.instrument_ids or (key[0] if account_specific else key) in rule.instrument_ids))
            elif not request.holdings:
                passed = True
        checks.append({'constraint': number, 'kind': rule.kind, 'source': rule.source,
                       'status': 'unknown' if passed is None else 'pass' if passed else 'fail'})
    return checks
