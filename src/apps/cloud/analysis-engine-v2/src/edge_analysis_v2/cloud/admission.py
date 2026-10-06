"""Shared Standard Workflow admission; accepting work is distinct from publishing it."""
from datetime import datetime, timedelta, timezone
import json

from botocore.exceptions import BotoCoreError, ClientError

from edge_analysis_v2.cloud.contract import decode_request


class RequestConflict(ValueError):
    """An existing identity belongs to different immutable execution input."""


def canonical_input(request):
    """Return stable JSON, including the source, with one timestamp spelling."""
    request = decode_request(json.dumps(request, allow_nan=False), internal=True)
    # Source adapters select Korean trading dates from this timestamp.
    request['analysis_at'] = datetime.fromisoformat(request['analysis_at']).astimezone(timezone(timedelta(hours=9))).isoformat()
    return json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class WorkflowAdmission:
    """Reconcile one execution identity before returning acceptance.

    Args:
        workflows: Step Functions client for the server-owned workflow.
        state_machine: Standard Workflow ARN; aliases/versions are not accepted.
        requests: Durable request store. Omitted only by the existing API runtime
            until its dedicated admission database identity is provisioned.
        now: Aware clock for the unresolved-request retention guard.
    """

    def __init__(self, workflows, state_machine, *, requests=None, now=None):
        self.workflows, self.state_machine, self.requests = workflows, state_machine, requests
        self.now = now or (lambda:datetime.now(timezone.utc))
        if len(state_machine.split(':')) != 7 or ':stateMachine:' not in state_machine:
            raise ValueError('An unqualified Standard Workflow ARN is required')

    def execution_arn(self, identity):
        return self.state_machine.replace(':stateMachine:', ':execution:')+':'+identity

    def execution(self, identity):
        """Treat only confirmed absence as missing; preserve dependency failures."""
        try:
            return self.workflows.describe_execution(executionArn=self.execution_arn(identity))
        except ClientError as exc:
            if exc.response['Error']['Code'] == 'ExecutionDoesNotExist':
                return None
            raise

    def submit(self, request):
        """Return the accepted ARN only after durable tracking, when configured."""
        raw = canonical_input(request)
        identity = request['analysis_id']
        arn = self.execution_arn(identity)
        record = self.requests.reserve(request, arn, raw) if self.requests else None
        if record and record['accepted_at'] is not None:
            return {'execution_arn':record['execution_arn'], 'new':False}
        previous = self.execution(identity)
        created = previous is None
        if previous is None:
            # Pending records cannot prove absence once AWS can recycle the name.
            if record and self.now()-record['created_at'] >= timedelta(days=89):
                raise RuntimeError('Unconfirmed execution requires operator reconciliation')
            try:
                self.workflows.start_execution(stateMachineArn=self.state_machine, name=identity, input=raw)
            except (ClientError, BotoCoreError):
                previous = self.execution(identity)
                if previous is None:
                    raise
                created = False
        if previous is not None and canonical_input(json.loads(previous['input'])) != raw:
            raise RequestConflict('Request identity conflict')
        if self.requests:
            self.requests.accept(identity, arn)
        return {'execution_arn':arn, 'new':created}
