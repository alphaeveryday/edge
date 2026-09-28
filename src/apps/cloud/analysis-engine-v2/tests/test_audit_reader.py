"""Protect query boundaries before any database access."""

from unittest.mock import Mock

import pytest
from psycopg.pq import TransactionStatus

from edge_analysis_v2.audit_reader import read_analysis_evidence


def test_unknown_kind_never_reaches_sql():
    connection = Mock()
    with pytest.raises(ValueError, match="kind"):
        read_analysis_evidence(connection, "movement; DROP TABLE tool_runs", "id")
    connection.transaction.assert_not_called()


@pytest.mark.parametrize("autocommit, status", [
    (False, TransactionStatus.IDLE), (True, TransactionStatus.INTRANS),
])
def test_reader_does_not_join_or_commit_callers_transaction(autocommit, status):
    connection = Mock(autocommit=autocommit)
    connection.info.transaction_status = status
    with pytest.raises(ValueError, match="idle"):
        read_analysis_evidence(connection, "movement", "id")
    connection.transaction.assert_not_called()
