"""artifact·manifest 경계 테스트 (ALPHA-665, 계획 §8 전반부).

의도: S3 와 DB 는 한 트랜잭션이 아니라서(v0.7 9절) 복구가 전적으로 "결정적·불변
key/바이트" 위에 선다 — 결정성·불변성·왕복이 깨지면 재실행 no-op 과 orphan 복구
판정이 전부 오염된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from data_pipeline.lake.storage import (
    LocalStorage,
    minute_window_manifest_key,
    raw_price_minute_artifact_key,
)
from data_pipeline.minute.artifacts import (
    ArtifactImmutabilityError,
    build_window_manifest,
    parse_manifest,
    put_immutable,
    serialize_manifest,
    serialize_records,
    sha256_bytes,
)
from data_pipeline.minute.models import KST

WINDOW_START = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
RECORDS = [
    {"unit_id": "100000", "ts": WINDOW_START, "open": 1000, "high": 1010,
     "low": 995, "close": 1005, "volume": 1234},
    {"unit_id": "100001", "ts": WINDOW_START, "open": 2000, "high": 2020,
     "low": 1990, "close": 2010, "volume": 567},
]


def make_manifest(**overrides):
    args = dict(
        dataset="price_minute",
        session_id="msn_x",
        window_start=WINDOW_START,
        window_end=WINDOW_START + timedelta(minutes=1),
        generation=1,
        units={"received": ["100001", "100000"], "no_trade": ["100005"], "missing": []},
        artifact_key="raw/.../bars.ndjson",
        artifact_checksum="a" * 64,
    )
    args.update(overrides)
    return build_window_manifest(**args)


class TestKeys:
    def test_deterministic_and_immutable_shape(self):
        # run_id 없는 결정적 key — correction 만 generation 으로 갈린다
        key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        assert key == (
            "raw/source=toss/dataset=price_minute/market=KR"
            "/session_date=2026-07-31/window=0900/generation=1/bars.ndjson"
        )
        corrected = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 2)
        assert corrected != key  # 새 세대 = 새 key — 기존 artifact 를 덮지 않는다

    def test_manifest_key_zone_disjoint_from_canonical(self):
        # canonical price_bars(ALPHA-648 확정 설계)와 존이 달라야 충돌이 없다
        key = minute_window_manifest_key("price_minute", "toss", "KR", "2026-07-31", "0900", 1)
        assert key.startswith("operations_archive/minute_manifests/")
        assert "canonical/" not in key


class TestSerialization:
    def test_records_bytes_deterministic(self):
        first = serialize_records(RECORDS)
        second = serialize_records([dict(r) for r in RECORDS])
        assert first == second
        assert sha256_bytes(first) == sha256_bytes(second)
        # 행 순서는 데이터의 일부다 — 호출자(collector)가 unit_id 정렬을 보장한다
        assert serialize_records(list(reversed(RECORDS))) != first

    def test_manifest_roundtrip(self):
        manifest = make_manifest()
        parsed = parse_manifest(serialize_manifest(manifest))
        assert parsed == manifest
        assert parsed["units"]["received"] == ["100000", "100001"]  # 정렬 고정
        assert parsed["units"]["invalid"] == []  # 미지정 분류도 빈 목록으로 존재

    def test_manifest_unknown_unit_class_rejected(self):
        with pytest.raises(ValueError, match="미지 키"):
            make_manifest(units={"receievd": ["100000"]})  # 오타가 조용히 빠지면 안 된다

    def test_manifest_missing_field_rejected(self):
        manifest = make_manifest()
        del manifest["artifact_checksum"]
        with pytest.raises(ValueError, match="누락"):
            parse_manifest(serialize_manifest(manifest))


class TestPutImmutable:
    def test_put_then_rerun_is_noop(self, tmp_path):
        storage = LocalStorage(root=tmp_path)
        key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        data = serialize_records(RECORDS)
        first = put_immutable(storage, key, data)
        second = put_immutable(storage, key, data)  # 재실행 — no-op 재사용
        assert first == second == sha256_bytes(data)
        assert storage.get_bytes(key) == data

    def test_different_bytes_same_key_fails_loud(self, tmp_path):
        # 같은 key 다른 내용 = 결정성 위반 — 덮으면 checksum 재사용 판정이 오염된다
        storage = LocalStorage(root=tmp_path)
        key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        put_immutable(storage, key, serialize_records(RECORDS))
        with pytest.raises(ArtifactImmutabilityError):
            put_immutable(storage, key, serialize_records(RECORDS[:1]))
        assert storage.get_bytes(key) == serialize_records(RECORDS)  # 원본 보존

    def test_orphan_scenario_reuses_artifact(self, tmp_path):
        # v0.7 9절 복구 표: S3 PUT 후 DB commit 전 종료 → 재claim 실행이 같은
        # key/checksum 을 재사용한다 (orphan quarantine 은 3-2 reconciler 소관)
        storage = LocalStorage(root=tmp_path)
        key = raw_price_minute_artifact_key("toss", "KR", "2026-07-31", "0900", 1)
        data = serialize_records(RECORDS)
        checksum_before_crash = put_immutable(storage, key, data)
        checksum_after_restart = put_immutable(storage, key, data)
        assert checksum_before_crash == checksum_after_restart
