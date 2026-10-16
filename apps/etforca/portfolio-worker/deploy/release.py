"""Application release only. Terraform owns all AWS infrastructure and routing."""
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
REGION = 'ap-northeast-2'
NEWSLETTER_PAUSE = '''newsletter_timer_was_active=0
if systemctl is-active --quiet portfolio-newsletter.timer; then newsletter_timer_was_active=1; fi
trap 'result=$?; if [ "$newsletter_timer_was_active" = 1 ]; then systemctl start portfolio-newsletter.timer || echo NEWSLETTER_TIMER_RESTORE_FAILED >&2; fi; exit "$result"' EXIT
if systemctl cat portfolio-newsletter.timer >/dev/null 2>&1; then systemctl stop portfolio-newsletter.timer; fi
newsletter_wait=0
while true; do
    newsletter_state=$(systemctl show --property=ActiveState --value portfolio-newsletter.service 2>/dev/null || true)
    case "$newsletter_state" in active|activating|deactivating) ;; *) break ;; esac
    if [ "$newsletter_wait" -ge 330 ]; then echo NEWSLETTER_DRAIN_TIMEOUT >&2; exit 1; fi
    sleep 5
    newsletter_wait=$((newsletter_wait + 5))
done'''


def admin_ui_directory():
    directory = ROOT.parent / 'portfolio-ui' / 'dist-admin'
    if not (directory / 'admin.html').is_file() or not (directory / 'assets').is_dir():
        raise RuntimeError('Build the private administrator UI before release')
    return directory


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()) if os.getenv('GITHUB_EVENT_PATH') else {}
    trusted_run = os.getenv('GITHUB_EVENT_NAME') == 'workflow_run' and event.get('workflow_run', {}).get('head_branch') == 'dev' and event['workflow_run']['conclusion'] == 'success'
    if os.getenv('GITHUB_ACTIONS') != 'true' or not (os.getenv('GITHUB_REF') == 'refs/heads/dev' or trusted_run):
        raise RuntimeError('Release through the GitHub dev workflow only')
    session = boto3.Session(region_name=REGION)
    ssm, s3 = session.client('ssm'), session.client('s3')
    admin_ui = admin_ui_directory()
    version = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    config = json.loads(ssm.get_parameter(Name='/edge/portfolio/deployment')['Parameter']['Value'])
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / 'release.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name in ('Dockerfile', 'pyproject.toml', 'uv.lock', 'src', 'deploy/compose.yml', 'deploy/Caddyfile', 'deploy/runtime_env.py', 'deploy/admin_smoke.py', 'deploy/portfolio-newsletter.service', 'deploy/portfolio-newsletter.timer'):
                path = ROOT / name
                for child in sorted(path.rglob('*')) if path.is_dir() else [path]:
                    if child.is_file() and '__pycache__' not in child.parts:
                        tar.add(child, arcname=child.relative_to(ROOT))
            tar.add(admin_ui, arcname='admin-ui')
            metadata = json.dumps({'version': version}).encode()
            entry = tarfile.TarInfo('src/edge_portfolio/data/deployment.json')
            entry.size = len(metadata)
            tar.addfile(entry, io.BytesIO(metadata))
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
        'docker compose --env-file /opt/edge-portfolio/runtime.env --profile newsletter build',
        NEWSLETTER_PAUSE,
        # Keep the old API, database and outlook service alive until the worker finishes.
        'docker compose --env-file /opt/edge-portfolio/runtime.env stop --timeout 630 worker',
        'docker compose --env-file /opt/edge-portfolio/runtime.env up -d --wait --wait-timeout 120',
        'docker compose --env-file /opt/edge-portfolio/runtime.env exec -T api python -c "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8092/health\')"',
        f'python3 {directory}/deploy/admin_smoke.py',
        f'docker compose --env-file /opt/edge-portfolio/runtime.env exec -T api python -m edge_portfolio.verification --version {version} --register-only',
        f'ln -sfn {directory} /opt/edge-portfolio/current',
        f'install -m 644 {directory}/deploy/portfolio-newsletter.service {directory}/deploy/portfolio-newsletter.timer /etc/systemd/system/',
        'systemctl daemon-reload', 'systemctl enable --now portfolio-newsletter.timer', 'trap - EXIT']
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
