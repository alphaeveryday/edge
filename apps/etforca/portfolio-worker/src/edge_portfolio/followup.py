"""Answer a follow-up from the saved report when it can; otherwise revisit it through the full analysis."""
import asyncio
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from typing import Literal

import httpx
from pydantic import Field, ValidationError

from .agent import MODEL, MODEL_CALL_TIMEOUT_SECONDS, PROMPTS, run_analysis
from .catalog import instruments
from .contracts import AnalysisInput, Section, StrictModel, validate_section
from .recommendation import answer_amount_error
from .structured_output import structured_call

# 실패하면 이어서 전체 분석(최대 600초)을 돈다. 둘을 합쳐도 실행 중 작업 만료(시작 후 11분, store.expire)보다 짧아야 한다.
LIGHT_TIMEOUT_SECONDS = 45
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
    reason: str = Field(max_length=200)  # 분류를 돕는 칸. 저장하지 않는다
    answer: Section | None = None


# 미국 ETF 티커 중 답에 흔히 쓰는 금융 용어와 같은 것(2026-11 목록에서 확인). 이 글자가 나왔다고 새 종목을 권한 것으로 보지 않는다.
TERMS = {'REIT', 'ESG', 'IPO', 'ROE', 'EPS', 'USD'}
NUMBER = re.compile(r'\d[\d,]*(?:\.\d+)?')


def _norm(value):
    return re.sub(r'\s', '', value).upper()


@lru_cache(maxsize=1)
def _etf_tickers():
    tickers = sorted({row['ticker'] for row in instruments().values() if row['kind'] == 'etf' and len(row['ticker']) >= 3} - TERMS, key=len, reverse=True)
    return re.compile(r'(?<![A-Za-z0-9])(' + '|'.join(map(re.escape, tickers)) + r')(?![A-Za-z0-9])')


def _named(text, name):
    """text(공백 없앤 대문자)에 name 이 더 긴 종목 이름의 일부가 아닌 채로 나오는가. 'TIGER 미국나스닥100커버드콜' 안의 'TIGER 미국나스닥100'은 아니다."""
    longer = [other for other in (_norm(row['name']) for row in instruments().values()) if len(other) > len(name) and name in other]
    at = text.find(name)
    while at >= 0:
        if not any(text.startswith(other, at - other.index(name)) for other in longer if other.index(name) <= at):
            return True
        at = text.find(name, at + 1)
    return False


def new_instruments(text, known):
    """답에 나온 종목 중 부모 보고서·입력·대화·질문(known) 어디에도 없는 것. 설명 답이 검증을 거치지 않은 새 종목을 권하지 못하게 한다.
    새로 담을 종목은 ETF로만 추천하므로 티커는 ETF 것만, 원문 대문자 그대로 본다('TDF', 'MSCI' 같은 용어가 미국 주식 티커와 겹친다)."""
    said, seen = _norm(text), _norm(known)
    found = [row['name'] for row in instruments().values()
             if len(_norm(row['name'])) >= 4 and _norm(row['name']) in said and _named(said, _norm(row['name'])) and not _named(seen, _norm(row['name']))]
    known_tickers = set(_etf_tickers().findall(known))
    by_ticker = {row['ticker']: row['name'] for row in instruments().values() if row['kind'] == 'etf'}
    return found + [by_ticker[ticker] for ticker in dict.fromkeys(_etf_tickers().findall(text)) if ticker not in known_tickers]


def unchecked_numbers(text, known, report, request):
    """답의 숫자가 보고서에 있는지. 원화는 기존 금액 검사(억·만만 쓰고 '원'을 뺀 표기도), %·달러는 보고서·대화·질문에 같은 값이 있어야 한다."""
    won = re.sub(r'(\d[\d,.]*\s*(?:억(?:\s*\d[\d,.]*\s*만)?|만))(?!\s*원)', r'\1 원', text)
    problems = [answer_amount_error({**report, 'answer': won}, request)]
    values = {Decimal(number.replace(',', '')) for number in NUMBER.findall(known)} | {Decimal(0), Decimal(100)}
    for match in re.finditer(r'(\d[\d,]*(?:\.\d+)?)\s*(?:%|퍼센트|달러)|\$\s*(\d[\d,]*(?:\.\d+)?)', text):
        stated = (match[1] or match[2]).replace(',', '')
        digits = len(stated.split('.')[1]) if '.' in stated else 0
        if not any(round(value, digits) == Decimal(stated) for value in values):
            problems.append(f'{match.group().strip()} 은(는) 보고서에 없는 숫자예요. 보고서·대화에 적힌 값만 그대로 쓰세요.')
    return ' '.join(problem for problem in problems if problem)


def cited_pairs(node):
    """부모 보고서 섹션이 실제로 인용한 (source_id, passage_id). 모델은 출처 본문을 보지 못하므로 이것만 다시 인용할 수 있다."""
    if isinstance(node, dict):
        pairs = {(node['source_id'], node['passage_id'])} if {'source_id', 'passage_id'} <= node.keys() else set()
        return pairs.union(*(cited_pairs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(cited_pairs(value) for value in node))
    return set()


async def light_answer(question, report, key, *, model_call=None, conversation=None, emit=lambda event, **fields: None):
    """저장된 보고서로 답할 수 있는 질문(explain)이면 모델 한 번으로 답한다. 고른 route 와 답(없으면 None)을 돌려준다."""
    skill = (PROMPTS / 'followup' / 'SKILL.md').read_text(encoding='utf8')
    parent = {field: report[field] for field in REPORT_FIELDS if field in report}
    if (parent.get('recommendation') or {}).get('historical_performance'):
        parent['recommendation'] = {k: v for k, v in parent['recommendation'].items() if k != 'historical_performance'}
    request = AnalysisInput.model_validate(report['input'], context={'persisted_input': True})
    known = json.dumps({**parent, 'input': report['input'], 'conversation': conversation or [], 'question': question}, ensure_ascii=False)
    allowed = cited_pairs(parent)
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
                    return reply.route, None
                if reply.answer is None:
                    raise ValueError('explain 이면 answer 를 써야 해요.')
                answer = validate_section('question', reply.answer.model_dump(), report.get('sources', []))
                if stray := [f"{e['source_id']}/{e['passage_id']}" for e in answer['evidence'] if (e['source_id'], e['passage_id']) not in allowed]:
                    raise ValueError(f"{', '.join(stray)} 은(는) 보고서 섹션이 인용한 근거가 아니에요. 보고서 evidence 의 source_id·passage_id 만 쓰거나 빈 배열로 두세요.")
                if problem := unchecked_numbers(' '.join(answer['paragraphs']), known, report, request):
                    raise ValueError(problem)
                if added := new_instruments(' '.join(answer['paragraphs']), known):
                    emit('light_new_instrument', names=added[:5])
                    return 'rerun', None  # 새 종목 추천은 전체 분석이 검증한다
                return 'explain', answer
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
                route, answer = await light_answer(question, report, key, model_call=model_call, conversation=conversation, emit=emit)
        except Exception as error:  # 가벼운 답의 실패는 고객 실패가 아니다. 지금까지처럼 전체 분석으로 답한다.
            route, answer = 'rerun', None
            emit('light_failed', code=type(error).__name__)
        emit('light_routed', route=route)  # 모델이 적은 reason 은 고객 API(diagnostics)로 나가므로 남기지 않는다
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
