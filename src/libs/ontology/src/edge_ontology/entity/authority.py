"""1. 실체(Entity) — 실체 **인스턴스** 명부(닫힌 집합).

규제기관·법원·중앙은행·시장기관은 닫힌 집합이라 명부가 정답을 갖는다. 조회는 **절(section)
단위로 좁혀서** 한다 — 좁히지 않으면 별칭 평면 하나를 공유해 법원 자리에 규제기관이
해소된다. 어느 역할이 어느 절을 보는지는 이 층이 모른다(관계층 `identity` 선언 소관).

열린 집합(제품·개념·지역·규칙)은 명부로 닫을 수 없어 정규화 문자열을 채번한다 — 그쪽도
관계층이 판단한다.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from .._resource import load_yaml_resource
from ..constants import ENTITY_DIR

AUTHORITY_REGISTRY_RESOURCE = "authority_registry_v0_1.yaml"
REGISTRY_SECTIONS = ("authorities", "courts", "central_banks", "institutions",
                     "foreign_authorities")


@dataclass(frozen=True)
class AuthorityEntry:
    entity_id: str
    display_name: str
    actor_type: str
    country_code: str
    section: str


def normalize_name(text: str) -> str:
    """기관명 정규화 — NFKC · 공백/가운뎃점 제거 · casefold.

    명부 적재와 조회가 **같은 함수**를 써야 한다. 갈리면 '공정 거래위원회'가
    영영 안 잡힌다. 계약 `meta.matching.rule` 이 완전일치라고 못박은 이유이기도 하다.
    """
    folded = unicodedata.normalize("NFKC", text)
    return "".join(folded.split()).replace("·", "").casefold()


@dataclass(frozen=True)
class AuthorityRegistry:
    entries: Mapping[str, AuthorityEntry]                  # entity_id → 항목
    by_section: Mapping[str, Mapping[str, str]]            # 절 → (정규화 별칭 → entity_id)

    def resolve(self, mention: str, sections: Iterable[str]) -> str | None:
        """멘션을 기관 entity_id 로 — **주어진 절 안에서만** 찾는다.

        절을 좁히는 것이 이 함수의 요점이다. 전 절을 뒤지면 COURT 자리에 규제기관이
        해소된다. 어느 역할이 어느 절을 보는지는 관계층 `identity` 선언이 정한다.

        contains 매칭은 하지 않는다 — '금융위원회 산하 …' 같은 문장에서 엉뚱한 기관을
        물어온다(계약 meta.matching). 못 찾으면 정직하게 미해소로 남긴다.
        """
        key = normalize_name(mention)
        for section in sections:
            hit = self.by_section.get(section, {}).get(key)
            if hit is not None:
                return hit
        return None


@lru_cache(maxsize=1)
def load_authority_registry(path: Path | str | None = None) -> AuthorityRegistry:
    """명부 적재 + 정합 검사(미지 절·중복 id·별칭 충돌·모호어 유입)."""
    doc = load_yaml_resource(ENTITY_DIR, AUTHORITY_REGISTRY_RESOURCE, override=path)
    banned = {normalize_name(x) for x in
              (doc.get("meta", {}).get("matching", {}).get("ambiguous_rejected") or ())}
    entries: dict[str, AuthorityEntry] = {}
    by_section: dict[str, dict[str, str]] = {}
    by_alias: dict[str, str] = {}
    for section in REGISTRY_SECTIONS:
        aliases: dict[str, str] = {}
        for raw in doc.get(section) or ():
            entry = AuthorityEntry(entity_id=raw["entity_id"],
                                   display_name=raw["display_name"],
                                   actor_type=raw["actor_type"],
                                   country_code=raw["country_code"],
                                   section=section)
            if entry.entity_id in entries:
                raise ValueError(f"기관 id 중복: {entry.entity_id}")
            entries[entry.entity_id] = entry
            for alias in raw.get("aliases") or ():
                key = normalize_name(alias)
                if key in banned:
                    raise ValueError(f"모호어를 별칭으로 썼다: {alias} ({entry.entity_id})")
                # 절 간 별칭 충돌도 막는다 — 절을 좁혀 조회하더라도 같은 이름이 두 기관을
                # 가리키면 어느 절에서 걸리느냐에 따라 답이 달라진다.
                if key in by_alias and by_alias[key] != entry.entity_id:
                    raise ValueError(f"별칭 충돌 {alias}: {by_alias[key]} vs {entry.entity_id}")
                by_alias[key] = entry.entity_id
                aliases[key] = entry.entity_id
        by_section[section] = aliases
    unknown = set(doc) - {"meta", *REGISTRY_SECTIONS}
    if unknown:
        raise ValueError(f"명부에 알 수 없는 절이 있다 — 코드가 안 읽는다: {sorted(unknown)}")
    return AuthorityRegistry(
        entries=MappingProxyType(entries),
        by_section=MappingProxyType({k: MappingProxyType(v) for k, v in by_section.items()}),
    )
