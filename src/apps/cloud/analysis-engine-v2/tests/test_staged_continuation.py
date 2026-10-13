import json

from edge_analysis_v2.cloud.artifacts import FILES, PREFIX, was_staged


class Store:
    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {'Body': type('Body', (), {'read': lambda _: json.dumps(self.objects[Key]).encode()})()}


def test_only_a_run_that_left_an_investigation_report_counts_as_staged():
    staged, single, unknown = 'a' * 32, 'b' * 32, 'c' * 32
    store = Store({PREFIX + staged + '/manifest.json': {'files': {'investigation.md': {}, 'screen.json': {}}},
                   PREFIX + single + '/manifest.json': {'files': {'screen.json': {}}}})
    assert was_staged(store, 'bucket', staged)
    assert not was_staged(store, 'bucket', single)
    assert not was_staged(store, 'bucket', unknown)
    assert 'investigation.md' in FILES


def test_a_staged_run_uploads_its_stage_files_and_one_total_measurement(tmp_path):
    from edge_analysis_v2.analysis.service import total_measurement
    from edge_analysis_v2.cloud.artifacts import Publisher, download, validate_manifest
    from test_cloud_observation import MemoryS3, REQUEST
    stages = {'investigation': {'wall_seconds': 400.0, 'tool_calls': 60, 'usage': {'input_tokens': 10, 'output_tokens': 5},
                                'tools': {'read_web_document': {'calls': 12, 'errors': 2}}},
              'writing': {'wall_seconds': 200.0, 'tool_calls': 3, 'usage': {'input_tokens': 4, 'output_tokens': 7},
                          'tools': {'write_outlook_body': {'calls': 1, 'errors': 0}}}}
    total = total_measurement(stages)
    assert (total['wall_seconds'], total['tool_calls'], total['usage']) == (600.0, 63, {'input_tokens': 14, 'output_tokens': 12})
    assert set(total['tools']) == {'read_web_document', 'write_outlook_body'} and total['stages'] == stages

    source = tmp_path / 'worker'
    (source / 'investigation').mkdir(parents=True)
    (source / 'investigation' / 'measurement.json').write_text(json.dumps(stages['investigation']))
    (source / 'measurement.json').write_text(json.dumps(total))
    (source / 'investigation.md').write_text('# report')
    s3 = MemoryS3()
    job = REQUEST | {'origin': 'cloud', 'status': 'running', 'started_at': REQUEST['analysis_at'], 'scenario': 'database'}
    manifest = Publisher(s3, 'bucket', REQUEST['analysis_id'], source).publish(job)
    validate_manifest(manifest)
    assert {'investigation/measurement.json', 'measurement.json', 'investigation.md'} <= set(manifest['files'])
    download(s3, 'bucket', manifest, tmp_path / 'local')
    assert json.loads((tmp_path / 'local' / 'investigation' / 'measurement.json').read_text())['tool_calls'] == 60
