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
    """View name -> (migration, its text, offset of the last CREATE of that view); dropped views are removed.

    The last statement wins, also inside one migration: PostgreSQL applies them in order.
    """
    latest = {}
    for path in sorted(MIGRATIONS.glob('V*.sql')):
        sql = _text(path.read_text(encoding='utf8'))
        for match in STATEMENT.finditer(sql):
            verb, name, rename = match.groups()
            if verb.upper().startswith('CREATE'):
                latest[name] = (path.name, sql, match.start())
            elif verb.upper().startswith('DROP'):
                latest.pop(name, None)
            elif rename:
                raise AssertionError(f'{path.name}: rename ontology_view.{name} as DROP + CREATE so the file follows')
    return latest


def _matches(file_text, sql, at):
    """The file's statement (from its CREATE) is exactly the text at the last definition's offset."""
    start = STATEMENT.search(file_text)
    body = file_text[start.start():] if start else file_text
    return sql[at:at + len(body)] == body and sql[at + len(body):at + len(body) + 1] in ('', '\n')


def test_every_view_file_is_the_newest_migration_definition_of_that_view():
    latest = _latest_definitions()
    files = {p.stem: _text(p.read_text(encoding='utf8')) for p in VIEWS.glob('*.sql')}
    assert files
    assert sorted(set(files) - set(latest)) == [], 'view files no migration creates (or a migration dropped)'
    assert sorted(set(latest) - set(files)) == [], 'migrated views with no file in sql/views'
    stale = [f'{name}: {latest[name][0]}' for name, text in files.items() if not _matches(text, *latest[name][1:])]
    assert stale == [], 'view file differs from the newest definition of that view; add a migration with the same text'


if __name__ == '__main__':
    test_every_view_file_is_the_newest_migration_definition_of_that_view()
    print(f'ontology views match migrations: {len(list(VIEWS.glob("*.sql")))} files')
