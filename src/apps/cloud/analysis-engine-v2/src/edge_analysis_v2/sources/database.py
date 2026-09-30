"""Fixed-time source reads; no fixture generation or mutable DB connection in tools."""
import json
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from edge_analysis_v2.storage.database import DB_HOST
from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.tools.fixture_data.common import holdings, instant, number


def connect_sources(ca_path: Path, *, session=None):
    """Open a dedicated TLS-verified, read-only source transaction.

    Args:
        ca_path: Regional RDS CA bundle.
        session: Optional AWS session; default uses the restricted reader profile.

    Returns:
        Caller-owned repeatable-read psycopg connection with dictionary rows.
    """
    ca = Path(ca_path).resolve(strict=True)
    if session is None:
        import boto3
        session = boto3.Session(profile_name='edge-v2-readonly', region_name='ap-northeast-2')
    secret = json.loads(session.client('secretsmanager').get_secret_value(
        SecretId='edge/analysis-v2/readonly')['SecretString'])
    expected = dict(username='edge_analysis_v2_reader', host=DB_HOST, port=5432, dbname='edge')
    if any(secret.get(k) != v for k,v in expected.items()) or not isinstance(secret.get('password'), str) or not secret['password']:
        raise ValueError('Dedicated source reader identity required')
    connection = psycopg.connect(host=DB_HOST, hostaddr='127.0.0.1', port=15432,
        user=expected['username'], password=secret['password'], dbname='edge',
        sslmode='verify-full', sslrootcert=str(ca), connect_timeout=10,
        row_factory=dict_row, options='-c default_transaction_read_only=on -c statement_timeout=15000')
    connection.read_only = True
    connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return connection


def _rows(connection, statement, args=()):
    with connection.cursor() as cur:
        cur.execute(statement, args)
        return cur.fetchall()


def load_source(connection, ticker, analysis_at):
    """Read holdings and relevant news using a single analysis cutoff.

    Args:
        connection: Read-only repeatable-read connection with dictionary rows.
        ticker: Exact KRX ETF ticker.
        analysis_at: RFC3339 cutoff with explicit offset.

    Returns:
        In-memory observations accepted by DatabaseTools; absent domains stay empty.
    """
    if not connection.read_only or connection.isolation_level != psycopg.IsolationLevel.REPEATABLE_READ:
        raise ValueError('Read-only repeatable-read source connection required')
    at = instant(analysis_at)
    etfs = _rows(connection, """SELECT i.instrument_id,i.ticker,e.display_name
        FROM instrument i JOIN entity e ON e.entity_id=i.instrument_id
        WHERE i.ticker=%s AND i.market_code='XKRX' AND i.instrument_type='ETF'""", (ticker,))
    if len(etfs) != 1:
        raise ValueError('Exact KRX ETF not found')
    etf = etfs[0]
    statuses = _rows(connection, """SELECT trade_date,input_row_count,valid_row_count,data_version
        FROM etf_holding_snapshot_status WHERE etf_instrument_id=%s
        AND trade_date<=%s AND loaded_at<=%s ORDER BY trade_date DESC LIMIT 40""", (etf['instrument_id'],at.date(),at))
    if not statuses:
        raise ValueError('No holdings snapshot available at analysis time')
    portfolio, instruments, status_rows = [], {ticker: {'instrument_id': ticker, 'name': etf['display_name']}}, []
    source_ids = {ticker: etf['instrument_id']}
    actors = {}
    for status in statuses:
        rows = _rows(connection, """SELECT h.constituent_instrument_id,h.weight_ratio,h.available_at,
            i.ticker,i.market_code,e.display_name,ep.issuer_actor_id
            FROM etf_holding_snapshot h JOIN instrument i ON i.instrument_id=h.constituent_instrument_id
            JOIN entity e ON e.entity_id=i.instrument_id LEFT JOIN equity_profile ep ON ep.instrument_id=i.instrument_id
            WHERE h.etf_instrument_id=%s AND h.trade_date=%s AND h.data_version=%s
            AND h.available_at<=%s ORDER BY h.weight_ratio DESC NULLS LAST,h.constituent_instrument_id""",
            (etf['instrument_id'],status['trade_date'],status['data_version'],at))
        valid = [r for r in rows if r['weight_ratio'] is not None]
        if len(valid) != status['valid_row_count']:
            raise ValueError('Holdings rows and ingestion status disagree')
        day = status['trade_date'].isoformat()
        status_rows.append({'as_of_date':day,'input_count':status['input_row_count'],'valid_count':len(valid)})
        for row in valid:
            symbol = row['ticker']
            if row['market_code'] not in ('XKRX','XKOS') or symbol in source_ids and source_ids[symbol] != row['constituent_instrument_id']:
                raise ValueError('Ambiguous or unsupported constituent identity')
            source_ids[symbol] = row['constituent_instrument_id']
            instruments[symbol] = {'instrument_id': symbol, 'name': row['display_name']}
            actors[symbol] = row['issuer_actor_id']
            portfolio.append({'instrument_id':symbol,'weight':number(row['weight_ratio']),
                'as_of_date':day,'available_at':row['available_at'].isoformat()})
    data = {'context':{'etf_code':ticker,'analysis_at':at.isoformat(),
                       'flow_as_of_date':(at.date()-timedelta(days=1)).isoformat()},
            'instruments':list(instruments.values()),'holdings':portfolio,'holdings_status':status_rows,
            'source_instrument_ids':source_ids,'trading_dates':[], 'news':[], 'news_links':[]}
    current = holdings(data, require_complete=False)
    top = sorted(current['holdings'], key=lambda r:r['weight'], reverse=True)[:5]
    targets = [etf['instrument_id']]+[source_ids[r['instrument_id']] for r in top]
    targets += [actors[r['instrument_id']] for r in top if actors.get(r['instrument_id'])]
    articles = _rows(connection, """SELECT d.document_id,d.title,d.published_at,d.available_at,n.lead_text,n.lead_observed_at
        FROM document d JOIN news_document n USING(document_id)
        WHERE d.document_type='NEWS' AND d.published_at BETWEEN %s AND %s AND d.available_at<=%s
        AND EXISTS(SELECT 1 FROM document_entity de WHERE de.document_id=d.document_id AND de.entity_id=ANY(%s))
        ORDER BY d.published_at DESC,d.document_id LIMIT 301""", (at-timedelta(days=30),at,at,targets))
    data['news_limit_reached'] = len(articles)>300
    for row in articles[:300]:
        body = row['lead_text'] if row['lead_observed_at'] is not None and row['lead_observed_at']<=at else None
        data['news'].append({'news_id':row['document_id'],'title':row['title'],'body':body,'body_kind':'excerpt',
            'published_at':row['published_at'].isoformat(),'available_at':row['available_at'].isoformat()})
    data['news_links'] = _rows(connection, """SELECT DISTINCT a.document_id AS news_id,
        l.thread_id,s.source_event_id AS event_id,s.lifecycle_stage AS stage
        FROM document_assertion a JOIN event_evidence e USING(assertion_id)
        JOIN source_event s USING(source_event_id) JOIN event_thread_link l USING(source_event_id)
        WHERE a.document_id=ANY(%s) AND a.available_at<=%s AND s.available_at<=%s AND l.evaluated_at<=%s
        AND s.source_class='NEWS' AND l.source_class='NEWS' AND s.event_status='ACTIVE' AND l.thread_id IS NOT NULL
        ORDER BY l.thread_id,s.source_event_id,a.document_id""", ([r['news_id'] for r in data['news']],at,at,at))
    return data


class DatabaseTools(FixtureTools):
    """Use shared deterministic calculations with verified database observations only."""

    data_source = 'database'

    def __init__(self, source):
        super().__init__(source)
        enabled = {'get_etf_holdings','search_news_threads','get_issue_evidence'}
        self._tools = {name:tool for name,tool in self._tools.items() if name in enabled}
        self._tools['get_etf_holdings'].update(
            callback=lambda:holdings(self.fixture, require_complete=False),
            description='조회 시점에 확보된 구성종목과 원래 비중입니다. coverage=partial이면 전체 포트폴리오가 확인되지 않았으며 가중 계산에 사용할 수 없습니다.',
            formula=r'W=\sum_i w_i\quad\text{(observed weights; no renormalization)}')
        self._tools['get_issue_evidence']['description'] = '기사 ID로 확보된 내용을 읽습니다. include_body=true는 발췌(body_kind=excerpt)이며 전체 기사 원문이 아닙니다. false는 최종 근거용 ID·제목입니다. null인 본문을 추측하지 마세요.'
        for name,tool in self._tools.items():
            tool['version'] = 'database-v1'
            tool['sources'] = ['etf_holding_snapshot','etf_holding_snapshot_status'] if name=='get_etf_holdings' else ['document','news_document','source_event','event_thread_link']
