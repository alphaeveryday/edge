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
LAKE_PRICE_MINUTE_PREFIX = "canonical/market_data/price_minute"


def minute_artifact_key(
    market: str, session_date: str, window_hhmm: str, generation: int
) -> str:
    """분봉 window artifact 키 — `data_pipeline.lake.storage.canonical_price_minute_artifact_key`
    의 전사(미러)다. 경로 규약 SSOT 는 저쪽이고, 엔진은 이미지 분리 때문에 data-pipeline
    에 의존하지 않아 문자열이 두 벌이다 — e2e 골든패스가 저 빌더로 **쓴** artifact 를
    이 키로 **읽어** 두 전사의 수렴을 고정한다(PIPELINE_ID 수렴과 같은 축).
    """
    return (
        f"{LAKE_PRICE_MINUTE_PREFIX}/market={market}/session_date={session_date}"
        f"/window={window_hhmm}/generation={generation}/bars.ndjson"
    )


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

    def _read_minute_bars(
        self, market: str, session_date: str, window_hhmm: str, generation: int
    ) -> dict[str, dict[str, Any]]:
        """분봉 window artifact(NDJSON) 를 unit_id → 레코드 dict 로 읽는다.

        artifact 부재(NoSuchKey)는 빈 dict — window 커밋 직후의 짧은 지연이 정상
        경로라 호출부가 ReturnsNotReady 로 접어 재시도한다. 형상 밖 행(비객체·
        unit_id 결측)은 건너뛴다 — canonical 진입 차단의 정본 게이트는 워커 검증
        경계(ALPHA-679)고, 여기서 한 행이 분해 전체를 죽이면 안 된다.
        """
        import json

        from botocore.exceptions import ClientError

        key = minute_artifact_key(market, session_date, window_hhmm, generation)
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return {}
            raise
        rows: dict[str, dict[str, Any]] = {}
        for line in body.decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict) and record.get("unit_id"):
                rows[str(record["unit_id"])] = record
        return rows

    def load_minute_returns(
        self,
        market: str,
        session_date: str,
        open_window_hhmm: str,
        open_generation: int,
        trigger_window_hhmm: str,
        trigger_generation: int,
    ) -> dict[str, float | None]:
        """unit 별 장중 수익률 — 세션 시가 window 의 open 대비 트리거 window 의 close.

        ETF 트리거 판정(ALPHA-708)과 같은 축(시가 대비)이다 — 분해가 다른 기준가를
        쓰면 트리거가 설명하는 움직임과 분해가 설명하는 움직임이 갈린다(ALPHA-710).
        가격 계약 위반(0·음수·비수치)은 그 unit 만 None 으로 접는다 — 분해는 None 을
        미가격으로 제외한다(daily `load_returns` 와 같은 계약).
        """
        opens = self._read_minute_bars(market, session_date, open_window_hhmm, open_generation)
        closes = (
            opens
            if (open_window_hhmm, open_generation)
            == (trigger_window_hhmm, trigger_generation)
            else self._read_minute_bars(
                market, session_date, trigger_window_hhmm, trigger_generation)
        )
        if not opens or not closes:
            return {}
        returns: dict[str, float | None] = {}
        for unit_id, record in closes.items():
            open_record = opens.get(unit_id)
            try:
                open_price = float(open_record["open"]) if open_record else None
                close_price = float(record["close"])
            except (KeyError, TypeError, ValueError):
                returns[unit_id] = None
                continue
            returns[unit_id] = (
                (close_price / open_price - 1.0)
                if open_price and open_price > 0 and close_price > 0
                else None
            )
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
