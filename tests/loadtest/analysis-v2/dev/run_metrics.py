"""끝난 배치의 단건 시도마다 실제 실행 이미지·실행 ARN 전달·DB 연결·실행 제어 실패·비용·시작 구간을 읽는다.

SFN 실행 이력과 S3 관측 산출물만 읽는다. 분석·태스크를 새로 시작하지 않는다. ECS 는 멈춘 태스크를
약 1시간 뒤 잊으므로 실행 이미지는 실행 이력의 ECS 종료 이벤트에서 읽는다(`TaskSubmitted` 엔 digest 가 없다).

    AWS_PROFILE=edge uv run --no-project --with boto3 python batch_run.py report <실행 이름>   # 먼저 results/<실행 이름>.json
    AWS_PROFILE=edge uv run --no-project --with boto3 python run_metrics.py <실행 이름>
"""
import argparse
import collections
import json
import re
import statistics
from datetime import datetime, timezone

import boto3

from batch_run import BUCKET, REGION, RESULTS, SINGLE_EXECUTION

sfn = boto3.client('stepfunctions', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)
DB_KEYS = ('connects', 'connect_ms_p50', 'connect_ms_p95', 'connect_ms_max', 'operation_ms_total', 'retries', 'retry_wait_s')


def spread(values):
    return [round(min(values), 1), round(statistics.median(values), 1), round(max(values), 1)] if values else None


def attempt(ident):
    arn = SINGLE_EXECUTION + ident
    events = [e for page in sfn.get_paginator('get_execution_history').paginate(executionArn=arn) for e in page['events']]
    found = {'env_arn': None, 'digest': None, 'task_definition': None, 'acquire_s': None, 'provision_s': None,
             'control_failures': collections.Counter()}
    for index, event in enumerate(events):
        kind = event['type']
        if kind == 'TaskScheduled' and event['taskScheduledEventDetails']['resourceType'] == 'ecs':
            env = json.loads(event['taskScheduledEventDetails']['parameters'])['Overrides']['ContainerOverrides'][0]['Environment']
            found['env_arn'] = next((e.get('Value') for e in env if e['Name'] == 'ANALYSIS_EXECUTION_ARN'), None)
        elif kind == 'TaskSubmitted' and event['taskSubmittedEventDetails']['resourceType'] == 'ecs':
            found['acquire_s'] = (event['timestamp'] - events[0]['timestamp']).total_seconds()
        elif kind in ('TaskSucceeded', 'TaskFailed'):
            details = event.get('taskSucceededEventDetails') or event.get('taskFailedEventDetails')
            if details.get('resourceType') == 'ecs':
                raw = details.get('output') or details.get('cause') or ''
                digest, task = re.search(r'"ImageDigest":"(sha256:[0-9a-f]+)', raw), re.search(r'task-definition/([\w-]+:\d+)', raw)
                created, started = re.search(r'"CreatedAt":(\d+)', raw), re.search(r'"StartedAt":(\d+)', raw)
                found['digest'], found['task_definition'] = digest and digest.group(1), task and task.group(1)
                if created and started:
                    found['provision_s'] = (int(started.group(1)) - int(created.group(1))) / 1000
        elif kind.startswith('LambdaFunction') and kind.endswith(('Failed', 'TimedOut')):
            state = next(e['stateEnteredEventDetails']['name'] for e in reversed(events[:index]) if e['type'] == 'TaskStateEntered')
            details = event.get('lambdaFunctionFailedEventDetails') or event.get('lambdaFunctionTimedOutEventDetails') or {}
            found['control_failures'][f"{state}:{details.get('error')}"] += 1
    found['arn_passed'] = found.pop('env_arn') == arn
    try:
        manifest = json.loads(s3.get_object(Bucket=BUCKET, Key=f'analysis-v2/runs/{ident}/manifest.json')['Body'].read())
    except s3.exceptions.NoSuchKey:
        return found | {'db': None, 'cost_usd': None}
    cost = None
    for chunk in reversed(manifest['events']):  # 모델 비용은 마지막 결과 메시지에 있다
        hit = re.findall(r'"total_cost_usd": ?([0-9.]+)', s3.get_object(Bucket=BUCKET, Key=chunk['key'])['Body'].read().decode())
        if hit:
            cost = float(hit[-1])
            break
    return found | {'db': manifest['job'].get('db_connections'), 'cost_usd': cost}


def main(name):
    batch = json.loads((RESULTS/f'{name}.json').read_text())
    # batch_run.py report 의 inside() 와 같은 범위: 같은 기준시각의 이전 배치가 남긴 시도는 세지 않는다
    began = datetime.fromisoformat(batch['started'])
    ended = datetime.fromisoformat(batch['stopped']) if batch['stopped'] else datetime.now(timezone.utc)
    rows = [{'etf_code': r['etf_code'], 'analysis_id': a['analysis_id'], 'status': a['status'], 'failure': a.get('failure')}
            | attempt(a['analysis_id']) for r in batch['rows'] for a in r['attempts']
            if began <= datetime.fromisoformat(a['started']) <= ended]
    done = [r for r in rows if r['status'] == 'SUCCEEDED' and r['db']]
    failures = collections.Counter()
    for r in rows:
        failures.update(r['control_failures'])
    result = {'execution': name, 'attempts': len(rows),
              'digests': collections.Counter(r['digest'] for r in rows), 'task_definitions': collections.Counter(r['task_definition'] for r in rows),
              'arn_passed': sum(r['arn_passed'] for r in rows), 'control_failures': dict(failures),
              'acquire_s': spread([r['acquire_s'] for r in rows if r['acquire_s'] is not None]),
              'provision_s': spread([r['provision_s'] for r in rows if r['provision_s'] is not None]),
              # 성공한 분석의 DB 연결(결과 DB). retries 는 원천 읽기와 결과 DB 단계의 연결 계열 재시도를 함께 센다
              'db_succeeded': {key: spread([r['db'][key] for r in done if r['db'].get(key) is not None]) for key in DB_KEYS},
              # 통계가 없는 시도(워커가 manifest 를 못 남김)는 0 이 아니라 미관측으로 따로 센다
              'db_retries_observed_attempts': sum(r['db']['retries'] for r in rows if r['db']),
              'db_missing_attempts': sum(r['db'] is None for r in rows),
              'cost_usd_succeeded': round(sum(r['cost_usd'] or 0 for r in done), 2),
              'cost_missing_attempts': sum(r['cost_usd'] is None for r in rows), 'rows': rows}
    path = RESULTS/f'{name}.metrics.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False, indent=1, default=str))
    print('saved', path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', help='batch_run.py report 가 남긴 실행 이름')
    main(parser.parse_args().name)
