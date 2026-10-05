"""dev 인프라 비용 산정 — AWS 사용량을 읽기만 하고(collect) 서울 공개 단가로 계산한다(calc·plan).

실제 청구가 아니다. Cost Explorer 는 조직 SCP 로 막혀 있어 쓰지 않는다. 자원을 만들거나 바꾸지 않는다.
- Fargate 과금 시간: SFN 이력에 남은 태스크의 `PullStartedAt`→`StoppedAt`(초 올림, 최소 60초).
- 자리 점유(지연 계산용)는 `Analyze` 진입→`Release` 진입이며 과금 시간과 섞지 않는다.
- LLM API 요금은 넣지 않는다. 모델 요청이 NAT 를 지나는 전송량만 인프라로 센다.

    AWS_PROFILE=edge uv run --no-project --with boto3 python infra_cost.py collect --start 2026-09-22 --end 2026-10-06
    python3 infra_cost.py calc results/infra-usage-20260922-20261006.json     # 서비스·태스크 구간 진단
    python3 infra_cost.py plan results/infra-usage-20260922-20261006.json     # 기준선·단가·시나리오(+ .plan.json)
    python3 infra_cost.py plan <usage.json> --batch <배치 실행 이름> --trading-days 2026-10-06,2026-10-07 --compare <이전 .plan.json>
    uv run --no-project --with pytest python -m pytest test_infra_cost.py -q
"""
import argparse
import collections
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGION, ACCOUNT = 'ap-northeast-2', '393229433969'
BUCKET = 'edge-dev-pipeline-lake'
KST = timezone(timedelta(hours=9))
RESULTS = Path(__file__).resolve().parent / 'results'

# 서울 리전 공개 가격표(pricing.us-east-1.amazonaws.com/offers/v1.0/aws/<서비스>/current/ap-northeast-2/index.csv,
# 2026-09-11~10-02 발행본). 온디맨드·Linux. 전송 규칙은 아래 TRANSFER_RULES 의 공식 문서.
PRICE_CHECKED = '2026-10-05'
PRICE = {
    'fargate_vcpu_h': 0.04656, 'fargate_gb_h': 0.00511,            # AmazonECS APN2-Fargate-vCPU-Hours:perCPU / GB-Hours (x86)
    'fargate_arm_vcpu_h': 0.03725, 'fargate_arm_gb_h': 0.00409,    # APN2-Fargate-ARM-*
    'rds_h': {'db.t4g.micro': 0.025, 'db.t4g.small': 0.051, 'db.t4g.medium': 0.102, 'db.t4g.large': 0.203},  # PostgreSQL Single-AZ
    'rds_gp3_gb_mo': 0.131,
    'ec2': {'t3.micro': 0.013, 't3.small': 0.026, 't4g.small': 0.0208},  # AmazonEC2 Linux Shared
    'ebs_gp3_gb_mo': 0.0912,
    'nat_h': 0.059, 'nat_gb': 0.059,                                # AmazonEC2 NatGateway-Hours/Bytes
    'ipv4_h': 0.005,                                                # AmazonVPC PublicIPv4:InUseAddress
    'alb_h': 0.0225, 'alb_lcu_h': 0.008,                            # AWSELB Application
    'xaz_gb': 0.01, 'internet_out_gb': 0.126,                       # AWSDataTransfer Regional(방향마다) / Out(전역 100 GB 무료 뒤)
    's3_gb_mo': 0.025, 's3_put_1k': 0.0045,
    'logs_ingest_gb': 0.76, 'logs_store_gb_mo': 0.0314, 'alarm_mo': 0.10,
    'sfn_transition': 0.0000271,                                    # AmazonStates Standard (계정 월 4,000 무료)
    'lambda_gb_s': 0.0000166667, 'lambda_req': 0.0000002,           # x86
    'sqs_req': 0.0000004, 'apigw_http_req': 0.00000123,
    'secret_mo': 0.40, 'ecr_gb_mo': 0.10, 'cloudmap_resource_mo': 0.10, 'route53_zone_mo': 0.50,
}
TRANSFER_RULES = {
    'rds': 'EC2(ENI)↔RDS 다른 AZ: 클라이언트 쪽 $0.01/GB, RDS 쪽 추가 요금 없음 — aws.amazon.com/rds/pricing',
    'eni': 'EC2·RDS·ENI 의 AZ 간 전송은 방향마다 $0.01/GB — aws.amazon.com/ec2/pricing/on-demand, Fargate 는 표준 전송 요금',
    'nat': 'NAT 와 다른 AZ 자원 사이 전송에는 AZ 간 요금이 붙는다(쪽별 부과 명시 없음 → $0.01~0.02/GB) — VPC 사용자 안내서 NAT 요금',
}

DEFAULTS = {
    'month_days': 31, 'month_trading': 20,          # 2026-10: 10-05 대체공휴일·10-09 한글날 휴장
    'batch_hour': 6,                                 # 정기 전망 배치 시작 시각(KST). --batch 로 실행 이름을 직접 줄 수 있다
    'events_per_trading_day': 32.0,                  # 가격변동 FIRE 거래일 중앙(08-03~10-02, 42거래일, DB 조회 — 이 JSON 밖)
    'event_rates': [0.3, 0.8, 2.2],                  # ETF당 하루 사건(가정): 낮음 / 현재 39종 평균 / 현재 최대일
    'current_price_calls': 468 * 390,                # 1분 가격 워커 하루 호출: 468종목 × 390창
    'kis_bytes_low': 2000,                           # 호출당 NAT 바이트 하한(압축 응답 가정). 상한은 측정으로 계산
    'tokens_mb': {'outlook': 9.6, 'movement': 2.7},  # 실행당 모델 요청 본문(입력+캐시 토큰 × 4바이트 근사) → NAT
    'session_hours': 8.6,                            # 장중 서비스 거래일 가동 시간(10-02 실측 518분)
    'min_sample': 10,
    'scenarios': [
        {'name': 'S1 국내 주식형(이름 분류 427, 지원 미검증)', 'N': 427, 'U': [1000, 1800], 'accounts': [2, 2]},
        {'name': 'S2 국내 상장 전체 지원 가정(1,175)', 'N': 1175, 'U': [1800, 2700], 'accounts': [2, 4]},
    ],
}


def fargate_hourly(cpu_units, mem_mb, arm=False):
    p = ('fargate_arm_vcpu_h', 'fargate_arm_gb_h') if arm else ('fargate_vcpu_h', 'fargate_gb_h')
    return cpu_units / 1024 * PRICE[p[0]] + mem_mb / 1024 * PRICE[p[1]]


def billed_seconds(task):
    """Fargate 과금 초: 이미지 받기 시작부터 태스크 종료까지, 초 올림, 최소 60초(Linux)."""
    if task.get('pull_start') is None or task.get('stopped') is None:
        return None
    return max(60, math.ceil(task['stopped'] - task['pull_start']))


def kind_of(execution):
    m = re.search(r'"kind"\s*:\s*"(\w+)"', execution.get('input') or '')
    return m.group(1) if m else None


def etf_of(execution):
    m = re.search(r'"etf_code"\s*:\s*"(\w+)"', execution.get('input') or '')
    return m.group(1) if m else None


def at_of(execution):
    """입력의 analysis_at 을 시각(초)으로. 정기 배치와 그 자식은 같은 기준시각을 쓴다."""
    m = re.search(r'"analysis_at"\s*:\s*"([^"]+)"', execution.get('input') or '')
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def billing_state(task):
    """'billed' / 'not_billed'(기록은 읽었고 이미지 받기 전에 끝남 — 과금 없음) / 'unknown'(기록을 못 읽었거나 아직 안 끝남)."""
    if billed_seconds(task) is not None:
        return 'billed'
    parsed = task.get('parsed', task.get('created') is not None or bool(task.get('family')))
    if parsed and task.get('pull_start') is None and task.get('stopped') is not None:
        return 'not_billed'
    return 'unknown'


def run_puts(execution, run_objects):
    """한 분석 실행의 S3 PUT: 이벤트 조각·파일 객체(목록 실측) + manifest 덮어쓰기(코드상 시작·5초마다·종료, 추정)."""
    objects = (run_objects or {}).get(execution['name'])
    if objects is None:
        return None, None
    run_s = sum((t.get('exec_stopped') or t.get('stopping') or 0) - (t.get('started') or 0)
                for t in execution['tasks'] if t.get('started'))
    manifest = (2 + math.ceil(run_s / 5)) if objects['manifest'] else 0
    return objects['events'] + objects['files'], manifest


# ---------------------------------------------------------------- collect (읽기 전용)

def collect(args):
    import boto3
    start = datetime.fromisoformat(args.start).replace(tzinfo=KST)
    end = datetime.fromisoformat(args.end).replace(tzinfo=KST)
    cw = boto3.client('cloudwatch', region_name=REGION)
    ecs = boto3.client('ecs', region_name=REGION)
    sfn = boto3.client('stepfunctions', region_name=REGION)

    def series(ns, metric, dims, stat='Sum', period=86400):
        """KST 날짜별 값. 하루 버킷은 KST 자정 기준. 점이 없는 날은 키가 없다(0 과 구분)."""
        out = collections.defaultdict(float)
        t = start
        while t < end:  # GetMetricStatistics 는 한 번에 1,440 점
            t2 = min(end, t + timedelta(days=1 if period == 60 else 60))
            r = cw.get_metric_statistics(Namespace=ns, MetricName=metric, StartTime=t, EndTime=t2, Period=period,
                                         Statistics=[stat], Dimensions=[{'Name': k, 'Value': v} for k, v in dims.items()])
            for p in r['Datapoints']:
                out[p['Timestamp'].astimezone(KST).date().isoformat()] += p[stat]
            t = t2
        return dict(sorted(out.items()))

    data = {'window': [start.isoformat(), end.isoformat()], 'collected_at': datetime.now(KST).isoformat(),
            'meta': {'region': REGION, 'account': ACCOUNT}}
    # 계정 전체 Fargate 동시 vCPU(분당 평균) → 날짜별 vCPU·분. 이 지표가 없는 날은 수집 공백으로 본다.
    data['fargate_vcpu_minutes'] = series('AWS/Usage', 'ResourceCount',
                                          {'Service': 'Fargate', 'Type': 'Resource', 'Resource': 'vCPU', 'Class': 'Standard/OnDemand'},
                                          'Average', 60)
    services = []
    for cluster in [c.split('/')[-1] for c in ecs.list_clusters()['clusterArns']]:
        arns = ecs.list_services(cluster=cluster, maxResults=50)['serviceArns']
        for chunk in [arns[i:i + 10] for i in range(0, len(arns), 10)]:
            for s in ecs.describe_services(cluster=cluster, services=chunk)['services']:
                td = ecs.describe_task_definition(taskDefinition=s['taskDefinition'])['taskDefinition']
                dims = {'ClusterName': cluster, 'ServiceName': s['serviceName']}
                services.append({'cluster': cluster, 'service': s['serviceName'], 'task_definition': s['taskDefinition'].split('/')[-1],
                                 'cpu': td.get('cpu'), 'memory': td.get('memory'),
                                 'arch': (td.get('runtimePlatform') or {}).get('cpuArchitecture', 'X86_64'),
                                 # CPU 지표의 분당 표본 수 = 서비스에 태스크가 떠 있던 분(태스크 수와 무관하게 1)
                                 'running_minutes': series('AWS/ECS', 'CPUUtilization', dims, 'SampleCount', 60),
                                 'cpu_avg_pct': series('AWS/ECS', 'CPUUtilization', dims, 'Average'),
                                 'cpu_max_pct': series('AWS/ECS', 'CPUUtilization', dims, 'Maximum')})
    data['services'] = services

    ec2 = boto3.client('ec2', region_name=REGION)
    nat = ec2.describe_nat_gateways(Filters=[{'Name': 'state', 'Values': ['available']}])['NatGateways']
    data['nat'] = {n['NatGatewayId']: {m: series('AWS/NATGateway', m, {'NatGatewayId': n['NatGatewayId']})
                                       for m in ('BytesInFromSource', 'BytesOutToDestination', 'BytesInFromDestination', 'BytesOutToSource')}
                   for n in nat}
    data['ec2'] = [{'id': i['InstanceId'], 'type': i['InstanceType'], 'name': next((t['Value'] for t in i.get('Tags', []) if t['Key'] == 'Name'), ''),
                    'az': i['Placement']['AvailabilityZone'], 'public_ip': i.get('PublicIpAddress')}
                   for r in ec2.describe_instances(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}])['Reservations'] for i in r['Instances']]
    data['ebs'] = [{'id': v['VolumeId'], 'type': v['VolumeType'], 'gb': v['Size'], 'instance': (v['Attachments'] or [{}])[0].get('InstanceId')}
                   for v in ec2.describe_volumes()['Volumes']]
    data['public_ipv4'] = [{'ip': n['Association']['PublicIp'], 'type': n['InterfaceType'], 'desc': n['Description']}
                           for n in ec2.describe_network_interfaces()['NetworkInterfaces'] if n.get('Association', {}).get('PublicIp')]
    data['vpc_endpoints'] = [e['ServiceName'] for e in ec2.describe_vpc_endpoints()['VpcEndpoints']]

    rds = boto3.client('rds', region_name=REGION)
    data['rds'] = []
    for d in rds.describe_db_instances()['DBInstances']:
        dims = {'DBInstanceIdentifier': d['DBInstanceIdentifier']}
        data['rds'].append({'id': d['DBInstanceIdentifier'], 'class': d['DBInstanceClass'], 'az': d['AvailabilityZone'], 'multi_az': d['MultiAZ'],
                            'storage_gb': d['AllocatedStorage'], 'storage_type': d['StorageType'], 'backup_days': d['BackupRetentionPeriod'],
                            'credits_charged': series('AWS/RDS', 'CPUSurplusCreditsCharged', dims),
                            'cpu_avg_pct': series('AWS/RDS', 'CPUUtilization', dims, 'Average'),
                            'cpu_max_pct': series('AWS/RDS', 'CPUUtilization', dims, 'Maximum'),
                            'free_storage_min': series('AWS/RDS', 'FreeStorageSpace', dims, 'Minimum'),
                            # 하루 평균 초당 바이트 — × 86,400 이 하루 바이트. 고객 질의와 RDS 감시 트래픽을 함께 센다
                            'net_rx_bps': series('AWS/RDS', 'NetworkReceiveThroughput', dims, 'Average'),
                            'net_tx_bps': series('AWS/RDS', 'NetworkTransmitThroughput', dims, 'Average')})

    elb = boto3.client('elbv2', region_name=REGION)
    data['alb'] = {lb['LoadBalancerName']: series('AWS/ApplicationELB', 'ConsumedLCUs', {'LoadBalancer': lb['LoadBalancerArn'].split(':loadbalancer/')[1]})
                   for lb in elb.describe_load_balancers()['LoadBalancers'] if lb['Type'] == 'application'}
    apis = boto3.client('apigatewayv2', region_name=REGION).get_apis()['Items']
    data['apigw'] = {a['Name']: series('AWS/ApiGateway', 'Count', {'ApiId': a['ApiId']}) for a in apis}
    lam = boto3.client('lambda', region_name=REGION)
    data['lambda'] = {f['FunctionName']: {'memory_mb': f['MemorySize'], 'arch': f['Architectures'][0],
                                          'invocations': series('AWS/Lambda', 'Invocations', {'FunctionName': f['FunctionName']}),
                                          'duration_ms': series('AWS/Lambda', 'Duration', {'FunctionName': f['FunctionName']})}
                      for f in lam.list_functions()['Functions']}
    sqs = boto3.client('sqs', region_name=REGION)
    data['sqs'] = {u.split('/')[-1]: {m: series('AWS/SQS', m, {'QueueName': u.split('/')[-1]})
                                      for m in ('NumberOfMessagesSent', 'NumberOfMessagesReceived', 'NumberOfMessagesDeleted', 'NumberOfEmptyReceives')}
                   for u in sqs.list_queues().get('QueueUrls', [])}
    logs = boto3.client('logs', region_name=REGION)
    groups = [g for p in logs.get_paginator('describe_log_groups').paginate() for g in p['logGroups']]
    data['logs'] = {'stored_gb': sum(g.get('storedBytes', 0) for g in groups) / 1e9,
                    'ingest_bytes': {g['logGroupName']: series('AWS/Logs', 'IncomingBytes', {'LogGroupName': g['logGroupName']}) for g in groups}}
    data['s3'] = {b: {'gb': series('AWS/S3', 'BucketSizeBytes', {'BucketName': b, 'StorageType': 'StandardStorage'}, 'Average'),
                      'objects': series('AWS/S3', 'NumberOfObjects', {'BucketName': b, 'StorageType': 'AllStorageTypes'}, 'Average')}
                  for b in [x['Name'] for x in boto3.client('s3').list_buckets()['Buckets']]}
    ecr = boto3.client('ecr', region_name=REGION)
    data['ecr_gb'], data['ecr_unique_gb'] = {}, {}
    for r in ecr.describe_repositories()['repositories']:
        name = r['repositoryName']
        images = [i for p in ecr.get_paginator('describe_images').paginate(repositoryName=name) for i in p['imageDetails']]
        data['ecr_gb'][name] = sum(i['imageSizeInBytes'] for i in images) / 1e9  # 공유 레이어를 이미지마다 다시 센 상한
        layers = {}
        for k in range(0, len(images), 100):
            for img in ecr.batch_get_image(repositoryName=name, imageIds=[{'imageDigest': i['imageDigest']} for i in images[k:k + 100]],
                                           acceptedMediaTypes=['application/vnd.docker.distribution.manifest.v2+json',
                                                               'application/vnd.oci.image.manifest.v1+json'])['images']:
                layers.update({l['digest']: l['size'] for l in json.loads(img['imageManifest']).get('layers', [])})
        data['ecr_unique_gb'][name] = sum(layers.values()) / 1e9  # 저장소 안 고유 레이어 합
    data['counts'] = {'secrets': len(boto3.client('secretsmanager', region_name=REGION).list_secrets(MaxResults=100)['SecretList']),
                      'alarms': len(cw.describe_alarms(MaxRecords=100)['MetricAlarms']),
                      'route53_zones': len(boto3.client('route53').list_hosted_zones()['HostedZones'])}
    sd = boto3.client('servicediscovery', region_name=REGION)
    data['counts']['cloudmap_instances'] = sum(len(sd.list_instances(ServiceId=s['Id'])['Instances']) for s in sd.list_services()['Services'])

    # SFN: 실행마다 상태 전이 수, Lambda 호출 수, ECS 태스크의 과금 시각과 자리 점유 구간
    def history(execution):
        events = [e for p in sfn.get_paginator('get_execution_history').paginate(executionArn=execution['executionArn']) for e in p['events']]
        row = {'name': execution['name'], 'status': execution['status'], 'start': execution['startDate'].timestamp(),
               'stop': execution['stopDate'].timestamp() if execution.get('stopDate') else None,
               'transitions': sum(e['type'].endswith('StateEntered') for e in events),
               'lambda_calls': sum(e['type'] == 'LambdaFunctionScheduled' for e in events), 'tasks': [], 'input': None, 'states': []}
        for e in events:
            if e['type'] == 'ExecutionStarted':
                row['input'] = e['executionStartedEventDetails']['input'][:400]
            if e['type'].endswith('StateEntered'):
                row['states'].append((e['stateEnteredEventDetails']['name'], e['timestamp'].timestamp()))
            if e['type'] in ('TaskSucceeded', 'TaskFailed', 'TaskTimedOut'):
                d = e.get('taskSucceededEventDetails') or e.get('taskFailedEventDetails') or e.get('taskTimedOutEventDetails') or {}
                if d.get('resourceType') != 'ecs':
                    continue
                try:
                    t = json.loads(d.get('output') or d.get('cause') or '{}')
                except ValueError:
                    t = {}
                ms = lambda k: t[k] / 1000 if isinstance(t.get(k), (int, float)) else None
                row['tasks'].append({'parsed': bool(t), 'family': (t.get('Group') or '').replace('family:', ''), 'cpu': int(t.get('Cpu') or 0), 'memory': int(t.get('Memory') or 0),
                                     'created': ms('CreatedAt'), 'pull_start': ms('PullStartedAt'), 'pull_stop': ms('PullStoppedAt'),
                                     'started': ms('StartedAt'), 'exec_stopped': ms('ExecutionStoppedAt'), 'stopping': ms('StoppingAt'),
                                     'stopped': ms('StoppedAt'), 'az': t.get('AvailabilityZone'), 'event': e['type']})
        return row

    executions = {}
    for sm in sfn.list_state_machines()['stateMachines']:
        rows = []
        for p in sfn.get_paginator('list_executions').paginate(stateMachineArn=sm['stateMachineArn']):
            rows += [x for x in p['executions'] if start <= x['startDate'] < end]
            if p['executions'] and p['executions'][-1]['startDate'] < start:
                break
        with ThreadPoolExecutor(8) as pool:
            executions[sm['name']] = list(pool.map(history, rows))
    data['sfn'] = executions

    # 분석 실행별 S3 객체(이벤트 조각·파일·manifest). 실행 이름 = 분석 ID 접두사
    s3 = boto3.client('s3', region_name=REGION)

    def objects(name):
        keys = [o['Key'] for p in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=f'analysis-v2/runs/{name}/')
                for o in p.get('Contents', [])]
        return {'events': sum(k.endswith('/events.jsonl') for k in keys),
                'files': sum('/objects/' in k and not k.endswith('/events.jsonl') for k in keys),
                'manifest': sum(k.endswith('/manifest.json') for k in keys)}
    names = [r['name'] for r in executions.get('edge-dev-analysis-v2', [])]
    with ThreadPoolExecutor(16) as pool:
        data['run_objects'] = dict(zip(names, pool.map(objects, names)))
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"infra-usage-{start:%Y%m%d}-{end:%Y%m%d}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding='utf-8')
    print('saved', path, {k: len(v) for k, v in executions.items()})


# ---------------------------------------------------------------- 공통

def classify_days(d, trading_override=None):
    """기간의 날짜를 거래일·휴일·수집 공백·미완료로 나눈다. 공백·미완료 날은 평균에서 뺀다."""
    start = datetime.fromisoformat(d['window'][0]).astimezone(KST).date()
    end = datetime.fromisoformat(d['window'][1]).astimezone(KST).date()
    collected = datetime.fromisoformat(d['collected_at']).astimezone(KST)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]
    partial = [x for x in days if datetime.fromisoformat(x).replace(tzinfo=KST) + timedelta(days=1) > collected]
    missing = [x for x in days if x not in d['fargate_vcpu_minutes'] and x not in partial]
    valid = [x for x in days if x not in partial and x not in missing]
    if trading_override:
        trading = [x for x in valid if x in trading_override]
    else:
        price = next((s for s in d['services'] if s['service'].endswith('price-worker')), None)
        trading = [x for x in valid if price and price['running_minutes'].get(x, 0) > 60]
    other = [x for x in valid if x not in trading]
    return {'days': days, 'trading': trading, 'other': other, 'missing': missing, 'partial': partial}


def avg(series, ds):
    """유효한 날의 평균. 지표 점이 없는 유효한 날은 '실행 없음' = 0 이다(수집 공백 날은 ds 에서 이미 빠졌다)."""
    return sum(series.get(x, 0) for x in ds) / len(ds) if ds else None


# ---------------------------------------------------------------- plan

FINISHED = ('SUCCEEDED', 'FAILED')  # 정기 배치는 항목 실패가 있어도 끝까지 돌고 FAILED 로 끝난다


def scheduled_batches(d, names=None, hour=6):
    """정기 전망 배치와 그 자식 분석 실행. 이름을 주면 그 실행만, 아니면 hour:00~hour:10 KST 에 시작한 배치."""
    batches = []
    for b in d['sfn'].get('edge-dev-analysis-v2-outlook-batch', []):
        st = datetime.fromtimestamp(b['start'], KST)
        if names and b['name'] not in names:
            continue
        if not names and not (st.hour == hour and st.minute < 10):
            continue
        if b['status'] not in FINISHED:  # 도는 중·중단된 배치는 자식 표본이 덜 찼다
            batches.append({'batch': b, 'children': None, 'date': st.date().isoformat(), 'matched_by': None})
            continue
        stop = b['stop'] or float('inf')
        at = at_of(b)  # 배치가 자식에게 같은 기준시각을 넘긴다 — 시간 창이 겹친 수동 실행을 거른다
        children = [r for r in d['sfn'].get('edge-dev-analysis-v2', [])
                    if kind_of(r) == 'outlook' and b['start'] <= r['start'] <= stop and (at is None or at_of(r) == at)]
        batches.append({'batch': b, 'children': children, 'date': st.date().isoformat(),
                        'matched_by': 'analysis_at' if at is not None else '시간 창(배치 입력에 analysis_at 없음)'})
    return batches


def analysis_cost(rows, run_objects, tokens_mb):
    """실행 목록의 실제 누적 비용: Fargate(과금 초 합), S3 PUT(객체 실측 + manifest 추정), NAT(요청 본문 근사), SFN·Lambda(이력 실측)."""
    states = collections.Counter(billing_state(t) for r in rows for t in r['tasks'])
    billed = sum(billed_seconds(t) or 0 for r in rows for t in r['tasks'])
    fargate = sum(fargate_hourly(t['cpu'], t['memory']) * billed_seconds(t) / 3600 for r in rows for t in r['tasks'] if billed_seconds(t))
    puts_obj = puts_manifest = 0
    unknown_puts = 0
    for r in rows:
        o, m = run_puts(r, run_objects)
        if o is None:
            unknown_puts += 1
            continue
        puts_obj += o
        puts_manifest += m
    tasks = sum(1 for r in rows if r['tasks'])
    return {'runs': len(rows), 'tasks': tasks, 'billed_s': billed, 'tasks_unknown_billing': states['unknown'],
            # 과금 시각을 못 읽은 태스크가 있으면 0 이 아니라 '모름'이다
            'fargate': None if states['unknown'] else fargate,
            's3_puts_objects': puts_obj, 's3_puts_manifest_est': puts_manifest, 's3_runs_without_listing': unknown_puts,
            # 목록이 없는 실행이 하나라도 있으면 0 이 아니라 '모름'이다
            's3': None if unknown_puts else (puts_obj + puts_manifest) * PRICE['s3_put_1k'] / 1000,
            'nat': tasks * tokens_mb / 1000 * PRICE['nat_gb'],
            'sfn': sum(r['transitions'] for r in rows) * PRICE['sfn_transition'],
            'lambda': sum(r['lambda_calls'] for r in rows) * (0.25 * 1.3 * PRICE['lambda_gb_s'] + PRICE['lambda_req'])}


def build_plan(d, opts):
    o = {**DEFAULTS, **{k: v for k, v in opts.items() if v is not None}}
    M, T = o['month_days'], o['month_trading']
    O = M - T
    cls = classify_days(d, o.get('trading_days'))
    trading, other = cls['trading'], cls['other']
    if not trading or not other:
        raise ValueError(f'거래일 {len(trading)}일·휴일 {len(other)}일 — 둘 다 1일 이상이어야 평균을 낸다(기간 또는 --trading-days 조정)')
    rows, notes = [], []

    def total_of(c):
        """확대 단가: 실행이 새로 만드는 사용량 전부."""
        return None if c['s3'] is None or c['fargate'] is None else c['fargate'] + c['s3'] + c['nat'] + c['sfn'] + c['lambda']

    def baseline_of(c):
        """현재 기준선: NAT·Lambda 는 계정 실측 행(NAT 처리, Lambda·SQS·API Gateway)에 이미 있어 뺀다."""
        return None if c['s3'] is None or c['fargate'] is None else c['fargate'] + c['s3'] + c['sfn']

    def add(group, kind, name, t, oth, note):
        rows.append({'group': group, 'kind': kind, 'name': name, 'trading': t, 'other': oth,
                     'month': None if t is None or oth is None else t * T + oth * O, 'note': note})

    # --- 분석 단가(실제 누적 과금 기반)
    batches = scheduled_batches(d, o.get('batches'), o['batch_hour'])
    per_batch = []
    for b in [b for b in batches if b['children'] is None]:
        notes.append(f"정기 배치 {b['batch']['name']} 상태 {b['batch']['status']} — 끝나지 않아 표본에서 뺐다")
    for b in [b for b in batches if b['children'] is not None]:
        c = analysis_cost(b['children'], d.get('run_objects'), o['tokens_mb']['outlook'])
        c['sfn'] += b['batch']['transitions'] * PRICE['sfn_transition']
        targets = len({etf_of(r) for r in b['children'] if etf_of(r)})
        per_batch.append({'name': b['batch']['name'], 'date': b['date'], 'attempts': c['runs'], 'targets': targets,
                          'completed': sum(r['status'] == 'SUCCEEDED' for r in b['children']), 'matched_by': b['matched_by'], **c,
                          'total': total_of(c), 'baseline': baseline_of(c)})
    if any(b['total'] is None for b in per_batch):
        notes.append('정기 배치 실행 일부에 S3 목록 또는 과금 시각이 없다 — 전망 비용 N/A(collect 를 다시 돌린다)')
        per_batch_ok = False
    else:
        per_batch_ok = True
    if per_batch and per_batch_ok:
        outlook_day = sum(b['baseline'] for b in per_batch) / len(per_batch)
        targets = round(sum(b['targets'] for b in per_batch) / len(per_batch))
        outlook_unit = sum(b['total'] for b in per_batch) / len(per_batch) / targets if targets else None
        retry_ratio = sum(b['attempts'] for b in per_batch) / max(1, sum(b['targets'] for b in per_batch))
    else:
        outlook_day = outlook_unit = targets = retry_ratio = None
        if not per_batch:
            notes.append('정기 전망 배치 표본 0 — 전망 비용·단가는 N/A(0 으로 두지 않음)')

    movement_rows = [r for r in d['sfn'].get('edge-dev-analysis-v2', []) if kind_of(r) == 'movement' and r['tasks']]
    mv = analysis_cost(movement_rows, d.get('run_objects'), o['tokens_mb']['movement']) if movement_rows else None
    movement_unit = total_of(mv) / mv['runs'] if mv and total_of(mv) is not None else None
    movement_base = baseline_of(mv) / mv['runs'] if mv and baseline_of(mv) is not None else None
    if mv and movement_unit is None:
        notes.append('가격변동 실행 일부에 S3 목록 또는 과금 시각이 없다 — 건당 단가 N/A')
    if not mv:
        notes.append('가격변동 실행 표본 0 — 건당 단가 N/A')
    elif mv['runs'] < o['min_sample']:
        notes.append(f"가격변동 실행 표본 {mv['runs']}건(<{o['min_sample']}) — 단가 표본 부족")

    # --- 기준선: 상시 고정
    for s in d['services']:
        if s['cluster'] == 'edge-dev-service' and s['cpu']:
            h = fargate_hourly(int(s['cpu']), int(s['memory']), s['arch'] == 'ARM64')
            add('기타 서비스', '상시 고정', f"Fargate {s['service']} {s['cpu']}/{s['memory']}", h * 24, h * 24, '상시 1대(desired)')
    for r in d['rds']:
        st = r['storage_gb'] * PRICE['rds_gp3_gb_mo'] / M
        c = PRICE['rds_h'][r['class']] * 24 + st
        add('공유 기반' if r['id'] == 'edge-dev' else '기타 서비스', '상시 고정', f"RDS {r['id']} {r['class']} + gp3 {r['storage_gb']} GB", c, c,
            f"크레딧 과금 {sum(r['credits_charged'].values()):.1f} vCPU·시간, 백업은 무료 할당 안으로 추정(지표 없음)")
    groups = {'edge-dev-airflow-host': '공유 기반'}
    for e in d['ec2']:
        gb = sum(v['gb'] for v in d['ebs'] if v['instance'] == e['id'])
        c = PRICE['ec2'][e['type']] * 24 + gb * PRICE['ebs_gp3_gb_mo'] / M
        add(groups.get(e['name'], '기타 서비스'), '상시 고정', f"EC2 {e['name']} {e['type']} + EBS {gb} GB", c, c, 'unlimited 크레딧 과금은 지표로 확인')
    nat_h = PRICE['nat_h'] * 24 * len(d['nat'])
    add('공유 기반', '상시 고정', 'NAT 게이트웨이 시간', nat_h, nat_h, f"{len(d['nat'])}개")
    ip_by = collections.Counter('ALB' if 'ELB' in x['desc'] else 'NAT' if x['type'] == 'nat_gateway' else 'EC2' for x in d['public_ipv4'])
    for k, n in sorted(ip_by.items()):
        c = n * PRICE['ipv4_h'] * 24
        add('공유 기반' if k == 'NAT' else '기타 서비스', '상시 고정', f'공인 IPv4 {k} {n}개', c, c, '$0.005/시간')
    lcu = sum(sum(v.get(x, 0) for x in trading + other) for v in d['alb'].values()) / max(1, len(trading + other))
    alb = len(d['alb']) * PRICE['alb_h'] * 24 + lcu * PRICE['alb_lcu_h']
    add('기타 서비스', '상시 고정', f"ALB {len(d['alb'])}개(시간+LCU)", alb, alb, 'LCU 측정')
    ecr_lo, ecr_hi = sum(d['ecr_unique_gb'].values()), sum(d['ecr_gb'].values())
    add('공유 기반', '상시 고정', f'ECR 저장 {ecr_lo:.0f} GB(이미지 합 {ecr_hi:.0f} GB)', ecr_lo * PRICE['ecr_gb_mo'] / M, ecr_lo * PRICE['ecr_gb_mo'] / M,
        f"저장소 안 고유 레이어. 이미지 합이면 월 +{(ecr_hi - ecr_lo) * PRICE['ecr_gb_mo']:.1f}")
    s3gb = sum(v['gb'][max(v['gb'])] for v in d['s3'].values() if v['gb']) / 1e9
    add('공유 기반', '상시 고정', f'S3 저장 {s3gb:.0f} GB', s3gb * PRICE['s3_gb_mo'] / M, s3gb * PRICE['s3_gb_mo'] / M, '기간 마지막 날')
    misc = (d['counts']['secrets'] * PRICE['secret_mo'] + d['counts']['alarms'] * PRICE['alarm_mo']
            + d['counts']['cloudmap_instances'] * PRICE['cloudmap_resource_mo'] + d['counts'].get('route53_zones', 1) * PRICE['route53_zone_mo']) / M
    add('공유 기반', '상시 고정', f"Secrets {d['counts']['secrets']}·알람 {d['counts']['alarms']}·Cloud Map {d['counts']['cloudmap_instances']}·Route53 {d['counts'].get('route53_zones', 1)}",
        misc, misc, '무료 한도(알람 10개 등) 미적용')

    # --- 거래일 장중 서비스
    for s in d['services']:
        if s['cluster'] == 'edge-dev-worker' and s['cpu']:
            h = fargate_hourly(int(s['cpu']), int(s['memory']), s['arch'] == 'ARM64')
            add('장중 경로', '거래일 장중 서비스', f"Fargate {s['service'].replace('edge-dev-data-pipeline-', '')} {s['cpu']}/{s['memory']}",
                h * avg(s['running_minutes'], trading) / 60, h * avg(s['running_minutes'], other) / 60, '측정 가동 분')

    # --- 실행량
    if outlook_day is not None:
        add('장중 경로', '실행량', f'전망 정기 배치(배치 {len(per_batch)}개, 시도 평균 {sum(b["attempts"] for b in per_batch) / len(per_batch):.1f})',
            outlook_day, outlook_day, '배치별 실제 누적 과금·객체 수 + SFN, 매일(NAT·Lambda 는 계정 실측 행에 포함)')
    else:
        add('장중 경로', '실행량', '전망 정기 배치', None, None, 'N/A 표본 0')
    add('장중 경로', '실행량', f"가격변동({o['events_per_trading_day']:g}건/거래일)",
        None if movement_base is None else o['events_per_trading_day'] * movement_base, 0,
        'N/A 표본 0' if movement_base is None else f"건당 ${movement_base:.4f}(표본 {mv['runs']}건 평균, NAT·Lambda 제외)")
    for sm, rs in sorted(d['sfn'].items()):
        if sm.startswith('edge-dev-data-pipeline'):
            by = collections.defaultdict(float)
            unknown = 0
            for r in rs:
                by[datetime.fromtimestamp(r['start'], KST).date().isoformat()] += r['transitions'] * PRICE['sfn_transition']  # SFN 상태 전이
                for t in r['tasks']:
                    unknown += billing_state(t) == 'unknown'
                    if billed_seconds(t):
                        by[datetime.fromtimestamp(r['start'], KST).date().isoformat()] += fargate_hourly(t['cpu'], t['memory']) * billed_seconds(t) / 3600
            name = f"배치 태스크 {sm.replace('edge-dev-data-pipeline', 'pipeline')}"
            if unknown:
                notes.append(f'{name}: 과금 시각을 모르는 태스크 {unknown}개 — N/A')
                add('장중 경로', '실행량', name, None, None, 'N/A 과금 시각 결손')
            else:
                add('장중 경로', '실행량', name, avg(by, trading), avg(by, other), 'SFN 태스크 과금 시간 + 상태 전이')
    proc = {}
    for m in d['nat'].values():
        for x in cls['days']:
            proc[x] = proc.get(x, 0) + m['BytesInFromSource'].get(x, 0) + m['BytesInFromDestination'].get(x, 0)
    # NAT 는 트래픽이 없어도 점을 남긴다 — 유효한 날에 점이 없으면 0 이 아니라 결손이다
    nat_gaps = sorted({x for m in d['nat'].values() for k in ('BytesInFromSource', 'BytesInFromDestination') for x in trading + other if x not in m[k]})
    if d['nat'] and not nat_gaps:
        nat_t, nat_o = avg(proc, trading) / 1e9, avg(proc, other) / 1e9
        add('공유 기반', '실행량', f'NAT 처리 {nat_t:.2f}/{nat_o:.2f} GB(거래일/휴일)', nat_t * PRICE['nat_gb'], nat_o * PRICE['nat_gb'],
            '소스 입력 + 목적지 입력(바이트마다 한 번)')
    else:
        nat_t = nat_o = None
        notes.append(f'NAT 처리 지표 결손 {nat_gaps or "게이트웨이 없음"} — NAT 처리 N/A')
        add('공유 기반', '실행량', 'NAT 처리', None, None, 'N/A 지표 없음')
    ingest = collections.defaultdict(float)
    for g in d['logs']['ingest_bytes'].values():
        for k, v in g.items():
            ingest[k] += v
    store = d['logs']['stored_gb'] * PRICE['logs_store_gb_mo'] / M
    add('공유 기반', '실행량', 'CloudWatch 로그 수집·보관', avg(ingest, trading) / 1e9 * PRICE['logs_ingest_gb'] + store,
        avg(ingest, other) / 1e9 * PRICE['logs_ingest_gb'] + store, '측정')
    valid = trading + other

    def requests_cost(ds):  # 거래일·휴일을 따로 평균 낸다(기준월의 거래일 비율이 표본과 다르다)
        lam = sum(sum(v['duration_ms'].get(x, 0) for x in ds) / 1000 * v['memory_mb'] / 1024 * PRICE['lambda_gb_s']
                  + sum(v['invocations'].get(x, 0) for x in ds) * PRICE['lambda_req'] for v in d['lambda'].values())
        sqs = sum(sum(m.get(x, 0) for x in ds) for v in d['sqs'].values() for m in v.values()) * PRICE['sqs_req']
        api = sum(sum(v.get(x, 0) for x in ds) for v in d['apigw'].values()) * PRICE['apigw_http_req']
        return (lam + sqs + api) / len(ds)
    add('공유 기반', '실행량', 'Lambda·SQS·API Gateway', requests_cost(trading), requests_cost(other), '측정, 무료 한도 미적용')
    lake = d['s3'].get(BUCKET, {}).get('objects', {})
    ks = sorted(k for k in lake if k in valid)
    if len(ks) >= 2:
        # 일별 객체 수는 하루 한 번 찍은 값이다. 두 관측 사이에 시작한 분석 실행의 객체만 뺀다
        between = [r for r in d['sfn'].get('edge-dev-analysis-v2', []) if ks[0] <= datetime.fromtimestamp(r['start'], KST).date().isoformat() < ks[-1]]
        listing = d.get('run_objects') or {}
        unlisted = [r['name'] for r in between if r['name'] not in listing]
        elapsed = (datetime.fromisoformat(ks[-1]) - datetime.fromisoformat(ks[0])).days  # 관측 개수가 아니라 경과 일수
        if unlisted:
            notes.append(f'두 객체 수 관측 사이 분석 실행 {len(unlisted)}건의 S3 목록이 없다 — 분석 외 PUT 하한 N/A')
            add('공유 기반', '실행량', 'S3 PUT 하한(분석 외)', None, None, 'N/A 분석 실행 목록 결손')
        else:
            analysis_objects = sum(listing[r['name']]['events'] + listing[r['name']]['files'] for r in between)
            new_objects = max(0, lake[ks[-1]] - lake[ks[0]] - analysis_objects) / elapsed
            add('공유 기반', '실행량', f'S3 PUT 하한(분석 외 새 객체 {new_objects:.0f}/일)', new_objects * PRICE['s3_put_1k'] / 1000,
                new_objects * PRICE['s3_put_1k'] / 1000, f'{ks[0]}~{ks[-1]} 객체 수 증가 − 분석 산출물. 덮어쓰기·GET 미포함')
    else:
        notes.append(f'버킷 객체 수 관측 {len(ks)}개(<2) — 분석 외 PUT 하한 N/A')
        add('공유 기반', '실행량', 'S3 PUT 하한(분석 외)', None, None, 'N/A 객체 수 관측 부족')

    def known_vcpu(ds):
        svc = sum(int(s['cpu']) / 1024 * avg(s['running_minutes'], ds) / 60 for s in d['services'] if s['cpu'])
        h = collections.defaultdict(float)
        for rs in d['sfn'].values():
            for r in rs:
                for t in r['tasks']:
                    if billed_seconds(t):
                        h[datetime.fromtimestamp(r['start'], KST).date().isoformat()] += t['cpu'] / 1024 * billed_seconds(t) / 3600
        return svc + avg(h, ds)
    resid = [max(0, avg(d['fargate_vcpu_minutes'], ds) / 60 - known_vcpu(ds)) if ds else 0 for ds in (trading, other)]
    add('공유 기반', '실행량', f'SFN 밖 Fargate 잔여 ≤{resid[0]:.1f}/{resid[1]:.1f} vCPU·시간', resid[0] * fargate_hourly(1024, 2048),
        resid[1] * fargate_hourly(1024, 2048), '계정 AWS/Usage − 서비스·SFN 태스크(프로비저닝 시간 포함 상한)')

    known = [r for r in rows if r['month'] is not None]
    totals = {'group': {}, 'kind': {}}
    for r in known:
        totals['group'][r['group']] = totals['group'].get(r['group'], 0) + r['month']
        totals['kind'][r['kind']] = totals['kind'].get(r['kind'], 0) + r['month']
    totals['month'] = sum(r['month'] for r in known)
    totals['trading_day'] = sum(r['trading'] for r in known)
    totals['other_day'] = sum(r['other'] for r in known)
    totals['incomplete'] = [r['name'] for r in rows if r['month'] is None]

    # --- 시나리오(기준선에 이미 있는 공유 자원은 다시 더하지 않는다)
    kis_high = (nat_t - nat_o) * 1e9 / o['current_price_calls'] if nat_t is not None and nat_t > nat_o else None
    worker = fargate_hourly(1024, 2048) * o['session_hours'] * T
    scenarios = []
    cur_out = None if outlook_day is None else outlook_day * M
    cur_mv = None if movement_base is None else o['events_per_trading_day'] * movement_base * T
    for sc in o['scenarios']:
        n, (u_lo, u_hi), (a_lo, a_hi) = sc['N'], sc['U'], sc['accounts']
        a_mid = round((a_lo + a_hi) / 2)
        rates = o['event_rates']
        out_add = None if outlook_unit is None else (n - targets) * outlook_unit * M
        # 현재 사건분은 기준선에 있으므로 늘어난 사건 수만 확대 단가로 더한다
        mv_add = None if movement_unit is None else [(n * r - o['events_per_trading_day']) * movement_unit * T for r in rates]
        workers = [0, (a_mid - 1) * worker, (a_hi - 1) * worker]

        def nat(u, byte):
            delta = max(0, n * 390 + u * 78 - o['current_price_calls'])  # ③ ETF 매분 + 구성종목 5분 경계
            return None if byte is None else delta * byte / 1e9 * PRICE['nat_gb'] * T
        nat3 = [nat(u_lo, o['kis_bytes_low']), nat(u_lo, None if kis_high is None else (o['kis_bytes_low'] + kis_high) / 2), nat(u_hi, kis_high)]
        lines = [{'item': '전망(매일)', 'formula': f'(N−{targets}) × 대상 ETF·일 단가 × {M}', 'current': cur_out, 'add': None if out_add is None else [out_add] * 3},
                 {'item': f'가격변동 {rates[0]}/{rates[1]}/{rates[2]}건/ETF·일', 'formula': f"(N × 율 − {o['events_per_trading_day']:g}) × 건 단가 × {T}", 'current': cur_mv, 'add': mv_add},
                 {'item': f'수집 워커(계좌 {a_lo}~{a_hi}, 낮음=한 워커 다중 앱키)', 'formula': f'(계좌−1) × ${fargate_hourly(1024, 2048) * o["session_hours"]:.3f} × {T}',
                  'current': 0, 'add': workers},
                 {'item': f'KIS 호출 NAT(③, U {u_lo}~{u_hi})', 'formula': f'Δ호출 × {o["kis_bytes_low"]}~{kis_high or 0:.0f} B × $0.059 × {T}',
                  'current': None, 'add': nat3}]
        add_total = None if any(l['add'] is None or None in l['add'] for l in lines) else [sum(l['add'][i] for l in lines) for i in range(3)]
        scenarios.append({'name': sc['name'], 'N': n, 'U': sc['U'], 'accounts': sc['accounts'], 'lines': lines, 'add_total': add_total,
                          'total': None if add_total is None else [totals['month'] + x for x in add_total],
                          's3_minute_gb_per_month': [(n + u) * 84e6 / 468 / 1e9 * T for u in sc['U']]})

    # --- 조건부(사용량 근거 없음, 합계에 넣지 않음)
    rds_main = next((r for r in d['rds'] if r['id'] == 'edge-dev'), None)
    conditional = []
    if rds_main:
        gb = {x: (rds_main['net_rx_bps'].get(x, 0) + rds_main['net_tx_bps'].get(x, 0)) * 86400 / 1e9 for x in valid}
        since = o.get('transfer_since') or valid[0]
        for label, td, od in (('기간 전체', trading, other),
                              (f'{since} 이후', [x for x in trading if x >= since], [x for x in other if x >= since])):
            if not td or not od:
                conditional.append({'item': f'RDS↔클라이언트 AZ 간 전송({label})', 'month': [None, None],
                                    'basis': f'거래일 {len(td)}·휴일 {len(od)}일 — 표본 없음'})
                continue
            hi = (avg(gb, td) * T + avg(gb, od) * O) * PRICE['xaz_gb']
            conditional.append({'item': f'RDS↔클라이언트 AZ 간 전송({label}, 거래일 {len(td)}·휴일 {len(od)}일)', 'month': [0, hi],
                                'basis': f"RDS 송수신 거래일 평균 {avg(gb, td):.0f} GB(최대 {max(gb[x] for x in td):.0f}) × $0.01 × 다른 AZ 비율 0~1. {TRANSFER_RULES['rds']}"})
    conditional.append({'item': 'NAT↔다른 AZ 태스크 전송', 'month': [0, None if nat_t is None else (nat_t * T + nat_o * O) * 2 * PRICE['xaz_gb']],
                        'basis': f"NAT 처리 × $0.01~0.02 × 다른 AZ 비율 0~1. {TRANSFER_RULES['nat']}"})
    out_gb = sum(avg(m['BytesOutToDestination'], trading) * T + avg(m['BytesOutToDestination'], other) * O for m in d['nat'].values()) / 1e9
    conditional.append({'item': '인터넷 송신', 'month': [0, out_gb * PRICE['internet_out_gb']],
                        'basis': f'월 {out_gb:.0f} GB — 결제 계정 전역 무료 100 GB 안이면 0'})
    if rds_main:
        for target in ('db.t4g.medium', 'db.t4g.large'):
            diff = (PRICE['rds_h'][target] - PRICE['rds_h'][rds_main['class']]) * 24 * M
            conditional.append({'item': f"RDS {rds_main['class']}→{target}", 'month': [diff, diff], 'basis': 'CPU·연결·저장 지표로 판단'})
    for cpu, mem in ((256, 512), (512, 1024), (1024, 2048)):
        conditional.append({'item': f'PgBouncer {cpu}/{mem} 상시 1~2대', 'month': [fargate_hourly(cpu, mem, True) * 24 * M, fargate_hourly(cpu, mem) * 24 * M * 2],
                            'basis': 'ARM 1대~x86 2대, Cloud Map 대당 $0.10 별도, 용량 미검증'})
    conditional.append({'item': '수집 워커 2 vCPU 상향(대당)', 'month': [worker, worker], 'basis': '다중 앱키 부하 미검증'})
    conditional.append({'item': '가격변동 소비자 최대 4대', 'month': [0, 4 * fargate_hourly(256, 512) * o['session_hours'] * T], 'basis': '단계 확장 0~4대'})
    unestimated = ['S3 GET·덮어쓰기 재시도(요청 지표 꺼짐)', '세금·지원 플랜', '무료 한도 적용 여부(결제 계정 단위)',
                   '집중일 SFN 대기 확인(실행·분당 $0.000174 × 대기 분)', 'LLM API 요금(사용자 확인 약 $1/일, 재산정 보류)']

    return {'meta': {'region': REGION, 'price_checked': PRICE_CHECKED, 'window': d['window'], 'collected_at': d['collected_at'],
                     'days': cls, 'month_days': M, 'month_trading': T, 'outlook_targets': targets, 'outlook_attempts_per_target': retry_ratio,
                     'events_per_trading_day': o['events_per_trading_day'], 'event_rates': o['event_rates'],
                     'movement_retry_assumption': '실측 시도 그대로(재시도 가정 추가 없음)', 'kis_bytes': [o['kis_bytes_low'], kis_high],
                     'scenario_inputs': o['scenarios'], 'notes': notes},
            'batches': per_batch, 'movement': mv,
            'unit': {'outlook_per_target_day': outlook_unit, 'movement_per_event': movement_unit, 'movement_baseline_per_event': movement_base},
            'baseline': rows, 'totals': totals, 'scenarios': scenarios, 'conditional': conditional, 'unestimated': unestimated,
            'transfer_rules': TRANSFER_RULES}


def money(x):
    return 'N/A' if x is None else f'{x:,.1f}'


def day(x):
    return 'N/A' if x is None else f'{x:.3f}'


def render(p, compare=None):
    m = p['meta']
    c = m['days']
    print(f"리전 {m['region']} · 단가 확인 {m['price_checked']} · 기간 {m['window'][0][:10]}~{m['window'][1][:10]}(수집 {m['collected_at'][:16]})")
    print(f"유효 {len(c['trading']) + len(c['other'])}일(거래일 {len(c['trading'])}·휴일 {len(c['other'])}) · 수집 공백 {c['missing'] or '없음'} · 미완료 {c['partial'] or '없음'}")
    print(f"기준월 {m['month_days']}일(거래일 {m['month_trading']}) · 전망 대상 {m['outlook_targets']} · 시도/대상 {m['outlook_attempts_per_target'] and round(m['outlook_attempts_per_target'], 2)}"
          f" · 가격변동 {m['events_per_trading_day']:g}건/거래일 · 사건 율 {m['event_rates']} · 재시도 {m['movement_retry_assumption']}")
    for b in p['batches']:
        print(f"  배치 {b['name'][:36]} {b['date']}({b['matched_by']}): 시도 {b['attempts']}·대상 {b['targets']}·성공 {b['completed']}, 과금 {b['billed_s']}초, "
              f"PUT 객체 {b['s3_puts_objects']}+manifest 추정 {b['s3_puts_manifest_est']}, 기준선 ${day(b['baseline'])}·단가용 ${day(b['total'])}")
    if p['movement']:
        mv = p['movement']
        print(f"  가격변동 표본 {mv['runs']}건: 과금 합 {mv['billed_s']}초(평균 {mv['billed_s'] / mv['runs']:.0f}), PUT 객체 {mv['s3_puts_objects']}+manifest 추정 {mv['s3_puts_manifest_est']}")
    for n in m['notes']:
        print('  ⚠', n)

    print('\n### 현재 비용\n\n| 구분 | 성격 | 항목 | 거래일 $ | 휴일 $ | 월 $ | 근거 |\n|---|---|---|---:|---:|---:|---|')
    for r in p['baseline']:
        print(f"| {r['group']} | {r['kind']} | {r['name']} | {day(r['trading'])} | {day(r['other'])} | {money(r['month'])} | {r['note']} |")
    t = p['totals']
    print(f"\n합계 월 ${t['month']:.1f}(거래일 ${t['trading_day']:.2f}, 휴일 ${t['other_day']:.2f}) · 구분 "
          + ', '.join(f'{k} {v:.1f}' for k, v in t['group'].items()) + ' · 성격 ' + ', '.join(f'{k} {v:.1f}' for k, v in t['kind'].items()))
    if t['incomplete']:
        print('  ⚠ 합계에서 빠진 N/A 항목:', t['incomplete'])
    print(f"단가: 전망 대상 ETF·일 ${money(p['unit']['outlook_per_target_day'] and p['unit']['outlook_per_target_day'] * 1000)}/1000, "
          f"가격변동 건 ${money(p['unit']['movement_per_event'] and p['unit']['movement_per_event'] * 1000)}/1000")

    print('\n### 확대 추가분과 확대 후 합계(낮음/중간/높음, 월 $)\n\n| 시나리오 | 항목 | 식 | 현재 | 추가 |\n|---|---|---|---:|---|')
    for s in p['scenarios']:
        for i, l in enumerate(s['lines']):
            print(f"| {s['name'] if i == 0 else ''} | {l['item']} | {l['formula']} | {money(l['current'])} | "
                  + ('N/A' if l['add'] is None else ' / '.join(money(x) for x in l['add'])) + ' |')
        print(f"| | **추가 합** | | | **{' / '.join(money(x) for x in s['add_total']) if s['add_total'] else 'N/A'}** |")
        print(f"| | **확대 후 합계** | 현재 {t['month']:.1f} + 추가 | | **{' / '.join(money(x) for x in s['total']) if s['total'] else 'N/A'}** |")
        print(f"| | 분 자료 S3 | (N+U) × 0.18 MB × 거래일 | | 매월 +{s['s3_minute_gb_per_month'][0]:.1f}~{s['s3_minute_gb_per_month'][1]:.1f} GB 누적 |")

    print('\n### 조건부(합계에 넣지 않음, 월 $)')
    for x in p['conditional']:
        print(f"- {x['item']}: {money(x['month'][0])}~{money(x['month'][1])} — {x['basis']}")
    print('\n### 미산정\n- ' + '\n- '.join(p['unestimated']))

    if compare:
        # 항목 이름에 든 측정값은 바뀌므로 숫자를 지운 이름으로 짝짓는다
        key = lambda name: re.sub(r'\d+(\.\d+)?', '#', name.split('(')[0]).strip()
        print(f"\n### 이전 결과 대비\n- 합계 {compare['totals']['month']:.1f} → {t['month']:.1f} ({t['month'] - compare['totals']['month']:+.1f})")
        prev = {key(r['name']): (r['name'], r['month']) for r in compare['baseline']}
        for r in p['baseline']:
            name, before = prev.pop(key(r['name']), (None, None))
            if before is None or r['month'] is None or abs(r['month'] - before) >= 0.05:
                print(f"- {r['name']}: {money(before)} → {money(r['month'])}" + (f' (이전 이름 {name})' if name and name != r['name'] else ''))
        for name, before in prev.values():
            print(f'- {name}: {money(before)} → (없음)')


def plan(args):
    d = json.loads(Path(args.usage).read_text(encoding='utf-8'))
    opts = {'month_days': args.month_days, 'month_trading': args.month_trading, 'batches': args.batch or None, 'batch_hour': args.batch_hour,
            'trading_days': args.trading_days.split(',') if args.trading_days else None,
            'events_per_trading_day': args.events_per_trading_day, 'transfer_since': args.transfer_since,
            'event_rates': [float(x) for x in args.event_rates.split(',')] if args.event_rates else None}
    p = build_plan(d, opts)
    out = Path(args.usage).with_suffix('.plan.json')
    out.write_text(json.dumps(p, ensure_ascii=False, indent=1, default=str), encoding='utf-8')  # 출력이 실패해도 결과는 남긴다
    render(p, json.loads(Path(args.compare).read_text(encoding='utf-8')) if args.compare else None)
    print('\nsaved', out)


# ---------------------------------------------------------------- calc (진단)

def calc(args):
    d = json.loads(Path(args.usage).read_text(encoding='utf-8'))
    cls = classify_days(d)
    print(f"days {len(cls['days'])}, trading {cls['trading']}, missing {cls['missing']}, partial {cls['partial']}")
    print('\n== 분석 태스크 구간(초): 대기(생성→받기 시작, 미과금) / 받기 / 시작 / 실행 / 종료 처리 / 과금 / 자리 점유')
    rows = d['sfn'].get('edge-dev-analysis-v2', [])
    overhead = []
    for kind in ('outlook', 'movement'):
        xs = []
        for r in rows:
            if kind_of(r) != kind:
                continue
            acquire = next((ts for n, ts in reversed(r['states']) if n == 'Analyze'), None)
            release = next((ts for n, ts in r['states'] if n.startswith('Release') and acquire and ts >= acquire), None)
            for t in r['tasks']:
                if not (t['pull_start'] and t['stopped'] and t['created'] and t['started']):
                    continue
                end = t['exec_stopped'] or t['stopping']
                slot = (release - acquire) if acquire and release else None
                xs.append({'queue': t['pull_start'] - t['created'], 'pull': t['pull_stop'] - t['pull_start'], 'boot': t['started'] - t['pull_stop'],
                           'run': end - t['started'], 'stop': t['stopped'] - end, 'billed': billed_seconds(t), 'slot': slot})
                if slot is not None:
                    overhead.append(slot - billed_seconds(t))

        def q(k):
            v = sorted(x[k] for x in xs if x[k] is not None)
            return f'{v[len(v) // 2]:.0f}(p90 {v[int(0.9 * (len(v) - 1))]:.0f}, max {v[-1]:.0f}, n={len(v)})' if v else '-'
        print(f"  {kind}: 대기 {q('queue')} 받기 {q('pull')} 시작 {q('boot')} 실행 {q('run')} 종료 {q('stop')} | 과금 {q('billed')} | 자리 점유 {q('slot')}")
    if overhead:
        overhead.sort()
        print(f'  자리 점유 − 과금: 중앙 {overhead[len(overhead) // 2]:.1f}초, p90 {overhead[int(0.9 * (len(overhead) - 1))]:.1f}, n={len(overhead)}')
    print('\n== 서비스 CPU(거래일 평균 / 기간 최대 %)')
    for s in d['services']:
        print(f"  {s['service']}: {avg(s['cpu_avg_pct'], cls['trading']) or 0:.1f} / {max(s['cpu_max_pct'].values() or [0]):.0f}")
    print(f"\n== Fargate 계정 전체: 거래일 {avg(d['fargate_vcpu_minutes'], cls['trading']) / 60:.1f} vCPU·시간, 휴일 {avg(d['fargate_vcpu_minutes'], cls['other']) / 60:.1f}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    c = sub.add_parser('collect')
    c.add_argument('--start', required=True, help='KST 날짜(포함)')
    c.add_argument('--end', required=True, help='KST 날짜(제외)')
    c.set_defaults(func=collect)
    k = sub.add_parser('calc')
    k.add_argument('usage')
    k.set_defaults(func=calc)
    p = sub.add_parser('plan')
    p.add_argument('usage')
    p.add_argument('--month-days', type=int)
    p.add_argument('--month-trading', type=int)
    p.add_argument('--batch', action='append', help='정기 전망 배치 실행 이름(여러 번). 없으면 --batch-hour 로 찾는다')
    p.add_argument('--batch-hour', type=int)
    p.add_argument('--trading-days', help='쉼표로 나눈 KST 날짜. 없으면 1분 가격 워커 가동(60분 초과)으로 판정')
    p.add_argument('--events-per-trading-day', type=float)
    p.add_argument('--event-rates', help='쉼표로 나눈 ETF당 하루 사건 율 3개')
    p.add_argument('--transfer-since', help='RDS AZ 간 전송을 따로 볼 시작 날짜(예: v2 전환 2026-10-02)')
    p.add_argument('--compare', help='이전 .plan.json')
    p.set_defaults(func=plan)
    a = parser.parse_args()
    a.func(a)
