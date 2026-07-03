"""레이크 스토리지 — 백엔드 추상화 + 경로 규약."""

from .storage import (
    LocalStorage,
    S3Storage,
    Storage,
    collection_log_key,
    make_storage,
    raw_news_partition,
)

__all__ = [
    "Storage",
    "LocalStorage",
    "S3Storage",
    "make_storage",
    "raw_news_partition",
    "collection_log_key",
]
