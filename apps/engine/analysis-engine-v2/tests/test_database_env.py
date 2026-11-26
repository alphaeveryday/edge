"""A database named in the environment must be reached without AWS, and never in clear text."""
import sys
from unittest.mock import Mock

import pytest

from edge_analysis_v2.sources import database as sources
from edge_analysis_v2.storage import database as db

ENV = {"EDGE_ANALYSIS_DB_HOST": "pg.internal", "EDGE_ANALYSIS_DB_PORT": "6432", "EDGE_ANALYSIS_DB_NAME": "edge_oci",
       "EDGE_ANALYSIS_DB_WRITER_USER": "writer", "EDGE_ANALYSIS_DB_WRITER_PASSWORD": "writer-only",
       "EDGE_ANALYSIS_DB_READER_USER": "reader", "EDGE_ANALYSIS_DB_READER_PASSWORD": "reader-only"}


@pytest.fixture
def host(tmp_path, monkeypatch):
    """Name a database in the environment and make any use of boto3 fail the test."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "boto3", None)
    monkeypatch.setattr(db, "_SESSIONS", {})
    connect = Mock()
    monkeypatch.setattr(db.psycopg, "connect", connect)
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    return ca, connect


def test_results_connect_to_the_named_host_without_aws(host):
    # The OCI hosts have no Secrets Manager and no tunnel; ignoring the environment would call both.
    ca, connect = host
    assert db.connect_results(ca, cloud=True) is connect.return_value
    args = connect.call_args.kwargs
    assert (args["host"], args["port"], args["dbname"]) == ("pg.internal", 6432, "edge_oci")
    assert (args["user"], args["password"]) == ("writer", "writer-only")
    assert args["sslmode"] == "verify-full" and args["sslrootcert"] == str(ca.resolve())
    assert "hostaddr" not in args and args["autocommit"] is True and args["options"] == db.RESULT_OPTIONS


def test_sources_connect_to_the_named_host_read_only_without_aws(host):
    ca, connect = host
    connection = sources.connect_sources(ca, cloud=True)
    args = connect.call_args.kwargs
    assert (args["host"], args["port"], args["dbname"]) == ("pg.internal", 6432, "edge_oci")
    assert (args["user"], args["password"]) == ("reader", "reader-only")
    assert args["sslmode"] == "verify-full" and "default_transaction_read_only=on" in args["options"]
    assert "hostaddr" not in args and connection.read_only is True


def test_role_names_default_to_the_dedicated_identities(host, monkeypatch):
    # Leaving the user unset must not fall through to an administrator or to libpq's own default.
    ca, connect = host
    monkeypatch.delenv("EDGE_ANALYSIS_DB_WRITER_USER")
    monkeypatch.delenv("EDGE_ANALYSIS_DB_READER_USER")
    db.connect_results(ca)
    assert connect.call_args.kwargs["user"] == db.DB_USER
    sources.connect_sources(ca)
    assert connect.call_args.kwargs["user"] == "edge_analysis_v2_reader"


def test_require_omits_the_root_certificate(host, monkeypatch):
    # libpq verifies under ``require`` whenever a root certificate is given; the image ships the RDS bundle.
    ca, connect = host
    monkeypatch.setenv("EDGE_ANALYSIS_DB_SSLMODE", "require")
    db.connect_results(ca)
    assert connect.call_args.kwargs["sslmode"] == "require" and "sslrootcert" not in connect.call_args.kwargs


@pytest.mark.parametrize("change", [{"EDGE_ANALYSIS_DB_WRITER_PASSWORD": ""}, {"EDGE_ANALYSIS_DB_SSLMODE": "prefer"},
                                    {"EDGE_ANALYSIS_DB_SSLMODE": "disable"}])
def test_missing_password_or_clear_text_mode_is_rejected_before_connection(host, monkeypatch, change):
    ca, connect = host
    for key, value in change.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(db.ResultDatabaseError) as rejected:
        db.connect_results(ca)
    assert not rejected.value.transient
    connect.assert_not_called()
