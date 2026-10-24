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


def test_mapped_public_tables_and_columns_exist_in_the_flyway_migrations():
    # A few edges read a source table directly. Its shape is owned by libs/schema (Flyway).
    migrations = Path(__file__).parents[2] / 'schema' / 'migrations-cloud'
    sql = '\n'.join(p.read_text(encoding='utf8') for p in sorted(migrations.glob('V*.sql')))
    public = [(label, s) for _, label, s in _sources(_mapping()) if s['schema'] == 'public']
    assert public, 'no direct table reads left: drop this test'
    missing = []
    for label, source in public:
        table = source['table']
        if not re.search(rf'CREATE TABLE (public\.)?{table}\b', sql, re.I):
            missing.append(f'{label}: table {table}')
        missing += [f'{label}: {table}.{f["sourceFieldName"]}' for f in source['mappedField']
                    if not re.search(rf'\b{f["sourceFieldName"]}\b', sql)]
    assert missing == []


def _loader():
    import importlib.util
    path = Path(__file__).parents[3] / 'infra' / 'terraform' / 'modules' / 'ontology-graph' / 'load_schema.py'
    spec = importlib.util.spec_from_file_location('load_schema', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_up_loader_waits_for_the_server_then_uploads_this_mapping_without_logging_secrets(capsys):
    # WHY: PuppyGraph starts with an empty schema every time; the loader is the only thing that restores it.
    # It must keep trying while the server is still starting, send this mapping plus the connection, and
    # never print a password even when the server echoes the request back in an error.
    import base64, gzip, io, urllib.error
    loader = _loader()
    env = {'GRAPH_SCHEMA_GZ': base64.b64encode(gzip.compress(ROOT.joinpath('puppygraph-schema.json').read_bytes())).decode(),
           'GRAPH_CREDENTIALS': json.dumps({'username': 'u', 'password': 'admin-secret', 'db_username': 'ro', 'db_password': 'db-secret'}),
           'DB_HOST': 'db.internal'}
    sent, answers = [], [urllib.error.URLError('refused'),
                         urllib.error.HTTPError(loader.URL, 500, 'x', {}, io.BytesIO(b'{"error":"No healthy leader","echo":"db-secret"}')),
                         io.BytesIO(b'{"ok": true, "message": "Schema uploaded successfully"}')]

    def post(request, timeout):
        sent.append(json.loads(request.data))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert loader.main(env, post=post, sleep=lambda s: None) == 0
    assert len(sent) == 3
    assert {n['label'] for n in sent[-1]['node']} == {n['label'] for n in _mapping()['node']}
    assert sent[-1]['catalog'][0]['jdbc'] == {'username': 'ro', 'password': 'db-secret',
                                              'jdbcUri': 'jdbc:postgresql://db.internal:5432/edge?sslmode=require'}
    out = capsys.readouterr().out
    assert 'graph_schema_uploaded' in out and 'db-secret' not in out and 'admin-secret' not in out


def test_start_up_loader_gives_up_loudly(capsys):
    loader = _loader()
    import base64, gzip, urllib.error
    env = {'GRAPH_SCHEMA_GZ': base64.b64encode(gzip.compress(b'{"node":[],"edge":[]}')).decode(),
           'GRAPH_CREDENTIALS': json.dumps({'username': 'u', 'password': 'p', 'db_username': 'r', 'db_password': 'd'}),
           'DB_HOST': 'h'}

    def post(request, timeout):
        raise urllib.error.URLError('refused')

    assert loader.main(env, post=post, sleep=lambda s: None) == 1
    assert 'graph_schema_failed' in capsys.readouterr().out
