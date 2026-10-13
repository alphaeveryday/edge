"""Conditional compound-growth arithmetic; these are assumptions, not forecasts."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Annotated

from pydantic import Field

from .contracts import StrictModel


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
                             'paid_krw': str(principal + monthly * years * 12)})
    return {'status': 'available', 'principal_krw': str(principal), 'monthly_krw': str(monthly), 'rows': rows,
            'assumptions': ['연수익률이 매년 같고 월말에 같은 금액을 납입하는 가정이에요.',
                            '세금·수수료·물가 상승은 제외했으며 미래 수익을 예측하거나 보장하지 않아요.']}
