"""Concurrent requests wait without starting another billable analysis task."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import Mock

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.cloud.control import SETTLE, control


@pytest.fixture
def capacity():
    dsn = os.environ['V2_TEST_DSN']
    args = conninfo_to_dict(dsn)
    if (args.get('host'), args.get('port'), args.get('dbname')) != ('127.0.0.1', '55439', 'analysis_v2'):
        raise ValueError('Requires isolated local analysis_v2 database')
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute('TRUNCATE analysis_execution_slots')
        conn.execute('SET ROLE edge_analysis_v2_writer')
        workflows = Mock()
        workflows.describe_execution.return_value = {'status': 'RUNNING'}
        workflows.get_paginator.return_value.paginate.return_value = [{'events': []}]
        ecs = Mock()
        workflows.get_paginator.return_value.paginate.return_value = [{'events': []}]
        yield conn, dsn, workflows, ecs
        conn.execute('DELETE FROM analysis_execution_slots')


def test_capacity_is_shared_and_same_owner_retry_is_idempotent(capacity):
    conn, dsn, workflows, ecs = capacity
    def acquire(arn):
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute('SET ROLE edge_analysis_v2_writer')
            return arn, control(other, workflows, ecs, 'cluster', arn, 'acquire')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ['movement', 'outlook']))
    owners = [arn for arn, result in results if result['acquired']]
    assert len(owners) == 1
    assert control(conn, workflows, ecs, 'cluster', owners[0], 'acquire')['acquired']
    assert conn.execute('SELECT count(*) FROM analysis_execution_slots').fetchone()[0] == 1


def test_aborted_workflow_does_not_release_capacity_until_ecs_task_stops(capacity):
    conn, _, workflows, ecs = capacity
    assert control(conn, workflows, ecs, 'cluster', 'old', 'acquire')['acquired']
    workflows.describe_execution.return_value = {'status': 'ABORTED'}
    workflows.get_paginator.return_value.paginate.return_value = [{'events': [{'taskSubmittedEventDetails': {'resourceType': 'ecs', 'resource': 'runTask.sync', 'output': json.dumps({'Tasks': [{'TaskArn': 'task'}]})}}]}]
    ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'RUNNING'}]}
    assert not control(conn, workflows, ecs, 'cluster', 'new', 'acquire')['acquired']
    ecs.stop_task.assert_called_once()
    workflows.get_paginator.return_value.paginate.return_value = [{'events': []}]
    assert not control(conn, workflows, ecs, 'cluster', 'new', 'acquire')['acquired']
    ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'STOPPED'}]}
    assert control(conn, workflows, ecs, 'cluster', 'new', 'acquire')['acquired']


def test_release_is_idempotent_and_aws_failure_cannot_free_live_slot(capacity):
    conn, _, workflows, ecs = capacity
    assert control(conn, workflows, ecs, 'cluster', 'one', 'acquire')['acquired']
    workflows.describe_execution.side_effect = RuntimeError('AWS unavailable')
    # The unjudgeable slot stays occupied and still fills the capacity of one, without failing the caller.
    result = control(conn, workflows, ecs, 'cluster', 'two', 'acquire')
    assert not result['acquired'] and result['held'] == [{'execution_arn': 'one', 'reason': 'check-failed:RuntimeError'}]
    assert conn.execute('SELECT execution_arn FROM analysis_execution_slots').fetchall() == [('one',)]
    workflows.describe_execution.side_effect = None
    control(conn, workflows, ecs, 'cluster', 'one', 'release')
    control(conn, workflows, ecs, 'cluster', 'one', 'release')
    assert control(conn, workflows, ecs, 'cluster', 'two', 'acquire')['acquired']


def test_three_slots_still_serialize_the_same_etf_and_kind(capacity):
    conn, _, workflows, ecs = capacity
    assert control(conn, workflows, ecs, 'cluster', 'a', 'acquire', slots=3, request_key='movement:091160')['acquired']
    assert not control(conn, workflows, ecs, 'cluster', 'b', 'acquire', slots=3, request_key='movement:091160')['acquired']
    assert control(conn, workflows, ecs, 'cluster', 'c', 'acquire', slots=3, request_key='movement:069500')['acquired']


# ── Reclaim needs evidence that the old task stopped (ALPHA-1166) ────────────────────────────────
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _run_task(kind, at, **details):
    key = {'TaskScheduled': 'taskScheduledEventDetails', 'TaskStarted': 'taskStartedEventDetails',
           'TaskSubmitted': 'taskSubmittedEventDetails', 'TaskFailed': 'taskFailedEventDetails'}[kind]
    return {'type': kind, 'timestamp': at, key: {'resourceType': 'ecs', 'resource': 'runTask.sync', **details}}


def submitted(arn, at):
    return _run_task('TaskSubmitted', at, output=json.dumps({'Tasks': [{'TaskArn': arn}]}))


def stopped_record(arn, at):
    return _run_task('TaskFailed', at, error='States.TaskFailed', cause=json.dumps({'TaskArn': arn, 'LastStatus': 'STOPPED'}))


def leftover(conn, workflows, *events, status='ABORTED', key='outlook:069500'):
    """A finished execution whose slot was never released, with the given history."""
    conn.execute("INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES ('old','old',%s)", (key,))
    workflows.describe_execution.side_effect = lambda executionArn: {'status': status if executionArn == 'old' else 'RUNNING'}
    workflows.get_paginator.return_value.paginate.return_value = [{'events': list(events)}]


def slots_left(conn):
    return sorted(row[0] for row in conn.execute('SELECT execution_arn FROM analysis_execution_slots'))


def test_unprovable_slot_stays_counted_but_never_fails_other_etfs(capacity):
    # ECS answering MISSING minutes after submission can be API lag, not a finished task.
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows, submitted('task', NOW - timedelta(minutes=1)))
    ecs.describe_tasks.return_value = {'tasks': [], 'failures': [{'arn': 'task', 'reason': 'MISSING'}]}
    blocked = control(conn, workflows, ecs, 'cluster', 'one', 'acquire', slots=1, request_key='outlook:091160', now=NOW)
    assert not blocked['acquired'] and blocked['held'] == [{'execution_arn': 'old', 'reason': 'task-missing-recent'}]
    other = control(conn, workflows, ecs, 'cluster', 'two', 'acquire', slots=3, request_key='outlook:091160', now=NOW)
    assert other['acquired'] and slots_left(conn) == ['old', 'two']
    same_key = control(conn, workflows, ecs, 'cluster', 'three', 'acquire', slots=3, request_key='outlook:069500', now=NOW)
    assert not same_key['acquired']  # the kept slot still holds its kind and ETF


def test_missing_task_frees_the_slot_only_after_the_settle_window(capacity):
    conn, _, workflows, ecs = capacity
    ecs.describe_tasks.return_value = {'tasks': [], 'failures': [{'arn': 'task', 'reason': 'MISSING'}]}
    leftover(conn, workflows, submitted('task', NOW - SETTLE + timedelta(seconds=30)))
    assert not control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500', now=NOW)['acquired']
    workflows.get_paginator.return_value.paginate.return_value = [{'events': [submitted('task', NOW - SETTLE)]}]
    result = control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500', now=NOW)
    assert result['acquired'] and result['held'] == [] and slots_left(conn) == ['new']


def test_missing_task_without_submission_time_is_not_proof(capacity):
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows)
    conn.execute("UPDATE analysis_execution_slots SET task_arns='{task}' WHERE execution_arn='old'")
    ecs.describe_tasks.return_value = {'tasks': [], 'failures': [{'arn': 'task', 'reason': 'MISSING'}]}
    result = control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500', now=NOW)
    assert not result['acquired'] and result['held'][0]['reason'] == 'task-missing-recent'


def test_stop_recorded_in_history_frees_slot_even_after_ecs_forgot_the_task(capacity):
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows, submitted('task', NOW - timedelta(minutes=1)),
             stopped_record('task', NOW - timedelta(seconds=30)), status='FAILED')
    ecs.describe_tasks.return_value = {'tasks': [], 'failures': [{'arn': 'task', 'reason': 'MISSING'}]}
    assert control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500', now=NOW)['acquired']
    ecs.describe_tasks.assert_not_called()


def test_other_failure_reasons_keep_the_slot(capacity):
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows, submitted('task', NOW - timedelta(hours=2)))
    ecs.describe_tasks.return_value = {'tasks': [], 'failures': [{'arn': 'task', 'reason': 'ACCESS_DENIED'}]}
    result = control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500', now=NOW)
    assert not result['acquired'] and result['held'][0]['reason'] == 'task-state-unknown'


def worker_task(key):
    kind, etf = key.split(':')
    return {'taskArn': 'live', 'lastStatus': 'RUNNING', 'overrides': {'containerOverrides': [{'name': 'analysis-v2', 'environment': [
        {'name': 'ANALYSIS_REQUEST', 'value': json.dumps({'analysis_id': 'a'*32, 'kind': kind, 'etf_code': etf})}]}]}}


def test_unrecorded_task_is_freed_only_when_ecs_lists_no_worker_for_the_same_etf(capacity):
    # RunTask was called but the execution stopped before recording the task ARN.
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows, _run_task('TaskScheduled', NOW - timedelta(minutes=20)),
             _run_task('TaskStarted', NOW - timedelta(minutes=20)))
    ask = lambda family='edge-dev-analysis-v2', now=NOW: control(
        conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500', family=family, now=now)
    first = ask(family=None)
    assert not first['acquired'] and first['held'][0]['reason'] == 'task-unrecorded'
    assert ask(now=NOW - timedelta(minutes=12))['held'][0]['reason'] == 'task-unrecorded'  # still settling
    ecs.get_paginator.return_value.paginate.return_value = [{'taskArns': ['live']}]
    ecs.describe_tasks.return_value = {'tasks': [worker_task('outlook:069500')], 'failures': []}
    assert ask()['held'][0]['reason'] == 'task-unrecorded-running'
    ecs.describe_tasks.return_value = {'tasks': [worker_task('outlook:091160')], 'failures': []}
    assert ask()['acquired'] and slots_left(conn) == ['new']


def test_run_task_failure_without_a_task_is_resolved_the_same_way(capacity):
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows, _run_task('TaskScheduled', NOW - timedelta(minutes=20)),
             _run_task('TaskFailed', NOW - timedelta(minutes=20), error='ECS.AmazonECSException', cause='capacity'),
             status='FAILED')
    ecs.get_paginator.return_value.paginate.return_value = [{'taskArns': []}]
    assert control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500',
                   family='edge-dev-analysis-v2', now=NOW)['acquired']


def test_release_with_a_running_task_keeps_the_slot_and_does_not_fail(capacity):
    conn, _, workflows, ecs = capacity
    assert control(conn, workflows, ecs, 'cluster', 'one', 'acquire')['acquired']
    workflows.get_paginator.return_value.paginate.return_value = [{'events': [submitted('task', NOW)]}]
    ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'RUNNING'}], 'failures': []}
    result = control(conn, workflows, ecs, 'cluster', 'one', 'release', now=NOW)
    assert result['held'] == [{'execution_arn': 'one', 'reason': 'task-running'}] and slots_left(conn) == ['one']
    ecs.stop_task.assert_called_once()


def test_concurrent_acquires_and_reclaim_keep_the_cap_and_one_per_etf(capacity):
    conn, dsn, _, _ = capacity
    for arn, key in (('done', 'outlook:069500'), ('live1', 'outlook:091160'), ('live2', 'outlook:102110')):
        conn.execute('INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES (%s,%s,%s)', (arn, arn, key))
    keys = ['outlook:069500'] * 4 + ['movement:091160'] * 3 + ['outlook:233740'] * 3
    barrier = Barrier(len(keys))
    def acquire(index):
        workflows, ecs = Mock(), Mock()
        workflows.describe_execution.side_effect = lambda executionArn: {'status': 'SUCCEEDED' if executionArn == 'done' else 'RUNNING'}
        workflows.get_paginator.return_value.paginate.return_value = [{'events': [submitted('task', NOW)]}]
        ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'STOPPED'}], 'failures': []}
        with psycopg.connect(dsn, autocommit=True) as other:
            other.execute('SET ROLE edge_analysis_v2_writer')
            barrier.wait()
            return control(other, workflows, ecs, 'cluster', f'new{index}', 'acquire', slots=3,
                           request_key=keys[index], now=NOW)['acquired']
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        acquired = sum(pool.map(acquire, range(len(keys))))
    rows = conn.execute('SELECT execution_arn, request_key FROM analysis_execution_slots').fetchall()
    assert acquired == 1 and len(rows) == 3 and len({key for _, key in rows}) == 3 and 'done' not in {arn for arn, _ in rows}


def test_unrecorded_task_stays_held_when_ecs_cannot_describe_or_is_still_stopping_it(capacity):
    conn, _, workflows, ecs = capacity
    leftover(conn, workflows, _run_task('TaskScheduled', NOW - timedelta(minutes=20)))
    ask = lambda: control(conn, workflows, ecs, 'cluster', 'new', 'acquire', slots=3, request_key='outlook:069500',
                          family='edge-dev-analysis-v2', now=NOW)
    ecs.get_paginator.return_value.paginate.return_value = [{'taskArns': ['live']}]
    ecs.describe_tasks.return_value = {'tasks': [], 'failures': [{'arn': 'live', 'reason': 'ACCESS_DENIED'}]}
    assert ask()['held'][0]['reason'] == 'task-unrecorded-running'
    # StopTask was sent (desiredStatus STOPPED) but the container is still running: only the STOPPED listing has it.
    listings = {'RUNNING': [{'taskArns': []}], 'STOPPED': [{'taskArns': ['live']}]}
    ecs.get_paginator.return_value.paginate.side_effect = lambda **kwargs: listings[kwargs['desiredStatus']]
    stopping = worker_task('outlook:069500') | {'desiredStatus': 'STOPPED', 'lastStatus': 'DEACTIVATING'}
    ecs.describe_tasks.return_value = {'tasks': [stopping], 'failures': []}
    assert ask()['held'][0]['reason'] == 'task-unrecorded-running'
    ecs.describe_tasks.return_value = {'tasks': [stopping | {'lastStatus': 'STOPPED'}], 'failures': []}
    assert ask()['acquired']
