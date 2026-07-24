"""온톨로지 SSOT 읽기 뷰 — registry·quantities·lifecycle·thread 계약의 단일 진입점.

실험실(event-ontology repo) normalize.ontology_view 의 이식이다. 정규화·스레딩이 필요로
하는 파생값(수량 역할·identity 역할·stage 어휘·novelty 어휘)을 타입당 한 객체로 응축한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from .bundle import load_ontology_bundle
from .resource_io import read_text_resource
from .unified import load_lifecycle_models, load_type_definitions

THREAD_CONTRACT_RESOURCE = "news_thread_contract_v0_1.yaml"


@dataclass(frozen=True)
class TypeView:
    type_id: str
    family: str | None
    lifecycle_model: str | None
    stage_sensitive: bool
    predicates: frozenset[str]
    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...]
    quantity_roles: frozenset[str]
    currency_roles: frozenset[str]
    role_menu: frozenset[str]
    identity_required: tuple[str, ...]
    identity_optional: tuple[str, ...]
    missing_identity_policy: str
    stages: frozenset[str]


@dataclass(frozen=True)
class OntologyView:
    types: Mapping[str, TypeView]
    novelty_statuses: frozenset[str]


def _load_thread_contract() -> dict[str, Any]:
    contract = yaml.safe_load(read_text_resource(THREAD_CONTRACT_RESOURCE))
    if not isinstance(contract, dict):
        raise ValueError("news thread contract must parse into a mapping")
    return contract


@lru_cache(maxsize=1)
def load_ontology_view() -> OntologyView:
    load_ontology_bundle()  # 소비 전에 registry/profiles/features 교차검증
    definitions = load_type_definitions()
    lifecycle_models = load_lifecycle_models()
    contract_types = _load_thread_contract().get("types") or {}

    types: dict[str, TypeView] = {}
    novelty: set[str] = set()
    for type_id, spec in definitions.items():
        roles = spec.get("roles") or {}
        required = tuple(roles.get("required") or [])
        optional = tuple(roles.get("optional") or [])
        quantities = spec.get("quantities") or {}
        quantity_roles = frozenset(quantities.keys())
        currency_roles = frozenset(
            role for role, meta in quantities.items() if (meta or {}).get("unit_family") == "CURRENCY"
        )
        model = lifecycle_models.get(spec.get("lifecycle_model")) or {}
        stages = frozenset(model.get("stages") or []) | frozenset(model.get("terminal") or [])

        rule = contract_types.get(type_id) or {}
        identity = rule.get("identity") or {}
        novelty.update(rule.get("novelty_statuses") or [])

        types[type_id] = TypeView(
            type_id=type_id,
            family=spec.get("family"),
            lifecycle_model=spec.get("lifecycle_model"),
            stage_sensitive=bool(spec.get("stage_sensitive")),
            predicates=frozenset(spec.get("predicates") or []),
            required_roles=required,
            optional_roles=optional,
            quantity_roles=quantity_roles,
            currency_roles=currency_roles,
            role_menu=frozenset(required) | frozenset(optional) | quantity_roles,
            identity_required=tuple(identity.get("required") or []),
            identity_optional=tuple(identity.get("optional_discriminators") or []),
            missing_identity_policy=str(rule.get("missing_identity_policy") or "EMIT_UNKNOWN_LINK_ONLY"),
            stages=stages,
        )
    return OntologyView(types=MappingProxyType(types), novelty_statuses=frozenset(novelty))
