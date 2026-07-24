from __future__ import annotations

from pathlib import Path
from typing import Any

from ..unified import load_type_definitions


def load_profiles(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Build the operational profile view from the unified type definitions.

    ``path`` optionally overrides the ``types/`` directory.
    """
    profiles: dict[str, dict[str, Any]] = {}
    for type_id, spec in load_type_definitions(path).items():
        roles = spec.get("roles") or {}
        profiles[type_id] = {
            "event_type_id": type_id,
            "family": spec.get("family"),
            "lifecycle_model": spec.get("lifecycle_model"),
            "allowed_predicates": list(spec.get("predicates") or []),
            "required_roles": list(roles.get("required") or []),
            "optional_roles": list(roles.get("optional") or []),
            "identity_roles": list(roles.get("identity") or []),
            "stage_sensitive": bool(spec.get("stage_sensitive")),
        }
    return profiles
