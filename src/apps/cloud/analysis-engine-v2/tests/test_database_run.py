"""The real-run entry point must not retain source connections during model work."""
import json
import sys

import pytest

from edge_analysis_v2.analysis import database_run


@pytest.mark.parametrize('fails', [False,True])
def test_real_cli_closes_source_and_records_actual_execution_status(tmp_path,monkeypatch,fails):
    active = []
    env_file = tmp_path / 'settings.env'
    env_file.write_text('TINYFISH_API_KEY=private-web-key\n', encoding='utf-8')
    class SourceConnection:
        def __enter__(self):
            active.append(True)
            return self
        def __exit__(self,*args):
            active.clear()
    def execute(**kwargs):
        assert not active
        assert 'fixture' not in kwargs
        assert 'previous_analysis_id' not in kwargs  # Service selects its same-source predecessor.
        assert kwargs['source_tools']=='real tools'
        if fails:
            raise ValueError('test failure')
    monkeypatch.setattr(sys,'argv',['run','--kind','movement','--ticker','091160',
        '--analysis-at','2026-09-29T12:00:00+09:00','--rds-ca','ca.pem',
        '--env-file',str(env_file),'--runs-dir',str(tmp_path)])
    monkeypatch.setattr(database_run,'read_settings',lambda path:{'key':'test-key','model':'test'})
    monkeypatch.setattr(database_run,'connect_sources',lambda path:SourceConnection())
    monkeypatch.setattr(database_run,'load_source',lambda *args:{'raw':'database'})
    monkeypatch.setattr(database_run,'load_flow',lambda connection,data:data)
    monkeypatch.setattr(database_run,'load_prices',lambda connection,data:data)
    monkeypatch.setattr(database_run,'connect_results',lambda path:SourceConnection())
    monkeypatch.setattr(database_run,'load_research_observations',lambda connection,data:data)
    def tools(data, *, web):
        assert isinstance(web, database_run.WebResearch)
        assert web.cutoff.isoformat() == '2026-09-29T12:00:00+09:00'
        return 'real tools'
    monkeypatch.setattr(database_run,'DatabaseTools',tools)
    monkeypatch.setattr(database_run,'execute_request',execute)
    if fails:
        with pytest.raises(ValueError,match='test failure'):
            database_run.main()
    else:
        database_run.main()
    job=json.loads(next(tmp_path.glob('*/job.json')).read_text(encoding='utf-8'))
    assert 'previous_analysis_id' not in job
    assert job['data_source']=='database'
    assert job['status']==('failed' if fails else 'completed')
    assert 'private-web-key' not in json.dumps(job)
