from datetime import datetime, timezone

import pytest
from edge_portfolio.contracts import AnalysisInput, calculate
from edge_portfolio.outlook import OutlookReader


def test_unknown_weights_are_not_fabricated_and_percent_is_not_money():
    request = AnalysisInput(holdings=[{'name': 'VOO'}], horizon='10년 이상', input_mode='names')
    assert calculate(request)['holdings'][0]['weight_pct'] is None
    request = AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상', input_mode='percent')
    assert calculate(request)['total_krw'] is None
    with pytest.raises(ValueError):
        AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 70}], horizon='10년 이상', input_mode='percent')


async def test_no_batch_no_purposeless_no_unidentified_reads(monkeypatch):
    monkeypatch.setenv('PORTFOLIO_OUTLOOK_DSN', 'test')
    calls = []
    def read(code, as_of):
        calls.append(code)
        return {'status': 'missing'}
    request = AnalysisInput(holdings=[{'name': '069500', 'amount_krw': 100}], horizon='10년 이상')
    reader = OutlookReader(request, datetime.now(timezone.utc), read)
    args = {'etf_code': '069500', 'purpose': '보유한 코스피 ETF의 최근 하락 위험을 확인하기 위해', 'decision_question': '현재 보유 비중을 줄일 근거가 기존 전망에 있는가?'}
    for invalid in ({**args, 'etf_code': ['069500', '102110']}, {**args, 'purpose': ''}, {**args, 'etf_code': '102110'}):
        with pytest.raises(ValueError):
            await reader.call(invalid, [])
    assert calls == []
    await reader.call(args, [])
    assert calls == ['069500']
    assert reader.queries[0]['purpose'] == args['purpose']
    with pytest.raises(ValueError, match='LIMIT'):
        await reader.call(args, [])


async def test_at_most_three_single_etfs_even_if_many_codes_are_known(monkeypatch):
    monkeypatch.setenv('PORTFOLIO_OUTLOOK_DSN', 'test')
    request = AnalysisInput(holdings=[{'name': code} for code in ['069500', '102110', '360750', '379800']], horizon='10년 이상', input_mode='names')
    reader = OutlookReader(request, datetime.now(timezone.utc), lambda *_: {'status': 'missing'})
    for index, holding in enumerate(request.holdings):
        args = {'etf_code': holding.name, 'purpose': '각 보유 ETF의 매도 판단에 필요한 하락 요인을 확인', 'decision_question': '이 종목의 투자 전제가 달라졌다는 근거가 있는가?'}
        if index < 3:
            await reader.call(args, [])
        else:
            with pytest.raises(ValueError, match='LIMIT'):
                await reader.call(args, [])


def test_internal_gateway_rejects_batch_arbitrary_sql_and_missing_purpose(monkeypatch):
    from fastapi.testclient import TestClient
    from edge_portfolio import outlook_gateway
    calls = []
    monkeypatch.setattr(outlook_gateway, 'read_one', lambda code, _: calls.append(code) or {'status': 'missing'})
    args = {'etf_code': '069500', 'purpose': '보유한 ETF의 최근 전망에서 하락 위험을 확인하기 위해', 'decision_question': '현재 보유 비중을 줄일 근거가 기존 전망에 있는가?', 'as_of': '2026-01-01T00:00:00Z'}
    with TestClient(outlook_gateway.app) as client:
        for change in ({'etf_code': ['069500']}, {'sql': 'SELECT * FROM outlook_analyses'}, {'purpose': ' ' * 20}):
            assert client.post('/outlook', json={**args, **change}).status_code == 422
        assert calls == []
        assert client.post('/outlook', json=args).json()['status'] == 'missing'
        assert calls == ['069500']
