"""결정적 fake collector 테스트 (계획 §6).

의도: 이후 모든 PR 의 테스트가 이 fake 위에 선다. 결정성(같은 입력=같은 checksum,
시각 무관)이 깨지면 재실행 no-op·중복 0 같은 상위 합격 기준을 검증할 수 없으므로,
결정성 자체를 여기서 계약으로 고정한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from data_pipeline.minute.fake_collector import FakeNewsFeed, FakePriceCollector
from data_pipeline.minute.models import KST, CollectionRequest, load_universe
from data_pipeline.ops.states import DATA_INCOMPLETE, DATA_VALID
from data_pipeline.parse import bigkinds_date

FIXTURES = Path(__file__).parent / "fixtures"
UNIVERSE = load_universe(FIXTURES / "universe_348.json")


def scenario(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_request(run_id=None, session_id=None) -> CollectionRequest:
    start = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
    return CollectionRequest(
        dataset="price_minute",
        window_start=start,
        window_end=start + timedelta(minutes=1),
        run_id=run_id or uuid4(),
        session_id=session_id or uuid4(),
        execution_mode="resident",
        universe_version=UNIVERSE.universe_version,
        unit_ids=UNIVERSE.unit_ids,
        failure_injection=None,
    )


NOW = datetime(2026, 7, 31, 9, 1, 5, tzinfo=KST)


class TestPriceDeterminism:
    def test_same_seed_same_request_same_checksum(self):
        run_id, session_id = uuid4(), uuid4()
        request = make_request(run_id, session_id)
        first, records_first, _ = FakePriceCollector(
            scenario("price_normal.json"), seed=42
        ).collect(request, NOW)
        second, records_second, _ = FakePriceCollector(
            scenario("price_normal.json"), seed=42
        ).collect(request, NOW)
        assert first.result_checksum == second.result_checksum
        assert records_first == records_second

    def test_different_seed_different_checksum(self):
        request = make_request()
        first, _, _ = FakePriceCollector(scenario("price_normal.json"), seed=42).collect(
            request, NOW
        )
        second, _, _ = FakePriceCollector(scenario("price_normal.json"), seed=43).collect(
            request, NOW
        )
        assert first.result_checksum != second.result_checksum

    def test_clock_change_does_not_change_checksum(self):
        # 계획 §6: 현재 시각을 바꿔도 explicit window 결과는 동일해야 한다
        request = make_request(uuid4(), uuid4())
        collector = FakePriceCollector(scenario("price_normal.json"), seed=42)
        early, _, _ = collector.collect(request, NOW)
        late, _, _ = collector.collect(request, NOW + timedelta(hours=3))
        assert early.result_checksum == late.result_checksum
        assert early.manifest_checksum == late.manifest_checksum
        # 실행 시각은 stage_timestamps 에만 나타난다
        assert early.stage_timestamps != late.stage_timestamps

    def test_same_instant_different_tz_same_records(self):
        # 같은 순간을 KST 로 주든 UTC 로 주든 결과가 같아야 한다 — digest 가
        # str(datetime) 표현이 아니라 UTC 정규화 값에서 유도됨을 고정한다
        run_id, session_id = uuid4(), uuid4()
        kst_request = make_request(run_id, session_id)
        utc_request = kst_request.model_copy(
            update={
                "window_start": kst_request.window_start.astimezone(timezone.utc),
                "window_end": kst_request.window_end.astimezone(timezone.utc),
            }
        )
        collector = FakePriceCollector(scenario("price_normal.json"), seed=42)
        kst_result, kst_records, _ = collector.collect(kst_request, NOW)
        utc_result, utc_records, _ = collector.collect(utc_request, NOW)
        assert kst_result.result_checksum == utc_result.result_checksum
        assert [r["open"] for r in kst_records] == [r["open"] for r in utc_records]

    def test_unit_order_does_not_change_checksum(self):
        # universe_hash 가 순서 무관이듯 window 데이터 identity 도 순서 무관이어야 한다 —
        # 같은 멤버십의 다른 순서가 다른 checksum 이면 허위 correction 이 생긴다
        run_id, session_id = uuid4(), uuid4()
        request = make_request(run_id, session_id)
        shuffled = request.model_copy(update={"unit_ids": tuple(reversed(request.unit_ids))})
        collector = FakePriceCollector(scenario("price_normal.json"), seed=42)
        original, _, _ = collector.collect(request, NOW)
        reordered, _, _ = collector.collect(shuffled, NOW)
        assert original.result_checksum == reordered.result_checksum

    def test_checksum_derives_from_data_not_generation(self):
        # 값이 같은 재실행(generation 만 증가)은 같은 checksum 이어야 한다 — 이게 깨지면
        # "같은 checksum → artifact 재사용·generation 불변" 판정(계획 §8)이 성립 안 한다
        request = make_request(uuid4(), uuid4())
        first, _, _ = FakePriceCollector(scenario("price_normal.json"), seed=42).collect(
            request, NOW
        )
        rerun, _, _ = FakePriceCollector({"generation": 2}, seed=42).collect(request, NOW)
        assert rerun.generation == 2
        assert first.result_checksum == rerun.result_checksum


class TestPriceScenarioValidation:
    def test_typo_key_fails_loud(self):
        # fixture 키 오타가 조용히 no-op 시나리오가 되면 실패 경로 테스트가 무력화된다
        with pytest.raises(ValueError, match="미지 키"):
            FakePriceCollector({"scenario": "x", "missing_unit_idz": ["100003"]}, seed=1)
        with pytest.raises(ValueError, match="미지 키"):
            FakeNewsFeed({"scenario": "x", "initial_countt": 10}, seed=1)

    def test_string_instead_of_list_fails_loud(self):
        # "100003" 을 그대로 주면 문자 집합 {'1','0','3'} 이 돼 시나리오가 무력화된다
        with pytest.raises(ValueError, match="문자열 배열"):
            FakePriceCollector({"missing_unit_ids": "100003"}, seed=1)

    def test_duplicate_scenario_ids_fail_loud(self):
        # 중복은 frozenset 으로 조용히 접힌다 — 다른 ID 를 의도한 복붙 오류일 수 있다
        with pytest.raises(ValueError, match="중복 ID"):
            FakePriceCollector({"missing_unit_ids": ["100003", "100003"]}, seed=1)

    def test_overlapping_roles_fail_loud(self):
        # 한 unit 이 missing 이자 no_trade 면 분기 순서가 의미를 임의로 정한다
        with pytest.raises(ValueError, match="같은 unit"):
            FakePriceCollector(
                {"missing_unit_ids": ["100003"], "no_trade_unit_ids": ["100003"]}, seed=1
            )

    def test_ghost_unit_fails_loud(self):
        # 오타 ID 는 알려진 키 안에 숨으면 전 unit 성공으로 조용히 넘어간다 — collect 가 잡는다
        collector = FakePriceCollector({"missing_unit_ids": ["999999"]}, seed=1)
        with pytest.raises(ValueError, match="universe 에 없는 unit"):
            collector.collect(make_request(), NOW)

    def test_non_int_generation_fails_loud(self):
        with pytest.raises(ValueError, match="정수"):
            FakePriceCollector({"generation": 1.9}, seed=1)

    def test_correction_without_generation_bump_fails_loud(self):
        # generation 1 이면 delta 가 적용되지 않는다 — 선언한 정정의 조용한 no-op 차단
        with pytest.raises(ValueError, match="generation"):
            FakePriceCollector(
                {"correction": {"unit_ids": ["100000"], "close_delta": 7}}, seed=1
            )

    def test_correction_with_zero_delta_fails_loud(self):
        # delta 0 이면 generation 만 올라가고 값은 그대로 — 무의미한 정정 차단
        with pytest.raises(ValueError, match="close_delta"):
            FakePriceCollector(
                {"generation": 2, "correction": {"unit_ids": ["100000"]}}, seed=1
            )

    def test_correction_with_empty_units_fails_loud(self):
        # 빈 unit_ids 는 generation/delta 가드를 모두 우회한다 ({} 블록 포함)
        with pytest.raises(ValueError, match="unit_ids"):
            FakePriceCollector(
                {"generation": 2, "correction": {"unit_ids": [], "close_delta": 7}}, seed=1
            )
        with pytest.raises(ValueError, match="unit_ids"):
            FakePriceCollector({"generation": 2, "correction": {}}, seed=1)

    def test_correction_overlapping_missing_fails_loud(self):
        # missing unit 은 bar 를 안 만드니 정정이 물리적으로 불가능하다
        with pytest.raises(ValueError, match="같은 unit"):
            FakePriceCollector(
                {
                    "generation": 2,
                    "missing_unit_ids": ["100000"],
                    "correction": {"unit_ids": ["100000"], "close_delta": 7},
                },
                seed=1,
            )


class TestPriceScenarios:
    def test_normal_all_units_succeed(self):
        result, records, manifest = FakePriceCollector(
            scenario("price_normal.json"), seed=1
        ).collect(make_request(), NOW)
        assert result.status == DATA_VALID
        assert result.expected_count == 348
        assert result.succeeded_count == 348
        assert result.failed_count == 0
        assert len(records) == 348
        assert manifest["missing"] == [] and manifest["no_trade"] == []

    def test_partial_missing_is_failure(self):
        config = scenario("price_partial_missing.json")
        result, records, manifest = FakePriceCollector(config, seed=1).collect(
            make_request(), NOW
        )
        assert result.status == DATA_INCOMPLETE
        assert result.failed_count == 5
        assert result.succeeded_count == 343
        assert manifest["missing"] == sorted(config["missing_unit_ids"])
        record_units = {r["unit_id"] for r in records}
        assert record_units.isdisjoint(config["missing_unit_ids"])

    def test_no_trade_distinct_from_missing(self):
        # 무거래는 성공(분봉 없음이 사실)이고 missing 은 실패다 — 계획 §9 구분.
        # 개수 단언만으론 "no-trade unit 이 record 를 내고 정상 unit 이 누락"되는 상쇄를
        # 못 잡는다 — unit 단위로 확인한다
        config = scenario("price_no_trade.json")
        no_trade_units = set(config["no_trade_unit_ids"])
        result, records, manifest = FakePriceCollector(config, seed=1).collect(
            make_request(), NOW
        )
        assert result.status == DATA_VALID
        assert result.succeeded_count == 348
        assert result.failed_count == 0
        record_units = {r["unit_id"] for r in records}
        assert record_units == set(UNIVERSE.unit_ids) - no_trade_units
        assert manifest["no_trade"] == sorted(no_trade_units)

    def test_stale_bar_timestamp_outside_window(self):
        request = make_request()
        _, records, _ = FakePriceCollector(scenario("price_stale.json"), seed=1).collect(
            request, NOW
        )
        stale_units = set(scenario("price_stale.json")["stale_unit_ids"])
        stale_records = [r for r in records if r["unit_id"] in stale_units]
        assert len(stale_records) == 3
        assert all(r["ts"] < request.window_start for r in stale_records)

    def test_correction_changes_target_unit_only(self):
        request = make_request(uuid4(), uuid4())
        config = scenario("price_correction.json")
        target_units = set(config["correction"]["unit_ids"])
        delta = config["correction"]["close_delta"]
        original, original_records, _ = FakePriceCollector(
            scenario("price_normal.json"), seed=1
        ).collect(request, NOW)
        corrected, corrected_records, _ = FakePriceCollector(config, seed=1).collect(
            request, NOW
        )
        assert original.generation == 1
        assert corrected.generation == 2
        assert original.result_checksum != corrected.result_checksum
        # 지목한 unit 만 정확히 delta 만큼 바뀌고 나머지는 불변이어야 한다
        by_unit_before = {r["unit_id"]: r for r in original_records}
        for record in corrected_records:
            before = by_unit_before[record["unit_id"]]
            if record["unit_id"] in target_units:
                assert record["close"] == before["close"] + delta
            else:
                assert record == before

    def test_ohlc_invariants(self):
        _, records, _ = FakePriceCollector(scenario("price_normal.json"), seed=1).collect(
            make_request(), NOW
        )
        for r in records:
            assert r["high"] >= max(r["open"], r["close"])
            assert 0 < r["low"] <= min(r["open"], r["close"])


class TestNewsFeed:
    def test_pages_deterministic(self):
        feed = FakeNewsFeed(scenario("news_page_drift.json"), seed=7)
        assert feed.fetch_page(3, 1, 50) == feed.fetch_page(3, 1, 50)

    def test_page_drift_between_polls(self):
        # 새 기사가 앞에 끼면 같은 기사의 page 내 위치가 밀린다 — anchor 로만 frontier 판정 가능
        feed = FakeNewsFeed(scenario("news_page_drift.json"), seed=7)
        page_before = feed.fetch_page(0, 1, 50)
        page_after = feed.fetch_page(1, 1, 50)
        anchor = page_before[0]["NEWS_ID"]
        positions_after = [a["NEWS_ID"] for a in page_after]
        assert positions_after.index(anchor) == 7  # new_per_poll 만큼 밀림

    def test_duplicate_news_id_appears(self):
        config = scenario("news_duplicate.json")
        feed = FakeNewsFeed(config, seed=7)
        page = feed.fetch_page(config["duplicate"]["poll_index"], 1, 200)
        ids = [a["NEWS_ID"] for a in page]
        assert len(ids) == len(set(ids)) + 1  # 정확히 1건 중복 관측

    def test_duplicate_of_nonexistent_article_fails_loud(self):
        # 존재하지 않는 기사의 "중복"은 그냥 신규 기사라 dedupe 테스트가 무력화된다
        config = scenario("news_duplicate.json") | {
            "duplicate": {"poll_index": 0, "position": 0, "of_index": 100_000}
        }
        with pytest.raises(ValueError, match="발행분"):
            FakeNewsFeed(config, seed=7)

    def test_negative_poll_index_fails_loud(self):
        feed = FakeNewsFeed(scenario("news_page_drift.json"), seed=7)
        with pytest.raises(ValueError, match="poll_index"):
            feed.fetch_page(-1, 1, 50)

    def test_out_of_range_duplicate_position_fails_loud(self):
        # 범위 밖 position 을 feed 끝으로 접으면 페이지 예산 안에서 중복이 안 보인다
        config = scenario("news_duplicate.json") | {
            "duplicate": {"poll_index": 0, "position": 100_000, "of_index": 10}
        }
        with pytest.raises(ValueError, match="position"):
            FakeNewsFeed(config, seed=7)

    def test_late_correction_needs_preexisting_original(self):
        # 첫 등장부터 수정본이면 원본→수정본 lifecycle 이 성립하지 않는다
        config = {
            "initial_count": 0,
            "new_per_poll": 1,
            "late_correction": {"poll_index": 1, "article_index": 0},
        }
        with pytest.raises(ValueError, match="article_index"):
            FakeNewsFeed(config, seed=7)

    def test_duplicate_burst_poll_fails_loud(self):
        # 같은 poll 의 두 burst 는 dict 덮어쓰기로 조용히 하나가 된다 — anchor-miss
        # fixture 의 burst 가 줄어들면 실패 경로가 검증 없이 초록이 된다
        with pytest.raises(ValueError, match="poll_index=3"):
            FakeNewsFeed(
                {
                    "initial_count": 10,
                    "bursts": [
                        {"poll_index": 3, "count": 100},
                        {"poll_index": 3, "count": 50},
                    ],
                },
                seed=7,
            )

    def test_invalid_calendar_date_fails_loud(self):
        with pytest.raises(ValueError, match="달력일"):
            FakeNewsFeed({"initial_count": 1, "date_yyyymmdd": "20261399"}, seed=7)
        # strptime 은 미패딩("2026731")도 파싱한다 — 자릿수 강제까지 확인
        with pytest.raises(ValueError, match="8자리"):
            FakeNewsFeed({"initial_count": 1, "date_yyyymmdd": "2026731"}, seed=7)

    def test_anchor_miss_burst_exceeds_page_budget(self):
        # burst 가 MAX_PAGES×page_size 를 넘으면 anchor 에 못 닿는다 → INCOMPLETE 경로 입력
        config = scenario("news_anchor_miss.json")
        feed = FakeNewsFeed(config, seed=7)
        burst_poll = config["bursts"][0]["poll_index"]
        anchor = feed.fetch_page(burst_poll - 1, 1, 100)[0]["NEWS_ID"]
        max_pages, page_size = 4, 100
        fetched = {
            article["NEWS_ID"]
            for page in range(1, max_pages + 1)
            for article in feed.fetch_page(burst_poll, page, page_size)
        }
        assert anchor not in fetched

    def test_late_correction_same_id_new_content(self):
        config = scenario("news_late_correction.json")
        feed = FakeNewsFeed(config, seed=7)
        target = config["late_correction"]["article_index"]
        poll = config["late_correction"]["poll_index"]
        target_id = f"01100901.20260731{target:06d}"

        def find(poll_index):
            for page in range(1, 10):
                for article in feed.fetch_page(poll_index, page, 100):
                    if article["NEWS_ID"] == target_id:
                        return article
            raise AssertionError("대상 기사를 못 찾았다")

        before, after = find(poll - 1), find(poll)
        assert before["NEWS_ID"] == after["NEWS_ID"]
        assert before["PROVIDER_LINK_PAGE"] == after["PROVIDER_LINK_PAGE"]
        assert before["CONTENT"] != after["CONTENT"]

    def test_articles_compatible_with_existing_bigkinds_parser(self):
        # 계획 §10 은 기존 parser 재사용을 전제한다 — fake 기사가 `parse.bigkinds_date`
        # 를 통과하지 못하면 후속 PR 의 뉴스 lifecycle 테스트가 게이트에서 전부 차단된다
        feed = FakeNewsFeed(scenario("news_page_drift.json"), seed=7)
        article = feed.fetch_page(0, 1, 1)[0]
        assert bigkinds_date(article) == "2026-07-31"
        assert bigkinds_date({"NEWS_ID": article["NEWS_ID"]}) == "2026-07-31"  # DATE 없이도
        for field in ("TITLE", "CONTENT", "PROVIDER", "PROVIDER_LINK_PAGE"):
            assert article[field]
