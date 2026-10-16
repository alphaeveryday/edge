"""Versioned, private evaluation records; customer jobs and quotas stay separate."""
import argparse
import asyncio
import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from . import store

CASES = Path(__file__).parent / 'data' / 'verification_cases.json'


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_verification_runs (
        id text PRIMARY KEY, version text NOT NULL, environment text NOT NULL,
        cases jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_verification_attempts (
        run_id text REFERENCES portfolio_verification_runs(id), case_id integer NOT NULL,
        attempt integer NOT NULL, execution_key text NOT NULL, state text NOT NULL,
        input jsonb NOT NULL, result jsonb, failures jsonb NOT NULL DEFAULT '[]', seconds double precision,
        created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
        PRIMARY KEY(run_id,case_id,attempt), UNIQUE(run_id,case_id,execution_key))''')


def register(run_id, version, environment, cases):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,160}', run_id) or not version or len(version) > 200:
        raise ValueError('INVALID_VERIFICATION_VERSION')
    if environment not in ('production_runtime', 'isolated_runtime'):
        raise ValueError('INVALID_VERIFICATION_ENVIRONMENT')
    if len(cases) != 20 or {c['case'] for c in cases} != set(range(1, 21)):
        raise ValueError('EXACTLY_20_CASES_REQUIRED')
    from .contracts import AnalysisInput
    for case in cases:
        AnalysisInput.model_validate(case['input'])
        if not case.get('title') or not case.get('expected'):
            raise ValueError('CASE_DESCRIPTION_REQUIRED')
    with store.connect() as db:
        db.execute('INSERT INTO portfolio_verification_runs(id,version,environment,cases) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                   (run_id, version, environment, Jsonb(cases)))
        row = db.execute('SELECT version,environment,cases FROM portfolio_verification_runs WHERE id=%s', (run_id,)).fetchone()
        if row != {'version': version, 'environment': environment, 'cases': cases}:
            raise ValueError('VERIFICATION_VERSION_CONFLICT')


def begin_attempt(run_id, case_id, execution_key, input_snapshot=None):
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (run_id,))
        run = db.execute('SELECT cases FROM portfolio_verification_runs WHERE id=%s', (run_id,)).fetchone()
        if not run or case_id not in [c['case'] for c in run['cases']]:
            raise ValueError('UNKNOWN_CASE')
        submitted = input_snapshot if input_snapshot is not None else next(c['input'] for c in run['cases'] if c['case'] == case_id)
        previous = db.execute('SELECT * FROM portfolio_verification_attempts WHERE run_id=%s AND case_id=%s AND execution_key=%s', (run_id, case_id, execution_key)).fetchone()
        if previous:
            if previous['input'] != submitted:
                raise ValueError('ATTEMPT_INPUT_CONFLICT')
            return previous
        return db.execute('''INSERT INTO portfolio_verification_attempts(run_id,case_id,attempt,execution_key,state,input)
            SELECT %s,%s,COALESCE(max(attempt),0)+1,%s,'running',%s FROM portfolio_verification_attempts
            WHERE run_id=%s AND case_id=%s RETURNING *''', (run_id, case_id, execution_key, Jsonb(submitted), run_id, case_id)).fetchone()


def finish_attempt(run_id, case_id, attempt, result, failures, seconds, state='succeeded'):
    if state not in ('succeeded', 'failed') or seconds < 0:
        raise ValueError('INVALID_VERIFICATION_RESULT')
    with store.connect() as db:
        return db.execute('''UPDATE portfolio_verification_attempts SET state=%s,result=%s,failures=%s,seconds=%s,finished_at=now()
            WHERE run_id=%s AND case_id=%s AND attempt=%s AND state='running' RETURNING attempt''',
            (state, Jsonb(result), Jsonb(failures), seconds, run_id, case_id, attempt)).fetchone() is not None


def runs():
    with store.connect() as db:
        return db.execute("SELECT id,version,environment,created_at FROM portfolio_verification_runs ORDER BY (id LIKE 'deploy-%') DESC,created_at DESC,id DESC").fetchall()


def detail(run_id):
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        run = db.execute('SELECT * FROM portfolio_verification_runs WHERE id=%s', (run_id,)).fetchone()
        if run:
            run['attempts'] = db.execute('''SELECT case_id,attempt,state,failures,seconds,created_at,finished_at,
                state='running' AND created_at<now()-interval '12 minutes' AS interrupted
                FROM portfolio_verification_attempts WHERE run_id=%s ORDER BY case_id,attempt''', (run_id,)).fetchall()
        return run


def attempt_detail(run_id, case_id, attempt):
    with store.connect() as db:
        return db.execute("SELECT *,state='running' AND created_at<now()-interval '12 minutes' AS interrupted FROM portfolio_verification_attempts WHERE run_id=%s AND case_id=%s AND attempt=%s", (run_id, case_id, attempt)).fetchone()


def check_report(case, report):
    """Check explicit machine-verifiable expectations; prose still needs human review."""
    checks = case.get('checks', {})
    recommendation = report.get('recommendation') or {}
    allocations = recommendation.get('allocations') or []
    failures = []
    if checks.get('scenario_only'):
        scenario = report.get('scenarios') or {}
        rows = scenario.get('rows') or []
        if not any(Decimal(str(r.get('annual_return_pct', 0))) == 5 and r.get('years') == 10 and Decimal(str(r.get('future_krw', 0))) == 93470527 for r in rows):
            failures.append('요청한 10년·연 5% 계산 결과 불일치')
        if allocations:
            failures.append('계산만 요청했으나 ETF 교체 추천')
    elif checks.get('expect_conflict'):
        if allocations:
            failures.append('동시에 충족할 수 없는 계좌·종목 조건으로 추천')
    elif not allocations:
        failures.append('추천 비중 없음')
    if allocations and sum(Decimal(str(a['weight_pct'])) for a in allocations) != 100:
        failures.append('추천 비중 합계가 100% 아님')
    for a in allocations:
        if a['instrument_id'] in checks.get('excluded_ids', []):
            failures.append('제외 종목 추천')
        if checks.get('allowed_accounts') and a['account'] not in checks['allowed_accounts']:
            failures.append('계좌 제약 위반')
        if checks.get('allowed_markets') and a['instrument_id'].split(':')[0] not in checks['allowed_markets']:
            failures.append('상장시장 제약 위반')
        if checks.get('no_invented_money') and a.get('amount_krw') is not None:
            failures.append('미입력 투자금액 임의 생성')
    for key, minimum in checks.get('minimum_weights', {}).items():
        if sum(Decimal(str(a['weight_pct'])) for a in allocations if a['instrument_id'] == key) < minimum:
            failures.append('최소 비중 미충족')
    if checks.get('no_sell') and any(Decimal(str(a.get('sell_krw') or 0)) > 0 for a in (recommendation.get('execution') or {}).get('rows', [])):
        failures.append('매도 금지 위반')
    if not (report.get('sections') or {}).get('opinion', {}).get('paragraphs'):
        failures.append('결론 본문 없음')
    return list(dict.fromkeys(failures))


async def run(version):
    from .agent import run_analysis
    from .contracts import AnalysisInput
    from .worker import failure_codes
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    run_id = 'deploy-' + version
    register(run_id, version, 'production_runtime', cases)
    # A pinned one-off container survives later deployments; one runner owns each version.
    with store.connect() as lock:
        lock.autocommit = True
        if not lock.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,1)) AS acquired', (run_id,)).fetchone()['acquired']:
            return
        for case in cases:
            with store.connect() as db:
                previous = db.execute('SELECT * FROM portfolio_verification_attempts WHERE run_id=%s AND case_id=%s ORDER BY attempt DESC LIMIT 1', (run_id, case['case'])).fetchone()
            if previous and previous['state'] == 'succeeded' and not previous['failures']:
                continue
            if previous and previous['state'] == 'running':
                finish_attempt(run_id, case['case'], previous['attempt'], None, ['RUNNER_INTERRUPTED'], 0, 'failed')
            entry = begin_attempt(run_id, case['case'], str(time.time_ns()))
            started = time.monotonic()
            try:
                async with asyncio.timeout(600):
                    report = await run_analysis(AnalysisInput.model_validate(case['input']), os.environ['DEEPSEEK_API_KEY'], lambda stage: None)
                finish_attempt(run_id, case['case'], entry['attempt'], report, check_report(case, report), time.monotonic()-started)
            except Exception as error:
                finish_attempt(run_id, case['case'], entry['attempt'], None, failure_codes(error), time.monotonic()-started, 'failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--register-only', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.version):
        parser.error('version must be the deployed Git commit')
    deployment = CASES.with_name('deployment.json')
    if not deployment.exists() or json.loads(deployment.read_text())['version'] != args.version:
        parser.error('version does not match this deployed image')
    store.initialize()
    if args.register_only:
        register('deploy-' + args.version, args.version, 'production_runtime', json.loads(CASES.read_text(encoding='utf-8')))
    else:
        asyncio.run(run(args.version))


if __name__ == '__main__':
    main()
