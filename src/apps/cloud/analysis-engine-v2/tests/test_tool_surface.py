"""Reduce repeated source IDs without weakening the tool execution boundary."""
import asyncio
from copy import deepcopy
import json

import pytest
from jsonschema import ValidationError

from edge_analysis_v2.agent import runner as model_runner
from edge_analysis_v2.tools.model_schema import agent_tool_schemas


def schemas():
    return [{'function': {'name': 'read', 'description': 'Read a known observation.',
        'parameters': {'type': 'object', 'additionalProperties': False,
            'required': ['instrument_id', 'trade_date', 'investor'], 'properties': {
                'instrument_id': {'type': 'string', 'enum': ['stock-1']},
                'trade_date': {'type': 'string', 'enum': ['2026-09-18']},
                'investor': {'type': 'string', 'enum': ['foreign', 'institution']},
                'start_observation': {'type': 'string', 'enum': ['daily:2026-09-18']},
                'news_ids': {'type': 'array', 'items': {'type': 'string', 'enum': ['news-1']}}
            }}}}]


def test_data_choices_are_read_from_input_not_repeated_in_tool_schema():
    original = schemas()
    before = deepcopy(original)
    visible = agent_tool_schemas(original)
    properties = visible[0]['function']['parameters']['properties']
    for key in ('instrument_id', 'trade_date', 'start_observation'):
        assert properties[key] == {'type': 'string'}
    assert properties['news_ids']['items'] == {'type': 'string'}
    assert properties['investor']['enum'] == ['foreign', 'institution']
    assert original == before


def test_hidden_scope_constraints_still_reject_before_execution(monkeypatch):
    monkeypatch.setattr(model_runner, 'create_sdk_mcp_server', lambda **kwargs: kwargs)
    called = []
    expected = {'tool_run_id': 'stored-id', 'result': {'value': 18}}
    def call(name, arguments):
        called.append(arguments)
        return expected
    server, _ = model_runner.make_server(schemas(), call)
    handler = server['tools'][0].handler
    valid = {'instrument_id': 'stock-1', 'trade_date': '2026-09-18', 'investor': 'foreign'}
    for invalid in (valid | {'instrument_id': 'other'}, valid | {'trade_date': '2099-01-01'},
                    valid | {'investor': 'unknown'}):
        rejected = asyncio.run(handler(invalid))
        # Rejected as a tool error the agent can read and repair, never executed.
        assert rejected['is_error'] is True and rejected['content'][0]['text']
    assert called == []
    result = asyncio.run(handler(valid))
    assert json.loads(result['content'][0]['text']) == expected
    assert called == [valid]
