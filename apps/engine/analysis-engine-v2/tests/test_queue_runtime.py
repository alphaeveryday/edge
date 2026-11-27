"""The transport variable must leave the AWS consumer exactly as it was unless it says ``db``."""
import subprocess
import sys
from threading import Thread
from unittest.mock import Mock

import pytest

from edge_analysis_v2.cloud import queue_runtime


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """main() with every network client replaced; records what it would have served."""
    session = Mock()
    monkeypatch.setattr(queue_runtime.boto3, 'Session', lambda **_: session)
    monkeypatch.setattr(queue_runtime.signal, 'signal', lambda *_: None)
    monkeypatch.setenv('AWS_REGION', 'ap-northeast-2')
    monkeypatch.setenv('RDS_CA_PATH', str(tmp_path))
    monkeypatch.setenv('STATE_MACHINE_ARN', 'arn:aws:states:ap-northeast-2:123:stateMachine:test')
    monkeypatch.setenv('PRICE_QUEUE_URL', 'https://sqs.test/explain')
    for name in ('ANALYSIS_QUEUE_TRANSPORT', 'ANALYSIS_SLOTS'):
        monkeypatch.delenv(name, raising=False)
    served = {}
    def serve(queue, queue_url, load, admission, stop, retract):
        served.update(queue=queue, queue_url=queue_url, admission=admission, stop=stop)
        if served.get('fail'):
            raise RuntimeError('admission loop crashed')
        stop.set()
    monkeypatch.setattr(queue_runtime, 'serve', serve)
    return session, served


@pytest.mark.parametrize('value', [None, 'sqs'])
def test_without_db_the_consumer_is_the_sqs_and_workflow_one(runtime, monkeypatch, value):
    """AWS runs this image until the account closes; an unset variable must not change what it talks to."""
    session, served = runtime
    if value:
        monkeypatch.setenv('ANALYSIS_QUEUE_TRANSPORT', value)
    queue_runtime.main()
    assert {call.args[0] for call in session.client.call_args_list} == {'stepfunctions', 'sqs'}
    assert served['queue_url'] == 'https://sqs.test/explain'
    assert type(served['admission']).__name__ == 'WorkflowAdmission'


def test_importing_the_runtime_does_not_load_the_local_runner():
    """The runner drags in the worker and the model SDK (about 40 MB more); the AWS consumer has 512 MB.
    A fresh interpreter, because this test session has already imported both."""
    code = ("import sys, edge_analysis_v2.cloud.queue_runtime; "
            "sys.exit(any(name in sys.modules for name in "
            "('edge_analysis_v2.cloud.local', 'edge_analysis_v2.cloud.worker')))")
    assert subprocess.run([sys.executable, '-c', code]).returncode == 0


def test_db_serves_the_outbox_and_calls_no_aws_queue_or_workflow(runtime, monkeypatch):
    session, served = runtime
    monkeypatch.setenv('ANALYSIS_QUEUE_TRANSPORT', 'db')
    monkeypatch.setenv('ANALYSIS_SLOTS', '2')
    queue_runtime.main()
    session.client.assert_not_called()
    assert served['queue_url'] == 'price-explanation-realtime'
    assert (type(served['queue']).__name__, type(served['admission']).__name__) == ('OutboxQueue', 'LocalAdmission')


def test_a_crashed_admission_loop_stops_the_runner_instead_of_hanging(runtime, monkeypatch):
    """The runner thread is not a daemon; without the stop signal the process would never exit to be restarted."""
    _, served = runtime
    served['fail'] = True
    monkeypatch.setenv('ANALYSIS_QUEUE_TRANSPORT', 'db')
    monkeypatch.setenv('ANALYSIS_SLOTS', '2')
    raised = []
    def run():
        try:
            queue_runtime.main()
        except RuntimeError as error:
            raised.append(str(error))
    # On its own thread with a deadline: the regression this guards is main() never returning.
    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(10)
    hung = thread.is_alive()
    stopped = served['stop'].is_set()
    served['stop'].set()   # release the runner thread whatever happened
    assert not hung and stopped
    assert raised == ['admission loop crashed']


@pytest.mark.parametrize('environment', [{'ANALYSIS_QUEUE_TRANSPORT': 'kafka'},
                                         {'ANALYSIS_QUEUE_TRANSPORT': 'db'},                       # no capacity
                                         {'ANALYSIS_QUEUE_TRANSPORT': 'db', 'ANALYSIS_SLOTS': '0'}])
def test_a_misconfigured_transport_refuses_to_start(runtime, monkeypatch, environment):
    _, served = runtime
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises((ValueError, KeyError)):
        queue_runtime.main()
    assert not served
