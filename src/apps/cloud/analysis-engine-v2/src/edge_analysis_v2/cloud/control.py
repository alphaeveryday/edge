"""Reserve analysis capacity before starting Fargate; reclaim only stopped work."""
from datetime import datetime, timedelta, timezone
import json
from hashlib import sha256
import logging
import os
from pathlib import Path
import time

import boto3
from psycopg.rows import dict_row

from edge_analysis_v2.storage.database import connect_results

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
TERMINAL = ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED')
# The ECS API is eventually consistent; AWS advises backing off up to five minutes before trusting
# DescribeTasks after RunTask. Absence from ECS counts as evidence only after twice that long.
SETTLE = timedelta(minutes=10)


def _run_task(details):
    return details.get('resourceType') == 'ecs' and details.get('resource') == 'runTask.sync'


def task_history(workflows, execution_arn):
    """Read what the execution history proves about its Analyze task.

    Returns:
        Submitted task ARNs with their submission time, task ARNs whose completion event already
        reported STOPPED, and the time of the last RunTask event when a call never recorded a task.
    """
    submitted, stopped, unrecorded = {}, set(), None
    # Optimized ECS integration owns StartedBy. Its execution history identifies tasks.
    for page in workflows.get_paginator('get_execution_history').paginate(executionArn=execution_arn):
        for event in page['events']:
            at = event.get('timestamp')
            details = event.get('taskSubmittedEventDetails', {})
            if _run_task(details):
                for task in json.loads(details['output']).get('Tasks', []):
                    submitted[task['TaskArn']] = at
                unrecorded = None
                continue
            for key, field in (('taskSucceededEventDetails', 'output'), ('taskFailedEventDetails', 'cause')):
                details = event.get(key, {})
                if not _run_task(details):
                    continue
                try:
                    task = json.loads(details.get(field) or 'null')
                except ValueError:
                    task = None
                if isinstance(task, dict) and task.get('LastStatus') == 'STOPPED' and task.get('TaskArn'):
                    stopped.add(task['TaskArn'])
            for key in ('taskScheduledEventDetails', 'taskStartedEventDetails', 'taskFailedEventDetails'):
                if _run_task(event.get(key, {})) and not submitted:
                    unrecorded = at  # A RunTask call that never reported its task (failed or interrupted)
    return submitted, stopped, unrecorded


def running_for_key(ecs, cluster, family, request_key):
    """Return true when any running worker task carries the same kind and ETF in its request."""
    arns = [arn for page in ecs.get_paginator('list_tasks').paginate(
        cluster=cluster, family=family, desiredStatus='RUNNING') for arn in page['taskArns']]
    for start in range(0, len(arns), 100):
        for task in ecs.describe_tasks(cluster=cluster, tasks=arns[start:start+100])['tasks']:
            for container in task.get('overrides', {}).get('containerOverrides', []):
                for variable in container.get('environment', []):
                    if variable.get('name') != 'ANALYSIS_REQUEST':
                        continue
                    request = json.loads(variable['value'])
                    if f"{request.get('kind')}:{request.get('etf_code')}" == request_key:
                        return True
    return False


def reclaimable(connection, workflows, ecs, cluster, slot, *, family=None, now=None):
    """Decide from evidence whether a finished execution's slot may be reused.

    A slot is freed only when every task its execution started is known to have stopped: a completion
    event in the execution history, ECS reporting STOPPED, or ECS no longer knowing a task submitted
    longer ago than SETTLE. Anything less keeps the slot occupied. Surviving tasks are asked to stop.

    Returns:
        Whether the slot can be deleted, and the reason for the decision.
    """
    now = now or datetime.now(timezone.utc)
    submitted, stopped, unrecorded = task_history(workflows, slot['execution_arn'])
    arns = set(slot['task_arns']) | set(submitted)
    if not arns:
        if unrecorded is None:
            return True, 'no-task-started'
        # RunTask was called but its task was never recorded. Only ECS can say whether it runs.
        if family is None or now - unrecorded < SETTLE:
            return False, 'task-unrecorded'
        if running_for_key(ecs, cluster, family, slot['request_key']):
            return False, 'task-unrecorded-running'
        return True, 'task-unrecorded-none-running'
    # StopTask removes a task from the default RUNNING listing before it has exited.
    connection.execute('UPDATE analysis_execution_slots SET task_arns=%s WHERE execution_arn=%s',
                       (sorted(arns), slot['execution_arn']))
    pending = sorted(arns - stopped)
    if not pending:
        return True, 'stopped-recorded'
    response = ecs.describe_tasks(cluster=cluster, tasks=pending)
    states = {task['taskArn']: task['lastStatus'] for task in response.get('tasks', [])}
    missing = {failure['arn'] for failure in response.get('failures', []) if failure.get('reason') == 'MISSING'}
    active = [arn for arn in pending if arn in states and states[arn] != 'STOPPED']
    for arn in active:
        ecs.stop_task(cluster=cluster, task=arn, reason='Analysis workflow finished')
    if active:
        return False, 'task-running'
    unknown = [arn for arn in pending if states.get(arn) != 'STOPPED' and not (
        arn in missing and submitted.get(arn) is not None and now - submitted[arn] >= SETTLE)]
    if unknown:
        return False, 'task-missing-recent' if all(arn in missing for arn in unknown) else 'task-state-unknown'
    return True, 'stopped-or-missing-after-settle' if missing else 'stopped'


def control(connection, workflows, ecs, cluster, execution_arn, action, *, slots=1, request_key=None,
            family=None, now=None):
    """Acquire one shared slot or release it after the owning task stops.

    A slot whose release cannot be proven stays occupied and keeps counting against the capacity,
    but its failed check never fails the call for other executions.

    Args:
        connection: Idle result-writer connection.
        workflows: Step Functions client for the single-analysis workflow.
        ecs: ECS client restricted to the analysis cluster.
        cluster: Server-configured cluster ARN.
        execution_arn: Caller workflow execution ARN.
        action: Acquire or release.
        slots: Shared configured capacity, from one through three.
        request_key: Analysis kind and ETF code; one execution at a time per pair.
        family: Worker task family, used only to look for a task whose ARN was never recorded.
        now: Aware clock for tests.

    Returns:
        Acquired flag, stable reservation identity, and slots kept occupied without proof of release.
    """
    if action not in ('acquire', 'release'):
        raise ValueError('Unknown execution control action')
    if type(slots) is not int or not 1 <= slots <= 3:
        raise ValueError('Invalid analysis capacity')
    request_key = request_key or execution_arn
    started_by = sha256(execution_arn.encode()).hexdigest()[:32]
    # AWS calls occur outside the transaction so a slow dependency never holds the lock.
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT execution_arn,started_by,request_key,task_arns FROM analysis_execution_slots')
        existing_slots = cur.fetchall()
    removable, held = [], []
    for slot in existing_slots:
        arn = slot['execution_arn']
        try:
            if arn == execution_arn:
                finished = action == 'release'
            else:
                finished = workflows.describe_execution(executionArn=arn)['status'] in TERMINAL
            if not finished:
                continue
            free, reason = reclaimable(connection, workflows, ecs, cluster, slot, family=family, now=now)
        except Exception as exc:
            free, reason = False, 'check-failed:' + type(exc).__name__
        if free:
            removable.append(arn)
            if reason not in ('stopped', 'stopped-recorded', 'no-task-started'):
                LOG.warning('Reclaiming analysis slot execution=%s reason=%s', arn, reason)
        else:
            held.append({'execution_arn': arn, 'reason': reason})
            LOG.warning('Keeping analysis slot occupied execution=%s reason=%s', arn, reason)
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('analysis-v2-capacity')::bigint)")
        for arn in removable:
            connection.execute('DELETE FROM analysis_execution_slots WHERE execution_arn=%s', (arn,))
        if action == 'release':
            return {'acquired': False, 'started_by': started_by, 'held': held}
        rows = connection.execute('SELECT execution_arn,request_key FROM analysis_execution_slots').fetchall()
        if len(rows) < slots and all(row[1] != request_key for row in rows):
            connection.execute('INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES (%s,%s,%s)',
                               (execution_arn, started_by, request_key))
            acquired = True
        else:
            acquired = any(row[0] == execution_arn for row in rows)
    return {'acquired': acquired, 'started_by': started_by, 'held': held}


_CLIENTS = {}


def handler(event, context):
    """Handle internal SFN control calls; there is no public HTTP route."""
    began = time.monotonic()
    if not _CLIENTS:
        # Warm invocations reuse the session and clients; building them is CPU-bound at this memory size.
        session = boto3.Session()
        _CLIENTS.update(session=session, workflows=session.client('stepfunctions'), ecs=session.client('ecs'))
    clients = time.monotonic()
    expected = os.environ['EXECUTION_ARN_PREFIX']
    arn = event['execution_arn']
    if not isinstance(arn, str) or not arn.startswith(expected):
        raise ValueError('Unexpected workflow execution')
    with connect_results(Path(os.environ['RDS_CA_PATH']), session=_CLIENTS['session'], cloud=True) as connection:
        connected = time.monotonic()
        result = control(connection, _CLIENTS['workflows'], _CLIENTS['ecs'],
                         os.environ['CLUSTER_ARN'], arn, event['action'],
                         slots=int(os.environ['ANALYSIS_SLOTS']), request_key=event.get('request_key'),
                         family=os.environ.get('TASK_FAMILY'))
    done = time.monotonic()
    timing = {'clients_ms': round((clients-began)*1000), 'connect_ms': round((connected-clients)*1000),
              'control_ms': round((done-connected)*1000)}
    LOG.info('Execution control action=%s timing=%s held=%d', event['action'], json.dumps(timing), len(result['held']))
    return result | {'timing_ms': timing}
