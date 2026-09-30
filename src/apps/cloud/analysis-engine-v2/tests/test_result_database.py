"""Result connections must never use administrator or alternate-host credentials."""
import json
from unittest.mock import Mock

import pytest

from edge_analysis_v2.storage import database as db


def secret():
    return dict(username=db.DB_USER, host=db.DB_HOST, port=5432, dbname="edge", password="test-only")


@pytest.mark.parametrize("change", [{"username": "edge"}, {"host": "other"},
    {"port": 15433}, {"dbname": "other"}, {"password": ""}])
def test_wrong_secret_is_rejected_before_connection(tmp_path, monkeypatch, change):
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    session = Mock()
    session.client.return_value.get_secret_value.return_value = {"SecretString": json.dumps(secret() | change)}
    connect = Mock()
    monkeypatch.setattr(db.psycopg, "connect", connect)
    with pytest.raises(db.ResultDatabaseError):
        db.connect_results(ca, session=session)
    connect.assert_not_called()


def test_connection_uses_fixed_tunnel_tls_and_independent_commit(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    session = Mock()
    session.client.return_value.get_secret_value.return_value = {"SecretString": json.dumps(secret())}
    connect = Mock()
    monkeypatch.setattr(db.psycopg, "connect", connect)
    assert db.connect_results(ca, session=session) is connect.return_value
    args = connect.call_args.kwargs
    assert args["user"] == db.DB_USER
    assert args["host"] == db.DB_HOST and args["hostaddr"] == "127.0.0.1"
    assert args["port"] == 15433 and args["sslmode"] == "verify-full"
    assert args["sslrootcert"] == str(ca.resolve()) and args["autocommit"] is True
    session.client.return_value.get_secret_value.assert_called_once_with(SecretId="edge/analysis-v2/writer")


def test_connection_error_does_not_expose_secret(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    session = Mock()
    session.client.return_value.get_secret_value.return_value = {"SecretString": json.dumps(secret())}
    monkeypatch.setattr(db.psycopg, "connect", Mock(side_effect=RuntimeError("password=test-only")))
    with pytest.raises(db.ResultDatabaseError) as error:
        db.connect_results(ca, session=session)
    assert "test-only" not in str(error.value)
    assert error.value.__suppress_context__
