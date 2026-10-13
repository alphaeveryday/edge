"""Trusted controller only. Restricted source reader over the existing fixed SSM tunnel."""
import argparse
import json
from datetime import datetime, timezone, date
from pathlib import Path

HOST = 'edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com'
PROFILE = 'edge-v2-readonly'
REGION = 'ap-northeast-2'
ROLE = 'edge-analysis-v2-local-readonly'
DOCUMENT = 'EdgeResearchPostgresReadOnlyTunnel'
TUNNEL_PROFILE = 'edge-research-ro'
TARGET = 'i-0ba627536f36993d5'


def connect(ca_path):
    import boto3
    import psycopg
    from psycopg.rows import dict_row
    session = boto3.Session(profile_name=PROFILE,region_name=REGION)
    identity = session.client('sts').get_caller_identity()
    if identity['Account'] != '393229433969' or f':assumed-role/{ROLE}/' not in identity['Arn']:
        raise ValueError('Restricted source role required')
    secret = json.loads(session.client('secretsmanager').get_secret_value(SecretId='edge/analysis-v2/readonly')['SecretString'])
    expected = dict(username='edge_analysis_v2_reader',host=HOST,port=5432,dbname='edge')
    if any(secret.get(k)!=v for k,v in expected.items()):
        raise ValueError('Source credential identity mismatch')
    conn = psycopg.connect(host=HOST,hostaddr='127.0.0.1',port=15435,user=expected['username'],
        password=secret['password'],dbname='edge',sslmode='verify-full',sslrootcert=str(Path(ca_path).resolve(strict=True)),
        connect_timeout=10,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=15000')
    conn.read_only = True
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return conn


def probe(conn):
    report = {}
    statements = {
        'identity': "SELECT current_user, current_setting('transaction_read_only') AS read_only, ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()",
        'elevated_roles': "SELECT rolname FROM pg_roles WHERE pg_has_role(current_user,oid,'MEMBER') AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls)",
        'writable_tables': "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind IN ('r','p','v','f') AND (has_table_privilege(c.oid,'INSERT') OR has_table_privilege(c.oid,'UPDATE') OR has_table_privilege(c.oid,'DELETE') OR has_table_privilege(c.oid,'TRUNCATE'))",
        'creatable_schemas': "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND has_schema_privilege(oid,'CREATE')",
        'callable_definers': "SELECT n.nspname,p.proname,p.provolatile FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND p.prosecdef AND has_function_privilege(p.oid,'EXECUTE')",
        'source_columns': "SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('price_daily','instrument','etf_holding_snapshot','document','news_document') ORDER BY table_name,ordinal_position",
        'instrument': "SELECT instrument_id,ticker FROM instrument WHERE ticker='305720' AND market_code='XKRX'",
        'price_coverage': "SELECT count(*) AS rows,count(open_price) AS opens,min(trade_date) AS first_date,max(trade_date) AS last_date,min(available_at) AS earliest_available FROM price_daily WHERE instrument_id IN (SELECT instrument_id FROM instrument WHERE ticker='305720' AND market_code='XKRX')",
        'price_bases': "SELECT price_basis,count(*) AS rows,count(open_price) AS opens FROM price_daily WHERE instrument_id IN (SELECT instrument_id FROM instrument WHERE ticker='305720' AND market_code='XKRX') GROUP BY price_basis",
        'holdings_coverage': "SELECT count(DISTINCT trade_date) AS dates,min(trade_date) AS first_date,max(trade_date) AS last_date FROM etf_holding_snapshot WHERE etf_instrument_id IN (SELECT instrument_id FROM instrument WHERE ticker='305720' AND market_code='XKRX')",
        'distribution_tables': "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE '%dividend%' OR table_name LIKE '%distribution%' OR table_name LIKE '%corporate_action%')",
    }
    for name,sql in statements.items():
        report[name] = conn.execute(sql).fetchall()
    report['direct_write_denied'] = not any(report[k] for k in ('elevated_roles','writable_tables','creatable_schemas'))
    report['routine_review_required'] = bool(report['callable_definers'])
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--ca',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--export-start',type=date.fromisoformat)
    parser.add_argument('--export-end',type=date.fromisoformat)
    args=parser.parse_args()
    with connect(args.ca) as conn:
        result=probe(conn)
        if args.export_start or args.export_end:
            if not args.export_start or not args.export_end or args.export_start>args.export_end:
                raise ValueError('Both ordered export dates required')
            prices=conn.execute("""SELECT p.trade_date,p.open_price,p.close_price,p.volume,p.price_basis,
                p.available_at,p.data_version FROM price_daily p JOIN instrument i USING(instrument_id)
                WHERE i.ticker=%s AND i.market_code='XKRX' AND p.trade_date BETWEEN %s AND %s
                ORDER BY p.trade_date,p.available_at""",('305720',args.export_start,args.export_end)).fetchall()
            args.out.parent.mkdir(parents=True,exist_ok=True)
            export=dict(ticker='305720',kind='quarantined_source_export',
                retrieved_at=datetime.now(timezone.utc).isoformat(),prices=prices,
                limitations=['Not an approved replay dataset','No distribution history or calendar attestation',
                             'Price basis/revision semantics require review before admission'])
            args.out.with_name('price-export.json').write_text(json.dumps(export,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    readiness=dict(status='insufficient_data',two_year_run_completed=False,
        summary='SSM 읽기 연결 검증됨. 실제 2년 실험은 미실행: KRX 거래일 달력·가격 판본 증명·완전한 분배금 이력·웹 본문 아카이브 검증이 필요합니다.',
        missing=['attested KRX trading calendar for two years','unadjusted execution price provenance and PIT revisions',
                 'complete ETF ex-date/payment-date distribution history','verified historical web body versions'],
        coverage={key:result[key] for key in ('price_coverage','price_bases','holdings_coverage','distribution_tables')})
    args.out.with_name('readiness.json').write_text(json.dumps(readiness,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('source_columns','callable_definers')},ensure_ascii=False,default=str))


if __name__=='__main__':
    main()
