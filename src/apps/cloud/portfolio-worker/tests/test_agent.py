import json
import pytest
from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput, ROLES, LIMITS


class Evidence:
    sources = [{"id": "s1", "excerpt": "official source quotation"}]


async def test_one_conversation_visits_four_roles_and_keeps_context():
    phases, conversations = [], []
    async def model(payload):
        conversations.append(len(payload["messages"]))
        role = phases[-1]
        count = LIMITS[role][0] // 2
        section = {"paragraphs": ["가" * count, "나" * count], "evidence": [{"source_id": "s1", "quote": "official source quotation"}], "limitations": []}
        return {"choices": [{"message": {"role": "assistant", "content": json.dumps(section), "reasoning_content": "private"}}]}
    result = await run_analysis(AnalysisInput(holdings=[{"name": "VOO", "amount_krw": 100}], horizon="10년 이상"), "test", phases.append, model_call=model, evidence=Evidence())
    assert phases == list(ROLES)
    assert conversations == sorted(set(conversations))
    assert list(result["sections"]) == list(ROLES)
    assert "private" not in json.dumps(result)


async def test_invalid_output_stops_after_one_repair():
    calls = []
    async def model(payload):
        calls.append(1)
        return {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}
    with pytest.raises(ValueError, match="OUTPUT_INVALID"):
        await run_analysis(AnalysisInput(holdings=[{"name": "VOO", "amount_krw": 100}], horizon="10년 이상"), "test", lambda _: None, model_call=model, evidence=Evidence())
    assert len(calls) == 2
