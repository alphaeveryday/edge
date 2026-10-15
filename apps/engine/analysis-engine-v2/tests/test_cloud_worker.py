"""Publication and observation failures are distinct operational outcomes."""
from unittest.mock import MagicMock, Mock

import pytest

from edge_analysis_v2.cloud import worker
from edge_analysis_v2.cloud.contract import decode_json
from test_cloud_observation import MemoryS3, REQUEST


@pytest.mark.parametrize('failure', [None,'source','model','export'])
@pytest.mark.parametrize('owner', [None, 'execution-arn'])
def test_worker_records_failure_without_leaking_keys(tmp_path,monkeypatch,failure,owner):
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
    execute=Mock()
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
    assert job['reasoning_effort']=='low'
    if failure!='source':
        model_call=execute.call_args.kwargs['model_call']
        assert model_call.func is worker.run_model
        assert model_call.keywords=={'effort':'low'}
    assert b'SENSITIVE' not in raw
    assert job['status']==('failed' if failure in ('source','model') else 'completed')
    assert job['observation_status']==('incomplete' if failure=='export' else 'complete')
    # A duplicate must not start source reads or erase these terminal observations.
    load.reset_mock()
    with pytest.raises(FileExistsError):
        worker.run(request,**args)
    load.assert_not_called()
