import json

import pytest

from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput
from report_fixtures import final_payload
from test_agent import Evidence


@pytest.mark.parametrize('research_missing', [False, True])
async def test_customer_input_wait_does_not_stop_independent_research(monkeypatch, research_missing):
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first', 'alternative'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, opinions, events = [], [], []
    async def model(payload):
        role = stages[-1]
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content['decision'] = {}
        if role == 'opinion':
            opinions.append(1)
            content['answers'] = [{'question_index': 1, 'status': 'needs_input',
                'answer': '월 납입액은 목표 금액을 확인하면 계산할 수 있어요.', 'missing': ['목표 금액']}]
            if research_missing and len(opinions) == 1:
                content['withheld_reason'] = 'missing_data'
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상', question='매달 얼마를 넣어야 하나요?')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence(), on_event=events.append)
    assert len(opinions) == (2 if research_missing else 1)
    assert result['completion']['status'] == 'needs_input'
    assert result['completion']['tasks'][0]['missing'] == ['목표 금액']
    assert sum(event['event'] == 'research_recovery' for event in events) == int(research_missing)


async def test_missing_weights_alone_does_not_trigger_unproductive_research(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    today = datetime.now(timezone.utc).date().isoformat()
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'unavailable', 'candidates': [], 'reason': 'CURRENT_WEIGHTS_REQUIRED'}
            self.batches = {'first'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, opinions = [], []
    async def model(payload):
        role = stages[-1]
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question': content['decision'] = {}
        if role == 'opinion':
            opinions.append(1)
            content['evidence_coverage'] = [{'instrument_id': 'US:VOO', 'field': field, 'status': 'supported',
                'claim_as_of': today, 'evidence': [{'source_id': 's1', 'passage_id': 'p1'}]} for field in ('structure', 'holdings', 'fees')]
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(input_mode='names', holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO'}], horizon='10년 이상')
    evidence = SimpleNamespace(sources=[{'id': 's1', 'passages': [{'id': 'p1', 'text': today + ' Official product research.'}]}])
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=evidence)
    assert len(opinions) == 1
    assert result['completion']['status'] == 'needs_input'
