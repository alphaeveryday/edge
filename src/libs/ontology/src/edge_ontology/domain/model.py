from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TypeSpec:
    type_id: str
    predicates: list[str]
    required_roles: list[str]
    note: str = ""
    stage: bool = False
