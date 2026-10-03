"""Re-delivery must never buy another analysis or acknowledge untracked work."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from threading import Lock

from botocore.exceptions import ClientError, ReadTimeoutError
import pytest

from edge_analysis_v2.cloud.admission import WorkflowAdmission, RequestConflict
from edge_analysis_v2.cloud.contract import decode_request

REQUEST = dict(analysis_id='a'*32, kind='movement', etf_code='091160',
               analysis_at='2026-10-02T10:00:02+09:00')
MACHINE = 'arn:aws:states:ap-northeast-2:123:stateMachine:test'


class Workflows:
    def __init__(self):
        self.rows = {}
        self.lock = Lock()
        self.starts = 0
        self.lose_response = False

    def start_execution(self, *, stateMachineArn, name, input):
        with self.lock:
            if name not in self.rows:
                self.starts += 1
                self.rows[name] = {'input':input, 'status':'RUNNING'}
            elif self.rows[name]['input'] != input or self.rows[name]['status'] != 'RUNNING':
                raise ClientError({'Error':{'Code':'ExecutionAlreadyExists'}}, 'StartExecution')
        if self.lose_response:
            raise ReadTimeoutError(endpoint_url='https://states.example')
        return {'executionArn':stateMachineArn.replace(':stateMachine:', ':execution:')+':'+name}

    def describe_execution(self, *, executionArn):
        row = self.rows.get(executionArn.rsplit(':',1)[-1])
        if row is None:
            raise ClientError({'Error':{'Code':'ExecutionDoesNotExist'}}, 'DescribeExecution')
        return deepcopy(row)


class Requests:
    def __init__(self):
        self.rows = {}
        self.lock = Lock()
        self.fail_accept = False

    def reserve(self, request, execution_arn, input_json):
        with self.lock:
            row = self.rows.setdefault(request['analysis_id'], dict(input_json=input_json,
                execution_arn=execution_arn, created_at=datetime.now(timezone.utc), accepted_at=None))
            if (row['input_json'],row['execution_arn']) != (input_json,execution_arn):
                raise RequestConflict('Request identity conflict')
            return deepcopy(row)

    def accept(self, identity, execution_arn):
        if self.fail_accept:
            raise RuntimeError('Database unavailable')
        self.rows[identity]['accepted_at'] = datetime.now(timezone.utc)


@pytest.fixture
def admission():
    return WorkflowAdmission(Workflows(), MACHINE, requests=Requests())


def test_concurrent_delivery_starts_one_workflow_and_persists_acceptance(admission):
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _:admission.submit(REQUEST), range(10)))
    assert admission.workflows.starts == 1
    assert len({r['execution_arn'] for r in results}) == 1
    assert admission.requests.rows[REQUEST['analysis_id']]['accepted_at'] is not None


def test_same_id_different_input_cannot_reuse_or_launch(admission):
    admission.submit(REQUEST)
    with pytest.raises(RequestConflict):
        admission.submit(REQUEST | {'etf_code':'102110'})
    assert admission.workflows.starts == 1


def test_timeout_after_aws_acceptance_is_reconciled(admission):
    admission.workflows.lose_response = True
    admission.submit(REQUEST)
    assert admission.requests.rows[REQUEST['analysis_id']]['accepted_at']
    assert admission.workflows.starts == 1


def test_tracking_failure_prevents_ack_and_redelivery_repairs_it(admission):
    admission.requests.fail_accept = True
    with pytest.raises(RuntimeError):
        admission.submit(REQUEST)
    assert admission.requests.rows[REQUEST['analysis_id']]['accepted_at'] is None
    admission.requests.fail_accept = False
    admission.workflows.rows[REQUEST['analysis_id']]['status'] = 'FAILED'
    admission.submit(REQUEST)
    assert admission.requests.rows[REQUEST['analysis_id']]['accepted_at']
    assert admission.workflows.starts == 1


def test_reservation_failure_cannot_start_work(admission, monkeypatch):
    def fail(*args):
        raise RuntimeError('Database unavailable')
    monkeypatch.setattr(admission.requests, 'reserve', fail)
    with pytest.raises(RuntimeError):
        admission.submit(REQUEST)
    assert admission.workflows.starts == 0


def test_accepted_record_survives_aws_history_expiry(admission):
    original = admission.submit(REQUEST)
    admission.workflows.rows.clear()
    assert admission.submit(REQUEST)['execution_arn'] == original['execution_arn']
    assert admission.workflows.starts == 1


def test_old_unconfirmed_request_does_not_restart_after_name_expiry(admission):
    admission.requests.fail_accept = True
    with pytest.raises(RuntimeError):
        admission.submit(REQUEST)
    admission.requests.rows[REQUEST['analysis_id']]['created_at'] -= timedelta(days=90)
    admission.workflows.rows.clear()
    with pytest.raises(RuntimeError, match='reconciliation'):
        admission.submit(REQUEST)
    assert admission.workflows.starts == 1


def test_timezone_spelling_and_json_order_cannot_create_a_conflict(admission):
    admission.submit(REQUEST)
    admission.submit(dict(reversed(list(REQUEST.items()))) | {'analysis_at':'2026-10-02T01:00:02Z'})
    assert admission.workflows.starts == 1


def test_premarket_request_keeps_korean_trading_day_in_worker_input(admission):
    request = REQUEST | {'kind':'outlook','analysis_at':'2026-10-01T23:30:00Z'}
    admission.submit(request)
    supplied = json.loads(admission.workflows.rows[REQUEST['analysis_id']]['input'])
    assert supplied['analysis_at']=='2026-10-02T08:30:00+09:00'


def test_public_api_cannot_inject_an_internal_trigger():
    request = REQUEST | {'source':dict(event_id='event-1', event_type='PriceTriggerFired',
                                      trigger_id='original-trigger', generation=1)}
    with pytest.raises(ValueError):
        decode_request(json.dumps(request))
    assert decode_request(json.dumps(request), internal=True) == request
    with pytest.raises(ValueError):
        decode_request(json.dumps(request | {'kind':'outlook'}), internal=True)
