"""The extracted entrypoint must preserve query execution and cleanup."""
from unittest.mock import Mock

import pytest

from edge_db_query import __main__ as cli


@pytest.mark.parametrize("use_file", [False, True])
def test_query_input_reaches_database_and_prints_rows(monkeypatch, tmp_path, capsys, use_file):
    sql = "SELECT 1 AS ok"
    path = tmp_path / "query.sql"
    path.write_text(sql, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["db-query", "--file", str(path)] if use_file else ["db-query", "--sql", sql])
    conn = Mock()
    run = Mock(return_value=(["ok"], [(1,)]))
    monkeypatch.setattr(cli, "connect_readonly", Mock(return_value=conn))
    monkeypatch.setattr(cli, "run_query", run)
    assert cli.main() == 0
    run.assert_called_once_with(conn, sql)
    conn.close.assert_called_once()
    assert capsys.readouterr().out == '{"ok": 1}\n'


def test_failed_query_closes_connection_and_propagates_error(monkeypatch):
    monkeypatch.setattr("sys.argv", ["db-query", "--sql", "SELECT 1"])
    conn = Mock()
    monkeypatch.setattr(cli, "connect_readonly", Mock(return_value=conn))
    monkeypatch.setattr(cli, "run_query", Mock(side_effect=RuntimeError("query failed")))
    with pytest.raises(RuntimeError, match="query failed"):
        cli.main()
    conn.close.assert_called_once()


def test_invalid_configuration_emits_structured_error(monkeypatch, capsys):
    import json

    monkeypatch.setattr("sys.argv", ["db-query", "--sql", "SELECT 1"])
    monkeypatch.setenv("PGSCHEMA", "public; invalid")
    assert cli.main() == 1
    event = json.loads(capsys.readouterr().out)
    assert event["event"] == "error"
    assert "invalid PGSCHEMA" in event["message"]
