"""v2 전망 N건(최대 900)이 동시에 만드는 DB 부하를 로컬 격리 PostgreSQL 에서 잰다 (ALPHA-1157 후속).

review_probe.py 모드 c 와 같은 실제 코드(worker.run → execute_request → 저장, B 의 단계별 연결·retry_transient·
발행 시 자리 확인)를 돌린다. 다른 점은 분석 1건이 OS 프로세스가 아니라 스레드라는 것이다. 900개 프로세스는 부하
발생기 메모리가 먼저 바닥나므로, 프로세스 P개에 스레드를 나눠 담는다. 스레드는 분석마다 하나이고 풀이나 세마포어로
줄 세우지 않는다. 모두 같은 시각 t0(+start_after)에 깨어난다.

원래 워커 경로와 다른 것(결과 해석 때 함께 본다):
  - 한 프로세스에 분석 여러 건: GIL·모듈 전역(STATS)을 공유한다. STATS 는 쓰지 않고 연결·재시도를 분석별로 따로 센다.
  - connect_results·connect_sources: Secrets Manager·TLS 없이 비밀번호 인증으로 직접 연결한다. 세션 옵션은 실제와 같다.
  - 원천 읽기: reader 연결 한 트랜잭션에서 pg_sleep(--src) 뒤 합성 fixture. 거시·재무는 실제 함수(빈 로컬 DB라 약 0초).
  - 모델: 대역. dev 실측 프로필(dev/tool_profile.py)의 툴 기록 시각을 재생하거나(--profile), --llm 초에 --tools 회를 고르게 부른다.
  - ETF: 분석마다 다른 합성 코드(9xxxxx). 자리 표는 실행 제어 없이 분석마다 행을 미리 넣는다(슬롯 우회). 중복 실행 제어는 재지 않는다.
  - SFN·Lambda·ECS·S3 는 없다(S3 는 로컬 디렉터리).

자식은 --net 으로 DB 와 같은 Docker 네트워크의 컨테이너(compose 의 generator 이미지)에서 돌린다. 호스트에서 돌리면
Docker Desktop 포트 중계가 초당 수백 회 연결에 먼저 무너진다(서버 로그에 없는 'server closed the connection
unexpectedly'). 관측용 관리자 연결과 조율 프로세스는 호스트에 남는다.

  N='--net v2load900_default --db-host postgres --port 5432'
  load900.py run --n 900 --profile ../dev/results/stage2-c37-20261004.tool_profile.json $N   # 실제 빈도 재생
  load900.py run --n 900 --tools 30 --align-end 150 --start-spread 60 $N                      # 최종 저장 동시
  load900.py child ...                                                                         # 내부용
"""
import argparse
import asyncio
from collections import Counter
import contextvars
import json
import math
import os
import random
import re
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from probe import ANALYSIS_AT, DSN, HERE, REPO, RESULTS, WORK, LocalSession, admin, record, retarget, setup
from review_probe import peak, reset

AID = contextvars.ContextVar('aid', default=None)
OUT = HERE/'results-900'
ARN = 'arn:aws:states:ap-northeast-2:1:execution:edge-dev-analysis-v2:'
CONTAINER = 'v2load900-postgres-1'


def dsn(role, host, port):
    return DSN[role].replace('host=127.0.0.1 port=55461', f'host={host} port={port}')


# ── 자식 프로세스: 분석 여러 건을 스레드로 ──────────────────────────────────────────────
def child(args):
    spec = json.loads(Path(args.spec).read_text())
    from edge_analysis_v2.analysis import service
    from edge_analysis_v2.cloud import worker
    from edge_analysis_v2.storage.database import ResultDatabaseError, retry_transient, transient
    from edge_analysis_v2.tools.fixture_data import make_fixture

    events, runs, mine = [], {}, {a['id']: a for a in spec['analyses']}
    profile = spec['profile']

    def connect(role, **options):
        event = {'a': AID.get(), 'r': role, 's': time.time()}
        events.append(event)
        try:
            target = dsn(role, spec['host'], spec['port'])
            if spec.get('bad_password') == role:  # --bad-password: 인증 오류 주입
                target = target.replace('local_only', 'wrong_password')
            connection = psycopg.connect(target, **options)
        except Exception as exc:
            event['e'], event['f'] = str(exc).strip().splitlines()[-1][:160], time.time()
            raise
        event['o'] = time.time()
        close = connection.close
        def closing():
            event.setdefault('c', time.time())
            close()
        connection.close = closing
        return connection

    # 실제와 같은 시작 옵션. --no-startup-options(풀 경로)면 빼고 같은 값을 역할 기본값에서 받는다(run 이 확인)
    writer_options = {'options': '-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000'}
    reader_options = {'options': '-c default_transaction_read_only=on -c statement_timeout=15000'}
    if not spec['startup_options']:
        writer_options = reader_options = {}

    def connect_results(ca_path, *, session=None, cloud=False):  # 실제와 같은 예외 모양
        try:
            return connect('writer', autocommit=True, **writer_options)
        except Exception as exc:
            error = ResultDatabaseError('Writer connection unavailable', transient=isinstance(exc, psycopg.OperationalError))
            error.cause_text = str(exc)  # 탐침에서만: 운영 connect_results 는 원래 문구를 버린다(from None) — 제안 분류(--policy p)용
            raise error from None

    def connect_sources(ca_path, *, session=None, cloud=False):  # 실제 connect_sources 는 psycopg 오류를 그대로 올린다
        connection = connect('reader', row_factory=dict_row, **reader_options)
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        return connection

    def step_name(operation):  # 재시도 단위가 감싼 DB 단계 함수(step)의 파일·이름·줄. 없으면 감싼 함수 자신
        code = operation.__code__
        fn = operation.__closure__[code.co_freevars.index('step')].cell_contents if 'step' in code.co_freevars else operation
        return f"{Path(fn.__code__.co_filename).stem}.{fn.__qualname__.replace('.<locals>', '')}:{fn.__code__.co_firstlineno}"

    def classify(exc):  # --policy p 의 제안 분류(README '연결 실패 분류'). 문구 기반이라 목록 밖은 원인 불명
        text, state = (getattr(exc, 'cause_text', '') + ' ' + str(exc)).lower(), getattr(exc, 'sqlstate', None)
        if 'query_wait_timeout' in text:
            return 'pool_wait'
        if any(k in text for k in ('password authentication failed', 'unsupported startup parameter', 'does not exist')):
            return 'auth_config'
        if state in ('57P01', '57P02', '57P03', '53300') or any(k in text for k in (
                'too many connections', 'too many clients', 'not currently accepting', 'starting up', 'shutting down',
                'connection refused', 'server closed the connection', 'terminating connection', 'timeout expired')):
            return 'transient'
        return 'unknown' if transient(exc) else 'permanent'

    def policied(operation, run, name):
        """--policy a|b 실험 정책(운영 코드 아님). 단계 시작부터 한 시계로 마감을 잰다.

        새 시도는 '남은 시간 ≥ 풀 대기 상한 + 여유'일 때만 시작해, 재시도가 시간 예산을 늘리지 않게 한다.
        진행 중인 시도는 끊지 못하므로 마감 초과는 막지 않고 steps 에 경과로 남긴다. a 는 풀 대기 초과를 재시도하지 않는다."""
        started, delay, attempts = time.monotonic(), 0.5, 0
        while True:
            attempts += 1
            try:
                result = operation()
                run['steps'].append((time.monotonic() - started, attempts, 'ok', time.monotonic() - started - spec['deadline'], name))
                return result
            except Exception as exc:
                text = str(exc).strip().splitlines()[-1][:120] if str(exc).strip() else ''
                run['op_errors'].append((time.time(), type(exc).__name__ + ': ' + text))
                pool_wait = 'query_wait_timeout' in str(exc)
                wait = random.uniform(0, delay)  # full jitter
                left = spec['deadline'] - (time.monotonic() - started) - wait
                kind = classify(exc)
                wanted = (kind == 'transient' or (kind == 'unknown' and attempts < 2)) if spec['policy'] == 'p' else (
                    transient(exc) and (spec['policy'] == 'b' or not pool_wait))
                if not (wanted and left >= spec['pool_wait'] + spec['margin']):
                    run['steps'].append((time.monotonic() - started, attempts, kind if spec['policy'] == 'p' else (
                                             'pool_wait' if pool_wait else type(exc).__name__),
                                         time.monotonic() - started - spec['deadline'], name))
                    run['exhausted'] += transient(exc)
                    raise
                run['retry_waits'].append((time.time(), wait))
                time.sleep(wait)
                delay = min(delay * 2, spec['backoff_cap'])

    def counted(operation, **kwargs):  # 실제 retry_transient 를 그대로 부르고 시도 실패·대기·소진만 분석별로 센다
        run, name = runs[AID.get()], step_name(operation)
        if spec.get('inject_unknown') and spec['inject_unknown'] in name:  # 분류 목록 밖 오류를 매 시도 주입
            def operation():
                raise psycopg.OperationalError('injected unclassified failure')
        if spec.get('lose_reply') and spec['lose_reply'] in name and not run.get('reply_lost'):
            inner = operation
            def operation():  # 커밋은 DB 에서 끝나고 응답만 잃은 상황을 클라이언트 쪽에서 흉내 낸다(첫 시도 1회)
                result = inner()
                if not run.get('reply_lost'):
                    run['reply_lost'] = True
                    raise psycopg.OperationalError('server closed the connection unexpectedly (injected after commit)')
                return result
        if spec.get('policy'):
            return policied(operation, run, name)
        started, errors_before = time.monotonic(), len(run['op_errors'])
        def finish(outcome):  # 운영 retry_transient 경로도 단계 시간·시도 수를 남긴다. 초과 기준은 예산 20초
            run['steps'].append((time.monotonic() - started, len(run['op_errors']) - errors_before + (outcome == 'ok'),
                                 outcome, time.monotonic() - started - 20, name))
        def sleep(seconds):
            run['retry_waits'].append((time.time(), seconds))
            time.sleep(seconds)
        def attempt():  # 연결 뒤 실패(풀의 query_wait_timeout 등)도 남긴다. 연결 실패는 events 에도 있다
            try:
                return operation()
            except Exception as exc:
                run['op_errors'].append((time.time(), type(exc).__name__ + ': ' + str(exc).strip().splitlines()[-1][:120]))
                raise
        try:
            result = retry_transient(attempt, sleep=sleep, **kwargs)
            finish('ok')
            return result
        except Exception as exc:
            finish(type(exc).__name__)
            if transient(exc):
                run['exhausted'] += 1
            raise

    def load_source(connection, ticker, analysis_at):
        connection.execute('SELECT pg_sleep(%s)', (spec['src'],))
        return retarget(make_fixture(analysis_at=analysis_at), ticker)

    research = worker.load_research_observations
    def held(connection, data):  # --research-profile: 시작 때 writer 에서 도는 거시·재무 시간(dev 실측 상한)
        hold = mine[AID.get()]['research']
        if hold:
            connection.execute('SELECT pg_sleep(%s)', (hold,))
        return research(connection, data)

    async def until(at, run):
        delay = at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)
            run['overshoot'].append(time.time() - at)  # 깨어나야 할 시각보다 늦은 만큼 = 부하 발생기 스케줄링 지연
        else:
            run['behind'].append(-delay)  # 앞 툴 기록(대기·재시도 포함)이 길어 예정보다 늦게 부른다

    async def model(**kwargs):
        run, me = runs[AID.get()], mine[AID.get()]
        run['model_calls'] = run.get('model_calls', 0) + 1  # DB 단계 재시도가 모델을 다시 부르지 않는지
        run['model_start'] = start = time.time()
        call, news = kwargs['call'], kwargs['initial']['news'][0]['news_id']
        if profile:
            p, scale = profile[me['profile']], me['scale']
            times = [run['began'] + scale*o for o in p['tool_offsets_s'][1:]]  # 첫 기록은 execute_request 의 요인 조회
            end = run['began'] + scale*max(p['job_s'] - 1.0, p['tool_offsets_s'][-1])
        else:
            end = spec['t0'] + spec['align_end'] if spec['align_end'] else start + spec['llm']
            step = (end - start) / (spec['tools'] + 1)
            times = [start + step*(i + 1) for i in range(spec['tools'])]
        reference = None
        write = lambda: asyncio.to_thread(call, 'write_outlook_body', {'title': '계약 이행을 확인해요', 'items': [
            dict(id='contract', title_keyword='계약', sentences=['판매 물량을 확보했어요.'], tool_run_ids=[reference])]})
        for index, at in enumerate(times):
            await until(at, run)
            if profile and index == 1:  # 실측 기록 수를 지킨다: 본문 편집이 프로필의 둘째 기록 자리를 쓴다
                await write()
            else:
                reference = (await asyncio.to_thread(call, 'get_issue_evidence',
                                                     {'news_ids': [news], 'include_body': False}))['tool_run_id']
            run['calls'] += 1
            if not profile and index == 0:  # 고르게 모드는 review_probe.py 대역과 같은 호출 수(대조용)
                await write()
                run['calls'] += 1
        await until(end, run)
        run['model_end'] = time.time()
        return {'outlook': {'direction': '상승'}, 'summary_card': {'title': '물량 확보', 'summary': '이행을 확인해요.'},
                'factors': [{'type': f, 'sticker': '중립', 'sentence': '자료를 확인했어요.'}
                            for f in ('이슈', '차트', '매크로', '밸류', '수급')],
                'conclusion': {'title': '이행 확인', 'supports': [{'label': '계약', 'tool_run_ids': [reference]}],
                               'burdens': [], 'sentence': '진행 상황을 확인해요.'},
                'issue_detail': {'headline': '공급 계약', 'items': [dict(title_keyword='계약',
                    sentence='판매 물량을 확보했어요.', sentiment='positive', tool_run_ids=[reference])]}}

    worker.connect_results, worker.connect_sources = connect_results, connect_sources
    worker.retry_transient = service.retry_transient = counted
    worker.load_source, worker.load_flow, worker.load_prices = load_source, (lambda c, d: d), (lambda c, d, **_: d)
    worker.load_research_observations = held
    worker.execute_request = lambda **kw: service.execute_request(model_call=model, **kw)

    def one(a):
        AID.set(a['id'])
        run = runs[a['id']] = {'retry_waits': [], 'op_errors': [], 'exhausted': 0, 'calls': 0, 'overshoot': [],
                               'behind': [], 'steps': []}
        time.sleep(max(0.0, t0 + a['start_after'] - time.time()))
        run['began'] = time.time()
        request = {'analysis_id': a['id'], 'kind': 'outlook', 'etf_code': a['etf'], 'analysis_at': ANALYSIS_AT}
        try:
            worker.run(request, bucket='local', ca_path=HERE, folder=WORK/'artifacts'/a['id'], key='fake-key',
                       model='stub', session=LocalSession(), owner=ARN + a['id'])
            run['exit'], run['error'] = 0, None
        except Exception as exc:
            cause = exc.__cause__ or exc.__context__
            run['exit'], run['error'] = 1, type(exc).__name__ + ': ' + str(exc)[:160] + (
                ' <- ' + type(cause).__name__ + ': ' + str(cause).strip().splitlines()[0][:120] if cause else '')
        run['ended'] = time.time()

    threads = [threading.Thread(target=one, args=(a,), daemon=True) for a in spec['analyses']]
    print('ready', time.time(), flush=True)
    t0 = float(sys.stdin.readline())
    spec['t0'] = t0
    cpu, stop = [], threading.Event()
    def sample():  # 이 프로세스(스레드 전체)가 쓴 CPU 코어 수, 1초 표본
        last, wall = time.process_time(), time.time()
        while not stop.wait(1.0):
            now, at = time.process_time(), time.time()
            cpu.append((at, round((now - last) / (at - wall), 3), threading.active_count()))
            last, wall = now, at
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    sampler.join()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    Path(args.out).write_text(json.dumps({'runs': runs, 'events': events, 'cpu': cpu,
        'cpu_seconds': round(usage.ru_utime + usage.ru_stime, 1),
        'max_rss_mb': round(usage.ru_maxrss / (2**20 if sys.platform == 'darwin' else 2**10), 1)}))


# ── 관측: PostgreSQL 활동·대기, 컨테이너 CPU·메모리 ─────────────────────────────────────
def observe(stop, pg, box, containers, pool, console, errors):
    def pools():  # PgBouncer 관리 콘솔: 역할별 클라이언트 진행·대기, 서버 연결 진행·그 밖, 가장 오래 기다린 초
        with psycopg.connect(console, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
            while not stop.wait(0.5):
                cur = c.execute('SHOW POOLS')
                names = [d.name for d in cur.description]
                rows = [dict(zip(names, r)) for r in cur.fetchall()]
                pool.append((time.time(), [(r['user'].rsplit('_', 1)[-1], r['cl_active'], r['cl_waiting'], r['sv_active'],
                                            sum(r.get(k, 0) for k in ('sv_idle', 'sv_used', 'sv_tested', 'sv_login')),
                                            r['maxwait'] + r.get('maxwait_us', 0) / 1e6)
                                           for r in rows if r['database'] == 'analysis_v2']))

    def activity():
        with admin() as c:
            while not stop.wait(0.5):
                rows = c.execute("SELECT usename, state, wait_event_type, wait_event, count(*) n FROM pg_stat_activity "
                                 "WHERE backend_type='client backend' AND usename LIKE 'edge_analysis_v2%' "
                                 "GROUP BY 1,2,3,4").fetchall()
                pg.append((time.time(), [(r['usename'].rsplit('_', 1)[-1], r['state'], r['wait_event_type'],
                                          r['wait_event'], r['n']) for r in rows]))
    def container():  # docker stats 는 1~2초마다 한 줄. 화면 제어 문자를 지운다
        proc = subprocess.Popen(['docker', 'stats', *containers, '--format', '{{.Name}};{{.CPUPerc}};{{.MemUsage}}'],
                                stdout=subprocess.PIPE, text=True)
        while not stop.is_set():
            raw = proc.stdout.readline()
            if not raw and proc.poll() is not None:
                raise RuntimeError(f'docker stats exited {proc.returncode}')
            line = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', raw).strip()
            for hit in re.finditer(r'([\w.-]+);([\d.]+)%;([\d.]+)(KiB|MiB|GiB)', line):
                scale = {'KiB': 1 / 1024, 'MiB': 1, 'GiB': 1024}[hit[4]]
                box.append((time.time(), hit[1], float(hit[2]), float(hit[3]) * scale))
        proc.kill()
    def guarded(target):  # 스레드 예외는 join 으로 오지 않는다 — 모아서 결과에 남긴다
        def run():
            try:
                target()
            except Exception as exc:
                errors.append(f'{target.__name__}: {type(exc).__name__}: {exc}')
        return run
    threads = [threading.Thread(target=guarded(f), daemon=True)
               for f in (activity, container) + ((pools,) if console else ())]
    for thread in threads:
        thread.start()
    return threads


def database_counters():
    with admin() as c:
        return c.execute("SELECT sessions, sessions_fatal, sessions_abandoned, xact_commit, xact_rollback, deadlocks "
                         "FROM pg_stat_database WHERE datname='analysis_v2'").fetchone()


def pool_stats(console):
    if not console:
        return {}
    with psycopg.connect(console, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
        cur = c.execute('SHOW STATS')
        names = [d.name for d in cur.description]
        row = next((dict(zip(names, r)) for r in cur.fetchall() if r[0] == 'analysis_v2'), None)
    if row is None:  # KILL 직후에는 그 DB 의 통계 행이 없다 — 다시 생기면 0부터 센다
        return {k: 0 for k in names if k.startswith('total_')}
    return {k: int(v) for k, v in row.items() if k.startswith('total_')}


def quiet_start(console):
    """두 방식 모두 v2 역할 서버 연결 0개에서 시작한다. 풀은 비우고, 직접은 남은 연결이 있으면 거부한다.

    PgBouncer 가 앞 실행의 서버 연결(server_idle_timeout 600초)을 쥐고 있으면 직접 실행의 역할 한도를 먹는다."""
    if console:
        with psycopg.connect(console, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
            c.execute('KILL analysis_v2')
            c.execute('RESUME analysis_v2')
    deadline = time.time() + 10
    while True:
        with admin() as c:
            left = c.execute("SELECT count(*) n FROM pg_stat_activity WHERE usename LIKE 'edge_analysis_v2%'").fetchone()['n']
        if left == 0:
            return
        if time.time() > deadline:
            raise SystemExit(f'v2 역할 연결 {left}개가 남아 있어 같은 연결 예산에서 시작할 수 없다(직접 실행이면 PgBouncer 를 내린다)')
        time.sleep(0.5)


def pool_budget(console):
    """PgBouncer 가 역할별로 여는 서버 연결 상한(max_user_connections, 없으면 default_pool_size)."""
    if not console:
        return None
    with psycopg.connect(console, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
        default = int(next(r[1] for r in c.execute('SHOW CONFIG').fetchall() if r[0] == 'default_pool_size'))
        cur = c.execute('SHOW USERS')
        names = [d.name for d in cur.description]
        users = {r['name']: r for r in (dict(zip(names, row)) for row in cur.fetchall())}
    return {role: int(users.get(f'edge_analysis_v2_{role}', {}).get('max_user_connections') or default)
            for role in ('writer', 'reader')}


def role_configs():
    with admin() as c:
        return {r['rolname']: sorted(r['rolconfig'] or []) for r in c.execute(
            "SELECT rolname, rolconfig FROM pg_roles WHERE rolname IN ('edge_analysis_v2_writer','edge_analysis_v2_reader')")}


def settings():
    with admin() as c:
        rows = c.execute("SELECT name, setting, unit FROM pg_settings WHERE name IN ('max_connections','shared_buffers',"
                         "'superuser_reserved_connections','password_encryption','max_worker_processes','work_mem')").fetchall()
        return {r['name']: r['setting'] + (r['unit'] or '') for r in rows} | {'version': c.execute('SHOW server_version').fetchone()['server_version']}


# ── 집계 ────────────────────────────────────────────────────────────────────────────────
def dist(values, digits=3):
    v = sorted(values)
    if not v:
        return None
    q = lambda p: round(v[min(len(v) - 1, int(len(v) * p))], digits)
    return {'n': len(v), 'p50': q(0.5), 'p90': q(0.9), 'p99': q(0.99), 'max': round(v[-1], digits)}


def refusal(text):
    for key, label in (('query_wait_timeout', 'pool_wait_timeout'), ('Writer connection unavailable', 'writer_connect'),
                       ('too many connections for role', 'role_limit'), ('too many clients', 'server_limit'),
                       ('remaining connection slots', 'server_limit'), ('max_client_conn', 'pool_client_limit'),
                       ('timeout', 'timeout'), ('refused', 'refused'), ('not permitted to log in', 'login_denied')):
        if key in text:
            return label
    return text[:80]


def phase(run, at):
    if at < run.get('model_start', math.inf):
        return 'start'
    return 'audit' if at < run.get('model_end', math.inf) else 'final'


def summarize(analyses, children, t0, pg, box, before, after, pool, pool_before, pool_after, db=None):
    runs = {k: v for c in children for k, v in c['runs'].items()}
    events = [e for c in children for e in c['events']]
    ids = [a['id'] for a in analyses]
    for i in ids:
        runs.setdefault(i, {'exit': None, 'error': 'no output (child crashed?)', 'retry_waits': [], 'op_errors': [],
                            'exhausted': 0,
                            'calls': 0, 'overshoot': [], 'behind': []})
    end = max([r['ended'] for r in runs.values() if 'ended' in r] + [t0])
    # 서버·풀이 끊은 연결은 psycopg 가 close() 를 부르지 않아 닫힌 시각이 없다(풀의 query_wait_timeout).
    # 그 분석이 실패를 받은 시각에 닫힌 것으로 본다. 그런 실패도 없으면 실행 끝까지 열린 것으로 남기고 센다.
    broken = unclosed = 0
    for e in events:
        if 'o' in e and 'c' not in e:
            failed = [t for t, _ in runs.get(e['a'], {}).get('op_errors', []) if t >= e['o']]
            if failed:
                e['c'], broken = min(failed), broken + 1
            else:
                unclosed += 1
    roles = {}
    for role in ('writer', 'reader'):
        mine = [e for e in events if e['r'] == role]
        opened = [e for e in mine if 'o' in e]
        roles[role] = {'attempts': len(mine), 'opened': len(opened),
                       'refused': dict(Counter(refusal(e['e']) for e in mine if 'e' in e)),
                       'connect_ms': dist([(e['o'] - e['s']) * 1000 for e in opened], 1),
                       'refuse_ms': dist([(e['f'] - e['s']) * 1000 for e in mine if 'e' in e], 1),
                       'hold_ms': dist([(e.get('c', end) - e['o']) * 1000 for e in opened], 1),
                       'peak_open_client': peak([(e['o'], e.get('c', end)) for e in opened]),
                       'peak_connecting_client': peak([(e['s'], e.get('o', e.get('f', end))) for e in mine]),
                       'conn_seconds': round(sum(e.get('c', end) - e['o'] for e in opened), 1)}
    phases = {}
    for name in ('start', 'audit', 'final'):
        mine = [e for e in events if e['a'] in runs and phase(runs[e['a']], e['s']) == name]
        waits = [w for r in runs.values() for at, w in r['retry_waits'] if phase(r, at) == name]
        phases[name] = {'attempts': len(mine), 'refused': sum('e' in e for e in mine),
                        'connect_ms': dist([(e['o'] - e['s']) * 1000 for e in mine if 'o' in e], 1),
                        'retries': len(waits), 'retry_wait_s': round(sum(waits), 1),
                        # 연결 시도(거절 포함)·연결 점유(풀 대기열 포함)·재시도 대기를 모두 더한 DB 단계 시간
                        'db_time_s': round(sum(e.get('c', e.get('f', end)) - e['s'] for e in mine) + sum(waits), 1)}
    spent = Counter()
    for e in events:
        spent[e['a']] += e.get('c', e.get('f', end)) - e['s']
    for i, r in runs.items():
        spent[i] += sum(w for _, w in r['retry_waits'])
    done = [r for r in runs.values() if 'ended' in r]
    db = db or db_state(ids, runs)
    samples = [dict() for _ in pg]
    for sample, (_, group) in zip(samples, pg):
        for role, *_rest, n in group:
            sample[role] = sample.get(role, 0) + n
    waits = {}
    for _, group in pg:
        for role, state, kind, event, n in group:
            if state == 'active' and kind:
                waits[f'{role}:{kind}:{event}'] = waits.get(f'{role}:{kind}:{event}', 0) + n
    lags = [r['began'] - (t0 + a['start_after']) for a in analyses for r in [runs[a['id']]] if 'began' in r]
    return {
        'submitted': len(ids), 'started': sum('began' in r for r in runs.values()),
        'peak_in_progress': peak([(r['began'], r['ended']) for r in done]),
        # 모델 대역 중 실패한 분석은 끝난 시각까지 모델 대기로 센다
        'peak_in_model': peak([(r['model_start'], r.get('model_end', r['ended'])) for r in done if 'model_start' in r]),
        'ok': sum(r['exit'] == 0 for r in runs.values()), 'failed': sum(r['exit'] == 1 for r in runs.values()),
        'incomplete': sum(r['exit'] is None for r in runs.values()),
        'errors': sorted({(r['error'] or '')[:220] for r in runs.values() if r['exit'] != 0})[:8],
        'elapsed_s': round(end - t0, 1),
        'analysis_s': dist([r['ended'] - r['began'] for r in done], 1),
        'start_phase_s': dist([r['model_start'] - r['began'] for r in done if 'model_start' in r], 2),
        'final_phase_s': dist([r['ended'] - r['model_end'] for r in done if 'model_end' in r], 2),
        'roles': roles, 'phases': phases,
        'connections_closed_by_failure': broken, 'connections_unclosed': unclosed,
        'db_time_s_per_analysis': dist([spent[i] for i in ids], 2),
        'operation_errors': dict(Counter(refusal(m) for r in runs.values() for _, m in r['op_errors'])),
        'policy_steps': (lambda s: s and {  # --policy 실행만: 단계 전체 시간(연결·풀 대기·SQL·재시도 대기)과 결과
            'steps': len(s), 'elapsed_s': dist([x[0] for x in s], 2), 'attempts': dict(Counter(x[1] for x in s)),
            'outcomes': dict(Counter(x[2] for x in s)),
            'over_deadline': sum(x[3] > 0 for x in s), 'over_s_max': round(max([x[3] for x in s] + [0]), 2),
            'over_s': dist([x[3] for x in s if x[3] > 0], 2),
            'by_step': {k: dict(Counter(f"{x[2]}{'/over' if x[3] > 0 else ''}" for x in s if x[4] == k))
                        for k in sorted({x[4] for x in s})},
            # 진행 중인 호출은 끊지 않는다: '마감 내 성공'은 모든 DB 단계가 마감 안에 끝난 워커 성공만 센다
            'worker_ok': sum(r['exit'] == 0 for r in runs.values()),
            'worker_ok_all_steps_within': sum(r['exit'] == 0 and all(x[3] <= 0 for x in r.get('steps', []))
                                              for r in runs.values())})(
            [x for r in runs.values() for x in r.get('steps', [])]),
        'model_calls_per_analysis': dict(Counter(r.get('model_calls', 0) for r in runs.values())),
        'retries': {'total': sum(len(r['retry_waits']) for r in runs.values()),
                    'analyses_retrying': sum(bool(r['retry_waits']) for r in runs.values()),
                    'wait_s_per_analysis': dist([sum(w for _, w in r['retry_waits']) for r in runs.values()], 2),
                    'exhausted_ops': sum(r['exhausted'] for r in runs.values()),
                    'analyses_exhausted': sum(bool(r['exhausted']) for r in runs.values())},
        'generator': {'procs': len(children), 'start_lag_ms': dist([x * 1000 for x in lags], 1),
                      'sleep_overshoot_ms': dist([x * 1000 for r in runs.values() for x in r['overshoot']], 1),
                      'tool_behind_s': dist([x for r in runs.values() for x in r['behind']], 2),
                      'cpu_cores_peak_per_proc': max((s[1] for c in children for s in c['cpu']), default=None),
                      'cpu_seconds': sum(c['cpu_seconds'] for c in children),
                      'max_rss_mb_per_proc': max((c['max_rss_mb'] for c in children), default=None),
                      'threads_peak_per_proc': max((s[2] for c in children for s in c['cpu']), default=None)},
        'postgres': {'peak_backends': {role: max((s.get(role, 0) for s in samples), default=0)
                                       for role in ('writer', 'reader')},
                     'peak_backends_total': max((sum(s.values()) for s in samples), default=0),
                     'top_waits': dict(sorted(waits.items(), key=lambda kv: -kv[1])[:6]),
                     'cpu_pct': dist([b[2] for b in box if b[1] == CONTAINER], 1),
                     'mem_mib_max': max((b[3] for b in box if b[1] == CONTAINER), default=None),
                     'counters_delta': {k: after[k] - before[k] for k in before}},
        'pool': pool_summary(pool, box, pool_before, pool_after),
        'db': db,
    }, {'runs': {i: {k: v for k, v in runs[i].items() if k not in ('overshoot', 'behind')} for i in ids},
        'timeline': timeline(events, t0)}


def db_state(ids, runs):
    """최종 저장 상태: 요청 ID 집합, 상태별 수, 저장 모양, 부분 저장, 툴 기록 수 대조."""
    with admin() as c:
        status = {r['analysis_id']: r['status'] for r in c.execute('SELECT analysis_id, status FROM outlook_analyses')}
        tables = [r['table_name'] for r in c.execute(
            "SELECT table_name FROM information_schema.columns WHERE column_name='analysis_id' AND table_schema='public' "
            "AND table_name LIKE 'outlook\\_%' AND table_name <> 'outlook_analyses' ORDER BY 1")]
        rows = {t: {r['analysis_id']: r['n'] for r in c.execute(f'SELECT analysis_id, count(*) n FROM {t} GROUP BY 1')}
                for t in tables}
        rows['tool_runs'] = {r['analysis_id']: r['n'] for r in c.execute(
            'SELECT outlook_analysis_id analysis_id, count(*) n FROM tool_runs GROUP BY 1')}
    completed = [i for i in ids if status.get(i) == 'completed']
    shapes = {tuple(rows[t].get(i, 0) for t in tables) for i in completed}
    expected_tool_rows = {i: runs[i]['calls'] + 1 for i in completed}  # + execute_request 의 요인 조회 1건
    return {'completed': len(completed), 'failed': sum(status.get(i) == 'failed' for i in ids),
            'running': sum(status.get(i) == 'running' for i in ids), 'no_row': sum(i not in status for i in ids),
            'unexpected_rows': len(set(status) - set(ids)),
            'worker_ok_db_not_completed': sum(runs[i]['exit'] == 0 and status.get(i) != 'completed' for i in ids),
            'publication_shapes': [dict(zip(tables, s)) for s in shapes],
            'partial_publications': sum(any(rows[t].get(i, 0) for t in tables) for i in ids if status.get(i) != 'completed'),
            'tool_rows_mismatch': sum(rows['tool_runs'].get(i, 0) != n for i, n in expected_tool_rows.items())}


def pool_summary(pool, box, before, after):
    if not before:
        return None
    by_role = {}
    for _, rows in pool:
        for role, active, waiting, sv_active, sv_other, maxwait in rows:
            mine = by_role.setdefault(role, {'clients_active_max': 0, 'clients_waiting_max': 0,
                                             'server_active_max': 0, 'server_total_max': 0, 'maxwait_s_max': 0.0})
            mine['clients_active_max'] = max(mine['clients_active_max'], active)
            mine['clients_waiting_max'] = max(mine['clients_waiting_max'], waiting)
            mine['server_active_max'] = max(mine['server_active_max'], sv_active)
            mine['server_total_max'] = max(mine['server_total_max'], sv_active + sv_other)
            mine['maxwait_s_max'] = round(max(mine['maxwait_s_max'], maxwait), 3)
    delta = {k: after[k] - before[k] for k in before}
    assigned = delta.get('total_server_assignment_count') or delta.get('total_xact_count') or 1
    return {'by_role': by_role, 'stats_delta': delta,
            'avg_wait_ms_per_assignment': round(delta['total_wait_time'] / 1000 / assigned, 2),  # 통계 시간 단위는 µs
            'pgbouncer_cpu_pct': dist([b[2] for b in box if 'pgbouncer' in b[1]], 1),
            'pgbouncer_mem_mib_max': max((b[3] for b in box if 'pgbouncer' in b[1]), default=None)}


def timeline(events, t0):
    """초 단위: 연결 시도, 거절, 그 초에 열린 연결 수(클라이언트 기록)."""
    seconds = {}
    for e in events:
        bucket = seconds.setdefault(int(e['s'] - t0), [0, 0])
        bucket[0] += 1
        bucket[1] += 'e' in e
    return sorted((k, *v) for k, v in seconds.items())


# ── 실행 ────────────────────────────────────────────────────────────────────────────────
def run(args):
    roles = {r['rolname']: r['rolconnlimit'] for r in setup(args.writer_limit, args.reader_limit)}
    configs = role_configs()
    if not args.startup_options:  # 설정을 버리고 연결만 되게 하지 않는다: 같은 값이 역할 기본값에 있어야 돈다
        need = {'edge_analysis_v2_writer': {'statement_timeout=15s', 'idle_in_transaction_session_timeout=30s'},
                'edge_analysis_v2_reader': {'default_transaction_read_only=on', 'statement_timeout=15s'}}
        missing = {r: sorted(v - set(configs.get(r, []))) for r, v in need.items() if v - set(configs.get(r, []))}
        if missing:
            raise SystemExit(f'역할 기본값이 시작 옵션과 다르다(pool_check.py 가 reader 값을 넣는다): {missing}')
    quiet_start(args.pool_console)  # TRUNCATE 가 남은 트랜잭션에 막히기 전에 정리·거부한다
    reset(definitions=True)
    budget = pool_budget(args.pool_console)
    pool_writer = args.pool_writer or args.writer_limit  # 연결 몫 분리: 풀 writer 몫 < 역할 한도, 나머지는 직접 연결 몫
    if budget and (budget != {'writer': pool_writer, 'reader': args.reader_limit} or pool_writer > args.writer_limit):
        raise SystemExit(f'풀 서버 연결 예산 {budget} 이 역할 한도 writer {args.writer_limit}·reader {args.reader_limit} 와 다르다')
    rng = random.Random(args.seed)
    profile = json.loads(Path(args.profile).read_text())['runs'] if args.profile else None
    research = []
    if args.research_profile:
        research = [max(0.0, v['start_to_first_tool_s'] - 1.0)
                    for v in json.loads(Path(args.research_profile).read_text())['per_etf'].values()]
    analyses = [{'id': uuid4().hex, 'etf': f'9{i:05d}', 'start_after': args.start_spread * i / args.n,
                 'profile': i % len(profile) if profile else None, 'scale': round(rng.uniform(0.9, 1.1), 3),
                 'research': research[i % len(research)] if research else 0.0} for i in range(args.n)]
    with admin() as c, c.transaction():
        c.cursor().executemany('INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES (%s,%s,%s)',
                               [(ARN + a['id'], a['id'][:32], 'load900:' + a['id']) for a in analyses])
    procs = args.procs or min(15, max(1, math.ceil(args.n / 60)))
    label = args.label or (f'n{args.n}-{"profile" if profile else f"llm{args.llm:g}-t{args.tools}"}'
                           f'{f"-spread{args.start_spread:g}" if args.start_spread else ""}'
                           f'{f"-end{args.align_end:g}" if args.align_end else ""}'
                           f'{"-pool" if args.pool_console else ""}-{time.strftime("%m%d%H%M%S")}')
    folder = OUT/label
    folder.mkdir(parents=True, exist_ok=True)
    children = []
    for k in range(procs):
        spec = {'analyses': analyses[k::procs], 'profile': profile, 'src': args.src, 'llm': args.llm,
                'tools': args.tools, 'align_end': args.align_end, 'host': args.db_host, 'port': args.port,
                'startup_options': args.startup_options, 'policy': args.policy, 'deadline': args.deadline,
                'pool_wait': args.pool_wait, 'margin': args.margin, 'backoff_cap': args.backoff_cap,
                'bad_password': args.bad_password, 'inject_unknown': args.inject_unknown, 'lose_reply': args.lose_reply}
        (folder/f'spec-{k}.json').write_text(json.dumps(spec))
        command = [sys.executable, __file__]
        if args.net:  # Docker Desktop 포트 중계를 거치지 않도록 DB 와 같은 네트워크의 컨테이너에서 돈다
            command = ['docker', 'run', '-i', '--rm', '--network', args.net, '--name', f'v2load900-gen-{os.getpid()}-{k}',
                       '-v', f'{REPO}:{REPO}', '--tmpfs', str(WORK), '-w', str(HERE), '-e', f'EDGE_REPO={REPO}',
                       args.image, 'python', 'load900.py']
        children.append(subprocess.Popen(command + ['child', '--spec', str(folder/f'spec-{k}.json'),
                                          '--out', str(folder/f'child-{k}.json')], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=(folder/f'child-{k}.err').open('w'), text=True))
    clock_offsets = []
    for proc in children:
        line = proc.stdout.readline().split()
        if not line or line[0] != 'ready':
            raise RuntimeError('child did not start; see child-*.err')
        clock_offsets.append(round(time.time() - float(line[1]), 3))  # 자식 시계가 늦은 만큼(+ 파이프 지연)
    stop, pg, box, pool, observe_errors = threading.Event(), [], [], [], []
    containers = [CONTAINER] + (['v2load900-pgbouncer-1'] if args.pool_console else [])
    watchers = observe(stop, pg, box, containers, pool, args.pool_console, observe_errors)
    if args.policy:  # 풀 대기 상한을 이 실행의 정책 값으로(전역 설정, 실행 콘솔 SET — 재시작·RELOAD 하면 ini 값으로 돌아간다)
        if args.pool_wait + args.margin > args.deadline:
            raise SystemExit('--policy 는 풀 대기 + 여유 ≤ 단계 마감이어야 한다')
    if args.policy and args.pool_console:
        with psycopg.connect(args.pool_console, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
            c.execute(f'SET query_wait_timeout = {args.pool_wait:g}')
            applied = next(r[1] for r in c.execute('SHOW CONFIG').fetchall() if r[0] == 'query_wait_timeout')
        if float(applied) != args.pool_wait:  # noqa: 풀 경로만
            raise SystemExit(f'query_wait_timeout 적용 실패: {applied}')
    before, pool_before = database_counters(), pool_stats(args.pool_console)
    t0 = time.time() + 2.0
    for proc in children:
        proc.stdin.write(f'{t0}\n')
        proc.stdin.close()
    codes = [proc.wait() for proc in children]
    after, pool_after = database_counters(), pool_stats(args.pool_console)
    stop.set()
    for thread in watchers:
        thread.join(timeout=10)
    outputs = [json.loads((folder/f'child-{k}.json').read_text()) for k in range(procs)
               if codes[k] == 0 and (folder/f'child-{k}.json').exists()]
    result, detail = summarize(analyses, outputs, t0, pg, box, before, after, pool, pool_before, pool_after)
    result['child_exit_codes'], result['child_clock_offset_s'] = codes, [min(clock_offsets), max(clock_offsets)]
    expected = {'pg': len(pg), 'container': len({b[1] for b in box}) == len(containers) and len(box)}
    if args.pool_console:
        expected['pool'] = len(pool)
    missing = [k for k, v in expected.items() if not v]
    result['observation'] = {'errors': observe_errors + [f'no {k} samples' for k in missing],
                             'samples': {'pg': len(pg), 'container': len(box), 'pool': len(pool)}}
    (folder/'detail.json').write_text(json.dumps(detail | {'pg': pg, 'container': box, 'pool': pool}))
    conditions = {'n': args.n, 'procs': procs, 'profile': args.profile or None, 'llm_s': None if profile else args.llm,
                  'tools': None if profile else args.tools, 'align_end_s': args.align_end, 'start_spread_s': args.start_spread,
                  'src_s': args.src, 'research_profile': args.research_profile or None, 'db': f'{args.db_host}:{args.port}',
                  'generator_net': args.net or 'host', 'generator_image': args.image if args.net else sys.version.split()[0],
                  'psycopg': psycopg.__version__, 'pool_writer': args.pool_writer or args.writer_limit, 'policy': args.policy and {k: getattr(args, k) for k in
                      ('policy', 'deadline', 'pool_wait', 'margin', 'backoff_cap')}, 'startup_options': args.startup_options, 'role_config': configs,
                  'pool_console': bool(args.pool_console), 'pool_budget': budget,
                  'role_limits': roles, 'server': settings(), 'seed': args.seed, 'detail': str(folder)}
    record(f'load900-{label}', conditions, result)
    if result['observation']['errors'] or any(codes):  # 기록은 남기되 불완전한 관측을 성공으로 끝내지 않는다
        raise SystemExit(f"incomplete run: observation={result['observation']['errors']} child_exit_codes={codes}")


def resummarize(args):
    """앞 실행의 자식 원본(results-900/<이름>/)으로 연결·재시도 지표를 다시 집계해 새 줄로 남긴다.

    DB 최종 상태·생성기 지표·관측 표본은 원래 기록을 쓴다(그 사이 DB 는 다음 실행이 비웠다)."""
    name = f'load900-{args.label}'
    original = [e for e in map(json.loads, RESULTS.read_text().splitlines()) if e['name'] == name][-1]
    folder = Path(original['conditions']['detail'])
    specs = sorted(folder.glob('spec-*.json'), key=lambda f: int(f.stem.split('-')[1]))
    analyses = [a for f in specs for a in json.loads(f.read_text())['analyses']]
    children = [json.loads((folder/f'child-{k}.json').read_text()) for k in range(len(specs))]
    detail = json.loads((folder/'detail.json').read_text())
    ended = max(r['ended'] for c in children for r in c['runs'].values() if 'ended' in r)
    t0 = ended - original['result']['elapsed_s']  # t0 는 기록하지 않았다 — 경과 시간에서 되돌린다(0.1초 단위)
    counters = original['result']['postgres']['counters_delta']
    pool_delta = (original['result'].get('pool') or {}).get('stats_delta') or {}
    result, _ = summarize(analyses, children, t0, detail['pg'], detail['container'], {k: 0 for k in counters}, counters,
                          detail.get('pool', []), {k: 0 for k in pool_delta}, pool_delta, db=original['result']['db'])
    for key in ('generator', 'elapsed_s', 'child_exit_codes', 'child_clock_offset_s', 'observation'):
        result[key] = original['result'].get(key)
    record(name, original['conditions'] | {'resummarized': {'from_record_at': original['at'], 'reason': args.reason}},
           result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('run')
    p.add_argument('--n', type=int, default=37); p.add_argument('--procs', type=int, default=0)
    p.add_argument('--profile', default='', help='dev/tool_profile.py 결과. 주면 --llm·--tools 는 쓰지 않는다')
    p.add_argument('--llm', type=float, default=30); p.add_argument('--tools', type=int, default=40)
    p.add_argument('--align-end', type=float, default=0, help='모든 모델 대역이 t0+이 초에 함께 끝난다(최종 저장 동시)')
    p.add_argument('--start-spread', type=float, default=0, help='분석 시작을 이 초에 고르게 나눈다(0 = 한꺼번에)')
    p.add_argument('--src', type=float, default=0.3); p.add_argument('--research-profile', default='')
    p.add_argument('--writer-limit', type=int, default=20); p.add_argument('--reader-limit', type=int, default=6)
    p.add_argument('--db-host', default='127.0.0.1'); p.add_argument('--port', type=int, default=55461,
                   help='writer·reader 접속 주소(풀 비교 때 PgBouncer). 관측용 관리자 연결은 늘 127.0.0.1:55461')
    p.add_argument('--net', default='', help='자식을 이 Docker 네트워크의 컨테이너로 띄운다(예: v2load900_default)')
    p.add_argument('--image', default='v2load900-gen')
    p.add_argument('--no-startup-options', dest='startup_options', action='store_false',
                   help='연결 시작 옵션을 빼고 역할 기본값을 쓴다(PgBouncer 는 options 의 statement_timeout 을 거절한다)')
    p.add_argument('--pool-console', default='', help='PgBouncer 관리 콘솔 DSN. 주면 대기열·서버 연결·통계를 남긴다')
    p.add_argument('--seed', type=int, default=1157); p.add_argument('--label', default='')
    p.add_argument('--pool-writer', type=int, default=0, help='풀 writer 서버 연결 상한(0 = --writer-limit). 역할 한도는 --writer-limit')
    p.add_argument('--policy', choices=['a', 'b', 'p'], default='', help='실험 정책(운영 코드 아님). a=풀 대기 초과 재시도 없음, b=지터 재시도')
    p.add_argument('--deadline', type=float, default=0, help='DB 단계 하나의 시간 예산(연결·풀 대기·SQL·재시도 대기, 단계 시작부터)')
    p.add_argument('--pool-wait', type=float, default=20, help='PgBouncer query_wait_timeout(실행 콘솔로 SET)')
    p.add_argument('--margin', type=float, default=5, help='새 시도는 남은 시간 ≥ 풀 대기 + 이 값일 때만')
    p.add_argument('--backoff-cap', type=float, default=4)
    p.add_argument('--bad-password', choices=['writer', 'reader'], default='', help='장애 주입: 이 역할의 비밀번호를 틀리게')
    p.add_argument('--inject-unknown', default='', help='장애 주입: 이름에 이 문자열이 든 DB 단계가 매 시도 분류 밖 오류')
    p.add_argument('--lose-reply', default='', help='장애 주입: 이 단계의 첫 시도 커밋 뒤 응답 유실')
    p = commands.add_parser('resummarize')
    p.add_argument('--label', required=True); p.add_argument('--reason', required=True)
    p = commands.add_parser('child')
    p.add_argument('--spec', required=True); p.add_argument('--out', required=True)
    args = parser.parse_args()
    {'run': run, 'child': child, 'resummarize': resummarize}[args.command](args)
