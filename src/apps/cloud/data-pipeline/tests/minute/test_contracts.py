"""1분 파이프라인 공통 계약 테스트 (계획 §6).

의도: 계약 위반(naive datetime·half-open 위반·필드 드리프트)이 런타임 깊숙이가 아니라
생성 시점에 터지고, universe/window 수량(348 units·390 windows)이 fixture 로 고정된다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from data_pipeline.minute.clock import VirtualClock
from data_pipeline.minute.instrumentation import JSONL_FIELDS, JsonlInstrumentationWriter
from data_pipeline.minute.models import (
    KST,
    WINDOWS_PER_SESSION,
    CollectionRequest,
    canonical_json,
    content_checksum,
    load_universe,
    plan_session_windows,
)

FIXTURES = Path(__file__).parent / "fixtures"
SESSION_DATE = date(2026, 7, 31)


def make_request(**overrides) -> CollectionRequest:
    start = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
    base = dict(
        dataset="price_minute",
        window_start=start,
        window_end=start + timedelta(minutes=1),
        run_id=uuid4(),
        session_id=uuid4(),
        execution_mode="resident",
        universe_version="univ-fixture-v1",
        unit_ids=("100000", "100001"),
        failure_injection=None,
    )
    base.update(overrides)
    return CollectionRequest(**base)


class TestRequestContract:
    def test_naive_datetime_rejected(self):
        # naive 시각이 통과하면 KST/UTC 혼선이 window identity 를 조용히 오염시킨다
        with pytest.raises(ValidationError):
            make_request(window_start=datetime(2026, 7, 31, 9, 0))

    def test_half_open_violation_rejected(self):
        start = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
        with pytest.raises(ValidationError):
            make_request(window_start=start, window_end=start)
        with pytest.raises(ValidationError):
            make_request(window_start=start, window_end=start - timedelta(minutes=1))

    def test_duplicate_unit_ids_rejected(self):
        # 중복 unit 이 들어오면 expected/succeeded 수량 계약이 깨진다
        with pytest.raises(ValidationError):
            make_request(unit_ids=("100000", "100000"))

    def test_frozen(self):
        request = make_request()
        with pytest.raises(ValidationError):
            request.dataset = "other"


class TestSessionWindows:
    def test_390_windows_fixed(self):
        windows = plan_session_windows(SESSION_DATE)
        assert len(windows) == WINDOWS_PER_SESSION == 390
        # half-open 연속: 이전 end == 다음 start, 장 시작·마감 경계 고정
        assert windows[0][0] == datetime(2026, 7, 31, 9, 0, tzinfo=KST)
        assert windows[-1][1] == datetime(2026, 7, 31, 15, 30, tzinfo=KST)
        assert all(w[1] - w[0] == timedelta(minutes=1) for w in windows)
        assert all(prev[1] == cur[0] for prev, cur in zip(windows, windows[1:]))


class TestUniverseFixture:
    def test_348_units_fixed(self):
        universe = load_universe(FIXTURES / "universe_348.json")
        assert len(universe.etf_ids) == 31
        assert len(universe.constituent_ids) == 317
        assert len(universe.unit_ids) == 348
        assert len(set(universe.unit_ids)) == 348

    def test_hash_deterministic(self):
        first = load_universe(FIXTURES / "universe_348.json")
        second = load_universe(FIXTURES / "universe_348.json")
        assert first.universe_hash == second.universe_hash
        assert len(first.universe_hash) == 64


class TestCanonicalJson:
    def test_datetime_serialized_as_utc_z(self):
        kst_time = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
        assert canonical_json([kst_time]) == '["2026-07-31T00:00:00Z"]'

    def test_naive_datetime_fails(self):
        with pytest.raises(ValueError):
            canonical_json([datetime(2026, 7, 31, 9, 0)])

    def test_checksum_is_lowercase_sha256(self):
        digest = content_checksum(["a", 1])
        assert len(digest) == 64 and digest == digest.lower()


class TestVirtualClock:
    def test_naive_start_rejected(self):
        with pytest.raises(ValueError):
            VirtualClock(datetime(2026, 7, 31, 9, 0))

    def test_advance_forward_only(self):
        clock = VirtualClock(datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc))
        clock.advance(timedelta(minutes=1))
        assert clock.now() == datetime(2026, 7, 31, 9, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            clock.advance(timedelta(seconds=-1))


class TestJsonlWriter:
    def _record(self) -> dict:
        return {field: None for field in JSONL_FIELDS} | {
            "architecture": "pg_sqs",
            "dataset": "price_minute",
            "expected_count": 348,
            "seed": 42,
            "window_start": datetime(2026, 7, 31, 9, 0, tzinfo=KST),
        }

    def test_field_contract_enforced(self, tmp_path):
        writer = JsonlInstrumentationWriter(tmp_path / "run.jsonl")
        record = self._record()
        record.pop("seed")
        with pytest.raises(ValueError, match="누락"):
            writer.write(record)
        with pytest.raises(ValueError, match="미지"):
            writer.write(self._record() | {"extra_field": 1})

    def test_roundtrip_fixed_order_and_utc_z(self, tmp_path):
        path = tmp_path / "run.jsonl"
        JsonlInstrumentationWriter(path).write(self._record())
        parsed = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert tuple(parsed.keys()) == JSONL_FIELDS
        assert parsed["window_start"] == "2026-07-31T00:00:00Z"
        assert parsed["expected_count"] == 348
