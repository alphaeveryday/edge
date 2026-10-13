"""Conditional compound-growth arithmetic; these are assumptions, not forecasts."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re
from typing import Annotated

from pydantic import Field

from .contracts import StrictModel
from .execution import parse_funding, projection_question, NO_CONTRIBUTION

PRINCIPAL_DECLARATION = re.compile(r'^\s*(?:제\s*)?(?:원금|현재\s*평가금액|현재\s*자산|보유금액)(?:은|는|이|가|\s+|(?=\d))')
PRINCIPAL_AMOUNT = re.compile(r'(?:\d[\d,.\s천백십만억]*|[일이삼사오육칠팔구십백천만억]+)\s*(?:원(?!금)|달러|USD|KRW|유로|엔|위안|불)|[$₩€£¥]\s*\d')


def principal_declaration(clause):
    if any(word in clause for word in ('정정', '취소', '철회')):
        return True  # An unlabeled correction must not reuse a superseded amount.
    if not re.search(r'원금|현재\s*평가금액|현재\s*자산|보유금액', clause):
        return False
    horizon = re.search(r'\d+(?:\.\d+)?\s*년\s*(?:뒤|후)', clause)
    if horizon and not PRINCIPAL_AMOUNT.search(clause[:horizon.start()]) and re.search(r'(?:되나요|될까요)\s*$', clause):
        return False  # A future target question does not replace today's principal.
    return bool(PRINCIPAL_AMOUNT.search(clause) or PRINCIPAL_DECLARATION.match(clause) and not re.search(r'\d+(?:\.\d+)?\s*년', clause))


def principal_statements(text):
    """Parse only fully understood amount declarations and accompanying clauses."""
    if any(word in text for word in ('취소', '철회', '미정', '아니', '정도', '대략', '정정')):
        return None
    values = []
    statements = 0
    clauses = re.split(r'((?<!\d)\.|\.(?!\d)|[!?;\n])', text)
    for index in range(0, len(clauses), 2):
        clause = clauses[index]
        if re.search(r'원금|현재\s*평가금액|현재\s*자산|보유금액', clause) and re.search(r'모르|모릅|몰라|불명|확인\s*필요', clause):
            return None
        if principal_declaration(clause):
            statements += 1
            if index + 1 < len(clauses) and clauses[index + 1] == '?':
                return None
        match = re.fullmatch(r'\s*(?:제\s*)?(?:원금|현재\s*평가금액|현재\s*자산|보유금액)(?:은|는|이|가)?\s*([0-9][0-9,.\s억만]*원)\s*(?:입니다|이에요|예요|이야|임)?\s*', clause)
        if match:
            funding = parse_funding('추가 ' + match[1])
            if funding:
                values.append(funding['additional_krw'])
            elif re.fullmatch(r'0\s*원', match[1]):
                values.append(Decimal(0))
        elif clause.strip() and not (projection_question(clause) or NO_CONTRIBUTION.fullmatch(clause) or re.fullmatch(r'\s*매도\s*금지\s*', clause)):
            commitment = re.fullmatch(r'\s*(.+?원(?:씩)?)\s*(?:(?:을|를)?\s*(?:넣|납입|투자|입금|추가)(?:을게요|할게요|합니다|해요|하겠습니다|하겠어요|겠습니다|습니다|어요))?\s*', clause)
            if not commitment or parse_funding(commitment[1]) is None:
                return None  # Unrecognized clauses may qualify or replace the amount.
    return values if len(values) == statements else None


def stated_principal(text):
    values = principal_statements(text)
    return values[0] if values is not None and len(values) == 1 else None


def principal_context_matches(text, principal):
    values = principal_statements(text)
    return values is not None and len(values) <= 1 and all(value == principal for value in values)


class ScenarioInput(StrictModel):
    annual_returns: list[Annotated[Decimal, Field(ge=-100, le=100, allow_inf_nan=False)]] = Field(default_factory=lambda: [Decimal(-3), Decimal(0), Decimal(3), Decimal(6)], min_length=1, max_length=7)
    years: list[Annotated[int, Field(ge=1, le=50, strict=True)]] = Field(default_factory=lambda: [5, 10, 15], min_length=1, max_length=5)


TOOL = {'type': 'function', 'function': {'name': 'calculate_scenarios',
    'description': '확인된 원금·납입금으로 명시적 연수익률 가정과 기간별 자산을 계산한다. 기본 -3/0/3/6%, 5/10/15년은 비교 가정이며 예측이 아니다. 원금을 만들어내지 않는다.',
    'parameters': ScenarioInput.model_json_schema()}}


def build_scenarios(principal_krw, args, *, monthly_krw=0, additional_krw=0):
    assumptions = ScenarioInput.model_validate(args)
    if principal_krw is None:
        return {'status': 'needs_input', 'reason': 'CURRENT_AMOUNTS_REQUIRED', 'rows': []}
    principal, monthly, additional = [Decimal(str(value)) for value in (principal_krw, monthly_krw, additional_krw)]
    if any(not value.is_finite() or value < 0 or value != value.to_integral_value() for value in (principal, monthly, additional)):
        raise ValueError('INVALID_SCENARIO_PRINCIPAL')
    if principal > 20 * 10**13 or monthly > 10**13 or additional > 10**13:
        raise ValueError('SCENARIO_AMOUNT_LIMIT')
    principal += additional
    rows = []
    with localcontext() as context:
        context.prec = 80
        for rate in dict.fromkeys(assumptions.annual_returns):
            yearly = 1 + rate / 100
            monthly_rate = yearly ** (Decimal(1) / 12) - 1 if yearly else Decimal(-1)
            for years in dict.fromkeys(assumptions.years):
                factor = yearly ** years
                deposits = monthly * 12 * years if monthly_rate == 0 else monthly * (factor - 1) / monthly_rate
                value = (principal * factor + deposits).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                rows.append({'annual_return_pct': str(rate), 'years': years, 'future_krw': str(value),
                             'paid_krw': str(int(principal + monthly * years * 12))})
    return {'status': 'available', 'principal_krw': str(int(principal)), 'monthly_krw': str(int(monthly)), 'rows': rows,
            'assumptions': ['연수익률이 매년 같고 월말에 같은 금액을 납입하는 가정이에요.',
                            '세금·수수료·물가 상승은 제외했으며 미래 수익을 예측하거나 보장하지 않아요.']}
