from __future__ import annotations

from pathlib import Path

from ..constants import DEFAULT_VERSION
from ..domain import TypeSpec
from ..unified import load_type_definitions
from .model import Registry


def load_registry(path: Path | str | None = None, version: str = DEFAULT_VERSION) -> Registry:
    """Build the type registry view from the unified type definitions.

    ``path`` optionally overrides the ``types/`` directory.
    """
    types: dict[str, TypeSpec] = {}
    for type_id, spec in load_type_definitions(path).items():
        roles = spec.get("roles") or {}
        types[type_id] = TypeSpec(
            type_id=type_id,
            predicates=list(spec.get("predicates") or []),
            required_roles=list(roles.get("required") or []),
            note=spec.get("note", "") or "",
            stage=bool(spec.get("stage_sensitive")),
        )
    return Registry(types=types, version=version)
