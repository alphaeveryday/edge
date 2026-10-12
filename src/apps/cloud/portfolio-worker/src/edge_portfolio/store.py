"""Dedicated PostgreSQL queue. Claims and publication are atomic and fenced."""
import json
import os
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect():
    return psycopg.connect(os.environ["PORTFOLIO_DATABASE_URL"], row_factory=dict_row)


def initialize():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS portfolio_jobs (
            id text PRIMARY KEY, owner text NOT NULL, request_key text NOT NULL,
            input jsonb NOT NULL, state text NOT NULL DEFAULT 'queued',
            stage text, result jsonb, failure text, created_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz, finished_at timestamptz,
            UNIQUE(owner, request_key))""")
        db.execute("CREATE INDEX IF NOT EXISTS portfolio_queue ON portfolio_jobs(state, created_at)")


def submit(owner, key, payload):
    with connect() as db:
        # Serializes admission for this visitor, including duplicate browser requests.
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (owner,))
        existing = db.execute("SELECT * FROM portfolio_jobs WHERE owner=%s AND request_key=%s", (owner, key)).fetchone()
        if existing:
            if existing["input"] != payload:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return existing
        active = db.execute("SELECT count(*) AS n FROM portfolio_jobs WHERE owner=%s AND (state IN ('queued','running') OR created_at>now()-interval '1 day')", (owner,)).fetchone()["n"]
        if active >= 3:
            raise ValueError("REQUEST_LIMIT")
        queued = db.execute("SELECT count(*) AS n FROM portfolio_jobs WHERE state IN ('queued','running')").fetchone()["n"]
        if queued >= 20:
            raise ValueError("QUEUE_FULL")
        return db.execute("INSERT INTO portfolio_jobs(id,owner,request_key,input) VALUES (%s,%s,%s,%s) RETURNING *",
                          (uuid4().hex, owner, key, Jsonb(payload))).fetchone()


def read(job_id, owner):
    with connect() as db:
        return db.execute("SELECT * FROM portfolio_jobs WHERE id=%s AND owner=%s", (job_id, owner)).fetchone()


def claim():
    with connect() as db:
        db.execute("UPDATE portfolio_jobs SET state='failed',failure='WORKER_INTERRUPTED',finished_at=now() WHERE state='running' AND started_at < now()-interval '6 minutes'")
        db.execute("UPDATE portfolio_jobs SET state='failed',failure='QUEUE_EXPIRED',finished_at=now() WHERE state='queued' AND created_at < now()-interval '30 minutes'")
        return db.execute("""UPDATE portfolio_jobs SET state='running',started_at=now()
            WHERE id=(SELECT id FROM portfolio_jobs WHERE state='queued' ORDER BY created_at
                      FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""").fetchone()


def stage(job_id, name):
    with connect() as db:
        db.execute("UPDATE portfolio_jobs SET stage=%s WHERE id=%s AND state='running'", (name, job_id))


def finish(job_id, result=None, failure=None):
    with connect() as db:
        row = db.execute("""UPDATE portfolio_jobs SET state=%s,result=%s,failure=%s,finished_at=now()
                           WHERE id=%s AND state='running' RETURNING id""",
                         ("failed" if failure else "succeeded", Jsonb(result), failure, job_id)).fetchone()
        return row is not None
