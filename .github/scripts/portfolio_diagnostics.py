"""Read fixed, redacted portfolio diagnostics through the existing deployment role."""
import base64
import json
import os
import time

import boto3


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


def main():
    if os.getenv('GITHUB_ACTIONS') != 'true' or os.getenv('GITHUB_REF') != 'refs/heads/dev':
        raise RuntimeError('Diagnostics require the GitHub dev workflow')
    ssm = boto3.client('ssm', region_name='ap-northeast-2')
    config = json.loads(ssm.get_parameter(Name='/edge/portfolio/deployment')['Parameter']['Value'])
    encoded = base64.b64encode(HOST_SCRIPT.encode()).decode()
    command = ssm.send_command(InstanceIds=[config['instance_id']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': [f"python3 -c \"import base64; exec(base64.b64decode('{encoded}'))\""], 'executionTimeout': ['120']})['Command']['CommandId']
    for _ in range(24):
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
