"""v2 실행 구조 대안 검토 탐침 (ALPHA-1157 후속, 로컬 격리 전용).

probe.py 와 같은 컨테이너, 마이그레이션, 대역(LLM·원천 조회·S3)을 쓰고 실제 worker.run·execute_request·저장 코드를 돌린다.
현재 구조(모드 a)와 '모델 대기 중 DB 연결을 쥐지 않는' 구조(모드 b)를 같은 입력으로 비교한다.

  a  현재 코드 그대로. 분석 1건이 writer 연결 3개를 모델 대기 내내 쥔다. 락 연결은 세션 락 둘(ETF 락, 워커 슬롯),
     작업 연결은 세션 락 하나(분석 ID 락)를 쥐고, 감사 연결은 락 없이 툴 기록 전용이다.
  b  서비스 코드는 그대로 두고 결과 DB 연결만 ShortConnection 으로 바꿔 끼운다. 트랜잭션·커서·문장 하나마다
     연결을 열고 닫는다. 그래서 세션 advisory lock 세 개는 그 문장이 끝나면 풀려 보호 효과가 없다. b 는 그 보호를
     단건 SFN 의 자리 표(analysis_execution_slots), 실행 이름, manifest 선점에 맡긴다는 가정이다.
     --retry 초 동안 연결 생성 실패를 0.5초 간격으로 다시 시도한다(0 이면 재시도 없음, 모드 a 는 늘 0).

이 탐침에는 SFN 자리 표가 없다. 모든 실행은 워커 슬롯 99(사실상 제어 없음)로 돈다. 그래서 a 의 37건 결과는
배포 상태(자리 3에서 3건씩 실행)의 실패율이 아니라 '한꺼번에 띄우면 무엇이 먼저 막히는가'다.

  review_probe.py occupancy [--modes a,b --n 37 --llm 30 --tools 40 --writer-limit -1 --reader-limit -1 --retry 0]
  review_probe.py faults    [--n 3 --llm 20 --tools 40 --deny 5 --retry 15]   # 모델 대기 중 연결 끊김, 저장 순간 DB 불가
  review_probe.py save-faults                                               # 저장 트랜잭션 도중 끊김, 커밋 응답 유실
  review_probe.py dup                                                       # b 에서 같은 ID, 같은 ETF 를 무엇이 막는가
  review_probe.py order                                                     # 같은 ETF 가격변동 두 건의 순서가 뒤집힐 때
  review_probe.py control   [--rounds 20]                                   # 실행 제어 함수의 동시 호출·자리 회수
  review_probe.py retry-safety                                              # 저장 재호출, 툴 기록 재저장의 동일성 확인
  review_probe.py sites                                                     # b 에서 분석 1건이 연결을 어디서 몇 번 여는가

시간은 줄여 잰다(모델 대기 30초, 실제는 240~455초). 툴 호출 40회는 dev 실데이터 전망의 중앙값 41회에 맞췄다.
그래서 같은 툴 호출 수가 약 12배 촘촘하게 몰린다. 연결 수는 툴 호출 수를 따라가고, 연결 생성 빈도는 실제보다 불리한 쪽이다.

save-faults 의 저장 재시도(--save-retry)와 retry-safety 의 툴 기록 되읽기는 이 탐침 안의 시험 구현이다. 서비스 코드에는 없다.
"""
import argparse
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
import json
import signal
import subprocess
import sys
import threading
import time
from unittest.mock import Mock
from uuid import uuid4

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from probe import ANALYSIS_AT, DSN, HERE, WORK, LocalSession, admin, etf_codes, record, reset, retarget, setup


class _Rows:
    """트랜잭션 밖 execute() 결과. 연결을 닫기 전에 행을 다 읽어 둔다."""
    def __init__(self, rows, rowcount):
        self._rows, self.rowcount = list(rows), rowcount

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class _Info:
    def __init__(self, status):
        self.transaction_status = status


def _site():
    """연결을 여는 v2 코드의 가장 안쪽 함수 이름(연결 수 내역용)."""
    frame = sys._getframe(2)
    while frame is not None:
        path = frame.f_code.co_filename
        if 'edge_analysis_v2' in path and 'review_probe' not in path:
            return frame.f_code.co_name
        frame = frame.f_back
    return 'unknown'


class ShortConnection:
    """psycopg Connection 처럼 보이되 연산 사이에 아무것도 쥐지 않는다.

    transaction()·cursor()·execute() 하나가 실제 연결 하나를 열고 끝나면 닫는다. 트랜잭션 안의 호출은 그
    트랜잭션의 연결을 쓴다(중첩 transaction 은 savepoint). 연결 실패는 retry 초 동안 다시 연다.
    """
    autocommit = True

    def __init__(self, factory, retry=0.0):
        self._factory, self._retry, self._current = factory, retry, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    @property
    def info(self):
        return _Info(self._current.info.transaction_status if self._current else TransactionStatus.IDLE)

    def _open(self):
        deadline, site = time.time() + self._retry, _site()
        while True:
            try:
                return self._factory(site)
            except Exception:
                if time.time() >= deadline:
                    raise
                time.sleep(0.5)

    @contextmanager
    def transaction(self):
        if self._current is not None:
            with self._current.transaction() as tx:
                yield tx
            return
        connection = self._open()
        self._current = connection
        try:
            with connection.transaction() as tx:
                yield tx
        finally:
            self._current = None
            connection.close()

    @contextmanager
    def cursor(self, **kwargs):
        if self._current is not None:
            with self._current.cursor(**kwargs) as cur:
                yield cur
            return
        connection = self._open()
        try:
            with connection.cursor(**kwargs) as cur:
                yield cur
        finally:
            connection.close()

    def execute(self, query, params=None, **kwargs):
        if self._current is not None:
            return self._current.execute(query, params, **kwargs)
        connection = self._open()
        try:
            cur = connection.execute(query, params, **kwargs)
            return _Rows(cur.fetchall() if cur.description else [], cur.rowcount)
        finally:
            connection.close()


# ── 분석 1건 (= ECS 태스크 1개에 대응하는 프로세스) ──────────────────────────────────────
def one(args):
    from edge_analysis_v2.analysis import service
    from edge_analysis_v2.cloud import worker
    from edge_analysis_v2.storage.database import ResultDatabaseError
    from edge_analysis_v2.tools.fixture_data import make_fixture

    connections, marks, began = [], {}, time.time()
    retry = args.retry if args.mode == 'b' else 0.0

    def connect(role, site=None, **options):
        event = {'role': role, 'open_start': time.time(), 'site': site}
        connections.append(event)
        try:
            connection = psycopg.connect(DSN[role], **options)
        except Exception as exc:
            event['error'] = str(exc).strip().splitlines()[-1][:160]
            raise ResultDatabaseError('Writer connection unavailable') from None
        event['opened'] = time.time()
        close = connection.close
        def closing():
            event.setdefault('closed', time.time())
            close()
        connection.close = closing
        return connection

    # save-kill: 발행 트랜잭션이 첫 항목을 넣으려는 순간 그 세션을 서버에서 끊는다(한 번만).
    armed = {'kill': args.inject == 'save-kill', 'lost': args.inject == 'save-ack-lost'}
    match = 'INSERT INTO outlook_items' if args.kind == 'outlook' else 'INSERT INTO movement_items'

    class KillCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            if armed['kill'] and isinstance(query, str) and match in query:
                armed['kill'] = False
                marks['killed_in_save'] = time.time()
                with admin() as a:
                    a.execute('SELECT pg_terminate_backend(%s)', (self.connection.info.backend_pid,))
            return super().execute(query, params, **kwargs)

    def writer(site=None):  # 실제 connect_results 와 같은 세션 옵션
        connection = connect('writer', site, autocommit=True,
                             options='-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000')
        connection.cursor_factory = KillCursor
        return connection

    def connect_results(ca_path, *, session=None, cloud=False):
        return writer() if args.mode == 'a' else ShortConnection(writer, retry)

    def connect_sources(ca_path, *, session=None, cloud=False):
        deadline = time.time() + retry
        while True:
            try:
                connection = connect('reader', row_factory=dict_row,
                                     options='-c default_transaction_read_only=on -c statement_timeout=15000')
                break
            except Exception:
                if time.time() >= deadline:
                    raise
                time.sleep(0.5)
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        return connection

    def load_source(connection, ticker, analysis_at):  # 대역: 원천 읽기 시간만 흉내
        connection.execute('SELECT pg_sleep(%s)', (args.src,))
        return retarget(make_fixture(analysis_at=analysis_at), ticker)

    async def model(**kwargs):  # 대역: 모델 대기 사이사이의 툴 호출(감사 기록)만 흉내
        marks['model_start'] = time.time()
        marks['model_calls'] = marks.get('model_calls', 0) + 1
        marks['saw_previous'] = bool(kwargs['initial'].get('previous_analysis'))
        call, news = kwargs['call'], kwargs['initial']['news'][0]['news_id']
        step, reference = args.llm / (args.tools + 1), None
        for index in range(args.tools):
            await asyncio.sleep(step)
            reference = (await asyncio.to_thread(call, 'get_issue_evidence',
                                                 {'news_ids': [news], 'include_body': False}))['tool_run_id']
            if index == 0 and args.kind == 'outlook':
                await asyncio.to_thread(call, 'write_outlook_body', {'title': '계약 이행을 확인해요', 'items': [
                    dict(id='contract', title_keyword='계약', sentences=['판매 물량을 확보했어요.'], tool_run_ids=[reference])]})
        await asyncio.sleep(step)
        marks['model_end'] = time.time()
        if args.kind == 'movement':
            return {'new_items': [dict(candidate_id='new', type='이슈', title_keyword='계약',
                    sentence='판매 물량을 확보했어요.', sentiment='positive', tool_run_ids=[reference])],
                    'selected_item_ids': ['new'], 'summary': '공급 계약을 확인해요.'}
        return {'outlook': {'direction': '상승'}, 'summary_card': {'title': '물량 확보', 'summary': '이행을 확인해요.'},
                'factors': [{'type': f, 'sticker': '중립', 'sentence': '자료를 확인했어요.'}
                            for f in ('이슈', '차트', '매크로', '밸류', '수급')],
                'conclusion': {'title': '이행 확인', 'supports': [{'label': '계약', 'tool_run_ids': [reference]}],
                               'burdens': [], 'sentence': '진행 상황을 확인해요.'},
                'issue_detail': {'headline': '공급 계약', 'items': [dict(title_keyword='계약',
                    sentence='판매 물량을 확보했어요.', sentiment='positive', tool_run_ids=[reference])]}}

    from edge_analysis_v2.storage.publications import PublicationStore
    for name in ('save_outlook', 'save_movement'):
        original = getattr(PublicationStore, name)
        def lost(self, *a, _original=original, **k):  # save-ack-lost: 커밋은 됐는데 호출자는 실패로 받는다(한 번만)
            result = _original(self, *a, **k)
            if armed['lost']:
                armed['lost'] = False
                marks['ack_lost_after_commit'] = time.time()
                raise psycopg.OperationalError('injected: commit outcome unknown to caller')
            return result
        def retrying(self, *a, _inner=lost, **k):  # --save-retry: 연결 계열 오류만, 시간 상한 안에서 트랜잭션 전체를 다시
            deadline = time.time() + args.save_retry
            while True:
                try:
                    return _inner(self, *a, **k)
                except psycopg.OperationalError:
                    if time.time() >= deadline:
                        raise
                    marks.setdefault('save_retries', 0)
                    marks['save_retries'] += 1
                    time.sleep(0.5)
        setattr(PublicationStore, name, retrying)

    worker.connect_results, worker.connect_sources = connect_results, connect_sources
    worker.load_source, worker.load_flow, worker.load_prices = load_source, (lambda c, d: d), (lambda c, d, **_: d)
    worker.execute_request = partial(service.execute_request, model_call=model)
    request = {'analysis_id': args.id, 'kind': args.kind, 'etf_code': args.etf, 'analysis_at': args.at or ANALYSIS_AT}
    error = None
    try:
        worker.run(request, bucket='local', ca_path=HERE, folder=WORK/'artifacts'/args.id,
                   key='fake-key', model='stub', session=LocalSession(), slots=99)
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)[:160]
        cause = exc.__cause__ or exc.__context__
        if cause is not None:
            error += ' <- ' + type(cause).__name__ + ': ' + str(cause).strip().splitlines()[0][:120]
    print(json.dumps({'id': args.id, 'kind': args.kind, 'etf': args.etf, 'exit': 1 if error else 0, 'error': error,
                      'marks': marks, 'began': began, 'ended': time.time(), 'connections': connections},
                     ensure_ascii=False), flush=True)
    sys.exit(1 if error else 0)


# ── 여러 건 동시 실행과 관측 ────────────────────────────────────────────────────────────
def peak(intervals):
    """[(열림, 닫힘)] 의 최대 겹침 수. 클라이언트 기록이라 0.2초 표본이 놓치는 짧은 연결도 센다."""
    points = sorted([(a, 1) for a, _ in intervals] + [(b, -1) for _, b in intervals], key=lambda p: (p[0], p[1]))
    best = now = 0
    for _, delta in points:
        now += delta
        best = max(best, now)
    return best


def launch(specs, *, mode, llm, tools, src=0.3, retry=0.0, faults=()):
    """faults: [(초, 'terminate')] 또는 [(초, 'deny', 지속 초)]. 기준은 전체 시작 시각.

    terminate 는 writer 세션을 모두 끊는다. deny 는 writer 로그인을 막고 세션을 끊은 뒤 지속 초 뒤에 푼다
    (장애 조치처럼 연결이 끊기고 잠시 새 연결도 안 되는 상황)."""
    stop, samples, notes = threading.Event(), [], []

    def sample():
        with admin() as c:
            while not stop.wait(0.2):
                rows = c.execute("SELECT usename, count(*) n FROM pg_stat_activity WHERE backend_type='client backend' "
                                 "AND usename LIKE 'edge_analysis_v2%' GROUP BY 1").fetchall()
                samples.append({r['usename'].rsplit('_', 1)[-1]: r['n'] for r in rows})

    def inject():
        with admin() as c:
            for fault in sorted(faults):
                time.sleep(max(0, started + fault[0] - time.time()))
                if fault[1] == 'deny':
                    c.execute('ALTER ROLE edge_analysis_v2_writer NOLOGIN')
                n = len(c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                                  "WHERE usename='edge_analysis_v2_writer'").fetchall())
                notes.append({'at': round(time.time()-started, 2), 'fault': fault[1], 'terminated': n})
                if fault[1] == 'deny':
                    time.sleep(fault[2])
                    c.execute('ALTER ROLE edge_analysis_v2_writer LOGIN')
                    notes.append({'at': round(time.time()-started, 2), 'fault': 'login-restored'})

    started = time.time()
    threads = [threading.Thread(target=sample, daemon=True), threading.Thread(target=inject, daemon=True)]
    for thread in threads:
        thread.start()

    def start(s):
        time.sleep(max(0, started + s.get('start_after', 0) - time.time()))
        return subprocess.Popen([sys.executable, __file__, 'one', '--mode', mode, '--kind', s['kind'], '--etf', s['etf'],
                                 '--id', s['id'], '--llm', str(llm), '--tools', str(tools), '--src', str(src),
                                 '--retry', str(retry), '--at', s.get('at', ''), '--inject', s.get('inject', 'none'),
                                 '--save-retry', str(s.get('save_retry', 0))],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    specs = sorted(specs, key=lambda s: s.get('start_after', 0))
    procs = [start(s) for s in specs]  # start_after 가 있으면 앞 건이 끝나기를 기다리지 않고 그 시각에 시작한다
    for spec, proc in zip(specs, procs):
        if spec.get('kill_after') is not None:
            time.sleep(max(0, started + spec['kill_after'] - time.time()))
            proc.send_signal(signal.SIGKILL)
    outputs = []
    for spec, proc in zip(specs, procs):
        out, err = proc.communicate()
        line = out.strip().splitlines()[-1] if out.strip() else None
        outputs.append(json.loads(line) if line else spec | {'exit': proc.returncode, 'error': err.strip()[-300:] or 'killed',
                                                             'connections': [], 'marks': {}})
    elapsed = time.time() - started
    stop.set()
    threads[0].join(timeout=5)
    threads[1].join(timeout=60)
    with admin() as c:
        status = {r['analysis_id']: r['status'] for kind in ('outlook', 'movement') for r in c.execute(
            f'SELECT analysis_id, status FROM {kind}_analyses')}
        tool_runs = c.execute('SELECT count(*) n FROM tool_runs').fetchone()['n']
    conns, end = [e for o in outputs for e in o['connections']], time.time()
    roles = {}
    for role in ('writer', 'reader'):
        opened = [e for e in conns if e['role'] == role and 'opened' in e]
        intervals = [(e['opened'], e.get('closed', end)) for e in opened]
        roles[role] = {'connects': len(opened), 'connect_errors': sum('error' in e for e in conns if e['role'] == role),
                       'peak_client': peak(intervals), 'peak_sampled': max((s.get(role, 0) for s in samples), default=0),
                       'conn_seconds': round(sum(b - a for a, b in intervals), 1)}
    windows = [(o['marks']['model_start'], o['marks']['model_end']) for o in outputs if 'model_end' in o.get('marks', {})]
    ids = {o['id'] for o in outputs}
    return {'elapsed_s': round(elapsed, 2), 'ok': sum(o['exit'] == 0 for o in outputs),
            'failed': sum(o['exit'] != 0 for o in outputs), 'max_concurrent_model': peak(windows),
            'errors': sorted({(o['error'] or '')[:200] for o in outputs if o['exit'] != 0}),
            'connect_error_texts': sorted({e['error'][:90] for e in conns if 'error' in e}),
            'roles': roles, 'faults': notes, 'tool_runs': tool_runs,
            'db_rows': {s: sum(1 for i in ids if status.get(i) == s) for s in ('completed', 'failed', 'running')}
                       | {'no_row': sum(1 for i in ids if i not in status)},
            'runs': [{k: o.get(k) for k in ('id', 'kind', 'etf', 'exit', 'error', 'marks')} | {'db': status.get(o['id'])}
                     | {'writer_sites': dict(Counter(str(e.get('site')) for e in o['connections']
                                                     if e['role'] == 'writer').most_common())}
                     for o in outputs]}


def limits(writer, reader):
    return {r['rolname']: r['rolconnlimit'] for r in setup(writer, reader)}


def occupancy(args):
    codes, _ = etf_codes()
    roles = limits(args.writer_limit, args.reader_limit)
    for mode in args.modes.split(','):
        reset(definitions=True)
        specs = [{'kind': args.kind, 'etf': codes[i % len(codes)], 'id': uuid4().hex} for i in range(args.n)]
        retry = args.retry if mode == 'b' else 0.0
        record(f'review-occupancy-{mode}-n{args.n}', {'mode': mode, 'n': args.n, 'kind': args.kind, 'llm_s': args.llm,
               'tools': args.tools, 'src_s': args.src, 'retry_s': retry, 'role_limits': roles},
               launch(specs, mode=mode, llm=args.llm, tools=args.tools, src=args.src, retry=retry))


def faults(args):
    """모델 대기 중 writer 연결이 모두 끊기거나, 모델이 끝나는 순간 writer 로그인이 몇 초 막힌다."""
    codes, _ = etf_codes()
    roles = limits(20, 6)
    cases = [('terminate-mid-wait', [(args.llm*0.5 + 1, 'terminate')]),
             ('deny-at-save', [(args.llm + 0.3, 'deny', args.deny)])]
    for label, plan in cases:
        for mode, retry in (('a', 0.0), ('b', 0.0), ('b', args.retry)):
            reset()
            specs = [{'kind': args.kind, 'etf': codes[i], 'id': uuid4().hex} for i in range(args.n)]
            record(f'review-fault-{label}-{mode}-retry{retry:g}', {'mode': mode, 'retry_s': retry, 'n': args.n,
                   'kind': args.kind, 'llm_s': args.llm, 'tools': args.tools, 'plan': plan, 'role_limits': roles},
                   launch(specs, mode=mode, llm=args.llm, tools=args.tools, retry=retry, faults=plan))


def dup(args):
    """b 에서 세션 락이 사라질 때 무엇이 중복을 막고 무엇이 못 막는가."""
    codes, _ = etf_codes()
    roles = limits(20, 6)
    reset()
    same = uuid4().hex
    r = record('review-dup-b-same-id-concurrent', {'mode': 'b', 'role_limits': roles, 'llm_s': args.llm},
               launch([{'kind': 'outlook', 'etf': codes[0], 'id': same}] * 2, mode='b', llm=args.llm, tools=args.tools))
    assert (r['ok'], r['failed']) == (1, 1) and 'PreconditionFailed' in r['errors'][0], r['errors']  # manifest 선점
    # 같은 ETF·다른 ID. 둘째를 1초 늦춘다. 같은 순간에 시작하면 대역이 문장 하나 동안만 잡는 ETF 락끼리
    # 부딪혀, 'b 에는 ETF 락이 없다'는 설계 대신 대역의 부산물을 재게 된다(첫 실행에서 관측).
    pair = lambda: [{'kind': 'outlook', 'etf': codes[0], 'id': uuid4().hex},
                    {'kind': 'outlook', 'etf': codes[0], 'id': uuid4().hex, 'start_after': 1}]
    reset()
    r = record('review-dup-b-same-etf-different-id', {'mode': 'b', 'role_limits': roles, 'llm_s': args.llm, 'stagger_s': 1},
               launch(pair(), mode='b', llm=args.llm, tools=args.tools))
    assert (r['ok'], r['db_rows']['completed']) == (2, 2), r  # 워커 안에서는 아무것도 막지 않는다
    reset()
    r = record('review-dup-a-same-etf-different-id', {'mode': 'a', 'role_limits': roles, 'llm_s': args.llm, 'stagger_s': 1},
               launch(pair(), mode='a', llm=args.llm, tools=args.tools))
    assert (r['ok'], r['failed']) == (1, 1) and 'ValueError' in r['errors'][0], r  # 워커의 ETF 락이 거절한다
    print('dup: all assertions passed')


OUTLOOK_ROWS = ('outlook_items', 'outlook_factors', 'outlook_conclusion_keywords', 'outlook_factor_metrics',
                'outlook_issue_items')


def outlook_rows(identity):
    with admin() as c:
        return {t: c.execute(f'SELECT count(*) n FROM {t} WHERE analysis_id=%s', (identity,)).fetchone()['n']
                for t in OUTLOOK_ROWS}


def save_run_or_confirm(store, connection, **saved):
    """시험 구현(서비스 코드에 없음): 결과를 모르는 재호출이 유일 키에 걸리면 저장된 기록을 되읽어 같은지 본다.

    같으면 저장된 응답을 돌려주고, 다르면 오류를 숨기지 않는다."""
    try:
        return store.save_run(**saved)
    except psycopg.errors.UniqueViolation:
        row = connection.execute('''SELECT tool_id, outlook_analysis_id, movement_analysis_id, arguments, context, output,
            status, error_message, started_at, finished_at FROM tool_runs WHERE tool_run_id=%s''',
            (saved['tool_run_id'],)).fetchone()
        outlook = saved['analysis_kind'] == 'outlook'
        expected = (saved['tool_id'], saved['analysis_id'] if outlook else None, None if outlook else saved['analysis_id'],
                    saved['arguments'], saved['context'], saved.get('output'),
                    'completed' if saved.get('output') is not None else 'failed', saved.get('error_message'),
                    saved['started_at'], saved['finished_at'])
        if tuple(row) != expected:
            raise ValueError('tool_run_id already holds different evidence') from None
        return row[5]


def retry_safety(args):
    """커밋 결과를 모르는 저장을 같은 시도 안에서 다시 부르면 안전한가(연결·트랜잭션 재시도를 넣을 때의 전제)."""
    from edge_analysis_v2.storage.publications import PublicationStore
    from edge_analysis_v2.storage.tool_runs import ToolStore
    codes, _ = etf_codes()
    limits(20, 6)
    reset()
    identity = uuid4().hex
    r = launch([{'kind': 'outlook', 'etf': codes[0], 'id': identity}], mode='b', llm=2, tools=3)
    assert r['ok'] == 1, r
    with psycopg.connect(DSN['writer'], autocommit=True) as connection:
        store = PublicationStore(connection)
        before, published = outlook_rows(identity), store.get_outlook(identity)
        again = store.save_outlook(identity, {}, {})  # 이미 완료 → 검증·쓰기 없이 저장된 결과를 돌려준다
        after = outlook_rows(identity)
        run = connection.execute('''SELECT tool_run_id, tool_id, arguments, context, output, started_at, finished_at
            FROM tool_runs WHERE outlook_analysis_id=%s AND status='completed' LIMIT 1''', (identity,)).fetchone()
        saved = dict(tool_run_id=run[0], tool_id=run[1], analysis_kind='outlook', analysis_id=identity, arguments=run[2],
                     context=run[3], output=run[4], started_at=run[5], finished_at=run[6])
        tools = ToolStore(connection)
        outcome = {}
        try:
            tools.save_run(**saved)
            outcome['plain_rerun_same_content'] = 'accepted'
        except psycopg.errors.UniqueViolation:
            outcome['plain_rerun_same_content'] = 'UniqueViolation'
        outcome['confirm_rerun_same_content'] = 'confirmed' if save_run_or_confirm(tools, connection, **saved) == run[4] else 'mismatch'
        changed = saved | {'output': run[4] | {'result': {'tampered': True}}}
        try:
            save_run_or_confirm(tools, connection, **changed)
            outcome['confirm_rerun_different_content'] = 'accepted'
        except ValueError as exc:
            outcome['confirm_rerun_different_content'] = 'rejected: ' + str(exc)
        stored_unchanged = connection.execute('SELECT output FROM tool_runs WHERE tool_run_id=%s', (run[0],)).fetchone()[0] == run[4]
    result = {'save_outlook_rerun_same_result': again == published, 'rows_before_after': [before, after],
              'tool_run': outcome, 'stored_tool_run_unchanged': stored_unchanged}
    assert (result['save_outlook_rerun_same_result'] and before == after and stored_unchanged
            and outcome == {'plain_rerun_same_content': 'UniqueViolation', 'confirm_rerun_same_content': 'confirmed',
                            'confirm_rerun_different_content': 'rejected: tool_run_id already holds different evidence'}), result
    record('review-retry-safety', {'mode': 'b'}, result)
    print('retry-safety: all assertions passed')


def save_faults(args):
    """발행 저장 트랜잭션 도중 세션이 끊기거나, 커밋은 됐는데 호출자가 실패로 받는 경우.

    save-kill: 첫 항목 INSERT 직전에 그 세션을 서버에서 끊는다(트랜잭션 도중). save-ack-lost: 커밋 직후 호출자에게
    연결 오류를 낸다. --save-retry 는 이 탐침 안의 시험 구현이다: 연결 계열 오류(OperationalError)만, 시간 상한 안에서
    발행 트랜잭션 전체를 다시 부른다. 모델은 다시 부르지 않는다."""
    codes, _ = etf_codes()
    roles = limits(20, 6)
    cases = (('baseline', 'b', 'none', 0), ('save-kill', 'a', 'save-kill', 0), ('save-kill', 'b', 'save-kill', 0),
             ('save-kill', 'b', 'save-kill', args.save_retry), ('save-ack-lost', 'b', 'save-ack-lost', 0),
             ('save-ack-lost', 'b', 'save-ack-lost', args.save_retry))
    expected = None
    results = []
    for label, mode, inject, save_retry in cases:
        reset()
        identity = uuid4().hex
        r = launch([{'kind': 'outlook', 'etf': codes[0], 'id': identity, 'inject': inject, 'save_retry': save_retry}],
                   mode=mode, llm=args.llm, tools=args.tools, retry=5.0 if mode == 'b' else 0.0)
        rows = outlook_rows(identity)
        expected = expected or rows
        marks = r['runs'][0]['marks'] or {}
        summary = {'exit': r['runs'][0]['exit'], 'db': r['runs'][0]['db'], 'rows': rows, 'rows_as_baseline': rows == expected,
                   'model_calls': marks.get('model_calls'), 'save_retries': marks.get('save_retries', 0),
                   'error': r['runs'][0]['error']}
        results.append((label, mode, save_retry, summary))
        record(f'review-save-fault-{label}-{mode}-retry{save_retry:g}', {'mode': mode, 'inject': inject,
               'save_retry_s': save_retry, 'llm_s': args.llm, 'tools': args.tools, 'role_limits': roles}, summary)
    print('\n'.join(f'{l:14} {m} retry{s:<3g} exit={x["exit"]} db={x["db"]} rows_as_baseline={x["rows_as_baseline"]} '
                    f'model_calls={x["model_calls"]} save_retries={x["save_retries"]}' for l, m, s, x in results))


def order(args):
    """같은 ETF 가격변동 두 건의 실행 순서가 뒤집힐 때(나중 사건이 먼저 실행, 또는 앞 사건이 늦게 끝남).

    자리 표가 막는 것은 동시 실행뿐이다. 여기서는 동시성 없이 순서만 뒤집어 기존 코드의 이전 설명 선택, 전달 가드,
    최신 조회가 무엇을 하는지 본다(모드 a, 현재 코드 그대로)."""
    from edge_analysis_v2.api.publications import PublicationReader
    codes, _ = etf_codes()
    limits(20, 6)
    reset()
    with admin() as c:
        c.execute('DELETE FROM tenant_delivery')
        if not c.execute('SELECT 1 FROM tenant LIMIT 1').fetchone():
            c.execute("INSERT INTO tenant(tenant_name, environment, status) VALUES ('probe', 'DEV', 'ACTIVE')")
    etf, ids = codes[0], {name: uuid4().hex for name in ('t1', 't2', 't3')}
    at = {'t1': '2026-09-21T10:00:00+09:00', 't2': '2026-09-21T10:10:00+09:00', 't3': '2026-09-21T10:20:00+09:00'}
    for name in ('t2', 't1', 't3'):  # t1 이 t2 뒤에 실행된다
        r = launch([{'kind': 'movement', 'etf': etf, 'id': ids[name], 'at': at[name]}], mode='a', llm=args.llm, tools=args.tools)
        assert r['ok'] == 1, r
    name_of = {v: k for k, v in ids.items()}
    with admin() as c:
        rows = {name: c.execute('SELECT previous_analysis_id, published_at IS NOT NULL published FROM movement_analyses '
                                'WHERE analysis_id=%s', (i,)).fetchone() for name, i in ids.items()}
        delivered = {name: c.execute("SELECT count(*) n FROM tenant_delivery WHERE movement_analysis_id=%s AND delivery_type='NEW'",
                                     (i,)).fetchone()['n'] for name, i in ids.items()}
        chain = [r['analysis_id'] for r in c.execute('''WITH RECURSIVE h AS (SELECT analysis_id, previous_analysis_id
            FROM movement_analyses WHERE analysis_id=%s UNION ALL SELECT a.analysis_id, a.previous_analysis_id
            FROM movement_analyses a JOIN h ON a.analysis_id=h.previous_analysis_id) SELECT analysis_id FROM h''', (ids['t3'],))]
    latest = PublicationReader(lambda: psycopg.connect(DSN['writer'], autocommit=True)).latest(etf, 'movement')['analysis_id']
    result = {'run_order': ['t2', 't1', 't3'],
              'previous': {n: name_of.get(r['previous_analysis_id']) for n, r in rows.items()},
              'published': {n: r['published'] for n, r in rows.items()}, 'delivered_new': delivered,
              't3_chain': [name_of[i] for i in chain], 'latest': name_of[latest]}
    record('review-order-inverted-movement', {'mode': 'a', 'llm_s': args.llm, 'tools': args.tools}, result)
    assert result['previous'] == {'t1': None, 't2': None, 't3': 't2'}, result
    assert result['delivered_new'] == {'t1': 0, 't2': 1, 't3': 1} and result['latest'] == 't3', result
    assert 't1' not in result['t3_chain'], result
    print('order: t1 은 완료·게시되지만 전달되지 않고(t2 가 이미 전달됨), 뒤 사건의 이전 설명 사슬에도 들어가지 않는다')


def control_exp(args):
    """실행 제어 함수(Lambda 본문)를 동시에 여러 번 불러도 상한과 '종류·ETF 하나씩'을 지키는가."""
    from edge_analysis_v2.cloud.control import control
    codes, _ = etf_codes()
    limits(-1, -1)
    calls, lock = {'describe': 0}, threading.Lock()

    def workflows():
        client = Mock()
        def describe(executionArn):
            with lock:
                calls['describe'] += 1
            return {'status': 'RUNNING'}
        client.describe_execution.side_effect = describe
        client.get_paginator.return_value.paginate.return_value = [{'events': []}]
        return client

    def round_(slots, keys):
        with admin() as c:
            c.execute('TRUNCATE analysis_execution_slots')
        barrier = threading.Barrier(len(keys))
        def acquire(index):
            with psycopg.connect(DSN['writer'], autocommit=True) as connection:
                barrier.wait()
                return control(connection, workflows(), Mock(), 'cluster', f'arn:{index}', 'acquire',
                               slots=slots, request_key=keys[index])['acquired']
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:
            acquired = list(pool.map(acquire, range(len(keys))))
        with admin() as c:
            rows = c.execute('SELECT request_key FROM analysis_execution_slots').fetchall()
        return sum(acquired), len(rows), len({r['request_key'] for r in rows})

    results = {}
    cases = (('37-distinct-slots3', 3, [f'outlook:{c}' for c in codes], 3),
             ('10-same-key-slots3', 3, ['movement:091160'] * 10, 1),
             ('37-five-keys-slots3', 3, [f'movement:{codes[i % 5]}' for i in range(37)], 3),
             ('37-distinct-slots1', 1, [f'outlook:{c}' for c in codes], 1))
    for name, slots, keys, expect in cases:
        calls['describe'] = 0
        rounds = [round_(slots, keys) for _ in range(args.rounds)]
        results[name] = {'rounds': args.rounds, 'all_ok': all(r == (expect,)*3 for r in rounds), 'first': rounds[0],
                         'describe_calls_per_round': calls['describe'] / args.rounds}
        assert results[name]['all_ok'], (name, rounds)
    # 늦게 돌아오는 옛 워커: 실행은 끝났는데(ABORTED) 태스크가 아직 RUNNING 이면 같은 종류·ETF 의 새 시도는 자리를
    # 못 받고, STOPPED 가 확인된 뒤에야 회수한다. integration_tests/test_execution_control.py 와 같은 단언이다.
    with admin() as c:
        c.execute('TRUNCATE analysis_execution_slots')
    key = 'outlook:' + codes[0]
    with psycopg.connect(DSN['writer'], autocommit=True) as connection:
        flow, ecs = workflows(), Mock()
        assert control(connection, flow, ecs, 'cluster', 'old', 'acquire', slots=3, request_key=key)['acquired']
        flow.describe_execution.side_effect = None
        flow.describe_execution.return_value = {'status': 'ABORTED'}
        flow.get_paginator.return_value.paginate.return_value = [{'events': [{'taskSubmittedEventDetails': {
            'resourceType': 'ecs', 'resource': 'runTask.sync', 'output': json.dumps({'Tasks': [{'TaskArn': 'task'}]})}}]}]
        ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'RUNNING'}]}
        blocked = not control(connection, flow, ecs, 'cluster', 'new', 'acquire', slots=3, request_key=key)['acquired']
        ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'STOPPED'}]}
        reclaimed = control(connection, flow, ecs, 'cluster', 'new', 'acquire', slots=3, request_key=key)['acquired']
    results['zombie-task-blocks-reclaim'] = {'blocked_while_running': blocked, 'stop_task_calls': ecs.stop_task.call_count,
                                            'reclaimed_after_stopped': reclaimed}
    assert (blocked, ecs.stop_task.call_count, reclaimed) == (True, 1, True), results

    # 아래는 AWS 응답을 대역으로 준 제어 로직 확인이다. 응답 형식은 2026-10-03 dev 읽기 조회로 맞췄다
    # (13시간 지난 STOPPED 태스크의 DescribeTasks → tasks [], failures [{reason: MISSING}]).
    def stale(status, events, describe=None, history_error=None):
        """끝난 실행 'old'(다른 ETF)의 자리가 남은 상태에서 새 실행이 다른 ETF 의 자리를 청한다."""
        with admin() as c:
            c.execute('TRUNCATE analysis_execution_slots')
            c.execute("INSERT INTO analysis_execution_slots(execution_arn, started_by, request_key) VALUES ('old', 'x', %s)",
                      ('outlook:' + codes[1],))
        flow, ecs = Mock(), Mock()
        flow.describe_execution.return_value = {'status': status}
        if history_error:
            flow.get_paginator.return_value.paginate.side_effect = history_error
        else:
            flow.get_paginator.return_value.paginate.return_value = [{'events': events}]
        ecs.describe_tasks.return_value = describe or {'tasks': [], 'failures': []}
        with psycopg.connect(DSN['writer'], autocommit=True) as connection:
            try:
                outcome = 'acquired' if control(connection, flow, ecs, 'cluster', 'new', 'acquire', slots=3,
                                                request_key='outlook:' + codes[2])['acquired'] else 'waiting'
            except Exception as exc:
                outcome = 'raised ' + type(exc).__name__ + ': ' + str(exc)
        with admin() as c:
            left = [r['execution_arn'] for r in c.execute('SELECT execution_arn FROM analysis_execution_slots ORDER BY 1')]
        return {'acquire': outcome, 'slots_left': left, 'describe_tasks_called': ecs.describe_tasks.called}

    submitted = [{'taskSubmittedEventDetails': {'resourceType': 'ecs', 'resource': 'runTask.sync',
                                                 'output': json.dumps({'Tasks': [{'TaskArn': 'task'}]})}}]
    # 1) 반납 없이 끝난 실행(ABORTED·TIMED_OUT, 또는 반납 재시도 소진)의 자리가 1시간 넘게 남아 태스크가 ECS 에서 사라짐
    results['stale-slot-task-missing'] = stale('ABORTED', submitted, describe={
        'tasks': [], 'failures': [{'arn': 'task', 'reason': 'MISSING'}]})
    # 2) 태스크 제출 기록이 없는 끝난 실행(태스크를 띄우지 않았거나, RunTask 도중 중단돼 기록이 안 남음) → ECS 를 묻지 않고 회수
    results['stale-slot-no-task-recorded'] = stale('ABORTED', [])
    # 3) 실행 이력 조회 실패 → 끝난 다른 실행의 자리를 판정 못 하면 새 자리 확보 전체가 실패
    results['stale-slot-history-error'] = stale('ABORTED', [], history_error=RuntimeError('ThrottlingException'))
    assert results['stale-slot-task-missing']['acquire'].startswith('raised RuntimeError'), results
    assert results['stale-slot-no-task-recorded'] == {'acquire': 'acquired', 'slots_left': ['new'],
                                                      'describe_tasks_called': False}, results
    assert results['stale-slot-history-error']['acquire'].startswith('raised'), results

    # 4) 회수와 새 자리 확보가 동시에: 자리 3 중 하나가 끝난(STOPPED) 실행의 것이고, 10곳이 동시에 다른 ETF 자리를 청한다
    def race():
        with admin() as c:
            c.execute('TRUNCATE analysis_execution_slots')
            for i in range(3):
                c.execute('INSERT INTO analysis_execution_slots(execution_arn, started_by, request_key) VALUES (%s, %s, %s)',
                          (f'live:{i}' if i else 'done', f's{i}', f'outlook:{codes[i]}'))
        barrier = threading.Barrier(10)
        def acquire(index):
            flow, ecs = Mock(), Mock()
            flow.describe_execution.side_effect = lambda executionArn: {'status': 'SUCCEEDED' if executionArn == 'done' else 'RUNNING'}
            flow.get_paginator.return_value.paginate.return_value = [{'events': submitted}]
            ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'STOPPED'}]}
            with psycopg.connect(DSN['writer'], autocommit=True) as connection:
                barrier.wait()
                return control(connection, flow, ecs, 'cluster', f'new:{index}', 'acquire', slots=3,
                               request_key=f'outlook:{codes[10 + index]}')['acquired']
        with ThreadPoolExecutor(max_workers=10) as pool:
            acquired = sum(pool.map(acquire, range(10)))
        with admin() as c:
            left = sorted(r['execution_arn'] for r in c.execute('SELECT execution_arn FROM analysis_execution_slots'))
        return acquired, left
    races = [race() for _ in range(args.rounds)]
    results['reclaim-races-acquire'] = {'rounds': args.rounds, 'acquired_per_round': sorted({a for a, _ in races}),
                                        'done_slot_left': any('done' in left for _, left in races),
                                        'rows_per_round': sorted({len(left) for _, left in races})}
    assert results['reclaim-races-acquire'] == {'rounds': args.rounds, 'acquired_per_round': [1],
                                                'done_slot_left': False, 'rows_per_round': [3]}, results
    record('review-control-concurrent-acquire', {'threads': 'one per caller (Lambda 동시 실행 N 흉내)', 'rounds': args.rounds},
           results)
    print('control: all assertions passed')


def sites(args):
    """b 에서 분석 1건이 writer 연결을 어느 함수에서 몇 번 여는가(툴 호출 수에 따라 변한다)."""
    codes, _ = etf_codes()
    limits(20, 6)
    out = {}
    for kind, tools in (('outlook', args.tools), ('movement', 12)):
        reset(definitions=True)
        r = launch([{'kind': kind, 'etf': codes[0], 'id': uuid4().hex}], mode='b', llm=2, tools=tools)
        assert r['ok'] == 1, r
        out[kind] = {'tools': tools, 'writer_connects': r['roles']['writer']['connects'], 'by_site': r['runs'][0]['writer_sites']}
    record('review-connection-sites-b', {'mode': 'b'}, out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('one')
    p.add_argument('--mode', choices=('a', 'b'), required=True)
    p.add_argument('--kind', required=True); p.add_argument('--etf', required=True); p.add_argument('--id', required=True)
    p.add_argument('--llm', type=float, default=8); p.add_argument('--tools', type=int, default=40)
    p.add_argument('--src', type=float, default=0.3); p.add_argument('--retry', type=float, default=0.0)
    p.add_argument('--at', default=''); p.add_argument('--save-retry', type=float, default=0.0)
    p.add_argument('--inject', default='none', choices=('none', 'save-kill', 'save-ack-lost'))
    p = commands.add_parser('occupancy')
    p.add_argument('--modes', default='a,b'); p.add_argument('--n', type=int, default=37)
    p.add_argument('--kind', default='outlook'); p.add_argument('--llm', type=float, default=30)
    p.add_argument('--tools', type=int, default=40); p.add_argument('--src', type=float, default=0.3)
    p.add_argument('--retry', type=float, default=0.0)
    p.add_argument('--writer-limit', type=int, default=-1); p.add_argument('--reader-limit', type=int, default=-1)
    p = commands.add_parser('faults')
    p.add_argument('--n', type=int, default=3); p.add_argument('--kind', default='outlook')
    p.add_argument('--llm', type=float, default=20); p.add_argument('--tools', type=int, default=40)
    p.add_argument('--deny', type=float, default=5); p.add_argument('--retry', type=float, default=15)
    p = commands.add_parser('dup')
    p.add_argument('--llm', type=float, default=6); p.add_argument('--tools', type=int, default=10)
    p = commands.add_parser('control')
    p.add_argument('--rounds', type=int, default=20)
    commands.add_parser('retry-safety')
    p = commands.add_parser('save-faults')
    p.add_argument('--llm', type=float, default=4); p.add_argument('--tools', type=int, default=10)
    p.add_argument('--save-retry', type=float, default=10)
    p = commands.add_parser('order')
    p.add_argument('--llm', type=float, default=2); p.add_argument('--tools', type=int, default=4)
    p = commands.add_parser('sites')
    p.add_argument('--tools', type=int, default=40)
    args = parser.parse_args()
    {'one': one, 'occupancy': occupancy, 'faults': faults, 'dup': dup, 'control': control_exp,
     'retry-safety': retry_safety, 'save-faults': save_faults, 'order': order, 'sites': sites}[args.command](args)
