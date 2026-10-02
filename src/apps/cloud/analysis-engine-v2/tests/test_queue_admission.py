"""A queue receipt may disappear only after durable workflow acceptance."""
from threading import Event
from unittest.mock import Mock

from botocore.exceptions import ReadTimeoutError
import pytest

from edge_analysis_v2.cloud.consumer import poll_once, serve
from test_workflow_admission import MACHINE, REQUEST as BASE_REQUEST, Requests, Workflows
from edge_analysis_v2.cloud.admission import WorkflowAdmission

REQUEST = BASE_REQUEST | {'source':dict(event_id='event-1',event_type='PriceTriggerFired',
                                       trigger_id='original',generation=1)}


@pytest.fixture
def queue():
    sqs = Mock()
    sqs.receive_message.return_value = {'Messages':[
        {'Body':'original relay JSON', 'ReceiptHandle':'receipt-1'}]}
    store, aws = Requests(), Workflows()
    admission = WorkflowAdmission(aws, MACHINE, requests=store)
    loader = Mock(return_value=REQUEST)
    return sqs, loader, admission, Event()


def poll(queue):
    sqs, loader, admission, stop = queue
    return poll_once(sqs, 'queue-url', loader, admission, stop)


@pytest.mark.parametrize('fails', [False, True])
def test_reversion_is_persisted_before_ack_without_starting_model_workflow(queue, fails):
    sqs, loader, admission, stop = queue
    event = {'event_id':'revert-1', 'event_type':'ExposureReverted', 'payload':{}}
    loader.return_value = event
    retract = Mock(side_effect=RuntimeError('Database unavailable') if fails else None)
    if fails:
        with pytest.raises(RuntimeError):
            poll_once(sqs, 'queue-url', loader, admission, stop, retract)
        sqs.delete_message.assert_not_called()
    else:
        poll_once(sqs, 'queue-url', loader, admission, stop, retract)
        sqs.delete_message.assert_called_once()
    retract.assert_called_once_with(event)
    assert admission.workflows.starts == 0


def test_ack_observes_committed_admission_without_waiting_for_analysis(queue):
    sqs, loader, admission, _ = queue
    def delete(**kwargs):
        assert admission.requests.rows[REQUEST['analysis_id']]['accepted_at']
        assert admission.workflows.rows[REQUEST['analysis_id']]['status']=='RUNNING'
        assert kwargs=={'QueueUrl':'queue-url','ReceiptHandle':'receipt-1'}
    sqs.delete_message.side_effect = delete
    poll(queue)
    loader.assert_called_once_with('original relay JSON')
    sqs.receive_message.assert_called_once_with(QueueUrl='queue-url',MaxNumberOfMessages=1,WaitTimeSeconds=20)
    sqs.delete_message.assert_called_once()


@pytest.mark.parametrize('failure',['source','reserve','aws','record'])
def test_unconfirmed_work_is_not_deleted(queue,failure):
    sqs, loader, admission, _ = queue
    if failure=='source': loader.side_effect=ValueError('private body')
    if failure=='reserve': admission.requests.reserve=Mock(side_effect=RuntimeError('DB password'))
    if failure=='aws': admission.workflows.start_execution=Mock(side_effect=ReadTimeoutError(endpoint_url='private'))
    if failure=='record': admission.requests.fail_accept=True
    with pytest.raises(Exception): poll(queue)
    sqs.delete_message.assert_not_called()


def test_ack_response_loss_reuses_execution_and_new_receipt(queue):
    sqs, _, admission, _ = queue
    sqs.delete_message.side_effect=ReadTimeoutError(endpoint_url='private')
    with pytest.raises(ReadTimeoutError): poll(queue)
    sqs.delete_message.side_effect=None
    sqs.receive_message.return_value['Messages'][0]['ReceiptHandle']='receipt-2'
    poll(queue)
    assert admission.workflows.starts==1
    sqs.delete_message.assert_called_with(QueueUrl='queue-url',ReceiptHandle='receipt-2')


def test_aws_response_loss_is_reconciled_before_ack(queue):
    sqs, _, admission, _ = queue
    admission.workflows.lose_response=True
    poll(queue)
    assert admission.workflows.starts==1
    sqs.delete_message.assert_called_once()


def test_empty_queue_has_no_other_side_effects(queue):
    sqs, loader, _, _ = queue
    sqs.receive_message.return_value={}
    poll(queue)
    loader.assert_not_called()
    sqs.delete_message.assert_not_called()


@pytest.mark.parametrize('loaded',[BASE_REQUEST,BASE_REQUEST | {'kind':'outlook'}])
def test_loader_cannot_turn_queue_events_into_unrelated_requests(queue,loaded):
    sqs, loader, admission, _ = queue
    loader.return_value=loaded
    with pytest.raises(ValueError):poll(queue)
    assert admission.workflows.starts==0
    sqs.delete_message.assert_not_called()


def test_shutdown_after_receive_leaves_receipt_for_redelivery(queue):
    sqs, loader, _, stop = queue
    def receive(**kwargs):
        stop.set()
        return {'Messages':[{'Body':'event','ReceiptHandle':'receipt-1'}]}
    sqs.receive_message.side_effect=receive
    poll(queue)
    loader.assert_not_called()
    sqs.delete_message.assert_not_called()


def test_shutdown_during_admission_finishes_ack(queue):
    sqs, loader, _, stop = queue
    def load(body):
        stop.set()
        return REQUEST
    loader.side_effect=load
    poll(queue)
    sqs.delete_message.assert_called_once()


def test_without_durable_store_no_message_is_received(queue):
    sqs, _, admission, _ = queue
    admission.requests=None
    with pytest.raises(ValueError):poll(queue)
    sqs.receive_message.assert_not_called()


def test_receive_failure_backs_off_and_never_logs_body_or_error_details(queue,caplog):
    sqs, loader, admission, _ = queue
    sqs.receive_message.side_effect=RuntimeError('secret-value')
    stop=Event(); stop.wait=Mock(side_effect=lambda _:stop.set())
    serve(sqs,'queue-url',loader,admission,stop)
    stop.wait.assert_called_once_with(5)
    assert 'RuntimeError' in caplog.text and 'secret-value' not in caplog.text
    loader.assert_not_called()


def test_unsupported_event_is_retained_and_next_message_can_be_processed(queue,caplog):
    sqs, loader, admission, _ = queue
    stop=Event(); stop.wait=Mock()
    def load(body):
        if loader.call_count==1:
            raise ValueError('ExposureReverted private body')
        stop.set()
        return REQUEST
    loader.side_effect=load
    serve(sqs,'queue-url',loader,admission,stop)
    assert loader.call_count==2
    sqs.delete_message.assert_called_once()
    assert 'private body' not in caplog.text
