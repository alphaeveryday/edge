import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def release(monkeypatch):
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace())
    spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'deploy/release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('exists', [True, False])
def test_secret_bootstrap_preserves_existing_password_and_never_prints_it(release, exists, capsys):
    class Missing(Exception): pass
    class Client:
        exceptions = SimpleNamespace(ResourceNotFoundException=Missing)
        writes = []
        def describe_secret(self, **args):
            assert args['SecretId'] == 'edge/portfolio/admin'
        def get_secret_value(self, **args):
            if not exists:
                raise Missing()
            return {'SecretString': 'existing-private-credential'}
        def put_secret_value(self, **args):
            self.writes.append(json.loads(args['SecretString']))
    client = Client()
    release.ensure_admin_secret(client)
    assert len(client.writes) == int(not exists)
    if not exists:
        assert client.writes[0]['username'] == 'admin'
        assert len(client.writes[0]['password']) >= 32
    assert capsys.readouterr().out == ''


def test_missing_terraform_resource_or_permission_stops_release(release):
    class Client:
        def describe_secret(self, **args):
            raise PermissionError('Terraform prerequisite not ready')
    with pytest.raises(PermissionError):
        release.ensure_admin_secret(Client())
