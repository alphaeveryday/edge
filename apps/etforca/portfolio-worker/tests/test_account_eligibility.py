import pytest
from pydantic import ValidationError

from edge_portfolio.contracts import TaxProfile


def profile(**changes):
    return TaxProfile.model_validate(dict(residence='대한민국 거주자', age='19~54세', income='소득 없음',
                                          financial_income_tax='아니요', accounts=[], **changes))


@pytest.mark.parametrize('change', [dict(residence='비거주자'), dict(age='15세 미만'),
                                  dict(financial_income_tax='예'),
                                  dict(age='15~18세', previous_year_earned_income='아니요')])
def test_api_cannot_bypass_disabled_isa_target(change):
    data = profile().model_dump() | change | {'recommended_accounts': ['ISA']}
    with pytest.raises(ValidationError, match='ISA'):
        TaxProfile.model_validate(data)


def test_no_income_does_not_disqualify_pension_savings_or_retirement_benefit_irp():
    assert profile(recommended_accounts=['연금저축']).recommended_accounts == ['연금저축']
    assert profile(recommended_accounts=['IRP'], irp_qualification='퇴직급여 수령 대상')
    with pytest.raises(ValidationError, match='IRP'):
        profile(recommended_accounts=['IRP'], irp_qualification='해당 없음')


def test_unknown_or_legacy_qualification_is_not_invented_as_ineligible():
    assert profile(recommended_accounts=['ISA', 'IRP'])
    assert profile(recommended_accounts=['IRP'], irp_qualification='확인 필요')
