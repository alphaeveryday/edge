"""Partial ETF coverage must not suppress valid stock facts or manufacture totals."""
import pytest

from edge_analysis_v2.sources.database import DatabaseTools
from edge_analysis_v2.sources.calendar import trading_dates
from test_real_sources import source


def flow_source():
    data=source()
    data['trading_dates']=['2026-09-21','2026-09-22','2026-09-23','2026-09-28','2026-09-29']
    data['flow']=[{'instrument_id':'000001','investor':'foreign','date':day,
        'net_amount_krw':amount,'finalized':True,'available_at':day+'T18:00:00+09:00'}
        for day,amount in zip(data['trading_dates'],[100,-30,20,0,80])]
    return data


@pytest.mark.parametrize('operation,direction,key,expected', [('sum','none','amount_krw',170),
    ('frequency','net_buy','matched_days',3), ('streak','net_buy','streak_days',1)])
def test_individual_flow_is_independent_of_partial_etf_weights(operation,direction,key,expected):
    result=DatabaseTools(flow_source()).call('calculate_investor_flow',dict(
        instrument_id='000001',investor='foreign',lookback_days=5,operation=operation,direction=direction))['result']
    assert result[key]==expected
    assert result['end_date']=='2026-09-29'
    assert result['scope']=='individual'


def test_missing_flow_is_not_a_zero_or_shorter_window():
    data=flow_source();data['flow'].pop(2)
    with pytest.raises(ValueError,match='missing'):
        DatabaseTools(data).call('sum_investor_net_flow',dict(instrument_id='000001',investor='foreign',lookback_days=5))


def test_calendar_includes_no_chuseok_sessions_and_rejects_uncovered_year():
    assert trading_dates('2026-09-23','2026-09-28')==['2026-09-23','2026-09-28']
    with pytest.raises(ValueError,match='2026'):
        trading_dates('2025-12-30','2026-01-05')


@pytest.mark.parametrize('field,value', [('as_of_date','2026-10-01'),('available_at','2026-09-30T13:00:00+09:00')])
def test_future_membership_cannot_authorize_a_stock(field,value):
    data=flow_source();data['holdings'][0][field]=value
    with pytest.raises(ValueError,match='outside'):
        DatabaseTools(data).call('sum_investor_net_flow',dict(instrument_id='000001',investor='foreign',lookback_days=5))
