"""S3 canonical-lake reader for prices and ETF holdings.

``boto3`` and ``pyarrow`` are imported lazily (repo convention for heavy deps):
the reader is only touched on the price/holdings path, not on a calm early exit.
"""
from __future__ import annotations

import io
from datetime import date
from typing import Any

from ..config import Settings
from ..domain.models import Holding

LAKE_PRICE_PREFIX = "canonical/market_data/price_daily"
LAKE_HOLDINGS_PREFIX = "canonical/holdings/etf_holdings"


def make_s3_client(settings: Settings):
    """Build an S3 client, honoring an optional AWS profile (lazy boto3)."""
    import boto3

    if settings.aws_profile:
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.region)
    else:
        session = boto3.Session(region_name=settings.region)
    return session.client("s3")


class LakeReader:
    """Reads close-to-close returns and ETF holdings from the S3 lake."""

    def __init__(self, s3, bucket: str) -> None:
        self._s3 = s3
        self._bucket = bucket

    def _partition_values(self, base: str, key: str) -> list[str]:
        """Sorted partition values for ``key=`` immediately under ``base``."""
        resp = self._s3.list_objects_v2(Bucket=self._bucket, Prefix=base, Delimiter="/")
        out: list[str] = []
        for common in resp.get("CommonPrefixes", []):
            seg = common.get("Prefix", "").rstrip("/").split("/")[-1]
            if seg.startswith(f"{key}="):
                out.append(seg[len(key) + 1:])
        return sorted(out)

    def _read_parquet_prefix(self, prefix: str, columns: list[str]) -> list[dict[str, Any]]:
        import pyarrow.parquet as pq

        rows: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                if not obj["Key"].endswith(".parquet"):
                    continue
                body = self._s3.get_object(Bucket=self._bucket, Key=obj["Key"])["Body"].read()
                rows.extend(pq.read_table(io.BytesIO(body), columns=columns).to_pylist())
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return rows

    def load_returns(self, market: str, trade_date: date) -> dict[str, float | None]:
        """Close-to-close return per ticker, using the preceding partition as D-1."""
        base = f"{LAKE_PRICE_PREFIX}/market={market}/"
        dates = self._partition_values(base, "trade_date")
        today = trade_date.isoformat()
        if today not in dates:
            return {}
        idx = dates.index(today)
        prev = dates[idx - 1] if idx > 0 else None
        cur = {
            str(r["ticker"]): r["close"]
            for r in self._read_parquet_prefix(f"{base}trade_date={today}/", ["ticker", "close"])
            if r.get("close") is not None
        }
        prv = (
            {
                str(r["ticker"]): r["close"]
                for r in self._read_parquet_prefix(f"{base}trade_date={prev}/", ["ticker", "close"])
                if r.get("close") is not None
            }
            if prev
            else {}
        )
        returns: dict[str, float | None] = {}
        for ticker, close in cur.items():
            prev_close = prv.get(ticker)
            returns[ticker] = (close / prev_close - 1.0) if prev_close and prev_close > 0 else None
        return returns

    def load_holdings(
        self, etf_id: str, market: str, trade_date: date
    ) -> tuple[list[Holding], str | None]:
        """Constituent weights (fraction) for one ETF.

        Selection is by target-ETF row presence: latest as_of <= trade_date, else
        the earliest future snapshot — the same rule as the pipeline's trigger
        writer (ALPHA-418), so a fired trigger and its explanation agree.
        """
        base = f"{LAKE_HOLDINGS_PREFIX}/market={market}/"
        dates = self._partition_values(base, "as_of_date")
        target = trade_date.isoformat()
        eligible = [x for x in dates if x <= target]
        future = [x for x in dates if x > target]
        for chosen in [*reversed(eligible), *future]:
            rows = self._read_parquet_prefix(
                f"{base}as_of_date={chosen}/",
                ["etf_id", "constituent_ticker", "constituent_name", "weight_pct"],
            )
            holdings = [
                Holding(
                    ticker=str(r["constituent_ticker"]),
                    name=r.get("constituent_name"),
                    weight=float(r["weight_pct"] or 0.0) / 100.0,
                )
                for r in rows
                if str(r.get("etf_id")) == etf_id and r.get("constituent_ticker")
            ]
            if holdings:
                return holdings, chosen
        return [], None
