"""Private operational reads and anonymous presence in the portfolio database."""
from uuid import UUID

from . import store


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_presence (
        owner text PRIMARY KEY, first_seen timestamptz NOT NULL DEFAULT now(),
        last_seen timestamptz NOT NULL DEFAULT now())''')
    db.execute('CREATE INDEX IF NOT EXISTS portfolio_presence_seen ON portfolio_presence(last_seen)')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_visit_days (
        day date NOT NULL, owner text NOT NULL, PRIMARY KEY(day,owner))''')


def heartbeat(owner):
    with store.connect() as db:
        db.execute('''INSERT INTO portfolio_presence(owner) VALUES (%s) ON CONFLICT(owner)
            DO UPDATE SET last_seen=now() WHERE portfolio_presence.last_seen < now()-interval '20 seconds' ''', (owner,))
        db.execute("INSERT INTO portfolio_visit_days VALUES ((now() AT TIME ZONE 'Asia/Seoul')::date,%s) ON CONFLICT DO NOTHING", (owner,))
        db.execute("DELETE FROM portfolio_visit_days WHERE day < (now() AT TIME ZONE 'Asia/Seoul')::date-90")
        db.execute("DELETE FROM portfolio_presence WHERE last_seen < now()-interval '90 days'")


def overview():
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result = db.execute('''SELECT now() AS as_of,
            (SELECT count(*) FROM portfolio_presence WHERE last_seen>now()-interval '2 minutes') AS active_visitors,
            (SELECT count(*) FROM portfolio_visit_days WHERE day=(now() AT TIME ZONE 'Asia/Seoul')::date) AS today_visitors,
            (SELECT min(first_seen) FROM portfolio_presence) AS tracking_since,
            (SELECT count(*) FROM portfolio_jobs WHERE kind='analysis' AND state='queued') AS queued,
            (SELECT count(*) FROM portfolio_jobs WHERE kind='analysis' AND state='running') AS running''').fetchone()
        rows = db.execute("""SELECT state,count(*) AS n FROM portfolio_jobs WHERE kind='analysis'
            AND created_at >= date_trunc('day',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul' GROUP BY state""").fetchall()
        result['today_jobs'] = {state: 0 for state in ('queued', 'running', 'succeeded', 'failed')}
        result['today_jobs'].update({row['state']: row['n'] for row in rows})
        result['daily'] = db.execute("""SELECT g.day::date,
            (SELECT count(*) FROM portfolio_visit_days v WHERE v.day=g.day::date) AS visitors,
            (SELECT count(*) FROM portfolio_jobs j WHERE j.kind='analysis'
                AND j.created_at >= g.day AT TIME ZONE 'Asia/Seoul'
                AND j.created_at < (g.day+interval '1 day') AT TIME ZONE 'Asia/Seoul') AS analyses
            FROM generate_series(date_trunc('day',now() AT TIME ZONE 'Asia/Seoul')-interval '6 days',
                date_trunc('day',now() AT TIME ZONE 'Asia/Seoul'),interval '1 day') AS g(day)""").fetchall()
        return result


def records(kind, days, state, page):
    # SQL fragments are fixed per validated enum; filters and paging remain parameters.
    if kind == 'analyses':
        table, timestamp = 'portfolio_jobs', 'created_at'
        fields = 'id, left(owner,12) AS visitor, created_at AS at, state, stage, failure, started_at, finished_at'
        condition, args = "kind='analysis' AND (%s='' OR state=%s)", [state, state]
        order = 'created_at DESC,id DESC'
    elif kind == 'drafts':
        table, timestamp = 'portfolio_input_drafts', 'updated_at'
        fields = 'id, left(owner,12) AS visitor, updated_at AS at'
        condition, args, order = 'TRUE', [], 'updated_at DESC,id DESC'
    elif kind == 'reports':
        table, timestamp = 'portfolio_report_requests', 'created_at'
        fields = 'job_id AS id, left(owner,12) AS visitor, email, created_at AS at'
        condition, args, order = 'TRUE', [], 'created_at DESC,job_id DESC,owner DESC'
    else:
        raise ValueError('Unknown record kind')
    condition += f" AND {timestamp} >= (date_trunc('day',now() AT TIME ZONE 'Asia/Seoul')-(%s-1)*interval '1 day') AT TIME ZONE 'Asia/Seoul'"
    args.append(days)
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        total = db.execute(f'SELECT count(*) AS n FROM {table} WHERE {condition}', args).fetchone()['n']
        rows = db.execute(f'SELECT {fields} FROM {table} WHERE {condition} ORDER BY {order} LIMIT 50 OFFSET %s', [*args, (page-1)*50]).fetchall()
        return {'items': rows, 'total': total, 'page': page, 'page_size': 50}


def detail(kind, record_id):
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if kind in ('analyses', 'reports'):
            row = db.execute("""SELECT id,left(owner,12) AS visitor,input,result,state,stage,failure,created_at,started_at,finished_at
                FROM portfolio_jobs WHERE id=%s AND kind='analysis'""", (record_id,)).fetchone()
            if row:
                row['events'] = db.execute('SELECT at,stage FROM portfolio_events WHERE job_id=%s ORDER BY at', (record_id,)).fetchall()
                row['followups'] = db.execute("SELECT input,result,state,failure,created_at FROM portfolio_jobs WHERE parent_id=%s AND kind='chat' ORDER BY created_at", (record_id,)).fetchall()
            return row
        try:
            record_id = str(UUID(record_id))
        except ValueError:
            return None
        row = db.execute('SELECT id,left(owner,12) AS visitor,updated_at FROM portfolio_input_drafts WHERE id=%s', (record_id,)).fetchone()
        if not row:
            return None
        sections = {name: db.execute(f'SELECT payload FROM portfolio_input_{name} WHERE draft_id=%s', (record_id,)).fetchone()['payload'] for name in store.INPUT_TABLES}
        row['input'] = {**sections['portfolio'], **sections['horizon'], 'preferences': {
            **sections['funding'], **sections['interests'], **sections['notes'], **sections['tax']}}
        return row
