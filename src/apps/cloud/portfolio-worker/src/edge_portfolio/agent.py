"""One conversation, four fixed roles, bounded model/tool execution."""
import asyncio
import hashlib
import json
import re
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .contracts import LIMITS, ROLES, Section, FinalReport, calculate, validate_section

PROMPTS = Path(__file__).parent / "prompts"
MODEL = "deepseek-flash"
EXA_URL = "https://mcp.exa.ai/mcp"
TOOLS = [
    {"type": "function", "function": {"name": "search_sources", "description": "Exa에서 ETF 공식 자료와 반대 근거 검색. 개인정보를 검색어에 넣지 않는다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "objective": {"type": "string"}}, "required": ["query", "objective"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "fetch_source", "description": "검색에서 발견한 URL의 본문을 읽고 근거 ID를 받는다.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}}},
]


class ExaEvidence:
    def __init__(self, session):
        self.session = session
        self.sources = []
        self.discovered = set()
        self.calls = 0

    async def call(self, name, args):
        self.calls += 1
        if self.calls > 12:
            raise ValueError("TOOL_LIMIT")
        if name == "search_sources" and set(args) == {"query", "objective"}:
            if any(not isinstance(v, str) or not 1 <= len(v) <= 1000 for v in args.values()):
                raise ValueError("INVALID_TOOL_ARGUMENT")
            remote, arguments = "web_search_exa", {**args, "numResults": 3}
        elif name == "fetch_source" and set(args) == {"url"} and args["url"] in self.discovered:
            remote, arguments = "web_fetch_exa", {"urls": [args["url"]], "maxCharacters": 6000}
        else:
            raise ValueError("TOOL_NOT_ALLOWED")
        async with asyncio.timeout(35):
            result = await self.session.call_tool(remote, arguments)
        if result.isError:
            raise ValueError("EXA_FAILED")
        body = "\n".join(b.text for b in result.content if b.type == "text")[:24000]
        if remote == "web_search_exa":
            for url in re.findall(r"^URL:\s*(https://\S+)", body, re.M):
                parsed = urlsplit(url)
                if parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443):
                    self.discovered.add(url)
            return {"search_results": body, "fetchable_urls": sorted(self.discovered)}
        if len(body.strip()) < 50:
            raise ValueError("EMPTY_SOURCE")
        source = {"id": f"s{len(self.sources) + 1}", "url": args["url"], "excerpt": body[:6500],
                  "fetched_at": datetime.now(timezone.utc).isoformat()}
        chunks = []
        for line in re.split(r'(?<=[.!?])\s+|\n+', source['excerpt']):
            for start in range(0, len(line), 280):
                chunk = line[start:start + 280].strip()
                if len(chunk) >= 12:
                    chunks.append(chunk)
        source['passages'] = [{'id': f'p{i + 1}', 'text': text} for i, text in enumerate(chunks)]
        self.sources.append(source)
        return source


async def run_analysis(request, key, on_stage, *, model_call=None, evidence=None, observe=None):
    started = time.monotonic()
    metrics = calculate(request)
    system = (PROMPTS / "system.md").read_text(encoding="utf8")
    skills = {role: (PROMPTS / role / "SKILL.md").read_text(encoding="utf8") for role in ROLES}
    version = hashlib.sha256((system + "".join(skills.values())).encode()).hexdigest()
    context = {"input": request.model_dump(mode="json"), "computed": metrics,
               "as_of": datetime.now(timezone.utc).isoformat(), "aws_outlook": {"status": "not_connected"}}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    sections, trace = {}, []
    async with asyncio.timeout(300), AsyncExitStack() as stack:
        if evidence is None:
            streams = await stack.enter_async_context(streamablehttp_client(EXA_URL))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            evidence = ExaEvidence(session)
        if model_call is None:
            client = await stack.enter_async_context(httpx.AsyncClient(timeout=90))

            async def model_call(payload):
                response = await client.post("https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {key}"}, json=payload)
                response.raise_for_status()
                return response.json()

        calls = 0
        for role in ROLES:
            on_stage(role)
            schema = FinalReport if role == 'opinion' else Section
            messages.append({"role": "user", "content": skills[role] + "\n현재 등록된 근거 ID: " + json.dumps([s['id'] for s in evidence.sources]) + "\n등록되지 않은 ID를 만들지 마세요. 첫 질문 해석 단계에서는 사용자 입력만 해석하고 evidence는 빈 배열로 둡니다. 그 다음 역할은 search_sources 후 fetch_source로 실제 근거를 등록해야 합니다.\n출력 JSON 스키마:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)})
            repairs = 0
            while True:
                calls += 1
                if calls > 18 or len(json.dumps(messages).encode()) > 350000:
                    raise ValueError("MODEL_BUDGET")
                result = await model_call({"model": MODEL, "messages": messages, "tools": TOOLS,
                    "tool_choice": "none" if role == "question" else "auto", "thinking": {"type": "enabled"}, "reasoning_effort": "low",
                    "max_tokens": 10000 if role == 'opinion' else 6000, "stream": False})
                choice = result["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise ValueError("MODEL_TRUNCATED")
                message = choice["message"]
                if observe:
                    observe({"role": role, "content": message.get("content"), "tools": message.get("tool_calls"), "sources": evidence.sources})
                # Preserve reasoning_content in the private conversation for DeepSeek tool turns.
                messages.append(message)
                trace.append({"role": role, "call": calls, "usage": result.get("usage", {}), "elapsed_seconds": round(time.monotonic() - started, 2)})
                if message.get("tool_calls"):
                    for call in message["tool_calls"]:
                        function = call["function"]
                        answer = await evidence.call(function["name"], json.loads(function["arguments"]))
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(answer, ensure_ascii=False)})
                    continue
                try:
                    raw = json.loads(message.get("content") or "")
                    if role == 'opinion':
                        final = FinalReport.model_validate(raw)
                        sections = {name: validate_section(name, getattr(final, name).model_dump(), evidence.sources) for name in ROLES}
                    else:
                        sections[role] = validate_section(role, raw, evidence.sources)
                    break
                except (ValueError, TypeError) as error:
                    repairs += 1
                    if repairs > 1:
                        raise ValueError("OUTPUT_INVALID: " + str(error)[:500]) from error
                    low, high = LIMITS[role]
                    messages.append({"role": "user", "content": f"출력 검증 실패: {str(error)[:400]}. 현재 역할의 JSON을 한 번 수정하세요. 본문은 {(low + high) // 2}자 목표, 최대 {high}자. 근거는 실제 도구가 준 source_id와 passage_id를 고르세요. 등록 근거가 없는 질문 해석 단계는 evidence: []로 고치세요."})
                    if role == 'opinion':
                        messages[-1]['content'] += f' 최종 출력에는 네 칸 모두 필요합니다. 오류가 난 칸의 한도를 적용하세요: {LIMITS}.'
        return {"sections": sections, "metrics": metrics, "sources": evidence.sources,
                "as_of": context["as_of"], "model": MODEL, "prompt_version": version,
                "elapsed_seconds": round(time.monotonic() - started, 2), "trace": trace,
                "aws_outlook": context["aws_outlook"]}
