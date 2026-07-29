"""2. 속성(Attribute) — 전 사건 타입이 상속하는 공용 실체상태 풀.

시총·매출·레버리지처럼 어느 사건에서든 분모가 되는 값들이다. 타입 고유 속성은 사건
타입 정의(4. 사건층 resources/process/types/*.yaml)가 갖는다 — 이 풀은 그 위에 얹힌다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .._resource import load_yaml_resource
from ..constants import ATTRIBUTE_DIR
from .model import Attribute, parse_section

COMMON_ATTRIBUTES_RESOURCE = "common_features_v0_1.yaml"


@lru_cache(maxsize=1)
def load_common_attributes(path: Path | str | None = None) -> Mapping[str, Attribute]:
    """공용 풀 전량 — 속성 id → Attribute. 사건 타입의 derived 가 참조할 수 있는 이름들."""
    doc = load_yaml_resource(ATTRIBUTE_DIR, COMMON_ATTRIBUTES_RESOURCE, override=path)
    pool: dict[str, Attribute] = {}
    for section, body in (doc.get("common_blocks") or {}).items():
        for attribute_id, attribute in parse_section(section, body).items():
            if attribute_id in pool:
                raise ValueError(f"공용 속성 id 중복: {attribute_id}")
            pool[attribute_id] = attribute
    if not pool:
        raise ValueError("공용 속성 풀이 비었다")
    return MappingProxyType(pool)
