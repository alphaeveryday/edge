import asyncio
import json

import psycopg
from mcp.client import Client

from edge_analysis_v2 import mcp_server

from edge_analysis_v2.mcp_server import build
from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.tools.fixture_data.demo import make_fixture


def ask(steps):
    opened = []

    def open_tools(etf_code, analysis_at):
        opened.append((etf_code, analysis_at))
        if etf_code == 'closed':
            raise OSError('tunnel 127.0.0.1:15432 closed')
        return FixtureTools(make_fixture()), []

    async def run():
        async with Client(build(open_tools)) as client:
            return [await client.call_tool(name, arguments) for name, arguments in steps]

    return asyncio.run(run()), opened


def test_an_outside_agent_opens_an_etf_then_calls_the_listed_tools():
    (opened_etf, holdings), loads = ask([
        ('open_etf', {'etf_code': '091160', 'analysis_at': '2026-09-21T10:00:00+09:00'}),
        ('call', {'tool': 'get_etf_holdings', 'arguments': {}}),
    ])
    assert loads == [('091160', '2026-09-21T10:00:00+09:00')], 'The database is read once, for the asked ETF and time'
    listed = {tool['name']: tool for tool in json.loads(opened_etf.content[0].text)['tools']}
    assert 'get_etf_holdings' in listed and listed['get_etf_holdings']['parameters']['type'] == 'object', \
        'The agent learns each tool and its argument schema from open_etf, since nothing else lists them'
    result = json.loads(holdings.content[0].text)
    assert not holdings.is_error and result['etf_code'] == '091160' and result['result'], \
        'Every answer says which ETF and time it describes'


def test_calling_before_opening_or_with_wrong_arguments_is_an_error_the_agent_can_read():
    (early, _, wrong), _ = ask([
        ('call', {'tool': 'get_etf_holdings', 'arguments': {}}),
        ('open_etf', {'etf_code': '091160'}),
        ('call', {'tool': 'get_etf_holdings', 'arguments': {'unexpected': 1}}),
    ])
    assert early.is_error and 'open_etf' in early.content[0].text
    assert wrong.is_error and 'schema' in wrong.content[0].text, 'A bad call tells the agent what to fix instead of ending the server'


def test_a_failed_open_says_why_and_leaves_no_earlier_etf_answering():
    (_, failed, after), _ = ask([
        ('open_etf', {'etf_code': '091160'}),
        ('open_etf', {'etf_code': 'closed'}),
        ('call', {'tool': 'get_etf_holdings', 'arguments': {}}),
    ])
    assert failed.is_error and '15432' in failed.content[0].text, 'A closed tunnel must be named, not "Error executing tool"'
    assert after.is_error and 'open_etf' in after.content[0].text, 'Calls must not silently answer for the previously opened ETF'


def test_a_failing_writer_side_drops_only_macro_and_financial_tools_and_says_why(monkeypatch):
    source = {k: v for k, v in make_fixture().items() if k not in ('macro', 'financials')}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement):
            raise psycopg.errors.InsufficientPrivilege('permission denied for function macro_as_of')

    monkeypatch.setattr(mcp_server, 'connect_sources', lambda ca: Connection())
    monkeypatch.setattr(mcp_server, 'load_source', lambda connection, etf, at: source)
    monkeypatch.setattr(mcp_server, 'load_flow', lambda connection, data: data)
    monkeypatch.setattr(mcp_server, 'load_prices', lambda connection, data: data)
    monkeypatch.setattr(mcp_server, 'connect_results', lambda ca: Connection())
    tools, notes = mcp_server.load('ca.pem', '091160', '2026-09-21T10:00:00+09:00')
    names = {schema['function']['name'] for schema in tools.schemas}
    assert 'get_etf_holdings' in names and 'get_macro_observations' not in names, 'Holdings still answer when only the writer side fails'
    assert notes and 'InsufficientPrivilege' in notes[0], 'The note names the real failure, not a guess about the tunnel'
