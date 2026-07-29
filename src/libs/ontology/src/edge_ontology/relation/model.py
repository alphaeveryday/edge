"""3. 관계(Relation) — 실체를 잇는 구조적 연결 하나의 모형."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# 해소 방식 — 관계가 가리키는 실체의 정체성을 무엇으로 삼는가.
REGISTRY = "REGISTRY"   # 닫힌 명부 완전일치
MINT = "MINT"           # 정규화 문자열 채번(열린 집합)
NONE = "NONE"           # 해소하지 않는다 — 외부 키로 오거나 동명이인 위험
SCHEMES = frozenset({REGISTRY, MINT, NONE})


@dataclass(frozen=True)
class Relation:
    """역할 하나 = 사건이 실체(또는 값)를 붙잡는 자리.

    세 축이 각각 다른 것을 말한다:
      - `entity_kind` — **무엇인가**. 없으면 비실체 자리이고 `value_class` 가 이유를 말한다
        (TIME·VALUE·TEXT). 둘 중 정확히 하나만 채워진다.
      - `scheme`/`registry_sections` — **무엇으로 키를 삼는가**. 종과 독립이다.
      - `mints` — 명부에서 못 찾았을 때 채번까지 갈지.
    """
    role_code: str
    entity_kind: str | None = None
    value_class: str | None = None
    scheme: str = NONE
    registry_sections: tuple[str, ...] = ()

    @property
    def is_entity(self) -> bool:
        return self.entity_kind is not None

    @property
    def mints(self) -> bool:
        """멘션에서 개념 키를 채번하는 역할인가."""
        return self.scheme == MINT

    @property
    def uses_registry(self) -> bool:
        return self.scheme == REGISTRY


@dataclass(frozen=True)
class RelationVocabulary:
    """관계 어휘 전량 — 사건 타입이 쓸 수 있는 역할의 닫힌 집합."""
    relations: Mapping[str, Relation]
    mint_fallback: frozenset[str]   # REGISTRY 이면서 미해소 시 채번까지 가는 역할

    def get(self, role_code: str) -> Relation | None:
        return self.relations.get(role_code)

    def kind_of(self, role_code: str) -> str | None:
        """실체 역할이면 종별, 아니면 None(비실체이거나 어휘 밖)."""
        relation = self.relations.get(role_code)
        return relation.entity_kind if relation else None

    def sections_for(self, role_code: str) -> tuple[str, ...]:
        """그 역할이 명부에서 볼 절. 명부를 안 쓰면 빈 튜플."""
        relation = self.relations.get(role_code)
        return relation.registry_sections if relation else ()

    def can_mint(self, role_code: str) -> bool:
        """채번 대상 역할인가 — MINT 이거나 REGISTRY+mint_fallback."""
        relation = self.relations.get(role_code)
        if relation is None:
            return False
        return relation.mints or role_code in self.mint_fallback

    @property
    def entity_roles(self) -> frozenset[str]:
        return frozenset(code for code, rel in self.relations.items() if rel.is_entity)

    @property
    def non_entity_roles(self) -> Mapping[str, str]:
        """비실체 역할 → 값 범주(TIME·VALUE·TEXT)."""
        return {code: rel.value_class for code, rel in self.relations.items()
                if rel.value_class is not None}
