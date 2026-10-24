"""지난 분석 입력을 지금 코드로 다시 통과시켜 어디서 실패하는지 센다.

운영 호스트의 1회용 worker 컨테이너에서만 돈다(.github/scripts/portfolio_diagnostics.py). DB 는 읽기만 하고
(모든 연결을 읽기 전용 거래로 연다), 출력은 집계와 job id 뿐이다 — 입력 원문·금액·종목·질문 글은 찍지 않는다.
실행: python -m edge_portfolio.past_review <days|0=전체> <llm_samples>
"""
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal

from pydantic import ValidationError

from . import store

BUSY_WAIT_SECONDS = 5
LLM_TIMEOUT_SECONDS = 300
TOTAL_SECONDS = 3000  # 호스트가 3300초에 컨테이너를 끊는다. 그 전에 모은 결과를 찍는다
RATE_LIMITS = ('PRICE_PROVIDER_RATE_LIMIT', 'EXA_RATE_LIMIT')
MAX_LLM_SAMPLES = 5
EXAMPLES_PER_CODE = 3
OUTPUT_LIMIT = 20000  # SSM 표준 출력은 약 24KB 에서 잘린다
ACCOUNTS = '일반 계좌|ISA|연금저축|IRP|연금저축\\+IRP'
# 밑줄 든 대문자 코드(계좌 이름 접미사 허용)나 예외 클래스 이름만. 밑줄 없는 대문자 낱말(종목 기호일 수 있다)은 거른다
SAFE_CODE = re.compile(rf'[A-Z][A-Z0-9]*_[A-Z0-9_]{{0,62}}(?::(?:{ACCOUNTS}))?|[A-Z][A-Za-z0-9]{{0,60}}(?:Error|Exception|Timeout|Group)')
SAFE_FIELD = re.compile(r'[a-z_]{1,40}')
SAFE_MESSAGE = re.compile(r'[A-Za-z][A-Za-z0-9 .;_]{0,80}')


def code(text):
    """코드 모양만 그대로 둔다. 고객 글이 섞일 수 있는 나머지는 OTHER 다."""
    text = str(text)
    return text if SAFE_CODE.fullmatch(text) else 'OTHER'


def error_codes(error):
    if isinstance(error, ValidationError):
        codes = []
        for issue in error.errors(include_input=False, include_context=False, include_url=False):
            path = '.'.join(part for part in issue['loc'] if isinstance(part, str) and SAFE_FIELD.fullmatch(part))
            kind = issue['type'] if SAFE_FIELD.fullmatch(issue['type']) else 'invalid'
            message = issue['msg'].removeprefix('Value error, ')
            detail = f'({message})' if kind == 'value_error' and SAFE_MESSAGE.fullmatch(message) else ''
            codes.append(f'{path or "input"}:{kind}{detail}')
        return codes or ['VALIDATION']
    if isinstance(error, ValueError):
        return [code(str(error).split(':', 1)[0])]
    return [code(type(error).__name__)]


def contract(raw):
    """(worker 가 읽는 저장 입력, 저장 입력 거부 코드, 오늘 다시 제출할 때 거부 코드)."""
    from .api import admit
    from .contracts import AnalysisInput
    try:
        persisted = AnalysisInput.model_validate(raw, context={'persisted_input': True})
        persisted_codes = []
    except ValidationError as error:
        persisted, persisted_codes = None, error_codes(error)
    try:
        admit(AnalysisInput.model_validate(raw))
        resubmit_codes = []
    except (ValidationError, ValueError) as error:
        resubmit_codes = error_codes(error)
    return persisted, persisted_codes, resubmit_codes


def text_without_chips(request):
    """8차는 칩만 조건이다. 글은 있는데 칩이 없으면 글로 적은 조건이 지금은 버려질 수 있다(모델 없이는 글 속 조건 여부를 모른다)."""
    text = request.question.strip() or request.preferences.note.strip() or request.goal_text.strip()
    return bool(text) and not request.constraints


def catalog_misses(request):
    from .catalog import analysis_catalog
    catalog = analysis_catalog(request)
    return [h.kind for h in request.holdings if h.instrument_id != 'CASH:KRW' and h.instrument_id not in catalog]


def baseline_ids(request, as_of):
    """분석이 가장 먼저 받는 지금 보유 종목(+미국 상장이면 환율). 받을 수 없는 입력이면 그 이유 코드."""
    from .market_data import MarketData
    market = MarketData(request, as_of)
    if not market.current:
        return None, 'CURRENT_WEIGHTS_REQUIRED'
    ids = set(market.current) - {'CASH:KRW'}
    if not ids:
        return None, 'CASH_ONLY'  # 받을 지금 보유 가격이 없다. 후보 가격은 분석이 고른 뒤에 받는다
    if ids - market.catalog.keys():
        return None, 'CURRENT_INSTRUMENT_UNAVAILABLE'
    if any(market.catalog[key]['market'] == 'US' for key in ids):
        ids.add('FX')
    return ids, None


def busy():
    """고객 분석이 대기·실행 중인가. 그동안은 가격 공급자와 호스트를 고객에게 양보한다."""
    with store.connect() as db:
        return store.queued_count(db) > 0


async def collect_prices(ids, as_of):
    """분석과 같은 곳(수집기가 저장한 가격)만 읽는다. 분석처럼 공급자는 부르지 않는다.
    읽기 전용이라 빠진 종목을 수집기에 요청하지는 않고, 못 쓰는 이유 코드만 모은다."""
    from .dashboard_work import saved_prices
    rejected = {}
    records = await asyncio.to_thread(saved_prices, sorted(ids), as_of, rejected)
    return records, rejected


def baseline_price(ids, records, rejected, as_of):
    from .market_data import price_failure
    from .optimizer import optimization_bounds
    if missing := sorted(ids - records.keys()):
        return rejected.get(missing[0], 'PRICE_UNAVAILABLE')
    try:
        optimization_bounds(ids - {'FX'}, records, as_of)
    except (KeyError, ValueError) as error:
        return price_failure(error)
    return 'OK'


def exact_weights(amounts):
    """(종목, 계좌)별 크기를 합계가 정확히 100인 비중(소수 8자리)으로. 모자란 끝자리는 마지막 칸에서 맞춘다."""
    total = sum(amounts.values(), Decimal(0))
    if not total:
        return []
    rows = [{'instrument_id': key[0], 'account': key[1],
             'weight_pct': (value * 100 / total).quantize(Decimal('.00000001'), rounding=ROUND_FLOOR)} for key, value in amounts.items()]
    rows[-1]['weight_pct'] = 100 - sum(row['weight_pct'] for row in rows[:-1])
    return [row for row in rows if row['weight_pct'] > 0]


def current_allocations(request):
    """지금 보유를 그대로 목표로 둔 배분."""
    amounts = defaultdict(Decimal)
    for h in request.holdings:
        amounts[(h.instrument_id, h.account)] += h.amount_krw or 0
    return exact_weights(amounts)


def past_allocations(result):
    """지난 분석이 낸 추천 배분. 저장하며 float 이 된 비중을 다시 합계 100으로 맞춘다(실행 규칙만 보려는 것이다)."""
    amounts = defaultdict(Decimal)
    for row in ((result or {}).get('recommendation') or {}).get('allocations') or []:
        if row.get('instrument_id') and row.get('weight_pct'):
            amounts[(row['instrument_id'], row.get('account'))] += Decimal(str(row['weight_pct']))
    return exact_weights(amounts)


def execution_codes(request, allocations):
    """새 실행 규칙(입력한 돈만·계좌 안에서만·칩)으로 실행표를 만들어 본 상태와 이유 코드."""
    from .decision import Decision, chip_constraints
    from .execution import plan_execution
    if not allocations:
        return ['NO_ALLOCATIONS']
    try:
        result = plan_execution(request, allocations, Decision(constraints=chip_constraints(request)))
    except Exception as error:
        return ['ERROR:' + error_codes(error)[0]]
    return [result['status']] + [f"{result['status']}:{code(reason)}" for reason in result['reasons']]


def distinct_inputs(rows):
    """같은 입력은 한 번만(가장 최근 job). draft_id 는 저장할 때마다 달라 같은 입력 판정에서 뺀다."""
    seen = {}
    for row in rows:
        key = json.dumps({k: v for k, v in row['input'].items() if k != 'draft_id'}, sort_keys=True, ensure_ascii=False)
        seen.setdefault(key, row)
    return list(seen.values())


class Report:
    def __init__(self):
        self.counts = defaultdict(Counter)
        self.examples = defaultdict(list)

    def add(self, stage, codes, job_id):
        for item in codes:
            self.counts[stage][item] += 1
            label = f'{stage}/{item}'
            if len(self.examples[label]) < EXAMPLES_PER_CODE and re.fullmatch(r'[a-f0-9]{32}', job_id):
                self.examples[label].append(job_id)


async def review(rows, *, as_of, llm_samples=0, key=None):
    deadline = time.monotonic() + TOTAL_SECONDS
    report = Report()
    inputs = distinct_inputs(rows)
    checked = []
    for row in inputs:
        try:
            request, persisted_codes, resubmit_codes = contract(row['input'])
        except Exception as error:
            report.add('contract_error', [code(type(error).__name__)], row['id'])
            continue
        report.add('contract_persisted', persisted_codes or ['OK'], row['id'])
        report.add('contract_resubmit', resubmit_codes or ['OK'], row['id'])
        if request is None:
            continue
        checked.append((row, request))
        report.add('conditions', ['TEXT_WITHOUT_CHIPS' if text_without_chips(request) else
                                  'HAS_CHIPS' if request.constraints else 'NO_TEXT_NO_CHIPS'], row['id'])
        misses = catalog_misses(request)
        report.add('catalog', [f'MISSING_{kind.upper()}' for kind in misses] or ['OK'], row['id'])
        report.add('execution_current', execution_codes(request, current_allocations(request)), row['id'])
        if allocations := past_allocations(row.get('result')):
            report.add('execution_past_recommendation', execution_codes(request, allocations), row['id'])
    baselines, uses = {}, Counter()
    for row, request in checked:
        try:
            ids, reason = baseline_ids(request, as_of)
        except Exception as error:
            ids, reason = None, 'ERROR:' + error_codes(error)[0]
        baselines[row['id']] = (ids, reason)
        uses.update(ids or ())
    records, rejected = await collect_prices(uses, as_of)
    for row, _ in checked:
        ids, reason = baselines[row['id']]
        report.add('price_analysis', [reason or baseline_price(ids, records, rejected, as_of)], row['id'])
        if ids:
            missing = len(ids - records.keys())
            report.add('price_missing_per_analysis', [str(min(missing, 3)) + ('+' if missing >= 3 else '')], row['id'])
    summary = {
        'as_of': as_of.isoformat(), 'rows': len(rows), 'distinct_inputs': len(inputs), 'contract_passed': len(checked),
        'stages': {stage: dict(counter.most_common()) for stage, counter in report.counts.items()},
        'prices': {'instruments': len(uses), 'stored': len(records),
                   'missing': dict(Counter(rejected.values()).most_common())},
    }
    if llm_samples:
        summary['llm'] = await llm_replays(checked[:min(llm_samples, MAX_LLM_SAMPLES)], key, deadline)
    summary['examples'] = dict(report.examples)
    return summary


async def llm_replays(samples, key, deadline=None):
    """최근 서로 다른 입력을 실제 분석으로 돌린다(worker 와 같은 저장 입력 판정). 상태·실패 코드·시간만 남긴다.
    고객 분석이 길게 이어지거나 남은 시간이 한 건에 모자라거나 공급자가 호출 제한을 걸면 나머지는 건너뛴다."""
    from .agent import run_analysis
    from .worker import failure_codes
    deadline = deadline or time.monotonic() + TOTAL_SECONDS
    results, stop = [], None
    for row, request in samples:
        waited = 0
        while not stop and waited < LLM_TIMEOUT_SECONDS and time.monotonic() + LLM_TIMEOUT_SECONDS <= deadline and busy():
            await asyncio.sleep(BUSY_WAIT_SECONDS)
            waited += BUSY_WAIT_SECONDS
        stop = stop or ('skipped_deadline' if time.monotonic() + LLM_TIMEOUT_SECONDS > deadline else None)
        if stop or waited >= LLM_TIMEOUT_SECONDS:
            results.append({'job': row['id'], 'status': stop or 'skipped_busy'})
            continue
        started = time.monotonic()
        try:
            async with asyncio.timeout(LLM_TIMEOUT_SECONDS):
                result = await run_analysis(request, key, lambda role: None)
            optimization = result.get('optimization', {})
            status = str(optimization.get('status', 'none'))
            entry = {'status': 'succeeded', 'optimization': status if SAFE_FIELD.fullmatch(status) else 'other',
                     'candidates': len(optimization.get('candidates', []))}
            seen = json.dumps(result, default=str)
        except Exception as error:
            entry = {'status': 'failed', 'codes': [code(item) for item in failure_codes(error)]}
            seen = f'{entry["codes"]} {error!r}'
        # ponytail: 결과 전체에서 호출 제한 코드를 찾는다. 분석이 제한 표시를 따로 내면 그것으로 바꾼다
        stop = 'skipped_rate_limited' if any(limit in seen for limit in RATE_LIMITS) else None
        results.append({'job': row['id'], **entry, 'seconds': round(time.monotonic() - started, 1)})
    return results


def fit(summary):
    line = 'REVIEW ' + json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
    if len(line) > OUTPUT_LIMIT:
        summary = {**summary, 'examples': 'omitted_for_size'}
        line = 'REVIEW ' + json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
    return line[:OUTPUT_LIMIT]


def read_only_connections():
    """이 프로세스의 모든 DB 연결(분석 재실행 경로 포함)을 읽기 전용 거래로 연다. 쓰기는 DB 가 거부한다."""
    connect = store.connect

    def read_only():
        db = connect()
        db.read_only = True
        return db
    store.connect = read_only


def load(days):
    from .admin_store import TEST_OWNERS
    window = 'AND created_at > now() - make_interval(days => %s::int)' if days else ''
    with store.connect() as db:
        # 시험 방문자의 분석은 빼고 고객 분석만 본다. result 는 지난 추천 배분만 읽는다
        return db.execute(f"""SELECT id, input, jsonb_build_object('recommendation', jsonb_build_object('allocations',
            result->'recommendation'->'allocations')) AS result FROM portfolio_jobs
            WHERE kind='analysis' AND test_label IS NULL AND owner NOT IN ({TEST_OWNERS}) {window} ORDER BY created_at DESC""",
            (days,) if days else ()).fetchall()


def main(argv):
    days, llm_samples = int(argv[0]), int(argv[1])
    if not 0 <= days <= 3650 or not 0 <= llm_samples <= MAX_LLM_SAMPLES:
        raise SystemExit('days 0..3650, llm_samples 0..5')
    read_only_connections()
    rows = load(days)
    summary = asyncio.run(review(rows, as_of=datetime.now(timezone.utc).date(), llm_samples=llm_samples,
                                 key=os.environ.get('DEEPSEEK_API_KEY') if llm_samples else None))
    print(fit({'days': days or 'all', **summary}), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
