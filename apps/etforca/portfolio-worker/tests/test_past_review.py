"""지난 분석 재검토: 새 코드의 실패 지점을 단계별로 세되, 고객 입력은 출력으로 나가지 않고 DB 에는 쓰지 않는다.
모든 입력은 직접 만든 가짜 자료다."""
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from edge_portfolio import past_review, store

AS_OF = date(2026, 10, 23)
BUSY = past_review.busy
PRIVATE = ['PRIVATE', '1234567', '7654321', '069500', 'VOO', 'KODEX', '비밀']


def job(number):
    return f'{number:032x}'


def payload(holdings, **changes):
    return {'holdings': holdings, 'horizon': '10년 이상', 'question': 'PRIVATE 질문 비밀',
            'preferences': {'contribution': '이미 다 매수한 상태에요', 'accounts': ['일반 계좌', 'ISA'], 'note': '비밀 메모'}, **changes}


KR = {'name': 'KODEX 200', 'kind': 'etf', 'instrument_id': 'KR:069500', 'account': 'ISA', 'amount_krw': 1234567}
CASH = {'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 7654321}
US = {'name': 'VOO', 'kind': 'etf', 'instrument_id': 'US:VOO', 'amount_krw': 1234567}


def record(currency):
    days = pd.bdate_range(end=AS_OF - timedelta(days=1), periods=30)
    frame = pd.DataFrame({'Adj Close': range(100, 130), 'Close': range(100, 130)}, index=days, dtype=float)
    return {'frame': frame, 'currency': currency}


@pytest.fixture(autouse=True)
def idle(monkeypatch):
    monkeypatch.setattr(past_review, 'busy', lambda: False)


@pytest.fixture
def prices(monkeypatch):
    calls = []
    async def fetch(symbol, as_of, **options):
        calls.append(symbol)
        if symbol == 'VOO':
            raise ValueError('PRICE_STALE')
        return record('KRW')
    monkeypatch.setattr('edge_portfolio.market_data.fetch_prices', fetch)
    return calls


async def test_each_stage_names_the_failure_point_and_never_prints_customer_input(prices):
    # 지난 추천이 ISA 돈을 일반 계좌의 미국 상장 ETF 로 옮겼다 — 8차 계좌 규칙에서 실행할 수 없다
    moved = {'recommendation': {'allocations': [{'instrument_id': 'US:VOO', 'account': '일반 계좌', 'weight_pct': 100.0}]}}
    rows = [
        {'id': job(1), 'input': payload([KR, CASH], draft_id='00000000-0000-0000-0000-000000000001'), 'result': moved},
        {'id': job(2), 'input': payload([KR, CASH], draft_id='00000000-0000-0000-0000-000000000002'), 'result': None},  # 같은 입력
        {'id': job(3), 'input': payload([{**KR, 'instrument_id': 'KR:999999'}], preferences={'note': '비밀'}), 'result': None},
        {'id': job(4), 'input': payload([KR], horizon=''), 'result': None},
        {'id': job(5), 'input': payload([KR, US], question='', constraints=[{'kind': 'no_sell'}]), 'result': None},
        {'id': job(6), 'input': payload([CASH], question=''), 'result': None},
    ]
    summary = await past_review.review(rows, as_of=AS_OF, pause=0)
    stages = summary['stages']
    assert summary['distinct_inputs'] == 5, 'A re-saved draft is the same customer scenario and is reviewed once'
    assert stages['contract_persisted'] == {'OK': 4, 'horizon:literal_error': 1}
    assert stages['contract_resubmit']['CONTRIBUTION_CHOICE_REQUIRED'] == 1, 'The submit gate (api.admit) must be the same judgment'
    assert stages['conditions'] == {'TEXT_WITHOUT_CHIPS': 3, 'HAS_CHIPS': 1}
    assert stages['catalog'] == {'OK': 3, 'MISSING_ETF': 1}
    assert stages['execution_past_recommendation'] == {'infeasible': 1, 'infeasible:ACCOUNT_TRANSFER_NOT_ALLOWED:ISA': 1}
    assert stages['execution_current'] == {'ready': 4}, 'Keeping today\'s holdings must always be executable'
    assert stages['price_analysis'] == {'OK': 1, 'CURRENT_INSTRUMENT_UNAVAILABLE': 1, 'BASELINE_PRICE_UNAVAILABLE': 1,
                                        'CASH_ONLY': 1}, 'Cash has no baseline price to fail'
    assert sorted(prices) == ['069500.KS', 'KRW=X', 'VOO'], 'Each instrument is fetched once for every analysis that holds it'
    assert summary['prices']['failures'] == {'OK': 2, 'PRICE_STALE': 1}
    assert summary['examples']['catalog/MISSING_ETF'] == [job(3)]
    line = past_review.fit(summary)
    assert not [word for word in PRIVATE if word in line]


async def test_price_budget_stops_fetching_and_reports_unchecked_analyses(prices):
    rows = [{'id': job(1), 'input': payload([KR]), 'result': None}]
    summary = await past_review.review(rows, as_of=AS_OF, budget=-1, pause=0)
    assert prices == []
    assert summary['prices']['stopped_early'] is True
    assert summary['stages']['price_analysis'] == {'NOT_CHECKED': 1}


async def test_rate_limit_stops_all_fetching_so_live_customer_analyses_keep_the_provider(monkeypatch):
    calls = []
    async def fetch(symbol, as_of, **options):
        calls.append(symbol)
        raise ValueError('PRICE_PROVIDER_RATE_LIMIT')
    monkeypatch.setattr('edge_portfolio.market_data.fetch_prices', fetch)
    records, outcomes = await past_review.collect_prices({'KR:069500': 2, 'US:VOO': 1, 'FX': 1}, AS_OF, pause=0)
    assert calls == ['069500.KS'] and outcomes['KR:069500'][0] == 'PRICE_PROVIDER_RATE_LIMIT'


async def test_fetching_waits_while_customer_analyses_are_queued(prices, monkeypatch):
    states = iter([True, True, False])
    monkeypatch.setattr(past_review, 'busy', lambda: next(states))
    monkeypatch.setattr(past_review, 'BUSY_WAIT_SECONDS', 0)
    await past_review.collect_prices({'KR:069500': 1}, AS_OF, pause=0)
    assert prices == ['069500.KS']
    assert next(states, 'drained') == 'drained', 'Each fetch first checks for a live customer analysis'



def test_codes_drop_anything_that_could_be_customer_text():
    from edge_portfolio.contracts import AnalysisInput
    for text in ('VOO', 'KR:069500', 'PRIVATE_비밀', 'Invalid isoformat string', 'ACCOUNT_TRANSFER_NOT_ALLOWED:비밀'):
        assert past_review.code(text) == 'OTHER'
    assert past_review.code('ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:연금저축+IRP') == 'ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:연금저축+IRP'
    assert past_review.code('TimeoutError') == 'TimeoutError'
    tax = {'residence': '대한민국 거주자', 'age': '19~54세', 'income': 'x', 'financial_income_tax': '아니요',
           'accounts': [{'account': 'ISA', 'opened': '1999-99-99'}]}
    with pytest.raises(Exception) as error:
        AnalysisInput.model_validate(payload([KR], preferences={'tax_profile': tax}))
    assert '1999-99-99' in str(error.value)
    assert not [code for code in past_review.error_codes(error.value) if '1999' in code]


def test_review_only_reads_and_every_connection_is_read_only(monkeypatch, capsys):
    connections = []
    class DB:
        read_only = False
        def __init__(self): connections.append(self); self.queries = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, args=()):
            assert self.read_only, 'A connection opened during the review must refuse writes'
            self.queries.append(query)
            return self
        def fetchall(self): return [{'id': job(1), 'input': payload([KR]), 'result': None}]
        def fetchone(self): return {'n': 0}
    monkeypatch.setattr(store, 'connect', DB)
    monkeypatch.setattr(past_review, 'busy', BUSY)  # 고객 분석 대기 확인도 같은 읽기 전용 연결로 센다
    for name in ('heartbeat', 'mark_test_visitor'):
        monkeypatch.setattr(f'edge_portfolio.admin_store.{name}', lambda *a, **k: pytest.fail('visitor statistics must not be touched'))
    async def fetch(symbol, as_of, **options):
        store.connect().execute('SELECT 1')  # 분석 경로 어디서 연결을 열어도 읽기 전용이다
        return record('KRW')
    monkeypatch.setattr('edge_portfolio.market_data.fetch_prices', fetch)
    monkeypatch.setattr(past_review, 'PRICE_PAUSE_SECONDS', 0)
    past_review.main(['90', '0'])
    queries = [query for db in connections for query in db.queries]
    assert all(query.lstrip().startswith('SELECT') for query in queries)
    assert "owner NOT IN" in queries[0] and 'test_label IS NULL' in queries[0], 'Test visitors and test-labelled jobs are not customers'
    output = capsys.readouterr().out
    assert output.startswith('REVIEW ') and output.count('\n') == 1
    assert not [word for word in PRIVATE if word in output]


async def test_llm_samples_report_status_codes_and_time_only(monkeypatch):
    seen = []
    async def run_analysis(request, key, on_stage):
        seen.append(key)
        if len(seen) == 2:
            raise ValueError('PRIVATE 비밀 error text')
        return {'optimization': {'status': 'available', 'candidates': [1, 2]}, 'sections': 'PRIVATE result'}
    monkeypatch.setattr('edge_portfolio.agent.run_analysis', run_analysis)
    from edge_portfolio.contracts import AnalysisInput
    samples = [({'id': job(n)}, AnalysisInput.model_validate(payload([KR]))) for n in (1, 2)]
    results = await past_review.llm_replays(samples, 'test-key')
    assert [(r['job'], r['status']) for r in results] == [(job(1), 'succeeded'), (job(2), 'failed')]
    assert results[0]['optimization'] == 'available' and results[0]['candidates'] == 2
    assert results[1]['codes'] == ['ValueError']
    assert not [word for word in PRIVATE if word in str(results)]


async def test_llm_samples_yield_to_live_customers_and_to_provider_rate_limits(monkeypatch, prices):
    calls = []
    async def run_analysis(request, key, on_stage):
        calls.append(key)
        return {}
    monkeypatch.setattr('edge_portfolio.agent.run_analysis', run_analysis)
    monkeypatch.setattr(past_review, 'BUSY_WAIT_SECONDS', past_review.LLM_TIMEOUT_SECONDS)
    monkeypatch.setattr(past_review, 'busy', lambda: True)
    monkeypatch.setattr(past_review.asyncio, 'sleep', AsyncMock())
    from edge_portfolio.contracts import AnalysisInput
    results = await past_review.llm_replays([({'id': job(1)}, AnalysisInput.model_validate(payload([KR])))], 'k')
    assert results == [{'job': job(1), 'status': 'skipped_busy'}] and calls == []
    monkeypatch.setattr(past_review, 'busy', lambda: False)
    async def limited(symbol, as_of, **options):
        raise ValueError('PRICE_PROVIDER_RATE_LIMIT')
    monkeypatch.setattr('edge_portfolio.market_data.fetch_prices', limited)
    summary = await past_review.review([{'id': job(1), 'input': payload([KR]), 'result': None}], as_of=AS_OF, llm_samples=1, pause=0)
    assert summary['llm'] == 'skipped_rate_limited' and calls == []


async def test_llm_samples_stop_after_a_replay_hits_a_rate_limit_or_the_deadline(monkeypatch):
    calls = []
    async def run_analysis(request, key, on_stage):
        calls.append(key)
        return {'optimization': {'status': 'available', 'failures': {'US:VOO': 'PRICE_PROVIDER_RATE_LIMIT'}}}
    monkeypatch.setattr('edge_portfolio.agent.run_analysis', run_analysis)
    from edge_portfolio.contracts import AnalysisInput
    samples = [({'id': job(n)}, AnalysisInput.model_validate(payload([KR]))) for n in (1, 2)]
    results = await past_review.llm_replays(samples, 'k')
    assert len(calls) == 1 and results[1] == {'job': job(2), 'status': 'skipped_rate_limited'}
    results = await past_review.llm_replays(samples, 'k', deadline=past_review.time.monotonic() + 10)
    assert len(calls) == 1 and [r['status'] for r in results] == ['skipped_deadline'] * 2, 'A replay that cannot finish before the host cut-off never starts'
