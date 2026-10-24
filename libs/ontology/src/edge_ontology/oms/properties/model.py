"""2. 속성(Attribute) — 실체·사건이 지니는 값 하나의 모형."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
from types import MappingProxyType

# 속성의 세 갈래. 어느 갈래냐가 그 값을 어디서 얻는지를 정한다.
QUANTITY = "QUANTITY"   # 사건이 실어 오는 수량 — 원문 표면형에서 파싱한다(event_measure)
STATE = "STATE"         # 사건 시점 실체의 상태 — 외부 재무/시장 자산에서 join 한다
DERIVED = "DERIVED"     # 위 둘의 함수 — formula 로 계산한다

SECTIONS: Mapping[str, str] = MappingProxyType({
    "quantities": QUANTITY,
    "entity_state": STATE,
    "derived": DERIVED,
})


@dataclass(frozen=True)
class Attribute:
    attribute_id: str
    kind: str                       # QUANTITY | STATE | DERIVED
    desc: str
    dtype: str | None = None
    unit_family: str | None = None  # CURRENCY·PERCENT·RATIO·DURATION_DAYS …
    scope: str | None = None        # STATE: 어느 역할의 실체에 붙는 상태인가
    basis: tuple[str, ...] = ()     # QUANTITY: TOTAL/ANNUAL — 총액/연간 혼동이 치명 오류
    required: bool = False          # QUANTITY: 없으면 completeness=partial
    formula: str | None = None      # DERIVED
    inputs: tuple[str, ...] = ()    # DERIVED: 참조하는 속성 id


def parse_attribute(attribute_id: str, kind: str, spec: Any) -> Attribute:
    if not isinstance(spec, Mapping):
        raise ValueError(f"속성 {attribute_id} 정의가 매핑이 아니다")
    return Attribute(
        attribute_id=attribute_id,
        kind=kind,
        desc=str(spec.get("desc") or ""),
        dtype=spec.get("dtype"),
        unit_family=spec.get("unit_family"),
        scope=spec.get("scope"),
        basis=tuple(spec.get("basis") or ()),
        required=bool(spec.get("required")),
        formula=spec.get("formula"),
        inputs=tuple(spec.get("inputs") or ()),
    )


def parse_section(section: str, body: Any) -> Mapping[str, Attribute]:
    """`quantities`/`entity_state`/`derived` 블록 하나 → 속성 표."""
    kind = SECTIONS[section]
    return MappingProxyType({
        attribute_id: parse_attribute(attribute_id, kind, spec)
        for attribute_id, spec in (body or {}).items()
    })
