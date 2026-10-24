"""Result connections must never use administrator or alternate-host credentials."""
import json
from unittest.mock import Mock

import psycopg
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


def writer_session(password="test-only"):
    session = Mock()
    session.client.return_value.get_secret_value.side_effect = lambda **_: {"SecretString": json.dumps(dict(
        username=db.DB_USER, host=db.DB_HOST, port=5432, dbname="edge", password=password))}
    return session


def test_secret_is_fetched_once_and_again_after_a_refused_connection(tmp_path, monkeypatch):
    # Each analysis opens tens of connections; a rotated password must still recover without a restart.
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    session = writer_session()
    attempts = []
    def connect(**kwargs):
        attempts.append(kwargs["password"])
        if len(attempts) == 3:
            raise psycopg.OperationalError("password authentication failed")
        return Mock()
    monkeypatch.setattr(db.psycopg, "connect", connect)
    db.connect_results(ca, session=session)
    db.connect_results(ca, session=session)
    assert session.client.return_value.get_secret_value.call_count == 1
    with pytest.raises(db.ResultDatabaseError) as refused:
        db.connect_results(ca, session=session)
    assert refused.value.transient
    db.connect_results(ca, session=session)
    assert session.client.return_value.get_secret_value.call_count == 2


def test_identity_mismatch_is_not_transient(tmp_path, monkeypatch):
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    session = writer_session()
    session.client.return_value.get_secret_value.side_effect = lambda **_: {"SecretString": json.dumps({"username": "other"})}
    monkeypatch.setattr(db.psycopg, "connect", Mock())
    with pytest.raises(db.ResultDatabaseError) as rejected:
        db.connect_results(ca, session=session)
    assert not rejected.value.transient


def test_retry_repeats_only_connection_failures_within_the_time_bound():
    clock, waits = [0.0], []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    def dropped():
        raise psycopg.errors.AdminShutdown("terminating connection due to administrator command")
    with pytest.raises(psycopg.errors.AdminShutdown):
        db.retry_transient(dropped, seconds=10, sleep=sleep, clock=lambda: clock[0], jitter=lambda low, high: high)
    assert waits == [0.5, 1, 2, 4] and sum(waits) <= 10
    spread, ticking = [], [0.0]
    def jittered(seconds):
        spread.append(seconds)
        ticking[0] += seconds
    with pytest.raises(psycopg.errors.AdminShutdown):
        db.retry_transient(dropped, seconds=10, sleep=jittered, clock=lambda: ticking[0])
    assert all(low <= wait <= high for wait, (low, high) in zip(spread, [(0.25, 0.5), (0.5, 1), (1, 2), (2, 4)]))
    for error in (psycopg.errors.QueryCanceled("statement timeout"), ValueError("bad input"),
                  db.ResultDatabaseError("identity", transient=False)):
        calls = []
        def fail():
            calls.append(1)
            raise error
        with pytest.raises(type(error)):
            db.retry_transient(fail, sleep=sleep, clock=lambda: clock[0])
        assert calls == [1]
    outcomes = iter([db.ResultDatabaseError("refused", transient=True), "done"])
    def flaky():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    assert db.retry_transient(flaky, sleep=sleep, clock=lambda: clock[0]) == "done"


def test_callers_that_build_a_session_per_call_do_not_grow_the_cache(tmp_path, monkeypatch):
    # The control Lambda can create a session on every invocation of a long-lived container.
    ca = tmp_path / "ca.pem"
    ca.write_text("test")
    monkeypatch.setattr(db.psycopg, "connect", lambda **_: Mock())
    for _ in range(20):
        db.connect_results(ca, session=writer_session())
    assert len(db._SECRETS) <= db._CACHED_SESSIONS
