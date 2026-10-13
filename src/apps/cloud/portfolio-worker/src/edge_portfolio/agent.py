"""One conversation, four fixed roles, bounded model/tool execution."""
import asyncio
import hashlib
import json
import os
import re
import time
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .contracts import LIMITS, ROLES, Section, FinalReport, calculate, validate_section
from .outlook import TOOL as OUTLOOK_TOOL, OutlookReader
from .recommendation import search_etfs, publish_recommendation
from .market_data import MarketData, TOOL as MARKET_TOOL, enrich_report
from .optimizer import Optimizer, TOOL as OPTIMIZER_TOOL
from .exa_transport import ExaTransport
from .decision import Decision, QuestionSection, validate_decision, check_constraints

PROMPTS = Path(__file__).parent / "prompts"
MODEL = "deepseek-flash"
EXA_URL = "https://mcp.exa.ai/mcp"
MAX_EXA_CALLS = 70
MAX_MODEL_CALLS = 24
MAX_CONTEXT_BYTES = 350000
ANALYSIS_TIMEOUT_SECONDS = 300
TOOLS = [
    {"type": "function", "function": {"name": "search_etfs", "description": "추천할 ETF의 등록 ID를 이름 또는 티커로 찾는다. 상품 근거 조회와는 별개다. 한 번에 8개까지 반환한다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}},
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
        if self.calls > MAX_EXA_CALLS:
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
            return {"search_results": body[:2500], "fetchable_urls": sorted(self.discovered)}
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
        return {key: value for key, value in source.items() if key != 'excerpt'}


async def run_analysis(request, key, on_stage, *, model_call=None, evidence=None, observe=None, market=None,
                       on_event=None, on_checkpoint=None):
    started = time.monotonic()
    metrics = calculate(request)
    system = (PROMPTS / "system.md").read_text(encoding="utf8")
    skills = {role: (PROMPTS / role / "SKILL.md").read_text(encoding="utf8") for role in ROLES}
    version = hashlib.sha256((system + "".join(skills.values())).encode()).hexdigest()
    code_version = hashlib.sha256(b''.join(path.name.encode() + path.read_bytes() for path in sorted(PROMPTS.parent.glob('*.py')))).hexdigest()
    events = []
    limits = {'model_calls': MAX_MODEL_CALLS, 'context_bytes': MAX_CONTEXT_BYTES, 'exa_calls': MAX_EXA_CALLS,
              'elapsed_seconds': ANALYSIS_TIMEOUT_SECONDS}

    def emit(event, **fields):
        entry = {'event': event, 'elapsed_seconds': round(time.monotonic() - started, 2), **fields}
        events.append(entry)
        if on_event:
            on_event(entry)

    emit('run_started', model=MODEL, prompt_version=version, code_version=code_version, limits=limits)
    context = {"input": request.model_dump(mode="json"), "computed": metrics,
               "as_of": datetime.now(timezone.utc).isoformat(), "aws_outlook": {"status": "available_on_purpose" if os.getenv('PORTFOLIO_OUTLOOK_URL') else "not_connected"}}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    sections, trace, published = {}, [], {}
    outlook = OutlookReader(request, datetime.fromisoformat(context['as_of']))
    market = market or MarketData(request, datetime.fromisoformat(context['as_of']).date())
    optimizer = Optimizer(market, request)
    decision = Decision()
    checks = []

    def checkpoint():
        if on_checkpoint:
            on_checkpoint({'sections': dict(sections), 'sources': list(evidence.sources), 'metrics': metrics,
                           'optimization': optimizer.result, 'as_of': context['as_of'],
                           'decision': decision.model_dump(mode='json'), 'input': request.model_dump(mode='json')})

    async with asyncio.timeout(ANALYSIS_TIMEOUT_SECONDS), AsyncExitStack() as stack:
        if evidence is None:
            exa_client = await stack.enter_async_context(httpx.AsyncClient(transport=ExaTransport(api_key=os.environ['EXA_API_KEY']), timeout=httpx.Timeout(30, read=300)))
            streams = await stack.enter_async_context(streamable_http_client(EXA_URL, http_client=exa_client))
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
        read_retries = 0
        catalog_calls = 0
        for role in ROLES:
            on_stage(role)
            emit('stage_started', role=role)
            schema = FinalReport if role == 'opinion' else QuestionSection if role == 'question' else Section
            messages.append({"role": "user", "content": skills[role] + "\n현재 등록된 근거 ID: " + json.dumps([s['id'] for s in evidence.sources]) + "\n등록되지 않은 ID를 만들지 마세요. 첫 질문 해석 단계에서는 사용자 입력만 해석하고 evidence는 빈 배열로 둡니다. 그 다음 역할은 search_sources 후 fetch_source로 실제 근거를 등록해야 합니다.\n출력 JSON 스키마:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)})
            repairs = 0
            while True:
                calls += 1
                if calls > MAX_MODEL_CALLS:
                    emit('limit_reached', budget='model_calls', used=calls - 1, limit=MAX_MODEL_CALLS)
                    raise ValueError("MODEL_CALL_BUDGET")
                context_bytes = len(json.dumps(messages, ensure_ascii=False).encode('utf8'))
                if context_bytes > MAX_CONTEXT_BYTES:
                    emit('limit_reached', budget='context_bytes', used=context_bytes, limit=MAX_CONTEXT_BYTES)
                    raise ValueError("MODEL_CONTEXT_BUDGET")
                emit('model_started', role=role, call=calls, context_bytes=context_bytes)
                try:
                    result = await model_call({"model": MODEL, "messages": messages, "tools": TOOLS[:1] if role == 'question' else TOOLS + [OUTLOOK_TOOL, MARKET_TOOL, OPTIMIZER_TOOL],
                        "tool_choice": "auto", "thinking": {"type": "enabled"}, "reasoning_effort": "low",
                        "max_tokens": 16000 if role == 'opinion' else 6000, "stream": False})
                except httpx.ReadError:
                    emit('model_failed', role=role, call=calls, code='ReadError', retry=read_retries == 0)
                    if read_retries:
                        raise
                    read_retries += 1
                    await asyncio.sleep(1)
                    continue
                choice = result["choices"][0]
                usage = {name: value for name, value in result.get('usage', {}).items()
                         if name in {'prompt_tokens', 'completion_tokens', 'total_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'}
                         and isinstance(value, int) and not isinstance(value, bool) and value >= 0}
                emit('model_finished', role=role, call=calls, usage=usage,
                     finish_reason=choice.get('finish_reason') if choice.get('finish_reason') in {'stop', 'tool_calls', 'length', 'content_filter'} else 'unknown')
                if choice.get("finish_reason") == "length":
                    raise ValueError("MODEL_TRUNCATED")
                message = choice["message"]
                if observe:
                    observe({"role": role, "content": message.get("content"), "tools": message.get("tool_calls"), "sources": evidence.sources})
                # Preserve reasoning_content in the private conversation for DeepSeek tool turns.
                messages.append(message)
                trace.append({"role": role, "call": calls, "usage": usage, "elapsed_seconds": round(time.monotonic() - started, 2)})
                if message.get("tool_calls"):
                    for call in message["tool_calls"]:
                        function = call["function"]
                        tool = function['name'] if function['name'] in {'search_etfs', 'search_sources', 'fetch_source', 'optimize_portfolios', 'read_portfolio_history', 'read_etf_outlook'} else 'unknown'
                        emit('tool_started', role=role, tool=tool)
                        try:
                            args = json.loads(function['arguments'])
                            if not isinstance(args, dict):
                                args = None
                        except (ValueError, TypeError):
                            args = None
                        if args is None:
                            answer = {'status': 'rejected', 'reason': 'INVALID_TOOL_ARGUMENT'}
                        elif role == 'question' and function['name'] != 'search_etfs':
                            answer = {'status': 'rejected', 'reason': 'QUESTION_STAGE_CATALOG_ONLY'}
                        elif function['name'] == 'search_etfs':
                            catalog_calls += 1
                            if catalog_calls > 6:
                                answer = {'status': 'rejected', 'reason': 'Catalog search limit reached; use registered IDs already returned'}
                            else:
                                try:
                                    answer = search_etfs(args.get('query'))
                                except ValueError as error:
                                    answer = {'status': 'rejected', 'reason': str(error)}
                        elif function['name'] == 'optimize_portfolios':
                            try:
                                answer = await optimizer.tool(args)
                            except ValueError as error:
                                answer = {'status': 'rejected', 'reason': str(error)[:300]}
                        elif function['name'] == 'read_portfolio_history':
                            try:
                                answer = await market.tool(args)
                            except ValueError as error:
                                answer = {'status': 'rejected', 'reason': str(error)[:300]}
                        elif function['name'] == 'read_etf_outlook':
                            try:
                                answer = await outlook.call(args, evidence.sources)
                            except ValueError as error:
                                answer = {'status': 'rejected', 'reason': str(error)[:300]}
                        else:
                            try:
                                answer = await evidence.call(function["name"], args)
                            except ValueError as error:
                                if str(error) not in {'TOOL_NOT_ALLOWED', 'INVALID_TOOL_ARGUMENT', 'EMPTY_SOURCE', 'EXA_FAILED'}:
                                    emit('tool_failed', role=role, tool=tool, code='TOOL_LIMIT' if str(error) == 'TOOL_LIMIT' else type(error).__name__,
                                         counters={'exa_calls': getattr(evidence, 'calls', 0), 'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
                                    raise
                                answer = {'status': 'unavailable' if str(error) in {'EMPTY_SOURCE', 'EXA_FAILED'} else 'rejected',
                                          'reason': str(error), 'fetchable_urls': sorted(getattr(evidence, 'discovered', set())),
                                          'instruction': '본문을 읽지 못했습니다. 등록된 URL을 정확히 사용하거나 다른 공식 근거를 검색하세요. 실패한 응답을 근거로 인용하지 마세요.'}
                            except BaseException as error:
                                emit('tool_failed', role=role, tool=tool, code=type(error).__name__,
                                     counters={'exa_calls': getattr(evidence, 'calls', 0), 'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
                                raise
                        status = answer.get('status', 'available') if isinstance(answer, dict) else 'available'
                        reason = answer.get('reason') if isinstance(answer, dict) else None
                        codes = {'INVALID_TOOL_ARGUMENT', 'TOOL_LIMIT', 'TOOL_NOT_ALLOWED', 'EXA_FAILED', 'EMPTY_SOURCE',
                                 'CURRENT_WEIGHTS_REQUIRED', 'COMPARISON_DATA_UNAVAILABLE', 'NO_COMMON_PRICE_HISTORY',
                                 'NO_COMPARABLE_PRICE_HISTORY', 'BASELINE_PRICE_UNAVAILABLE', 'PRICE_STALE', 'PRICE_UNAVAILABLE'}
                        emit('tool_finished', role=role, tool=tool,
                             status=status if status in {'available', 'unavailable', 'rejected', 'no_match', 'found', 'not_found', 'missing', 'stale', 'not_connected'} else 'unknown',
                             code=reason if reason in codes else 'TOOL_REJECTED' if status in {'rejected', 'unavailable'} else None,
                             counters={'exa_calls': getattr(evidence, 'calls', 0), 'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
                        checkpoint()
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(answer, ensure_ascii=False)})
                    continue
                try:
                    raw = json.loads(message.get("content") or "")
                    if role == 'opinion':
                        final = FinalReport.model_validate(raw)
                        selected = optimizer.validate(final)
                        if final.recommendation:
                            checks = check_constraints(decision, [row.model_dump() for row in final.recommendation.allocations], request, market.catalog)
                        elif not checks:
                            checks = [{'constraint': number, 'kind': rule.kind, 'source': rule.source, 'status': 'unknown',
                                       'reason': 'UNVERIFIED_CONSTRAINT' if rule.kind == 'unverified' else 'NO_ALLOCATION_SELECTED'}
                                      for number, rule in enumerate(decision.constraints, 1)]
                        if final.recommendation and any(check['status'] != 'pass' for check in checks):
                            raise ValueError('CUSTOMER_CONSTRAINT_NOT_SATISFIED: select another candidate; never relax mandatory constraints: ' + json.dumps(checks, ensure_ascii=False))
                        sections = {name: validate_section(name, getattr(final, name).model_dump(), evidence.sources) for name in ROLES}
                        published = publish_recommendation(final, request, metrics, evidence.sources)
                        published['optimization'] = {**optimizer.result, 'selected_candidate_id': final.selected_candidate_id}
                        if not selected and optimizer.result['candidates']:
                            blocked = any(check['status'] != 'pass' and check.get('reason') != 'NO_ALLOCATION_SELECTED' for check in checks)
                            published['optimization']['reason'] = 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' if blocked else 'RECOMMENDATION_WITHHELD' if checks else 'RATING_BELOW_MINIMUM'
                        if selected:
                            await enrich_report(published, final, market, evidence.sources)
                    else:
                        if role == 'question':
                            interpreted = QuestionSection.model_validate(raw)
                            decision = validate_decision(interpreted.decision, request, market.catalog)
                            raw = interpreted.model_dump(exclude={'decision'})
                        if role == 'portfolio' and optimizer.result is None:
                            raise ValueError('Before completing portfolio, call optimize_portfolios once with 10 distinct ETF combinations; obtain at least 4 distinct registered ETFs first using search_etfs')
                        sections[role] = validate_section(role, raw, evidence.sources)
                    emit('stage_completed', role=role)
                    checkpoint()
                    break
                except (ValueError, TypeError) as error:
                    repairs += 1
                    emit('validation_failed', role=role, attempt=repairs, code=type(error).__name__)
                    if repairs > (2 if role == 'opinion' else 1):
                        raise ValueError("OUTPUT_INVALID: " + str(error)[:500]) from error
                    low, high = LIMITS[role]
                    messages.append({"role": "user", "content": f"출력 검증 실패: {str(error)[:400]}. 현재 역할의 JSON을 한 번 수정하세요. 본문은 {(low + high) // 2}자 목표, 최대 {high}자. 근거는 실제 도구가 준 source_id와 passage_id를 고르세요. 등록 근거가 없는 질문 해석 단계는 evidence: []로 고치세요."})
                    if role == 'opinion':
                        messages[-1]['content'] += f' 최종 출력에는 네 칸 모두 필요합니다. 오류가 난 칸의 한도를 적용하세요: {LIMITS}.'
        emit('run_completed', counters={'model_calls': calls, 'exa_calls': getattr(evidence, 'calls', 0),
             'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
        return {**published, "sections": sections, "metrics": metrics, "sources": evidence.sources,
                'input': request.model_dump(mode='json'), 'decision': decision.model_dump(mode='json'),
                'baseline_kind': market.baseline_kind, 'customer_constraint_checks': checks,
                "as_of": context["as_of"], "model": MODEL, "prompt_version": version,
                "elapsed_seconds": round(time.monotonic() - started, 2), "trace": trace,
                'diagnostics': {'events': events, 'termination_reason': 'COMPLETED', 'model_calls': calls,
                    'exa_calls': getattr(evidence, 'calls', 0), 'limits': limits, 'code_version': code_version,
                    'model': MODEL, 'prompt_version': version},
                "aws_outlook": {'queries': outlook.queries, 'status': 'queried' if outlook.queries else 'not_requested'}}
