"""Persist tool evidence independently of the analysis publication transaction."""

import json
from datetime import datetime
from typing import Any

from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def _json_value(value: Any) -> None:
    """Reject values whose type or value would change during JSON encoding."""
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        for child in value.values():
            _json_value(child)
    elif type(value) is list:
        for child in value:
            _json_value(child)
    elif value is not None and type(value) not in (str, int, float, bool):
        raise ValueError("Evidence must contain JSON values only")
    json.dumps(value, allow_nan=False)


class ToolStore:
    """Store immutable definitions and completed or failed tool executions.

    Args:
        connection: Dedicated autocommit PostgreSQL connection. The caller owns
            its lifecycle and must not share it with analysis transactions.
    """

    def __init__(self, connection: Connection):
        self.connection = connection
        self._require_idle()

    def _require_idle(self) -> None:
        if (not self.connection.autocommit
                or self.connection.info.transaction_status != TransactionStatus.IDLE):
            raise ValueError("Tool audit requires a dedicated idle autocommit connection")

    def register_definition(
        self, *, tool_id: str, function_name: str, version: str,
        description: str, source_names: list[str], formula_latex: str | None = None,
    ) -> None:
        """Register a definition, rejecting changes to an existing identity.

        Args:
            tool_id: Stable definition identifier.
            function_name: Public tool function name.
            version: Version of the calculation implementation.
            description: Administrator-facing explanation of the calculation.
            source_names: Human-readable names of the source datasets.
            formula_latex: Mathematical definition, when applicable.

        Raises:
            ValueError: The same identifier already describes a different tool.
        """
        self._require_idle()
        definition = dict(tool_id=tool_id, function_name=function_name, version=version,
                          description=description, source_names=source_names,
                          formula_latex=formula_latex)
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO tool_definitions
                    (tool_id, function_name, version, description, source_names, formula_latex)
                VALUES (%(tool_id)s, %(function_name)s, %(version)s, %(description)s,
                        %(source_names)s, %(formula_latex)s)
                ON CONFLICT (tool_id) DO NOTHING
                """, definition)
            cur.execute("SELECT * FROM tool_definitions WHERE tool_id = %s", (tool_id,))
            if cur.fetchone() != definition:
                raise ValueError("Existing tool definition is immutable; use a new version")

    def save_run(
        self, *, tool_run_id: str, tool_id: str, analysis_kind: str, analysis_id: str,
        arguments: dict, context: dict, output: dict | None,
        started_at: datetime, finished_at: datetime, error_message: str | None = None,
    ) -> dict | None:
        """Commit evidence and return exactly the output read back from PostgreSQL.

        Args:
            tool_run_id: Identifier already assigned to this execution.
            tool_id: Registered tool definition identifier.
            analysis_kind: Either movement or outlook.
            analysis_id: Previously committed parent analysis identifier.
            arguments: Actual tool arguments; no copy of source rows.
            context: Source observation timestamps needed for later lookup.
            output: Complete agent-visible response, or None on failure.
            started_at: Timezone-aware execution start time.
            finished_at: Timezone-aware execution completion time.
            error_message: Sanitized failure message; never credentials or raw exceptions.

        Returns:
            Stored response on success, or None for a recorded failure.

        Raises:
            ValueError: Invalid identity, JSON, timing, or success/failure combination.
        """
        self._require_idle()
        if analysis_kind not in ("movement", "outlook"):
            raise ValueError("Unknown analysis kind")
        if any(t.utcoffset() is None for t in (started_at, finished_at)) or finished_at < started_at:
            raise ValueError("Execution times must be timezone-aware and ordered")
        if type(arguments) is not dict or type(context) is not dict:
            raise ValueError("Arguments and context must be objects")
        if output is None:
            if not isinstance(error_message, str) or not error_message.strip():
                raise ValueError("Failed execution requires an error message")
        elif (type(output) is not dict or output.get("tool_run_id") != tool_run_id
              or "result" not in output or error_message is not None):
            raise ValueError("Response must identify this successful execution")
        for value in (arguments, context, output):
            _json_value(value)
        with self.connection.transaction(), self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO tool_runs
                    (tool_run_id, tool_id, movement_analysis_id, outlook_analysis_id,
                     arguments, context, output, status, error_message, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING output
                """, (tool_run_id, tool_id,
                      analysis_id if analysis_kind == "movement" else None,
                      analysis_id if analysis_kind == "outlook" else None,
                      Jsonb(arguments), Jsonb(context), Jsonb(output) if output is not None else None,
                      "completed" if output is not None else "failed", error_message,
                      started_at, finished_at))
            stored = cur.fetchone()["output"]
        return stored

    def get_run(self, tool_run_id: str) -> dict | None:
        """Read execution evidence together with its administrator-facing definition.

        Args:
            tool_run_id: Execution identifier attached to the generated item.

        Returns:
            Joined evidence record, or None when the execution does not exist.
        """
        self._require_idle()
        with self.connection.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT r.*, d.function_name, d.version, d.description,
                       d.formula_latex, d.source_names
                FROM tool_runs r JOIN tool_definitions d USING (tool_id)
                WHERE r.tool_run_id = %s
                """, (tool_run_id,))
            return cur.fetchone()
