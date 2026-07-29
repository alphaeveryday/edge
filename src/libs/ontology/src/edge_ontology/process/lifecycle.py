"""4. 사건(Process) — 라이프사이클 모델(단계 순서축)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from .._resource import load_yaml_resource
from ..constants import PROCESS_DIR

LIFECYCLE_RESOURCE = "lifecycle_models_v0_1.yaml"


@lru_cache(maxsize=1)
def load_lifecycle_models(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """모델명 → {stages: 순서 리스트, terminal: 종결 상태}. stages=[] 는 단발 사건."""
    models = load_yaml_resource(PROCESS_DIR, LIFECYCLE_RESOURCE, override=path).get("models") or {}
    if not models:
        raise ValueError("라이프사이클 모델이 하나도 없다")
    return models


def stage_sequence(model: str | None, models: dict[str, dict[str, Any]] | None = None) -> tuple[str, ...]:
    """모델의 순서축(stages + terminal) — 프롬프트 메뉴·검증·novelty 가 공유하는 어휘."""
    spec = (models if models is not None else load_lifecycle_models()).get(model or "") or {}
    return tuple(spec.get("stages") or ()) + tuple(spec.get("terminal") or ())
