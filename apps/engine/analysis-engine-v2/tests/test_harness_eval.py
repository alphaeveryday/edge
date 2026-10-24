"""Fixed evaluation sources must not invent evidence at model-guessed URLs."""
from edge_analysis_v2.quality.harness_eval import scenario
import asyncio
import json
import pytest


def test_only_listed_ir_contains_financial_guidance():
    call, _, _ = scenario('available')
    guessed = call('read_web_document', {'url':'https://example.com/filing'})
    assert guessed['result']['status'] == 'unavailable'
    actual = call('read_web_document', {'url':'https://example.com/ir'})
    assert '10%' in actual['result']['text']


@pytest.mark.parametrize('direction', ['decrease', 'increase'])
def test_wrong_model_answer_fails_evaluation_and_preserves_the_failure_report(monkeypatch, tmp_path, direction):
    from edge_analysis_v2.quality import harness_eval
    async def candidate(**kwargs):
        observed = kwargs['call']('read_web_document', {'url':'https://example.com/ir'})
        (tmp_path/'workspace.json').write_text(json.dumps({'todos':[{'content':'Profit direction', 'status':'completed'}]}))
        events = [{'message_type':'AssistantMessage', 'message':{'content':[{'name':name}]}}
                  for name in ('mcp__workspace__update_tasks','mcp__analysis__read_web_document')]
        (tmp_path/'events.jsonl').write_text('\n'.join(json.dumps(event) for event in events))
        return {'profit_direction':direction, 'explanation':'Candidate conclusion',
                'evidence_ids':[observed['tool_run_id']]}
    monkeypatch.setattr(harness_eval, 'run_model', candidate)
    if direction == 'increase':
        with pytest.raises(ValueError, match='conclusion_matches_fixed_facts'):
            asyncio.run(harness_eval.evaluate(tmp_path, 'available', 'unused-key', 'fixture-model'))
    else:
        asyncio.run(harness_eval.evaluate(tmp_path, 'available', 'unused-key', 'fixture-model'))
    report = json.loads((tmp_path/'evaluation.json').read_text())
    assert report['checks']['conclusion_matches_fixed_facts'] == (direction == 'decrease')
    assert (tmp_path/'evaluation_calls.json').exists()
