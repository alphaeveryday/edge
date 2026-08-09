"""Shared trust-boundary contract for JSON returned by analysis models."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..observability import record

FORBIDDEN_EXECUTABLE_KEYS = frozenset({"sql", "query", "view_name"})
MAX_MODEL_RESPONSE_BYTES = 256 * 1024
MAX_MODEL_LIST_ITEMS = 64


class ModelContractError(ValueError):
    """Base class for deterministic model-output contract violations."""


class ModelSchemaError(ModelContractError):
    """The model returned fields that could be interpreted as executable data."""

    code = "MODEL_SCHEMA_REJECTED"

    def __init__(self, keys: tuple[str, ...]) -> None:
        self.keys = keys
        super().__init__(f"{self.code}: forbidden root keys: {', '.join(keys)}")


class ModelShapeError(ModelContractError):
    """The model returned the wrong JSON type for a stage field."""

    code = "MODEL_SHAPE_REJECTED"

    def __init__(self, field: str, expected: str) -> None:
        self.field = field
        super().__init__(f"{self.code}: {field} must be {expected}")


class ModelLimitError(ModelContractError):
    """The model returned a structurally valid but operationally unsafe payload."""

    code = "MODEL_LIMIT_REJECTED"

    def __init__(self, field: str, limit: int) -> None:
        self.field = field
        self.limit = limit
        super().__init__(f"{self.code}: {field} exceeds limit {limit}")


def validate_model_output(output: dict[str, Any]) -> dict[str, Any]:
    """Reject executable root fields before a stage interprets model output."""
    if not isinstance(output, dict):
        record("llm.model_shape_rejected", code=ModelShapeError.code,
               field="$", expected="object")
        raise ModelShapeError("$", "an object")
    keys = tuple(sorted(FORBIDDEN_EXECUTABLE_KEYS.intersection(output)))
    if keys:
        record("llm.model_schema_rejected", code=ModelSchemaError.code, keys=list(keys))
        raise ModelSchemaError(keys)
    try:
        encoded = json.dumps(
            output, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        record("llm.model_shape_rejected", code=ModelShapeError.code,
               field="$", expected="JSON-serializable object")
        raise ModelShapeError("$", "a JSON-serializable object") from exc
    size = len(encoded)
    if size > MAX_MODEL_RESPONSE_BYTES:
        record("llm.model_limit_rejected", code=ModelLimitError.code,
               field="$", limit=MAX_MODEL_RESPONSE_BYTES, actual=size)
        raise ModelLimitError("$", MAX_MODEL_RESPONSE_BYTES)
    return output


def ask_checked(ask: Callable[[str, str], dict[str, Any]],
                system: str, user: str) -> dict[str, Any]:
    """Call a model and validate its response at the untrusted boundary."""
    return validate_model_output(ask(system, user))


def list_field(output: dict[str, Any], field: str) -> list[Any]:
    """Read an optional list field without letting strings/dicts masquerade as lists."""
    value = output.get(field, [])
    if not isinstance(value, list):
        record("llm.model_shape_rejected", code=ModelShapeError.code,
               field=field, expected="array")
        raise ModelShapeError(field, "an array")
    if len(value) > MAX_MODEL_LIST_ITEMS:
        record("llm.model_limit_rejected", code=ModelLimitError.code,
               field=field, limit=MAX_MODEL_LIST_ITEMS, actual=len(value))
        raise ModelLimitError(field, MAX_MODEL_LIST_ITEMS)
    return value


def object_field(output: dict[str, Any], field: str) -> dict[str, Any]:
    """Read an optional object field without silently coercing another JSON type."""
    value = output.get(field, {})
    if not isinstance(value, dict):
        record("llm.model_shape_rejected", code=ModelShapeError.code,
               field=field, expected="object")
        raise ModelShapeError(field, "an object")
    return value


__all__ = ["FORBIDDEN_EXECUTABLE_KEYS", "MAX_MODEL_LIST_ITEMS",
           "MAX_MODEL_RESPONSE_BYTES", "ModelContractError", "ModelLimitError",
           "ModelSchemaError", "ModelShapeError", "ask_checked", "list_field",
           "object_field", "validate_model_output"]
