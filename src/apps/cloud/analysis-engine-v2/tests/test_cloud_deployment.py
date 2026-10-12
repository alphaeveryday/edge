"""Deployment must retain the cloud entrypoint while limiting writable mounts."""
import io
import json
from pathlib import Path

import pytest
import yaml


def register(monkeypatch, container, volumes):
    workflow = Path(__file__).resolve().parents[5]/'.github/workflows/deploy-analysis-engine-v2.yml'
    steps = yaml.safe_load(workflow.read_text(encoding='utf-8'))['jobs']['deploy']['steps']
    script = next(step['run'] for step in steps if step.get('name','').startswith('Register the immutable'))
    python = script.split("python - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
    task = {'family':'edge-dev-analysis-v2', 'containerDefinitions':[container], 'volumes':volumes,
            'taskRoleArn':'keep-role', 'networkMode':'awsvpc'}
    output = io.StringIO()
    def open_file(path, mode='r'):
        if path == '/tmp/task.json':
            return io.StringIO(json.dumps({'taskDefinition':task}))
        assert path == '/tmp/next-task.json' and mode == 'w'
        return output
    monkeypatch.setenv('ANALYSIS_IMAGE', 'reviewed-image')
    exec(compile(python, str(workflow), 'exec'), {'open':open_file})
    return json.loads(output.getvalue())


def test_deploy_is_repeatable_and_keeps_worker_configuration(monkeypatch):
    original = {'name':'worker','image':'old','command':['--request','fixed-request']}
    task = register(monkeypatch, original, [])
    worker = task['containerDefinitions'][0]
    assert worker['command'] == original['command']
    assert task['taskRoleArn'] == 'keep-role'
    assert worker['user'] == '1001:1001' and worker['readonlyRootFilesystem'] is True
    assert worker['linuxParameters']['capabilities'] == {'drop':['ALL']}
    assert worker['mountPoints'] == [{'sourceVolume':'analysis-scratch','containerPath':'/tmp','readOnly':False}]
    assert task['volumes'] == [{'name':'analysis-scratch'}]
    assert register(monkeypatch, worker, task['volumes']) == task


def test_deploy_does_not_silently_replace_shared_mounts(monkeypatch):
    with pytest.raises(AssertionError):
        register(monkeypatch, {'mountPoints':[{'containerPath':'/shared'}]}, [{'name':'shared'}])
