"""Cloud observation must preserve contracts and survive local restarts."""
import io
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from edge_analysis_v2.cloud.contract import decode_request
from edge_analysis_v2.cloud.artifacts import Publisher, download


REQUEST = dict(analysis_id='a'*32, kind='outlook', etf_code='091160',
               analysis_at='2026-10-01T08:30:00+09:00')


class MemoryS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        if kwargs.get('IfNoneMatch')=='*' and Key in self.objects:
            raise FileExistsError('Manifest already exists')
        self.objects[Key] = bytes(Body)

    def get_object(self, *, Bucket, Key, **kwargs):
        return {'Body': io.BytesIO(self.objects[Key])}


@pytest.mark.parametrize('change', [dict(extra=1), dict(kind='daily'), dict(etf_code='../x'),
    dict(analysis_id='../x'), dict(analysis_at='2026-10-01T08:30:00'), dict(analysis_at='bad')])
def test_invalid_request_cannot_launch_arbitrary_work(change):
    with pytest.raises(ValueError):
        decode_request(json.dumps(REQUEST | change))


def test_duplicate_json_keys_and_nonfinite_values_are_not_silently_accepted():
    for raw in ['{"kind":"outlook","kind":"movement"}', '{"x":NaN}']:
        with pytest.raises(ValueError):
            decode_request(raw)
    assert decode_request(json.dumps(REQUEST)) == REQUEST


def test_incremental_transport_preserves_numbers_shapes_and_complete_events(tmp_path):
    source, local = tmp_path/'source', tmp_path/'local'
    source.mkdir()
    body = {'columns':['amount'], 'rows':[[9007199254740993]], 'note':'원문'}
    (source/'input.json').write_text(json.dumps(body, ensure_ascii=False), encoding='utf-8')
    events = source/'events.jsonl'
    events.write_bytes(b'{"step":1}\n{"step":')
    s3 = MemoryS3()
    publisher = Publisher(s3, 'bucket', REQUEST['analysis_id'], source)
    job = REQUEST | {'status':'running', 'origin':'cloud', 'started_at':REQUEST['analysis_at']}
    manifest = publisher.publish(job)
    download(s3, 'bucket', manifest, local)
    assert json.loads((local/'input.json').read_text(encoding='utf-8')) == body
    assert (local/'events.jsonl').read_bytes() == b'{"step":1}\n'
    events.write_bytes(b'{"step":1}\n{"step":2}\n')
    manifest = publisher.publish(job | {'status':'completed'})
    download(s3, 'bucket', manifest, local)
    download(s3, 'bucket', manifest, local)
    assert (local/'events.jsonl').read_bytes() == events.read_bytes()
    assert json.loads((local/'job.json').read_text())['status'] == 'completed'


def test_corrupt_remote_file_does_not_replace_last_good_local_file(tmp_path):
    source, local = tmp_path/'source', tmp_path/'local'
    source.mkdir()
    (source/'input.json').write_text('{"value":1}')
    s3 = MemoryS3()
    publisher = Publisher(s3,'bucket',REQUEST['analysis_id'],source)
    manifest = publisher.publish(REQUEST | {'status':'running','origin':'cloud','started_at':REQUEST['analysis_at']})
    download(s3,'bucket',manifest,local)
    entry = manifest['files']['input.json']
    s3.objects[entry['key']] = b'corrupt'
    (local/'.objects'/entry['sha256']).write_bytes(b'corrupt-cache')
    (local/'input.json').write_text('{"value":2}')
    with pytest.raises(ValueError):
        download(s3,'bucket',manifest,local)
    assert (local/'input.json').read_text() == '{"value":2}'
    malicious = deepcopy(manifest)
    malicious['files']['../escape'] = entry
    with pytest.raises(ValueError):
        download(s3,'bucket',malicious,local)


def test_dashboard_restart_does_not_interrupt_a_cloud_analysis(tmp_path):
    from edge_analysis_v2.dashboard.jobs import ExecutionDashboard
    folder = tmp_path/REQUEST['analysis_id']
    folder.mkdir()
    (folder/'job.json').write_text(json.dumps(REQUEST | {'status':'running','origin':'cloud'}))
    ExecutionDashboard(tmp_path,key='test',model='test',connection_factory=Mock())
    assert json.loads((folder/'job.json').read_text())['status'] == 'running'


def test_cloud_db_connection_does_not_require_a_local_tunnel(tmp_path, monkeypatch):
    from edge_analysis_v2.storage import database as db
    ca = tmp_path/'ca.pem'
    ca.write_text('test')
    session = Mock()
    session.client.return_value.get_secret_value.return_value = {'SecretString':json.dumps(dict(
        host=db.DB_HOST,port=5432,dbname='edge',username=db.DB_USER,password='test'))}
    connect = Mock()
    monkeypatch.setattr(db.psycopg,'connect',connect)
    db.connect_results(ca, session=session, cloud=True)
    assert connect.call_args.kwargs['port'] == 5432
    assert 'hostaddr' not in connect.call_args.kwargs


def test_duplicate_worker_cannot_erase_previous_observations(tmp_path):
    s3=MemoryS3()
    job=REQUEST | {'status':'completed','origin':'cloud','started_at':REQUEST['analysis_at']}
    first=Publisher(s3,'bucket',REQUEST['analysis_id'],tmp_path)
    first.publish(job)
    before=dict(s3.objects)
    with pytest.raises(FileExistsError):
        Publisher(s3,'bucket',REQUEST['analysis_id'],tmp_path).publish(job | {'status':'running'})
    assert s3.objects==before


def test_terminal_publish_drains_backlog_and_rejects_truncated_event(tmp_path, monkeypatch):
    from edge_analysis_v2.cloud import artifacts
    monkeypatch.setattr(artifacts,'LIMIT',32)
    s3=MemoryS3()
    publisher=Publisher(s3,'bucket',REQUEST['analysis_id'],tmp_path)
    job=REQUEST | {'status':'completed','origin':'cloud','started_at':REQUEST['analysis_at']}
    content=b'{"step":1}\n'*20
    (tmp_path/'events.jsonl').write_bytes(content)
    manifest=publisher.publish(job)
    assert b''.join(s3.objects[e['key']] for e in manifest['events'])==content
    (tmp_path/'events.jsonl').write_bytes(content+b'{"step":')
    with pytest.raises(ValueError):
        publisher.publish(job)
