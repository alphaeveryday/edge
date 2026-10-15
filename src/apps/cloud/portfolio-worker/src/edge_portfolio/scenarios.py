"""Conditional compound-growth arithmetic; these are assumptions, not forecasts."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re
from typing import Annotated

from pydantic import Field

from .contracts import StrictModel
from .decision import Funding
from .execution import parse_funding, projection_question, NO_CONTRIBUTION, FUNDING_CALCULATION_SUFFIX

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
            commitment = re.fullmatch(r'\s*(.+?원(?:씩)?)\s*(?:(?:을|를)?\s*(?:넣|납입|투자|입금|추가)(?:을게요|할게요|합니다|해요|하겠습니다|하겠어요|겠습니다|습니다|어요)|' + FUNDING_CALCULATION_SUFFIX + r')?\s*', clause)
            if not commitment or parse_funding(commitment[1]) is None:
                return None  # Unrecognized clauses may qualify or replace the amount.
    return values if len(values) == statements else None


def stated_principal(text):
    values = principal_statements(text)
    return values[0] if values is not None and len(values) == 1 else None


def principal_context_matches(text, principal):
    values = principal_statements(text)
    return values is not None and len(values) <= 1 and all(value == principal for value in values)


def principal_preference_matches(text, principal):
    # InputForm's fixed choices describe funding mode/interests, not amount qualifications.
    form_values = {'이미 다 매수한 상태에요', '매달 꾸준히', '목돈 한 번에', '아직 고민 중',
        '지수 따라가기', '배당', '성장', '안정 (채권·금)', '반도체', 'AI·빅테크', '방산', '로봇',
        '원자력', '전력 인프라', '바이오·헬스케어', '소비재', '금융', '리츠·부동산', '친환경 에너지',
        '한국 주식', '미국 주식', '선진국', '신흥국', '단기채', '장기채', '금·원자재'}
    return text in form_values or principal_context_matches(text, principal)


class ScenarioInput(StrictModel):
    annual_returns: list[Annotated[Decimal, Field(ge=-100, le=100, allow_inf_nan=False)]] = Field(default_factory=lambda: [Decimal(-3), Decimal(0), Decimal(3), Decimal(6)], min_length=1, max_length=7)
    years: list[Annotated[int, Field(ge=1, le=50, strict=True)]] = Field(default_factory=lambda: [5, 10, 15], min_length=1, max_length=5)


class ScenarioToolInput(ScenarioInput):
    funding: Funding | None = Field(default=None, description='이번 질문의 원문·원금·납입 조건으로만 계산한다. 생략하면 기존 자금 조건을 사용한다. 이전 매수 계획과 별도이며 추가 입금을 원금에 중복 합산하지 않는다.')


TOOL = {'type': 'function', 'function': {'name': 'calculate_scenarios',
    'description': '고객이 이번에 지정한 원금·납입금은 funding에 직접 넣어 계산한다. 이전 매수 계획의 추가 자금을 자동으로 포함하지 않는다. 생략 시 기존 조건을 사용한다. 기본 -3/0/3/6%, 5/10/15년은 비교 가정이며 예측이 아니다.',
    'parameters': ScenarioToolInput.model_json_schema()}}


def build_scenarios(principal_krw, args, *, monthly_krw=0, additional_krw=0, contribution_months=None, start_delay_months=0):
    assumptions = ScenarioInput.model_validate(args)
    if principal_krw is None:
        return {'status': 'needs_input', 'reason': 'CURRENT_AMOUNTS_REQUIRED', 'rows': []}
    principal, monthly, additional = [Decimal(str(value)) for value in (principal_krw, monthly_krw, additional_krw)]
    if any(not value.is_finite() or value < 0 or value != value.to_integral_value() for value in (principal, monthly, additional)):
        raise ValueError('INVALID_SCENARIO_PRINCIPAL')
    if principal > 20 * 10**13 or monthly > 10**13 or additional > 10**13:
        raise ValueError('SCENARIO_AMOUNT_LIMIT')
    if contribution_months is not None and (type(contribution_months) is not int or not 1 <= contribution_months <= 600):
        raise ValueError('INVALID_CONTRIBUTION_MONTHS')
    if type(start_delay_months) is not int or not 0 <= start_delay_months <= 600:
        raise ValueError('INVALID_CONTRIBUTION_START')
    principal += additional
    rows = []
    with localcontext() as context:
        context.prec = 80
        for rate in dict.fromkeys(assumptions.annual_returns):
            yearly = 1 + rate / 100
            monthly_rate = yearly ** (Decimal(1) / 12) - 1 if yearly else Decimal(-1)
            for years in dict.fromkeys(assumptions.years):
                factor = yearly ** years
                available_months = max(0, years * 12 - start_delay_months)
                months = min(available_months, contribution_months) if contribution_months is not None else available_months
                remainder = max(0, years * 12 - start_delay_months - months)
                deposits = monthly * months if monthly_rate == 0 or months == 0 else monthly * ((1 + monthly_rate) ** months - 1) / monthly_rate
                deposits *= (1 + monthly_rate) ** remainder if remainder else 1
                value = (principal * factor + deposits).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                rows.append({'annual_return_pct': str(rate), 'years': years, 'future_krw': str(value),
                             'paid_krw': str(int(principal + monthly * months))})
    return {'status': 'available', 'principal_krw': str(int(principal)), 'monthly_krw': str(int(monthly)), 'rows': rows,
            'assumptions': ['연수익률이 매년 같고 월말에 같은 금액을 납입하는 가정이에요.',
                            '세금·수수료·물가 상승은 제외했으며 미래 수익을 예측하거나 보장하지 않아요.',
                            *([f'월 납입은 {contribution_months}개월 동안만 반영했어요.'] if contribution_months is not None else []),
                            *([f'월 납입은 {start_delay_months}개월 뒤부터 시작한다고 가정했어요.'] if start_delay_months else [])]}
