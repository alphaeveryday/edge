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
    skills = {'available':['analysis:hypothesis-analysis-workflow'], 'documents':[]}
    (source/'skills.json').write_text(json.dumps(skills), encoding='utf-8')
    (source/'AGENTS.md').write_text('Use skills when needed.', encoding='utf-8')
    events = source/'events.jsonl'
    events.write_bytes(b'{"step":1}\n{"step":')
    s3 = MemoryS3()
    publisher = Publisher(s3, 'bucket', REQUEST['analysis_id'], source)
    job = REQUEST | {'status':'running', 'origin':'cloud', 'started_at':REQUEST['analysis_at']}
    manifest = publisher.publish(job)
    download(s3, 'bucket', manifest, local)
    assert json.loads((local/'input.json').read_text(encoding='utf-8')) == body
    assert json.loads((local/'skills.json').read_text(encoding='utf-8')) == skills
    assert (local/'AGENTS.md').read_text(encoding='utf-8') == 'Use skills when needed.'
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


def test_run_record_keeps_inherited_research_state_once_and_compact(tmp_path):
    """The inherited state is already a database row; copying it into every run made outlook records grow daily."""
    source, local = tmp_path/'source', tmp_path/'local'
    source.mkdir()
    state = {'version': 2, 'materials': {'m1': {'kind': 'note', 'text': '원문'}, 'm2': {'kind': 'note', 'text': 'x'}}}
    initial = {'context': {'etf_code': '091160'}, 'research_state': state, 'holdings': [[1, 0.5]]}
    (source/'input.json').write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding='utf-8')
    (source/'investigation').mkdir()
    (source/'investigation'/'input.json').write_text(json.dumps(initial), encoding='utf-8')
    (source/'model_input.json').write_text(json.dumps({'input': {'research_state': {'catalog': {}}}}), encoding='utf-8')
    s3 = MemoryS3()
    manifest = Publisher(s3, 'bucket', REQUEST['analysis_id'], source).publish(
        REQUEST | {'status':'running', 'origin':'cloud', 'started_at':REQUEST['analysis_at']})
    download(s3, 'bucket', manifest, local)
    for name in ('input.json', 'investigation/input.json'):
        kept = json.loads((local/name).read_text(encoding='utf-8'))
        assert kept['research_state']['materials'] == 2 and 'omitted' in kept['research_state']
        assert {k: v for k, v in kept.items() if k != 'research_state'} == {k: v for k, v in initial.items() if k != 'research_state'}
    # What the model was actually given is the catalog, so that file is kept as written.
    assert json.loads((local/'model_input.json').read_text()) == {'input': {'research_state': {'catalog': {}}}}
    assert b'\n' not in (local/'input.json').read_bytes()
    # The local input is untouched: a resumed local run compares it with the input it rebuilds.
    assert json.loads((source/'input.json').read_text(encoding='utf-8')) == initial


def test_snapshot_rows_are_summarized_and_evidence_rows_kept_whole():
    from edge_analysis_v2.cloud.artifacts import encode, omit_snapshots
    evidence = {'tool_run_id': 'e', 'tool_id': 'sum_weighted_net_flow:v1', 'output': {'result': [1, 2]}}
    state = {'tool_run_id': 's', 'tool_id': 'record_research_state:v1', 'output': {'result': {'materials': {'m': 'x'*100}}}}
    news = {'tool_run_id': 'n', 'tool_id': 'capture_news_snapshot:v2', 'output': {'data': 'y'*50}}
    kept = omit_snapshots([evidence, state, news])
    assert kept[0] == evidence
    assert [run['tool_run_id'] for run in kept] == ['e', 's', 'n']
    for run, original in zip(kept[1:], (state, news)):
        assert set(run['output']) == {'omitted', 'bytes'} and run['output']['bytes'] == len(encode(original['output']))
    assert state['output']['result']['materials']  # the caller's rows are not modified


def test_failed_upload_is_retried_on_the_next_flush(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    (source/'input.json').write_text('{"value":1}')
    s3 = MemoryS3()
    publisher = Publisher(s3, 'bucket', REQUEST['analysis_id'], source)
    job = REQUEST | {'status':'running', 'origin':'cloud', 'started_at':REQUEST['analysis_at']}
    real = s3.put_object
    s3.put_object = Mock(side_effect=OSError('network'))
    with pytest.raises(OSError):
        publisher.publish(job)
    s3.put_object = real
    assert 'input.json' in publisher.publish(job)['files']


def test_changed_file_is_uploaded_again_and_unchanged_file_is_not_reparsed(tmp_path, monkeypatch):
    from edge_analysis_v2.cloud import artifacts
    source = tmp_path/'source'
    source.mkdir()
    (source/'response.json').write_text('{"value":1}')
    s3 = MemoryS3()
    publisher = Publisher(s3, 'bucket', REQUEST['analysis_id'], source)
    job = REQUEST | {'status':'running', 'origin':'cloud', 'started_at':REQUEST['analysis_at']}
    first = publisher.publish(job)['files']['response.json']['sha256']
    parsed = Mock(side_effect=artifacts.decode_json)
    monkeypatch.setattr(artifacts, 'decode_json', parsed)
    publisher.publish(job)
    assert parsed.call_count == 0  # the 5-second flush skips files that did not change
    (source/'response.json').write_text('{"value":2}')
    assert publisher.publish(job)['files']['response.json']['sha256'] != first


def test_a_file_that_cannot_be_encoded_fails_loudly_instead_of_being_skipped(tmp_path):
    # A JSON file still being written is skipped until it parses; one that parses but cannot be stored must not vanish.
    source = tmp_path/'source'
    source.mkdir()
    (source/'response.json').write_text('{"x":1e999}')
    publisher = Publisher(MemoryS3(), 'bucket', REQUEST['analysis_id'], source)
    with pytest.raises(ValueError):
        publisher.publish(REQUEST | {'status':'running', 'origin':'cloud', 'started_at':REQUEST['analysis_at']})
