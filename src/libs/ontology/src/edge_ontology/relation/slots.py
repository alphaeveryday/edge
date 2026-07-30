"""3. 관계(Relation) — 논항 자리(argument slot) 적재.

`event_argument.slot` 의 SSOT. (타입, 역할) → subject·object·qualifier.

역할 전역이 아니라 (타입, 역할)로 선언한다 — 같은 역할이 타입에 따라 자리를 바꾼다
(`ISSUER` 는 배당결정에서 subject, 제품인증에서 object, 임원매매에서 qualifier). 술어는
자리를 바꾸지 않으므로 술어별 선언은 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .._resource import load_yaml_resource
from ..constants import RELATION_DIR

ARGUMENT_SLOTS_RESOURCE = "argument_slots_v0_1.yaml"

# `ck_event_argument_slot` CHECK 어휘와 동형이어야 한다 — 어긋나면 적재가 제약 위반으로 죽는다.
SLOT_VALUES = ("subject", "object", "qualifier")

# 충돌 사유 어휘. 둘의 해소 경로가 다르다.
VOCABULARY_DEFECT = "vocabulary_defect"   # 어휘 정리로만 닫힌다(어휘 개정 = 재태깅)
SLOT_ARITY = "slot_arity"                 # 3값의 한계. CHECK 를 넓히면 닫힌다(재태깅 없음)
COLLISION_REASONS = frozenset({VOCABULARY_DEFECT, SLOT_ARITY})


@dataclass(frozen=True)
class KnownCollision:
    """한 타입 안에서 (종, slot) 이 겹치는 자리 — 사유와 함께 등재된 것만 통과한다."""
    type_id: str
    roles: tuple[str, ...]
    reason: str
    why: str


def _document(path: Path | str | None) -> Mapping[str, object]:
    return load_yaml_resource(RELATION_DIR, ARGUMENT_SLOTS_RESOURCE, override=path)


@lru_cache(maxsize=1)
def load_argument_slots(path: Path | str | None = None) -> Mapping[str, Mapping[str, str]]:
    """타입 id → (역할 → slot). 어휘 밖 값이 있으면 즉시 죽는다(Rule 12)."""
    doc = _document(path)
    table = doc.get("slots") or {}
    if not isinstance(table, dict) or not table:
        raise ValueError("argument_slots.slots 가 비었다")
    out: dict[str, Mapping[str, str]] = {}
    errors: list[str] = []
    for type_id, body in table.items():
        row: dict[str, str] = {}
        for role, slot in (body or {}).items():
            if slot not in SLOT_VALUES:
                errors.append(f"{type_id}.{role}: slot 어휘 밖 값 {slot!r}")
                continue
            row[role] = slot
        out[type_id] = MappingProxyType(row)
    declared = (doc.get("meta") or {}).get("pair_count")
    total = sum(len(v) for v in out.values())
    if declared is not None and declared != total:
        errors.append(f"meta.pair_count={declared!r} 가 실제 {total} 와 다르다")
    if errors:
        raise ValueError("논항 자리 적재 실패:\n" + "\n".join(errors))
    return MappingProxyType(out)


@lru_cache(maxsize=1)
def load_known_collisions(path: Path | str | None = None) -> tuple[KnownCollision, ...]:
    """등재된 (종, slot) 충돌. 여기 없는 충돌은 레지스트리 적재를 죽인다."""
    doc = _document(path)
    out: list[KnownCollision] = []
    for raw in doc.get("known_collisions") or ():
        if not isinstance(raw, dict):
            raise ValueError("known_collisions 항목이 매핑이 아니다")
        reason = str(raw.get("reason") or "")
        if reason not in COLLISION_REASONS:
            raise ValueError(f"known_collisions 사유 어휘 밖: {reason!r}")
        if not str(raw.get("why") or "").strip():
            raise ValueError(f"known_collisions {raw.get('type_id')}: why 필수 — "
                             f"사유 없는 면제는 게이트를 무력화한다")
        out.append(KnownCollision(type_id=str(raw["type"]),
                                  roles=tuple(raw.get("roles") or ()),
                                  reason=reason, why=str(raw["why"])))
    return tuple(out)
