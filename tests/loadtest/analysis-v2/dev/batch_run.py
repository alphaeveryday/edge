"""dev 전망 배치 실행과 측정 — 배치를 시작하거나 끝난 실행을 읽어 항목별 시간·시도·DB 연결 수를 남긴다.

실제 AWS dev 자원을 쓴다. `start` 는 ETF 수만큼 유료 분석을 돌린다(완료분은 재사용).

    AWS_PROFILE=edge uv run --with boto3 python batch_run.py start --analysis-at 2026-10-02T06:30:00Z --etf 091160 069500
    AWS_PROFILE=edge uv run --with boto3 python batch_run.py start --analysis-at 2026-10-02T07:00:00Z        # 37종 전체
    AWS_PROFILE=edge uv run --with boto3 python batch_run.py report <실행 이름>                               # 끝난(또는 도는) 실행 측정
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3

REGION, ACCOUNT = 'ap-northeast-2', '393229433969'
BATCH = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:edge-dev-analysis-v2-outlook-batch'
SINGLE_EXECUTION = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:edge-dev-analysis-v2:'
RESULTS = Path(__file__).resolve().parent/'results'
sfn = boto3.client('stepfunctions', region_name=REGION)


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
    return found


def report(args):
    arn = BATCH.replace(':stateMachine:', ':execution:') + ':' + args.name
    execution = sfn.describe_execution(executionArn=arn)
    batch_input = json.loads(execution['input'])
    began, ended = execution['startDate'], execution.get('stopDate') or datetime.now(timezone.utc)
    if execution['status'] == 'SUCCEEDED':
        body = json.loads(execution['output'])
        items, summary = body['items'], body['summary']
    else:  # 실패면 cause 에 집계와 미완료 항목만 있다. 완료 항목은 단건 실행에서 다시 읽는다
        items, summary = [], json.loads(execution['cause']) if execution.get('cause', '').startswith('{') else {'cause': execution.get('cause')}
    events = 0
    for page in sfn.get_paginator('get_execution_history').paginate(executionArn=arn, includeExecutionData=False):
        events += len(page['events'])
    definition = json.loads(sfn.describe_state_machine_for_execution(executionArn=arn)['definition'])
    codes = batch_input.get('etf_codes') or definition['States']['Defaults']['Result']['etf_codes']
    limit = batch_input.get('max_attempts') or definition['States']['Defaults']['Result']['max_attempts']
    outcomes = {i['etf_code']: i for i in items} | {i['etf_code']: i for i in summary.get('unfinished', [])}
    rows = [{'etf_code': code, 'outcome': outcomes.get(code, {}).get('outcome', 'completed' if execution['status'] != 'RUNNING' else 'pending'),
             'published_at': outcomes.get(code, {}).get('published_at'), 'attempts': attempts(code, batch_input['analysis_at'], limit)}
            for code in codes]
    ran = [a for r in rows for a in r['attempts'] if a['seconds'] is not None and began <= datetime.fromisoformat(a['started']) <= ended]
    seconds = sorted(a['seconds'] for a in ran if a['status'] == 'SUCCEEDED')
    connections = boto3.client('cloudwatch', region_name=REGION).get_metric_statistics(
        Namespace='AWS/RDS', MetricName='DatabaseConnections', Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': 'edge-dev'}],
        StartTime=began - timedelta(minutes=10), EndTime=ended + timedelta(minutes=5), Period=60, Statistics=['Maximum'])['Datapoints']
    inside = [p['Maximum'] for p in connections if began <= p['Timestamp'] <= ended]
    before = [p['Maximum'] for p in connections if p['Timestamp'] < began]
    result = {
        'execution': args.name, 'status': execution['status'], 'error': execution.get('error'), 'input': batch_input,
        'started': began.isoformat(), 'stopped': execution['stopDate'].isoformat() if execution.get('stopDate') else None,
        'total_seconds': round((ended - began).total_seconds(), 1), 'history_events': events, 'summary': summary,
        'items': len(rows), 'child_runs_in_this_execution': len(ran),
        'child_failed_in_this_execution': sum(a['status'] != 'SUCCEEDED' for a in ran),
        'retried_items': sum(len(r['attempts']) > 1 for r in rows),
        'child_seconds_succeeded': {'n': len(seconds), 'min': seconds[0], 'median': seconds[len(seconds)//2], 'max': seconds[-1]} if seconds else None,
        'child_seconds_sum': round(sum(a['seconds'] for a in ran), 1),
        'db_connections_max': {'before_10min': max(before, default=None), 'during': max(inside, default=None)},
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
    args = parser.parse_args()
    {'start': start, 'report': report}[args.command](args)
