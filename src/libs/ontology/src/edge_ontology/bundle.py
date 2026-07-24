from __future__ import annotations

from typing import Any

from .features import load_feature_registry
from .profiles import load_profiles
from .registry import load_registry


def _validate_bundle(registry: Any, profiles: dict[str, dict[str, Any]], feature_registry: dict[str, Any]) -> None:
    registry_types = set(registry.types)
    profile_types = set(profiles)
    feature_types = set((feature_registry.get("types") or {}).keys())

    missing_profiles = sorted(profile_types - registry_types)
    if missing_profiles:
        raise ValueError(f"Profiles missing from ontology registry: {missing_profiles}")

    missing_features = sorted(feature_types - registry_types)
    if missing_features:
        raise ValueError(f"Feature specs missing from ontology registry: {missing_features}")

    errors: list[str] = []
    for type_id in sorted(profile_types):
        profile_roles = set(profiles[type_id].get("required_roles", [])) | set(profiles[type_id].get("optional_roles", []))
        registry_roles = registry.types[type_id].required_roles
        missing_roles = [role for role in registry_roles if role not in profile_roles]
        if missing_roles:
            errors.append(f"{type_id}: profile missing ontology roles {missing_roles}")
    if errors:
        raise ValueError("Bundle validation failed:\n" + "\n".join(errors))


def load_ontology_bundle() -> dict[str, Any]:
    registry = load_registry()
    profiles = load_profiles()
    feature_registry = load_feature_registry(profiles=profiles)
    _validate_bundle(registry, profiles, feature_registry)
    return {
        "registry": registry,
        "profiles": profiles,
        "feature_registry": feature_registry,
    }
