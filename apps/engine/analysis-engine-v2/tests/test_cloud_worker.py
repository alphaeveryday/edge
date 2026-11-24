"""Publication and observation failures are distinct operational outcomes."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from edge_analysis_v2.cloud import worker
from edge_analysis_v2.cloud.contract import decode_json
from test_cloud_observation import MemoryS3, REQUEST


@pytest.mark.parametrize('effort', ['low', 'high'])
@pytest.mark.parametrize('failure', [None,'source','model','runner','export'])
@pytest.mark.parametrize('owner', [None, 'execution-arn'])
def test_worker_records_failure_without_leaking_keys(tmp_path,monkeypatch,failure,owner,effort):
    s3=MemoryS3()
    session=Mock()
    session.client.return_value=s3
    connection=MagicMock()
    connection.__enter__.return_value=connection
    connection.execute.return_value.fetchone.return_value=(True,)
    monkeypatch.setattr(worker,'connect_results',Mock(return_value=connection))
    monkeypatch.setattr(worker,'connect_sources',Mock(return_value=connection))
    load=Mock(return_value={})
    if failure=='source':
        load.side_effect=ValueError('SENSITIVE')
    monkeypatch.setattr(worker,'load_source',load)
    monkeypatch.setattr(worker,'load_flow',lambda c,s:s)
    monkeypatch.setattr(worker,'load_prices',lambda c,s,request=None:s)
    monkeypatch.setattr(worker,'load_research_observations',lambda c,s:s)
    monkeypatch.setattr(worker,'DatabaseTools',Mock())
    runner = AsyncMock()
    if failure == 'runner':
        runner.side_effect = ValueError('SENSITIVE')
    monkeypatch.setattr(worker, 'run_model', runner)
    def execute_model(**kwargs):
        asyncio.run(kwargs['model_call'](effort=effort))
    execute=Mock(side_effect=execute_model)
    if failure=='model':
        execute.side_effect=ValueError('SENSITIVE')
    monkeypatch.setattr(worker,'execute_request',execute)
    export=Mock(return_value={'status':'passed'})
    if failure=='export':
        export.side_effect=RuntimeError('SENSITIVE')
    monkeypatch.setattr(worker,'export_records',export)
    request=REQUEST | {'kind':'outlook','execution_mode':'batch','analysis_at':'2026-10-15T21:00:00Z'}
    args=dict(bucket='bucket',ca_path=tmp_path/'ca',folder=tmp_path,key='SENSITIVE',model='deepseek-flash',session=session,owner=owner)
    if failure:
        with pytest.raises(RuntimeError):
            worker.run(request,**args)
    else:
        worker.run(request,**args)
    raw=s3.objects['analysis-v2/runs/'+REQUEST['analysis_id']+'/manifest.json']
    job=decode_json(raw)['job']
    if failure in ('source', 'model'):
        assert 'reasoning_effort' not in job
        runner.assert_not_called()
    else:
        assert job['reasoning_effort'] == effort
        runner.assert_awaited_once_with(effort=effort)
    assert b'SENSITIVE' not in raw
    assert job['status']==('failed' if failure in ('source','model','runner') else 'completed')
    assert job['observation_status']==('incomplete' if failure=='export' else 'complete')
    # A duplicate must not start source reads or erase these terminal observations.
    load.reset_mock()
    with pytest.raises(FileExistsError):
        worker.run(request,**args)
    load.assert_not_called()


def test_exported_records_summarize_snapshot_rows_and_keep_evidence(tmp_path, monkeypatch):
    """storage.json and evidence.json each copied the whole research state; the database row stays the source."""
    cited = {'tool_run_id': 'e', 'tool_id': 'list_events:v1', 'output': {'result': ['원문']}}
    state = {'tool_run_id': 's', 'tool_id': 'record_research_state:v1', 'output': {'result': {'materials': {'m': 1}}}}
    monkeypatch.setattr(worker, 'read_analysis_evidence', lambda *a: {'analysis': {'status': 'failed'}, 'tool_runs': [cited, state]})
    monkeypatch.setattr(worker, 'read_storage', lambda *a: {'storage': 'postgresql', 'tables': {'tool_runs': [cited, state]}})
    monkeypatch.setattr(worker, 'read_contract_audit', lambda *a: {'status': 'failed'})
    worker.export_records(None, REQUEST, tmp_path)
    for runs in (decode_json((tmp_path/'evidence.json').read_bytes())['tool_runs'],
                 decode_json((tmp_path/'storage.json').read_bytes())['tables']['tool_runs']):
        assert runs[0] == cited
        assert set(runs[1]['output']) == {'omitted', 'bytes'} and runs[1]['tool_run_id'] == 's'
