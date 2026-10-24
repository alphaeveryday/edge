import pytest
from edge_portfolio.catalog import analysis_catalog, canonicalize, directory, price_symbol
from edge_portfolio.contracts import AnalysisInput, calculate


def test_held_stocks_are_accepted_but_only_the_customers_own_stocks_enter_the_analysis_catalog():
    # 고객이 넣은 개별주는 분석하지만, AI가 고를 수 있는 새 종목은 ETF뿐이어야 한다.
    data = directory()
    assert data['counts']['KR'] > 1000 and data['counts']['US'] > 1000
    assert len({r['id'] for r in data['instruments']}) == len(data['instruments'])
    with pytest.raises(ValueError, match='SELECT_LISTED_INSTRUMENT'):
        canonicalize(AnalysisInput(holdings=[{'name': 'not a real fund', 'amount_krw': 100}], horizon='10년 이상'))
    request = canonicalize(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}, {'name': 'AAPL', 'amount_krw': 100},
                                                   {'name': 'x', 'instrument_id': 'KR:098120', 'amount_krw': 100}], horizon='10년 이상'))
    assert [(h.instrument_id, h.kind) for h in request.holdings] == [('US:VOO', 'etf'), ('US:AAPL', 'stock'), ('KR:098120', 'stock')]
    catalog = analysis_catalog(request)
    assert {'US:AAPL', 'KR:098120', 'US:VOO'} <= catalog.keys()
    assert 'KR:005930' not in catalog and 'US:MSFT' not in catalog, 'stocks the customer did not enter stay out'
    assert price_symbol('KR:098120') == '098120.KQ', 'KOSDAQ prices use the listed symbol, not .KS'


def test_cash_is_not_treated_as_an_etf_and_selection_controls_identity():
    request = canonicalize(AnalysisInput(holdings=[{'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 1000000}], horizon='10년 이상'))
    assert calculate(request)['total_krw'] == '1000000'
    assert calculate(request)['holdings'][0]['kind'] == 'cash'
    request = canonicalize(AnalysisInput(holdings=[{'name': 'arbitrary misleading name', 'instrument_id': 'KR:069500', 'amount_krw': 1000000}], horizon='10년 이상'))
    assert request.holdings[0].name == 'KODEX 200 (069500)'
