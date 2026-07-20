"""가격·ETF holdings 를 읽는 S3 canonical-lake 리더.

``boto3``·``pyarrow`` 는 지연 import 한다(무거운 의존의 레포 관례) — 리더는 가격/holdings
경로에서만 쓰이고, 잔잔한 조기 종료 경로에선 건드리지 않는다.
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
    """S3 클라이언트 생성(선택적 AWS 프로파일 반영, 지연 boto3)."""
    import boto3

    if settings.aws_profile:
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.region)
    else:
        session = boto3.Session(region_name=settings.region)
    return session.client("s3")


class LakeReader:
    """S3 레이크에서 종가 대비 등락과 ETF holdings 를 읽는다."""

    def __init__(self, s3, bucket: str) -> None:
        """주어진 S3 클라이언트와 버킷으로 리더를 만든다."""
        self._s3 = s3
        self._bucket = bucket

    def _partition_values(self, base: str, key: str) -> list[str]:
        """``base`` 바로 아래 ``key=`` 파티션 값들(정렬)."""
        resp = self._s3.list_objects_v2(Bucket=self._bucket, Prefix=base, Delimiter="/")
        out: list[str] = []
        for common in resp.get("CommonPrefixes", []):
            seg = common.get("Prefix", "").rstrip("/").split("/")[-1]
            if seg.startswith(f"{key}="):
                out.append(seg[len(key) + 1:])
        return sorted(out)

    def _read_parquet_prefix(self, prefix: str, columns: list[str]) -> list[dict[str, Any]]:
        """``prefix`` 하위 parquet 을 전부 읽어 행 dict 리스트로 반환한다."""
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
        """티커별 종가 대비 등락. 직전 파티션을 D-1 로 쓴다."""
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
        """한 ETF 의 구성종목 비중(fraction).

        대상 ETF 행 존재 기준으로 고른다: trade_date 이하 최신 as_of, 없으면 가장 이른
        미래 스냅샷 — 파이프라인 트리거 writer(ALPHA-418)와 같은 규칙이라 발화한 트리거와
        그 설명이 같은 holdings 로 분해된다.
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
