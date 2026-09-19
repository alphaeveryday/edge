"""Local isolated compose experiment. Run from this directory: python3 run-failover.py S2.
Creates an isolated project; preserves containers and data for inspection.
"""
from datetime import datetime
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

scenario = sys.argv[1] if len(sys.argv) > 1 else 'S2'
if scenario not in {'S1', 'S2', 'S3', 'S4', 'S5'}:
    raise SystemExit('Choose S1..S5')
root = Path(__file__).resolve().parent
out = root / 'runs' / (scenario + '-' + time.strftime('%Y%m%d-%H%M%S'))
out.mkdir(parents=True)
env = dict(os.environ, VOTE_REDIS_COMMAND_TIMEOUT='5000ms' if scenario == 'S1' else '500ms',
           VOTE_REDIS_DISCONNECTED_BEHAVIOR='DEFAULT' if scenario == 'S1' else 'REJECT_COMMANDS',
           VOTE_REDIS_TIMEOUT_OPTIONS='false' if scenario == 'S1' else 'true',
           VOTE_REDIS_RETRY='true' if scenario == 'S3' else 'false',
           VOTE_REDIS_READ_FROM='REPLICA_PREFERRED' if scenario == 'S5' else 'MASTER')
user_pool = int(os.environ.get('USER_POOL', '0'))
if user_pool and user_pool < 500:
    # 50rps 에서 같은 사용자의 요청 간격 = pool/50 초. 겹치면 ack 도착 순서가 커밋 순서와 달라져 사용자별 대조가 무효다.
    raise SystemExit('USER_POOL must be >= 500 (>= 10s between a user\'s requests at 50rps) or unset')
project = os.environ.get('EXPERIMENT_PROJECT', 'etf-' + scenario.lower() + '-' + str(int(time.time())))
mode = os.environ.get('VOTE_MODE', 'db-first')
compose = ['docker', 'compose', '-p', project, '-f', str(root.parent / 'docker-compose.yaml')]
if mode == 'write-behind':
    compose += ['-f', str(root.parent / 'docker-compose.write-behind.yaml')]
def dc(*args):
    return subprocess.check_output(compose + list(args), env=env, text=True)
def get(path):
    with urllib.request.urlopen('http://localhost:8080' + path, timeout=6) as r:
        return r.read().decode()
def parse_time(stamp):
    # k6 는 RFC3339 로 소수초를 가변 자릿수(최대 9)로 찍는다 — 3.11 미만 fromisoformat 은 6자리·Z 미지원.
    stamp = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], stamp).replace('Z', '+00:00')
    return datetime.fromisoformat(stamp)
def sql(query):
    return dc('exec', '-T', 'mysql', 'mysql', '-uapp', '-papp', '-Dapp', '-N', '-e', query)
(out / 'project.txt').write_text(project)
dc('up', '-d', '--no-build' if os.environ.get('EXPERIMENT_NO_BUILD') else '--build')
for _ in range(180):
    try:
        if json.loads(get('/actuator/health'))['status'] == 'UP': break
    except Exception: pass
    time.sleep(1)
else: raise SystemExit('App did not become ready')
master = dc('ps', '-q', 'redis-master').strip()
etf = str(int(time.time()))
env.update(ETF_ID=etf, SCENARIO=scenario, SUMMARY_PATH=str(out / 'summary.json'))
threads = []
start = time.monotonic()
with (out / 'k6.log').open('w') as log:
    load = subprocess.Popen(['k6', 'run', '--out', 'json=' + str(out / 'samples.json'), str(root / 'failover.js')], env=env, stdout=log, stderr=log)
    injected = False
    while load.poll() is None:
        elapsed = time.monotonic() - start
        if elapsed >= 60 and not injected:
            (out / 'fault.json').write_text(json.dumps({'epoch':time.time(), 'elapsed':elapsed, 'scenario':scenario}))
            if scenario == 'S4':
                subprocess.check_call(['docker', 'network', 'disconnect', project + '_default', master])
            else: dc('kill', '-s', 'SIGKILL', 'redis-master')
            injected = True
        try: threads.append({'elapsed':elapsed, 'metric':json.loads(get('/actuator/metrics/tomcat.threads.busy'))})
        except Exception as e: threads.append({'elapsed':elapsed, 'error':str(e)})
        time.sleep(1)
(out / 'threads.json').write_text(json.dumps(threads, indent=2))
def master_cli(*args):
    address = dc('exec', '-T', 'sentinel-1', 'redis-cli', '-p', '26379', '--raw', 'SENTINEL', 'get-master-addr-by-name', 'mymaster').splitlines()
    return dc('exec', '-T', 'sentinel-1', 'redis-cli', '-h', address[0], '-p', address[1], '--raw', *args)
if mode == 'write-behind':
    # DB snapshot 은 flush 가 dirty 를 비운 뒤에 떠야 한다 — 미flush 분은 지연이지 유실이 아니다.
    for _ in range(60):
        if master_cli('SCARD', 'vote:dirty-forecasts').strip() == '0': break
        time.sleep(1)
    (out / 'dirty-after-load.txt').write_text(master_cli('SCARD', 'vote:dirty-forecasts'))
drain_complete = mode != 'write-behind' or master_cli('SCARD', 'vote:dirty-forecasts').strip() == '0'
(out / 'before-reconcile.json').write_text(get('/api/v1/forecasts/' + etf + '/votes/count'))
(out / 'db.tsv').write_text(sql("select choice,count(*) from forecast_vote where forecast_id='" + etf + "' group by choice;"))
(out / 'duplicates.tsv').write_text(sql('select forecast_id,user_id,count(*) from forecast_vote group by forecast_id,user_id having count(*)>1;'))
# Observe automatic reconciliation for up to five minutes after the load.
expected = dict(line.split('\t') for line in (out / 'db.tsv').read_text().splitlines())
for _ in range(300):
    try:
        result = json.loads(get('/api/v1/forecasts/' + etf + '/votes/count'))['result']
        if result['source'] == 'redis' and all(result[c.lower()] == int(expected.get(c, 0)) for c in ('BUY', 'HOLD', 'SELL')): break
    except Exception: pass
    time.sleep(1)
(out / 'after-reconcile.json').write_text(get('/api/v1/forecasts/' + etf + '/votes/count'))
(out / 'master-voted.txt').write_text(master_cli('HLEN', 'vote:{' + etf + '}:choices'))
# 사용자별 최종 choice 3방향 대조: k6 ack(200 의 마지막 choice) vs DB vs Redis choices 해시.
acked = {}
with (out / 'samples.json').open() as samples:
    for line in samples:
        point = json.loads(line)
        if point.get('metric') == 'vote_status' and point['type'] == 'Point' and point['data']['tags'].get('status') == '200':
            user, stamp = point['data']['tags']['user'], parse_time(point['data']['time'])
            if user not in acked or stamp > acked[user][0]:
                acked[user] = (stamp, point['data']['tags']['choice'])
acked = {user: choice for user, (_, choice) in acked.items()}
db_choices = dict(line.split('\t') for line in sql("select user_id,choice from forecast_vote where forecast_id='" + etf + "';").splitlines())
redis_pairs = master_cli('HGETALL', 'vote:{' + etf + '}:choices').split()
redis_choices = dict(zip(redis_pairs[::2], redis_pairs[1::2]))
per_user = {'acked': len(acked), 'db': len(db_choices), 'redis': len(redis_choices),
            'ack_db_mismatch': sorted(u for u, c in acked.items() if db_choices.get(u) != c),
            'ack_redis_mismatch': sorted(u for u, c in acked.items() if redis_choices.get(u) != c),
            'db_redis_mismatch': sorted(u for u in set(db_choices) | set(redis_choices) if db_choices.get(u) != redis_choices.get(u))}
(out / 'per-user.json').write_text(json.dumps(per_user, indent=2))
(out / 'sentinel.log').write_text(dc('logs', '--timestamps', 'sentinel-1', 'sentinel-2', 'sentinel-3'))
(out / 'app.log').write_text(dc('logs', '--timestamps', 'app'))
final = json.loads((out / 'after-reconcile.json').read_text())['result']
correct = (final['source'] == 'redis'
           and all(final[c.lower()] == int(expected.get(c, 0)) for c in ('BUY', 'HOLD', 'SELL'))
           and int((out / 'master-voted.txt').read_text()) == sum(map(int, expected.values()))
           and not (out / 'duplicates.tsv').read_text().strip()
           and drain_complete and not per_user['db_redis_mismatch'] and not per_user['ack_db_mismatch'])
(out / 'checks.json').write_text(json.dumps({'mode':mode, 'db_redis_equal':correct, 'drain_complete':drain_complete, 'k6_exit':load.returncode,
    'ack_db_mismatch':len(per_user['ack_db_mismatch']), 'ack_redis_mismatch':len(per_user['ack_redis_mismatch'])}, indent=2))
print('Results:', out)
print('Cleanup after review:', ' '.join(compose + ['down']))
raise SystemExit(load.returncode or (1 if not correct else 0))
