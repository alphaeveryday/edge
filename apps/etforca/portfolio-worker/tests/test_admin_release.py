import importlib.util
import json
import sys
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_release_requires_a_separate_private_build(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace())
    spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'deploy/release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    monkeypatch.setattr(release, 'ROOT', tmp_path / 'portfolio-worker')
    with pytest.raises(RuntimeError, match='private administrator UI'):
        release.admin_ui_directory()
    private = tmp_path / 'portfolio-ui/dist-admin'
    (private / 'assets').mkdir(parents=True)
    (private / 'admin.html').write_text('<html>private</html>')
    assert release.admin_ui_directory() == private


def test_release_builds_before_draining_and_keeps_dependencies_until_worker_stops(monkeypatch, tmp_path):
    submitted = []
    class Ssm:
        def get_parameter(self, **kwargs):
            return {'Parameter': {'Value': json.dumps({'bucket': 'test-bucket', 'instance_id': 'test-instance'})}}
        def send_command(self, **kwargs):
            submitted.extend(kwargs['Parameters']['commands'])
            return {'Command': {'CommandId': 'test-command'}}
        def get_command_invocation(self, **kwargs):
            return {'Status': 'Success'}
    s3 = SimpleNamespace(upload_file=lambda *args: None)
    session = SimpleNamespace(client=lambda name: Ssm() if name == 'ssm' else s3)
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace(Session=lambda **kwargs: session))
    spec = importlib.util.spec_from_file_location('release_drain_test', Path(__file__).parents[1] / 'deploy/release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    private = tmp_path / 'admin-ui'
    private.mkdir()
    monkeypatch.setattr(release, 'admin_ui_directory', lambda: private)
    monkeypatch.setattr(release.subprocess, 'check_output', lambda *args, **kwargs: 'a' * 40)
    built = []
    def run(command, **kwargs):
        built.append(command)
        if isinstance(command, str):  # docker save | gzip writes the image file the release archive packs
            Path(command.rsplit('> ', 1)[1]).write_bytes(b'image')
    monkeypatch.setattr(release.subprocess, 'run', run)
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/dev')
    monkeypatch.delenv('GITHUB_EVENT_PATH', raising=False)
    release.main()
    # The 2 GB host ran out of memory building while serving: the image is built on the runner, the host only loads it
    assert built[0][:2] == ['docker', 'build'] and 'PORTFOLIO_VERSION=' + 'a' * 40 in built[0]
    assert not any(' build' in command for command in submitted)
    load = next(i for i, command in enumerate(submitted) if command.startswith('docker load'))
    stop = next(i for i, command in enumerate(submitted) if command.endswith(' stop --timeout 630 worker'))
    assert load < stop, 'A failed image load must leave the running worker intact'
    compose = [command for command in submitted if command.startswith('docker compose')]
    assert compose[0].endswith(' stop --timeout 630 worker'), 'A ten-minute analysis must finish and save before replacement'
    assert ' up -d --wait' in compose[1] and '--profile admin' not in compose[1], 'The administrator must not start with the services'
    # The administrator starts only through the on-demand port, so the smoke must run after that port is enabled
    socket = submitted.index('systemctl daemon-reload')
    assert submitted.index('systemctl enable --now portfolio-admin.socket') < next(i for i, c in enumerate(submitted) if 'admin_smoke.py' in c)
    assert any(c.startswith('ln -sfn') for c in submitted[:socket]), 'The on-demand administrator starts from the new release'
    assert any('edge_portfolio.verification --version ' + 'a' * 40 + ' --register-only' in command for command in compose)


@pytest.mark.parametrize('was_active', [0, 1])
def test_failed_release_restores_only_a_previously_active_newsletter_timer(was_active):
    spec = importlib.util.spec_from_file_location('release_newsletter_test', Path(__file__).parents[1] / 'deploy/release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    shell = str(Path(shutil.which('git')).parents[1] / 'bin/bash.exe') if os.name == 'nt' else '/bin/sh'
    script = f'''set -eu
was_active={was_active}
systemctl() {{ printf '%s\\n' "$*"; [ "$1" != is-active ] || [ "$was_active" = 1 ]; }}
{release.NEWSLETTER_PAUSE}
exit 17
'''
    result = subprocess.run([shell, '-c', script], capture_output=True, text=True, check=False)
    assert result.returncode == 17, 'Restoring the timer must not hide the deployment failure'
    assert ('start portfolio-newsletter.timer' in result.stdout) is bool(was_active)


def test_release_drains_an_active_sender_without_terminating_its_send(tmp_path):
    spec = importlib.util.spec_from_file_location('release_newsletter_drain_test', Path(__file__).parents[1] / 'deploy/release.py')
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    shell = str(Path(shutil.which('git')).parents[1] / 'bin/bash.exe') if os.name == 'nt' else '/bin/sh'
    state = (tmp_path / 'state').as_posix()
    script = f'''set -eu
systemctl() {{
    case "$1" in
      show) if [ -f '{state}' ]; then echo inactive; else echo activating; fi ;;
      *) printf '%s\\n' "$*" ;;
    esac
}}
sleep() {{ echo waited; touch '{state}'; }}
{release.NEWSLETTER_PAUSE}
echo dependencies-can-now-stop
trap - EXIT
'''
    result = subprocess.run([shell, '-c', script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.index('waited') < result.stdout.index('dependencies-can-now-stop')
    assert 'stop portfolio-newsletter.timer' in result.stdout
    assert 'stop portfolio-newsletter.service' not in result.stdout
