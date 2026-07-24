from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ..constants import DEFAULT_VERSION
from ..domain import TypeSpec


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
            role_names = set(roles) if not isinstance(roles, Mapping) else set(roles.keys())
            for role in spec.required_roles:
                if role not in role_names:
                    violations.append(f"Missing required role {role} for {type_id}")
        return violations
