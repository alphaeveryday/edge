"""Price admission and analysis on one host, without SQS, Step Functions or ECS.

The producer's outbox is the queue and child processes are the tasks:

    outbox row (NEW)  --OutboxQueue-->  cloud.consumer (unchanged)  --LocalAdmission-->
    analysis_execution_requests (accepted_at NULL)  --LocalRunner-->  cloud.worker child

Admission stays separate from execution for the reason it always was: a price reversion must be
recorded at once, not behind analyses that each take minutes and run only a few at a time.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from edge_analysis_v2.cloud.admission import canonical_input
from edge_analysis_v2.cloud.worker import ARTIFACTS

LOG = logging.getLogger(__name__)
DESTINATION = 'price-explanation-realtime'
# Marks requests this runner owns; the column is NOT NULL UNIQUE and otherwise holds a workflow ARN.
LOCAL = 'local:'
# The values the SQS queue had: redelivery after 300 seconds, given up after 16 deliveries.
VISIBILITY_SECONDS = 300
MAX_RECEIVES = 16
POLL_SECONDS = 1
# The workflow stopped its Analyze task after this long.
TASK_SECONDS = 1100
KST = timezone(timedelta(hours=9))

# The eligibility predicate is the Relay's (data-pipeline JobLedger.claim_outbox_batch): a row held
# by either side is invisible to the other. The delivery count rises here, at claim time, so a
# consumer that dies before recording anything is still counted.
CLAIM = '''UPDATE dataset_commit_outbox o
    SET claimed_by=%s, claim_expires_at=now()+make_interval(secs=>%s), attempt_count=o.attempt_count+1
    WHERE o.event_id IN (SELECT c.event_id FROM dataset_commit_outbox c
        WHERE c.status='NEW' AND c.destination=%s
        AND (c.next_attempt_at IS NULL OR c.next_attempt_at<=now())
        AND (c.claimed_by IS NULL OR c.claim_expires_at<now())
        ORDER BY c.created_at LIMIT %s FOR UPDATE SKIP LOCKED)
    RETURNING o.event_id,o.event_type,o.payload,o.attempt_count'''
# Both closes require the claim: after it expires and another consumer takes the row, a late
# call from the first must change nothing.
CLOSE = '''UPDATE dataset_commit_outbox SET status=%s, published_at=%s, last_error=%s,
    claimed_by=NULL, claim_expires_at=NULL WHERE event_id=%s AND claimed_by=%s AND status='NEW' '''


class OutboxQueue:
    """The outbox seen through the two SQS calls cloud.consumer makes, so it runs unchanged.

    Receiving claims rows for VISIBILITY_SECONDS; deleting marks them PUBLISHED, which in this
    transport means admitted rather than sent. A row received more than MAX_RECEIVES times
    becomes DEAD with the reason in last_error instead of returning forever: there is no
    dead-letter queue to take it, and DEAD is where operators already look for stuck events.

    Args:
        connect: Opens a result-writer connection; it needs the outbox grants of
            V202611271200__allow_v2_outbox_claim.sql.
        stop: Shutdown event; an empty poll waits on it.
    """

    def __init__(self, connect, stop):
        self.connect, self.stop = connect, stop

    def receive_message(self, *, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
        # A fresh owner per call is the receipt: only this delivery can close what it claimed.
        owner = uuid4().hex
        messages = []
        # ponytail: one connection per poll; keep one open if connecting each second shows up.
        with self.connect() as connection:
            rows = connection.execute(CLAIM, (owner, VISIBILITY_SECONDS, QueueUrl, MaxNumberOfMessages)).fetchall()
            for event_id, event_type, payload, receives in rows:
                if receives > MAX_RECEIVES:
                    connection.execute(CLOSE, ('DEAD', None, 'DB_MAX_RECEIVE', event_id, owner))
                    LOG.error('Price event exceeded %s deliveries and is DEAD event_id=%s', MAX_RECEIVES, event_id)
                    continue
                body = json.dumps({'event_id': event_id, 'event_type': event_type, 'payload': payload})
                messages.append({'Body': body, 'ReceiptHandle': owner + '|' + event_id})
        if not messages:
            self.stop.wait(POLL_SECONDS)
        return {'Messages': messages}

    def delete_message(self, *, QueueUrl, ReceiptHandle):
        owner, event_id = ReceiptHandle.split('|', 1)
        with self.connect() as connection:
            closed = connection.execute(CLOSE, ('PUBLISHED', datetime.now(timezone.utc), None, event_id, owner)).rowcount
        if not closed:
            # The claim expired and the row was taken again; that delivery finds the request already reserved.
            LOG.warning('Price event acknowledgement lost its claim event_id=%s', event_id)


class LocalAdmission:
    """Record the request and leave it for LocalRunner; nothing is started here.

    The record is what a later reversion joins against, so it must exist before the event is
    acknowledged. accepted_at stays NULL until the analysis process has ended.
    """

    def __init__(self, requests):
        self.requests = requests

    def submit(self, request):
        identity = request['analysis_id']
        self.requests.reserve(request, LOCAL + identity, canonical_input(request))
        return {'execution_arn': LOCAL + identity, 'new': True}


def spawn_worker(raw, slots):
    """Start the unchanged cloud worker for one request, as the ECS task override did.

    ANALYSIS_EXECUTION_ARN is withheld: without a workflow slot the worker uses its session
    advisory locks (one per kind and ETF, and one of ``slots``), which need no Step Functions.
    """
    env = {name: value for name, value in os.environ.items() if name != 'ANALYSIS_EXECUTION_ARN'}
    env.update(ANALYSIS_REQUEST=raw, ANALYSIS_SLOTS=str(slots))
    return subprocess.Popen([sys.executable, '-m', 'edge_analysis_v2.cloud.worker'], env=env)


class LocalRunner:
    """Run admitted requests as child processes, at most ``slots`` at once, oldest first.

    Each request gets one process, as each workflow execution got one task: when the process
    ends, whatever its exit code, the request is marked accepted and is not picked again. A
    request is run again only if this runner itself stopped while its process was alive, which
    the worker tolerates (a completed analysis is not repeated). The host stops every evening,
    so a request left over from an earlier KST day is closed without running: explaining
    yesterday's price move today would publish stale content.

    Args:
        connect: Opens a result-writer connection.
        requests: RequestStore on the same database.
        slots: Analyses allowed at once, one through forty.
        spawn: Starts the worker for ``(input_json, slots)`` and returns a Popen-like object.
        clock: Monotonic seconds, for the per-process time limit.
        now: Aware current time, for the same-day rule.
    """

    def __init__(self, connect, requests, slots, *, spawn=spawn_worker, clock=time.monotonic,
                 now=lambda: datetime.now(timezone.utc)):
        if type(slots) is not int or not 1 <= slots <= 40:
            raise ValueError('Invalid analysis capacity')
        self.connect, self.requests, self.slots = connect, requests, slots
        self.spawn, self.clock, self.now = spawn, clock, now
        self.running = {}  # analysis_id -> (process, etf_code, started)

    def tick(self):
        """Close finished processes, then fill the free slots."""
        for identity, (process, _, started) in list(self.running.items()):
            code = process.poll()
            if code is None:
                if self.clock() - started > TASK_SECONDS:
                    LOG.error('Analysis exceeded %s seconds and is stopped analysis_id=%s', TASK_SECONDS, identity)
                    process.kill()
                continue
            # A failed accept keeps the entry, so the next tick tries again instead of rerunning.
            self.requests.accept(identity, LOCAL + identity)
            del self.running[identity]
            shutil.rmtree(ARTIFACTS / identity, ignore_errors=True)
            LOG.log(logging.WARNING if code else logging.INFO, 'Analysis process ended analysis_id=%s exit=%s', identity, code)
        if len(self.running) >= self.slots:
            return
        # ponytail: reads every pending row each second; add a partial index on accepted_at IS NULL
        # and a LIMIT if a backlog ever makes this slow.
        with self.connect() as connection:
            rows = connection.execute('''SELECT analysis_id, input_json FROM analysis_execution_requests
                WHERE accepted_at IS NULL AND execution_arn LIKE 'local:%%' ORDER BY created_at''').fetchall()
        today = self.now().astimezone(KST).date()
        busy = {etf for _, etf, _ in self.running.values()}
        for identity, raw in rows:
            if len(self.running) >= self.slots:
                break
            request = json.loads(raw)
            if identity in self.running or request['etf_code'] in busy:
                continue  # The worker refuses a second analysis of one ETF; it waits for the first.
            if datetime.fromisoformat(request['analysis_at']).astimezone(KST).date() != today:
                self.requests.accept(identity, LOCAL + identity)
                LOG.warning('Stale request closed without analysis analysis_id=%s analysis_at=%s', identity, request['analysis_at'])
                continue
            self.running[identity] = (self.spawn(raw, self.slots), request['etf_code'], self.clock())
            busy.add(request['etf_code'])

    def serve(self, stop):
        """Tick until shutdown, then stop the children; their requests stay pending."""
        while not stop.wait(POLL_SECONDS):
            try:
                self.tick()
            except Exception as exc:
                # Exception text may carry connection details.
                LOG.warning('Local analysis runner failed type=%s', type(exc).__name__)
        for process, _, _ in self.running.values():
            process.terminate()
