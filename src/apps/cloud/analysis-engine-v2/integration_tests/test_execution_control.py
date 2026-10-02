"""Concurrent requests wait without starting another billable analysis task."""
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.cloud.control import control


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
        ecs = Mock()
        ecs.list_tasks.return_value = {'taskArns': []}
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
    ecs.list_tasks.return_value = {'taskArns': ['task']}
    ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'RUNNING'}]}
    assert not control(conn, workflows, ecs, 'cluster', 'new', 'acquire')['acquired']
    ecs.stop_task.assert_called_once()
    ecs.list_tasks.return_value = {'taskArns': []}
    assert not control(conn, workflows, ecs, 'cluster', 'new', 'acquire')['acquired']
    ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'task', 'lastStatus': 'STOPPED'}]}
    assert control(conn, workflows, ecs, 'cluster', 'new', 'acquire')['acquired']


def test_release_is_idempotent_and_aws_failure_cannot_free_live_slot(capacity):
    conn, _, workflows, ecs = capacity
    assert control(conn, workflows, ecs, 'cluster', 'one', 'acquire')['acquired']
    workflows.describe_execution.side_effect = RuntimeError('AWS unavailable')
    with pytest.raises(RuntimeError):
        control(conn, workflows, ecs, 'cluster', 'two', 'acquire')
    assert conn.execute('SELECT execution_arn FROM analysis_execution_slots').fetchall() == [('one',)]
    control(conn, workflows, ecs, 'cluster', 'one', 'release')
    control(conn, workflows, ecs, 'cluster', 'one', 'release')
    assert control(conn, workflows, ecs, 'cluster', 'two', 'acquire')['acquired']
