"""3. 관계(Relation) — 실체를 잇는 구조적 연결."""
from .model import MINT, NONE, REGISTRY, Relation, RelationVocabulary
from .vocabulary import concept_key, load_relations, resolve_authority, role_entity_kind

__all__ = [
    "MINT",
    "NONE",
    "REGISTRY",
    "Relation",
    "RelationVocabulary",
    "concept_key",
    "load_relations",
    "resolve_authority",
    "role_entity_kind",
]
