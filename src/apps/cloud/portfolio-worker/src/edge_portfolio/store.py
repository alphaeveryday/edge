"""Dedicated PostgreSQL queue. Claims and publication are atomic and fenced."""
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect():
    return psycopg.connect(os.environ["PORTFOLIO_DATABASE_URL"], row_factory=dict_row,
                           connect_timeout=5, options='-c statement_timeout=5000')


def initialize():
    with connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(1266001)')
        db.execute("""CREATE TABLE IF NOT EXISTS portfolio_jobs (
            id text PRIMARY KEY, owner text NOT NULL, request_key text NOT NULL,
            input jsonb NOT NULL, state text NOT NULL DEFAULT 'queued',
            stage text, result jsonb, failure text, kind text NOT NULL DEFAULT 'analysis', parent_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz, finished_at timestamptz,
            UNIQUE(owner, request_key))""")
        db.execute("CREATE INDEX IF NOT EXISTS portfolio_queue ON portfolio_jobs(state, created_at)")
        db.execute("ALTER TABLE portfolio_jobs ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'analysis'")
        db.execute("ALTER TABLE portfolio_jobs ADD COLUMN IF NOT EXISTS parent_id text")
        db.execute("""CREATE TABLE IF NOT EXISTS portfolio_events (
            job_id text REFERENCES portfolio_jobs(id), at timestamptz NOT NULL DEFAULT now(),
            stage text NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS portfolio_report_requests (
            job_id text REFERENCES portfolio_jobs(id), owner text NOT NULL, email text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(job_id, owner))""")


def submit(owner, key, payload, parent_id=None):
    with connect() as db:
        # Serializes admission for this visitor, including duplicate browser requests.
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner,))
        existing = db.execute("SELECT * FROM portfolio_jobs WHERE owner=%s AND request_key=%s", (owner, key)).fetchone()
        if existing:
            if existing["input"] != payload or existing["parent_id"] != parent_id:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return existing
        db.execute("SELECT pg_advisory_xact_lock(1266)")
        if parent_id:
            parent = db.execute("SELECT id FROM portfolio_jobs WHERE id=%s AND owner=%s AND state='succeeded' AND kind='analysis'", (parent_id, owner)).fetchone()
            if not parent:
                raise ValueError("PARENT_NOT_FOUND")
            used = db.execute("SELECT id FROM portfolio_jobs WHERE owner=%s AND kind='chat' AND state IN ('queued','running','succeeded')", (owner,)).fetchone()
            if used:
                raise ValueError("CHAT_LIMIT")
        active = db.execute("SELECT count(*) AS n FROM portfolio_jobs WHERE owner=%s AND (state IN ('queued','running') OR created_at>now()-interval '1 day')", (owner,)).fetchone()["n"]
        if active >= 3:
            raise ValueError("REQUEST_LIMIT")
        queued = db.execute("SELECT count(*) AS n FROM portfolio_jobs WHERE state IN ('queued','running')").fetchone()["n"]
        if queued >= 20:
            raise ValueError("QUEUE_FULL")
        daily = db.execute("SELECT count(*) AS n FROM portfolio_jobs WHERE created_at>now()-interval '1 day'").fetchone()["n"]
        if daily >= int(os.getenv("PORTFOLIO_DAILY_JOB_LIMIT", "50")):
            raise ValueError("DAILY_LIMIT")
        return db.execute("INSERT INTO portfolio_jobs(id,owner,request_key,input,kind,parent_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                          (uuid4().hex, owner, key, Jsonb(payload), "chat" if parent_id else "analysis", parent_id)).fetchone()


def request_report(job_id, owner, email):
    with connect() as db:
        row = db.execute("SELECT id FROM portfolio_jobs WHERE id=%s AND owner=%s AND state='succeeded' AND kind='analysis'", (job_id, owner)).fetchone()
        if not row:
            return False
        db.execute("""INSERT INTO portfolio_report_requests(job_id,owner,email) VALUES (%s,%s,%s)
                      ON CONFLICT(job_id,owner) DO UPDATE SET email=excluded.email""", (job_id, owner, email))
        return True


def read(job_id, owner):
    with connect() as db:
        expire(db)
        return db.execute("SELECT * FROM portfolio_jobs WHERE id=%s AND owner=%s", (job_id, owner)).fetchone()


def expire(db):
    db.execute("UPDATE portfolio_jobs SET state='failed',failure='WORKER_INTERRUPTED',finished_at=now() WHERE state='running' AND started_at < now()-interval '6 minutes'")
    db.execute("UPDATE portfolio_jobs SET state='failed',failure='QUEUE_EXPIRED',finished_at=now() WHERE state='queued' AND created_at < now()-interval '30 minutes'")


def claim():
    with connect() as db:
        expire(db)
        return db.execute("""UPDATE portfolio_jobs SET state='running',started_at=now()
            WHERE id=(SELECT id FROM portfolio_jobs WHERE state='queued' ORDER BY created_at
                      FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""").fetchone()


def stage(job_id, name):
    with connect() as db:
        db.execute("UPDATE portfolio_jobs SET stage=%s WHERE id=%s AND state='running'", (name, job_id))
        db.execute("INSERT INTO portfolio_events(job_id,stage) VALUES (%s,%s)", (job_id, name))


def finish(job_id, result=None, failure=None):
    with connect() as db:
        row = db.execute("""UPDATE portfolio_jobs SET state=%s,result=%s,failure=%s,finished_at=now()
                           WHERE id=%s AND state='running' RETURNING id""",
                         ("failed" if failure else "succeeded", Jsonb(result), failure, job_id)).fetchone()
        return row is not None
