"""Explicit, billable smoke run against DeepSeek Flash and the Exa public endpoint."""
import asyncio
import json
import os
from pathlib import Path

from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.catalog import canonicalize


async def main():
    request = AnalysisInput(holdings=[
        {"name": "Vanguard S&P 500 ETF (VOO)", "amount_krw": 7000000},
        {"name": "Invesco NASDAQ 100 ETF (QQQM)", "amount_krw": 3000000}],
        horizon="10년 이상", question="두 ETF가 겹치면 하나를 줄여야 하나요?")
    Path("artifacts").mkdir(exist_ok=True)
    def observe(event):
        with Path('artifacts/smoke-events.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
    result = await run_analysis(canonicalize(request), os.environ["DEEPSEEK_API_KEY"], lambda stage: print(stage, flush=True), observe=observe)
    Path("artifacts/live-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({"seconds": result["elapsed_seconds"], "sources": len(result["sources"]),
                      "lengths": {k: sum(map(len, v["paragraphs"])) for k, v in result["sections"].items()}}))


asyncio.run(main())
