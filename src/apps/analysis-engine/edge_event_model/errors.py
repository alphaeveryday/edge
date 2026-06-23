"""Standard error codes + fail-log entry builder (spec section 8).

Three tiers:
* ``PipelineAbort``  -- whole run stops (no data access at all).
* ``TickerSkip``     -- one ticker/date skipped, others continue.
* ``RowDrop``        -- one row excluded, processing continues.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


class EdgeModelError(Exception):
    code: str = "EDGE_MODEL_ERROR"


class PipelineAbort(EdgeModelError):
    code = "PIPELINE_ABORT"


class DbUnavailable(PipelineAbort):
    code = "DB_UNAVAILABLE"


class FactorDataNotFound(PipelineAbort):
    code = "FACTOR_DATA_NOT_FOUND"


class PriceOhlcNotFound(PipelineAbort):
    code = "PRICE_OHLC_NOT_FOUND"


class TickerSkip(EdgeModelError):
    code = "TICKER_SKIP"


class PriceDataNotFound(TickerSkip):
    code = "PRICE_DATA_NOT_FOUND"


class InsufficientPriceHistory(TickerSkip):
    code = "INSUFFICIENT_PRICE_HISTORY"


class InsufficientObservations(TickerSkip):
    code = "INSUFFICIENT_OBSERVATIONS"


class NoNewsForDate(TickerSkip):
    code = "NO_NEWS_FOR_DATE"


class RowDrop(EdgeModelError):
    code = "ROW_DROP"


class InvalidPriceRow(RowDrop):
    code = "INVALID_PRICE_ROW"


class MissingFactorRow(RowDrop):
    code = "MISSING_FACTOR_ROW"


class EmbeddingFailed(RowDrop):
    code = "EMBEDDING_FAILED"


@dataclass(slots=True)
class FailLogEntry:
    error_code: str
    error_message: str
    trade_date: date | None = None
    ticker: str | None = None
    news_id: str | None = None
    feature_id: str | None = None
    importance_vector: dict[str, Any] | None = None
    factor_vector: dict[str, Any] | None = None
    model_prediction_return: float | None = None
    real_return: float | None = None
    error_return: float | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date.isoformat() if isinstance(self.trade_date, date) else self.trade_date,
            "ticker": self.ticker,
            "news_id": self.news_id,
            "feature_id": self.feature_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "importance_vector": self.importance_vector,
            "factor_vector": self.factor_vector,
            "model_prediction_return": self.model_prediction_return,
            "real_return": self.real_return,
            "error_return": self.error_return,
            "context": self.context,
        }
