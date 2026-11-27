"""Without Step Functions and ECS, this host must bound what runs and never run a request twice."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
from threading import Event

import pytest

from edge_analysis_v2.cloud import local
from edge_analysis_v2.cloud.local import LOCAL, LocalAdmission, LocalRunner, OutboxQueue, TASK_SECONDS
from edge_analysis_v2.cloud.worker import model_secret

NOW = datetime(2026, 12, 1, 1, 0, tzinfo=timezone.utc)   # 10:00 KST


def request(index, etf=None, at='2026-12-01T09:30:00+09:00'):
    return dict(analysis_id=f'{index:032x}', kind='movement', etf_code=etf or f'{index:06d}', analysis_at=at)


class Store:
    """Pending requests in admission order, read the way LocalRunner reads them."""

    def __init__(self, *requests):
        self.rows = {item['analysis_id']: {'raw': json.dumps(item), 'accepted': False} for item in requests}
        self.fail_accept = False

    @contextmanager
    def connect(self):
        yield self

    def execute(self, statement, args=()):
        assert 'accepted_at IS NULL' in statement and "LIKE 'local:%%'" in statement and 'ORDER BY created_at' in statement
        return self

    def fetchall(self):
        return [(identity, row['raw']) for identity, row in self.rows.items() if not row['accepted']]

    def accept(self, identity, arn):
        assert arn == LOCAL + identity
        if self.fail_accept:
            raise RuntimeError('database unavailable')
        self.rows[identity]['accepted'] = True


class Process:
    def __init__(self):
        self.code, self.killed, self.terminated = None, False, False

    def poll(self):
        return self.code

    def kill(self):
        self.killed, self.code = True, -9

    def terminate(self):
        self.terminated = True


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(local, 'ARTIFACTS', tmp_path)
    started, clock = [], [0.0]

    def make(store, slots=2):
        def spawn(raw, capacity):
            assert capacity == slots
            started.append((json.loads(raw)['analysis_id'], Process()))
            return started[-1][1]
        return LocalRunner(store.connect, store, slots, spawn=spawn, clock=lambda: clock[0], now=lambda: NOW)
    make.started, make.clock = started, clock
    return make


def test_only_the_configured_number_run_at_once_and_the_oldest_go_first(run):
    """The host has about one CPU; a burst of sixty events must not start sixty model processes."""
    store = Store(*(request(index) for index in range(5)))
    runner = run(store)
    runner.tick()
    runner.tick()
    assert [identity for identity, _ in run.started] == [request(0)['analysis_id'], request(1)['analysis_id']]

    run.started[0][1].code = 0
    runner.tick()
    assert [identity for identity, _ in run.started][2:] == [request(2)['analysis_id']]
    assert len(runner.running) == 2


@pytest.mark.parametrize('code', [0, 1, -9])
def test_a_request_gets_one_process_whatever_its_exit(run, code):
    """A request that crashes the worker every time must not be started again forever."""
    store = Store(request(1))
    runner = run(store, slots=1)
    runner.tick()
    run.started[0][1].code = code
    for _ in range(3):
        runner.tick()
    assert len(run.started) == 1
    assert store.rows[request(1)['analysis_id']]['accepted']


def test_a_finished_process_is_not_rerun_while_its_completion_cannot_be_recorded(run):
    store = Store(request(1))
    runner = run(store, slots=1)
    runner.tick()
    run.started[0][1].code = 0
    store.fail_accept = True
    with pytest.raises(RuntimeError):
        runner.tick()
    store.fail_accept = False
    runner.tick()
    assert len(run.started) == 1 and store.rows[request(1)['analysis_id']]['accepted']


def test_a_second_request_for_a_running_etf_waits_instead_of_failing(run):
    """The worker refuses a concurrent analysis of one ETF; started early it would end as a failure."""
    store = Store(request(1, etf='091160'), request(2, etf='091160'), request(3, etf='069500'))
    runner = run(store)
    runner.tick()
    assert [identity for identity, _ in run.started] == [request(1)['analysis_id'], request(3)['analysis_id']]
    run.started[0][1].code = 0
    runner.tick()
    assert run.started[2][0] == request(2)['analysis_id']


def test_a_request_from_an_earlier_day_is_closed_without_analysis(run, caplog):
    """The host stops each evening; yesterday's backlog must not be explained as today's move."""
    stale = request(1, at='2026-11-30T15:20:00+09:00')
    store = Store(stale, request(2))
    runner = run(store)
    with caplog.at_level(logging.WARNING):
        runner.tick()
    assert [identity for identity, _ in run.started] == [request(2)['analysis_id']]
    assert store.rows[stale['analysis_id']]['accepted']
    assert stale['analysis_id'] in caplog.text   # dropped work is reported, never silent


def test_a_process_past_the_task_limit_is_stopped_and_its_slot_returns(run):
    store = Store(request(1), request(2))
    runner = run(store, slots=1)
    runner.tick()
    run.clock[0] = TASK_SECONDS + 1
    runner.tick()
    assert run.started[0][1].killed
    runner.tick()
    assert run.started[1][0] == request(2)['analysis_id']


def test_the_folder_of_a_finished_analysis_is_removed(run, tmp_path):
    """An ECS task took its files away when it stopped; a host that keeps running fills its disk."""
    store = Store(request(1))
    runner = run(store, slots=1)
    runner.tick()
    folder = tmp_path / request(1)['analysis_id']
    folder.mkdir()
    (folder / 'events.jsonl').write_text('x')
    run.started[0][1].code = 0
    runner.tick()
    assert not folder.exists()


def test_shutdown_stops_children_and_leaves_their_requests_pending(run):
    store = Store(request(1))
    runner = run(store, slots=1)
    runner.tick()
    stop = Event()
    stop.set()
    runner.serve(stop)
    assert run.started[0][1].terminated
    assert not store.rows[request(1)['analysis_id']]['accepted']


@pytest.mark.parametrize('slots', [0, 41, '2', True])
def test_capacity_must_be_a_small_positive_integer(slots):
    with pytest.raises(ValueError):
        LocalRunner(None, None, slots)


def test_the_worker_child_uses_its_lock_slots_not_a_workflow_slot(monkeypatch):
    """With ANALYSIS_EXECUTION_ARN the worker publishes only while a workflow slot row exists; none will."""
    seen = {}
    monkeypatch.setenv('ANALYSIS_EXECUTION_ARN', 'arn:aws:states:leftover')
    monkeypatch.setattr(local.subprocess, 'Popen', lambda command, env: seen.update(command=command, env=env))
    local.spawn_worker('{"analysis_id":"x"}', 3)
    assert seen['command'][1:] == ['-m', 'edge_analysis_v2.cloud.worker']
    assert 'ANALYSIS_EXECUTION_ARN' not in seen['env']
    assert (seen['env']['ANALYSIS_REQUEST'], seen['env']['ANALYSIS_SLOTS']) == ('{"analysis_id":"x"}', '3')


def test_the_worker_child_inherits_only_what_it_reads(monkeypatch):
    """The child runs a model with file-reading tools; it must not hold credentials of other services."""
    seen = {}
    needed = {'DEEPSEEK_API_KEY': 'k', 'OBSERVATION_BUCKET': 'b', 'RDS_CA_PATH': '/ca', 'PATH': '/bin',
              'AWS_ENDPOINT_URL_S3': 'http://minio', 'AWS_SECRET_ACCESS_KEY': 's',
              'EDGE_ANALYSIS_DB_HOST': 'db', 'EDGE_ANALYSIS_DB_WRITER_PASSWORD': 'w'}
    foreign = {'DATA_PIPELINE_DB__PASSWORD': 'owner', 'DATA_PIPELINE_KIS_NAV__SOURCE__APP_SECRET': 'kis',
               'DATA_PIPELINE_DART_DISCLOSURE__SOURCE__API_KEY': 'dart', 'LLM_API_KEY': 'tagger'}
    for name, value in (needed | foreign).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(local.subprocess, 'Popen', lambda command, env: seen.update(env=env))
    local.spawn_worker('{}', 2)
    assert needed.items() <= seen['env'].items()
    assert not foreign.keys() & seen['env'].keys()


def test_admission_records_the_request_without_marking_it_run():
    class Requests:
        def reserve(self, *args):
            self.reserved = args
    requests = Requests()
    item = request(1)
    result = LocalAdmission(requests).submit(item)
    assert requests.reserved[1] == result['execution_arn'] == LOCAL + item['analysis_id']
    assert json.loads(requests.reserved[2])['analysis_id'] == item['analysis_id']
    assert not hasattr(requests, 'accepted')


def test_an_empty_poll_waits_instead_of_spinning():
    """cloud.consumer calls receive in a tight loop; SQS long polling used to do the waiting."""
    class Connection:
        @contextmanager
        def connect(self):
            yield self
        def execute(self, *args):
            return self
        def fetchall(self):
            return []
    class Stop:
        def wait(self, seconds):
            self.waited = seconds
    stop = Stop()
    assert OutboxQueue(Connection().connect, stop).receive_message(
        QueueUrl=local.DESTINATION, MaxNumberOfMessages=1, WaitTimeSeconds=20) == {'Messages': []}
    assert stop.waited == local.POLL_SECONDS


def test_model_credentials_come_from_the_environment_or_the_secret_never_a_mix():
    class Session:
        def client(self, name):
            assert name == 'secretsmanager'
            return self
        def get_secret_value(self, SecretId):
            assert SecretId == 'arn:secret'
            return {'SecretString': json.dumps({'DEEPSEEK_API_KEY': 'from-secret', 'DEEPSEEK_MODEL': 'secret-model'})}
    assert model_secret(Session(), {'DEEPSEEK_SECRET_ARN': 'arn:secret'}) == {
        'DEEPSEEK_API_KEY': 'from-secret', 'DEEPSEEK_MODEL': 'secret-model'}
    # No Secrets Manager call at all once the key is in the environment.
    assert model_secret(None, {'DEEPSEEK_API_KEY': 'from-env', 'TINYFISH_API_KEY': 'web',
                               'DEEPSEEK_SECRET_ARN': 'arn:secret'}) == {
        'DEEPSEEK_API_KEY': 'from-env', 'TINYFISH_API_KEY': 'web'}
