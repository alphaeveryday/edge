"""Fixed-time source reads; no fixture generation or mutable DB connection in tools."""
import json
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from edge_analysis_v2.storage.database import DB_HOST
from edge_analysis_v2.sources.calendar import trading_dates
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


def load_flow(connection, data):
    """Attach up to thirty prior daily sessions without filling absent investors.

    Args:
        connection: Same read-only source transaction used for holdings.
        data: Holdings/news bundle at a fixed analysis time; updated in place.

    Returns:
        The bundle with finalized prior-day flows and an independent calendar.
    """
    at = instant(data['context']['analysis_at'])
    dates = trading_dates('2026-01-01', at.date().isoformat())
    previous = [d for d in dates if d < at.date().isoformat()]
    if not previous:
        raise ValueError('No prior session in the registered calendar')
    data['trading_dates'] = dates
    data['context']['flow_as_of_date'] = previous[-1]
    source_ids = data['source_instrument_ids']
    symbols = {identity:ticker for ticker,identity in source_ids.items()}
    rows = _rows(connection, '''SELECT instrument_id,trade_date,net_val_foreign,
        net_val_institution_total,net_val_individual,available_at
        FROM investor_flow_daily WHERE instrument_id=ANY(%s) AND trade_date BETWEEN %s AND %s
        AND available_at<=%s ORDER BY instrument_id,trade_date''',
        (list(symbols),previous[-30:][0],previous[-1],at))
    data['flow'] = []
    for row in rows:
        for investor,column in [('foreign','net_val_foreign'),('institution','net_val_institution_total'),('individual','net_val_individual')]:
            if row[column] is not None:
                if type(row[column]) is not int:
                    raise ValueError('Flow source requires integer KRW')
                data['flow'].append({'instrument_id':symbols[row['instrument_id']],
                    'date':row['trade_date'].isoformat(),'investor':investor,'net_amount_krw':row[column],
                    'finalized':True,'available_at':row['available_at'].isoformat()})
    return data


def load_prices(connection, data):
    """Attach available closes and actual intraday trigger observations.

    Args:
        connection: Same read-only source transaction as other observations.
        data: Bundle with the registered trading calendar, updated in place.

    Returns:
        Price observations; absent high, low and turnover stay null.
    """
    at = instant(data['context']['analysis_at'])
    if not data['trading_dates']:
        raise ValueError('Registered trading calendar required before price reads')
    symbols = {identity:ticker for ticker,identity in data['source_instrument_ids'].items()}
    rows = _rows(connection, '''SELECT instrument_id,trade_date,close_price,volume,turnover_value,price_basis,available_at
        FROM price_daily WHERE instrument_id=ANY(%s) AND trade_date>=%s AND trade_date<%s AND available_at<=%s
        ORDER BY instrument_id,trade_date''', (list(symbols),data['trading_dates'][0],at.date(),at))
    data['prices'] = [{'instrument_id':symbols[r['instrument_id']], 'date':r['trade_date'].isoformat(),
        'close':number(r['close_price']), 'high':None, 'low':None,
        'volume':number(r['volume']) if r['volume'] is not None else None,
        'turnover':number(r['turnover_value']) if r['turnover_value'] is not None else None,
        'available_at':r['available_at'].isoformat()} for r in rows if r['close_price'] is not None]
    expected = [d for d in data['trading_dates'] if d < at.date().isoformat()]
    continuous = []
    for symbol in symbols.values():
        history = [r for r in data['prices'] if r['instrument_id']==symbol]
        by_date = {r['date']:r for r in history}
        if len(by_date)!=len(history) or any(d not in expected for d in by_date):
            raise ValueError('Duplicate price or non-session price date')
        for day in reversed(expected):
            if day not in by_date:
                break
            continuous.append(by_date[day])
    data['prices'] = sorted(continuous,key=lambda r:(r['instrument_id'],r['date']))
    # A source basis is not an adjusted-close guarantee. Keep its actual values.
    data['price_basis'] = sorted({r['price_basis'] for r in rows if r['price_basis'] is not None}) or ['unverified']
    points = _rows(connection, '''SELECT * FROM (
        SELECT DISTINCT ON (window_start) window_start,close_price,created_at
        FROM minute_price_trigger WHERE entity_id=%s AND trigger_kind='FIRE'
        AND window_start>=%s AND window_start+INTERVAL '1 minute'<=%s AND created_at<=%s
        ORDER BY window_start DESC,created_at DESC,generation DESC) observations
        ORDER BY window_start DESC LIMIT 5''', (data['context']['etf_code'],at.replace(hour=0,minute=0,second=0,microsecond=0),at,at))
    data['price_snapshots'] = [{'instrument_id':data['context']['etf_code'],
        'observed_at':(r['window_start']+timedelta(minutes=1)).isoformat(),
        'available_at':r['created_at'].isoformat(),'price':number(r['close_price']),
        'high':None,'low':None} for r in reversed(points)]
    return data


class DatabaseTools(FixtureTools):
    """Use shared deterministic calculations with verified database observations only."""

    data_source = 'database'

    def __init__(self, source):
        super().__init__(source)
        enabled = {'get_etf_holdings','search_news_threads','get_issue_evidence'}
        if 'flow' in source:
            enabled |= {'calculate_investor_flow','calculate_weighted_flow','sum_investor_net_flow','sum_weighted_net_flow'}
        if 'prices' in source:
            enabled |= {'calculate_chart_indicators','evaluate_indicator_transition','get_instrument_factors'}
        self._tools = {name:tool for name,tool in self._tools.items() if name in enabled}
        self._tools['get_etf_holdings'].update(
            callback=lambda:holdings(self.fixture, require_complete=False),
            description='조회 시점에 확보된 구성종목과 원래 비중입니다. coverage=partial이면 전체 포트폴리오가 확인되지 않았으며 가중 계산에 사용할 수 없습니다.',
            formula=r'W=\sum_i w_i\quad\text{(observed weights; no renormalization)}')
        self._tools['get_issue_evidence']['description'] = '기사 ID로 확보된 내용을 읽습니다. include_body=true는 발췌(body_kind=excerpt)이며 전체 기사 원문이 아닙니다. false는 최종 근거용 ID·제목입니다. null인 본문을 추측하지 마세요.'
        if 'prices' in source:
            self._tools['calculate_chart_indicators']['description'] += ' 고가·저가 미확보 시 바닥지수는 null입니다.'
            self._tools['evaluate_indicator_transition']['description'] += ' 실제 FIRE 가격 관측 사이의 전이입니다. 연속 분봉이 아니며 관측 부족은 null입니다.'
        for name,tool in self._tools.items():
            tool['version'] = 'database-v1'
            if name == 'get_instrument_factors':
                tool['sources'] = ['price_daily','minute_price_trigger','investor_flow_daily','etf_holding_snapshot']
            elif name in ('calculate_chart_indicators','evaluate_indicator_transition'):
                tool['sources'] = ['price_daily','minute_price_trigger']
            elif 'flow' in name:
                tool['sources'] = ['investor_flow_daily','etf_holding_snapshot','etf_holding_snapshot_status']
            else:
                tool['sources'] = ['etf_holding_snapshot','etf_holding_snapshot_status'] if name=='get_etf_holdings' else ['document','news_document','source_event','event_thread_link']

    def initial_input(self):
        """Expose raw observations and material source limitations to the agent."""
        result = super().initial_input()
        result['source_notes'] = [
            '구성종목 coverage=partial이면 전체 ETF 가중 수급·밸류를 계산할 수 없습니다.',
            '뉴스 본문은 확보 발췌입니다. 스레드는 현존 관계이며 과거 정정·삭제까지 복원하지 않습니다.',
            '가격 조정 방식 미확인. 고가·저가 미확보로 바닥지수·ATR은 미제공. 장중 관측은 실제 트리거 가격이며 연속 분봉이 아닙니다.',
            '2026년 거래일만 검증되어 52주 지표는 미제공. 매크로·재무는 아직 연결하지 않았습니다.',
        ]
        return result
