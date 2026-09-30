"""Exercise real adapter SQL without touching production source tables."""
from datetime import datetime, timezone
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from edge_analysis_v2.sources.database import load_source, load_flow, load_prices


@pytest.mark.parametrize('extra_weight,valid_count', [(0, 2), (None, 1)])
def test_source_cutoff_keeps_partial_holdings_and_excludes_future_news(extra_weight, valid_count):
    dsn = os.getenv('V2_FACTOR_TEST_DSN')
    if not dsn:
        pytest.skip('local source SQL database not configured')
    target = conninfo_to_dict(dsn)
    assert target['host'] == '127.0.0.1' and target['port'] == '55440' and target['dbname'] == 'analysis_v2'
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as c:
        c.execute('''CREATE TEMP TABLE instrument(instrument_id text,ticker text,market_code text,instrument_type text);
            CREATE TEMP TABLE entity(entity_id text,display_name text);
            CREATE TEMP TABLE equity_profile(instrument_id text,issuer_actor_id text);
            CREATE TEMP TABLE etf_holding_snapshot_status(etf_instrument_id text,trade_date date,input_row_count int,valid_row_count int,data_version text,loaded_at timestamptz);
            CREATE TEMP TABLE etf_holding_snapshot(etf_instrument_id text,constituent_instrument_id text,trade_date date,weight_ratio numeric,available_at timestamptz,data_version text);
            CREATE TEMP TABLE document(document_id text,title text,document_type text,published_at timestamptz,available_at timestamptz);
            CREATE TEMP TABLE news_document(document_id text,lead_text text,lead_observed_at timestamptz);
            CREATE TEMP TABLE document_entity(document_id text,entity_id text);
            CREATE TEMP TABLE document_assertion(assertion_id text,document_id text,available_at timestamptz);
            CREATE TEMP TABLE event_evidence(assertion_id text,source_event_id text);
            CREATE TEMP TABLE source_event(source_event_id text,lifecycle_stage text,available_at timestamptz,source_class text,event_status text);
            CREATE TEMP TABLE event_thread_link(source_event_id text,thread_id text,evaluated_at timestamptz,source_class text);
            CREATE TEMP TABLE investor_flow_daily(instrument_id text,trade_date date,net_val_foreign bigint,net_val_institution_total bigint,net_val_individual bigint,available_at timestamptz);
            CREATE TEMP TABLE price_daily(instrument_id text,trade_date date,close_price numeric,volume bigint,turnover_value numeric,price_basis text,available_at timestamptz);
            CREATE TEMP TABLE minute_price_trigger(entity_id text,trigger_kind text,window_start timestamptz,close_price numeric,created_at timestamptz,generation int);
            INSERT INTO instrument VALUES ('etf','091160','XKRX','ETF'),('stock','000001','XKOS','EQUITY');
            INSERT INTO entity VALUES ('etf','ETF'),('stock','Stock');
            INSERT INTO equity_profile VALUES ('stock','issuer');
            INSERT INTO etf_holding_snapshot_status VALUES ('etf','2026-09-30',2,1,'current','2026-09-30T08:00:00+09:00');
            INSERT INTO etf_holding_snapshot VALUES ('etf','stock','2026-09-30',.9971,'2026-09-30T08:00:00+09:00','current');
            INSERT INTO document VALUES ('known','Known','NEWS','2026-09-30T09:00:00+09:00','2026-09-30T09:01:00+09:00'),
                ('future','Future','NEWS','2026-09-30T13:00:00+09:00','2026-09-30T13:01:00+09:00');
            INSERT INTO news_document VALUES ('known','Late excerpt','2026-09-30T13:00:00+09:00'),('future','Future','2026-09-30T13:00:00+09:00');
            INSERT INTO document_entity VALUES ('known','issuer'),('future','issuer');
            INSERT INTO document_assertion VALUES ('assert','known','2026-09-30T09:02:00+09:00');
            INSERT INTO event_evidence VALUES ('assert','event');
            INSERT INTO source_event VALUES ('event','SIGNED','2026-09-30T09:03:00+09:00','NEWS','ACTIVE');
            INSERT INTO event_thread_link VALUES ('event','thread','2026-09-30T13:00:00+09:00','NEWS');''')
        c.execute("INSERT INTO instrument VALUES ('zero','000002','XKRX','EQUITY')")
        c.execute("INSERT INTO entity VALUES ('zero','Zero')")
        c.execute("INSERT INTO etf_holding_snapshot VALUES ('etf','zero','2026-09-30',%s,'2026-09-30T08:00:00+09:00','current')", (extra_weight,))
        c.execute('UPDATE etf_holding_snapshot_status SET input_row_count=3,valid_row_count=%s', (valid_count,))
        c.execute("""INSERT INTO investor_flow_daily VALUES
            ('stock','2026-09-28',100,NULL,-100,'2026-09-28T18:00:00+09:00'),
            ('stock','2026-09-29',200,300,-500,'2026-09-30T13:00:00+09:00'),
            ('stock','2026-09-30',999,999,999,'2026-09-30T09:00:00+09:00')""")
        c.execute("""INSERT INTO price_daily VALUES
            ('etf','2026-09-23',NULL,200,NULL,NULL,'2026-09-23T18:00:00+09:00'),
            ('etf','2026-09-28',100,200,NULL,NULL,'2026-09-28T18:00:00+09:00'),
            ('etf','2026-09-29',101,201,NULL,NULL,'2026-09-29T18:00:00+09:00'),
            ('etf','2026-09-30',999,999,NULL,NULL,'2026-09-30T09:00:00+09:00');
            INSERT INTO minute_price_trigger VALUES
            ('091160','FIRE','2026-09-30T09:59:00+09:00',104,'2026-09-30T10:00:05+09:00',1),
            ('091160','FIRE','2026-09-30T09:59:00+09:00',105,'2026-09-30T10:00:10+09:00',2),
            ('091160','FIRE','2026-09-30T11:59:00+09:00',999,'2026-09-30T12:00:05+09:00',1),
            ('091160','FIRE','2026-09-30T12:00:00+09:00',999,'2026-09-30T12:00:00+09:00',1);""")
        c.autocommit = False
        c.read_only = True
        c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        data = load_source(c, '091160', '2026-09-30T12:00:00+09:00')
        assert [r['news_id'] for r in data['news']] == ['known']
        assert data['news'][0]['body'] is None
        assert data['news_links'] == []
        assert data['holdings'][0]['weight'] == .9971
        assert data['source_instrument_ids']['000001'] == 'stock'
        assert len(data['holdings']) == valid_count
        load_flow(c, data)
        assert data['context']['flow_as_of_date'] == '2026-09-29'
        assert {r['date'] for r in data['flow']} == {'2026-09-28'}
        assert {r['investor'] for r in data['flow']} == {'foreign','individual'}
        load_prices(c,data)
        assert [r['close'] for r in data['prices']]==[100,101]
        assert all(r['turnover'] is None and r['high'] is None and r['low'] is None for r in data['prices'])
        assert len(data['price_snapshots'])==1
        assert data['price_snapshots'][0]['price']==105
        assert datetime.fromisoformat(data['price_snapshots'][0]['observed_at']).astimezone(timezone.utc).hour==1
        c.rollback()
        c.read_only = False
        c.execute('UPDATE etf_holding_snapshot_status SET valid_row_count=99')
        c.commit()
        c.read_only = True
        with pytest.raises(ValueError, match='disagree'):
            load_source(c, '091160', '2026-09-30T12:00:00+09:00')
