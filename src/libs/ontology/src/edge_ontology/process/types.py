"""4. 사건(Process) — 사건 **타입** 정의의 원본 읽기.

`resources/process/types/*.yaml` 는 타입당 하나의 병합 정의를 갖는다(family, predicates,
lifecycle_model, stage_sensitive, roles{required,optional,identity,primary}, note,
quantities, entity_state, derived). 이게 사건층 SSOT 다.

주의 — 이 lib 이 담는 사건은 **선험적 타입 스키마**뿐이다. 실제 사건 인스턴스, 절차적
지식, 복잡계 현상의 전개는 다른 모듈(data-pipeline·analysis-engine) 소관이다.
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from .._resource import yaml_mapping
from ..constants import PROCESS_DIR, RESOURCE_PACKAGE

TYPES_DIR = "types"


def _iter_type_files(types_dir: Path | str | None) -> list[tuple[str, str]]:
    if types_dir is None:
        root = resources.files(RESOURCE_PACKAGE).joinpath(PROCESS_DIR).joinpath(TYPES_DIR)
        entries = [(entry.name, entry.read_text(encoding="utf-8"))
                   for entry in root.iterdir() if entry.name.endswith(".yaml")]
    else:
        root = Path(types_dir)
        entries = [(path.name, path.read_text(encoding="utf-8")) for path in root.glob("*.yaml")]
    return sorted(entries)


def load_type_definitions(types_dir: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """타입 id → 원본 정의 매핑. 구조화된 뷰는 `load_process_registry()` 가 준다."""
    merged: dict[str, dict[str, Any]] = {}
    for name, text in _iter_type_files(types_dir):
        for type_id, spec in (yaml_mapping(text, name).get("types") or {}).items():
            if type_id in merged:
                raise ValueError(f"사건 타입이 파일 간 중복: {type_id} ({name})")
            merged[type_id] = spec
    if not merged:
        raise ValueError("사건 타입 정의가 하나도 없다")
    return merged
