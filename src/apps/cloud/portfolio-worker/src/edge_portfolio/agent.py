"""One conversation, four fixed roles, bounded model/tool execution."""
import asyncio
import hashlib
import json
import os
import re
import time
from copy import deepcopy
from decimal import Decimal
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .contracts import LIMITS, ROLES, Section, FinalReport, calculate, validate_section
from .outlook import TOOL as OUTLOOK_TOOL, OutlookReader
from .recommendation import search_etfs, publish_recommendation
from .market_data import MarketData, TOOL as MARKET_TOOL, enrich_report
from .optimizer import Optimizer, TOOL as OPTIMIZER_TOOL
from .exa_transport import ExaTransport
from .decision import Decision, QuestionSection, validate_decision, check_constraints
from .execution import execution_tool, resolve_funding, zero_funding_quote, TOOL as EXECUTION_TOOL
from .scenarios import build_scenarios, stated_principal, principal_declaration, principal_context_matches, principal_preference_matches, TOOL as SCENARIO_TOOL
from .evidence_quality import inspect_body, source_page
from .completion import assess_completion, data_status

PROMPTS = Path(__file__).parent / "prompts"
MODEL = "deepseek-flash"
EXA_URL = "https://mcp.exa.ai/mcp"
MAX_EXA_CALLS = 70
MAX_MODEL_CALLS = 24
MAX_CONTEXT_BYTES = 350000
ANALYSIS_TIMEOUT_SECONDS = 600
TOOLS = [
    {"type": "function", "function": {"name": "search_etfs", "description": "ETF 등록 ID를 이름·티커로 찾는다. 정확 일치 우선, 공백·하이픈 차이를 허용한다. 결과가 8개면 offset을 8씩 늘려 다음 후보를 읽는다. 상품 근거 조회와 별개다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "offset": {"type": "integer", "minimum": 0, "maximum": 200}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_sources", "description": "Exa에서 ETF 공식 자료와 반대 근거 검색. 개인정보를 검색어에 넣지 않는다.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "objective": {"type": "string"}}, "required": ["query", "objective"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "fetch_source", "description": "검색에서 발견한 URL의 본문을 읽고 근거 ID를 받는다. next_offset으로 다음 문단을 읽으며 절단된 문서는 expand로 확장하거나 다른 공식 자료를 찾는다.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "expand": {"type": "boolean"}}, "required": ["url"], "additionalProperties": False}}},
]


class ExaEvidence:
    def __init__(self, session, session_factory=None):
        self.session = session
        self.session_factory = session_factory
        self.sources = []
        self.discovered = set()
        self.calls = 0

    async def call(self, name, args):
        if name == 'fetch_source' and isinstance(args.get('url'), str) and set(args) <= {'url', 'offset', 'expand'}:
            if not isinstance(args.get('offset', 0), int) or isinstance(args.get('offset', 0), bool) or args.get('offset', 0) < 0:
                raise ValueError('INVALID_PASSAGE_OFFSET')
            cached = next((source for source in reversed(self.sources) if source['url'] == args['url']), None)
            if not isinstance(args.get('expand', False), bool):
                raise ValueError('INVALID_TOOL_ARGUMENT')
            if cached and not args.get('expand'):
                return source_page(cached, args.get('offset', 0))
            if args.get('offset', 0) != 0:
                raise ValueError('INVALID_PASSAGE_OFFSET')
        self.calls += 1
        if self.calls > MAX_EXA_CALLS:
            raise ValueError("TOOL_LIMIT")
        if name == "search_sources" and set(args) == {"query", "objective"}:
            if any(not isinstance(v, str) or not 1 <= len(v) <= 1000 for v in args.values()):
                raise ValueError("INVALID_TOOL_ARGUMENT")
            remote, arguments = "web_search_exa", {**args, "numResults": 3}
        elif name == "fetch_source" and set(args) <= {'url', 'offset', 'expand'} and isinstance(args.get('url'), str) and args['url'] in self.discovered:
            remote, arguments = "web_fetch_exa", {"urls": [args["url"]], "maxCharacters": 48000 if args.get('expand') else 24000}
        else:
            raise ValueError("TOOL_NOT_ALLOWED")
        async with asyncio.timeout(35):
            if self.session is None:
                self.session = await self.session_factory()
            result = await self.session.call_tool(remote, arguments)
        if result.isError:
            raise ValueError("EXA_FAILED")
        body = "\n".join(b.text for b in result.content if b.type == "text")[:arguments.get('maxCharacters', 24000) + 2000]
        if remote == "web_search_exa":
            for url in re.findall(r"^URL:\s*(https://\S+)", body, re.M):
                try:
                    parsed = urlsplit(url)
                    if parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None, 443):
                        self.discovered.add(url)
                except ValueError:
                    continue
            return {"search_results": body[:2500], "fetchable_urls": sorted(self.discovered)}
        quality = inspect_body(body, arguments['maxCharacters'])
        if quality['status'] == 'unavailable':
            raise ValueError(quality['reason'])
        source = {"id": f"s{len(self.sources) + 1}", "url": args["url"], "excerpt": body, **quality,
                  "fetched_at": datetime.now(timezone.utc).isoformat()}
        chunks = []
        for line in source['excerpt'].splitlines():
            if re.match(r'^\s*(?:URL|Published|Author):', line):
                continue
            for start in range(0, len(line), 240):
                # Overlap keeps standard written dates intact at passage boundaries.
                chunk = line[start:start + 280].strip()
                # The document already passed the body-quality check. Short rows
                # can carry dates, labels, or amounts needed to cite its tables.
                if chunk:
                    chunks.append(chunk)
        source['passages'] = [{'id': f'p{i + 1}', 'text': text} for i, text in enumerate(chunks)]
        self.sources.append(source)
        return source_page(source)


async def run_analysis(request, key, on_stage, *, model_call=None, evidence=None, observe=None, market=None,
                       on_event=None, on_checkpoint=None, prior_report=None, followup_question=None):
    started = time.monotonic()
    metrics = calculate(request)
    system = (PROMPTS / "system.md").read_text(encoding="utf8")
    skills = {role: (PROMPTS / role / "SKILL.md").read_text(encoding="utf8") for role in ROLES}
    version = hashlib.sha256((system + "".join(skills.values())).encode()).hexdigest()
    code_version = hashlib.sha256(b''.join(path.name.encode() + path.read_bytes() for path in sorted(PROMPTS.parent.glob('*.py')))).hexdigest()
    events = []
    timeout_seconds = 300 if followup_question is not None else ANALYSIS_TIMEOUT_SECONDS
    limits = {'model_calls': MAX_MODEL_CALLS, 'context_bytes': MAX_CONTEXT_BYTES, 'exa_calls': MAX_EXA_CALLS,
              'elapsed_seconds': timeout_seconds}

    def emit(event, **fields):
        entry = {'event': event, 'elapsed_seconds': round(time.monotonic() - started, 2), **fields}
        events.append(entry)
        if on_event:
            on_event(entry)

    emit('run_started', model=MODEL, prompt_version=version, code_version=code_version, limits=limits)
    context = {"input": request.model_dump(mode="json"), "computed": metrics,
               "as_of": datetime.now(timezone.utc).isoformat(), "aws_outlook": {"status": "available_on_purpose" if os.getenv('PORTFOLIO_OUTLOOK_URL') else "not_connected"}}
    if prior_report:
        context['prior_report'] = {key: prior_report[key] for key in ('sections', 'as_of', 'decision', 'completion', 'recommendation', 'current_evaluation', 'insights') if key in prior_report}
        if prior_report.get('recommendation'):
            context['prior_report']['recommendation'] = {key: value for key, value in prior_report['recommendation'].items() if key != 'historical_performance'}
        context['followup_question'] = followup_question
        context['followup_instruction'] = '후속 질문에 직접 답하고 저장된 결정을 설명하거나 보완하세요. 기존 고객 필수 제약은 유지합니다. 이전 보고서는 과거 판단이며 이번에 새로 확인한 사실과 구분합니다. 리밸런싱은 기존 추천의 실행표부터 확인하고 변경이 필요할 때만 새 후보를 제안하세요.'
    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    sections, trace, published = {}, [], {}
    outlook = OutlookReader(request, datetime.fromisoformat(context['as_of']))
    market = market or MarketData(request, datetime.fromisoformat(context['as_of']).date())
    optimizer = Optimizer(market, request)
    decision = Decision()
    checks = []
    observed_data = {}
    failed_source_urls = set()
    scenario_result = None
    followup_execution = None
    followup_checks = []
    execution_rejection = None
    execution_failures = {}
    execution_feasible = set()
    accepted_report = {}
    accepted_final = None
    accepted_search = None

    def checkpoint():
        if on_checkpoint:
            on_checkpoint({**accepted_report, 'sections': accepted_report.get('sections', dict(sections)), 'sources': list(evidence.sources), 'metrics': metrics,
                           **({'scenarios': deepcopy(scenario_result)} if scenario_result is not None else {}),
                           'optimization': accepted_report.get('optimization', optimizer.result), 'as_of': context['as_of'],
                           'decision': decision.model_dump(mode='json'), 'input': request.model_dump(mode='json')})

    async with asyncio.timeout(timeout_seconds), AsyncExitStack() as stack:
        if evidence is None:
            async def connect_evidence():
                exa_client = await stack.enter_async_context(httpx.AsyncClient(transport=ExaTransport(api_key=os.environ['EXA_API_KEY']), timeout=httpx.Timeout(30, read=300)))
                streams = await stack.enter_async_context(streamable_http_client(EXA_URL, http_client=exa_client))
                session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
                await session.initialize()
                return session
            evidence = ExaEvidence(None, connect_evidence)
        if prior_report:
            evidence.sources = deepcopy(prior_report.get('sources', []))
            if hasattr(evidence, 'discovered'):
                evidence.discovered.update(source['url'] for source in evidence.sources if source.get('url'))
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
            recovery_rounds = 0
            while True:
                state = data_status(market, optimizer, evidence.sources, observed_data)
                metrics['data_status'] = state
                metrics['unavailable_reason'] = ('과거 가격 자료를 확인했어요. 미래 전망과 배당 자료는 별도로 확인해요.' if state['history'] in {'available', 'partial'}
                    else '과거 가격 자료를 아직 조회하지 않았어요.' if state['history'] == 'not_requested' else '가격 자료의 일부가 없거나 비교 조건을 충족하지 못했어요.')
                context['computed'] = metrics
                context['data_status'] = state
                messages[1]['content'] = json.dumps(context, ensure_ascii=False)
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
                    result = await model_call({"model": MODEL, "messages": messages, "tools": TOOLS[:1] if role == 'question' else [SCENARIO_TOOL] if decision.kind == 'scenario' else TOOLS + [OUTLOOK_TOOL, MARKET_TOOL, OPTIMIZER_TOOL, EXECUTION_TOOL, SCENARIO_TOOL],
                        "tool_choice": "auto", "thinking": {"type": "enabled"}, "reasoning_effort": "low", "response_format": {"type": "json_object"},
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
                    repairs += 1
                    emit('validation_failed', role=role, attempt=repairs, code='MODEL_TRUNCATED')
                    if repairs > (2 if role == 'opinion' else 1):
                        raise ValueError("MODEL_TRUNCATED")
                    messages.append({'role': 'user', 'content': '응답이 길이 한도로 잘렸습니다. 이미 성공한 도구 결과는 유지하고 필수 답변만 짧은 JSON으로 다시 작성하세요.'})
                    continue
                message = choice["message"]
                if observe:
                    observe({"role": role, "content": message.get("content"), "tools": message.get("tool_calls"), "sources": evidence.sources})
                # Preserve reasoning_content in the private conversation for DeepSeek tool turns.
                messages.append(message)
                trace.append({"role": role, "call": calls, "usage": usage, "elapsed_seconds": round(time.monotonic() - started, 2)})
                if message.get("tool_calls"):
                    for call in message["tool_calls"]:
                        function = call["function"]
                        tool = function['name'] if function['name'] in {'search_etfs', 'search_sources', 'fetch_source', 'optimize_portfolios', 'read_portfolio_history', 'read_etf_outlook', 'calculate_rebalance', 'calculate_scenarios'} else 'unknown'
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
                        elif role != 'question' and decision.kind == 'scenario' and function['name'] != 'calculate_scenarios':
                            answer = {'status': 'rejected', 'reason': 'SCENARIO_CALCULATION_ONLY'}
                        elif function['name'] == 'search_etfs':
                            catalog_calls += 1
                            if catalog_calls > 20:
                                answer = {'status': 'rejected', 'reason': 'Catalog search limit reached; use registered IDs already returned'}
                            else:
                                try:
                                    answer = search_etfs(args.get('query'), args.get('offset', 0))
                                except ValueError as error:
                                    answer = {'status': 'rejected', 'reason': str(error)}
                        elif function['name'] == 'calculate_rebalance':
                            try:
                                answer = execution_tool(request, args, decision, market.catalog)
                                target = {}
                                for row in args['allocations']:
                                    target[row['instrument_id']] = target.get(row['instrument_id'], Decimal(0)) + Decimal(str(row['weight_pct']))
                                for candidate in (optimizer.result or {}).get('candidates', []):
                                    expected = {row['instrument_id']: row['weight_pct'] for row in candidate.get('allocations', [])}
                                    if target == expected:
                                        if answer['status'] in {'ready', 'conditional'}:
                                            execution_feasible.add(candidate['candidate_id'])
                                        if answer['status'] == 'infeasible' and candidate['candidate_id'] not in execution_feasible:
                                            execution_failures[candidate['candidate_id']] = answer
                                        else:
                                            execution_failures.pop(candidate['candidate_id'], None)
                            except ValueError as error:
                                answer = {'status': 'rejected', 'reason': str(error)[:300]}
                        elif function['name'] == 'calculate_scenarios':
                            try:
                                funding = resolve_funding(request, decision)
                                known_principal = bool(request.holdings) and all(h.amount_krw is not None for h in request.holdings)
                                principal = sum(h.amount_krw for h in request.holdings) if request.input_mode == 'amount' and known_principal else None
                                original_question = getattr(request, 'funding_questions', [request.question])[0]
                                for statement in ([followup_question, original_question] if followup_question else [request.question]):
                                    supplied_principal = stated_principal(statement)
                                    if supplied_principal is not None:
                                        principal = supplied_principal
                                        break
                                    if any(principal_declaration(clause) or re.search(r'(?:원금|현재\s*평가금액|현재\s*자산|보유금액).*(?:취소|철회|미정|아니|정도|대략|모르|모릅|몰라|불명|확인\s*필요)', clause)
                                           for clause in re.split(r'(?<!\d)\.|\.(?!\d)|[!?;\n]', statement)):
                                        principal = None  # An ambiguous correction must not silently reuse the old amount.
                                        break
                                contexts = [followup_question or request.question]
                                if followup_question and stated_principal(followup_question) is None:
                                    contexts.append(original_question)
                                preferences = (request.preferences.note, request.preferences.contribution, request.preferences.dividend_goal, *request.preferences.interests)
                                if not all(principal_context_matches(text, principal) for text in contexts) or not all(principal_preference_matches(text, principal) for text in preferences):
                                    principal = None  # Preserve unresolved qualifications when reusing an older amount.
                                answer = build_scenarios(principal, args, **{name: value or 0 for name, value in (funding or {}).items()})
                                if funding is None:
                                    answer = {'status': 'needs_input', 'reason': 'ADDITIONAL_FUNDING_REQUIRED', 'rows': []}
                                if scenario_result and scenario_result['status'] == answer['status'] == 'available':
                                    rows = {(row['annual_return_pct'], row['years']): row for row in [*scenario_result['rows'], *answer['rows']]}
                                    answer['rows'] = list(rows.values())
                                scenario_result = answer
                            except ValueError as error:
                                answer = {'status': 'rejected', 'reason': str(error)[:300]}
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
                                if str(error) not in {'TOOL_NOT_ALLOWED', 'INVALID_TOOL_ARGUMENT', 'INVALID_PASSAGE_OFFSET', 'EMPTY_SOURCE', 'INSUFFICIENT_SOURCE_BODY', 'EXA_FAILED'}:
                                    emit('tool_failed', role=role, tool=tool, code='TOOL_LIMIT' if str(error) == 'TOOL_LIMIT' else type(error).__name__,
                                         counters={'exa_calls': getattr(evidence, 'calls', 0), 'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
                                    raise
                                answer = {'status': 'unavailable' if str(error) in {'EMPTY_SOURCE', 'INSUFFICIENT_SOURCE_BODY', 'EXA_FAILED'} else 'rejected',
                                          'reason': str(error), 'fetchable_urls': sorted(getattr(evidence, 'discovered', set())),
                                          'instruction': '본문을 읽지 못했습니다. 등록된 URL을 정확히 사용하거나 다른 공식 근거를 검색하세요. 실패한 응답을 근거로 인용하지 마세요.'}
                            except BaseException as error:
                                emit('tool_failed', role=role, tool=tool, code=type(error).__name__,
                                     counters={'exa_calls': getattr(evidence, 'calls', 0), 'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
                                raise
                        status = answer.get('status', 'available') if isinstance(answer, dict) else 'available'
                        if tool in {'fetch_source', 'read_portfolio_history'} and status in {'available', 'partial', 'unavailable'}:
                            observed_data['sources' if tool == 'fetch_source' else 'history'] = status
                        if tool == 'fetch_source' and status == 'unavailable' and isinstance(args.get('url'), str):
                            failed_source_urls.add(args['url'])
                        reason = answer.get('reason') if isinstance(answer, dict) else None
                        codes = {'INVALID_TOOL_ARGUMENT', 'TOOL_LIMIT', 'TOOL_NOT_ALLOWED', 'EXA_FAILED', 'EMPTY_SOURCE',
                                 'CURRENT_WEIGHTS_REQUIRED', 'COMPARISON_DATA_UNAVAILABLE', 'NO_COMMON_PRICE_HISTORY',
                                 'NO_COMPARABLE_PRICE_HISTORY', 'BASELINE_PRICE_UNAVAILABLE', 'PRICE_STALE', 'PRICE_UNAVAILABLE',
                                 'CURRENT_AMOUNTS_REQUIRED', 'INVESTMENT_AMOUNT_REQUIRED', 'ADDITIONAL_FUNDING_REQUIRED',
                                 'NO_SELL_ZERO_TARGET', 'NO_SELL_VIOLATION', 'ACCOUNT_OPENING_REQUIRED',
                                 'ACCOUNT_WITHDRAWAL_REVIEW_REQUIRED', 'ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED', 'ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED'}
                        reason_codes = sorted({value.split(':')[0] for value in answer.get('reasons', []) if isinstance(value, str) and value.split(':')[0] in codes}) if isinstance(answer, dict) else []
                        emit('tool_finished', role=role, tool=tool,
                             status=status if status in {'available', 'unavailable', 'rejected', 'no_match', 'found', 'not_found', 'missing', 'stale', 'not_connected', 'ready', 'conditional', 'needs_input', 'infeasible'} else 'unknown',
                             reason_codes=reason_codes,
                             code=reason if reason in codes else 'TOOL_REJECTED' if status in {'rejected', 'unavailable'} else None,
                             counters={'exa_calls': getattr(evidence, 'calls', 0), 'catalog_calls': catalog_calls, 'source_count': len(evidence.sources)})
                        checkpoint()
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(answer, ensure_ascii=False)})
                    continue
                try:
                    raw = json.loads(message.get("content") or "")
                    if not isinstance(raw, dict):
                        raise ValueError('OUTPUT_OBJECT_REQUIRED')
                    research_exhausted = len(failed_source_urls) >= 2 and not (getattr(evidence, 'discovered', set()) - failed_source_urls)
                    source_free_allowed = decision.kind == 'scenario' or research_exhausted or (optimizer.result or {}).get('reason') in {'SERVICE_CUSTOMER_POLICY_CONFLICT', 'SAVED_TARGET_ONLY'}
                    if role == 'opinion':
                        final = FinalReport.model_validate(raw)
                        from .completion import retain_research
                        final = retain_research(final, accepted_final if optimizer.result == accepted_search else None)
                        execution_rejection = None
                        selected = optimizer.validate(final)
                        if final.recommendation:
                            checks = check_constraints(decision, [row.model_dump() for row in final.recommendation.allocations], request, market.catalog)
                        elif not checks:
                            checks = [{'constraint': number, 'kind': rule.kind, 'source': rule.source, 'status': 'unknown',
                                       'reason': 'UNVERIFIED_CONSTRAINT' if rule.kind == 'unverified' else 'NO_ALLOCATION_SELECTED'}
                                      for number, rule in enumerate(decision.constraints, 1)]
                        if final.recommendation and any(check['status'] != 'pass' for check in checks):
                            raise ValueError('CUSTOMER_CONSTRAINT_NOT_SATISFIED: select another candidate; never relax mandatory constraints: ' + json.dumps(checks, ensure_ascii=False))
                        sections = {name: validate_section('question' if source_free_allowed and (decision.kind == 'scenario' or not evidence.sources) and getattr(final, name).limitations else name,
                                    getattr(final, name).model_dump(), evidence.sources) for name in ROLES}
                        published = publish_recommendation(final, request, metrics, evidence.sources, decision)
                        if final.recommendation and published['recommendation']['execution']['status'] == 'infeasible':
                            execution_rejection = published['recommendation']['execution']
                            if final.selected_candidate_id not in execution_feasible:
                                execution_failures[final.selected_candidate_id] = execution_rejection
                            raise ValueError('EXECUTION_INFEASIBLE: choose another candidate or account placement: ' + json.dumps(execution_rejection['reasons']))
                        published['optimization'] = {**optimizer.result, 'selected_candidate_id': final.selected_candidate_id}
                        if not selected and optimizer.result['candidates']:
                            if not execution_rejection and all(row['candidate_id'] in execution_failures for row in optimizer.result['candidates']):
                                execution_rejection = execution_failures[optimizer.result['candidates'][0]['candidate_id']]
                            blocked = any(check['status'] != 'pass' and check.get('reason') != 'NO_ALLOCATION_SELECTED' for check in checks)
                            published['optimization']['reason'] = 'EXECUTION_INFEASIBLE' if execution_rejection else 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' if blocked else 'RATING_BELOW_MINIMUM' if final.withheld_reason == 'quality_failed' else 'RECOMMENDATION_WITHHELD'
                            if execution_rejection:
                                published['execution_rejection'] = execution_rejection
                        if selected:
                            await enrich_report(published, final, market, evidence.sources)
                        if not selected and final.withheld_reason:
                            published['withheld_reason'] = final.withheld_reason
                        if scenario_result is not None:
                            published['scenarios'] = scenario_result
                        if decision.execute_saved_target and followup_execution is not None and not published.get('recommendation'):
                            published['execution'] = followup_execution
                            checks = followup_checks or checks
                        published['completion'] = assess_completion(final, request, decision, published, evidence.sources, context['as_of'])
                        if decision.kind != 'scenario' and not evidence.sources and optimizer.result.get('reason') not in {'SERVICE_CUSTOMER_POLICY_CONFLICT', 'SAVED_TARGET_ONLY'}:
                            published['completion']['reasons'].append('MISSING_RESEARCH_DATA')
                            if published['completion']['status'] != 'needs_input':
                                published['completion']['status'] = 'partial'
                        needs_alternative = not selected and optimizer.result.get('reason') != 'SERVICE_CUSTOMER_POLICY_CONFLICT' and len(getattr(optimizer, 'batches', [])) < 2 and (
                            final.withheld_reason in {'quality_failed', 'customer_failed', 'search_exhausted'} or
                            published['optimization'].get('reason') in {'CUSTOMER_CONSTRAINT_NOT_SATISFIED', 'EXECUTION_INFEASIBLE', 'SERVICE_CRITERIA_NOT_MET', 'NO_COMMON_PRICE_HISTORY', 'NO_COMPARABLE_PRICE_HISTORY'})
                        if needs_alternative:
                            if published['completion']['status'] != 'needs_input':
                                published['completion']['status'] = 'partial'
                            published['completion']['reasons'].append('ALTERNATIVE_SEARCH_NOT_PERFORMED')
                        accepted_report = deepcopy({**published, 'sections': sections})
                        accepted_final = final.model_copy(deep=True)
                        accepted_search = deepcopy(optimizer.result)
                        completion = published['completion']
                        research_tasks = [task for task in completion['tasks'] if task['status'] == 'partial']
                        research_reasons = [reason for reason in completion['reasons'] if reason != 'UNANSWERED_QUESTIONS'
                            and not (reason == 'COMPARISON_DATA_UNAVAILABLE' and optimizer.result.get('reason') == 'CURRENT_WEIGHTS_REQUIRED')]
                        research_work = {'tasks': research_tasks, 'reasons': research_reasons,
                                         'missing_research': completion['missing_research']}
                        recoverable = bool(research_tasks or research_reasons or completion['missing_research'])
                        if recoverable and recovery_rounds < 2 and calls < MAX_MODEL_CALLS - 1:
                            checkpoint()
                            recovery_rounds += 1
                            repairs = 0
                            emit('research_recovery', role=role, attempt=recovery_rounds)
                            messages.append({'role': 'user', 'content': '고객 답변을 기다리는 항목과 별개로 아래의 조사 가능한 과제를 진행하세요. 빠진 답을 보완하고 근거를 추가로 읽거나 다른 후보 조합을 탐색하세요. 이미 확인한 추천과 답은 유지하고 필수 조건은 완화하지 않습니다. 고객에게 같은 질문을 반복하지 말고 확인하지 못한 항목만 남기세요.\n' + json.dumps(research_work, ensure_ascii=False)})
                            continue
                    else:
                        if role == 'question':
                            interpreted = QuestionSection.model_validate(raw)
                            validate_section('question', interpreted.model_dump(exclude={'decision'}), evidence.sources)
                            decision = validate_decision(interpreted.decision, request, market.catalog, question_text=followup_question or request.question)
                            if prior_report:
                                inherited = Decision.model_validate(prior_report.get('decision') or {})
                                rules = {}
                                resolved_sources = ({rule.source for rule in decision.constraints if rule.kind != 'unverified'}
                                                    - {rule.source for rule in decision.constraints if rule.kind == 'unverified'})
                                for rule in [*inherited.constraints, *decision.constraints]:
                                    if rule.kind == 'unverified' and rule.source in resolved_sources:
                                        continue
                                    identity = rule.model_dump(mode='json', exclude={'source'})
                                    if rule.kind == 'unverified':
                                        identity['source'] = rule.source
                                    for name in ('instrument_ids', 'accounts', 'markets'):
                                        identity[name] = sorted(identity[name])
                                    rules.setdefault(json.dumps(identity, sort_keys=True), rule)
                                if inherited.kind == 'new_money' and not any(rule.kind == 'no_sell' and not rule.instrument_ids for rule in rules.values()):
                                    from .decision import Constraint
                                    rule = Constraint(kind='no_sell', source='기존 신규 자금 전용 결정의 매도 금지')
                                    rules[rule.model_dump_json()] = rule
                                decision.constraints = list(rules.values())
                                decision.objectives = decision.objectives or inherited.objectives
                                decision.funding_text = zero_funding_quote(request, decision) or decision.funding_text or inherited.funding_text
                                if followup_question:
                                    decision.questions = [question for question in decision.questions if question in followup_question] or [followup_question]
                                # Each extraction is bounded and validated already; merging
                                # must not discard inherited rules at the extraction limit.
                                context['active_decision'] = decision.model_dump(mode='json')
                                saved_target_verified = prior_report.get('parent_state') != 'failed' and (prior_report.get('completion') or {}).get('status') == 'complete'
                                if decision.kind in {'rebalance', 'new_money'} and prior_report.get('recommendation') and saved_target_verified:
                                    rows = [{name: row[name] for name in ('instrument_id', 'account', 'weight_pct')}
                                            for row in prior_report['recommendation'].get('allocations', [])]
                                    if rows:
                                        try:
                                            followup_execution = execution_tool(request, {'allocations': rows}, decision, market.catalog)
                                            current_checks = check_constraints(decision, rows, request, market.catalog)
                                        except ValueError:
                                            followup_execution = {'status': 'needs_input', 'rows': [], 'accounts': [],
                                                'reasons': ['SAVED_TARGET_UNAVAILABLE'], 'assumptions': [], 'months': None,
                                                'required_additional_krw': None, 'additional_krw': '0', 'monthly_krw': None,
                                                'allocation_scope': 'total_portfolio'}
                                            current_checks = []
                                        if any(check['status'] != 'pass' for check in current_checks):
                                            followup_execution.update(status='needs_input' if any(check['status'] == 'unknown' for check in current_checks) else 'infeasible',
                                                rows=[], reasons=['CUSTOMER_CONSTRAINT_NOT_SATISFIED'])
                                        context['saved_target_execution'] = followup_execution
                                        followup_checks = current_checks
                                        if decision.execute_saved_target and followup_execution['status'] in {'ready', 'conditional'}:
                                            optimizer.result = {'status': 'not_requested', 'candidates': [], 'reason': 'SAVED_TARGET_ONLY'}
                            if decision.kind == 'scenario':
                                optimizer.result = {'status': 'not_requested', 'candidates': [], 'reason': 'SCENARIO_ONLY'}
                            optimizer.decision = decision
                            raw = interpreted.model_dump(exclude={'decision'})
                        if role == 'portfolio' and optimizer.result is None:
                            raise ValueError('Before completing portfolio, call optimize_portfolios once with 10 distinct ETF combinations; obtain at least 4 distinct registered ETFs first using search_etfs')
                        sections[role] = validate_section('question' if source_free_allowed and (decision.kind == 'scenario' or not evidence.sources) and raw.get('limitations') else role, raw, evidence.sources)
                    emit('stage_completed', role=role)
                    checkpoint()
                    break
                except (ValueError, TypeError) as error:
                    repairs += 1
                    emit('validation_failed', role=role, attempt=repairs,
                         code='OUTPUT_JSON' if isinstance(error, json.JSONDecodeError) else 'OUTPUT_SCHEMA' if isinstance(error, ValidationError) else 'OUTPUT_CONTRACT')
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
