"""dev 전망 배치 실행과 측정 — 배치를 시작하거나 끝난 실행을 읽어 항목별 시간·시도·DB 연결 수를 남긴다.

실제 AWS dev 자원을 쓴다. `start` 는 ETF 수만큼 유료 분석을 돌린다(완료분은 재사용).

    AWS_PROFILE=edge uv run --with boto3 python batch_run.py start --analysis-at 2026-10-02T06:30:00Z --etf 091160 069500
    AWS_PROFILE=edge uv run --with boto3 python batch_run.py start --analysis-at 2026-10-02T07:00:00Z        # 37종 전체
    AWS_PROFILE=edge uv run --with boto3 python batch_run.py report <실행 이름>                               # 끝난(또는 도는) 실행 측정
    AWS_PROFILE=edge uv run --with boto3 python batch_run.py latest                                           # 가장 최근 배치 실행(스케줄 발화 포함) 측정
"""
import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

REGION, ACCOUNT = 'ap-northeast-2', '393229433969'
BATCH = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:edge-dev-analysis-v2-outlook-batch'
SINGLE_EXECUTION = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:edge-dev-analysis-v2:'
RESULTS = Path(__file__).resolve().parent/'results'
BUCKET = 'edge-dev-pipeline-lake'
sfn = boto3.client('stepfunctions', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)


def start(args):
    payload = {'analysis_at': args.analysis_at}
    if args.etf:
        payload['etf_codes'] = args.etf
    if args.deadline:
        payload['deadline'] = args.deadline
    if args.max_attempts:
        payload['max_attempts'] = args.max_attempts
    name = args.name or 'manual-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    arn = sfn.start_execution(stateMachineArn=BATCH, name=name, input=json.dumps(payload))['executionArn']
    print('started', arn, json.dumps(payload), flush=True)
    if args.no_wait:
        return
    while sfn.describe_execution(executionArn=arn)['status'] == 'RUNNING':
        time.sleep(30)
    report(argparse.Namespace(name=name))


def attempts(etf, analysis_at, limit):
    """한 항목의 시도별 단건 실행(이름 = 분석 ID)을 읽는다. 없는 시도에서 멈춘다."""
    found = []
    for attempt in range(limit):
        ident = hashlib.md5(f'outlook:{etf}:{analysis_at}:{attempt}'.encode()).hexdigest()
        try:
            child = sfn.describe_execution(executionArn=SINGLE_EXECUTION + ident)
        except sfn.exceptions.ExecutionDoesNotExist:
            break
        stop = child.get('stopDate')
        found.append({'attempt': attempt, 'analysis_id': ident, 'status': child['status'],
                      'started': child['startDate'].isoformat(), 'stopped': stop.isoformat() if stop else None,
                      'seconds': round((stop - child['startDate']).total_seconds(), 1) if stop else None})
        if child['status'] not in ('SUCCEEDED', 'RUNNING'):
            found[-1]['failure'] = failure(ident)
        types = [event['type'] for page in sfn.get_paginator('get_execution_history').paginate(
            executionArn=SINGLE_EXECUTION + ident, includeExecutionData=False) for event in page['events']]
        # 단건 워크플로가 자리를 못 얻어 기다린 30초 횟수와, 실행 제어 호출이 실패한 횟수(스로틀, 시작 실패, 시간 초과)
        found[-1] |= {'capacity_waits_30s': types.count('WaitStateEntered'),
                      'control_failures': sum(t.startswith('LambdaFunction') and t.endswith(('Failed', 'TimedOut')) for t in types)}
    return found


def failure(ident):
    """워커가 관측 manifest 에 남긴 실패 유형(예: TimeoutError). 워커가 뜨기 전에 죽었으면 manifest 가 없다."""
    try:
        job = json.loads(s3.get_object(Bucket=BUCKET, Key=f'analysis-v2/runs/{ident}/manifest.json')['Body'].read())['job']
    except s3.exceptions.NoSuchKey:
        return 'no manifest'
    return (job.get('error') or job['status']).split(':')[0]


def slot_wait_seconds(began, ended):
    """구간 안에 워커가 남긴 '슬롯을 몇 초 기다려 잡았다' 로그의 대기 초. 다른 실행(수동 등)의 워커도 섞인다."""
    found, pages = [], boto3.client('logs', region_name=REGION).get_paginator('filter_log_events').paginate(
        logGroupName='/edge/analysis-v2', filterPattern='"Analysis slot"',
        startTime=int(began.timestamp()*1000), endTime=int(ended.timestamp()*1000) + 60000)
    for page in pages:
        for event in page['events']:
            words = event['message'].split()
            if 'after' in words:
                found.append(float(words[words.index('after') + 1]))
    return found


def report(args):
    arn = BATCH.replace(':stateMachine:', ':execution:') + ':' + args.name
    execution = sfn.describe_execution(executionArn=arn)
    batch_input = json.loads(execution['input'])
    began, ended = execution['startDate'], execution.get('stopDate') or datetime.now(timezone.utc)
    if execution['status'] == 'SUCCEEDED':
        body = json.loads(execution['output'])
        items, summary = body['items'], body['summary']
    else:  # 실패면 cause 에 집계와 미완료 항목만 있다. 중단·시간 초과·실행 중이면 집계 자체가 없다
        items, summary = [], json.loads(execution['cause']) if execution.get('cause', '').startswith('{') else {'cause': execution.get('cause')}
    judged = 'unfinished' in summary  # 배치가 항목 판정까지 마쳤는가
    events, waits = 0, {}
    for page in sfn.get_paginator('get_execution_history').paginate(executionArn=arn, includeExecutionData=False):
        events += len(page['events'])
        for event in page['events']:
            if event['type'] == 'WaitStateEntered':  # 30초 대기 1회: WaitForSlot = 실행 중 개수가 슬롯 수 이상, WaitForOther = 같은 작업을 남이 실행 중
                name = event['stateEnteredEventDetails']['name']
                waits[name] = waits.get(name, 0) + 1
    definition = json.loads(sfn.describe_state_machine_for_execution(executionArn=arn)['definition'])
    codes = batch_input.get('etf_codes') or definition['States']['Defaults']['Result']['etf_codes']
    limit = batch_input.get('max_attempts') or definition['States']['Defaults']['Result']['max_attempts']
    outcomes = {i['etf_code']: i for i in items} | {i['etf_code']: i for i in summary.get('unfinished', [])}
    # 집계에 없는 항목은 집계가 있을 때만 완료다(실패 배치의 cause 는 미완료 항목만 싣는다). 집계가 없으면 판정 없음.
    rows = [{'etf_code': code, 'outcome': outcomes.get(code, {}).get('outcome', 'completed' if judged else 'not judged'),
             'published_at': outcomes.get(code, {}).get('published_at'), 'attempts': attempts(code, batch_input['analysis_at'], limit)}
            for code in codes]
    inside = lambda a: began <= datetime.fromisoformat(a['started']) <= ended  # 이 배치 실행 구간에 시작한 시도만
    ran = [a for r in rows for a in r['attempts'] if a['seconds'] is not None and inside(a)]
    seconds = sorted(a['seconds'] for a in ran if a['status'] == 'SUCCEEDED')
    # 단건 실행이 동시에 떠 있던 최대 수(워크플로 기준 — 슬롯을 기다리는 시간도 포함한다)
    spans = [(datetime.fromisoformat(a['started']), datetime.fromisoformat(a['stopped']) if a['stopped'] else ended)
             for r in rows for a in r['attempts'] if inside(a)]  # 아직 도는 시도는 지금까지로 센다
    overlap = max((sum(b <= start < e for b, e in spans) for start, _ in spans), default=0)
    slot_waits = slot_wait_seconds(began, ended)
    reasons = {}
    for a in ran:
        if 'failure' in a:
            reasons[a['failure']] = reasons.get(a['failure'], 0) + 1
    def rds(metric, statistic):
        points = boto3.client('cloudwatch', region_name=REGION).get_metric_statistics(
            Namespace='AWS/RDS', MetricName=metric, Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': 'edge-dev'}],
            StartTime=began - timedelta(minutes=10), EndTime=ended + timedelta(minutes=5), Period=60, Statistics=[statistic])['Datapoints']
        return ([p[statistic] for p in points if p['Timestamp'] < began], [p[statistic] for p in points if began <= p['Timestamp'] <= ended])
    before, during = rds('DatabaseConnections', 'Maximum')
    memory, cpu = rds('FreeableMemory', 'Minimum'), rds('CPUUtilization', 'Maximum')
    every = [a for r in rows for a in r['attempts'] if inside(a)]
    result = {
        'execution': args.name, 'status': execution['status'], 'error': execution.get('error'), 'input': batch_input,
        'started': began.isoformat(), 'stopped': execution['stopDate'].isoformat() if execution.get('stopDate') else None,
        'total_seconds': round((ended - began).total_seconds(), 1), 'history_events': events, 'summary': summary,
        'max_concurrent_children': overlap, 'gate_waits_30s': waits,
        'child_capacity_waits_30s': sum(a['capacity_waits_30s'] for a in every),
        'child_control_failures': sum(a['control_failures'] for a in every),
        'worker_slot_wait_seconds': {'n': len(slot_waits), 'max': max(slot_waits, default=None), 'waited': sum(w > 0 for w in slot_waits)},
        'items': len(rows), 'child_runs_in_this_execution': len(ran),
        'child_failed_in_this_execution': sum(a['status'] != 'SUCCEEDED' for a in ran),
        'failure_reasons': reasons,
        'retried_items': sum(sum(inside(a) for a in r['attempts']) > 1 for r in rows),
        'child_seconds_succeeded': {'n': len(seconds), 'min': seconds[0], 'median': round(statistics.median(seconds), 1), 'max': seconds[-1]} if seconds else None,
        'child_seconds_sum': round(sum(a['seconds'] for a in ran), 1),
        'db_connections_max': {'before_10min': max(before, default=None), 'during': max(during, default=None)},
        'db_freeable_memory_min_mb': {'before_10min': round(min(memory[0])/2**20) if memory[0] else None, 'during': round(min(memory[1])/2**20) if memory[1] else None},
        'db_cpu_max_percent': {'before_10min': round(max(cpu[0]), 1) if cpu[0] else None, 'during': round(max(cpu[1]), 1) if cpu[1] else None},
        'rows': rows,
    }
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS/f'{args.name}.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False, indent=1))
    print('saved', path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('start')
    p.add_argument('--analysis-at', required=True, help='업무 기준시각. 소수 초 없이 Z 로 끝나는 UTC')
    p.add_argument('--etf', nargs='*', help='생략하면 정의에 구워진 37종 전체')
    p.add_argument('--deadline'); p.add_argument('--max-attempts', type=int); p.add_argument('--name')
    p.add_argument('--no-wait', action='store_true')
    p = commands.add_parser('report')
    p.add_argument('name')
    commands.add_parser('latest')
    args = parser.parse_args()
    if args.command == 'latest':  # 스케줄이 시작한 실행은 이름이 임의 값이라 목록에서 찾는다
        found = sfn.list_executions(stateMachineArn=BATCH, maxResults=1)['executions']
        if not found:
            parser.exit(1, '배치 실행 이력이 없다\n')
        args = argparse.Namespace(command='report', name=found[0]['name'])
    {'start': start, 'report': report}[args.command](args)
