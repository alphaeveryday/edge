"""Answer a follow-up from the saved report when it can; otherwise revisit it through the full analysis."""
import asyncio
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal

import httpx
from pydantic import Field, ValidationError

from .agent import MODEL, MODEL_CALL_TIMEOUT_SECONDS, PROMPTS, run_analysis
from .catalog import instruments
from .contracts import AnalysisInput, Section, StrictModel, validate_section
from .recommendation import answer_amount_error
from .structured_output import structured_call

LIGHT_TIMEOUT_SECONDS = 150
LIGHT_MODEL_CALLS = 3
# 모델에 보여 줄 부모 보고서 칸. 출처 본문(sources)은 크고 설명에 필요 없어 뺀다 — 섹션 evidence 에 인용 문장이 이미 있다.
REPORT_FIELDS = ('sections', 'recommendation', 'execution', 'scenarios', 'past', 'period', 'answer', 'final_note', 'verdict',
                 'completion', 'decision', 'current_evaluation', 'insights', 'constraint_results', 'as_of')


class FollowupInput(AnalysisInput):
    # Two independently bounded customer messages; the public analysis limit stays 1,000.
    question: str = Field(max_length=2100)
    funding_questions: list[str] = Field(exclude=True, min_length=2, max_length=2)


class LightReply(StrictModel):
    route: Literal['explain', 'calculate', 'rerun']
    reason: str = Field(max_length=200)
    answer: Section | None = None


def new_instruments(text, report):
    """답에 나온 종목 이름·코드 중 부모 보고서와 입력 어디에도 없는 것. 설명 답이 검증을 거치지 않은 새 종목을 권하지 못하게 한다."""
    norm = lambda value: re.sub(r'\s', '', value).upper()
    known, said = norm(json.dumps(report, ensure_ascii=False)), norm(text)
    found = []
    for row in instruments().values():
        name = norm(row['name'])
        named = len(name) >= 4 and name in said and name not in known
        coded = re.search(rf'(?<![0-9A-Z]){re.escape(norm(row["ticker"]))}(?![0-9A-Z])', said) and norm(row['ticker']) not in known \
            if len(row['ticker']) >= 3 else False
        if named or coded:
            found.append(row['name'])
    return found


async def light_answer(question, report, key, *, model_call=None, conversation=None, emit=lambda event, **fields: None):
    """저장된 보고서로 답할 수 있는 질문(explain)이면 모델 한 번으로 답한다. 고른 route 와 답(없으면 None)을 돌려준다."""
    skill = (PROMPTS / 'followup' / 'SKILL.md').read_text(encoding='utf8')
    parent = {field: report[field] for field in REPORT_FIELDS if field in report}
    if (parent.get('recommendation') or {}).get('historical_performance'):
        parent['recommendation'] = {k: v for k, v in parent['recommendation'].items() if k != 'historical_performance'}
    request = AnalysisInput.model_validate(report['input'], context={'persisted_input': True})
    messages = [{'role': 'system', 'content': skill},
                {'role': 'user', 'content': json.dumps({'input': report['input'], 'parent_report': parent,
                    'conversation': conversation or [], 'question': question}, ensure_ascii=False)}]
    async with httpx.AsyncClient(timeout=MODEL_CALL_TIMEOUT_SECONDS) as client:
        call = model_call or (lambda payload: structured_call(client, key, payload, LightReply))
        for attempt in range(1, LIGHT_MODEL_CALLS + 1):
            emit('model_started', role='light', call=attempt)
            # 첫 호출은 생각을 켜고(분류가 중요하다), 다시 묻는 호출은 submit_report 를 강제한다.
            payload = {'model': MODEL, 'messages': messages, 'tools': [], 'tool_choice': 'auto' if attempt == 1 else 'none',
                       'max_tokens': 4000, 'stream': False, **({'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'} if attempt == 1 else {})}
            try:
                async with asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS):
                    result = await call(payload)
                reply = LightReply.model_validate_json(result['choices'][0]['message']['content'])
                if reply.route != 'explain':
                    return reply.route, None, reply.reason
                if reply.answer is None:
                    raise ValueError('explain 이면 answer 를 써야 해요.')
                answer = validate_section('question', reply.answer.model_dump(), report.get('sources', []))
                problem = answer_amount_error({**report, 'answer': ' '.join(answer['paragraphs'])}, request)
                if problem:
                    raise ValueError(problem)
                if added := new_instruments(' '.join(answer['paragraphs']), parent | {'input': report['input'], 'conversation': conversation or []}):
                    emit('light_new_instrument', names=added[:5])
                    return 'rerun', None, 'NEW_INSTRUMENT'  # 새 종목 추천은 전체 분석이 검증한다
                return 'explain', answer, reply.reason
            except (ValueError, ValidationError, KeyError, TypeError) as error:
                emit('validation_failed', role='light', attempt=attempt, code=type(error).__name__)
                messages.append({'role': 'user', 'content': f'제출한 답을 받을 수 없어요: {str(error)[:600]} 같은 형식으로 submit_report 하나만 다시 제출하세요.'})
    raise ValueError('LIGHT_ANSWER_INVALID')


async def answer_question(question, report, key, *, on_stage=None, on_event=None, on_checkpoint=None,
                          revision_id=None, model_call=None, evidence=None, market=None, conversation=None, light=False):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError('INVALID_FOLLOWUP_QUESTION')
    route, light_events = None, []
    if light:
        # 설명형 질문은 저장된 보고서로 바로 답한다. 계산·새 구성·입력 정정이거나 바로 답하지 못하면 아래 전체 분석으로 간다
        # (그때 단계가 question 으로 바뀌어 화면이 '처음부터 다시 분석해요'를 보여 준다).
        started = time.monotonic()
        def emit(event, **fields):
            entry = {'event': event, 'elapsed_seconds': round(time.monotonic() - started, 2), **fields}
            light_events.append(entry)
            if on_event:
                on_event(entry)
        skill = (PROMPTS / 'followup' / 'SKILL.md').read_text(encoding='utf8')
        emit('run_started', model=MODEL, prompt_version=hashlib.sha256(skill.encode()).hexdigest(),
             code_version=hashlib.sha256(PROMPTS.parent.joinpath('followup.py').read_bytes()).hexdigest(),
             limits={'model_calls': LIGHT_MODEL_CALLS, 'elapsed_seconds': LIGHT_TIMEOUT_SECONDS})
        try:
            async with asyncio.timeout(LIGHT_TIMEOUT_SECONDS):
                route, answer, reason = await light_answer(question, report, key, model_call=model_call, conversation=conversation, emit=emit)
        except Exception as error:  # 가벼운 답의 실패는 고객 실패가 아니다. 지금까지처럼 전체 분석으로 답한다.
            route, answer, reason = 'rerun', None, 'LIGHT_FAILED'
            emit('light_failed', code=type(error).__name__)
        emit('light_routed', route=route, reason=reason[:200])
        if route == 'explain':
            cited = {item['source_id'] for item in answer['evidence']}
            return {'answer': answer, 'sources': [source for source in report.get('sources', []) if source.get('id') in cited],
                    'as_of': datetime.now(timezone.utc).isoformat(), 'parent_as_of': report.get('as_of'),
                    'status': 'complete', 'revision_id': revision_id, 'revision': None, 'route': 'explain',
                    'elapsed_seconds': round(time.monotonic() - started, 2),
                    'diagnostics': {'events': light_events, 'termination_reason': 'COMPLETED', 'route': 'explain', 'model': MODEL,
                                    'model_calls': sum(event['event'] == 'model_started' for event in light_events)}}
    original = AnalysisInput.model_validate(report['input'], context={'persisted_input': True})
    payload = original.model_dump(mode='json')
    payload['question'] = original.question + '\n\n후속 질문: ' + question
    payload['funding_questions'] = [original.question, question]
    request = FollowupInput.model_validate(payload, context={'persisted_input': True})
    revised = await run_analysis(request, key, on_stage or (lambda role: None),
        model_call=model_call, evidence=evidence, market=market, on_event=on_event,
        on_checkpoint=on_checkpoint, prior_report=deepcopy(report), followup_question=question,
        conversation=conversation)
    diagnostics = revised['diagnostics']
    if route:
        diagnostics = {**diagnostics, 'route': route, 'light_events': light_events}
    return {'answer': revised['sections']['opinion'], 'sources': revised['sources'],
            'as_of': revised['as_of'], 'parent_as_of': report.get('as_of'),
            'status': revised['completion']['status'], 'revision_id': revision_id,
            'revision': revised, 'elapsed_seconds': revised['elapsed_seconds'],
            **({'route': route} if route else {}), 'diagnostics': diagnostics}
