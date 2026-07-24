"""데이터 품질 게이트 — 정규화 산출을 canonical 로 넘기기 전 정합성 검사."""

from .disclosure import (
    BLOCKING_REASONS_DISCLOSURE,
    BLOCKING_REASONS_SEGMENT,
    validate_segment_fact,
    validate_supply_fact,
)
from .etf import BLOCKING_REASONS_ETF, validate_etf_holding
from .etf_nav import BLOCKING_REASONS_ETF_NAV, validate_etf_nav
from .etf_profile import BLOCKING_REASONS_ETF_PROFILE, validate_etf_profile
from .news import BLOCKING_REASONS, validate_news_meta
from .price import validate_ohlcv

__all__ = [
    "validate_ohlcv",
    "validate_news_meta",
    "BLOCKING_REASONS",
    "validate_supply_fact",
    "BLOCKING_REASONS_DISCLOSURE",
    "validate_segment_fact",
    "BLOCKING_REASONS_SEGMENT",
    "validate_etf_holding",
    "BLOCKING_REASONS_ETF",
    "validate_etf_nav",
    "BLOCKING_REASONS_ETF_NAV",
    "validate_etf_profile",
    "BLOCKING_REASONS_ETF_PROFILE",
]
