import asyncio
from dataclasses import dataclass
import json
from investment_lab.agent.runner import run_model
from investment_lab.agent.investment import OUTPUT
from investment_lab.pit import SCHEMAS


@dataclass
class ResultMessage:
    subtype: str = 'success'
    is_error: bool = False
    result: str = ''
    structured_output: dict | None = None


def test_model_has_no_shell_web_filesystem_or_unregistered_tools(tmp_path):
    captured=[]
    decision=dict(action='HOLD',weight=None,reason='uncertainty',evidence_ids=[],uncertainty='no evidence',change_condition='new admitted evidence')
    class Client:
        def __init__(self,options):
            captured.append(options)
        async def __aenter__(self):
            return self
        async def __aexit__(self,*args):
            return False
        async def query(self,value):
            assert json.loads(value)['cutoff']=='2025-01-03T18:00:00+09:00'
        async def receive_response(self):
            yield ResultMessage(structured_output=decision)
    result=asyncio.run(run_model(initial={'cutoff':'2025-01-03T18:00:00+09:00'},prompt='frozen prompt',schemas=SCHEMAS,
        call=lambda *args: {'items':[]},output_schema=OUTPUT,artifacts=tmp_path,key='test-api-secret',model='test',client_factory=Client))
    options=captured[0]
    assert options.tools==[]
    assert options.allowed_tools==['mcp__analysis__search','mcp__analysis__read']
    assert options.strict_mcp_config and options.setting_sources==[]
    assert result==decision
    assert 'test-api-secret' not in ''.join(p.read_text() for p in tmp_path.iterdir())
