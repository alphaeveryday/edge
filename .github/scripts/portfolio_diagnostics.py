"""Read fixed, redacted portfolio diagnostics through the existing deployment role."""
import base64
import json
import os
import re
import time

import boto3


REPLAY_SCRIPT = r'''
import asyncio,json,os,sys
from edge_portfolio import store
from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.worker import failure_codes
async def replay():
    with store.connect() as db:
        job=db.execute("SELECT input FROM portfolio_jobs WHERE id=%s AND state='failed' AND kind='analysis'",(sys.argv[1],)).fetchone()
    if not job:
        print('REPLAY '+json.dumps({'status':'not_found'}))
        return
    try:
        result=await run_analysis(AnalysisInput.model_validate(job['input']),os.environ['DEEPSEEK_API_KEY'],lambda role: None)
        optimization=result.get('optimization',{})
        print('REPLAY '+json.dumps({'status':'succeeded','seconds':result.get('elapsed_seconds'),'optimization':optimization.get('status'),'candidates':len(optimization.get('candidates',[]))}))
    except Exception as error:
        print('REPLAY '+json.dumps({'status':'failed','codes':failure_codes(error)}))
asyncio.run(replay())
'''


HOST_SCRIPT = r'''
import json,re,subprocess
compose = ['docker','compose','--env-file','/opt/edge-portfolio/runtime.env','-f','/opt/edge-portfolio/current/deploy/compose.yml']
for name in ('api','worker','outlook','db','proxy'):
    cid=subprocess.check_output(compose+['ps','--all','-q',name],text=True).strip()
    if not cid:
        print(json.dumps({'service':name,'state':'missing'}))
        continue
    info=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
    print(json.dumps({'service':name,'state':info['State']['Status'],'health':info['State'].get('Health',{}).get('Status'),'restarts':info['RestartCount']}))
    if name == 'worker':
        logs=subprocess.run(['docker','logs','--tail','100',cid],capture_output=True,text=True)
        for match in re.finditer(r'job=[a-f0-9]{32} status=(?:succeeded seconds=[0-9.]+|failed codes=\[[A-Za-z_\x27, ]+\])',logs.stdout+logs.stderr):
            print(match.group(0))
query="import json; from edge_portfolio import store; db=store.connect(); print(json.dumps(db.execute('SELECT id,state,stage,failure,kind,created_at,finished_at FROM portfolio_jobs ORDER BY created_at DESC LIMIT 10').fetchall(),default=str)); db.close()"
jobs=subprocess.run(compose+['exec','-T','api','python','-c',query],capture_output=True,text=True)
print(jobs.stdout if jobs.returncode == 0 else json.dumps({'jobs':'unavailable'}))
'''


def host_script(job_id):
    if not job_id:
        return HOST_SCRIPT
    if not re.fullmatch(r'[a-f0-9]{32}', job_id):
        raise ValueError('Invalid replay job ID')
    return HOST_SCRIPT + f'''
replay=subprocess.run(compose+['exec','-T','worker','python','-c',{REPLAY_SCRIPT!r},{job_id!r}],capture_output=True,text=True,timeout=330)
safe=[line for line in replay.stdout.splitlines() if line.startswith('REPLAY ')]
print(safe[-1] if safe else '{{"replay":"unavailable"}}')
'''


def main():
    if os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('GITHUB_REF') != 'refs/heads/dev':
        raise RuntimeError('Diagnostics require the GitHub dev workflow')
    script = host_script(os.getenv('PORTFOLIO_REPLAY_JOB_ID', ''))
    ssm = boto3.client('ssm', region_name='ap-northeast-2')
    config = json.loads(ssm.get_parameter(Name='/edge/portfolio/deployment')['Parameter']['Value'])
    encoded = base64.b64encode(script.encode()).decode()
    command = ssm.send_command(InstanceIds=[config['instance_id']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': [f"python3 -c \"import base64; exec(base64.b64decode('{encoded}'))\""], 'executionTimeout': ['360']})['Command']['CommandId']
    for _ in range(78):
        time.sleep(5)
        result = ssm.get_command_invocation(CommandId=command, InstanceId=config['instance_id'])
        if result['Status'] == 'Success':
            print(result['StandardOutputContent'])
            return
        if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
            raise RuntimeError(f'Diagnostics {command}: {result["Status"]}')
    raise TimeoutError(f'Diagnostics {command} did not finish')


if __name__ == '__main__':
    main()
