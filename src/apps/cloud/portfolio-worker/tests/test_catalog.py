import pytest
from edge_portfolio.catalog import canonicalize, directory
from edge_portfolio.contracts import AnalysisInput, calculate


def test_etf_directory_includes_both_markets_and_rejects_individual_stocks():
    data = directory()
    assert data['counts']['KR'] > 1000 and data['counts']['US'] > 1000
    assert len({r['id'] for r in data['instruments']}) == len(data['instruments'])
    for name in ('AAPL', '005930', 'not a real fund'):
        with pytest.raises(ValueError, match='SELECT_LISTED_ETF'):
            canonicalize(AnalysisInput(holdings=[{'name': name, 'amount_krw': 100}], horizon='10년 이상'))
    request = canonicalize(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'))
    assert request.holdings[0].instrument_id == 'US:VOO'


def test_cash_is_not_treated_as_an_etf_and_selection_controls_identity():
    request = canonicalize(AnalysisInput(holdings=[{'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 1000000}], horizon='10년 이상'))
    assert calculate(request)['total_krw'] == '1000000'
    assert calculate(request)['holdings'][0]['kind'] == 'cash'
    request = canonicalize(AnalysisInput(holdings=[{'name': 'arbitrary misleading name', 'instrument_id': 'KR:069500', 'amount_krw': 1000000}], horizon='10년 이상'))
    assert request.holdings[0].name == 'KODEX 200 (069500)'
