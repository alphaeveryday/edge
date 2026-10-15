"""A local observer never needs model keys or direct database credentials."""
import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from edge_analysis_v2.cloud.dashboard import CloudDashboard
from edge_analysis_v2.cloud.artifacts import Publisher
from test_cloud_observation import MemoryS3, REQUEST


def test_sync_reconstructs_existing_dashboard_files_and_survives_restart(tmp_path):
    source=tmp_path/'worker'
    source.mkdir()
    (source/'input.json').write_text('{"columns":["close"],"rows":[[18]]}')
    (source/'evidence.json').write_text('{"tool_runs":[{"output":{"result":{"value":18}}}]}')
    s3=MemoryS3()
    job=REQUEST | {'origin':'cloud','status':'running','started_at':REQUEST['analysis_at'],'scenario':'database'}
    Publisher(s3,'bucket',REQUEST['analysis_id'],source).publish(job)
    s3.get_paginator=Mock()
    s3.get_paginator.return_value.paginate.return_value=[{'Contents':[{'Key':k,'ETag':'one'} for k in s3.objects if k.endswith('manifest.json')]}]
    sfn=Mock()
    sfn.list_executions.return_value={'executions':[]}
    observer=CloudDashboard(tmp_path/'local',s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    observer.sync()
    assert observer.jobs()[0]['status']=='running'
    assert json.loads(observer.detail(REQUEST['analysis_id'])['artifacts']['input.json'])['rows']==[[18]]
    assert observer.read(REQUEST['kind'],REQUEST['analysis_id'])['tool_runs'][0]['output']['result']['value']==18
    restarted=CloudDashboard(tmp_path/'local',s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    assert restarted.jobs()[0]['status']=='running'
    assert observer.start(REQUEST)['status']=='running'
    sfn.start_execution.assert_not_called()


def test_start_validates_before_aws_and_keeps_exact_request_id(tmp_path):
    sfn=Mock()
    sfn.start_execution.return_value={'executionArn':'arn:execution:test'}
    observer=CloudDashboard(tmp_path,s3=Mock(),sfn=sfn,bucket='bucket',state_machine='arn')
    with pytest.raises(ValueError):
        observer.start(REQUEST | {'command':'injected'})
    sfn.start_execution.assert_not_called()
    job=observer.start(REQUEST)
    assert job['status']=='queued'
    assert json.loads(sfn.start_execution.call_args.kwargs['input'])==REQUEST
    assert sfn.start_execution.call_args.kwargs['name']==REQUEST['analysis_id']
    assert observer.start(REQUEST)==job
    assert sfn.start_execution.call_count==1
    with pytest.raises(ValueError):
        observer.start(REQUEST | {'kind':'movement'})


def test_failed_launch_on_later_page_is_visible_without_any_s3_artifacts(tmp_path):
    s3=Mock()
    s3.get_paginator.return_value.paginate.return_value=[]
    sfn=Mock()
    arn='arn:execution:'+REQUEST['analysis_id']
    sfn.list_executions.side_effect=[{'executions':[],'nextToken':'page2'}, {'executions':[{'executionArn':arn}]}]
    sfn.describe_execution.return_value={'status':'FAILED','input':json.dumps(REQUEST),'startDate':datetime.now(timezone.utc)}
    observer=CloudDashboard(tmp_path,s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    observer.sync()
    assert observer.jobs()[0]['status']=='interrupted'
    assert observer.jobs()[0]['observation_status']=='incomplete'
    assert sfn.list_executions.call_args.kwargs['nextToken']=='page2'


def test_local_auth_failure_does_not_change_cloud_job_state(tmp_path):
    s3,sfn=Mock(),Mock()
    sfn.start_execution.return_value={'executionArn':'arn:execution:'+REQUEST['analysis_id']}
    observer=CloudDashboard(tmp_path,s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    before=observer.start(REQUEST)
    s3.get_paginator.side_effect=RuntimeError('credentials unavailable')
    observer.sync()
    assert observer.sync_status['status']=='error'
    assert observer.jobs()==[before]


def test_a_price_triggered_execution_does_not_stop_synchronization_of_the_others(tmp_path):
    # WHY: trigger-started movement runs carry the worker's source coordinates in their workflow input. Rejecting
    # that input as an invalid public request ended the whole sync, hiding every execution listed after it.
    s3=Mock()
    s3.get_paginator.return_value.paginate.return_value=[]
    triggered=REQUEST | {'analysis_id':'b'*32,'kind':'movement',
        'source':{'event_id':'evt-1','event_type':'PriceTriggerFired','trigger_id':'trg-1','generation':1}}
    inputs={'arn:execution:'+triggered['analysis_id']:triggered,'arn:execution:'+REQUEST['analysis_id']:REQUEST}
    sfn=Mock()
    sfn.list_executions.return_value={'executions':[{'executionArn':arn} for arn in inputs]}
    sfn.describe_execution.side_effect=lambda executionArn:{'status':'FAILED','input':json.dumps(inputs[executionArn]),
        'startDate':datetime.now(timezone.utc)}
    observer=CloudDashboard(tmp_path,s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    observer.sync()
    assert observer.sync_status['status']!='error'
    assert {job['analysis_id'] for job in observer.jobs()}=={triggered['analysis_id'],REQUEST['analysis_id']}
    with pytest.raises(ValueError):
        observer.start(triggered)   # the public start endpoint still refuses source coordinates


def test_one_damaged_observation_is_reported_without_hiding_the_runs_after_it(tmp_path):
    # WHY: the damaged manifest is listed first on every poll; stopping there made all later runs invisible for good.
    source=tmp_path/'worker'
    source.mkdir()
    s3=MemoryS3()
    job=REQUEST | {'origin':'cloud','status':'running','started_at':REQUEST['analysis_at'],'scenario':'database'}
    Publisher(s3,'bucket',REQUEST['analysis_id'],source).publish(job)
    good=next(k for k in s3.objects if k.endswith('manifest.json'))
    bad=good.replace(REQUEST['analysis_id'],'0'*32)
    s3.objects[bad]=b'{not json'
    s3.get_paginator=Mock()
    s3.get_paginator.return_value.paginate.return_value=[{'Contents':[{'Key':bad,'ETag':'x'},{'Key':good,'ETag':'one'}]}]
    sfn=Mock()
    sfn.list_executions.return_value={'executions':[]}
    observer=CloudDashboard(tmp_path/'local',s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    observer.sync()
    assert [j['analysis_id'] for j in observer.jobs()]==[REQUEST['analysis_id']]
    assert observer.sync_status['status']=='error' and observer.sync_status['error'].startswith('1 run observation')
    sfn.list_executions.assert_called()


def test_a_run_record_without_a_start_time_is_rejected_instead_of_breaking_every_listing(tmp_path):
    source=tmp_path/'worker'
    source.mkdir()
    s3=MemoryS3()
    job=REQUEST | {'origin':'cloud','status':'running','scenario':'database'}
    Publisher(s3,'bucket',REQUEST['analysis_id'],source).publish(job | {'started_at':REQUEST['analysis_at']})
    key=next(k for k in s3.objects if k.endswith('manifest.json'))
    manifest=json.loads(s3.objects[key])
    del manifest['job']['started_at']
    s3.objects[key]=json.dumps(manifest).encode()
    s3.get_paginator=Mock()
    s3.get_paginator.return_value.paginate.return_value=[{'Contents':[{'Key':key,'ETag':'one'}]}]
    sfn=Mock()
    sfn.list_executions.return_value={'executions':[]}
    observer=CloudDashboard(tmp_path/'local',s3=s3,sfn=sfn,bucket='bucket',state_machine='arn')
    observer.sync()
    assert observer.jobs()==[] and observer.sync_status['status']=='error'
    assert key not in observer.etags
