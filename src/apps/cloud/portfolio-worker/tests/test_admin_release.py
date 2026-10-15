import importlib.util
import json
import sys
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
    monkeypatch.setattr(release.time, 'sleep', lambda _: None)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/dev')
    monkeypatch.delenv('GITHUB_EVENT_PATH', raising=False)
    release.main()
    compose = [command for command in submitted if command.startswith('docker compose')]
    assert compose[0].endswith(' build'), 'Failed builds must leave the running worker intact'
    assert compose[1].endswith(' stop --timeout 630 worker'), 'A ten-minute analysis must finish and save before replacement'
    assert ' up -d --wait' in compose[2], 'Service replacement must wait until the current analysis is saved'
    assert '--build' not in compose[2]
