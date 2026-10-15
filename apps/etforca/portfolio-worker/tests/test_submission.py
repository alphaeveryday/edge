"""Reject missing calculation inputs before consuming a customer's analysis attempt."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from edge_portfolio.api import submit
from edge_portfolio.contracts import AnalysisInput


@pytest.mark.parametrize('changes,code', [
    ({'input_mode': 'names', 'holdings': [{'name': 'VOO'}]}, 'HOLDING_WEIGHTS_REQUIRED'),
    ({'preferences': {'interests': ['성장']}}, 'CONTRIBUTION_CHOICE_REQUIRED'),
    ({'preferences': {'interests': ['성장'], 'contribution': '매달 꾸준히'}}, 'MONTHLY_AMOUNT_REQUIRED'),
    ({'preferences': {'interests': ['성장'], 'contribution': '  매달 꾸준히 '}}, 'MONTHLY_AMOUNT_REQUIRED'),
    ({'preferences': {'interests': ['성장'], 'contribution': '미정'}}, 'CONTRIBUTION_CHOICE_REQUIRED'),
])
def test_missing_inputs_are_rejected_before_queuing(monkeypatch, changes, code):
    queued = Mock()
    monkeypatch.setattr('edge_portfolio.api.store.submit', queued)
    body = AnalysisInput.model_validate({'holdings': [{'name': 'VOO', 'amount_krw': 1000000}],
        'horizon': '10년 이상', 'preferences': {'contribution': '이미 다 매수한 상태에요', 'interests': ['성장']}, **changes})
    with pytest.raises(HTTPException) as error:
        submit(body, Mock())
    assert error.value.status_code == 422
    assert error.value.detail == code
    queued.assert_not_called()


def test_unselected_objective_is_queued_without_inventing_a_customer_preference(monkeypatch):
    queued = Mock(return_value={'id': 'test-job'})
    monkeypatch.setattr('edge_portfolio.api.store.submit', queued)
    monkeypatch.setattr('edge_portfolio.api.canonicalize', lambda body: body)
    monkeypatch.setattr('edge_portfolio.api.owner', lambda request: 'test-owner')
    monkeypatch.setattr('edge_portfolio.api.public', lambda row: row)
    body = AnalysisInput.model_validate({'holdings': [{'name': 'VOO', 'amount_krw': 1000000}],
        'horizon': '10년 이상', 'preferences': {'contribution': '이미 다 매수한 상태에요'}})
    assert submit(body, Mock(), 'test-key') == {'id': 'test-job'}
    assert queued.call_args.args[2]['preferences']['interests'] == []
