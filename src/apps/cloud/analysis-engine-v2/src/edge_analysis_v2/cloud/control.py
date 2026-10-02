"""Reserve analysis capacity before starting Fargate; reclaim only stopped work."""
import json
from hashlib import sha256
import os
from pathlib import Path

import boto3
from psycopg.rows import dict_row

from edge_analysis_v2.storage.database import connect_results


def tasks_stopped(connection, workflows, ecs, cluster, slot):
    """Stop surviving tasks and return true only after ECS confirms their exit."""
    arns = set(slot['task_arns'])
    # Optimized ECS integration owns StartedBy. Its execution history identifies tasks.
    for page in workflows.get_paginator('get_execution_history').paginate(
            executionArn=slot['execution_arn']):
        for event in page['events']:
            details = event.get('taskSubmittedEventDetails', {})
            if details.get('resourceType') == 'ecs' and details.get('resource') == 'runTask.sync':
                output = json.loads(details['output'])
                arns.update(task['TaskArn'] for task in output.get('Tasks', []))
    arns = sorted(arns)
    if not arns:
        return True
    # StopTask removes a task from the default RUNNING listing before it has exited.
    connection.execute('UPDATE analysis_execution_slots SET task_arns=%s WHERE execution_arn=%s',
                       (arns, slot['execution_arn']))
    response = ecs.describe_tasks(cluster=cluster, tasks=arns)
    if response.get('failures'):
        raise RuntimeError('Cannot confirm task termination')
    active = [task for task in response['tasks'] if task['lastStatus'] != 'STOPPED']
    for task in active:
        ecs.stop_task(cluster=cluster, task=task['taskArn'], reason='Analysis workflow finished')
    return not active


def control(connection, workflows, ecs, cluster, execution_arn, action, *, slots=1, request_key=None):
    """Acquire one shared slot or release it after the owning task stops.

    Args:
        connection: Idle result-writer connection.
        workflows: Step Functions client for the single-analysis workflow.
        ecs: ECS client restricted to the analysis cluster.
        cluster: Server-configured cluster ARN.
        execution_arn: Caller workflow execution ARN.
        action: Acquire or release.
        slots: Shared configured capacity, from one through three.
        request_key: Analysis kind and ETF code; one execution at a time per pair.

    Returns:
        Acquired flag and stable reservation identity.
    """
    if action not in ('acquire', 'release'):
        raise ValueError('Unknown execution control action')
    if type(slots) is not int or not 1 <= slots <= 3:
        raise ValueError('Invalid analysis capacity')
    request_key = request_key or execution_arn
    started_by = sha256(execution_arn.encode()).hexdigest()[:32]
    # AWS calls occur outside the transaction so a slow dependency never holds the lock.
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT execution_arn,started_by,task_arns FROM analysis_execution_slots')
        existing_slots = cur.fetchall()
    removable = []
    for slot in existing_slots:
        if slot['execution_arn'] == execution_arn:
            finished = action == 'release'
        else:
            status = workflows.describe_execution(executionArn=slot['execution_arn'])['status']
            finished = status in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED')
        if finished and tasks_stopped(connection, workflows, ecs, cluster, slot):
            removable.append(slot['execution_arn'])
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('analysis-v2-capacity')::bigint)")
        for arn in removable:
            connection.execute('DELETE FROM analysis_execution_slots WHERE execution_arn=%s', (arn,))
        if action == 'release':
            return {'acquired': False, 'started_by': started_by}
        rows = connection.execute('SELECT execution_arn,request_key FROM analysis_execution_slots').fetchall()
        if len(rows) < slots and all(row[1] != request_key for row in rows):
            connection.execute('INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES (%s,%s,%s)',
                               (execution_arn, started_by, request_key))
            acquired = True
        else:
            acquired = any(row[0] == execution_arn for row in rows)
    return {'acquired': acquired, 'started_by': started_by}


def handler(event, context):
    """Handle internal SFN control calls; there is no public HTTP route."""
    session = boto3.Session()
    expected = os.environ['EXECUTION_ARN_PREFIX']
    arn = event['execution_arn']
    if not isinstance(arn, str) or not arn.startswith(expected):
        raise ValueError('Unexpected workflow execution')
    with connect_results(Path(os.environ['RDS_CA_PATH']), session=session, cloud=True) as connection:
        return control(connection, session.client('stepfunctions'), session.client('ecs'),
                       os.environ['CLUSTER_ARN'], arn, event['action'],
                       slots=int(os.environ['ANALYSIS_SLOTS']), request_key=event.get('request_key'))
