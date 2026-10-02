"""4. 사건(Process) — 시공간적 상호작용의 **선험적 타입 스키마**.

인스턴스·절차적 지식·복잡계 전개는 이 lib 밖(data-pipeline·analysis-engine) 소관이다.
"""
from .lifecycle import load_lifecycle_models, stage_sequence
from .model import ProcessRegistry, ProcessType
from .registry import load_process_registry
from .types import load_type_definitions

__all__ = [
    "ProcessRegistry",
    "ProcessType",
    "load_lifecycle_models",
    "load_process_registry",
    "load_type_definitions",
    "stage_sequence",
]
