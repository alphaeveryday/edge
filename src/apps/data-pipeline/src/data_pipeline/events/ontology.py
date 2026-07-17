"""Self-contained event ontology registry for the event assembly step.

The event-type vocabulary is bundled as ``ontology_ref.txt`` (one type per line,
``TYPE | pred:A,B STAGE | req:ROLE1,ROLE2 | note``). This mirrors the alphamale
event ontology contract so the daily pipeline can gate/validate model output
without an external dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Iterable, Mapping

DEFAULT_VERSION = "0.1"
_RESOURCE = "ontology_ref.txt"


@dataclass(frozen=True, slots=True)
class TypeSpec:
    type_id: str
    predicates: tuple[str, ...]
    required_roles: tuple[str, ...]
    note: str = ""
    stage: bool = False


@dataclass
class Registry:
    types: dict[str, TypeSpec]
    version: str = DEFAULT_VERSION

    def validate(
        self,
        type_id: str,
        predicate: str | None = None,
        roles: Iterable[str] | Mapping[str, object] | None = None,
    ) -> list[str]:
        spec = self.types.get(type_id)
        if spec is None:
            return [f"Unknown type_id: {type_id}"]
        violations: list[str] = []
        if predicate is not None and predicate not in spec.predicates:
            violations.append(f"Disallowed predicate {predicate!r} for {type_id}")
        if roles is not None:
            names = set(roles) if not isinstance(roles, Mapping) else set(roles.keys())
            for role in spec.required_roles:
                if role not in names:
                    violations.append(f"Missing required role {role} for {type_id}")
        return violations


def _parse_line(line: str) -> TypeSpec:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        raise ValueError(f"Malformed ontology line: {line!r}")
    type_id, pred_segment, req_segment, *note_parts = parts
    pred_text = pred_segment.removeprefix("pred:").strip()
    stage = bool(re.search(r"\bSTAGE\b", pred_text))
    pred_text = re.sub(r"\bSTAGE\b", "", pred_text).strip()
    predicates = tuple(p.strip() for p in pred_text.split(",") if p.strip())
    roles = tuple(r.strip() for r in req_segment.removeprefix("req:").split(",") if r.strip())
    return TypeSpec(
        type_id=type_id,
        predicates=predicates,
        required_roles=roles,
        note=" | ".join(p for p in note_parts if p),
        stage=stage,
    )


def load_registry(version: str = DEFAULT_VERSION) -> Registry:
    text = resources.files("data_pipeline.events").joinpath(_RESOURCE).read_text(encoding="utf-8")
    types: dict[str, TypeSpec] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        spec = _parse_line(line)
        types[spec.type_id] = spec
    return Registry(types=types, version=version)


__all__ = ["Registry", "TypeSpec", "load_registry", "DEFAULT_VERSION"]
