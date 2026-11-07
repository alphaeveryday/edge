"""추가 질문의 가벼운 경로: 받은 보고서로 답할 수 있는 질문은 전체 분석(p50 5분, 모델 수십 회)을 다시 돌리지 않는다."""
import json

import httpx
import pytest

from edge_portfolio.followup import answer_question

QUESTION = 'VOO는 왜 그대로 두라는 거예요?'
REPORT = {
    'input': {'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 10000000}],
              'horizon': '10년 이상', 'question': '지금 구성 괜찮나요?'},
    'as_of': '2026-11-01T00:00:00+00:00',
    'sources': [{'id': 's1', 'passages': [{'id': 'p1', 'text': 'official source quotation'}, {'id': 'p2', 'text': 'unrelated passage'}]}],
    'sections': {'opinion': {'paragraphs': ['VOO를 그대로 두세요.'] * 3, 'evidence': [{'source_id': 's1', 'passage_id': 'p1', 'quote': 'official source quotation'}], 'limitations': []}},
    'recommendation': {'allocations': [{'instrument_id': 'US:VOO', 'name': 'VOO', 'account': '일반 계좌', 'weight_pct': 100}],
                       'alternatives': ['TIGER 미국나스닥100커버드콜(합성)']},
}
PARAGRAPHS = ['VOO는 미국 대형주 500개에 나눠 담는 상품이라 지금 구성의 중심 역할을 해요.',
              '보고서는 이미 넓게 분산돼 있어서 다른 상품으로 바꿀 이유가 적다고 봤어요.',
              '지금 가진 1,000만 원을 그대로 두는 것이 이 분석의 결론이에요.']


def reply(route, paragraphs=None, evidence=()):
    answer = {'paragraphs': paragraphs, 'evidence': list(evidence), 'limitations': []} if paragraphs else None
    return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps({'route': route, 'reason': 'test', 'answer': answer}, ensure_ascii=False)}}]}


@pytest.fixture
def no_rerun(monkeypatch):
    async def analyze(*args, **kwargs):
        raise AssertionError('설명 질문이 전체 분석을 다시 돌렸어요')
    monkeypatch.setattr('edge_portfolio.followup.run_analysis', analyze)


@pytest.fixture
def rerun(monkeypatch):
    calls = []
    async def analyze(request, key, on_stage, **kwargs):
        calls.append(kwargs['followup_question'])
        on_stage('question')
        return {'sections': {'opinion': {'paragraphs': ['다시 분석한 답']}}, 'sources': [], 'as_of': '2026-11-07',
                'completion': {'status': 'complete'}, 'elapsed_seconds': 300, 'diagnostics': {'termination_reason': 'COMPLETED'}}
    monkeypatch.setattr('edge_portfolio.followup.run_analysis', analyze)
    return calls


async def test_an_explanation_is_answered_from_the_saved_report_in_one_model_call(no_rerun):
    payloads, events = [], []
    async def model(payload):
        payloads.append(payload)
        return reply('explain', PARAGRAPHS, [{'source_id': 's1', 'passage_id': 'p1'}])
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True, on_event=events.append,
                                   conversation=[{'question': '앞 질문', 'answer': ['앞 답']}])
    assert len(payloads) == 1 and payloads[0]['tools'] == []
    context = json.loads(payloads[0]['messages'][1]['content'])
    assert context['question'] == QUESTION and context['conversation'][0]['question'] == '앞 질문'
    assert 'sources' not in context['parent_report']  # 출처 본문은 보내지 않는다 — 섹션의 인용으로 충분하다
    assert result['route'] == 'explain' and result['status'] == 'complete' and result['revision'] is None
    assert result['answer']['paragraphs'] == PARAGRAPHS
    assert result['answer']['evidence'][0]['quote'] == 'official source quotation'
    assert [source['id'] for source in result['sources']] == ['s1']
    assert result['diagnostics']['model_calls'] == 1
    started = next(event for event in events if event['event'] == 'run_started')
    assert {'model', 'prompt_version', 'code_version', 'limits'} <= started.keys()  # 워커가 이 네 칸을 그대로 읽는다


@pytest.mark.parametrize('route', ['calculate', 'rerun'])
async def test_calculations_and_new_compositions_still_run_the_full_analysis(rerun, route):
    stages = []
    async def model(payload):
        return reply(route)
    result = await answer_question('10년 뒤 얼마예요?', REPORT, 'test', model_call=model, light=True, on_stage=stages.append)
    assert rerun == ['10년 뒤 얼마예요?'] and stages == ['question']  # 단계가 question 으로 바뀌어 화면이 '다시 분석'을 알린다
    assert result['route'] == route and result['revision']['sections']['opinion'] == result['answer']
    assert result['diagnostics']['route'] == route


async def test_an_amount_missing_from_the_report_is_sent_back_before_it_reaches_the_customer(no_rerun):
    sent = []
    async def model(payload):
        sent.append(payload)
        wrong = PARAGRAPHS[:2] + ['10년 뒤에는 3,000만 원이 될 거예요.']
        return reply('explain', wrong if len(sent) == 1 else PARAGRAPHS)
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert len(sent) == 2 and '3,000만 원' in sent[1]['messages'][-1]['content']
    assert sent[1]['tool_choice'] == 'none'  # 다시 묻는 호출은 submit_report 를 강제한다
    assert result['route'] == 'explain' and result['answer']['paragraphs'] == PARAGRAPHS


async def test_an_answer_that_never_passes_the_checks_falls_back_to_the_full_analysis(rerun):
    async def model(payload):
        return reply('explain', ['너무 짧아요.'] * 3)
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert rerun == [QUESTION] and result['route'] == 'rerun'
    assert any(event['event'] == 'light_failed' for event in result['diagnostics']['light_events'])


async def test_a_model_outage_on_the_light_path_is_not_a_customer_failure(rerun):
    async def model(payload):
        raise httpx.ConnectError('down')
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert rerun == [QUESTION] and result['status'] == 'complete'


async def test_an_explanation_cannot_recommend_an_instrument_the_report_never_checked(rerun):
    async def model(payload):
        return reply('explain', PARAGRAPHS[:2] + ['대신 QQQM을 새로 담아 보는 것도 좋아요.'])
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert rerun == [QUESTION] and result['route'] == 'rerun'
    assert any(event['event'] == 'light_new_instrument' for event in result['diagnostics']['light_events'])


async def test_without_the_light_flag_the_model_is_not_asked_to_route(rerun):
    async def model(payload):
        raise AssertionError('light=False 는 분류 호출을 하지 않는다')
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model)
    assert rerun == [QUESTION] and 'route' not in result


@pytest.mark.parametrize('wrong', [
    '연 12% 수익을 기대할 수 있어요.',        # 보고서에 없는 수익률
    '10년 뒤에는 1억이 될 거예요.',            # '원' 없이 쓴 금액도 금액 검사를 받는다
    '미국 주식 1,000달러어치를 더 사면 돼요.',  # 달러 금액
])
async def test_numbers_without_won_are_checked_against_the_report_too(no_rerun, wrong):
    sent = []
    async def model(payload):
        sent.append(payload)
        return reply('explain', PARAGRAPHS[:2] + [wrong] if len(sent) == 1 else PARAGRAPHS)
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert len(sent) == 2 and result['answer']['paragraphs'] == PARAGRAPHS


async def test_an_answer_can_only_cite_what_the_report_already_cited(no_rerun):
    # 모델은 출처 본문을 보지 못한다. 보고서가 인용하지 않은 문단을 근거로 붙이면 엉뚱한 인용이 고객에게 보인다.
    sent = []
    async def model(payload):
        sent.append(payload)
        return reply('explain', PARAGRAPHS, [{'source_id': 's1', 'passage_id': 'p2' if len(sent) == 1 else 'p1'}])
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert len(sent) == 2 and 's1/p2' in sent[1]['messages'][-1]['content']
    assert result['answer']['evidence'][0]['passage_id'] == 'p1'


async def test_a_shorter_fund_name_inside_a_known_longer_one_is_still_new(rerun):
    # 보고서에는 'TIGER 미국나스닥100커버드콜(합성)'만 있다. 'TIGER 미국나스닥100'을 새로 권하면 검증을 거치지 않은 추천이다.
    async def model(payload):
        return reply('explain', PARAGRAPHS[:2] + ['대신 TIGER 미국나스닥100을 새로 사 보세요.'])
    result = await answer_question(QUESTION, REPORT, 'test', model_call=model, light=True)
    assert rerun == [QUESTION] and result['route'] == 'rerun'
    flagged = next(event for event in result['diagnostics']['light_events'] if event['event'] == 'light_new_instrument')
    assert flagged['names'] == ['TIGER 미국나스닥100']


async def test_finance_terms_that_happen_to_be_tickers_do_not_force_a_rerun(no_rerun):
    # TDF·MSCI 는 미국 주식 티커, REIT·ESG 는 미국 ETF 티커와 글자가 같다. 용어 설명 답을 5분짜리 재분석으로 보내면 안 된다.
    question = 'TDF랑 리츠는 뭐가 달라요?'
    answer = ['TDF는 은퇴 시점에 맞춰 주식과 채권 비중을 스스로 바꾸는 상품이에요.',
              'REIT(리츠)는 부동산에서 나오는 임대료를 나눠 주는 상품이고 MSCI 지수와도 상관이 없어요.',
              '지금 보고서는 VOO를 그대로 두라고 했고 두 상품은 새로 권하지 않았어요.']
    async def model(payload):
        return reply('explain', answer)
    result = await answer_question(question, REPORT, 'test', model_call=model, light=True)
    assert result['route'] == 'explain'
