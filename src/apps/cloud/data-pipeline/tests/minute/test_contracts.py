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
    CollectionResult,
    Universe,
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

    def test_empty_unit_ids_rejected(self):
        # 빈 universe 요청이 통과하면 수집 0건이 VALID 로 커밋되고 watermark 만 전진한다
        with pytest.raises(ValidationError):
            make_request(unit_ids=())


def make_result(**overrides) -> CollectionResult:
    now = datetime(2026, 7, 31, 9, 1, tzinfo=KST)
    base = dict(
        status="VALID",
        expected_count=348,
        succeeded_count=348,
        failed_count=0,
        retry_count=0,
        artifact_uri="memory://minute/price_minute/x/2026-07-31T09:00",
        manifest_checksum="a" * 64,
        result_checksum="b" * 64,
        watermark_before=None,
        watermark_after=now,
        generation=1,
        stage_timestamps={"collection_started_at": now},
    )
    base.update(overrides)
    return CollectionResult(**base)


class TestResultContract:
    def test_status_must_be_data_status_vocabulary(self):
        # ops/states.py 네 축을 섞지 않는다 — 실행 축(SUCCEEDED)이 데이터 축에 오면 거부
        with pytest.raises(ValidationError):
            make_result(status="SUCCEEDED")
        with pytest.raises(ValidationError):
            make_result(status="BOGUS")

    def test_unclassified_units_rejected(self):
        # 합이 모자라면 unit 이 조용히 사라진 것 — VALID 위장 금지 (Rule 12)
        with pytest.raises(ValidationError):
            make_result(succeeded_count=347, failed_count=0)

    def test_valid_status_with_failures_rejected(self):
        # 실패가 있는데 VALID 면 status 만 믿는 소비자가 누락 window 를 정상 확정한다
        with pytest.raises(ValidationError):
            make_result(status="VALID", succeeded_count=343, failed_count=5)
        ok = make_result(status="INCOMPLETE", succeeded_count=343, failed_count=5)
        assert ok.failed_count == 5

    def test_counts_strict_no_coercion(self):
        # '3'(str)·True(bool) 가 수량으로 강제되면 잘못된 직렬화가 조용히 통과한다
        with pytest.raises(ValidationError):
            make_result(expected_count="348")
        with pytest.raises(ValidationError):
            make_result(retry_count=True)

    def test_checksum_format_enforced(self):
        # 빈/임의 checksum 이 통과하면 재실행 no-op 판정이 서로 다른 artifact 를 동일시한다
        with pytest.raises(ValidationError):
            make_result(result_checksum="")
        with pytest.raises(ValidationError):
            make_result(manifest_checksum="X" * 64)  # lowercase hex 만
        with pytest.raises(ValidationError):
            make_result(result_checksum="g" * 64)  # lowercase 라도 hex 밖 문자는 거부

    def test_stage_timestamps_must_have_evidence(self):
        with pytest.raises(ValidationError):
            make_result(stage_timestamps={})


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

    def test_hash_pinned_golden_value(self):
        # fixture 는 동결이다 — ID 하나를 바꾸고 개수·version 을 유지하는 드리프트도
        # 여기서 터져야 한다(재계산-비교만으로는 못 잡는다)
        universe = load_universe(FIXTURES / "universe_348.json")
        assert (
            universe.universe_hash
            == "5b33574f724ecb10f7f3db0830c2fc6dfb45b386ee8f3a9c9ae43dab1e03d52e"
        )

    def test_hash_is_membership_identity_not_order(self):
        # 같은 구성을 다른 순서로 로드해도 같은 universe 다 — 순서 차이가 세션 universe
        # 불일치(새 세션 거부)로 오판되면 안 된다
        original = load_universe(FIXTURES / "universe_348.json")
        reordered = Universe(
            universe_version=original.universe_version,
            etf_ids=tuple(reversed(original.etf_ids)),
            constituent_ids=tuple(reversed(original.constituent_ids)),
        )
        assert original.universe_hash == reordered.universe_hash


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

    def test_nan_infinity_rejected(self):
        # NaN/Infinity 는 표준 JSON 이 아니다 — 비정상 값에 유효한 checksum 을 주지 않는다
        with pytest.raises(ValueError):
            canonical_json([float("nan")])
        with pytest.raises(ValueError):
            canonical_json([float("inf")])


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

    def test_now_is_utc_like_system_clock(self):
        # SystemClock 과 교체 가능해야 한다 — KST 로 만들어도 now() 표현은 UTC
        clock = VirtualClock(datetime(2026, 7, 31, 9, 0, tzinfo=KST))
        assert clock.now().tzinfo == timezone.utc
        assert clock.now() == datetime(2026, 7, 31, 0, 0, tzinfo=timezone.utc)


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
