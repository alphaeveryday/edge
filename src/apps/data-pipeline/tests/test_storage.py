"""lake.storage 테스트 — 파티션 규약(SSOT)과 local 백엔드."""

import pytest

from data_pipeline.config import StorageConfig
from data_pipeline.lake import (
    LocalStorage,
    S3Storage,
    collection_log_key,
    make_storage,
    raw_news_partition,
)


def test_raw_news_partition_matches_lake_layout():
    # WHY: 이 문자열이 s3://stock-ai-lake/ 계층구조 계약이다 — 여기가 바뀌면
    #      레이크의 기존 파티션과 어긋나고 Step2 가 raw 를 찾지 못한다.
    assert (
        raw_news_partition("fmp", "US", "2026-07-01", "20260701T000000Z")
        == "raw/source=fmp/dataset=stock_news/market=US"
        "/published_date=2026-07-01/run_id=20260701T000000Z"
    )


def test_collection_log_key_matches_lake_layout():
    # WHY: 운영 로그 경로도 레이크 계약의 일부 — 조회 도구가 이 규약으로 찾는다.
    #      dataset= 로 갈라 같은 벤더의 뉴스·가격 로그가 같은 run_id 를 공유해도
    #      서로 덮어쓰지 않아야 한다.
    assert (
        collection_log_key("fmp", "price_daily", "2026-07-01", "r1")
        == "operations_archive/collection_logs/source=fmp/dataset=price_daily"
        "/started_date=2026-07-01/run_id=r1/log.json"
    )


def test_local_storage_roundtrip_and_list(tmp_path):
    # WHY: local 스텁은 s3 와 같은 키 규약으로 동작해야 백엔드 전환 시
    #      스텝 코드가 바뀌지 않는다(백엔드 추상화의 존재 이유).
    storage = LocalStorage(tmp_path)
    storage.put_bytes("raw/source=fmp/a.ndjson", b"line1\n")
    storage.put_bytes("raw/source=fmp/b.ndjson", b"line2\n")

    assert storage.get_bytes("raw/source=fmp/a.ndjson") == b"line1\n"
    assert storage.list_keys("raw") == [
        "raw/source=fmp/a.ndjson",
        "raw/source=fmp/b.ndjson",
    ]
    assert storage.list_keys("canonical") == []


def test_local_list_keys_uses_string_prefix_like_s3(tmp_path):
    # WHY: S3 는 문자열 prefix 매칭이라, 로컬 스텁이 prefix 를 '디렉터리'로 취급하면
    #      로컬 통과·배포 S3 불일치가 생긴다(백엔드 교체 계약 위반). 두 규약을 맞춘다.
    storage = LocalStorage(tmp_path)
    storage.put_bytes("raw/market=US/a.ndjson", b"x")
    storage.put_bytes("raw/market=KR/b.ndjson", b"y")

    # 컴포넌트 중간을 자르는 prefix 도 S3 처럼 매칭돼야 한다.
    assert storage.list_keys("raw/market=U") == ["raw/market=US/a.ndjson"]
    # 전체 키를 prefix 로 줘도 그 키가 매칭돼야 한다(디렉터리 취급이면 []).
    assert storage.list_keys("raw/market=US/a.ndjson") == ["raw/market=US/a.ndjson"]
    assert storage.list_keys("nope") == []


def test_make_storage_selects_backend_from_config():
    # WHY: 배포는 env(DATA_PIPELINE_STORAGE__*)만으로 s3 로 전환된다 — 선택 로직이
    #      설정을 무시하면 배포에서 로컬 디스크에 쓰고도 '성공'처럼 보인다.
    local = make_storage(StorageConfig(backend="local", local_root=".lake"))
    assert isinstance(local, LocalStorage)

    s3 = make_storage(StorageConfig(backend="s3", bucket="stock-ai-lake"))
    assert isinstance(s3, S3Storage)
    assert s3.bucket == "stock-ai-lake"


def test_s3_backend_without_bucket_fails_loud():
    # WHY: bucket 없는 s3 설정으로 부팅해 첫 put 에서야 죽으면 원인 추적이 어렵다 —
    #      설정 검증 시점에 실패해야 한다(Rule 12).
    with pytest.raises(ValueError):
        StorageConfig(backend="s3")
