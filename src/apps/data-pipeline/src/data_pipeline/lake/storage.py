"""레이크 스토리지 백엔드(local|s3) + 경로 빌더.

이 모듈이 s3://stock-ai-lake/ 파티션 규약의 SSOT 다 — 경로 문자열을
다른 곳에서 조립하지 말고 여기 빌더를 쓴다.

- raw:  run_id 별 append (재현성). 파티션은 published_date(수집일 아님).
- 로그: operations_archive/collection_logs/ 아래 run_id 별 1건.

백엔드는 설정(storage.backend)으로 고른다. MVP 개발은 local 스텁으로 돌리고,
배포는 s3 로 전환한다(같은 키 규약 — local 은 루트 디렉터리 아래 동일 상대경로).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..config import StorageConfig


# ── 경로 빌더 (파티션 규약 SSOT) ─────────────────────────
def raw_news_partition(
    source: str, market: str, published_date: str, run_id: str
) -> str:
    """raw 뉴스 파티션 프리픽스 (끝 슬래시 없음)."""
    return (
        f"raw/source={source}/dataset=stock_news/market={market}"
        f"/published_date={published_date}/run_id={run_id}"
    )


def raw_price_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 일봉(price_daily) 파티션 프리픽스 (끝 슬래시 없음).

    뉴스와 달리 파티션 키는 trade_date 가 아니라 ingest_date(수집일)다 — 가격 EOD
    응답은 한 심볼이 여러 trade_date 를 한 번에 주므로 원본을 수집일 기준으로
    보존한다(trade_date 별 분해는 후속 canonical/market_data 소관). SSOT: 사용자
    레이크 계층구조의 raw/source=fmp/dataset=price_daily/market=…/ingest_date=….
    """
    return (
        f"raw/source={source}/dataset=price_daily/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


def raw_financial_object_key(
    source: str,
    statement_type: str,
    market: str,
    ticker: str,
    period_type: str,
    fiscal_period_end: str,
    filing_date: str,
) -> str:
    """raw 재무제표 객체 키 (공시 하나 = 객체 하나).

    뉴스·가격 raw 와 달리 파티션 키가 ingest_date/run_id 가 아니라 **공시 정체성**이다
    (종목·문서·회계기간·공시일). 재무제표는 드물게·비동기로 공시되는데 매일 재폴링하므로,
    ingest_date/run_id 로 '전부 append'하면 같은 분기 payload 가 매일 쌓인다. 키 자체를
    공시 정체성으로 만들면 존재검사→신규만 put 이라 매일 폴링해도 저장은 분기당 1회다.

    - period_type(annual|quarter)을 키에 둔다 — 애플 Q4 분기와 FY 연간은 같은
      fiscal_period_end(회계연도말)를 공유하지만 서로 다른 명세라 구분돼야 한다.
    - filing_date 를 키에 둬 정정 공시(같은 기간, 다른 공시일)를 원본과 함께 새 키로
      보존한다(덮어쓰지 않음 = point-in-time 이력, 룩어헤드 방지). append-only 유지.
    """
    return (
        f"raw/source={source}/dataset=financial_statements"
        f"/statement_type={statement_type}/market={market}/ticker={ticker}"
        f"/period={period_type}/fiscal_period_end={fiscal_period_end}"
        f"/filing_date={filing_date}/data.json"
    )


def collection_log_key(source: str, dataset: str, started_date: str, run_id: str) -> str:
    """수집 실행 로그(런당 1건) 키.

    source 뿐 아니라 dataset 으로도 가른다 — 같은 벤더(source=fmp)의 뉴스(stock_news)·
    가격(price_daily) 수집이 같은 run_id 를 공유해도(오케스트레이션 백필 등) 로그가
    서로 덮어쓰지 않게. (뉴스만 있던 시절엔 dataset 없이 source 로만 갈랐다.)
    """
    return (
        f"operations_archive/collection_logs/source={source}/dataset={dataset}"
        f"/started_date={started_date}/run_id={run_id}/log.json"
    )


# ── 백엔드 ──────────────────────────────────────────────
class Storage(Protocol):
    """레이크 키-바이트 저장 계약. 키는 위 빌더가 만든 상대경로."""

    def put_bytes(self, key: str, data: bytes) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def list_keys(self, prefix: str) -> list[str]: ...


class LocalStorage:
    """로컬 파일 스텁 — 키를 루트 아래 동일 상대경로 파일로 저장한다."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        # S3 처럼 '문자열 prefix' 매칭 — prefix 를 디렉터리로 취급하면 백엔드 간
        # 동작이 갈린다(컴포넌트 중간을 자르는 prefix·전체 키 전달 등). 두 백엔드의
        # 키 규약을 일치시켜 로컬 통과·배포 S3 불일치를 막는다.
        if not self.root.exists():
            return []
        keys = (
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file()
        )
        return sorted(k for k in keys if k.startswith(prefix))


class S3Storage:
    """S3 백엔드. boto3 는 지연 import — 단위테스트는 boto3 없이 모듈을 import 한다."""

    def __init__(self, bucket: str):
        self.bucket = bucket
        self._client = None

    @property
    def client(self):  # pragma: no cover - 통합(실 S3)
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def put_bytes(self, key: str, data: bytes) -> None:  # pragma: no cover - 통합
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:  # pragma: no cover - 통합
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def list_keys(self, prefix: str) -> list[str]:  # pragma: no cover - 통합
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)


def make_storage(config: StorageConfig) -> Storage:
    """설정에 따라 백엔드를 고른다. 잘못된 조합은 StorageConfig 검증이 이미 막았다."""
    if config.backend == "s3":
        return S3Storage(bucket=config.bucket)  # type: ignore[arg-type]
    return LocalStorage(root=config.local_root)
