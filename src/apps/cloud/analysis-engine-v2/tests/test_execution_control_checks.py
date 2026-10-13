"""A control call looks up only the slots that can change its answer.

Every call used to ask Step Functions about every other slot. With forty held, one call took seconds;
three concurrent calls could not serve a burst of 95 workflows and 39 gave up before analysing (2026-10-13).
"""
from unittest.mock import Mock

import pytest

from edge_analysis_v2.cloud import control


def slot(arn, key):
    return {'execution_arn': arn, 'request_key': key}


HELD = [slot('a', 'outlook:1'), slot('b', 'outlook:2'), slot('c', 'movement:1')]


@pytest.fixture(autouse=True)
def forget(monkeypatch):
    monkeypatch.setattr(control, '_RUNNING', {})


def check(arn, action, slots, key, now=100.0):
    return control.slots_to_check(HELD, arn, action, slots, key, lambda: now)


def test_release_checks_only_its_own_slot():
    assert check('b', 'release', 3, 'outlook:2') == [HELD[1]]
    assert check('gone', 'release', 3, 'outlook:9') == []


def test_owner_retry_and_room_left_check_nothing():
    assert check('a', 'acquire', 3, 'outlook:1') == []
    assert check('new', 'acquire', 4, 'outlook:9') == []


def test_same_kind_and_etf_checks_only_the_slot_holding_it():
    # Freeing any other slot would not unblock the caller, and this one is asked about even if just seen running.
    control._RUNNING['c'] = 100.0
    assert check('new', 'acquire', 4, 'movement:1') == [HELD[2]]
    assert check('new', 'acquire', 3, 'movement:1') == [HELD[2]]


def test_full_table_skips_only_the_slots_seen_running_within_the_interval():
    assert check('new', 'acquire', 3, 'outlook:9') == HELD
    control._RUNNING.update(a=100.0, b=100.0 - control.SCAN_SECONDS + 0.1, c=100.0 - control.SCAN_SECONDS)
    assert check('new', 'acquire', 3, 'outlook:9') == [HELD[2]]
    assert check('new', 'acquire', 3, 'outlook:9', now=100.0 + control.SCAN_SECONDS) == HELD


class Table:
    """Stand-in for the writer connection: the slot table statements control() runs, without a database.

    integration_tests/test_execution_control.py runs the same calls against PostgreSQL but needs a local database.
    """

    def __init__(self, *rows):
        self.rows = [dict(row, started_by='x', task_arns=[]) for row in rows]

    def cursor(self, row_factory=None):
        return self

    transaction = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, parameters=()):
        if statement.startswith('SELECT execution_arn,started_by'):
            self.result = [dict(row) for row in self.rows]
        elif statement.startswith('SELECT execution_arn,request_key'):
            self.result = [(row['execution_arn'], row['request_key']) for row in self.rows]
        elif statement.startswith('DELETE'):
            self.rows = [row for row in self.rows if row['execution_arn'] != parameters[0]]
        elif statement.startswith('INSERT'):
            self.rows.append(dict(zip(('execution_arn', 'started_by', 'request_key'), parameters), task_arns=[]))
        return self

    def fetchall(self):
        return self.result


@pytest.fixture
def aws():
    workflows = Mock()
    workflows.describe_execution.return_value = {'status': 'RUNNING'}
    workflows.get_paginator.return_value.paginate.return_value = [{'events': []}]
    return workflows


def ask(table, workflows, arn, action='acquire', slots=3, now=100.0):
    return control.control(table, workflows, Mock(), 'cluster', arn, action, slots=slots, request_key=arn, clock=lambda: now)


def test_control_asks_about_no_other_slot_while_room_is_left_or_on_release(aws):
    table = Table(*HELD)
    assert ask(table, aws, 'new', slots=4)['acquired']
    assert ask(table, aws, 'new', slots=4)['acquired']
    ask(table, aws, 'a', 'release', slots=4)
    aws.describe_execution.assert_not_called()
    assert [row['execution_arn'] for row in table.rows] == ['b', 'c', 'new']


def test_control_asks_about_a_running_slot_once_per_interval_when_full(aws):
    table = Table(*HELD)
    assert not ask(table, aws, 'new')['acquired'] and aws.describe_execution.call_count == 3
    assert not ask(table, aws, 'other')['acquired'] and aws.describe_execution.call_count == 3
    assert not ask(table, aws, 'new', now=100.0 + control.SCAN_SECONDS)['acquired'] and aws.describe_execution.call_count == 6


def test_control_remembers_only_running_answers(aws):
    # A workflow that ended without releasing must be asked about again at once, until its slot can be reclaimed.
    table = Table(*HELD)
    aws.describe_execution.side_effect = RuntimeError('AWS unavailable')
    assert not ask(table, aws, 'new')['acquired'] and control._RUNNING == {}
    aws.describe_execution.side_effect = lambda executionArn: {'status': 'FAILED' if executionArn == 'a' else 'RUNNING'}
    assert ask(table, aws, 'new')['acquired'] and set(control._RUNNING) == {'b', 'c'}
    assert [row['execution_arn'] for row in table.rows] == ['b', 'c', 'new']
