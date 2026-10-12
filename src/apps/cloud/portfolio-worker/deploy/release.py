"""Application release only. Terraform owns all AWS infrastructure and routing."""
import hashlib
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
REGION = 'ap-northeast-2'


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()) if os.getenv('GITHUB_EVENT_PATH') else {}
    trusted_run = os.getenv('GITHUB_EVENT_NAME') == 'workflow_run' and event.get('workflow_run', {}).get('head_branch') == 'dev' and event['workflow_run']['conclusion'] == 'success'
    if os.getenv('GITHUB_ACTIONS') != 'true' or not (os.getenv('GITHUB_REF') == 'refs/heads/dev' or trusted_run):
        raise RuntimeError('Release through the GitHub dev workflow only')
    session = boto3.Session(region_name=REGION)
    ssm, s3 = session.client('ssm'), session.client('s3')
    config = json.loads(ssm.get_parameter(Name='/edge/portfolio/deployment')['Parameter']['Value'])
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name in ('Dockerfile', 'pyproject.toml', 'uv.lock', 'src', 'deploy/compose.yml', 'deploy/Caddyfile', 'deploy/runtime_env.py'):
                path = ROOT / name
                for child in sorted(path.rglob('*')) if path.is_dir() else [path]:
                    if child.is_file() and '__pycache__' not in child.parts:
                        tar.add(child, arcname=child.relative_to(ROOT))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        key = f'releases/{digest}.tar.gz'
        s3.upload_file(str(archive), config['bucket'], key)
    directory = f'/opt/edge-portfolio/releases/{digest[:16]}'
    commands = ['set -eu', 'cloud-init status --wait >/dev/null',
        f'mkdir -p {directory}', f'aws s3 cp s3://{config["bucket"]}/{key} {directory}/release.tar.gz --only-show-errors',
        f'echo "{digest}  {directory}/release.tar.gz" | sha256sum -c -',
        f'tar -xzf {directory}/release.tar.gz -C {directory}',
        f'curl -fsSL https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem -o {directory}/deploy/rds-ca.pem',
        'umask 077',
        f'aws secretsmanager get-secret-value --region {REGION} --secret-id edge/portfolio/runtime --query SecretString --output text > /opt/edge-portfolio/runtime.json',
        f'aws secretsmanager get-secret-value --region {REGION} --secret-id edge/analysis-v2/api-reader --query SecretString --output text > /opt/edge-portfolio/reader.json',
        f'python3 {directory}/deploy/runtime_env.py', f'cd {directory}/deploy',
        'docker compose --env-file /opt/edge-portfolio/runtime.env up -d --build --wait --wait-timeout 120',
        'docker compose --env-file /opt/edge-portfolio/runtime.env exec -T api python -c "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8092/health\')"',
        f'ln -sfn {directory} /opt/edge-portfolio/current']
    command = ssm.send_command(InstanceIds=[config['instance_id']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': commands, 'executionTimeout': ['1200']})['Command']['CommandId']
    print(f'Release {digest}; SSM {command}', flush=True)
    for _ in range(150):
        time.sleep(10)
        result = ssm.get_command_invocation(CommandId=command, InstanceId=config['instance_id'])
        if result['Status'] == 'Success':
            print('Host release verified')
            return
        if result['Status'] not in ('Pending', 'InProgress', 'Delayed'):
            raise RuntimeError(f'SSM release {command}: {result["Status"]}; inspect restricted SSM logs')
    raise TimeoutError(f'SSM release {command} did not finish')


if __name__ == '__main__':
    main()
