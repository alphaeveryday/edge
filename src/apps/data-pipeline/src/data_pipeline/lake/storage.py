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


def collection_log_key(source: str, started_date: str, run_id: str) -> str:
    """수집 실행 로그(런당 1건) 키."""
    return (
        f"operations_archive/collection_logs/source={source}"
        f"/started_date={started_date}/run_id={run_id}/log.json"
    )


def canonical_news_articles_key(published_date: str, source_vendor: str) -> str:
    """canonical 뉴스 메타 파티션 파일 키. run_id 없음 — article_id 병합(멱등)."""
    return (
        f"canonical/news/news_articles/published_date={published_date}"
        f"/source_vendor={source_vendor}/data.parquet"
    )


def canonical_news_bodies_key(published_date: str, source_vendor: str) -> str:
    """canonical 뉴스 본문 파티션 파일 키 — 메타(news_articles)와 분리된 데이터셋."""
    return (
        f"canonical/news/news_article_bodies/published_date={published_date}"
        f"/source_vendor={source_vendor}/data.parquet"
    )


def quality_log_key(dataset: str, checked_date: str, run_id: str) -> str:
    """품질검증 로그(런당 1건) 키."""
    return (
        f"operations_archive/data_quality_logs/dataset={dataset}"
        f"/checked_date={checked_date}/run_id={run_id}/log.json"
    )


# ── 백엔드 ──────────────────────────────────────────────
class Storage(Protocol):
    """레이크 키-바이트 저장 계약. 키는 위 빌더가 만든 상대경로."""

    def put_bytes(self, key: str, data: bytes) -> None: ...

    def get_bytes(self, key: str) -> bytes: ...

    def get_bytes_or_none(self, key: str) -> bytes | None: ...

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

    def get_bytes_or_none(self, key: str) -> bytes | None:
        """없으면 None — canonical 병합의 '기존 파티션 없음' 케이스가 정상 경로라서."""
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def list_keys(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in base.rglob("*")
            if p.is_file()
        )


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

    def get_bytes_or_none(self, key: str) -> bytes | None:  # pragma: no cover - 통합
        try:
            return self.get_bytes(key)
        except self.client.exceptions.NoSuchKey:
            return None

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
