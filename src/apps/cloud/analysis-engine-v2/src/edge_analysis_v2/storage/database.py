"""Dedicated result database connection through the fixed local SSM tunnel."""
from collections import OrderedDict
import json
from pathlib import Path
import random
import time

import psycopg

DB_HOST = "edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com"
DB_USER = "edge_analysis_v2_writer"
SECRET_ID = "edge/analysis-v2/writer"
# A session the server ended or is restarting. Statement timeouts (57014), constraint and data errors
# are deliberately absent: repeating them unchanged cannot succeed and would hide the cause.
TRANSIENT_SQLSTATES = ('57P01', '57P02', '57P03', '53300')
RETRY_SECONDS = 20


class ResultDatabaseError(RuntimeError):
    """Credential-free failure safe to display in the development dashboard.

    Attributes:
        transient: The database refused or dropped the connection; a later attempt may succeed.
    """

    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


def transient(exc):
    """Return true only for a refused or dropped connection, the failures worth repeating unchanged."""
    if isinstance(exc, ResultDatabaseError):
        return exc.transient
    state = getattr(exc, 'sqlstate', None)
    return isinstance(exc, psycopg.OperationalError) and (
        state is None or state.startswith('08') or state in TRANSIENT_SQLSTATES)


class ConnectionStats:
    """Process-wide timings of result connections. Holds no credentials or SQL text."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.connect_seconds, self.secret_fetches, self.secret_seconds = [], 0, 0.0
        self.operations, self.operation_seconds, self.retries, self.retry_wait_seconds = 0, 0.0, 0, 0.0

    def summary(self):
        """Connection setup vs secret lookup vs whole operations; SQL time is operation minus connect."""
        ms = sorted(round(seconds * 1000, 1) for seconds in self.connect_seconds)
        return {'connects': len(ms), 'connect_ms_p50': ms[len(ms)//2] if ms else None,
                'connect_ms_p95': ms[min(len(ms)-1, int(len(ms)*0.95))] if ms else None,
                'connect_ms_max': ms[-1] if ms else None, 'connect_ms_total': round(sum(ms), 1),
                'secret_fetches': self.secret_fetches, 'secret_ms_total': round(self.secret_seconds*1000, 1),
                'operations': self.operations, 'operation_ms_total': round(self.operation_seconds*1000, 1),
                'retries': self.retries, 'retry_wait_s': round(self.retry_wait_seconds, 1)}


STATS = ConnectionStats()
_SESSIONS = {}
# Per-session cache, newest last. Bounded because some callers build a session per call (a warm
# Lambda would otherwise keep every one); a cached entry keeps its session alive, so ids stay unique.
_SECRETS = OrderedDict()
_CACHED_SESSIONS = 8


def _secret(session, *, refresh):
    """Return the writer secret, reusing it and its client within this process."""
    cached = _SECRETS.get(id(session))
    if cached is None or cached['session'] is not session:
        cached = _SECRETS[id(session)] = {'session': session, 'client': session.client("secretsmanager"), 'secret': None}
        while len(_SECRETS) > _CACHED_SESSIONS:
            _SECRETS.popitem(last=False)
    _SECRETS.move_to_end(id(session))
    if refresh or cached['secret'] is None:
        started = time.monotonic()
        cached['secret'] = json.loads(cached['client'].get_secret_value(SecretId=SECRET_ID)["SecretString"])
        STATS.secret_fetches += 1
        STATS.secret_seconds += time.monotonic() - started
    return cached['secret']


def connect_results(ca_path: Path, *, session=None, cloud=False):
    """Connect with the result writer identity and return an idle connection.

    The secret and its client are reused within the process. A failed connection refetches the secret
    before the next attempt, so a rotated password recovers without a restart.

    Args:
        ca_path: Verified regional RDS certificate bundle on disk.
        session: Optional AWS session for isolated tests; defaults to edge-v2-writer.
        cloud: Connect inside the VPC using the task role and the native RDS port.

    Returns:
        Caller-owned autocommit PostgreSQL connection for ToolStore or explicit transactions.

    Raises:
        ResultDatabaseError: Credentials, certificate, or database connection unavailable. ``transient``
            is set when the database itself refused or dropped the connection.
    """
    try:
        ca = Path(ca_path).resolve(strict=True)
        if not ca.is_file():
            raise ValueError("CA bundle must be a file")
        if session is None:
            if cloud not in _SESSIONS:
                import boto3
                _SESSIONS[cloud] = boto3.Session(region_name="ap-northeast-2", **({} if cloud else {'profile_name':'edge-v2-writer'}))
            session = _SESSIONS[cloud]
        cached = _SECRETS.get(id(session))
        secret = _secret(session, refresh=bool(cached and cached.get('stale')))
        expected = dict(username=DB_USER, host=DB_HOST, port=5432, dbname="edge")
        if (any(secret.get(key) != value for key, value in expected.items())
                or not isinstance(secret.get("password"), str) or not secret["password"]):
            raise ValueError("Writer secret identity mismatch")
        address = {'port':5432} if cloud else {'hostaddr':'127.0.0.1','port':15433}
        started = time.monotonic()
        try:
            connection = psycopg.connect(host=DB_HOST, **address,
                                         dbname="edge", user=DB_USER, password=secret["password"],
                                         sslmode="verify-full", sslrootcert=str(ca), connect_timeout=10,
                                         autocommit=True,
                                         options="-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000")
        except psycopg.OperationalError:
            # psycopg reports no SQLSTATE for connect failures, so a rotated password looks like any other refusal.
            _SECRETS[id(session)]['stale'] = True
            raise
        _SECRETS[id(session)]['stale'] = False
        STATS.connect_seconds.append(time.monotonic() - started)
        return connection
    except Exception as exc:
        raise ResultDatabaseError("Writer connection unavailable; check writer profile, tunnel and RDS CA",
                                  transient=isinstance(exc, psycopg.OperationalError)) from None


def retry_transient(operation, *, seconds=RETRY_SECONDS, sleep=time.sleep, clock=time.monotonic, jitter=random.uniform):
    """Run an idempotent operation, repeating the whole of it after a dropped or refused connection.

    The operation must open its own connection and own complete transactions, so a repeat never
    continues a transaction that failed halfway. Any other error is raised at once. Waits are
    jittered so workers refused together do not all return together.

    Args:
        operation: Zero-argument callable; repeated as a unit.
        seconds: Longest total time spent waiting between attempts.

    Returns:
        The operation's result.
    """
    started, delay = clock(), 0.5
    while True:
        attempt = clock()
        try:
            result = operation()
            STATS.operations += 1
            STATS.operation_seconds += clock() - attempt
            return result
        except Exception as exc:
            if not transient(exc) or clock() - started + delay > seconds:
                raise
            wait = jitter(delay / 2, delay)
            STATS.retries += 1
            STATS.retry_wait_seconds += wait
            sleep(wait)
            delay = min(delay * 2, 8)
