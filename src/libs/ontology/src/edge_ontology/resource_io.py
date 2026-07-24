from __future__ import annotations

from importlib import resources

from .constants import RESOURCE_PACKAGE


def read_text_resource(*parts: str) -> str:
    target = resources.files(RESOURCE_PACKAGE)
    for part in parts:
        target = target.joinpath(part)
    return target.read_text(encoding="utf-8")
