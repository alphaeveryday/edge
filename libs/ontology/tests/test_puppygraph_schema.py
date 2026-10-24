"""The graph mapping uploaded to PuppyGraph must match the views it reads.

WHY: the mapping is not applied by Flyway. It is uploaded to the graph server at every start
(infra/terraform/modules/ontology-graph). A view column renamed or dropped without the mapping
following it breaks every graph query that touches that label, and nothing else would notice
before the next upload fails or returns empty results.
"""
import json
from pathlib import Path
import re
from importlib import resources

ROOT = resources.files('edge_ontology').joinpath('sql')


def _mapping():
    return json.loads(ROOT.joinpath('puppygraph-schema.json').read_text(encoding='utf8'))


def _sources(mapping):
    for kind in ('node', 'edge'):
        for item in mapping[kind]:
            yield kind, item['label'], item['dataSourceGroup']['externalDataSource']


def test_mapping_carries_no_connection_or_credentials():
    # The upload adds the catalog (JDBC user and password) at start-up from a parameter store.
    mapping = _mapping()
    assert set(mapping) == {'node', 'edge'}
    assert not re.search(r'jdbc|password', json.dumps(mapping), re.I)


def test_every_mapped_column_is_an_output_column_of_its_view():
    missing = []
    for kind, label, source in _sources(_mapping()):
        if source['schema'] == 'public':
            continue  # checked against the Flyway migrations below
        assert source['schema'] == 'ontology_view', label
        view = ROOT.joinpath('views', f"{source['table']}.sql")
        if not view.is_file():
            missing.append(f'{kind} {label}: no view file {source["table"]}.sql')
            continue
        sql = view.read_text(encoding='utf8')
        for field in source['mappedField']:
            column = field['sourceFieldName']
            if not re.search(rf'\bAS\s+"?{re.escape(column)}"?\s*(,|$|\n|\))', sql, re.I | re.M):
                missing.append(f'{kind} {label}: {source["table"]}.{column}')
    assert missing == []


def test_every_edge_joins_two_mapped_node_labels():
    mapping = _mapping()
    nodes = {n['label'] for n in mapping['node']}
    assert len(nodes) == 26
    assert [e['label'] for e in mapping['edge'] if not {e['fromNodeLabel'], e['toNodeLabel']} <= nodes] == []


def _table_sql(sql, table):
    """Only the statements that define or change this table: its CREATE TABLE block and ALTER TABLE lines."""
    parts = re.findall(rf'CREATE TABLE (?:public\.)?{table}\s*\((.*?)\n\);', sql, re.I | re.S)
    parts += re.findall(rf'ALTER TABLE (?:public\.)?{table}\b(.*?);', sql, re.I | re.S)
    return '\n'.join(parts)


def test_mapped_public_tables_and_columns_exist_in_the_flyway_migrations():
    # A few edges read a source table directly. Its shape is owned by libs/schema (Flyway).
    migrations = Path(__file__).parents[2] / 'schema' / 'migrations-cloud'
    sql = '\n'.join(p.read_text(encoding='utf8') for p in sorted(migrations.glob('V*.sql')))
    public = [(label, s) for _, label, s in _sources(_mapping()) if s['schema'] == 'public']
    assert public, 'no direct table reads left: drop this test'
    missing = []
    for label, source in public:
        table = _table_sql(sql, source['table'])
        if not table:
            missing.append(f'{label}: table {source["table"]}')
        for field in source['mappedField']:
            column = field['sourceFieldName']
            if re.search(rf'DROP COLUMN (IF EXISTS )?{column}\b', table, re.I) or not re.search(rf'^\s*(ADD COLUMN (IF NOT EXISTS )?)?{column}\s', table, re.I | re.M):
                missing.append(f'{label}: {source["table"]}.{column}')
    assert missing == []


def _loader():
    import importlib.util
    path = Path(__file__).parents[3] / 'infra' / 'terraform' / 'modules' / 'ontology-graph' / 'load_schema.py'
    spec = importlib.util.spec_from_file_location('load_schema', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env(schema=None):
    import base64, gzip
    raw = schema if schema is not None else ROOT.joinpath('puppygraph-schema.json').read_bytes()
    return {'GRAPH_SCHEMA_GZ': base64.b64encode(gzip.compress(raw)).decode(),
            'PUPPYGRAPH_USERNAME': 'ontology', 'PUPPYGRAPH_PASSWORD': 'admin-secret',
            'GRAPH_CREDENTIALS': json.dumps({'username': 'ignored', 'password': 'ignored', 'db_username': 'ro', 'db_password': 'db-secret'}),
            'DB_HOST': 'db.internal'}


def _post(answers, sent):
    def post(request, timeout):
        sent.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return post


def test_start_up_loader_waits_for_the_server_then_uploads_this_mapping_without_logging_secrets(capsys):
    # WHY: PuppyGraph starts with an empty schema every time; the loader is the only thing that restores it.
    # It must keep trying while the server is still starting, send this mapping plus the connection with the
    # server's own admin login, and never print a secret even when the server echoes the request back.
    import base64, io, urllib.error
    loader = _loader()
    token = base64.b64encode(b'ontology:admin-secret').decode()
    sent = []
    answers = [urllib.error.URLError('refused'),
               urllib.error.HTTPError(loader.URL, 500, 'x', {}, io.BytesIO(f'{{"error":"No healthy leader","echo":"db-secret {token}"}}'.encode())),
               io.BytesIO(b'{"ok": true, "message": "Schema uploaded successfully"}')]
    assert loader.main(_env(), post=_post(answers, sent), sleep=lambda s: None) == 0
    assert len(sent) == 3
    body = json.loads(sent[-1].data)
    assert {n['label'] for n in body['node']} == {n['label'] for n in _mapping()['node']}
    assert body['catalog'][0]['jdbc'] == {'username': 'ro', 'password': 'db-secret',
                                          'jdbcUri': 'jdbc:postgresql://db.internal:5432/edge?sslmode=require'}
    assert sent[-1].get_header('Authorization') == f'Basic {token}'
    out = capsys.readouterr().out
    assert 'graph_schema_uploaded' in out
    assert not any(secret in out for secret in ('db-secret', 'admin-secret', token))


def test_start_up_loader_stops_at_once_on_a_rejected_schema_or_login(capsys):
    # A 4xx answer cannot heal by waiting; retrying would only hide it for the whole deadline.
    import io, urllib.error
    loader = _loader()
    sent = []
    answers = [urllib.error.HTTPError(loader.URL, 401, 'x', {}, io.BytesIO(b'unauthorized'))]
    assert loader.main(_env(), post=_post(answers, sent), sleep=lambda s: None) == 1
    assert len(sent) == 1
    assert 'graph_schema_failed' in capsys.readouterr().out


def test_start_up_loader_gives_up_loudly_after_the_deadline(capsys):
    import urllib.error
    loader = _loader()
    ticks = iter(range(0, 10_000, 100))
    sent = []
    answers = [urllib.error.URLError('refused')] * 50
    assert loader.main(_env(b'{"node":[],"edge":[]}'), post=_post(answers, sent), sleep=lambda s: None, clock=lambda: next(ticks)) == 1
    assert len(sent) == loader.DEADLINE // 100
    assert 'graph_schema_failed' in capsys.readouterr().out


def test_start_up_loader_reports_a_malformed_answer_as_a_failure_line(capsys):
    import io
    loader = _loader()
    assert loader.main({**_env(), 'GRAPH_CREDENTIALS': '{}'}, post=_post([io.BytesIO(b'[]')], []), sleep=lambda s: None) == 1
    assert 'graph_schema_failed' in capsys.readouterr().out
