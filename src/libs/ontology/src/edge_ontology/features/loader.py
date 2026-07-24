from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from ..profiles import load_profiles
from ..unified import load_common_features, load_type_definitions

FEATURE_SECTIONS = ("quantities", "entity_state", "derived")
PIT_FORBIDDEN = re.compile(r"(^|_)(realized|post_event|car|next_day)(_|$)|^ar_|_ar$")


def _common_ids(specs: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for block in (specs.get("common_blocks") or {}).values():
        ids |= set(block.keys())
    return ids


def _type_feature_ids(spec: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for section in FEATURE_SECTIONS:
        ids |= set((spec.get(section) or {}).keys())
    return ids


def _feature_view(definitions: Mapping[str, Mapping[str, Any]], common: Mapping[str, Any]) -> dict[str, Any]:
    types: dict[str, Any] = {}
    for type_id, spec in definitions.items():
        entry: dict[str, Any] = {"primary_roles": list((spec.get("roles") or {}).get("primary") or [])}
        for section in FEATURE_SECTIONS:
            if spec.get(section):
                entry[section] = spec[section]
        types[type_id] = entry
    return {
        "meta": {**(common.get("meta") or {}), "type_count": len(types)},
        "common_blocks": common.get("common_blocks") or {},
        "types": types,
    }


def _validate_feature_registry(specs: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]]) -> None:
    errors: list[str] = []
    types = specs.get("types") or {}
    if not isinstance(types, Mapping):
        raise ValueError("Feature registry 'types' must be a mapping")

    missing = sorted(set(profiles) - set(types))
    extra = sorted(set(types) - set(profiles))
    if missing:
        errors.append(f"unspecified event types: {missing}")
    if extra:
        errors.append(f"specs for unknown event types: {extra}")

    declared_count = (specs.get("meta") or {}).get("type_count")
    if declared_count != len(types):
        errors.append(f"meta.type_count={declared_count!r} does not match merged type count {len(types)}")

    common = _common_ids(specs)
    for type_id, spec in types.items():
        primary_roles = spec.get("primary_roles")
        if not primary_roles:
            errors.append(f"{type_id}: primary_roles missing")

        type_feature_ids = _type_feature_ids(spec)

        seen: set[str] = set()
        for section in FEATURE_SECTIONS:
            for fid, meta in (spec.get(section) or {}).items():
                if fid in seen:
                    errors.append(f"{type_id}: duplicate feature id {fid}")
                seen.add(fid)
                if not isinstance(meta, Mapping) or not meta.get("desc"):
                    errors.append(f"{type_id}.{fid}: desc required")
                    continue
                if PIT_FORBIDDEN.search(fid.lower()):
                    errors.append(f"{type_id}.{fid}: outcome-flavored id violates PIT rule")

        declared = type_feature_ids | common
        for fid, meta in (spec.get("derived") or {}).items():
            if not meta.get("formula"):
                errors.append(f"{type_id}.{fid}: formula required")
            inputs = meta.get("inputs") or []
            if not inputs:
                errors.append(f"{type_id}.{fid}: inputs required")
            for ref in inputs:
                if ref not in declared:
                    errors.append(f"{type_id}.{fid}: input '{ref}' not declared")

        profile = profiles.get(type_id)
        if profile is None:
            continue
        roles = set(profile.get("required_roles", [])) | set(profile.get("optional_roles", []))

        for role in primary_roles or []:
            if role not in roles:
                errors.append(f"{type_id}: primary_role '{role}' not in profile roles")

    if errors:
        sample = "\n".join(errors[:20])
        more = "" if len(errors) <= 20 else f"\n... {len(errors) - 20} more"
        raise ValueError(f"Feature registry validation failed:\n{sample}{more}")


def load_feature_registry(
    path: Path | str | None = None,
    *,
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the feature-registry view from the unified type definitions.

    ``path`` optionally overrides the ``types/`` directory.
    """
    merged = _feature_view(load_type_definitions(path), load_common_features())
    loaded_profiles = profiles or load_profiles()
    _validate_feature_registry(merged, loaded_profiles)
    return merged
