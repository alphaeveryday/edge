"""Redis Cluster 부분 장애 실험. 이 디렉터리에서: python3 run-cluster.py C3
C1 기본값(60s·버퍼링·서킷 off) / C2 타임아웃(500ms·REJECT·서킷 off) / C3 전역 서킷 / C4 샤드 서킷(코드 필요)
env: HOT=failed|healthy(인기 종목 위치, 기본 failed) FAIL_SHARD=0 KILL_REPLICA=true RATE READ_RATE
     FAULT=kill|pause|partition (SIGKILL / docker pause=SIGSTOP=hang / 네트워크 분리=분단)
     REDIS_CLUSTER_REQUIRE_FULL_COVERAGE=yes|no REDIS_TOPOLOGY_REFRESH=false|true
"""
from datetime import datetime
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

scenario = sys.argv[1] if len(sys.argv) > 1 else 'C3'
if scenario not in {'C1', 'C2', 'C3', 'C4'}:
    raise SystemExit('Choose C1..C4')
hot = os.environ.get('HOT', 'failed')
fail_shard = int(os.environ.get('FAIL_SHARD', '0'))
kill_replica = os.environ.get('KILL_REPLICA', 'true') == 'true'
fault_kind = os.environ.get('FAULT', 'kill')
if fault_kind not in {'kill', 'pause', 'partition'}:
    raise SystemExit('FAULT must be kill|pause|partition')
inject_at = int(os.environ.get('INJECT_AT', '60'))
root = Path(__file__).resolve().parent
out = root / 'runs' / (f'{scenario}-{hot}-' + ('' if fault_kind == 'kill' else fault_kind + '-') + ('' if kill_replica else 'replica-')
                       + ('refresh-' if os.environ.get('REDIS_TOPOLOGY_REFRESH') == 'true' else '') + time.strftime('%Y%m%d-%H%M%S'))
out.mkdir(parents=True)
# 코드에서 서킷을 빼는 대신 실패율 임계치 100% env 로의 서킷 무력화
# 67% 이하 부분 장애에서 절대 열리지 않는 임계치
# environment 에 나열한 변수만 넘기는 compose 때문에 새 변수의 cluster compose 파일 추가 필요
# 기본값 yes 의 full-coverage 는 슬롯 하나만 비어도 전 노드 CLUSTERDOWN 응답
# 부분 장애의 전체 장애 확산 방지를 위한 실험 기본값 no
env = dict(os.environ,
           REDIS_CLUSTER_REQUIRE_FULL_COVERAGE=os.environ.get('REDIS_CLUSTER_REQUIRE_FULL_COVERAGE', 'no'),
           VOTE_REDIS_COMMAND_TIMEOUT='60s' if scenario == 'C1' else '500ms',
           VOTE_REDIS_DISCONNECTED_BEHAVIOR='DEFAULT' if scenario == 'C1' else 'REJECT_COMMANDS',
           VOTE_REDIS_TIMEOUT_OPTIONS='false' if scenario == 'C1' else 'true',
           VOTE_CIRCUIT_FAILURE_RATE='100' if scenario in ('C1', 'C2') else '50',
           VOTE_CIRCUIT_SCOPE='shard' if scenario == 'C4' else 'global')
project = os.environ.get('EXPERIMENT_PROJECT', 'etf-' + scenario.lower() + '-' + str(int(time.time())))
compose = ['docker', 'compose', '-p', project, '-f', str(root.parent / 'docker-compose.cluster.yaml')]
def dc(*args):
    return subprocess.check_output(compose + list(args), env=env, text=True)
def get(path):
    with urllib.request.urlopen('http://localhost:8080' + path, timeout=6) as r:
        return r.read().decode()
def post(path, headers):
    req = urllib.request.Request('http://localhost:8080' + path, data=b'', headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=6) as r:
        return r.read().decode()
def parse_time(stamp):
    stamp = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], stamp).replace('Z', '+00:00')
    return datetime.fromisoformat(stamp)
def sql(query):
    return dc('exec', '-T', 'postgres', 'psql', '-U', 'app', '-d', 'app', '-At', '-F', '\t', '-c', query)
(out / 'project.txt').write_text(project)
(out / 'env.json').write_text(json.dumps({k: v for k, v in env.items() if k.startswith(('VOTE_', 'REDIS_TOPOLOGY', 'REDIS_', 'HOT', 'FAIL_', 'KILL_', 'RATE', 'READ_RATE'))}, indent=2))
dc('up', '-d', '--no-build' if os.environ.get('EXPERIMENT_NO_BUILD') else '--build')
for _ in range(180):
    try:
        if json.loads(get('/actuator/health'))['status'] == 'UP': break
    except Exception: pass
    time.sleep(1)
else: raise SystemExit('App did not become ready')

# 서비스별 myself 줄에서 읽은 슬롯 시작 오름차순의 샤드 번호
# 페일오버로 노드가 바뀌어도 유지되는 슬롯 범위 기준
nodes = {}
for n in range(1, 7):
    line = next(l for l in dc('exec', '-T', f'redis-{n}', 'redis-cli', 'cluster', 'nodes').splitlines() if 'myself' in l)
    f = line.split()
    nodes[f'redis-{n}'] = {'id': f[0], 'role': 'master' if 'master' in f[2] else 'replica', 'master': f[3], 'slots': f[8:]}
masters = sorted((s for s, v in nodes.items() if v['role'] == 'master'), key=lambda s: int(nodes[s]['slots'][0].split('-')[0]))
shards = [{'index': i, 'master': m, 'slots': nodes[m]['slots'][0],
           'replica': next(s for s, v in nodes.items() if v['master'] == nodes[m]['id'])} for i, m in enumerate(masters)]
slot_ranges = ','.join(s['slots'] for s in shards)
hot_shard = fail_shard if hot == 'failed' else (fail_shard + 1) % len(shards)
(out / 'topology.json').write_text(json.dumps({'shards': shards, 'fail_shard': fail_shard, 'hot_shard': hot_shard, 'kill_replica': kill_replica}, indent=2))

base = str(int(time.time())) + '000'
env.update(FORECAST_BASE=base, HOT_SHARD=str(hot_shard), SLOT_RANGES=slot_ranges, SUMMARY_PATH=str(out / 'summary.json'))
metric_names = ['tomcat.threads.busy', 'hikaricp.connections.active', 'hikaricp.connections.pending',
                'vote.redis.write.failures', 'vote.redis.read.failures']
samples = []
start = time.monotonic()
with (out / 'k6.log').open('w') as log:
    load = subprocess.Popen(['k6', 'run', '--out', 'json=' + str(out / 'samples.json'), str(root / 'cluster.js')], env=env, stdout=log, stderr=log)
    injected = False
    while load.poll() is None:
        elapsed = time.monotonic() - start
        if elapsed >= inject_at and not injected:
            victims = [shards[fail_shard]['master']] + ([shards[fail_shard]['replica']] if kill_replica else [])
            (out / 'fault.json').write_text(json.dumps({'epoch': time.time(), 'elapsed': elapsed, 'scenario': scenario, 'victims': victims, 'fault': fault_kind}))
            if fault_kind == 'kill': dc('kill', '-s', 'SIGKILL', *victims)
            elif fault_kind == 'pause': dc('pause', *victims)
            else:
                # 다른 노드가 아는 주소와 맞추기 위한 분리 전 IP 로의 재연결
                victim_ips = {}
                for v in victims:
                    cid = dc('ps', '-q', v).strip()
                    ip = subprocess.check_output(['docker', 'inspect', '-f', '{{(index .NetworkSettings.Networks "' + project + '_default").IPAddress}}', cid], text=True).strip()
                    victim_ips[v] = (cid, ip)
                    subprocess.check_call(['docker', 'network', 'disconnect', project + '_default', cid])
            injected = True
        row = {'elapsed': round(elapsed, 1)}
        for name in metric_names:
            try: row[name] = json.loads(get('/actuator/metrics/' + name))['measurements'][0]['value']
            except Exception: row[name] = None
        samples.append(row)
        time.sleep(1)
(out / 'metrics.json').write_text(json.dumps(samples, indent=2))
load_end = time.time()

# 노드 복구와 cluster ok 대기 후 재조정 요청, 30종목의 DB 와 Redis 일치 확인
survivor = shards[(fail_shard + 1) % len(shards)]['master']
def cli(*args):
    return dc('exec', '-T', survivor, 'redis-cli', '-c', '--raw', *args)
# 안 멈춘 컨테이너의 unpause 를 docker 가 거부하므로 주입한 victims 만 복구 대상
if not injected: pass
elif fault_kind == 'kill': dc('start', *victims)
elif fault_kind == 'pause': dc('unpause', *victims)
else:
    for v, (cid, ip) in victim_ips.items():
        subprocess.check_call(['docker', 'network', 'connect', '--ip', ip, project + '_default', cid])
for _ in range(120):
    if 'cluster_state:ok' in cli('cluster', 'info'): break
    time.sleep(1)
(out / 'cluster-after.txt').write_text(cli('cluster', 'nodes'))
forecasts = json.loads((out / 'summary.json').read_text())['forecasts']['shards']
forecast_ids = [str(i) for s in forecasts for i in s]
db = {}
for line in sql("select etf_code,choice,count(*) from vote where etf_code in (" + ','.join("'" + i + "'" for i in forecast_ids) + ") group by etf_code,choice;").splitlines():
    fid, choice, cnt = line.split('\t'); db.setdefault(fid, {})[choice.lower()] = int(cnt)
# C1 에서 부하 종료 후에도 60s 타임아웃까지 스레드를 잡는 버퍼링 명령
# 조회 실패의 불일치 간주와 재시도
# count 만 같고 해시가 비면 다음 재투표의 오차감이 생기므로 choices 해시와 DB 사용자 수의 일치 확인
db_users = {}
for line in sql("select etf_code,count(*) from vote where etf_code in (" + ','.join("'" + i + "'" for i in forecast_ids) + ") group by etf_code;").splitlines():
    fid, cnt = line.split('\t'); db_users[fid] = int(cnt)
def consistent():
    bad = []
    for fid in forecast_ids:
        try:
            r = json.loads(get(f'/api/v1/forecasts/{fid}/votes/count'))['result']
            if r['source'] != 'redis' or any(r[c + 's'] != db.get(fid, {}).get(c, 0) for c in ('buy', 'wait', 'sell')): bad.append(fid); continue
            if int(cli('hlen', 'vote:{' + fid + '}:choices').strip() or 0) != db_users.get(fid, 0): bad.append(fid)
        except Exception: bad.append(fid)
    return bad
for _ in range(90):
    try:
        post('/api/v1/admin/votes/reconcile', {'X-Admin-Token': env.get('VOTE_ADMIN_TOKEN', 'local-experiment')}); break
    except Exception: time.sleep(2)
for _ in range(120):
    mismatch = consistent()
    if not mismatch: break
    time.sleep(2)
duplicates = sql('select etf_code,member_id,count(*) from vote group by etf_code,member_id having count(*)>1;').strip()
(out / 'app.log').write_text(dc('logs', '--timestamps', 'app'))
(out / 'redis.log').write_text(dc('logs', '--timestamps', *[f'redis-{n}' for n in range(1, 7)]))

# 장애 주입 기준 before, during(+20s), after 구간의 샤드 등급별 SLO 초과와 폴백 집계
fault = json.loads((out / 'fault.json').read_text())['epoch'] if (out / 'fault.json').exists() else None
def phase(ts):
    if fault is None: return 'before'
    d = ts.timestamp() - fault
    return 'before' if d < 0 else 'during' if d < 20 else 'after'
agg = {}
read_db = {}
with (out / 'samples.json').open() as fh:
    for line in fh:
        p = json.loads(line)
        if p['type'] != 'Point': continue
        m, d = p['metric'], p['data']; t = d.get('tags', {})
        ph = phase(parse_time(d['time']))
        if m in ('vote_latency', 'read_latency', 'unrelated_latency'):
            cls = 'unrelated' if m == 'unrelated_latency' else ('failed' if int(t['shard']) == fail_shard else 'healthy')
            key = (m.split('_')[0], ph, cls)
            a = agg.setdefault(key, {'n': 0, 'slo': 0, 'err': 0, 'db': 0, 'lat': []})
            a['n'] += 1; a['slo'] += d['value'] > 1000; a['err'] += t.get('status') != '200'; a['lat'].append(d['value'])
        if m == 'read_source':
            key = ('read', ph, 'failed' if int(t['shard']) == fail_shard else 'healthy')
            agg.setdefault(key, {'n': 0, 'slo': 0, 'err': 0, 'db': 0, 'lat': []})['db'] += t.get('source') == 'db'
            if t.get('source') == 'db':
                sec = math.floor(parse_time(d['time']).timestamp() - (fault or 0))
                read_db[sec] = read_db.get(sec, 0) + 1
rows = []
for (kind, ph, cls), a in sorted(agg.items()):
    if a['n'] == 0: continue  # 경계 시각 사이 버킷의 지연 표본 부재로 인한 분모 0 생략
    lat = sorted(a['lat']); p99 = lat[min(len(lat) - 1, int(len(lat) * 0.99))] if lat else None
    rows.append({'kind': kind, 'phase': ph, 'class': cls, 'n': a['n'], 'slo_exceed': round(a['slo'] / a['n'], 4), 'error': round(a['err'] / a['n'], 4),
                 'db_fallback': round(a['db'] / a['n'], 4) if kind == 'read' else None, 'p99_ms': round(p99, 1) if p99 is not None else None})
# 주입 시각부터 replica 승격 로그 시각까지의 페일오버 소요
# 장애 샤드 읽기의 source 가 다시 redis 로 돌아온 첫 시각 기준의 앱 회복
failover = None
for line in (out / 'redis.log').read_text().splitlines():
    m = re.search(r'\|\s+(\S+Z) .*Failover election won', line)
    if m and fault:
        failover = round(parse_time(m.group(1)).timestamp() - fault, 2); break
app_recover = None
with (out / 'samples.json').open() as fh:
    for line in fh:
        p = json.loads(line)
        if p['type'] == 'Point' and p['metric'] == 'read_source' and fault:
            t = p['data']['tags']
            ts = parse_time(p['data']['time']).timestamp()
            # 승격 전이나 주입 명령 완료 전 성공 응답의 회복 집계 제외
            if int(t['shard']) == fail_shard and t.get('source') == 'redis' and ts > fault + max(1, failover or 0):
                app_recover = round(ts - fault, 2); break
busy = [r['tomcat.threads.busy'] for r in samples if r['tomcat.threads.busy'] is not None]
pending = [r['hikaricp.connections.pending'] for r in samples if r['hikaricp.connections.pending'] is not None]
during_db = [v for s, v in read_db.items() if 0 <= s < 20]
# 열린 서킷의 호출 거부 예외를 서킷 개방 증거로 사용
# 주입 시각부터 첫 발생 시각까지의 감지 시간
# 종료 후 러너의 검산 호출 배제를 위한 주입부터 k6 종료까지의 집계 구간
app_log = (out / 'app.log').read_text()
stamps = [parse_time(m.group(1)).timestamp() for m in re.finditer(r'^\S+\s+\|\s+(\S+Z) io\.github\.resilience4j\.circuitbreaker\.CallNotPermittedException', app_log, re.M)]
stamps = [t for t in stamps if fault and fault <= t <= load_end]
not_permitted = len(stamps)
detect = round(stamps[0] - fault, 2) if stamps else None
result = {'scenario': scenario, 'hot': hot, 'fault': fault_kind, 'fail_shard': fail_shard, 'kill_replica': kill_replica, 'rows': rows,
          'tomcat_busy_max': max(busy, default=None), 'hikari_pending_max': max(pending, default=None),
          'db_fallback_qps_during': round(sum(during_db) / 20, 1), 'db_fallback_qps_peak': max(read_db.values(), default=0),
          'circuit_open_calls': not_permitted, 'circuit_detect_s': detect, 'clusterdown': app_log.count('CLUSTERDOWN'),
          'circuit_threshold': env['VOTE_CIRCUIT_FAILURE_RATE'], 'failover_s': failover, 'app_recover_s': app_recover,
          'topology_refresh': os.environ.get('REDIS_TOPOLOGY_REFRESH', 'false'),
          'reconcile_mismatch': mismatch, 'duplicates': bool(duplicates), 'k6_exit': load.returncode}
(out / 'result.json').write_text(json.dumps(result, indent=2))
print(f"{scenario} hot={hot} fault={fault_kind} fail_shard={fail_shard} busy_max={result['tomcat_busy_max']} pending_max={result['hikari_pending_max']} "
      f"db_fallback_qps={result['db_fallback_qps_during']} circuit_open={not_permitted} detect={detect}s failover={failover}s app_recover={app_recover}s clusterdown={result['clusterdown']} mismatch={len(mismatch)}")
print(f"{'kind':10}{'phase':8}{'class':10}{'n':>7}{'slo>1s':>8}{'err':>8}{'db':>8}{'p99ms':>9}")
for r in rows:
    db_col = f"{r['db_fallback']:>8.1%}" if r['db_fallback'] is not None else f"{'':>8}"
    print(f"{r['kind']:10}{r['phase']:8}{r['class']:10}{r['n']:>7}{r['slo_exceed']:>8.1%}{r['error']:>8.1%}{db_col}{r['p99_ms']:>9}")
print('Results:', out)
print('Cleanup after review:', ' '.join(compose + ['down', '-v']))
# 장애 미주입 런의 실험 제외
# 측정 대상인 HTTP 실패율의 게이트 미사용
if fault is None:
    print('FAULT WAS NEVER INJECTED — not a failure-injection run'); raise SystemExit(2)
raise SystemExit(load.returncode or (0 if not mismatch and not duplicates else 1))
