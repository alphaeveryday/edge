"""Verify SDK discovery, source queries and persisted envelopes through MCP."""
import asyncio
import json

import anyio
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from test_thread_repository import db, article, START, AT
from edge_analysis_v2.news_thread_server import make_news_thread_server


def test_sdk_call_reads_database_and_persists_identical_envelope(db, tmp_path):
    article(db, 0)
    server = make_news_thread_server(db, ["sixth-stock"], tmp_path,
                                    start_at=START, analysis_at=AT)["instance"]

    async def check():
        async with create_client_server_memory_streams() as (client, transport):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(server.run, *transport, server.create_initialization_options())
                async with ClientSession(*client) as session:
                    await session.initialize()
                    catalog = (await session.list_tools()).tools
                    assert [item.name for item in catalog] == ["get_news_thread", "get_issue_evidence", "search_news_threads"]
                    assert "탐색용" in catalog[0].description
                    invalid = await session.call_tool("get_news_thread", {"thread_id": "thread", "analysis_at": "2099"})
                    assert invalid.model_dump(by_alias=True)["isError"]
                    assert list((tmp_path / "runs").glob("*.json")) == []
                    found = await session.call_tool('search_news_threads', {})
                    assert not found.model_dump(by_alias=True)['isError']
                    assert json.loads(found.content[0].text)['result']['threads'][0]['thread_id'] == 'thread'
                    future = await session.call_tool('search_news_threads', {'end_at': '2026-09-22T08:30:00+09:00'})
                    assert future.model_dump(by_alias=True)['isError']
                    response = await session.call_tool("get_news_thread", {"thread_id": "thread"})
                    assert not response.model_dump(by_alias=True)["isError"]
                    output = json.loads(response.content[0].text)
                    record = json.loads((tmp_path / "runs" / (output["tool_run_id"] + ".json")).read_text(encoding="utf-8"))
                    assert record["output"] == output
                    assert record["arguments"] == {"thread_id": "thread"}
                    assert record["context"]["analysis_at"] == AT
                    assert output["result"]["stages"][0]["events"][0]["document_id"] == "0"
                    second = await session.call_tool("get_news_thread", {"thread_id": "thread"})
                    assert json.loads(second.content[0].text)["tool_run_id"] != output["tool_run_id"]
                    for include_body in (True, False):
                        response = await session.call_tool('get_issue_evidence', {'news_ids': ['0'], 'include_body': include_body})
                        assert not response.model_dump(by_alias=True)['isError']
                        evidence = json.loads(response.content[0].text)
                        record = json.loads((tmp_path / 'runs' / (evidence['tool_run_id'] + '.json')).read_text(encoding='utf-8'))
                        assert record['output'] == evidence
                        assert record['tool_id'] == 'get_issue_evidence:v2'
                        assert record['arguments']['include_body'] is include_body
                        assert ('lead_text' in evidence['result']['news'][0]) is include_body
                tasks.cancel_scope.cancel()
    asyncio.run(check())


def test_storage_failure_is_not_returned_as_success(db, tmp_path):
    article(db, 0)
    config = make_news_thread_server(db, ["sixth-stock"], tmp_path, start_at=START, analysis_at=AT)
    (tmp_path / "runs").rmdir()
    (tmp_path / "runs").write_text("blocked", encoding="utf-8")

    async def check():
        server = config["instance"]
        async with create_client_server_memory_streams() as (client, transport):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(server.run, *transport, server.create_initialization_options())
                async with ClientSession(*client) as session:
                    await session.initialize()
                    assert (await session.call_tool("get_news_thread", {"thread_id": "thread"})).model_dump(by_alias=True)["isError"]
                tasks.cancel_scope.cancel()
    asyncio.run(check())


def test_sdk_pagination_reaches_hidden_events_without_changing_scope(db, tmp_path):
    for number in range(7):
        article(db, number)
    config = make_news_thread_server(db, ['sixth-stock'], tmp_path, start_at=START, analysis_at=AT)

    async def check():
        server = config['instance']
        async with create_client_server_memory_streams() as (client, transport):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(server.run, *transport, server.create_initialization_options())
                async with ClientSession(*client) as session:
                    await session.initialize()
                    arguments, ids = {'thread_id': 'thread'}, []
                    while True:
                        response = await session.call_tool('get_news_thread', arguments)
                        assert not response.model_dump(by_alias=True)['isError']
                        output = json.loads(response.content[0].text)
                        page = output['result']
                        ids.extend(e['source_event_id'] for s in page['stages'] for e in s['events'])
                        if page['next_cursor'] is None:
                            break
                        arguments = {'thread_id': 'thread', 'cursor': page['next_cursor']}
                    assert ids == list(map(str, range(7)))
                    invalid = await session.call_tool('get_news_thread', {'thread_id': 'thread', 'cursor': 'outside'})
                    assert invalid.model_dump(by_alias=True)['isError']
                    assert len(list((tmp_path / 'runs').glob('*.json'))) == 3
                tasks.cancel_scope.cancel()
    asyncio.run(check())
