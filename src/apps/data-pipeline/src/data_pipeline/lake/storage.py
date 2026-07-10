"""레이크 스토리지 백엔드(local|s3) + 경로 빌더.

이 모듈이 s3://stock-ai-lake/ 파티션 규약의 SSOT 다 — 경로 문자열을
다른 곳에서 조립하지 말고 여기 빌더를 쓴다.

- raw:  run_id 별 append (재현성). 파티션 키는 소스별로 다르다 — 뉴스는 published_date,
        가격·재무는 ingest_date(수집일). 각 빌더 주석 참고.
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


def raw_financial_partition(
    source: str, market: str, ingest_date: str, run_id: str
) -> str:
    """raw 재무제표 파티션 프리픽스 (끝 슬래시 없음).

    가격(raw_price_partition)과 동형이다 — bronze 통일 규약: 소스 불문 원본을 수집일
    (ingest_date) 기준으로 run_id 별 append 한다(전부 보존, dedup 없음). 재무 응답은 한
    (심볼·문서·주기) 질의가 여러 회계기간을 한 번에 주므로 원본을 수집일로 보존한다.

    재무는 드물게·비동기로 공시돼 매일 재폴링하면 같은 스냅샷이 날마다 쌓이지만, 그 중복
    제거·정정(SCD)·point-in-time 판정은 후속 canonical(silver) MERGE 소관이다 — raw 는
    받은 그대로 append 해 감사·재현성을 지킨다(정체성 판정을 raw 로 끌어올리지 않는다).
    statement_type·period_type·filing_date 등은 각 레코드에 그대로 보존돼 canonical 이 쓴다.
    """
    return (
        f"raw/source={source}/dataset=financial_statements/market={market}"
        f"/ingest_date={ingest_date}/run_id={run_id}"
    )


# ── raw price 스캔(정제 입력) ────────────────────────────
# 정제(normalize_price)는 raw price 를 벤더·시장·수집일에 걸쳐 읽어 (market,ticker,
# trade_date) 로 재그룹한다. raw 는 수집일(ingest_date)로 파티션되므로 한 trade_date 가
# 여러 ingest_date/run_id 에 흩어진다 — 프리픽스로 dataset 전체를 훑어야 한다. 경로
# 조립뿐 아니라 **경로 해석(파싱)도 이 모듈이 SSOT** 다(다른 곳에서 key 를 split 하지 않는다).
_RAW_PRICE_MARKER = "/dataset=price_daily/"


def is_raw_price_key(key: str) -> bool:
    """raw price_daily 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return key.startswith("raw/") and _RAW_PRICE_MARKER in key and key.endswith(".ndjson")


def parse_raw_price_key(key: str) -> dict[str, str]:
    """raw price 키에서 파티션 값(source·market·ingest_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=price_daily/market=…/ingest_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "ingest_date": segs["ingest_date"],
        "run_id": segs["run_id"],
    }


# ── raw news 스캔(정제 입력) ─────────────────────────────
# 정제(normalize_news)는 raw stock_news 를 벤더·시장·발행일에 걸쳐 읽어 표준 메타행으로
# 정규화한다. 경로 조립뿐 아니라 **경로 해석(파싱)도 이 모듈이 SSOT** 다(다른 곳에서 key 를
# split 하지 않는다 — is_raw_price_key/parse_raw_price_key 와 동형).
_RAW_NEWS_MARKER = "/dataset=stock_news/"


def is_raw_news_key(key: str) -> bool:
    """raw stock_news 데이터 파일 키인지. (part-*.ndjson 만, 프리픽스 디렉터리 아님.)"""
    return key.startswith("raw/") and _RAW_NEWS_MARKER in key and key.endswith(".ndjson")


def parse_raw_news_key(key: str) -> dict[str, str]:
    """raw news 키에서 파티션 값(source·market·published_date·run_id) 추출.

    경로 규약: raw/source=…/dataset=stock_news/market=…/published_date=…/run_id=…/part-*.ndjson.
    `key=value` 세그먼트만 취해 dict 로 — part 파일명(= 없음)은 자연히 빠진다.
    """
    segs = dict(seg.split("=", 1) for seg in key.split("/") if "=" in seg)
    return {
        "source": segs["source"],
        "market": segs["market"],
        "published_date": segs["published_date"],
        "run_id": segs["run_id"],
    }


def canonical_price_daily_partition(market: str, trade_date: str) -> str:
    """canonical 일봉 파티션 프리픽스 (끝 슬래시 없음).

    raw 와 달리 run_id·source_vendor 파티션이 없다 — canonical 은 멱등이라 같은 raw 를
    몇 번 정제해도 결과가 같아야 하고, 벤더는 시장이 가른다(US=fmp, KR=kis). 정체성 키
    (market,ticker,trade_date) 중 market·trade_date 가 파티션, ticker 는 파티션 내 행 키다.
    """
    return f"canonical/market_data/price_daily/market={market}/trade_date={trade_date}"


def canonical_news_articles_partition(published_date: str) -> str:
    """canonical 뉴스 메타 파티션 프리픽스 (끝 슬래시 없음).

    canonical 은 소스를 흡수한 **통합 구조**다 — source_vendor 는 파티션이 아니라 **컬럼**
    (provenance)이라 벤더가 한 날짜 파티션에 섞인다. 파티션은 `published_date` 하나(가격의
    trade_date 파티션과 동형 — 프루닝·라이프사이클). run_id 는 없다(멱등). 정체성 키는
    `article_id`(=원문 URL 해시, 소스 무관)로 파티션 내 행 키다.
    """
    return f"canonical/news/news_articles/published_date={published_date}"


def quality_log_key(dataset: str, checked_date: str, run_id: str) -> str:
    """정제 품질 로그(검증 실행당 1건) 키.

    canonical 자체는 run_id 가 없지만(멱등), '이 검증 실행이 무엇을 몇 건 걸렀나'는
    실행 단위 감사라 run_id 로 남긴다. collection_log(수집)와 분리된 정제 단계 로그다.
    """
    return (
        f"operations_archive/data_quality_logs/dataset={dataset}"
        f"/checked_date={checked_date}/run_id={run_id}/log.json"
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
