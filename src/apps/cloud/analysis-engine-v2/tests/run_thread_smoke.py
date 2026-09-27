"""Manually call DeepSeek through Claude SDK against a synthetic news database."""
import argparse
import asyncio
import json
import os
from pathlib import Path

import yaml
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

from test_thread_repository import db, article, START, AT
from edge_analysis_v2.news_thread_server import make_news_thread_server


def validate_response(report, runs):
    """Check the model's returned IDs against the requested synthetic article."""
    text = report.get("result") or ""
    parsed, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    run = next(r for r in runs if r["output"]["tool_run_id"] == parsed["tool_run_id"])
    assert run["arguments"] == {"thread_id": "thread"}, "Wrong thread requested"
    assert run["output"]["result"] is not None, "Expected article was not retrieved"
    articles = {event["document_id"] for stage in run["output"]["result"]["stages"] for event in stage["events"]}
    assert parsed["document_id"] == "0" and "0" in articles, "Article ID does not match tool result"


async def run(env_file: Path, output: Path):
    """Run one bounded live-provider check and persist its inspectable result.

    Args:
        env_file: Local file containing DEEPSEEK_API_KEY and optional model.
        output: New artifact directory; existing runs are never overwritten.
    """
    config = {}
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep and key.strip() in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"):
            config[key.strip()] = value.strip().strip("\"'")
    key = os.environ.get("DEEPSEEK_API_KEY", config.get("DEEPSEEK_API_KEY", ""))
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is missing")
    output.mkdir(parents=True, exist_ok=False)
    model = config.get("DEEPSEEK_MODEL", "deepseek-flash")
    fixture = db.__wrapped__()
    connection = next(fixture)
    article(connection, 0)
    prompt = yaml.safe_load(Path(__file__).with_name("thread_smoke_prompt.yaml").read_text(encoding="utf-8"))["system_prompt"]
    request = json.dumps({"task": "지정 스레드를 조회하고 tool_run_id와 첫 사건 document_id를 반환", "thread_id": "thread"}, ensure_ascii=False)
    (output / "input.json").write_text(json.dumps({"source": "synthetic_sqlite", "user_prompt": request,
        "system_prompt": prompt, "analysis_at": AT}, ensure_ascii=False, indent=2), encoding="utf-8")
    server = make_news_thread_server(connection, ["sixth-stock"], output, start_at=START, analysis_at=AT)
    options = ClaudeAgentOptions(model=model, system_prompt=prompt, tools=[],
        mcp_servers={"analysis": server}, allowed_tools=["mcp__analysis__get_news_thread"],
        strict_mcp_config=True, permission_mode="dontAsk", setting_sources=[],
        max_turns=4, cwd=str(output.resolve()), thinking={"type": "disabled"},
        env={"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
             "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_API_KEY": "", "CLAUDE_CODE_OAUTH_TOKEN": "",
             "ANTHROPIC_MODEL": model, "DISABLE_AUTO_COMPACT": "0", "DISABLE_COMPACT": "0"})
    report = {"status": "failed", "source": "synthetic_sqlite", "model": model}
    try:
        async with asyncio.timeout(120):
            async with ClaudeSDKClient(options=options) as client:
                await client.query(request)
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        report.update(result=message.result, is_error=message.is_error)
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in (output / "runs").glob("*.json")]
        assert runs and not report.get("is_error", True), "No successful model/tool result"
        validate_response(report, runs)
        report["status"] = "passed"
        report["tool_run_ids"] = [r["output"]["tool_run_id"] for r in runs]
    except Exception as error:
        report["error"] = str(error).replace(key, "[redacted]")
    finally:
        fixture.close()
        (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.env_file, args.output_dir))
