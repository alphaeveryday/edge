from edge_portfolio.completion import assess_completion, retain_research
from edge_portfolio.contracts import AnalysisInput, FinalReport
from edge_portfolio.decision import Decision
from report_fixtures import final_payload


def test_holdings_and_candidates_remain_visible_when_no_recommendation_is_published():
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}], horizon='10년 이상')
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None})
    published = {'recommendation': None, 'optimization': {'status': 'available',
        'candidates': [{'allocations': [{'instrument_id': 'US:SPY'}]}]}}
    result = assess_completion(final, request, Decision(), published, [], '2026-10-14', considered_ids=['US:SPY', 'US:BND'])
    assert {item['instrument_id'] for item in result['missing_research']} == {'US:VOO', 'US:SPY'}
    rejected = [item for item in result['research_inventory'] if item['instrument_id'] == 'US:BND']
    assert len(rejected) == 3
    assert all(item['status'] == 'missing' and not item['required'] for item in rejected)
    assert result['status'] == 'partial'


def test_unselected_candidates_do_not_block_the_new_recommendation():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    final = FinalReport.model_validate(final_payload())
    result = assess_completion(final, request, Decision(objectives=['income']),
        {'recommendation': {'allocations': [{'instrument_id': 'US:VOO'}]}}, [], '2026-10-14', considered_ids=['US:VOO', 'US:BND'])
    assert {item['instrument_id'] for item in result['missing_research']} == {'US:VOO'}
    assert {item['field'] for item in result['missing_research']} == {'structure', 'holdings', 'fees', 'distributions'}
    assert any(item['instrument_id'] == 'US:BND' for item in result['research_inventory'])


def test_price_search_revision_does_not_erase_independent_product_research():
    previous = FinalReport.model_validate({**final_payload(), 'evidence_coverage': [
        {'instrument_id': 'US:VOO', 'field': 'fees', 'status': 'missing'}], 'answers': [
        {'question_index': 1, 'status': 'answered', 'answer': 'The earlier allocation answer.'}]})
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None})
    result = retain_research(final, previous, retain_answers=False)
    assert result.evidence_coverage == previous.evidence_coverage
    assert result.answers == []


def test_a_calculation_only_question_does_not_require_product_research():
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}], horizon='10년 이상')
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None})
    result = assess_completion(final, request, Decision(kind='scenario'),
        {'scenarios': {'status': 'available'}}, [], '2026-10-14')
    assert result['missing_research'] == []


async def test_the_first_checkpoint_registers_holdings_even_if_the_model_fails():
    import pytest
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    checkpoints = []
    async def model(_): raise RuntimeError('model unavailable')
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}], horizon='10년 이상')
    with pytest.raises(RuntimeError, match='model unavailable'):
        await run_analysis(request, 'test', lambda _: None, model_call=model, evidence=Evidence(), on_checkpoint=checkpoints.append)
    assert len(checkpoints[0]['research_inventory']) == 3
    assert all(row['instrument_id'] == 'US:VOO' and row['status'] == 'missing' for row in checkpoints[0]['research_inventory'])


def test_retained_evidence_can_cover_twenty_holdings_and_forty_searched_etfs():
    entries = [{'instrument_id': f'US:ETF{index}', 'field': field, 'status': 'missing'}
        for index in range(60) for field in ('structure', 'holdings', 'fees', 'distributions')]
    previous = FinalReport.model_validate({**final_payload(), 'evidence_coverage': entries[:160]})
    final = FinalReport.model_validate({**final_payload(), 'evidence_coverage': entries[160:]})
    assert len(retain_research(final, previous).evidence_coverage) == 240
