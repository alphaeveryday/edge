"""Local isolated compose experiment. Run from this directory: python3 run-failover.py S2.
Creates an isolated project; preserves containers and data for inspection.
"""
import json
import os
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
project = os.environ.get('EXPERIMENT_PROJECT', 'etf-' + scenario.lower() + '-' + str(int(time.time())))
compose = ['docker', 'compose', '-p', project, '-f', str(root.parent / 'docker-compose.yaml')]
def dc(*args):
    return subprocess.check_output(compose + list(args), env=env, text=True)
def get(path):
    with urllib.request.urlopen('http://localhost:8080' + path, timeout=6) as r:
        return r.read().decode()
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
master_address = dc('exec', '-T', 'sentinel-1', 'redis-cli', '-p', '26379', '--raw', 'SENTINEL', 'get-master-addr-by-name', 'mymaster').splitlines()
(out / 'master-voted.txt').write_text(dc('exec', '-T', 'sentinel-1', 'redis-cli', '-h', master_address[0], '-p', master_address[1], 'HLEN', 'vote:{' + etf + '}:choices'))
(out / 'sentinel.log').write_text(dc('logs', '--timestamps', 'sentinel-1', 'sentinel-2', 'sentinel-3'))
(out / 'app.log').write_text(dc('logs', '--timestamps', 'app'))
final = json.loads((out / 'after-reconcile.json').read_text())['result']
correct = (final['source'] == 'redis'
           and all(final[c.lower()] == int(expected.get(c, 0)) for c in ('BUY', 'HOLD', 'SELL'))
           and int((out / 'master-voted.txt').read_text()) == sum(map(int, expected.values()))
           and not (out / 'duplicates.tsv').read_text().strip())
(out / 'checks.json').write_text(json.dumps({'db_redis_equal':correct, 'k6_exit':load.returncode}, indent=2))
print('Results:', out)
print('Cleanup after review:', ' '.join(compose + ['down']))
raise SystemExit(load.returncode or (1 if not correct else 0))
