"""수집 설정 — 스키마(models) + 로더(loader)."""

from .loader import ConfigError, Settings, load_settings
from .models import (
    CollectionTargets,
    NewsConfig,
    NewsSource,
    PriceConfig,
    PriceSource,
    StorageConfig,
)

__all__ = [
    "ConfigError",
    "Settings",
    "load_settings",
    "NewsSource",
    "NewsConfig",
    "PriceSource",
    "PriceConfig",
    "CollectionTargets",
    "StorageConfig",
]
