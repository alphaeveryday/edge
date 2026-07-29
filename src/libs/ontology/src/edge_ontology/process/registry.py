"""4. 사건(Process) 레지스트리 — 네 층을 하나의 타입 뷰로 접고, 층간 정합을 강제한다.

여기가 유일한 교차검증 지점이다. 사건 타입은 아래 세 층을 참조하므로, 참조가 깨졌는지
아는 것도 사건층의 일이다:
  - 쓰는 역할이 3. 관계 어휘 안인가 (밖이면 entity_kind=NULL 로 조용히 실린다)
  - derived 가 참조하는 속성이 선언돼 있는가 (2. 속성층 공용 풀 + 타입 고유)
  - lifecycle_model 이 실재하는가 (없으면 stage 메뉴가 통째로 빈다)
리소스 통째 교체 실수를 반입 시점에 시끄럽게 잡는 게 이 로더의 존재 이유다(Rule 12).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .._resource import load_yaml_resource
from ..attribute.common import load_common_attributes
from ..attribute.model import SECTIONS, parse_section
from ..constants import DEFAULT_VERSION, PROCESS_DIR
from ..relation import load_relations
from ..relation.slots import SLOT_VALUES, load_argument_slots, load_known_collisions
from .lifecycle import load_lifecycle_models, stage_sequence
from .model import ProcessRegistry, ProcessType
from .types import load_type_definitions

THREAD_CONTRACT_RESOURCE = "news_thread_contract_v0_1.yaml"

# 사후정보 냄새가 나는 속성 id — point-in-time 규칙 위반. 실현수익률·초과수익 같은 걸
# 속성으로 두면 사건 시점에 알 수 없는 값이 특징에 섞여 미래를 훔친다.
PIT_FORBIDDEN = re.compile(r"(^|_)(realized|post_event|car|next_day)(_|$)|^ar_|_ar$")

_ERROR_SAMPLE = 20


def _build(type_id: str, spec: Mapping[str, Any], lifecycle: Mapping[str, Any],
           thread_rule: Mapping[str, Any], slots: Mapping[str, str]) -> ProcessType:
    roles = spec.get("roles") or {}
    model = spec.get("lifecycle_model")
    identity = thread_rule.get("identity") or {}
    return ProcessType(
        type_id=type_id,
        family=spec.get("family"),
        note=str(spec.get("note") or ""),
        predicates=tuple(spec.get("predicates") or ()),
        lifecycle_model=model,
        stages=stage_sequence(model, dict(lifecycle)),
        stage_sensitive=bool(spec.get("stage_sensitive")),
        required_roles=tuple(roles.get("required") or ()),
        optional_roles=tuple(roles.get("optional") or ()),
        identity_roles=tuple(roles.get("identity") or ()),
        primary_roles=tuple(roles.get("primary") or ()),
        slots=MappingProxyType(dict(slots)),
        **{section: parse_section(section, spec.get(section)) for section in SECTIONS},
        identity_required=tuple(identity.get("required") or ()),
        identity_optional=tuple(identity.get("optional_discriminators") or ()),
        missing_identity_policy=str(thread_rule.get("missing_identity_policy")
                                    or "EMIT_UNKNOWN_LINK_ONLY"),
    )


def _validate(types: Mapping[str, ProcessType], lifecycle: Mapping[str, Any]) -> None:
    vocabulary = load_relations()
    common = set(load_common_attributes())
    errors: list[str] = []
    # 등재된 충돌 면제 — 사유 없는 면제는 slots 로더가 이미 거부한다.
    exempt: dict[str, set[str]] = {}
    for collision in load_known_collisions():
        exempt.setdefault(collision.type_id, set()).update(collision.roles)

    for type_id, pt in types.items():
        # 수량 역할(quantities)은 관계가 아니라 2. 속성층이다 — 어휘 검사 대상이 아니다.
        participant_roles = set(pt.required_roles) | set(pt.optional_roles)
        off_vocabulary = sorted(participant_roles - set(vocabulary.relations))
        if off_vocabulary:
            errors.append(f"{type_id}: 관계 어휘 밖 역할 {off_vocabulary}")

        if pt.lifecycle_model and pt.lifecycle_model not in lifecycle:
            errors.append(f"{type_id}: 없는 lifecycle_model '{pt.lifecycle_model}'")

        if not pt.predicates:
            errors.append(f"{type_id}: predicates 가 비었다 — 기본 술어를 못 고른다")

        if not pt.primary_roles:
            errors.append(f"{type_id}: primary_roles 가 비었다 — anchor 역할을 못 고른다")

        for role in pt.primary_roles:
            if role not in participant_roles:
                errors.append(f"{type_id}: primary_role '{role}' 이 그 타입 역할 메뉴 밖")

        # 논항 자리 — **실체 역할**만 대상이다. 비실체 역할(TIME·VALUE·TEXT)은
        # event_argument 에 실리지 않으므로(entity_id NOT NULL) slot 이 없다.
        entity_roles = {r for r in participant_roles if vocabulary.kind_of(r)}
        missing_slots = sorted(entity_roles - set(pt.slots))
        if missing_slots:
            errors.append(f"{type_id}: 논항 자리 미선언 역할 {missing_slots}")
        stray_slots = sorted(set(pt.slots) - entity_roles)
        if stray_slots:
            errors.append(f"{type_id}: 실체 역할이 아닌데 자리를 선언했다 {stray_slots}")

        # 게이트: 한 타입 안에서 (종, slot) 이 같은 역할 쌍은 구분 불가다. 등재된 것만 통과.
        by_cell: dict[tuple[str, str], list[str]] = {}
        for role in sorted(entity_roles):
            by_cell.setdefault((vocabulary.kind_of(role) or "", pt.slots.get(role, "")),
                               []).append(role)
        for cell, clash in by_cell.items():
            if len(clash) < 2 or set(clash) <= exempt.get(type_id, set()):
                continue
            errors.append(f"{type_id}: (종={cell[0]}, slot={cell[1]}) 에 역할 둘 이상 "
                          f"{clash} — 구분 불가. 고치거나 "
                          f"argument_slots.known_collisions 에 사유와 함께 등재할 것")

        declared: set[str] = set()
        for section in SECTIONS:
            for attribute_id, attribute in getattr(pt, section).items():
                if attribute_id in declared:
                    errors.append(f"{type_id}: 속성 id 중복 {attribute_id}")
                declared.add(attribute_id)
                if not attribute.desc:
                    errors.append(f"{type_id}.{attribute_id}: desc 필수")
                elif PIT_FORBIDDEN.search(attribute_id.lower()):
                    errors.append(f"{type_id}.{attribute_id}: 사후정보 냄새 — PIT 규칙 위반")

        for attribute_id, attribute in pt.derived.items():
            if not attribute.formula:
                errors.append(f"{type_id}.{attribute_id}: formula 필수")
            if not attribute.inputs:
                errors.append(f"{type_id}.{attribute_id}: inputs 필수")
            for ref in attribute.inputs:
                if ref not in declared and ref not in common:
                    errors.append(f"{type_id}.{attribute_id}: 미선언 input '{ref}'")

    if errors:
        head = "\n".join(errors[:_ERROR_SAMPLE])
        more = "" if len(errors) <= _ERROR_SAMPLE else f"\n... 외 {len(errors) - _ERROR_SAMPLE}건"
        raise ValueError(f"사건 레지스트리 정합 실패:\n{head}{more}")


@lru_cache(maxsize=1)
def load_process_registry(types_dir: Path | str | None = None,
                          version: str = DEFAULT_VERSION,
                          slots_path: Path | str | None = None) -> ProcessRegistry:
    """사건 타입 전량 + novelty 어휘. 층간 참조가 깨졌으면 여기서 죽는다.

    ``types_dir``·``slots_path`` 는 실험실 리소스를 승격 **전에** 같은 검사로 굴리는
    통로다. 타입을 갈아끼우면 논항 자리 표도 함께 줘야 한다 — 게이트가 그걸 요구한다.
    """
    definitions = load_type_definitions(types_dir)
    lifecycle = load_lifecycle_models()
    contract = load_yaml_resource(PROCESS_DIR, THREAD_CONTRACT_RESOURCE)
    contract_types = contract.get("types") or {}
    argument_slots = load_argument_slots(slots_path)

    types: dict[str, ProcessType] = {}
    novelty: set[str] = set()
    for type_id, spec in definitions.items():
        rule = contract_types.get(type_id) or {}
        novelty.update(rule.get("novelty_statuses") or ())
        types[type_id] = _build(type_id, spec or {}, lifecycle, rule,
                                argument_slots.get(type_id) or {})

    _validate(types, lifecycle)
    return ProcessRegistry(types=MappingProxyType(types),
                           novelty_statuses=frozenset(novelty),
                           version=version)
