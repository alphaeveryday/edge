"""Dedicated result database connection through the fixed local SSM tunnel."""
import json
from pathlib import Path

import psycopg

DB_HOST = "edge-dev.cru6wwggoppd.ap-northeast-2.rds.amazonaws.com"
DB_USER = "edge_analysis_v2_writer"


class ResultDatabaseError(RuntimeError):
    """Credential-free failure safe to display in the development dashboard."""


def connect_results(ca_path: Path, *, session=None):
    """Connect with the result writer identity and return an idle connection.

    Args:
        ca_path: Verified regional RDS certificate bundle on disk.
        session: Optional AWS session for isolated tests; defaults to edge-v2-writer.

    Returns:
        Caller-owned autocommit PostgreSQL connection for ToolStore or explicit transactions.

    Raises:
        ResultDatabaseError: Credentials, certificate, or database connection unavailable.
    """
    try:
        ca = Path(ca_path).resolve(strict=True)
        if not ca.is_file():
            raise ValueError("CA bundle must be a file")
        if session is None:
            import boto3
            session = boto3.Session(profile_name="edge-v2-writer", region_name="ap-northeast-2")
        response = session.client("secretsmanager").get_secret_value(SecretId="edge/analysis-v2/writer")
        secret = json.loads(response["SecretString"])
        expected = dict(username=DB_USER, host=DB_HOST, port=5432, dbname="edge")
        if (any(secret.get(key) != value for key, value in expected.items())
                or not isinstance(secret.get("password"), str) or not secret["password"]):
            raise ValueError("Writer secret identity mismatch")
        return psycopg.connect(host=DB_HOST, hostaddr="127.0.0.1", port=15433,
                               dbname="edge", user=DB_USER, password=secret["password"],
                               sslmode="verify-full", sslrootcert=str(ca), connect_timeout=10,
                               autocommit=True,
                               options="-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000")
    except Exception:
        raise ResultDatabaseError("Writer connection unavailable; check writer profile, tunnel and RDS CA") from None
