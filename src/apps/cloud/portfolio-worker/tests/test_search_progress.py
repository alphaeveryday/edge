from datetime import date
from itertools import combinations

from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.market_data import MarketData
from edge_portfolio.optimizer import Optimizer
from test_market_data import record


async def test_two_overlapping_batches_do_not_count_as_twenty_distinct_alternatives():
    request = AnalysisInput(holdings=[{'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 100}], horizon='10년 이상')
    market = MarketData(request, date(2026, 10, 12), lambda *_: record())
    market.catalog = {f'KR:{key}': {'ticker': key, 'market': 'KR'} for key in 'ABCDE'}
    groups = [list(group) for size in (1, 2, 3) for group in combinations(market.catalog, size)]
    optimizer = Optimizer(market, request)
    first = await optimizer.tool({'combinations': groups[:10]})
    assert first['search_progress']['unique_combinations'] == 10
    repeated = await optimizer.tool({'combinations': groups[:10]})
    assert repeated['search_progress'] == first['search_progress']
    second = await optimizer.tool({'combinations': groups[:8] + groups[10:12]})
    assert second['search_progress']['unique_combinations'] == 12
    assert not second['search_progress']['alternatives_explored']
    third = await optimizer.tool({'combinations': groups[10:20]})
    assert third['search_progress']['unique_combinations'] == 20
    assert third['search_progress']['alternatives_explored']
    assert third['search_progress']['unique_etfs'] == 5


async def test_failed_price_search_still_reports_its_actual_search_scope():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    def unavailable(*_): raise ValueError('PRICE_UNAVAILABLE')
    market = MarketData(request, date(2026, 10, 12), unavailable)
    market.catalog = {f'KR:{key}': {'ticker': key, 'market': 'KR'} for key in 'ABCDE'}
    result = await Optimizer(market, request).tool({'combinations': [list(pair) for pair in combinations(market.catalog, 2)]})
    assert result['status'] == 'unavailable'
    assert result['search_progress']['unique_combinations'] == 10
    assert result['search_progress']['remaining_batches'] == 3


async def test_agent_continues_after_two_batches_with_only_twelve_unique_combinations(monkeypatch):
    import json
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence
    instances = []
    class Search:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET',
                'search_progress': {'unique_combinations': 12, 'unique_etfs': 5, 'remaining_batches': 2, 'alternatives_explored': False}}
            self.batches = {'first', 'second'}
            instances.append(self)
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Search)
    stages, opinions = [], []
    async def model(payload):
        role = stages[-1]
        content = {**final_payload(), 'recommendation': None, 'withheld_reason': 'search_exhausted'} if role == 'opinion' else final_payload()[role]
        if role == 'question': content['decision'] = {}
        if role == 'opinion':
            opinions.append(1)
            if len(opinions) == 2:
                assert 'ALTERNATIVE_SEARCH_NOT_PERFORMED' in payload['messages'][-1]['content']
                instances[0].result['search_progress'].update(unique_combinations=20, alternatives_explored=True)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    await run_analysis(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), 'test', stages.append, model_call=model, evidence=Evidence())
    assert len(opinions) == 2
