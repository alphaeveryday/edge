"""Extract intent with the model; enforce allocation constraints in code."""
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Section, StrictModel


class Constraint(StrictModel):
    kind: Literal['min_weight', 'max_weight', 'exclude', 'require', 'max_positions', 'accounts', 'markets', 'no_sell', 'unverified']
    source: str = Field(min_length=1, max_length=1000)
    instrument_ids: list[str] = Field(default_factory=list, max_length=20)
    accounts: list[Literal['일반 계좌', 'ISA', '연금저축', 'IRP']] = Field(default_factory=list, max_length=4)
    markets: list[Literal['KR', 'US']] = Field(default_factory=list, max_length=2)
    value: Decimal | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode='after')
    def sufficient_arguments(self):
        if self.kind in {'min_weight', 'max_weight', 'exclude', 'require'} and not self.instrument_ids:
            raise ValueError('Constraint needs registered instrument IDs; use unverified for unknown exposure')
        if self.kind in {'min_weight', 'max_weight', 'max_positions'} and self.value is None:
            raise ValueError('Constraint needs a numeric threshold')
        if self.kind == 'max_positions' and (self.value < 1 or self.value != self.value.to_integral_value()):
            raise ValueError('Position count must be a positive integer')
        if self.kind == 'accounts' and not self.accounts or self.kind == 'markets' and not self.markets:
            raise ValueError('Constraint needs allowed accounts or markets')
        return self


class Decision(StrictModel):
    kind: Literal['review', 'rebalance', 'new_money', 'new_portfolio', 'scenario'] = 'review'
    objectives: list[Literal['growth', 'stability', 'income', 'preserve', 'simplicity']] = Field(default_factory=list, max_length=5)
    constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    missing_inputs: list[str] = Field(default_factory=list, max_length=10)
    funding_text: str = Field(default='', max_length=100)


class QuestionSection(Section):
    decision: Decision


def validate_decision(decision, request, catalog):
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
    for rule in decision.constraints:
        if rule.source not in text:
            raise ValueError('CUSTOMER_CONSTRAINT_SOURCE: quote the exact customer input')
        if any(key != 'CASH:KRW' and key not in catalog for key in rule.instrument_ids):
            raise ValueError('UNREGISTERED_CONSTRAINT_INSTRUMENT: use unverified until resolved')
    if decision.funding_text and decision.funding_text not in text:
        raise ValueError('CUSTOMER_FUNDING_SOURCE: quote explicit additional money, not current holdings')
    if len(set(decision.objectives)) != len(decision.objectives):
        raise ValueError('Objective priorities must be distinct')
    if decision.kind == 'new_portfolio' and any(h.kind != 'cash' for h in request.holdings):
        raise ValueError('Existing holdings cannot be replaced with a cash baseline')
    return decision


def check_constraints(decision, rows, request, catalog):
    weights = {}
    for row in rows:
        key = row['instrument_id']
        weights[key] = weights.get(key, Decimal(0)) + Decimal(str(row['weight_pct']))
    checks = []
    for number, rule in enumerate(decision.constraints, 1):
        actual = sum((weights.get(key, Decimal(0)) for key in set(rule.instrument_ids)), Decimal(0))
        passed = None
        if rule.kind == 'min_weight':
            passed = actual >= rule.value
        elif rule.kind == 'max_weight':
            passed = actual <= rule.value
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
