"""Connect synchronous tool execution to committed PostgreSQL evidence."""

import logging
import os
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from edge_analysis_v2.storage.tool_runs import ToolStore

LOG = logging.getLogger(__name__)


class ToolInputError(ValueError):
    """A trusted, safe validation message that the caller can correct."""

    code = 'invalid_arguments'
    retryable = True


class ToolDataUnavailableError(ToolInputError):
    """The frozen source cannot answer; continue with alternatives, not the same request."""

    code = 'data_unavailable'
    retryable = False


class ToolSourceDataError(ToolInputError):
    """A known source defect prevents this calculation; other sources remain usable."""

    code = 'invalid_source_data'
    retryable = False


class ToolExecutionError(RuntimeError):
    """A failed calculation whose audit record can be retrieved by execution ID."""

    def __init__(self, tool_run_id: str, message: str = "Tool execution failed", *, recoverable: bool = False,
                 code: str = "tool_execution_failed", retryable: bool = False):
        self.tool_run_id = tool_run_id
        self.recoverable = recoverable
        self.code = code
        self.retryable = retryable
        super().__init__(f"{message}; tool_run_id={tool_run_id}")


class AuditedExecution:
    """Return tool responses only after their audit records have committed.

    Args:
        call: Existing synchronous function accepting tool name and arguments.
        store: Store with a dedicated connection, owned by the caller.
        definitions: ToolStore.register_definition keyword dictionaries.
        analysis_kind: Either movement or outlook.
        analysis_id: Already committed parent analysis identifier.
        context: Observation timestamps used by the bound executor.
    """

    def __init__(
        self, call: Callable[[str, dict], dict], store: ToolStore, *,
        definitions: list[dict], analysis_kind: str, analysis_id: str, context: dict,
    ):
        if analysis_kind not in ("movement", "outlook"):
            raise ValueError("Unknown analysis kind")
        self._tools = {d["function_name"]: d["tool_id"] for d in definitions}
        if len(self._tools) != len(definitions):
            raise ValueError("Each function must have exactly one active version")
        for definition in definitions:
            store.register_definition(**definition)
        self._call = call
        self._store = store
        self._owner = dict(analysis_kind=analysis_kind, analysis_id=analysis_id)
        self._context = deepcopy(context)

    def call(self, name: str, arguments: dict) -> dict:
        """Execute once, persist evidence, then deliver the stored response.

        Args:
            name: Registered function name.
            arguments: Original tool arguments, preserved before execution.

        Returns:
            Exact response committed by the evidence store.

        Raises:
            ToolExecutionError: Calculation failed and its failure was recorded.
            ValueError: Tool identity or returned evidence is invalid.
            Exception: Persistence failed; no response is delivered or retry attempted.
        """
        if name not in self._tools:
            raise ValueError("Unknown tool")
        saved = dict(tool_id=self._tools[name], **self._owner,
                     arguments=deepcopy(arguments), context=deepcopy(self._context),
                     started_at=datetime.now(timezone.utc))
        try:
            output = self._call(name, deepcopy(arguments))
        except Exception as error:
            run_id = uuid4().hex
            message = str(error) if isinstance(error, ToolInputError) else "Tool execution failed"
            if not isinstance(error, ToolInputError):
                # The agent sees only the generic message. The text may carry secrets, so operators get the
                # type and the raising line instead (ALPHA-1162).
                where = traceback.extract_tb(error.__traceback__)[-1]
                LOG.warning('Tool failed tool=%s tool_run_id=%s type=%s at=%s:%s in %s', name, run_id,
                            type(error).__name__, os.path.basename(where.filename), where.lineno, where.name)
            self._store.save_run(**saved, tool_run_id=run_id, output=None,
                                 finished_at=datetime.now(timezone.utc),
                                 error_message=message)
            recoverable = isinstance(error, ToolInputError)
            raise ToolExecutionError(run_id, message, recoverable=recoverable,
                                     code=error.code if recoverable else 'tool_execution_failed',
                                     retryable=error.retryable if recoverable else False) from None
        if not isinstance(output, dict) or not isinstance(output.get("tool_run_id"), str):
            raise ValueError("Tool response requires an execution identifier")
        return self._store.save_run(**saved, tool_run_id=output["tool_run_id"], output=output,
                                    finished_at=datetime.now(timezone.utc))
