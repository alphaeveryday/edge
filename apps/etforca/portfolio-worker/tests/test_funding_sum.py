import pytest

from edge_portfolio.decision import Decision, Funding
from edge_portfolio.execution import resolve_funding, validate_funding


SOURCE = 'isa 매달 20만원, irp 매달 10만원, 연금저축 매달20만원, 적금 매달 10만원, 여유금 매달 10만원, 금 매달 1g'


def test_separate_account_contributions_are_verified_as_a_sum_not_an_estimate():
    funding = Funding(source=SOURCE, monthly_krw=500000,
                      monthly_components_krw=[200000, 100000, 200000])
    validate_funding(funding, SOURCE)
    assert resolve_funding(None, Decision(funding=funding))['monthly_krw'] == 500000
    assert funding.estimated_fields == []
    assert Funding.model_validate(funding.model_dump()) == funding


@pytest.mark.parametrize('components,monthly', [
    ([200000, 100000, 200000], 700000),  # Unrelated savings are not silently added.
    ([2000000, 100000, 200000], 2300000),  # A unit error cannot be accepted as a sum.
    ([200000, 100000], None),
])
def test_invalid_derived_funding_does_not_become_executable_money(components, monthly):
    funding = Funding(source=SOURCE, monthly_krw=monthly, monthly_components_krw=components)
    with pytest.raises(ValueError, match='CUSTOMER_FUNDING_AMOUNT'):
        validate_funding(funding, SOURCE)


def test_old_reports_and_direct_amounts_do_not_require_component_metadata():
    funding = Funding.model_validate({'source': '매월 50만원', 'monthly_krw': 500000})
    validate_funding(funding, funding.source)
    assert funding.monthly_components_krw == []


@pytest.mark.parametrize('source', ['ISA와 IRP에 각각 월 20만원씩', 'ISA와 IRP에 각각 월 20만원씩, 총 월 40만원'])
def test_one_quoted_amount_can_describe_multiple_account_contributions(source):
    funding = Funding(source=source, monthly_krw=400000, monthly_components_krw=[200000, 200000])
    validate_funding(funding, source)
    assert resolve_funding(None, Decision(funding=funding))['monthly_krw'] == 400000
