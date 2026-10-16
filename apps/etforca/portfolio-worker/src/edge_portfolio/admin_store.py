"""Private operational reads and anonymous presence in the portfolio database."""
from uuid import UUID

from . import store


def initialize(db):
    db.execute('ALTER TABLE portfolio_jobs ADD COLUMN IF NOT EXISTS test_label text')
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


def mark_tests(job_ids, label):
    """Operator-only: identify proven test jobs, never infer them from customer text."""
    if not job_ids or not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise ValueError('INVALID_TEST_LABEL')
    with store.connect() as db:
        found = db.execute('SELECT id FROM portfolio_jobs WHERE id=ANY(%s) FOR UPDATE', (list(job_ids),)).fetchall()
        if {row['id'] for row in found} != set(job_ids):
            raise ValueError('UNKNOWN_JOB')
        db.execute('UPDATE portfolio_jobs SET test_label=%s WHERE id=ANY(%s)', (label.strip(), list(job_ids)))


def scope_filter(scope):
    return {'all': 'TRUE', 'customers': 'test_label IS NULL', 'tests': 'test_label IS NOT NULL'}[scope]


def overview(scope='all'):
    selected = f'WITH selected_jobs AS (SELECT * FROM portfolio_jobs WHERE {scope_filter(scope)}) '
    with store.connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result = db.execute(selected + '''SELECT now() AS as_of,
            (SELECT count(*) FROM portfolio_presence WHERE last_seen>now()-interval '2 minutes') AS active_visitors,
            (SELECT count(*) FROM portfolio_visit_days WHERE day=(now() AT TIME ZONE 'Asia/Seoul')::date) AS today_visitors,
            (SELECT min(first_seen) FROM portfolio_presence) AS tracking_since,
            (SELECT count(*) FROM selected_jobs WHERE kind='analysis' AND state='queued') AS queued,
            (SELECT count(*) FROM selected_jobs WHERE kind='analysis' AND state='running') AS running''').fetchone()
        rows = db.execute(selected + """SELECT state,count(*) AS n FROM selected_jobs WHERE kind='analysis'
            AND created_at >= date_trunc('day',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul' GROUP BY state""").fetchall()
        result['today_jobs'] = {state: 0 for state in ('queued', 'running', 'succeeded', 'failed')}
        result['today_jobs'].update({row['state']: row['n'] for row in rows})
        result.update(db.execute("""SELECT
            count(DISTINCT nullif(lower(btrim(email)), '')) FILTER (WHERE
                created_at >= date_trunc('day',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'
                AND created_at < (date_trunc('day',now() AT TIME ZONE 'Asia/Seoul')+interval '1 day') AT TIME ZONE 'Asia/Seoul'
            ) AS today_unique_emails,
            count(DISTINCT nullif(lower(btrim(email)), '')) AS total_unique_emails,
            count(*) AS total_report_requests FROM portfolio_report_requests""").fetchone())
        result['daily'] = db.execute(selected + """SELECT g.day::date,
            (SELECT count(*) FROM portfolio_visit_days v WHERE v.day=g.day::date) AS visitors,
            (SELECT count(*) FROM selected_jobs j WHERE j.kind='analysis'
                AND j.created_at >= g.day AT TIME ZONE 'Asia/Seoul'
                AND j.created_at < (g.day+interval '1 day') AT TIME ZONE 'Asia/Seoul') AS analyses
            FROM generate_series(date_trunc('day',now() AT TIME ZONE 'Asia/Seoul')-interval '6 days',
                date_trunc('day',now() AT TIME ZONE 'Asia/Seoul'),interval '1 day') AS g(day)""").fetchall()
        return result


def records(kind, days, state, page, scope='all'):
    # SQL fragments are fixed per validated enum; filters and paging remain parameters.
    if kind == 'analyses':
        table, timestamp = 'portfolio_jobs', 'created_at'
        fields = 'id, left(owner,12) AS visitor, created_at AS at, state, stage, failure, started_at, finished_at, test_label'
        condition, args = "kind='analysis' AND (%s='' OR state=%s)", [state, state]
        order = 'created_at DESC,id DESC'
    elif kind == 'drafts':
        table, timestamp = 'portfolio_input_drafts', 'updated_at'
        fields = 'id, left(owner,12) AS visitor, updated_at AS at'
        condition, args, order = 'TRUE', [], 'updated_at DESC,id DESC'
    elif kind == 'reports':
        table, timestamp = '(SELECT r.*,j.test_label FROM portfolio_report_requests r JOIN portfolio_jobs j ON j.id=r.job_id) AS reports', 'created_at'
        fields = 'job_id AS id, left(owner,12) AS visitor, email, created_at AS at, test_label'
        condition, args, order = 'TRUE', [], 'created_at DESC,job_id DESC,owner DESC'
    else:
        raise ValueError('Unknown record kind')
    if kind != 'drafts':
        condition += ' AND ' + scope_filter(scope)
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
            row = db.execute("""SELECT id,left(owner,12) AS visitor,input,result,state,stage,failure,created_at,started_at,finished_at,test_label
                FROM portfolio_jobs WHERE id=%s AND kind='analysis'""", (record_id,)).fetchone()
            if row:
                row['events'] = db.execute('SELECT at,stage FROM portfolio_events WHERE job_id=%s ORDER BY at', (record_id,)).fetchall()
                row['followups'] = db.execute("SELECT input,result,state,failure,created_at,COALESCE(test_label,%s) AS test_label FROM portfolio_jobs WHERE parent_id=%s AND kind='chat' ORDER BY created_at", (row['test_label'], record_id)).fetchall()
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
