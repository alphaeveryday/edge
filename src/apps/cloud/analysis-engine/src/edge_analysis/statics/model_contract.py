"""Shared trust-boundary contract for JSON returned by analysis models."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..observability import record

FORBIDDEN_EXECUTABLE_KEYS = frozenset({"sql", "query", "view_name"})


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
    return value


__all__ = ["FORBIDDEN_EXECUTABLE_KEYS", "ModelContractError", "ModelSchemaError",
           "ModelShapeError", "ask_checked", "list_field", "validate_model_output"]
