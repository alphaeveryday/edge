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
    FINAL_WINDOW_SETTLE_SEC,
    KST,
    WINDOWS_PER_EXTENDED_SESSION,
    WINDOWS_PER_SESSION,
    CollectionRequest,
    CollectionResult,
    Universe,
    canonical_json,
    content_checksum,
    load_universe,
    plan_session_windows,
    scheduled_at_for,
)

FIXTURES = Path(__file__).parent / "fixtures"
SESSION_DATE = date(2026, 7, 31)


def make_request(**overrides) -> CollectionRequest:
    start = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
    base = dict(
        dataset="price_minute",
        window_start=start,
        window_end=start + timedelta(minutes=1),
        run_id=f"run_{uuid4().hex}",
        session_id=f"msn_{uuid4().hex}",
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
    def test_status_must_be_result_vocabulary(self):
        # 축을 섞지 않는다 — 실행 축(SUCCEEDED)·원장 축(DUE)·ops 관측 축(UNKNOWN)이
        # 결과에 오면 거부. UNKNOWN 을 허용하면 window CHECK(7어휘)가 저장에서 거부해
        # 불확실성이 런타임 깊숙이에서 터진다
        for bad in ("SUCCEEDED", "BOGUS", "UNKNOWN", "DUE", "MISSING"):
            with pytest.raises(ValidationError):
                make_result(status=bad)

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
        windows = plan_session_windows(SESSION_DATE, universe=None)
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
            # 시간외 종목 선언이 없는 universe 는 그 축이 생기기 전과 같은 identity 다
            # — 배포만으로 hash 가 바뀌면 그날 세션이 통째로 막힌다(ALPHA-684)
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
            extended_hours_ids=tuple(reversed(original.extended_hours_ids)),
        )
        assert original.universe_hash == reordered.universe_hash


class TestTradingHoursClass:
    """시간대별 기대 유니버스 (ALPHA-684 — 실측 근거 `.dev/toss-openapi-실측.md`).

    의도: 개별주는 08:00~20:00(720), ETF·지수·비NXT 종목은 09:00~15:30(390)이다.
    이 축이 없으면 시간외 window 가 매번 "15:30 이 마지막인 종목"을 missing 으로 잡아
    영원히 INCOMPLETE 로 남는다.
    """

    def _universe(self, extended=()):
        return Universe(
            universe_version="v1", etf_ids=("E1",),
            constituent_ids=("C1", "C2"), extended_hours_ids=extended,
        )

    def test_close_window_waits_for_the_auction_print(self):
        """마감(15:30)으로 끝나는 window 만 늦게 집는다 — 종가 단일가 확정 대기.

        `window_end` 즉시 집으면 단일가가 아직 캔들에 안 실려 미완성 봉(vol 0·직전가)이
        커밋된다(08-03 실측: 0005G0 수집 43,710 V=0 vs 소급 43,305 V=140, 일봉 43,305).
        지연이 0 이면 그 회귀가 그대로 돌아온다.
        """
        windows = plan_session_windows(SESSION_DATE, universe=None)
        close_end = windows[-1][1]
        assert close_end == datetime(2026, 7, 31, 15, 30, tzinfo=KST)
        assert scheduled_at_for(close_end, dataset="price_minute") == close_end + timedelta(
            seconds=FINAL_WINDOW_SETTLE_SEC)
        # 상수를 양쪽에 쓰면 값이 1초로 바뀌어도 위 단언이 통과한다 — 계약은 "늦춘다"가
        # 아니라 "**벤더 캔들이 확정될 만큼** 늦춘다"이므로 의미 있는 하한을 건다.
        assert FINAL_WINDOW_SETTLE_SEC >= 30, (
            f"{FINAL_WINDOW_SETTLE_SEC}초로는 종가 단일가 반영 시차를 못 덮는다 — "
            "짧게 두면 미완성 봉(vol 0·직전가)이 다시 커밋된다")

    def test_news_sessions_are_not_delayed(self):
        """종가 단일가는 **가격 캔들** 얘기다 — 같은 plan_session 을 쓰는 뉴스 세션에
        걸면 realtime 뉴스 레인이 15:30 에 1분씩 늦어진다(추출·조립이 그만큼 밀린다)."""
        close = datetime(2026, 7, 31, 15, 30, tzinfo=KST)
        assert scheduled_at_for(close, dataset="news_minute") == close
        assert scheduled_at_for(close, dataset="price_minute") != close

    def test_naive_window_end_is_rejected(self):
        """naive 는 실행 환경 TZ 로 해석돼 호스트마다 결과가 갈린다 — KST 호스트에선
        지연되고 UTC 호스트에선 안 걸린다. 조용히 넘기면 배포 환경에 따라 결함이
        되살아난다(Rule 12)."""
        with pytest.raises(ValueError, match="naive"):
            scheduled_at_for(datetime(2026, 7, 31, 15, 30), dataset="price_minute")

    def test_non_close_windows_are_scheduled_at_window_end(self):
        """마감 아닌 window 는 그대로 `window_end` — 장중 지연을 만들면 안 된다."""
        windows = plan_session_windows(SESSION_DATE, universe=None)
        assert all(scheduled_at_for(we, dataset="price_minute") == we for _, we in windows[:-1])

    def test_extended_session_also_defers_its_1530_window(self):
        """시간외 세션(720)에도 15:30 로 끝나는 window 가 있고 거기에도 걸린다 —
        단일가 체결 시각은 세션 길이와 무관하다. 마지막(20:00) window 는 대상이 아니다."""
        windows = plan_session_windows(SESSION_DATE, universe=self._universe(("C1",)))
        by_end = {we: scheduled_at_for(we, dataset="price_minute") for _, we in windows}
        close = datetime(2026, 7, 31, 15, 30, tzinfo=KST)
        assert by_end[close] == close + timedelta(seconds=FINAL_WINDOW_SETTLE_SEC)
        last = datetime(2026, 7, 31, 20, 0, tzinfo=KST)
        assert by_end[last] == last

    def test_extended_universe_plans_720_windows(self):
        windows = plan_session_windows(SESSION_DATE, universe=self._universe(("C1",)))
        assert len(windows) == WINDOWS_PER_EXTENDED_SESSION == 720
        assert windows[0][0] == datetime(2026, 7, 31, 8, 0, tzinfo=KST)
        assert windows[-1][1] == datetime(2026, 7, 31, 20, 0, tzinfo=KST)
        assert all(prev[1] == cur[0] for prev, cur in zip(windows, windows[1:]))

    def test_no_extended_units_keeps_390(self):
        # 클래스가 선언되지 않은 universe 는 지금까지의 정규장 계획 그대로다
        assert len(plan_session_windows(SESSION_DATE, universe=self._universe())) == 390
        assert len(plan_session_windows(SESSION_DATE, universe=None)) == WINDOWS_PER_SESSION

    def test_units_at_narrows_outside_regular_hours(self):
        universe = self._universe(("C1",))
        assert universe.units_at(datetime(2026, 7, 31, 10, 0, tzinfo=KST)) == ("E1", "C1", "C2")
        assert universe.units_at(datetime(2026, 7, 31, 15, 29, tzinfo=KST)) == ("E1", "C1", "C2")
        # 15:30 부터는 시간외 종목만 — 여기서 전 종목을 기대하면 INCOMPLETE 가 영구화된다
        assert universe.units_at(datetime(2026, 7, 31, 15, 30, tzinfo=KST)) == ("C1",)
        assert universe.units_at(datetime(2026, 7, 31, 8, 0, tzinfo=KST)) == ("C1",)

    def test_units_at_uses_kst_not_host_local(self):
        # 원장 window 는 UTC 로 돌아온다 — 로컬 시각으로 읽으면 경계가 통째로 밀린다
        universe = self._universe(("C1",))
        utc_1000_kst = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
        assert universe.units_at(utc_1000_kst) == ("E1", "C1", "C2")

    def test_units_at_rejects_naive_datetime(self):
        # naive 를 astimezone 하면 호스트 로컬로 해석돼 같은 입력의 기대 집합이 배포
        # 환경마다 달라진다 — 그 차이는 조용한 누락으로 나온다
        with pytest.raises(ValueError):
            self._universe(("C1",)).units_at(datetime(2026, 7, 31, 10, 0))

    def test_hours_class_identity_ignores_declaration_order(self):
        # 클래스를 identity 에 넣되 **멤버십**으로 넣는다 — 선언 순서가 다르다고 세션
        # universe 불일치(재계획 거부)가 되면 안 된다
        assert (self._universe(("C1", "C2")).universe_hash
                == self._universe(("C2", "C1")).universe_hash)

    def test_units_at_rejects_window_outside_trading_hours(self):
        # 조용히 빈 집합을 주면 "기대할 게 없는 window" 와 "잘못 계획된 window" 가 같아진다
        with pytest.raises(ValueError):
            self._universe(("C1",)).units_at(datetime(2026, 7, 31, 7, 59, tzinfo=KST))
        with pytest.raises(ValueError):
            self._universe().units_at(datetime(2026, 7, 31, 16, 0, tzinfo=KST))

    def test_every_planned_window_has_expected_units(self):
        # 계획(plan_session_windows)과 기대(units_at)가 같은 규칙에서 나온다는 성질 —
        # 갈리면 Worker 가 기대 0 인 window 를 영원히 재시도한다
        for universe in (self._universe(), self._universe(("C1", "C2"))):
            for window_start, _ in plan_session_windows(SESSION_DATE, universe=universe):
                assert universe.units_at(window_start)

    def test_extended_ids_must_be_in_universe(self):
        # 오타를 통과시키면 그 종목이 시간외에 안 잡히는 이유를 아무도 못 찾는다
        with pytest.raises(ValidationError):
            self._universe(("NOPE",))
        with pytest.raises(ValidationError):
            self._universe(("C1", "C1"))

    def test_hours_class_is_part_of_universe_identity(self):
        # 멤버가 같아도 클래스가 다르면 다른 universe 다 — 같은 hash 면 session 충돌
        # 가드가 기대 유니버스 변경을 조용히 통과시킨다
        assert self._universe().universe_hash != self._universe(("C1",)).universe_hash


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
