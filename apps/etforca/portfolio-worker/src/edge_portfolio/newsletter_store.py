"""Explicit, verified subscriptions and a durable daily email outbox."""
import hashlib
import secrets
from uuid import uuid4

from psycopg.types.json import Jsonb

from .store import connect


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_newsletter_runtime (
        id boolean PRIMARY KEY DEFAULT true CHECK(id), ready boolean NOT NULL,
        checked_at timestamptz NOT NULL DEFAULT now())""")
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_newsletter_subscriptions (
        id text PRIMARY KEY, owner text NOT NULL, email text NOT NULL,
        job_id text NOT NULL REFERENCES portfolio_jobs(id), holdings jsonb NOT NULL,
        status text NOT NULL DEFAULT 'pending', consent_version text NOT NULL DEFAULT 'daily-v1',
        token_hash text, confirmation_token text, confirmation_state text NOT NULL DEFAULT 'queued',
        created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '2 days',
        confirmed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now())""")
    db.execute("ALTER TABLE portfolio_newsletter_subscriptions ADD COLUMN IF NOT EXISTS checked_at timestamptz NOT NULL DEFAULT 'epoch'")
    db.execute("CREATE INDEX IF NOT EXISTS portfolio_newsletter_email ON portfolio_newsletter_subscriptions(email,status)")
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_newsletter_content (
        instrument_id text NOT NULL, edition date NOT NULL, name text NOT NULL,
        content jsonb, state text NOT NULL DEFAULT 'queued', attempts int NOT NULL DEFAULT 0,
        failure text, started_at timestamptz, retry_at timestamptz NOT NULL DEFAULT now(),
        created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(instrument_id,edition))""")
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_newsletter_deliveries (
        email text NOT NULL, edition date NOT NULL, subscription_id text NOT NULL,
        state text NOT NULL DEFAULT 'sending', message_id text, failure text,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(email,edition))""")


def subscribe(job_id, owner, email):
    email = email.strip().lower()
    with connect() as db:
        # Serialize both visitor and address limits; an unverified request never replaces a live subscription.
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 1395))', (owner,))
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 1396))', (email,))
        job = db.execute("SELECT input FROM portfolio_jobs WHERE id=%s AND owner=%s AND kind='analysis' AND state='succeeded'",
                         (job_id, owner)).fetchone()
        if not job:
            raise ValueError('NOT_FOUND')
        holdings = {h['instrument_id']: {'instrument_id': h['instrument_id'], 'name': h['name']}
                    for h in job['input'].get('holdings', [])
                    if h.get('instrument_id') and h.get('kind') != 'cash' and not h['instrument_id'].startswith('CASH:')}
        if not holdings:
            raise ValueError('NO_HOLDINGS')
        existing = db.execute("""SELECT status FROM portfolio_newsletter_subscriptions
            WHERE owner=%s AND email=%s AND job_id=%s AND
            (status='confirmed' OR (status='pending' AND
                (confirmation_state IN ('queued','sending') OR (confirmation_state='sent' AND expires_at>now())))) ORDER BY created_at DESC LIMIT 1""",
            (owner, email, job_id)).fetchone()
        if existing:
            return existing['status']
        limited = db.execute("""SELECT count(*) AS n FROM portfolio_newsletter_subscriptions
            WHERE (owner=%s OR email=%s) AND created_at>now()-interval '1 day'""", (owner, email)).fetchone()['n']
        if limited >= 3:
            raise ValueError('SUBSCRIPTION_LIMIT')
        token = secrets.token_urlsafe(32)
        db.execute("""INSERT INTO portfolio_newsletter_subscriptions
            (id,owner,email,job_id,holdings,token_hash,confirmation_token) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (uuid4().hex, owner, email, job_id, Jsonb(list(holdings.values())), hashlib.sha256(token.encode()).hexdigest(), token))
        return 'pending'


def confirm(token):
    with connect() as db:
        row = db.execute("SELECT * FROM portfolio_newsletter_subscriptions WHERE token_hash=%s AND expires_at>now() FOR UPDATE",
                         (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row['status'] != 'pending':
            raise ValueError('INVALID_CONFIRMATION')
        # The sender completes the SES opt-in before making this subscription active.
        db.execute("UPDATE portfolio_newsletter_subscriptions SET status='confirmed',confirmed_at=now(),token_hash=NULL,confirmation_token=NULL,updated_at=now() WHERE id=%s", (row['id'],))
        return 'confirmed'


def delivery_ready():
    with connect() as db:
        row = db.execute("SELECT ready FROM portfolio_newsletter_runtime WHERE id=true AND checked_at>now()-interval '10 minutes'").fetchone()
        return bool(row and row['ready'])


def content(instrument_id, edition):
    with connect() as db:
        row = db.execute('SELECT content FROM portfolio_newsletter_content WHERE instrument_id=%s AND edition=%s',
                         (instrument_id, edition)).fetchone()
        return row['content'] if row else None


def claim_content():
    with connect() as db:
        db.execute("""UPDATE portfolio_newsletter_content SET state='failed',failure='WORKER_INTERRUPTED'
            WHERE state='running' AND attempts>=3 AND started_at<now()-interval '12 minutes'""")
        return db.execute("""UPDATE portfolio_newsletter_content SET state='running',started_at=now(),attempts=attempts+1
            WHERE (instrument_id,edition)=(SELECT instrument_id,edition FROM portfolio_newsletter_content
                WHERE edition=(now() AT TIME ZONE 'Asia/Seoul')::date AND attempts<3 AND
                    ((state IN ('queued','failed') AND retry_at<=now()) OR (state='running' AND started_at<now()-interval '12 minutes'))
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""").fetchone()


def finish_content(job, value=None, failure=None):
    with connect() as db:
        db.execute("""UPDATE portfolio_newsletter_content SET state=%s,content=%s,failure=%s,retry_at=now()+interval '10 minutes'
            WHERE instrument_id=%s AND edition=%s AND started_at=%s AND state='running'""",
            ('ready' if value else 'failed', Jsonb(value) if value else None, failure, job['instrument_id'], job['edition'], job['started_at']))


def release_content(job):
    with connect() as db:
        db.execute("""UPDATE portfolio_newsletter_content SET state='queued',started_at=NULL,attempts=attempts-1
            WHERE instrument_id=%s AND edition=%s AND started_at=%s AND state='running'""",
            (job['instrument_id'], job['edition'], job['started_at']))
