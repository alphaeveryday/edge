"""The view SQL in this package is what Flyway applies.

WHY: ontology_view is created by libs/schema migrations (V202610241930 onward), but the readable copy of each
view lives here in sql/views/, and the graph mapping test checks columns against that copy. If someone edits a
view file without adding a migration (or the other way round), the database, the copy and the graph mapping
silently disagree. Each view file must equal the most recent CREATE OR REPLACE of that view in the migrations.
"""
import re
from importlib import resources
from pathlib import Path

VIEWS = resources.files('edge_ontology').joinpath('sql', 'views')
MIGRATIONS = Path(__file__).parents[2] / 'schema' / 'migrations-cloud'


def _text(raw):
    return raw.replace('\r\n', '\n').strip()


def _latest_definitions():
    """View name -> text of the newest migration that (re)creates it."""
    latest = {}
    for path in sorted(MIGRATIONS.glob('V*.sql')):
        sql = _text(path.read_text(encoding='utf8'))
        for name in re.findall(r'CREATE (?:OR REPLACE )?VIEW ontology_view\.(\w+)', sql):
            latest[name] = (path.name, sql)
    return latest


def test_every_view_file_is_the_newest_migration_definition_of_that_view():
    latest = _latest_definitions()
    files = {p.name[:-4]: _text(p.read_text(encoding='utf8')) for p in VIEWS.iterdir() if p.name.endswith('.sql')}
    assert len(files) == 30
    assert sorted(set(files) - set(latest)) == [], 'view files no migration creates'
    assert sorted(set(latest) - set(files)) == [], 'migrated views with no file in sql/views'
    stale = [f'{name}: {latest[name][0]}' for name, text in files.items() if text not in latest[name][1]]
    assert stale == [], 'view file differs from its newest migration; add a migration with the same text'
