"""The view SQL in this package is what Flyway applies.

WHY: ontology_view is created by libs/schema migrations (V202610241930 onward), but the readable copy of each
view lives here in sql/views/, and the graph mapping test checks columns against that copy. If someone edits a
view file without adding a migration (or the other way round), the database, the copy and the graph mapping
silently disagree. Each view file must equal the newest CREATE OR REPLACE of that view in the migrations, and a
view a migration drops must not keep its file.

Standard library only: schema-validate runs this file directly (python3 <this file>) on migration-only changes,
where the Python test jobs are not selected.
"""
import re
from pathlib import Path

VIEWS = Path(__file__).parents[1] / 'src' / 'edge_ontology' / 'sql' / 'views'
MIGRATIONS = Path(__file__).parents[2] / 'schema' / 'migrations-cloud'
STATEMENT = re.compile(r'(CREATE (?:OR REPLACE )?VIEW|DROP VIEW (?:IF EXISTS )?|ALTER VIEW (?:IF EXISTS )?)\s*ontology_view\.(\w+)(\s+RENAME)?', re.I)


def _text(raw):
    return raw.replace('\r\n', '\n').strip()


def _latest_definitions():
    """View name -> (migration, text) of the newest migration that (re)creates it; dropped views are removed."""
    latest = {}
    for path in sorted(MIGRATIONS.glob('V*.sql')):
        sql = _text(path.read_text(encoding='utf8'))
        for verb, name, rename in STATEMENT.findall(sql):
            if verb.upper().startswith('CREATE'):
                latest[name] = (path.name, sql)
            elif verb.upper().startswith('DROP'):
                latest.pop(name, None)
            elif rename:
                raise AssertionError(f'{path.name}: rename ontology_view.{name} as DROP + CREATE so the file follows')
    return latest


def test_every_view_file_is_the_newest_migration_definition_of_that_view():
    latest = _latest_definitions()
    files = {p.stem: _text(p.read_text(encoding='utf8')) for p in VIEWS.glob('*.sql')}
    assert files
    assert sorted(set(files) - set(latest)) == [], 'view files no migration creates (or a migration dropped)'
    assert sorted(set(latest) - set(files)) == [], 'migrated views with no file in sql/views'
    stale = [f'{name}: {latest[name][0]}' for name, text in files.items() if text not in latest[name][1]]
    assert stale == [], 'view file differs from its newest migration; add a migration with the same text'


if __name__ == '__main__':
    test_every_view_file_is_the_newest_migration_definition_of_that_view()
    print(f'ontology views match migrations: {len(list(VIEWS.glob("*.sql")))} files')
