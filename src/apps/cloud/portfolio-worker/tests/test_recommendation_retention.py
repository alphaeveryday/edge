import pytest

from edge_portfolio.completion import retain_recommendation
from edge_portfolio.contracts import FinalReport
from edge_portfolio.optimizer import Optimizer
from report_fixtures import final_payload


@pytest.mark.parametrize('reason', ['missing_data', 'missing_input'])
def test_optional_gaps_cannot_erase_an_already_qualified_recommendation(reason):
    previous = FinalReport.model_validate({**final_payload(), 'selected_candidate_id': 'candidate-1'})
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None, 'withheld_reason': reason,
        'evidence_coverage': [{'instrument_id': 'US:VOO', 'field': 'distributions', 'status': 'missing'}]})
    result = retain_recommendation(final, previous)
    assert result.recommendation == previous.recommendation
    assert result.selected_candidate_id == previous.selected_candidate_id
    assert result.withheld_reason is None
    assert result.evidence_coverage == final.evidence_coverage
    assert result.return_forecast is None


@pytest.mark.parametrize('reason', ['quality_failed', 'customer_failed', 'search_exhausted'])
def test_new_disqualifying_findings_are_not_overridden_by_an_earlier_recommendation(reason):
    previous = FinalReport.model_validate(final_payload())
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None, 'withheld_reason': reason})
    assert retain_recommendation(final, previous).recommendation is None


def test_missing_all_judgment_evidence_can_still_publish_a_truthful_partial_report():
    optimizer = Optimizer(None)
    optimizer.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None, 'withheld_reason': 'missing_data'})
    assert optimizer.validate(final) is None
    optimizer.result = {'status': 'no_match', 'candidates': []}
    assert optimizer.validate(final) is None


def test_resolved_or_newly_blocked_answers_survive_recommendation_retention():
    previous = FinalReport.model_validate({**final_payload(), 'answers': [
        {'question_index': 1, 'status': 'partial', 'answer': '배당 자료를 더 확인하면 답할 수 있습니다.', 'missing': ['배당 자료']}]})
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None, 'withheld_reason': 'missing_data', 'answers': [
        {'question_index': 1, 'status': 'answered', 'answer': '확인한 지급 내역을 기준으로 배당 정보를 안내합니다.'},
        {'question_index': 2, 'status': 'needs_input', 'answer': '이 계산은 납입할 금액을 알려주시면 완성할 수 있습니다.', 'missing': ['납입 금액']}]})
    result = retain_recommendation(final, previous)
    assert result.answers == final.answers
    assert result.opinion == final.opinion
    assert result.insights == final.insights


def test_a_forecast_for_another_candidate_is_not_transferred_to_the_retained_candidate():
    previous = FinalReport.model_validate({**final_payload(), 'selected_candidate_id': 'candidate-1'})
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None, 'withheld_reason': 'missing_data',
        'selected_candidate_id': 'candidate-2', 'return_forecast': {'percent': 10,
            'reason': '다른 후보에 관한 시장 전망과 그 구성의 과거 실적을 바탕으로 추정한 조건부 수익 전망입니다.',
            'evidence': [{'source_id': 's1', 'passage_id': 'p1'}]}})
    result = retain_recommendation(final, previous)
    assert result.selected_candidate_id == 'candidate-1'
    assert result.return_forecast is None


@pytest.mark.parametrize('revise_search,accept_rewrite,needs_input', [(False, True, False), (False, False, False), (True, False, False), (False, False, True)])
@pytest.mark.parametrize('last_call', [False, 'resolved'])
async def test_recovery_keeps_a_qualified_recommendation_only_for_the_same_search(monkeypatch, revise_search, accept_rewrite, needs_input, last_call):
    import json
    from edge_portfolio.agent import run_analysis
    from edge_portfolio.contracts import AnalysisInput
    from test_agent import Evidence
    instances = []
    class CandidateSearch:
        def __init__(self, *args):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1', 'allocations': [{'instrument_id': 'US:VOO', 'weight_pct': 100}]}]}
            self.batches = {'first', 'alternative'}
            instances.append(self)
        def validate(self, final):
            return self.result['candidates'][0] if final.recommendation else None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', CandidateSearch)
    async def enrich(*args): pass
    monkeypatch.setattr('edge_portfolio.agent.enrich_report', enrich)
    if last_call == 'resolved':
        from edge_portfolio.completion import assess_completion
        assessments = []
        def resolved(*args, **kwargs):
            result = assess_completion(*args, **kwargs)
            assessments.append(1)
            if len(assessments) > 1:
                result.update(missing_research=[], reasons=[])
            return result
        monkeypatch.setattr('edge_portfolio.agent.assess_completion', resolved)
    stages, opinions = [], []
    async def model(payload):
        role = stages[-1]
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question': content['decision'] = {}
        if role == 'opinion':
            opinions.append(1)
            if last_call == 'resolved':
                assert len(opinions) <= 3, 'Resolved research must not be restarted by a text-retention marker'
            if needs_input and len(opinions) == 2:
                content['answers'] = [{'question_index': 1, 'status': 'needs_input', 'answer': '납입 금액을 알려주시면 실제 매수 금액도 계산할 수 있습니다.', 'missing': ['납입 금액']}]
            if len(opinions) == 1 or accept_rewrite and len(opinions) > 2:
                content['selected_candidate_id'] = 'candidate-1'
            else:
                if revise_search: instances[0].result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
                content.update(recommendation=None, withheld_reason='missing_data')
                content['opinion']['paragraphs'] = ['배당 자료가 없어 현재 추천을 보류하며 해당 자료를 확인한 뒤 최종 의견을 다시 제시합니다.'] * 3
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}], horizon='10년 이상',
                            question='추가 납입 시 실제 매수 금액도 계산해 주세요.' if needs_input else '')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
    assert len(opinions) > 1
    assert (result['recommendation'] is not None) == (not revise_search)
    if not revise_search:
        assert result['recommendation']['dividend_yield_1y'] is None
        assert bool(result['completion']['missing_research']) == (last_call != 'resolved')
        assert '보류' not in ''.join(result['sections']['opinion']['paragraphs'])
        assert ('RECOMMENDATION_TEXT_RETAINED' in result['completion']['reasons']) == (not accept_rewrite)
        if needs_input:
            assert result['completion']['status'] == 'needs_input'


@pytest.mark.parametrize('exhaustion', ['context', 'earlier_role', 'final_tools', 'prerequisite'])
async def test_research_limits_leave_a_valid_recommendation_publishable(monkeypatch, exhaustion):
    import json
    from edge_portfolio.agent import run_analysis
    from edge_portfolio.contracts import AnalysisInput
    from test_agent import Evidence
    class CandidateSearch:
        def __init__(self, *_):
            self.result = None if exhaustion == 'prerequisite' else {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
            self.batches = {'first', 'alternative'}
        async def tool(self, _):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
            return self.result
        def validate(self, final):
            return self.result['candidates'][0] if final.recommendation else None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', CandidateSearch)
    async def enrich(*_): pass
    monkeypatch.setattr('edge_portfolio.agent.enrich_report', enrich)
    stages, calls = [], []
    async def model(payload):
        role = stages[-1]
        calls.append(role)
        if exhaustion == 'prerequisite' and role == 'portfolio' and payload['tool_choice'] == 'auto':
            names = {tool['function']['name'] for tool in payload['tools']}
            name = 'optimize_portfolios' if names == {'search_etfs', 'optimize_portfolios'} else 'search_etfs'
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': str(len(calls)), 'type': 'function', 'function': {'name': name, 'arguments': '{"query":"VOO"}'}}]}}]}
        if ((exhaustion == 'earlier_role' and role == 'outlook') or (exhaustion == 'final_tools' and role == 'opinion')) and payload['tool_choice'] == 'auto':
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': str(len(calls)), 'type': 'function', 'function': {'name': 'search_etfs', 'arguments': '{"query":"VOO"}'}}]}}]}
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question': content['decision'] = {}
        if role == 'opinion':
            content['selected_candidate_id'] = 'candidate-1'
            if exhaustion == 'context': monkeypatch.setattr('edge_portfolio.agent.MAX_CONTEXT_BYTES', 1)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}],
        horizon='10년 이상'), 'test', stages.append, model_call=model, evidence=Evidence())
    assert result['recommendation'] is not None
    assert result['completion']['status'] == 'partial'
    assert len(calls) <= 24
    if exhaustion == 'context':
        assert result['diagnostics']['termination_reason'] == 'MODEL_CONTEXT_BUDGET_WITH_REPORT'
    else:
        assert calls.index('opinion') <= 21
