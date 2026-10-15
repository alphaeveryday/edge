import pytest

from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput
from test_agent import Evidence


async def test_account_strategy_is_loaded_even_when_the_customer_left_tax_profile_blank():
    seen = []
    async def model(payload):
        seen.append(payload['messages'][0]['content'])
        raise RuntimeError('captured prompt')
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}], horizon='10년 이상')
    with pytest.raises(RuntimeError, match='captured prompt'):
        await run_analysis(request, 'test', lambda _: None, model_call=model, evidence=Evidence())
    assert 'account-tax-strategy' in seen[0]
    assert 'DB' in seen[0] and 'DC' in seen[0]
    assert '계좌별 절세 전략' in seen[0]


async def test_account_skill_changes_are_included_in_the_recorded_prompt_version(monkeypatch, tmp_path):
    import shutil
    from edge_portfolio import agent
    shutil.copytree(agent.PROMPTS, tmp_path / 'prompts')
    monkeypatch.setattr(agent, 'PROMPTS', tmp_path / 'prompts')
    versions = []
    async def model(payload): raise RuntimeError('stop before external research')
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    async def run():
        with pytest.raises(RuntimeError):
            await run_analysis(request, 'test', lambda _: None, model_call=model, evidence=Evidence(),
                on_event=lambda event: versions.append(event['prompt_version']) if event['event'] == 'run_started' else None)
    await run()
    path = agent.PROMPTS / 'account-tax' / 'SKILL.md'
    path.write_text(path.read_text(encoding='utf-8') + '\nChanged account policy.\n', encoding='utf-8')
    await run()
    assert versions[0] != versions[1]
