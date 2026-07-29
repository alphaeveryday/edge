"""3. 관계(Relation) 어휘 적재 — role_bindings 리소스가 SSOT.

역할 어휘를 사건 타입에서 역파생하지 않는다. 관계는 사건보다 아래 층이라 사건 없이도
성립해야 하고(선험적), 그래야 4. 사건층이 "이 타입이 쓰는 역할이 어휘 안인가"를 검사할
수 있다 — 역파생하면 그 검사가 항진명제가 된다.

이 모듈은 두 축을 함께 읽는다: 역할→종별(`role_kinds`)과 역할→해소방식(`identity`).
둘은 독립이다. 예전에는 해소방식이 종별에 눌려 있어 코드가 종별을 도로 쪼개는 하드코딩
(CLOSED_SET_ROLES·MINTABLE_KINDS)을 들고 있었다 — 지금은 리소스 선언에서 파생한다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .._resource import load_yaml_resource
from ..constants import RELATION_DIR
from ..entity.authority import REGISTRY_SECTIONS, load_authority_registry, normalize_name
from ..entity.kinds import load_entity_kinds
from .model import MINT, NONE, REGISTRY, SCHEMES, Relation, RelationVocabulary

ROLE_BINDINGS_RESOURCE = "role_bindings_v0_1.yaml"

# 한 글자('A')나 숫자만인 멘션은 개념이 아니라 잡음이다 — 그런 걸 세우면 서로 무관한
# 사건이 한 스레드로 뭉친다.
MIN_CONCEPT_CHARS = 2


def _identity_spec(doc: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, str]]:
    identity = doc.get("identity") or {}
    per_role = identity.get("roles") or {}
    per_kind = identity.get("kind_default") or {}
    if not per_kind:
        raise ValueError("identity.kind_default 가 없다 — 역할의 해소 방식을 못 정한다")
    return per_role, per_kind


def _scheme_for(role: str, kind: str | None, per_role: Mapping[str, Any],
                per_kind: Mapping[str, str]) -> tuple[str, tuple[str, ...], bool]:
    """(scheme, sections, mint_fallback) — 역할 선언이 있으면 그것, 없으면 종별 기본값."""
    spec = per_role.get(role)
    if spec is None:
        scheme = per_kind.get(kind or "", NONE) if kind else NONE
        return str(scheme), (), False
    if not isinstance(spec, Mapping):
        raise ValueError(f"identity.roles.{role} 이 매핑이 아니다")
    scheme = str(spec.get("scheme") or NONE)
    sections = tuple(spec.get("sections") or ())
    if scheme == REGISTRY and not sections:
        raise ValueError(f"identity.roles.{role}: REGISTRY 인데 sections 가 없다 — "
                         f"절을 좁히지 않으면 엉뚱한 기관으로 해소된다")
    unknown = sorted(set(sections) - set(REGISTRY_SECTIONS))
    if unknown:
        raise ValueError(f"identity.roles.{role}: 명부에 없는 절 {unknown}")
    return scheme, sections, bool(spec.get("mint_fallback"))


@lru_cache(maxsize=1)
def load_relations(path: Path | str | None = None) -> RelationVocabulary:
    """관계 어휘 적재 + 정합 검사 — 어긋나면 즉시 죽는다(Rule 12).

    조용히 통과시키면 그 역할은 `entity_kind=NULL` 로 적재되고, 종별을 모르니 적재 경로도
    못 고른다.
    """
    doc = load_yaml_resource(RELATION_DIR, ROLE_BINDINGS_RESOURCE, override=path)
    table = doc.get("role_kinds") or {}
    entity_table = table.get("entity") or {}
    non_entity_table = table.get("non_entity") or {}
    per_role, per_kind = _identity_spec(doc)

    known_kinds = set(load_entity_kinds())
    unknown = set(entity_table) - known_kinds
    if unknown:
        raise ValueError(f"role_kinds.entity 가 실체 종별에 없는 종별을 쓴다: {sorted(unknown)}")
    unknown_default = set(per_kind) - known_kinds
    if unknown_default:
        raise ValueError(f"identity.kind_default 가 없는 종별을 쓴다: {sorted(unknown_default)}")
    missing_default = sorted(set(entity_table) - set(per_kind))
    if missing_default:
        raise ValueError(f"identity.kind_default 가 덮지 않는 종별: {missing_default}")
    bad_scheme = sorted(set(per_kind.values()) - SCHEMES)
    if bad_scheme:
        raise ValueError(f"identity.kind_default 에 알 수 없는 scheme: {bad_scheme}")

    relations: dict[str, Relation] = {}
    fallback: set[str] = set()
    for kind, roles in entity_table.items():
        for role in roles or ():
            if role in relations:
                raise ValueError(f"역할 {role} 이 종별 둘에 걸쳐 있다: "
                                 f"{relations[role].entity_kind} / {kind}")
            scheme, sections, mint_fallback = _scheme_for(role, kind, per_role, per_kind)
            if scheme not in SCHEMES:
                raise ValueError(f"역할 {role} 의 scheme 이 어휘 밖: {scheme}")
            if mint_fallback:
                fallback.add(role)
            relations[role] = Relation(role_code=role, entity_kind=kind, scheme=scheme,
                                       registry_sections=sections)

    for value_class, body in non_entity_table.items():
        for role in (body or {}).get("roles") or ():
            if role in relations:
                raise ValueError(f"역할 {role} 이 entity 와 non_entity 양쪽에 있다")
            relations[role] = Relation(role_code=role, value_class=value_class)

    off_vocabulary = sorted(set(per_role) - set(relations))
    if off_vocabulary:
        raise ValueError(f"identity.roles 가 어휘 밖 역할을 선언한다: {off_vocabulary}")
    if not relations:
        raise ValueError("관계 어휘가 비었다")
    return RelationVocabulary(relations=MappingProxyType(relations),
                              mint_fallback=frozenset(fallback))


def role_entity_kind(role_code: str) -> str | None:
    """역할이 가리키는 실체 종별. 비실체·어휘 밖이면 None."""
    return load_relations().kind_of(role_code)


def resolve_authority(role_code: str, mention: str) -> str | None:
    """명부로 해소하는 역할의 멘션 → 기관 entity_id.

    역할이 볼 절만 본다(`identity.roles.<역할>.sections`). 전 절을 뒤지면 COURT 자리에
    규제기관이 해소된다 — 그게 이 함수가 절을 인자로 넘기는 이유다.
    """
    sections = load_relations().sections_for(role_code)
    if not sections:
        return None
    return load_authority_registry().resolve(mention, sections)


def concept_key(role_code: str, mention: str) -> str | None:
    """역할이 가리키는 실체를 멘션에서 채번할 때 쓰는 키(정규화 문자열).

    채번 대상이 아니면 None. 키만 돌려준다 — **ID 채번은 하지 않는다**. 결정적 ID 는
    data-pipeline 의 `stable_domain_id` 가 유일한 산식이어야 하고(그 독스트링: 두 writer 가
    같은 함수를 공유해야 한다), 여기서 따로 해시하면 산식이 갈린다.

    정규화 텍스트가 곧 정체성이다 — 정규화가 거칠면 '갤럭시S25'와 '갤럭시 S25'가 갈리고,
    너무 뭉개면 무관한 제품이 합쳐진다. 현 규칙은 `normalize_name` 한 겹뿐이다.
    정본 개념 그래프는 계약 백로그 `product_revenue_concept_graph` 소관.

    명부만 쓰는 역할(AUTHORITY·COURT·CENTRAL_BANK)은 **채번하지 않는다**: 명부가 정답을
    갖고 있으므로, 못 찾았다면 그건 미등록이거나 '당국' 같은 모호어다. 거기서 채번하면
    같은 기관이 표기마다 다른 엔티티가 되어 조용한 오해소가 된다. 반대로 거래소처럼
    `mint_fallback` 이 선 역할은 미등재분을 채번한다.
    """
    if not load_relations().can_mint(role_code):
        return None
    key = normalize_name(mention)
    if len(key) < MIN_CONCEPT_CHARS or key.isdigit():
        return None
    return key
