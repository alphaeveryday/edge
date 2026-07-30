"""리소스 읽기 — 층 디렉터리 하나를 지나 YAML 매핑을 돌려주는 공용 통로."""
from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .constants import RESOURCE_PACKAGE


def read_text_resource(*parts: str) -> str:
    target = resources.files(RESOURCE_PACKAGE)
    for part in parts:
        target = target.joinpath(part)
    return target.read_text(encoding="utf-8")


def yaml_mapping(text: str, what: str) -> dict[str, Any]:
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{what} 리소스는 매핑이어야 한다")
    return payload


def load_yaml_resource(*parts: str, override: Path | str | None = None) -> dict[str, Any]:
    """패키지 리소스(또는 override 경로)를 읽어 매핑으로.

    ``override`` 는 실험실 리소스를 승격 **전에** 검증하는 통로다 — 테스트와 승격 리허설이
    같은 로더를 쓰게 한다.
    """
    text = (Path(override).read_text(encoding="utf-8") if override is not None
            else read_text_resource(*parts))
    return yaml_mapping(text, parts[-1] if parts else str(override))
