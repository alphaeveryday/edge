"""Cloud success includes movement delivery; outlook has no movement side effect."""
from unittest.mock import MagicMock, Mock

import pytest

from edge_analysis_v2.cloud import worker


@pytest.mark.parametrize('kind,fails',[('movement',False),('movement',True),('outlook',False)])
def test_worker_registers_after_analysis_and_reports_delivery_failure(monkeypatch,tmp_path,kind,fails):
    events=[]
    connection=MagicMock()
    connection.__enter__.return_value=connection
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value=None
    connection.execute.return_value.fetchone.return_value=(True,)
    monkeypatch.setattr(worker,'connect_results',lambda *a,**k:connection)
    monkeypatch.setattr(worker,'connect_sources',lambda *a,**k:connection)
    for name in ['load_source','load_flow','load_prices','DatabaseTools']:
        monkeypatch.setattr(worker,name,Mock())
    def observations(conn, source):
        assert conn is connection
        events.append('research')
        return source
    monkeypatch.setattr(worker,'load_research_observations',observations)
    monkeypatch.setattr(worker,'execute_request',lambda **k:events.append('analysis'))
    def deliver(*args):
        events.append('delivery')
        if fails:
            raise RuntimeError('delivery unavailable')
        return 1
    monkeypatch.setattr(worker,'enqueue_movement',deliver)
    publisher=Mock()
    states=[]
    publisher.publish.side_effect=lambda job:states.append(dict(job))
    monkeypatch.setattr(worker,'Publisher',lambda *a:publisher)
    monkeypatch.setattr(worker,'export_records',lambda *a:None)
    request={'kind':kind,'analysis_id':'one','etf_code':'091160','analysis_at':'2026-10-02T10:00:00+09:00'}
    def run():
        worker.run(request,bucket='test',ca_path=tmp_path/'ca',folder=tmp_path/'output',key='test',model='test',session=Mock())
    if fails:
        with pytest.raises(RuntimeError,match='analysis failed'):
            run()
        assert states[-1]['status']=='failed'
    else:
        run()
        assert states[-1]['status']=='completed'
    assert events==(['research','analysis','delivery'] if kind=='movement' else ['research','analysis'])


@pytest.mark.parametrize('conflict',[False,True])
def test_completed_movement_retries_delivery_without_model_or_observation_overwrite(monkeypatch,tmp_path,conflict):
    from datetime import datetime
    connection=MagicMock()
    connection.__enter__.return_value=connection
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value={
        'status':'completed','etf_code':'OTHER' if conflict else '091160',
        'analysis_at':datetime.fromisoformat('2026-10-02T10:00:00+09:00'),'data_source':'database'}
    monkeypatch.setattr(worker,'connect_results',lambda *a,**k:connection)
    deliver=Mock(return_value=1)
    monkeypatch.setattr(worker,'enqueue_movement',deliver)
    publisher=Mock(side_effect=AssertionError('Must preserve original manifest'))
    monkeypatch.setattr(worker,'Publisher',publisher)
    execute=Mock(side_effect=AssertionError('Must not call model again'))
    monkeypatch.setattr(worker,'execute_request',execute)
    request={'kind':'movement','analysis_id':'one','etf_code':'091160','analysis_at':'2026-10-02T10:00:00+09:00'}
    def run():
        worker.run(request,bucket='test',ca_path=tmp_path/'ca',folder=tmp_path/'output',key='test',model='test',session=Mock())
    if conflict:
        with pytest.raises(ValueError,match='different request'):
            run()
        deliver.assert_not_called()
    else:
        run()
        deliver.assert_called_once_with(connection,'one')
    publisher.assert_not_called()
    execute.assert_not_called()
