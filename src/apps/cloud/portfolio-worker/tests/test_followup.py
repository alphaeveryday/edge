import json
from edge_portfolio.followup import answer_question
from report_fixtures import section


async def test_followup_receives_the_current_score_and_recommended_portfolio(monkeypatch):
    report = {'sections': {}, 'sources': [{'id': 's1', 'passages': [{'id': 'p1', 'text': 'evidence'}]}],
              'metrics': {}, 'as_of': '2026-10-12', 'current_evaluation': {'overall': 3.5},
              'recommendation': {'title': '추천안', 'evaluation': {'overall': 4}}, 'insights': []}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': json.dumps(section())}}]}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, headers, json):
            context = __import__('json').loads(json['messages'][1]['content'])['report']
            assert context['current_evaluation']['overall'] == 3.5
            assert context['recommendation']['evaluation']['overall'] == 4
            return Response()
    monkeypatch.setattr('edge_portfolio.followup.httpx.AsyncClient', Client)
    answer = await answer_question('현재 점수가 왜 이 점수인가요?', report, 'test')
    assert len(answer['answer']['paragraphs']) == 3
