"""같은 DB 의 다른 서비스 DB 작업을 흉내 내는 배경 부하(혼합 부하 실험용). 요청 빈도·동시 수는 합성 값이다.

서비스마다 요청 1회 = 새 연결 + 실제 코드의 대표 SQL. 모두 PostgreSQL 에 직접 붙는다(풀은 분석 워커 앞에만 있다).
- api: v2 API 의 실제 읽기 `PublicationReader.latest`(api_reader 역할, `api/runtime.py` 와 같은 세션 옵션)
- control: 실행 제어 `control.py` 의 자리 판정 트랜잭션 중 읽기 부분(writer 역할, 전역 advisory xact lock + 자리 표 조회).
  자리 INSERT·DELETE 와 SFN·ECS 조회는 하지 않는다 — 배치의 자리 행을 건드리지 않기 위해서다. Lambda·SFN 은 검증하지 않는다.
- collect: 수집 `minute/commit.py` 의 `ON CONFLICT DO NOTHING` + 되읽기 SQL 모양을 같은 열의 로컬 표(`bg_collect_commit`)에서,
  수집 쪽 기본 상한(`call_budget.py` statement_timeout·lock_timeout 500 ms)으로.

    python background.py --host postgres --duration 560 --out <jsonl>
"""
import argparse
import json
from pathlib import Path
import random
import sys
import threading
import time

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]/'src/apps/cloud/analysis-engine-v2/src'))
from edge_analysis_v2.api.publications import PublicationReader  # noqa: E402

PASSWORD = 'local_only'
# 서비스: (역할, 동시 루프 수, 요청 사이 쉼(초), 세션 옵션). 동시 수는 dev 예약 동시 실행(API·실행 제어 각 3)을 따랐다.
SERVICES = {
    'api': ('edge_analysis_v2_api_reader', 3, 0.1,
            '-c default_transaction_read_only=on -c statement_timeout=5000 -c idle_in_transaction_session_timeout=10000'),
    'control': ('edge_analysis_v2_writer', 3, 1.0, '-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000'),
    'collect': ('bg_collector', 4, 0.2, '-c statement_timeout=500 -c lock_timeout=500'),
}


def setup(host):
    with psycopg.connect(host=host, port=5432, dbname='analysis_v2', user='v2_local', password=PASSWORD, autocommit=True) as c:
        c.execute(f"ALTER ROLE edge_analysis_v2_api_reader LOGIN PASSWORD '{PASSWORD}'")
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='bg_collector'").fetchone():
            c.execute(f"CREATE ROLE bg_collector LOGIN PASSWORD '{PASSWORD}'")
        c.execute('''CREATE TABLE IF NOT EXISTS bg_collect_commit (session_id text, window_start timestamptz, generation int,
            artifact_uri text, artifact_checksum text, manifest_uri text, manifest_checksum text,
            PRIMARY KEY (session_id, window_start, generation))''')
        c.execute('GRANT SELECT, INSERT ON bg_collect_commit TO bg_collector')
        c.execute('TRUNCATE bg_collect_commit')


def connect(host, service):
    role, _, _, options = SERVICES[service]
    return psycopg.connect(host=host, port=5432, dbname='analysis_v2', user=role, password=PASSWORD,
                           connect_timeout=10, autocommit=True, options=options)


def request(host, service, rng):
    if service == 'api':  # 연결은 PublicationReader 가 연다(실제 API 와 같은 팩토리 방식)
        return PublicationReader(lambda: connect(host, service)).latest(f'9{rng.randrange(900):05d}', 'outlook')
    with connect(host, service) as c:
        if service == 'control':
            with c.transaction():
                c.execute("SELECT pg_advisory_xact_lock(hashtext('analysis-v2-capacity')::bigint)")
                return c.execute('SELECT execution_arn,request_key FROM analysis_execution_slots').fetchall()
        with c.transaction():
            key = (f's{rng.randrange(50)}', f'2026-10-05T{rng.randrange(9, 16):02d}:{rng.randrange(60):02d}:00+09', 1)
            row = c.execute('''INSERT INTO bg_collect_commit VALUES (%s,%s,%s,'u','c','m','mc')
                ON CONFLICT (session_id, window_start, generation) DO NOTHING RETURNING artifact_uri''', key).fetchone()
            return row or c.execute('SELECT artifact_uri FROM bg_collect_commit WHERE session_id=%s AND window_start=%s '
                                    'AND generation=%s', key).fetchone()


def outcome(exc):
    text = str(exc)
    if 'too many connections' in text or 'too many clients' in text:
        return 'connect_refused'
    if getattr(exc, 'sqlstate', None) in ('57014', '55P03') or 'timeout expired' in text:
        return 'timeout'
    return 'error:' + type(exc).__name__


def loop(host, service, index, until, sink, lock):
    rng, pause = random.Random(f'{service}{index}'), SERVICES[service][2]
    while time.time() < until:
        started = time.time()
        try:
            request(host, service, rng)
            result = 'ok'
        except Exception as exc:
            result = outcome(exc)
        with lock:
            sink.write(json.dumps([service, round(started, 3), round(time.time() - started, 4), result]) + '\n')
        time.sleep(pause)


def summarize(path, start, length):
    """시작 표식(`bg-<이름>.marker` 첫 줄) + 10초부터 length 초 동안 시작한 요청만 서비스별로 센다."""
    rows = [r for r in map(json.loads, open(path)) if start + 10 <= r[1] < start + 10 + length]
    out = {}
    for service in SERVICES:
        mine = [r for r in rows if r[0] == service]
        ok = sorted(r[2] for r in mine if r[3] == 'ok')
        pick = lambda q: round(ok[min(len(ok) - 1, int(len(ok) * q))] * 1000, 1) if ok else None
        out[service] = {'requests': len(mine), 'per_s': round(len(mine) / length, 1), 'ok': len(ok),
                        'ms_p50_p95_p99_max': [pick(0.5), pick(0.95), pick(0.99), round(ok[-1] * 1000, 1) if ok else None],
                        'failed': {k: sum(r[3] == k for r in mine) for k in {r[3] for r in mine} - {'ok'}}}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default='postgres'); p.add_argument('--duration', type=float, default=0)
    p.add_argument('--out', required=True); p.add_argument('--summarize', type=float, default=0,
                                                           help='관측 창 길이(초). 주면 부하 대신 --out 을 집계한다')
    args = p.parse_args()
    if args.summarize:
        marker = Path(args.out).with_suffix('.marker')
        print(json.dumps(summarize(args.out, float(marker.read_text().split()[0]), args.summarize)))
        return
    setup(args.host)
    until, lock = time.time() + args.duration, threading.Lock()
    with open(args.out, 'w') as sink:
        threads = [threading.Thread(target=loop, args=(args.host, s, i, until, sink, lock))
                   for s, (_, n, _, _) in SERVICES.items() for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


if __name__ == '__main__':
    main()
