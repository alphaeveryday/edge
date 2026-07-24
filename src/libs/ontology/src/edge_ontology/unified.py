"""Unified ontology source of truth.

`resources/types/*.yaml` hold one merged definition per event type
(family, predicates, lifecycle_model, stage_sensitive, roles{required,optional,
identity,primary}, note, quantities, entity_state, derived). `lifecycle_models_v0_1.yaml`
defines the ordered stage sequence per lifecycle_model. `common_features_v0_1.yaml`
holds the shared financial pool inherited by every type.

The registry / profiles / feature-registry loaders build their views over these.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .constants import RESOURCE_PACKAGE
from .resource_io import read_text_resource

TYPES_DIR = "types"
LIFECYCLE_RESOURCE_NAME = "lifecycle_models_v0_1.yaml"
COMMON_FEATURES_RESOURCE_NAME = "common_features_v0_1.yaml"


def _yaml_mapping(text: str) -> dict[str, Any]:
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError("Ontology YAML payload must be a mapping")
    return payload


def _iter_type_files(types_dir: Path | str | None) -> list[tuple[str, str]]:
    if types_dir is None:
        root = resources.files(RESOURCE_PACKAGE).joinpath(TYPES_DIR)
        entries = [
            (entry.name, entry.read_text(encoding="utf-8"))
            for entry in root.iterdir()
            if entry.name.endswith(".yaml")
        ]
    else:
        root = Path(types_dir)
        entries = [(path.name, path.read_text(encoding="utf-8")) for path in root.glob("*.yaml")]
    return sorted(entries)


def load_type_definitions(types_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Merged per-type definitions from ``resources/types/*.yaml`` (SSOT)."""
    merged: dict[str, dict[str, Any]] = {}
    for name, text in _iter_type_files(types_dir):
        for type_id, spec in (_yaml_mapping(text).get("types") or {}).items():
            if type_id in merged:
                raise ValueError(f"Duplicate event type across type files: {type_id} ({name})")
            merged[type_id] = spec
    return merged


def load_lifecycle_models(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Ordered stage sequence + terminal states per lifecycle_model (SSOT)."""
    text = read_text_resource(LIFECYCLE_RESOURCE_NAME) if path is None else Path(path).read_text(encoding="utf-8")
    return _yaml_mapping(text).get("models") or {}


def load_common_features(path: Path | str | None = None) -> dict[str, Any]:
    """Shared financial feature pool inherited by every type."""
    text = read_text_resource(COMMON_FEATURES_RESOURCE_NAME) if path is None else Path(path).read_text(encoding="utf-8")
    return _yaml_mapping(text)
