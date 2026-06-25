from __future__ import annotations

from .dataset import (
    LEAKAGE_COLUMNS,
    TARGET_COLUMNS,
    SplitConfig,
    assert_no_leakage,
    build_dataset,
    chronological_split,
    spread_feature_columns,
)
from .factor_arm import compute_factor_arm
from .loaders import load_ff5, load_news, load_ohlc
from .news_arm import NewsWindows, TitleEmbedder, build_day_embeddings, build_news_windows, dedupe_news, structure_titles
from .returns import ZScore, add_log_returns, add_spread

__all__ = [
    "LEAKAGE_COLUMNS",
    "TARGET_COLUMNS",
    "SplitConfig",
    "TitleEmbedder",
    "ZScore",
    "add_log_returns",
    "add_spread",
    "assert_no_leakage",
    "build_dataset",
    "build_day_embeddings",
    "build_news_windows",
    "NewsWindows",
    "chronological_split",
    "compute_factor_arm",
    "dedupe_news",
    "load_ff5",
    "load_news",
    "load_ohlc",
    "spread_feature_columns",
    "structure_titles",
]
