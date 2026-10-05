"""infra_cost.plan 의 날짜 분류·표본 처리·합계 산식을 작은 고정 입력으로 확인한다(AWS 호출 없음).

    uv run --no-project --with pytest python -m pytest test_infra_cost.py -q
"""
import copy
import math
from datetime import datetime

import pytest

import infra_cost as ic


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def task(start, billed, cpu=1024, mem=2048):
    t0 = ts(start)
    return {'cpu': cpu, 'memory': mem, 'created': t0 - 15, 'pull_start': t0, 'pull_stop': t0 + 5, 'started': t0 + 7,
            'exec_stopped': t0 + billed - 20, 'stopping': t0 + billed - 20, 'stopped': t0 + billed}


def usage():
    day = {'2026-10-01': 2e9, '2026-10-03': 1e9}
    child = lambda name, etf, at, basis='2026-09-30T21:00:00Z': {
        'name': name, 'status': 'SUCCEEDED', 'start': ts(at), 'stop': ts(at) + 400, 'transitions': 7, 'lambda_calls': 2,
        'input': f'{{"analysis_at":"{basis}","kind":"outlook","etf_code":"{etf}"}}', 'states': [], 'tasks': [task(at, 300)]}
    return {
        'window': ['2026-10-01T00:00:00+09:00', '2026-10-05T00:00:00+09:00'], 'collected_at': '2026-10-04T12:00:00+09:00',
        # 10-02 는 계정 지표가 없다(수집 공백), 10-04 는 수집 시각 뒤에 끝난다(미완료)
        'fargate_vcpu_minutes': {'2026-10-01': 600.0, '2026-10-03': 30.0},
        'services': [
            {'cluster': 'edge-dev-worker', 'service': 'edge-dev-data-pipeline-price-worker', 'cpu': '1024', 'memory': '2048', 'arch': 'X86_64',
             'running_minutes': {'2026-10-01': 516.0}, 'cpu_avg_pct': {}, 'cpu_max_pct': {}},
            {'cluster': 'edge-dev-service', 'service': 'api', 'cpu': '256', 'memory': '512', 'arch': 'X86_64',
             'running_minutes': {'2026-10-01': 1440.0, '2026-10-03': 1440.0}, 'cpu_avg_pct': {}, 'cpu_max_pct': {}}],
        'rds': [{'id': 'edge-dev', 'class': 'db.t4g.small', 'storage_gb': 20, 'credits_charged': {},
                 'net_rx_bps': {}, 'net_tx_bps': {'2026-10-01': 1e9 / 86400, '2026-10-03': 0.0}}],
        'ec2': [], 'ebs': [], 'public_ipv4': [{'type': 'nat_gateway', 'desc': 'NAT'}], 'alb': {},
        'ecr_gb': {'r': 10.0}, 'ecr_unique_gb': {'r': 5.0},
        's3': {ic.BUCKET: {'gb': {'2026-10-03': 1e9}, 'objects': {'2026-10-01': 100.0, '2026-10-03': 400.0}}},
        'counts': {'secrets': 1, 'alarms': 0, 'cloudmap_instances': 0, 'route53_zones': 1},
        # NAT 는 트래픽이 없어도 날마다 점을 남긴다(0 값)
        'nat': {'n': {'BytesInFromSource': day, 'BytesInFromDestination': {'2026-10-01': 0.0, '2026-10-03': 0.0},
                      'BytesOutToDestination': day, 'BytesOutToSource': {}}},
        'logs': {'stored_gb': 0.0, 'ingest_bytes': {}}, 'lambda': {}, 'sqs': {}, 'apigw': {},
        'sfn': {'edge-dev-analysis-v2-outlook-batch': [{'name': 'sched', 'status': 'SUCCEEDED', 'start': ts('2026-10-01T06:00:05+09:00'),
                                                         'stop': ts('2026-10-01T07:00:00+09:00'), 'transitions': 20, 'lambda_calls': 0,
                                                         'input': '{"analysis_at":"2026-09-30T21:00:00Z"}', 'states': [], 'tasks': []},
                                                        {'name': 'manual', 'status': 'SUCCEEDED', 'start': ts('2026-10-01T11:00:00+09:00'),
                                                         'stop': ts('2026-10-01T11:30:00+09:00'), 'transitions': 9, 'lambda_calls': 0,
                                                         'input': '{}', 'states': [], 'tasks': []}],
                'edge-dev-analysis-v2': [child('a1', 'AAA', '2026-10-01T06:00:10+09:00'), child('a2', 'BBB', '2026-10-01T06:01:00+09:00'),
                                         # 수동 배치의 자식은 정기 배치 표본에 들어가면 안 된다
                                         child('m1', 'AAA', '2026-10-01T11:00:10+09:00', '2026-10-01T02:00:00Z')]},
        'run_objects': {'a1': {'events': 100, 'files': 10, 'manifest': 1}, 'a2': {'events': 100, 'files': 10, 'manifest': 1},
                        'm1': {'events': 100, 'files': 10, 'manifest': 1}},
    }


def with_movement(d):
    d = copy.deepcopy(d)
    d['sfn']['edge-dev-analysis-v2'].append({'name': 'v1', 'status': 'SUCCEEDED', 'start': ts('2026-10-01T10:00:00+09:00'), 'stop': None,
                                             'transitions': 7, 'lambda_calls': 2, 'input': '{"kind":"movement","etf_code":"AAA"}',
                                             'states': [], 'tasks': [task('2026-10-01T10:00:00+09:00', 120)]})
    d['run_objects']['v1'] = {'events': 50, 'files': 10, 'manifest': 1}
    return d


def test_days_separate_gap_and_partial_from_no_run():
    days = ic.classify_days(usage())
    assert days['trading'] == ['2026-10-01']
    assert days['other'] == ['2026-10-03']
    assert days['missing'] == ['2026-10-02']   # 계정 지표가 없는 날은 '실행 없음 0'으로 평균에 넣지 않는다
    assert days['partial'] == ['2026-10-04']


def test_billing_has_one_minute_minimum_and_rounds_up():
    assert ic.billed_seconds({'pull_start': 0.0, 'stopped': 30.2}) == 60
    assert ic.billed_seconds({'pull_start': 0.0, 'stopped': 120.2}) == 121


def test_only_scheduled_batch_children_form_the_outlook_sample():
    p = ic.build_plan(usage(), {})
    assert [b['name'] for b in p['batches']] == ['sched']
    b = p['batches'][0]
    assert (b['attempts'], b['targets'], b['billed_s']) == (2, 2, 600)
    # PUT = 객체(이벤트+파일) 실측 + manifest 추정(시작·종료 2 + 실행 273초/5 올림 55) 실행마다
    assert b['s3_puts_objects'] == 220 and b['s3_puts_manifest_est'] == 2 * (2 + math.ceil(273 / 5))
    assert p['meta']['outlook_targets'] == 2


def test_missing_sample_is_na_not_zero():
    p = ic.build_plan(usage(), {})
    mv_row = next(r for r in p['baseline'] if r['name'].startswith('가격변동'))
    assert mv_row['trading'] is None and mv_row['month'] is None
    assert mv_row['name'] in p['totals']['incomplete']
    assert all(s['add_total'] is None for s in p['scenarios'])

    d = usage()
    del d['run_objects']
    assert ic.build_plan(d, {})['unit']['outlook_per_target_day'] is None   # S3 목록이 없으면 0 이 아니라 모름


def test_totals_and_scenario_additions_follow_the_stated_formulas():
    p = ic.build_plan(with_movement(usage()), {'month_days': 30, 'month_trading': 20})
    known = [r for r in p['baseline'] if r['month'] is not None]
    assert p['totals']['month'] == pytest.approx(sum(r['month'] for r in known))
    for r in known:
        assert r['month'] == pytest.approx(r['trading'] * 20 + r['other'] * 10)
    s = p['scenarios'][0]
    unit = p['unit']['outlook_per_target_day']
    assert s['lines'][0]['add'][0] == pytest.approx((s['N'] - 2) * unit * 30)
    mv = p['unit']['movement_per_event']
    # 현재 사건분은 기준선(NAT·Lambda 제외 단가)에 있으므로 늘어난 사건만 확대 단가로 더한다
    assert s['lines'][1]['add'][1] == pytest.approx((s['N'] * 0.8 - 32) * mv * 20)
    for i in range(3):
        assert s['add_total'][i] == pytest.approx(sum(l['add'][i] for l in s['lines']))
        assert s['total'][i] == pytest.approx(p['totals']['month'] + s['add_total'][i])


def test_empty_day_class_fails_loudly():
    with pytest.raises(ValueError):
        ic.build_plan(usage(), {'trading_days': ['2026-10-01', '2026-10-03']})


def test_overlapping_manual_run_with_other_basis_is_not_a_batch_child():
    d = usage()
    d['sfn']['edge-dev-analysis-v2'][2]['start'] = ts('2026-10-01T06:30:00+09:00')  # 정기 배치 창 안에서 시작한 수동 실행
    b = ic.build_plan(d, {})['batches'][0]
    assert (b['attempts'], b['matched_by']) == (2, 'analysis_at')


def test_analysis_nat_and_lambda_are_not_added_twice_to_the_baseline():
    p = ic.build_plan(with_movement(usage()), {})
    b = p['batches'][0]
    outlook = next(r for r in p['baseline'] if r['name'].startswith('전망'))
    # 계정 실측 NAT 처리·Lambda 행이 이미 이 실행의 전송·호출을 센다 — 기준선은 Fargate·S3·SFN 만
    assert outlook['trading'] == pytest.approx(b['fargate'] + b['s3'] + b['sfn'])
    assert p['unit']['outlook_per_target_day'] * 2 == pytest.approx(b['fargate'] + b['s3'] + b['sfn'] + b['nat'] + b['lambda'])


def test_unreadable_task_timing_is_unknown_not_free():
    d = with_movement(usage())
    d['sfn']['edge-dev-analysis-v2'][-1]['tasks'][0]['stopped'] = None  # 기록은 있는데 종료 시각을 모름
    assert ic.build_plan(d, {})['unit']['movement_per_event'] is None
    d['sfn']['edge-dev-analysis-v2'][-1]['tasks'][0].update(pull_start=None, stopped=ts('2026-10-01T10:00:30+09:00'))
    assert ic.build_plan(d, {})['unit']['movement_per_event'] is not None  # 이미지 받기 전에 끝난 태스크는 과금이 없다(0)


def test_object_growth_is_per_elapsed_day_and_unlisted_runs_make_it_na():
    d = usage()
    d['s3'][ic.BUCKET]['objects'] = {'2026-10-01': 100.0, '2026-10-03': 1100.0}
    row = next(r for r in ic.build_plan(d, {})['baseline'] if r['name'].startswith('S3 PUT'))
    assert '335/일' in row['name']   # (1,000 − 분석 객체 330) / 경과 2일. 관측 개수(1)로 나누면 670
    del d['run_objects']['m1']
    row = next(r for r in ic.build_plan(d, {})['baseline'] if r['name'].startswith('S3 PUT'))
    assert row['month'] is None


def test_missing_nat_metrics_are_na_and_render_survives_na():
    d = usage()
    d['nat']['n'] = {k: {} for k in d['nat']['n']}
    p = ic.build_plan(d, {})
    assert next(r for r in p['baseline'] if r['name'].startswith('NAT 처리'))['month'] is None
    del d['run_objects']
    ic.render(ic.build_plan(d, {}))   # N/A 가 섞여도 출력이 죽지 않는다


def test_partial_gaps_are_na_too():
    d = usage()
    del d['nat']['n']['BytesInFromSource']['2026-10-03']        # 휴일 하루만 점이 없다
    assert next(r for r in ic.build_plan(d, {})['baseline'] if r['name'].startswith('NAT 처리'))['month'] is None
    d = usage()
    d['s3'][ic.BUCKET]['objects'] = {'2026-10-03': 400.0}       # 관측 1개로는 증가를 못 낸다 — 행이 사라지지 않고 N/A
    assert next(r for r in ic.build_plan(d, {})['baseline'] if r['name'].startswith('S3 PUT'))['month'] is None
    d = usage()
    d['sfn']['edge-dev-data-pipeline'] = [{'name': 'eod', 'status': 'SUCCEEDED', 'start': ts('2026-10-01T15:40:00+09:00'), 'stop': None,
                                           'transitions': 5, 'lambda_calls': 0, 'input': '{}', 'states': [],
                                           'tasks': [{'parsed': False, 'cpu': 0, 'memory': 0, 'pull_start': None, 'stopped': None}]}]
    p = ic.build_plan(d, {})
    assert next(r for r in p['baseline'] if r['name'] == '배치 태스크 pipeline')['month'] is None


def test_unfinished_batch_is_left_out_of_the_sample():
    d = usage()
    d['sfn']['edge-dev-analysis-v2-outlook-batch'][0].update(status='RUNNING', stop=None)
    p = ic.build_plan(d, {})
    assert p['batches'] == [] and p['unit']['outlook_per_target_day'] is None
    assert any('RUNNING' in n for n in p['meta']['notes'])
