"""뉴스 이벤트 태깅 — 기사 → assertion (ALPHA-138).

`ontology` 가 허용 라벨(alphamale 53타입 스냅샷)을, `extract` 가 추출·검증을 맡는다.
"""

from .extract import TAGGER_VERSION, build_prompt, extract_assertions
from .ontology import (
    DOC_CLASSES,
    allowed_predicates,
    allowed_roles,
    event_type_codes,
    load_profiles,
    ontology_version,
    required_roles,
)

__all__ = [
    "DOC_CLASSES",
    "TAGGER_VERSION",
    "allowed_predicates",
    "allowed_roles",
    "build_prompt",
    "event_type_codes",
    "extract_assertions",
    "load_profiles",
    "ontology_version",
    "required_roles",
]
