"""Ontology metadata and rule definitions. Public root exports remain stable."""
from .oms.properties import Attribute, load_common_attributes
from .constants import DEFAULT_VERSION, ONTOLOGY_VERSION
from .oms.entity import (AuthorityRegistry, EntityKind, load_authority_registry,
                     load_entity_kinds, normalize_name)
from .oms.labels import EventTypeLabel, event_type_label_ko, event_type_labels_ko
from .oms.events import (ProcessRegistry, ProcessType, load_lifecycle_models,
                      load_process_registry, load_type_definitions)
from .oms.roles import (Relation, RelationVocabulary, concept_key, is_policy_excluded, load_relations,
                       resolve_authority, role_entity_kind)

__all__ = [
    "DEFAULT_VERSION",
    "ONTOLOGY_VERSION",
    "Attribute",
    "AuthorityRegistry",
    "EntityKind",
    "EventTypeLabel",
    "event_type_label_ko",
    "event_type_labels_ko",
    "ProcessRegistry",
    "ProcessType",
    "Relation",
    "RelationVocabulary",
    "concept_key",
    "is_policy_excluded",
    "load_authority_registry",
    "load_common_attributes",
    "load_entity_kinds",
    "load_lifecycle_models",
    "load_process_registry",
    "load_relations",
    "load_type_definitions",
    "normalize_name",
    "resolve_authority",
    "role_entity_kind",
]
