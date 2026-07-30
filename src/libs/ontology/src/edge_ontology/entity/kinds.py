"""1. 실체(Entity) — 독립적으로 존재하는 것의 종별.

종별마다 `persistence_key` 가 다르다 — ISSUER 는 ticker, COMPANY_ENTITY 는
ticker_or_normalized_name, AUTHORITY_OR_RULE 는 normalized_authority_or_rule …
따라서 실체의 종별을 알아야 그 값을 무엇으로 적재할지가 정해진다.

역할(관계)→종별 결속은 이 층이 아니라 3. 관계층(`..relation`)이 갖는다. 실체는 관계보다
아래 층이라 관계를 몰라도 성립해야 한다(선험적 존재).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .._resource import load_yaml_resource
from ..constants import ENTITY_DIR

ENTITY_KINDS_RESOURCE = "entity_kinds_v0_1.yaml"


@dataclass(frozen=True)
class EntityKind:
    kind: str
    persistence_key: str
    current_mapping: str
    confidence_rule: str
    used_for: str


@lru_cache(maxsize=1)
def load_entity_kinds(path: Path | str | None = None) -> Mapping[str, EntityKind]:
    """종별 → EntityKind. persistence_key 가 비면 적재 경로를 못 고르므로 즉시 죽는다."""
    doc = load_yaml_resource(ENTITY_DIR, ENTITY_KINDS_RESOURCE, override=path)
    kinds: dict[str, EntityKind] = {}
    for kind, body in (doc.get("entity_kinds") or {}).items():
        body = body or {}
        key = str(body.get("persistence_key") or "")
        if not key:
            raise ValueError(f"실체 종별 {kind} 에 persistence_key 가 없다 — 적재 키를 못 고른다")
        kinds[kind] = EntityKind(
            kind=kind,
            persistence_key=key,
            current_mapping=str(body.get("current_mapping") or ""),
            confidence_rule=str(body.get("confidence_rule") or ""),
            used_for=str(body.get("used_for") or ""),
        )
    if not kinds:
        raise ValueError("실체 종별이 하나도 없다")
    return MappingProxyType(kinds)
