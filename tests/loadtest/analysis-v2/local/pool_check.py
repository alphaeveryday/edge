"""PgBouncer transaction pooling 이 v2 워커(B 경로)의 DB 계약을 지키는지 확인한다 (load900.py 비교의 전제).

세 경로를 같은 항목으로 본다.
  direct          지금 워커: 직접 연결 + 시작 옵션(options='-c statement_timeout=…' 등)
  pool+options    지금 워커 그대로 풀에 붙는다. PgBouncer 는 options 의 statement_timeout 을 거절한다.
  pool+defaults   풀 경로만 시작 옵션을 빼고 같은 값을 역할 기본값에서 받는다(실험 변경). writer 값은 이미
                  마이그레이션(V202609282100)에 있고, reader 값(읽기 전용·15초)은 이 스크립트가 로컬 역할에 넣는다.
설정을 무시해서 연결만 되게 하지 않는다. 세션 값은 SHOW 로, 시간 초과·읽기 전용·잠금은 실제 오류로 확인한다.

  pool_check.py   # compose -p v2load900 의 postgres 와 --profile pool 의 pgbouncer 가 떠 있어야 한다
"""
import json
import threading
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from probe import DSN, admin, record, setup

POOL = {role: DSN[role].replace('port=55461', 'port=56432') for role in ('writer', 'reader')}
OPTIONS = {'writer': '-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000',
           'reader': '-c default_transaction_read_only=on -c statement_timeout=15000'}
READER_DEFAULTS = ("ALTER ROLE edge_analysis_v2_reader SET default_transaction_read_only = on",
                   "ALTER ROLE edge_analysis_v2_reader SET statement_timeout = '15s'")


def connect(role, dsn, options, **kwargs):
    return psycopg.connect(dsn[role], **kwargs, **({'options': OPTIONS[role]} if options else {}))


def writer(dsn, options):
    return connect('writer', dsn, options, autocommit=True)


def reader(dsn, options):  # 실제 connect_sources 와 같은 연결 속성
    connection = connect('reader', dsn, options, row_factory=dict_row)
    connection.read_only = True
    connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return connection


def outcome(fn):
    try:
        return {'ok': fn()}
    except Exception as exc:
        return {'error': type(exc).__name__, 'sqlstate': getattr(exc, 'sqlstate', None),
                'message': str(exc).strip().splitlines()[-1][:160]}


def settings_case(dsn, options):
    """세션 값과 실제 시간 초과(writer 15초)."""
    def run():
        with writer(dsn, options) as c:
            values = {'writer': [c.execute(f'SHOW {k}').fetchone()[0]
                                 for k in ('statement_timeout', 'idle_in_transaction_session_timeout')]}
            values['writer_timeout_enforced'] = outcome(lambda: c.execute('SELECT pg_sleep(15.5)').fetchone())
        with reader(dsn, options) as c:
            values['reader'] = [c.execute(f'SHOW {k}').fetchone()[k]
                                for k in ('default_transaction_read_only', 'statement_timeout')]
            c.rollback()
        return values
    return outcome(run)


def reader_case(dsn, options):
    """REPEATABLE READ 읽기 전용: 같은 트랜잭션은 다른 연결의 커밋을 보지 않고, 쓰기는 거절된다."""
    def run():
        with reader(dsn, options) as c:
            with c.transaction():
                level = c.execute('SHOW transaction_isolation').fetchone()['transaction_isolation']
                read_only = c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
                before = c.execute('SELECT count(*) n FROM pool_check_rows').fetchone()['n']
                with admin() as a:
                    a.execute('INSERT INTO pool_check_rows VALUES (1)')
                during = c.execute('SELECT count(*) n FROM pool_check_rows').fetchone()['n']
            after = c.execute('SELECT count(*) n FROM pool_check_rows').fetchone()['n']
            c.rollback()
            write = outcome(lambda: c.execute('INSERT INTO pool_check_rows VALUES (2)').rowcount)
        return {'isolation': level, 'transaction_read_only': read_only,
                'count_before_during_after': [before, during, after], 'write': write}
    return outcome(run)


def ownership_case(dsn, options):
    """발행 트랜잭션의 자리 확인(PublicationStore._owned): 자리가 있으면 커밋까지 회수 DELETE 를 막고, 없으면 거절한다."""
    from edge_analysis_v2.storage.publications import PublicationStore
    def run():
        arn, blocked = 'arn:pool-check:' + uuid4().hex, {}
        with admin() as a:
            a.execute('INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES (%s,%s,%s)',
                      (arn, uuid4().hex, arn))
        with writer(dsn, options) as c:
            store = PublicationStore(c, owner=arn)  # 유휴 자동 커밋 연결에서 만든다(실제와 같다)
            with c.transaction(), c.cursor(row_factory=dict_row) as cur:
                store._owned(cur)
                def reclaim():  # 실행 제어의 회수와 같은 DELETE 를 다른 연결에서
                    with admin() as a:
                        a.execute("SET lock_timeout='1s'")
                        blocked['reclaim_while_open'] = outcome(lambda: a.execute(
                            'DELETE FROM analysis_execution_slots WHERE execution_arn=%s', (arn,)).rowcount)
                thread = threading.Thread(target=reclaim)
                thread.start()
                thread.join()
        with admin() as a:
            blocked['reclaim_after_commit'] = a.execute(
                'DELETE FROM analysis_execution_slots WHERE execution_arn=%s', (arn,)).rowcount
        with writer(dsn, options) as c:
            store = PublicationStore(c, owner=arn)
            with c.transaction(), c.cursor(row_factory=dict_row) as cur:
                blocked['slot_missing'] = outcome(lambda: store._owned(cur))
        return blocked
    return outcome(run)


def prepared_case(dsn, options):
    """psycopg 는 한 연결에서 같은 문장이 5번을 넘으면 서버 prepared statement 로 바꾼다(prepare_threshold).

    발행 저장(요인 지표 13행)이 한 트랜잭션에서 그렇고, 자동 커밋 연결은 문장마다 다른 서버 연결로 갈 수 있다.
    prepare=True 로 처음부터 prepared statement 를 쓰고, 다른 클라이언트가 방금 쓴 서버 연결을 트랜잭션으로 붙잡아
    다음 문장이 다른 서버 연결로 가게 만든다. 실제로 바뀌었는지는 pg_backend_pid 로 본다."""
    def run():
        with writer(dsn, options) as c, writer(dsn, options) as other:
            values, pids = [], []
            for i in range(6):
                value, pid = c.execute('SELECT %s::int + 2, pg_backend_pid()', (i,), prepare=True).fetchone()
                values.append(value)
                pids.append(pid)
                if i:
                    other.execute('COMMIT')
                other.execute('BEGIN')
                other.execute('SELECT 1')  # 풀은 가장 최근에 반납된 서버 연결(c 가 방금 쓴 것)을 준다
            other.execute('COMMIT')
            with c.transaction():
                in_transaction = [c.execute('SELECT %s::int + 1', (i,)).fetchone()[0] for i in range(8)]
        return {'values': values, 'distinct_server_pids': len(set(pids)), 'in_transaction': in_transaction}
    return outcome(run)


def console(statement):
    with psycopg.connect('host=127.0.0.1 port=56432 dbname=pgbouncer user=v2_local password=local_only',
                         autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
        cur = c.execute(statement)
        return cur.fetchall() if cur.description else None


def problems(result):
    """기대값과 다른 항목. direct·pool+defaults 는 같은 계약을 지켜야 하고, pool+options 는 거절, 지원을 끈 반증은 깨져야 한다."""
    found = []
    def need(path, ok, got):
        if not ok:
            found.append(f'{path}: {json.dumps(got, ensure_ascii=False)[:200]}')
    state = lambda value: (value or {}).get('sqlstate')
    for name in ('direct', 'pool+defaults'):
        r = result[name]
        settings, reader_, owned, prepared = (r[k].get('ok') or {} for k in ('settings', 'reader', 'ownership', 'prepared'))
        need(f'{name}.settings', settings.get('writer') == ['15s', '30s'] and settings.get('reader') == ['on', '15s']
             and state(settings.get('writer_timeout_enforced')) == '57014', r['settings'])
        need(f'{name}.reader', reader_.get('isolation') == 'repeatable read' and reader_.get('transaction_read_only') == 'on'
             and reader_.get('count_before_during_after') == [0, 0, 1] and state(reader_.get('write')) == '25006', r['reader'])
        need(f'{name}.ownership', state(owned.get('reclaim_while_open')) == '55P03' and owned.get('reclaim_after_commit') == 1
             and (owned.get('slot_missing') or {}).get('error') == 'OwnershipLost', r['ownership'])
        need(f'{name}.prepared', prepared.get('values') == list(range(2, 8))
             and prepared.get('in_transaction') == list(range(1, 9)), r['prepared'])
    switched = (result['pool+defaults']['prepared'].get('ok') or {}).get('distinct_server_pids', 0)
    need('pool+defaults.prepared.server_switch', switched >= 2, switched)
    for case, value in result['pool+options'].items():
        need(f'pool+options.{case}', 'unsupported startup parameter' in value.get('message', ''), value)
    need('pool+defaults.prepared_with_support_off', state(result['pool+defaults'].get('prepared_with_support_off')) == '26000',
         result['pool+defaults'].get('prepared_with_support_off'))
    return found


def main():
    setup(20, 6)
    with admin() as a:
        for statement in READER_DEFAULTS:
            a.execute(statement)
        a.execute('DROP TABLE IF EXISTS pool_check_rows')
        a.execute('CREATE TABLE pool_check_rows (n int)')
        a.execute('GRANT SELECT ON pool_check_rows TO edge_analysis_v2_reader')
        roles = {r['rolname']: r['rolconfig'] for r in a.execute(
            "SELECT rolname, rolconfig FROM pg_roles WHERE rolname IN ('edge_analysis_v2_writer','edge_analysis_v2_reader')")}
    # 앞 실행의 풀 서버 연결(server_idle_timeout 600초)이 직접 경로의 역할 한도를 먹지 않게 비운다
    console('KILL analysis_v2')
    console('RESUME analysis_v2')
    version = console('SHOW VERSION')[0][0]
    config = {r[0]: r[1] for r in console('SHOW CONFIG')}
    result = {}
    for name, dsn, options in (('direct', DSN, True), ('pool+options', POOL, True), ('pool+defaults', POOL, False)):
        result[name] = {'settings': settings_case(dsn, options), 'reader': reader_case(dsn, options),
                        'ownership': ownership_case(dsn, options), 'prepared': prepared_case(dsn, options)}
        with admin() as a:
            a.execute('TRUNCATE pool_check_rows')
    # 반증 가능성: 풀의 prepared statement 지원을 끄면 같은 검사가 깨져야 한다
    console('SET max_prepared_statements = 0')
    try:
        result['pool+defaults']['prepared_with_support_off'] = prepared_case(POOL, False)
    finally:
        console('SET max_prepared_statements = 200')
    with admin() as a:
        a.execute('DROP TABLE pool_check_rows')
    found = problems(result)
    record('pool-check', {'pgbouncer': version, 'role_config': roles,
                          'pool_config': {k: config.get(k) for k in ('pool_mode', 'default_pool_size', 'max_client_conn',
                              'query_wait_timeout', 'max_prepared_statements', 'auth_type', 'ignore_startup_parameters',
                              'track_extra_parameters')}}, result | {'problems': found})
    if found:  # 기록은 남기되 계약이 깨진 채로 성공 종료하지 않는다
        raise SystemExit('pool-check failed: ' + '; '.join(found))


if __name__ == '__main__':
    main()
