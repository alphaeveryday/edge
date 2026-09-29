"""Connect synchronous tool execution to committed PostgreSQL evidence."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from .tool_store import ToolStore


class ToolInputError(ValueError):
    """A trusted, safe validation message that the caller can correct."""


class ToolExecutionError(RuntimeError):
    """A failed calculation whose audit record can be retrieved by execution ID."""

    def __init__(self, tool_run_id: str, message: str = "Tool execution failed"):
        self.tool_run_id = tool_run_id
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
            self._store.save_run(**saved, tool_run_id=run_id, output=None,
                                 finished_at=datetime.now(timezone.utc),
                                 error_message=message)
            raise ToolExecutionError(run_id, message) from None
        if not isinstance(output, dict) or not isinstance(output.get("tool_run_id"), str):
            raise ValueError("Tool response requires an execution identifier")
        return self._store.save_run(**saved, tool_run_id=output["tool_run_id"], output=output,
                                    finished_at=datetime.now(timezone.utc))
