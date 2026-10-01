"""v2 실행 경로 용량·중복·재시도 탐침 (로컬 격리 전용).

실제로 도는 코드: edge_analysis_v2.cloud.worker.run → analysis.service.execute_request →
PublicationStore / ToolStore / AuditedExecution / BodyEditor, 그리고 실제 PostgreSQL 16 + 저장소 마이그레이션 77개
(역할 CONNECTION LIMIT 포함).
대역(실제가 아닌 것): LLM(model_call — 지연·오류만 흉내), 원천 조회(reader 역할로 접속해 pg_sleep 후 합성 fixture 반환),
S3(로컬 디렉터리, manifest 의 IfNoneMatch 선점만 재현), Secrets Manager, ECS/SFN(분석 1건 = OS 프로세스 1개).
따라서 이 결과는 LLM 처리량·실제 건당 시간·RDS 메모리를 말하지 않는다.

  probe.py setup                       # 로컬 역할 로그인·제한을 dev 실측값(writer 5, reader 3)으로 맞춘다
  probe.py sweep --kind outlook --c 4 --llm 8 [--writer-limit N]   # 동시 C건
  probe.py scenarios                   # 중복·재시도·중단 시나리오와 단언
  probe.py one ...                     # 내부용(분석 1건 = 프로세스 1개)
"""
import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
from functools import partial
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get('EDGE_REPO', HERE.parents[3]))  # tests/loadtest/analysis-v2/local → 저장소 루트
sys.path.insert(0, str(REPO/'src/apps/cloud/analysis-engine-v2/src'))
HOST = 'host=127.0.0.1 port=55461 dbname=analysis_v2 connect_timeout=10'
DSN = {'admin': HOST+' user=v2_local password=local_only',
       'writer': HOST+' user=edge_analysis_v2_writer password=local_only',
       'reader': HOST+' user=edge_analysis_v2_reader password=local_only'}
WORK = HERE/'work'
RESULTS = HERE/'results.jsonl'
ANALYSIS_AT = '2026-09-21T10:00:00+09:00'
TABLES = ('tool_runs', 'movement_items', 'outlook_items', 'outlook_factors', 'outlook_conclusion_keywords',
          'outlook_factor_metrics', 'outlook_issue_items', 'movement_analyses', 'outlook_analyses')


def etf_codes():
    """실제 설정의 국내 ETF 목록(스냅샷 해시와 함께)."""
    path = REPO/'src/apps/cloud/data-pipeline/src/data_pipeline/config/sources.toml'
    codes = list(tomllib.loads(path.read_text(encoding='utf-8'))['krx_etf']['source']['etf_map'])
    return codes, hashlib.sha256(','.join(codes).encode()).hexdigest()


def admin():
    return psycopg.connect(DSN['admin'], autocommit=True, row_factory=dict_row)


def setup(writer_limit=5, reader_limit=3):
    with admin() as c:
        c.execute("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='edge_analysis_v2_reader') "
                  "THEN CREATE ROLE edge_analysis_v2_reader; END IF; END $$")
        c.execute(f"ALTER ROLE edge_analysis_v2_writer LOGIN PASSWORD 'local_only' CONNECTION LIMIT {int(writer_limit)}")
        c.execute(f"ALTER ROLE edge_analysis_v2_reader LOGIN PASSWORD 'local_only' CONNECTION LIMIT {int(reader_limit)}")
        # outlook_factor_metrics·outlook_issue_items 권한은 V202609282200 이 준다. 추가 부여 없음.
        return c.execute("SELECT rolname, rolconnlimit FROM pg_roles WHERE rolname LIKE 'edge_analysis_v2%' ORDER BY 1").fetchall()


def reset():
    with admin() as c:
        c.execute('TRUNCATE ' + ','.join(TABLES) + ' CASCADE')
    if WORK.exists():
        subprocess.run(['rm', '-rf', str(WORK)], check=True)


# ── 분석 1건 (= ECS 태스크 1개에 대응하는 프로세스) ──────────────────────────────────────
class LocalS3:
    """manifest 선점(IfNoneMatch='*')만 실제 S3 처럼 거절한다."""
    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, **_):
        from botocore.exceptions import ClientError
        path = WORK/'s3'/Key
        path.parent.mkdir(parents=True, exist_ok=True)
        if IfNoneMatch == '*':
            try:
                with path.open('xb') as stream:
                    stream.write(Body)
            except FileExistsError:
                raise ClientError({'Error': {'Code': 'PreconditionFailed', 'Message': 'exists'}}, 'PutObject') from None
        else:
            path.write_bytes(Body)
        return {}


class LocalSession:
    def client(self, name):
        assert name == 's3'
        return LocalS3()


def retarget(value, code):
    if isinstance(value, dict):
        return {k: retarget(v, code) for k, v in value.items()}
    if isinstance(value, list):
        return [retarget(v, code) for v in value]
    return code if value == '091160' else value


def one(args):
    from edge_analysis_v2.analysis import service
    from edge_analysis_v2.cloud import worker
    from edge_analysis_v2.storage.database import ResultDatabaseError
    from edge_analysis_v2.tools.fixture_data import make_fixture

    connections, model_calls, began = [], [], time.time()

    def connect(role, **options):
        event = {'role': role, 'open_start': time.time()}
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

    def connect_results(ca_path, *, session=None, cloud=False):  # 실제 connect_results 와 같은 세션 옵션
        return connect('writer', autocommit=True,
                       options='-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000')

    def connect_sources(ca_path, *, session=None, cloud=False):
        connection = connect('reader', row_factory=dict_row,
                             options='-c default_transaction_read_only=on -c statement_timeout=15000')
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        return connection

    def load_source(connection, ticker, analysis_at):  # 대역: 원천 읽기 시간만 흉내
        connection.execute('SELECT pg_sleep(%s)', (args.src,))
        return retarget(make_fixture(analysis_at=analysis_at), ticker)

    async def model(**kwargs):  # 대역: LLM 대기와 툴 호출 순서만 흉내
        model_calls.append(time.time())
        await asyncio.sleep(args.llm/2)
        if args.fail == 'model':
            raise ValueError('injected model failure')
        reference = kwargs['call']('get_issue_evidence', {'news_ids': [kwargs['initial']['news'][0]['news_id']],
                                                           'include_body': False})['tool_run_id']
        if args.kind == 'outlook':
            kwargs['call']('write_outlook_body', {'title': '계약 이행을 확인해요', 'items': [
                dict(id='contract', title_keyword='계약', sentences=['판매 물량을 확보했어요.'], tool_run_ids=[reference])]})
        await asyncio.sleep(args.llm/2)
        if args.fail == 'timeout':
            raise TimeoutError()
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

    worker.connect_results, worker.connect_sources = connect_results, connect_sources
    worker.load_source, worker.load_flow, worker.load_prices = load_source, (lambda c, d: d), (lambda c, d: d)
    worker.execute_request = partial(service.execute_request, model_call=model)
    request = {'analysis_id': args.id, 'kind': args.kind, 'etf_code': args.etf, 'analysis_at': ANALYSIS_AT}
    error, stage = None, 'run'
    try:
        worker.run(request, bucket='local', ca_path=HERE, folder=WORK/'artifacts'/args.id,
                   key='fake-key', model='stub', session=LocalSession())
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)[:160]
    print(json.dumps({'id': args.id, 'kind': args.kind, 'etf': args.etf, 'exit': 1 if error else 0, 'error': error,
                      'model_calls': len(model_calls), 'began': began, 'ended': time.time(),
                      'connections': connections}, ensure_ascii=False), flush=True)
    sys.exit(1 if error else 0)


# ── 여러 건 동시 실행과 관측 ────────────────────────────────────────────────────────────
def launch(specs, *, llm, src=0.3, kill_after=None):
    """specs: [{kind, etf, id, fail?}] 를 동시에 시작하고 DB 세션을 표본 측정한다."""
    stop, samples, memory = threading.Event(), [], []

    def sample():
        with admin() as c:
            while not stop.wait(0.2):
                rows = c.execute("SELECT usename, count(*) n FROM pg_stat_activity WHERE backend_type='client backend' "
                                 "AND usename LIKE 'edge_analysis_v2%' GROUP BY 1").fetchall()
                samples.append((time.time(), {r['usename'].rsplit('_', 1)[-1]: r['n'] for r in rows}))

    def sample_memory():
        while not stop.wait(1.5):
            out = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{.MemUsage}}', 'v2probe-postgres-1'],
                                 capture_output=True, text=True).stdout.split('/')[0].strip()
            if out:
                memory.append(out)

    threads = [threading.Thread(target=sample, daemon=True), threading.Thread(target=sample_memory, daemon=True)]
    for thread in threads:
        thread.start()
    started = time.time()
    procs = [subprocess.Popen([sys.executable, __file__, 'one', '--kind', s['kind'], '--etf', s['etf'], '--id', s['id'],
                               '--llm', str(llm), '--src', str(src), '--fail', s.get('fail', 'none')],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for s in specs]
    if kill_after is not None:
        time.sleep(kill_after)
        for proc in procs:
            proc.send_signal(signal.SIGKILL)
    outputs = []
    for spec, proc in zip(specs, procs):
        out, err = proc.communicate()
        line = out.strip().splitlines()[-1] if out.strip() else None
        outputs.append(json.loads(line) if line else spec | {'exit': proc.returncode, 'error': 'no output (killed)' if kill_after is not None else err.strip()[-300:], 'model_calls': None, 'connections': []})
    elapsed = time.time() - started
    stop.set()
    for thread in threads:
        thread.join(timeout=5)
    with admin() as c:
        state = {kind: {r['status']: r['n'] for r in c.execute(
            f'SELECT status, count(*) n FROM {kind}_analyses GROUP BY 1').fetchall()} for kind in ('outlook', 'movement')}
    peak = {role: max((s.get(role, 0) for _, s in samples), default=0) for role in ('writer', 'reader')}
    held = [e['closed']-e['opened'] for o in outputs for e in o['connections'] if e['role'] == 'writer' and 'closed' in e]
    opened = [e['opened']-e['open_start'] for o in outputs for e in o['connections'] if 'opened' in e]
    return {'elapsed_s': round(elapsed, 2), 'ok': sum(o['exit'] == 0 for o in outputs),
            'failed': sum(o['exit'] != 0 for o in outputs),
            'errors': sorted({(o['error'] or '')[:90] for o in outputs if o['exit'] != 0}),
            'connect_errors': sorted({e['error'] for o in outputs for e in o['connections'] if 'error' in e}),
            'peak_sessions': peak, 'writer_connection_seconds': round(sum(held), 1),
            'connect_ms_max': round(max(opened, default=0)*1000, 1),
            'pg_container_mem': {'first': memory[0] if memory else None, 'peak_sampled': max(memory, key=mem_bytes) if memory else None},
            'db_rows': state, 'runs': outputs}


def mem_bytes(text):
    units = {'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'B': 1}
    for unit, scale in units.items():
        if text.endswith(unit):
            return float(text[:-len(unit)])*scale
    return 0.0


def record(name, conditions, result):
    codes, digest = etf_codes()
    entry = {'name': name, 'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'conditions': conditions,
             'etf_list': {'count': len(codes), 'sha256': digest},
             'code_sha': subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
             'result': result}
    with RESULTS.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
    brief = {k: v for k, v in result.items() if k != 'runs'}
    print(name, json.dumps(conditions, ensure_ascii=False), '\n  ', json.dumps(brief, ensure_ascii=False), flush=True)
    return result


def sweep(args):
    codes, _ = etf_codes()
    limits = setup(args.writer_limit, args.reader_limit)
    reset()
    kinds = args.kind.split(',')
    specs = [{'kind': kinds[i % len(kinds)], 'etf': codes[(i // len(kinds)) % len(codes)], 'id': uuid4().hex}
             for i in range(args.c)]
    record(f'sweep-{args.kind}-c{args.c}', {'c': args.c, 'kind': args.kind, 'llm_s': args.llm, 'src_s': args.src,
           'role_limits': {r['rolname']: r['rolconnlimit'] for r in limits}}, launch(specs, llm=args.llm, src=args.src))


def scenarios(args):
    """중복·재시도·중단 계약. 각 단언은 현재 코드가 실제로 하는 일을 고정한다(바람직한 동작이 아니라)."""
    codes, _ = etf_codes()
    limits = {r['rolname']: r['rolconnlimit'] for r in setup(5, 3)}
    cond = {'llm_s': args.llm, 'role_limits': limits}
    a, b = codes[0], codes[1]

    reset()  # S1 같은 종류·같은 ETF, 서로 다른 ID 동시 → 한쪽만 성공, 진 쪽은 DB 행 없음
    ids = [uuid4().hex, uuid4().hex]
    r = record('S1-same-etf-different-id', cond, launch([{'kind': 'outlook', 'etf': a, 'id': i} for i in ids], llm=args.llm))
    assert (r['ok'], r['failed']) == (1, 1) and r['db_rows']['outlook'] == {'completed': 1}, r['db_rows']
    loser = next(o['id'] for o in r['runs'] if o['exit'])
    winner = next(o['id'] for o in r['runs'] if not o['exit'])

    # S2 진 쪽 ID 를 그대로 재실행 → 관측 manifest 선점에 걸려 모델 호출 전 실패(ID 소진)
    r = record('S2-rerun-loser-same-id', cond, launch([{'kind': 'outlook', 'etf': b, 'id': loser}], llm=args.llm))
    assert r['failed'] == 1 and r['runs'][0]['model_calls'] in (0, None), r['runs'][0]

    # S3 완료된 ID 를 워커로 재실행 → 역시 manifest 선점 실패(저장 결과 재사용은 API 계층에서만)
    r = record('S3-rerun-completed-same-id', cond, launch([{'kind': 'outlook', 'etf': a, 'id': winner}], llm=args.llm))
    assert r['failed'] == 1 and r['db_rows']['outlook'] == {'completed': 1}

    reset()  # S4 모델 실패 → failed 행. 새 ID 재시도는 성공하고 완료 1·실패 1 이 남는다
    first = uuid4().hex
    r = record('S4a-model-failure', cond, launch([{'kind': 'outlook', 'etf': a, 'id': first, 'fail': 'model'}], llm=args.llm))
    assert r['db_rows']['outlook'] == {'failed': 1}
    r = record('S4b-retry-new-id', cond, launch([{'kind': 'outlook', 'etf': a, 'id': uuid4().hex}], llm=args.llm))
    assert r['db_rows']['outlook'] == {'failed': 1, 'completed': 1}

    reset()  # S5 실행 중 강제 종료(SIGKILL) → running 행이 남고 락은 풀린다. 새 ID 재시도 성공
    r = record('S5a-killed-mid-run', cond, launch([{'kind': 'outlook', 'etf': a, 'id': uuid4().hex}], llm=args.llm, kill_after=args.llm/2+1.5))
    assert r['db_rows']['outlook'] == {'running': 1}, r['db_rows']
    r = record('S5b-retry-after-kill-new-id', cond, launch([{'kind': 'outlook', 'etf': a, 'id': uuid4().hex}], llm=args.llm))
    assert r['db_rows']['outlook'] == {'running': 1, 'completed': 1}, r['db_rows']

    reset()  # S6 같은 ETF 의 전망+가격변동 병행 → ETF 락은 종류별이라 서로 안 막지만 writer 역할 한도(5)에 걸린다
    r = record('S6-outlook-plus-movement-same-etf', cond, launch(
        [{'kind': 'outlook', 'etf': a, 'id': uuid4().hex}, {'kind': 'movement', 'etf': a, 'id': uuid4().hex}], llm=args.llm))
    assert r['ok'] == 1 and r['failed'] == 1 and any('too many connections' in e for e in r['connect_errors']), r

    setup(-1, -1)
    reset()  # S7 한도를 풀면 같은 병행이 둘 다 성공(원인이 역할 한도임을 대조로 확인)
    r = record('S7-same-as-S6-without-role-limit', cond | {'role_limits': 'unlimited'}, launch(
        [{'kind': 'outlook', 'etf': a, 'id': uuid4().hex}, {'kind': 'movement', 'etf': a, 'id': uuid4().hex}], llm=args.llm))
    assert (r['ok'], r['failed']) == (2, 0), r
    setup(5, 3)
    print('scenarios: all assertions passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('setup')
    p = commands.add_parser('one')
    p.add_argument('--kind', required=True); p.add_argument('--etf', required=True); p.add_argument('--id', required=True)
    p.add_argument('--llm', type=float, default=8); p.add_argument('--src', type=float, default=0.3)
    p.add_argument('--fail', default='none', choices=('none', 'model', 'timeout'))
    p = commands.add_parser('sweep')
    p.add_argument('--kind', default='outlook'); p.add_argument('--c', type=int, required=True)
    p.add_argument('--llm', type=float, default=8); p.add_argument('--src', type=float, default=0.3)
    p.add_argument('--writer-limit', type=int, default=5); p.add_argument('--reader-limit', type=int, default=3)
    p = commands.add_parser('scenarios')
    p.add_argument('--llm', type=float, default=6)
    args = parser.parse_args()
    if args.command == 'setup':
        print(setup())
    else:
        {'one': one, 'sweep': sweep, 'scenarios': scenarios}[args.command](args)
