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
                    assert [item.name for item in catalog] == ["get_news_thread"]
                    assert "탐색용" in catalog[0].description
                    invalid = await session.call_tool("get_news_thread", {"thread_id": "thread", "analysis_at": "2099"})
                    assert invalid.model_dump(by_alias=True)["isError"]
                    assert list((tmp_path / "runs").glob("*.json")) == []
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
