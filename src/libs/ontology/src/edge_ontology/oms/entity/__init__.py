"""1. 실체(Entity) — 독립적으로 존재하는 것."""
from .authority import (REGISTRY_SECTIONS, AuthorityEntry, AuthorityRegistry,
                        load_authority_registry, normalize_name)
from .kinds import EntityKind, load_entity_kinds

__all__ = [
    "REGISTRY_SECTIONS",
    "AuthorityEntry",
    "AuthorityRegistry",
    "EntityKind",
    "load_authority_registry",
    "load_entity_kinds",
    "normalize_name",
]
