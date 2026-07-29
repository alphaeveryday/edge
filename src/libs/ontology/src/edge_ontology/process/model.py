"""4. 사건(Process) — 사건 타입 하나와 그 전량의 모형.

사건 타입은 아래 세 층의 **조합**이다:
  - 3. 관계: required/optional/identity/primary 역할 — 어떤 실체들이 어떻게 붙잡히는가
  - 2. 속성: quantities/entity_state/derived — 그 사건이 실어 오는 값
  - 1. 실체: 역할이 가리키는 종별(관계 어휘가 결속을 갖는다)
그 위에 사건 고유의 것이 얹힌다 — 술어(predicates)와 라이프사이클 단계.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..attribute.model import Attribute


@dataclass(frozen=True)
class ProcessType:
    type_id: str
    family: str | None
    note: str

    # 사건 고유
    predicates: tuple[str, ...]          # 순서 있음 — [0] 이 기본 술어 계약
    lifecycle_model: str | None
    stages: tuple[str, ...]              # 순서축(stages + terminal). 빈 튜플 = 단발 사건
    stage_sensitive: bool

    # 3. 관계
    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...]
    identity_roles: tuple[str, ...]
    primary_roles: tuple[str, ...]       # 게이트가 고른 티커가 맡을 수 있는 역할
    slots: Mapping[str, str]             # 역할 → 논항 자리(subject·object·qualifier)

    # 2. 속성
    quantities: Mapping[str, Attribute]
    entity_state: Mapping[str, Attribute]
    derived: Mapping[str, Attribute]

    # 스레딩 계약(사건의 동일성 판정) — resources/process/news_thread_contract
    identity_required: tuple[str, ...]
    identity_optional: tuple[str, ...]
    missing_identity_policy: str

    def slot_of(self, role_code: str) -> str | None:
        """그 역할이 이 사건에서 차지하는 논항 자리. 참여자 역할이 아니면 None.

        `event_argument.slot` 이 이 값이다. (타입, 역할)만으로 결정되므로 기사를 볼 필요가
        없다 — 추출 LLM 에게 물어보면 3값 중 무엇을 내도 범위검사를 통과해 오류가 조용하다.
        """
        return self.slots.get(role_code)

    @property
    def role_menu(self) -> frozenset[str]:
        """그 타입이 쓸 수 있는 역할 전량(참여자 + 수량)."""
        return frozenset(self.required_roles) | frozenset(self.optional_roles) | self.quantity_roles

    @property
    def quantity_roles(self) -> frozenset[str]:
        return frozenset(self.quantities)

    @property
    def required_quantity_roles(self) -> frozenset[str]:
        """없으면 completeness=partial 인 수량 역할."""
        return frozenset(k for k, a in self.quantities.items() if a.required)

    @property
    def currency_roles(self) -> frozenset[str]:
        return frozenset(k for k, a in self.quantities.items() if a.unit_family == "CURRENCY")

    @property
    def quantity_unit_families(self) -> Mapping[str, str]:
        """수량 역할 → 단위 계열. 파서 단위와 어긋나면 값을 버리는 판정 축."""
        return {k: a.unit_family for k, a in self.quantities.items() if a.unit_family}


@dataclass(frozen=True)
class ProcessRegistry:
    types: Mapping[str, ProcessType]
    novelty_statuses: frozenset[str]
    version: str

    def __contains__(self, type_id: object) -> bool:
        return type_id in self.types

    def __getitem__(self, type_id: str) -> ProcessType:
        return self.types[type_id]

    def get(self, type_id: str) -> ProcessType | None:
        return self.types.get(type_id)
