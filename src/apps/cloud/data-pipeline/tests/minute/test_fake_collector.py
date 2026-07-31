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
        first, records_first = FakePriceCollector(scenario("price_normal.json"), seed=42).collect(
            request, NOW
        )
        second, records_second = FakePriceCollector(
            scenario("price_normal.json"), seed=42
        ).collect(request, NOW)
        assert first.result_checksum == second.result_checksum
        assert records_first == records_second

    def test_different_seed_different_checksum(self):
        request = make_request()
        first, _ = FakePriceCollector(scenario("price_normal.json"), seed=42).collect(request, NOW)
        second, _ = FakePriceCollector(scenario("price_normal.json"), seed=43).collect(request, NOW)
        assert first.result_checksum != second.result_checksum

    def test_clock_change_does_not_change_checksum(self):
        # 계획 §6: 현재 시각을 바꿔도 explicit window 결과는 동일해야 한다
        request = make_request(uuid4(), uuid4())
        collector = FakePriceCollector(scenario("price_normal.json"), seed=42)
        early, _ = collector.collect(request, NOW)
        late, _ = collector.collect(request, NOW + timedelta(hours=3))
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
        kst_result, kst_records = collector.collect(kst_request, NOW)
        utc_result, utc_records = collector.collect(utc_request, NOW)
        assert kst_result.result_checksum == utc_result.result_checksum
        assert [r["open"] for r in kst_records] == [r["open"] for r in utc_records]

    def test_scenario_typo_key_fails_loud(self):
        # fixture 키 오타가 조용히 no-op 시나리오가 되면 실패 경로 테스트가 무력화된다
        with pytest.raises(ValueError, match="미지 키"):
            FakePriceCollector({"scenario": "x", "missing_unit_idz": ["100003"]}, seed=1)
        with pytest.raises(ValueError, match="미지 키"):
            FakeNewsFeed({"scenario": "x", "initial_countt": 10}, seed=1)


class TestPriceScenarios:
    def test_normal_all_units_succeed(self):
        result, records = FakePriceCollector(scenario("price_normal.json"), seed=1).collect(
            make_request(), NOW
        )
        assert result.status == DATA_VALID
        assert result.expected_count == 348
        assert result.succeeded_count == 348
        assert result.failed_count == 0
        assert len(records) == 348

    def test_partial_missing_is_failure(self):
        result, records = FakePriceCollector(
            scenario("price_partial_missing.json"), seed=1
        ).collect(make_request(), NOW)
        assert result.status == DATA_INCOMPLETE
        assert result.failed_count == 5
        assert result.succeeded_count == 343
        assert len(records) == 343

    def test_no_trade_distinct_from_missing(self):
        # 무거래는 성공(분봉 없음이 사실)이고 missing 은 실패다 — 계획 §9 구분
        result, records = FakePriceCollector(scenario("price_no_trade.json"), seed=1).collect(
            make_request(), NOW
        )
        assert result.status == DATA_VALID
        assert result.succeeded_count == 348
        assert result.failed_count == 0
        assert len(records) == 348 - 7  # no-trade unit 은 record 가 없다

    def test_stale_bar_timestamp_outside_window(self):
        request = make_request()
        _, records = FakePriceCollector(scenario("price_stale.json"), seed=1).collect(request, NOW)
        stale_units = set(scenario("price_stale.json")["stale_unit_ids"])
        stale_records = [r for r in records if r["unit_id"] in stale_units]
        assert len(stale_records) == 3
        assert all(r["ts"] < request.window_start for r in stale_records)

    def test_correction_bumps_generation_and_checksum(self):
        request = make_request(uuid4(), uuid4())
        original, _ = FakePriceCollector(scenario("price_normal.json"), seed=1).collect(
            request, NOW
        )
        corrected, _ = FakePriceCollector(scenario("price_correction.json"), seed=1).collect(
            request, NOW
        )
        assert original.generation == 1
        assert corrected.generation == 2
        assert original.result_checksum != corrected.result_checksum


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
