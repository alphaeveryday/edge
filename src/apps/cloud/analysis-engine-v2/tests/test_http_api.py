"""The consumer gets committed screens, never duplicate work or synthetic publications."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from botocore.exceptions import ClientError
import pytest

from edge_analysis_v2.api.service import AnalysisAPI

REQUEST = dict(analysis_id='a'*32, kind='outlook', etf_code='0177X0',
               analysis_at='2026-10-01T08:30:00+09:00')
NOW = datetime(2026,10,1,4,tzinfo=timezone.utc)


class Publications:
    def __init__(self):
        self.rows = {}
        self.body = {'summary':'원문', 'items':[], 'publication':{'etf_code':'0177X0'}}

    def find(self, identity):
        return self.rows.get(identity)

    def screen(self, kind, identity, feature):
        return deepcopy(self.body)

    def latest(self, code, kind):
        eligible = [r for r in self.rows.values() if r['kind']==kind and r['etf_code']==code
                    and r['status']=='completed' and r['data_source']=='database' and r['published_at']]
        return max(eligible,key=lambda r:(r['analysis_at'],r['published_at'],r['analysis_id'])) if eligible else None


class Workflows:
    def __init__(self):
        self.executions = {}
        self.starts = 0

    def start_execution(self, *, stateMachineArn, name, input):
        if name in self.executions:
            raise ClientError({'Error':{'Code':'ExecutionAlreadyExists'}},'StartExecution')
        self.starts += 1
        self.executions[name] = {'input':input, 'status':'RUNNING'}
        return {}

    def describe_execution(self, *, executionArn):
        key=executionArn.rsplit(':',1)[-1]
        if key not in self.executions:
            raise ClientError({'Error':{'Code':'ExecutionDoesNotExist'}},'DescribeExecution')
        return self.executions[key]


@pytest.fixture
def api():
    return AnalysisAPI(Publications(), Workflows(), 'arn:aws:states:ap-northeast-2:123:stateMachine:test', now=lambda:NOW)


def event(method='POST', path='/v2/analyses', body=None, authenticated=True):
    return {'requestContext':{'requestId':'req-1','http':{'method':method},
            'authorizer':{'iam':{'userArn':'arn:aws:iam::123:role/backend'}} if authenticated else {}},
            'rawPath':path, 'body':json.dumps(REQUEST if body is None else body), 'rawQueryString':''}


def row(**changes):
    return REQUEST | {'analysis_at':datetime.fromisoformat(REQUEST['analysis_at']),
        'status':'completed','data_source':'database','published_at':NOW} | changes


def test_new_request_and_retry_launch_exactly_one_workflow(api):
    first=api.handle(event())
    second=api.handle(event())
    assert first['statusCode']==202 and second['statusCode']==200
    assert api.workflows.starts==1
    assert json.loads(first['body'])['etf_code']=='0177X0'


def test_reusing_id_for_different_analysis_conflicts(api):
    api.handle(event())
    result=api.handle(event(body=REQUEST | {'kind':'movement'}))
    assert result['statusCode']==409 and api.workflows.starts==1


def test_existing_local_db_publication_does_not_launch_cloud_work(api):
    api.publications.rows[REQUEST['analysis_id']]=row()
    result=api.handle(event())
    assert result['statusCode']==200 and api.workflows.starts==0
    assert json.loads(result['body'])['status']=='completed'


@pytest.mark.parametrize('bad',[
    REQUEST | {'analysis_at':'2027-01-01T00:00:00+09:00'},
    REQUEST | {'analysis_at':'2026-10-01T08:30:00'},
    REQUEST | {'etf_code':'../../'}, REQUEST | {'etf_code':'0177x0'},
    REQUEST | {'image':'caller-command'},
])
def test_invalid_request_never_launches_billable_work(api,bad):
    assert api.handle(event(body=bad))['statusCode']==400
    assert api.workflows.starts==0


def test_unauthorized_duplicate_keys_and_oversized_input_fail_before_work(api):
    assert api.handle(event(authenticated=False))['statusCode']==403
    duplicate=event();duplicate['body']='{"kind":"outlook","kind":"movement"}'
    assert api.handle(duplicate)['statusCode']==400
    oversized=event();oversized['body']='x'*4097
    assert api.handle(oversized)['statusCode']==413
    assert api.workflows.starts==0


def test_completed_database_wins_over_failed_observation_workflow(api):
    api.handle(event())
    api.workflows.executions[REQUEST['analysis_id']]['status']='FAILED'
    api.publications.rows[REQUEST['analysis_id']]=row()
    result=api.handle(event('GET',f"/v2/analyses/outlook/{REQUEST['analysis_id']}"))
    assert json.loads(result['body'])['status']=='completed'


def test_stopped_worker_is_not_reported_as_running_forever(api):
    api.handle(event())
    api.publications.rows[REQUEST['analysis_id']]=row(status='running',published_at=None)
    api.workflows.executions[REQUEST['analysis_id']]['status']='TIMED_OUT'
    result=api.handle(event('GET',f"/v2/analyses/outlook/{REQUEST['analysis_id']}"))
    assert json.loads(result['body'])['status']=='failed'


def test_queued_screen_and_unknown_id_are_distinct(api):
    api.handle(event())
    path=f"/v2/analyses/outlook/{REQUEST['analysis_id']}/screens/all"
    assert api.handle(event('GET',path))['statusCode']==409
    assert api.handle(event('GET',path.replace('a'*32,'b'*32)))['statusCode']==404


def test_screen_body_preserves_contract_without_transport_wrapper(api):
    api.publications.rows[REQUEST['analysis_id']]=row()
    result=api.handle(event('GET',f"/v2/analyses/outlook/{REQUEST['analysis_id']}/screens/all"))
    assert result['statusCode']==200
    assert json.loads(result['body'])==api.publications.body
    assert result['headers']['X-Analysis-Id']==REQUEST['analysis_id']


def test_latest_ignores_new_running_failed_and_synthetic_rows(api):
    api.publications.rows['a'*32]=row()
    for n,status,source in [('b','running','database'),('c','failed','database'),('d','completed','synthetic')]:
        api.publications.rows[n*32]=row(analysis_id=n*32,analysis_at=NOW,status=status,data_source=source)
    result=api.handle(event('GET','/v2/etfs/0177X0/analyses/outlook/latest/screens/all'))
    assert result['headers']['X-Analysis-Id']=='a'*32


@pytest.mark.parametrize('path',[
    '/v2/analyses/movement/'+('a'*32)+'/screens/factors',
    '/v2/analyses/outlook/'+('a'*32)+'/screens/private',
    '/v2/etfs/bad/analyses/outlook/latest/screens/all',
])
def test_unsupported_routes_are_rejected(api,path):
    assert api.handle(event('GET',path))['statusCode']==400


def test_dependency_errors_do_not_leak_secrets(api):
    def fail(identity):
        raise RuntimeError('password=do-not-expose SELECT private_table')
    api.publications.find=fail
    result=api.handle(event())
    assert result['statusCode']==503
    assert 'do-not-expose' not in result['body'] and 'private_table' not in result['body']


def test_synthetic_publication_cannot_be_served_by_consumer_api(api):
    api.publications.rows['a'*32]=row(data_source='synthetic')
    assert api.handle(event('GET','/v2/analyses/outlook/'+('a'*32)))['statusCode']==404
    assert api.handle(event())['statusCode']==409
