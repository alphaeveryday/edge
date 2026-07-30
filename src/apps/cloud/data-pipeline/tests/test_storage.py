"""lake.storage 테스트 — 파티션 규약(SSOT)과 local 백엔드."""

import pytest

from data_pipeline.config import StorageConfig
from data_pipeline.lake import (
    LocalStorage,
    S3Storage,
    canonical_etf_holdings_partition,
    canonical_supply_contract_fact_partition,
    collection_log_key,
    is_raw_disclosure_key,
    is_raw_etf_key,
    make_storage,
    parse_raw_disclosure_key,
    parse_raw_etf_key,
    raw_disclosure_partition,
    raw_etf_partition,
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


def test_raw_etf_partition_matches_lake_layout():
    # WHY: ETF holdings 도 bronze 통일 규약 — 가격·재무와 동형(dataset=etf_holdings,
    #      ingest_date/run_id append). 이 문자열이 레이크 파티션 계약이다.
    assert (
        raw_etf_partition("fmp", "US", "2026-07-12", "20260712T000000Z")
        == "raw/source=fmp/dataset=etf_holdings/market=US"
        "/ingest_date=2026-07-12/run_id=20260712T000000Z"
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


def test_local_list_keys_are_posix_regardless_of_os(tmp_path):
    # WHY: 키 규약은 OS 무관 forward-slash 다(빌더 SSOT·S3 동형). Windows 에서 str(Path)의
    #      백슬래시가 새면 빌더 prefix 와 영영 안 맞아 레이크 전체가 '빈 것처럼' 보인다 —
    #      스텝이 0행을 읽고도 성공(0)으로 끝나는 조용한 실패가 된다.
    storage = LocalStorage(tmp_path)
    storage.put_bytes("feature/news/assertions/language=ko/part-0.parquet", b"x")
    assert storage.list_keys("feature/news/assertions/") == [
        "feature/news/assertions/language=ko/part-0.parquet"
    ]
    assert all("\\" not in k for k in storage.list_keys(""))


def test_disclosure_key_roundtrip_and_excludes_document_zip():
    # WHY: 정제(normalize_disclosure)는 raw 메타 ndjson 만 스캔하고 본문 documents/*.zip 은
    #      건드리면 안 된다(파싱 대상은 메타가 가리키는 본문) — is_ 판정이 zip 을 잡으면
    #      정제가 바이너리를 ndjson 으로 읽어 터진다. 경로 파싱도 이 모듈이 SSOT.
    meta = f"{raw_disclosure_partition('dart', 'KR', '2026-06-23', 'R1')}/part-00000.ndjson"
    doc = f"{raw_disclosure_partition('dart', 'KR', '2026-06-23', 'R1')}/documents/20260623900750.zip"
    assert is_raw_disclosure_key(meta) is True
    assert is_raw_disclosure_key(doc) is False
    assert parse_raw_disclosure_key(meta) == {
        "source": "dart", "market": "KR", "ingest_date": "2026-06-23", "run_id": "R1",
    }


def test_raw_etf_key_roundtrip_and_matches_only_ndjson():
    # WHY: ETF 정제(normalize_etf)는 raw etf_holdings 메타 ndjson 만 스캔한다 — is_ 판정과
    #      경로 파싱이 이 모듈의 SSOT 라 다른 곳에서 key 를 split 하지 않는다(가격 키와 동형).
    key = f"{raw_etf_partition('krx', 'KR', '2026-07-14', 'R1')}/part-00000.ndjson"
    assert is_raw_etf_key(key) is True
    assert is_raw_etf_key(raw_etf_partition("krx", "KR", "2026-07-14", "R1")) is False  # 프리픽스 아님
    assert parse_raw_etf_key(key) == {
        "source": "krx", "market": "KR", "ingest_date": "2026-07-14", "run_id": "R1",
    }


def test_canonical_etf_holdings_partition_is_market_as_of_keyed():
    # WHY: canonical ETF 는 멱등 — run_id·source_vendor 파티션 없이 (market, as_of_date)로
    #      가른다(가격 trade_date 와 동형). market-스코프라 한 파티션엔 한 벤더만 온다.
    assert (
        canonical_etf_holdings_partition("KR", "2026-07-14")
        == "canonical/holdings/etf_holdings/market=KR/as_of_date=2026-07-14"
    )


def test_canonical_supply_contract_fact_partition_is_report_date_keyed():
    # WHY: canonical 은 멱등 — run_id·source_vendor 파티션이 없고 report_date 하나로 가른다
    #      (가격 trade_date·뉴스 published_date 와 동형). rcept_no 는 파티션 내 행 키다.
    assert (
        canonical_supply_contract_fact_partition("2026-06-23")
        == "canonical/disclosures/supply_contract_fact/report_date=2026-06-23"
    )


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
