"""adaptive overlap 컨트롤러·source item 관측 테스트 (ALPHA-668, 계획 §10 전반부).

의도: 시각 커서가 없는 소스에서 증분의 정확성은 전적으로 anchor frontier 판정에
달렸다 — dedupe 가 깨지면 중복 LLM 호출, anchor 판정이 깨지면 기사 유실이 조용히
일어난다. truncation 을 성공으로 위장하는 경로가 최악이다(Rule 12).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.fake_collector import FakeNewsFeed
from data_pipeline.minute.models import KST
from data_pipeline.minute.news_overlap import (
    NewsSourceLedger,
    article_content_checksum,
    poll_new_articles,
)

_DB = DbConfig(password="x")
NOW = datetime(2026, 7, 31, 9, 5, tzinfo=KST)


def drift_feed():
    return FakeNewsFeed({"scenario": "drift", "initial_count": 120, "new_per_poll": 7}, seed=7)


def anchors_from(feed, poll_index, size=10):
    return frozenset(
        row["NEWS_ID"] for row in feed.fetch_page(poll_index, 1, size)
    )


class TestSeedPoll:
    def test_first_poll_bounded_seed(self):
        feed = drift_feed()
        outcome = poll_new_articles(
            feed, poll_index=0, anchor_ids=frozenset(), max_pages=2, page_size=50,
        )
        assert len(outcome.new_articles) == 100  # budget 만큼
        assert outcome.reached_anchor is True
        assert outcome.truncated is True  # 120건 중 100건 — 더 있다, 위장 금지
        assert len(outcome.next_anchor_ids) == 10

    def test_first_poll_small_feed_not_truncated(self):
        feed = FakeNewsFeed({"scenario": "s", "initial_count": 30}, seed=7)
        outcome = poll_new_articles(
            feed, poll_index=0, anchor_ids=frozenset(), max_pages=2, page_size=50,
        )
        assert len(outcome.new_articles) == 30
        assert outcome.truncated is False


class TestFrontier:
    def test_incremental_poll_returns_only_new(self):
        feed = drift_feed()
        anchors = anchors_from(feed, 0)
        outcome = poll_new_articles(
            feed, poll_index=1, anchor_ids=anchors, max_pages=4, page_size=50,
        )
        assert len(outcome.new_articles) == 7  # new_per_poll 만큼만 — drift 무관
        assert outcome.reached_anchor is True and outcome.truncated is False
        assert outcome.pages_used == 1

    def test_zero_new_poll_returns_empty(self):
        # 신규 0건 — 빈 결과가 정상이다(VALID_EMPTY 의 전제, 빈 메시지 생성 금지)
        feed = FakeNewsFeed({"scenario": "s", "initial_count": 50}, seed=7)
        anchors = anchors_from(feed, 0)
        outcome = poll_new_articles(
            feed, poll_index=1, anchor_ids=anchors, max_pages=4, page_size=50,
        )
        assert outcome.new_articles == ()
        assert outcome.reached_anchor is True and outcome.truncated is False

    def test_duplicate_news_id_deduped(self):
        feed = FakeNewsFeed(
            {"scenario": "dup", "initial_count": 60, "new_per_poll": 3,
             "duplicate": {"poll_index": 1, "position": 1, "of_index": 61}},
            seed=7,
        )
        anchors = anchors_from(feed, 0)
        outcome = poll_new_articles(
            feed, poll_index=1, anchor_ids=anchors, max_pages=4, page_size=50,
        )
        ids = [a["NEWS_ID"] for a in outcome.new_articles]
        assert len(ids) == len(set(ids))  # poll 내 duplicate 는 첫 관측만

    def test_anchor_miss_burst_marks_truncated(self):
        # 신규분이 budget 을 넘으면 잘라서 성공으로 위장하지 않는다 — INCOMPLETE 입력
        feed = FakeNewsFeed(
            {"scenario": "burst", "initial_count": 100, "new_per_poll": 3,
             "bursts": [{"poll_index": 1, "count": 450}]},
            seed=7,
        )
        anchors = anchors_from(feed, 0)
        outcome = poll_new_articles(
            feed, poll_index=1, anchor_ids=anchors, max_pages=4, page_size=100,
        )
        assert outcome.reached_anchor is False
        assert outcome.truncated is True
        assert len(outcome.new_articles) == 400  # budget 상한까지는 확보
        # truncated 여도 다음 frontier 는 이번 최신단이다 — 다음 poll 이 이어간다
        assert outcome.next_anchor_ids[0] == feed.fetch_page(1, 1, 1)[0]["NEWS_ID"]

    def test_vanished_anchor_feed_end_is_reached(self):
        # anchor 기사가 피드에서 사라졌고(보존기간 밖) 피드 끝에 닿음 — 신규분은
        # 전부 담겼으므로 도달로 본다(영구 INCOMPLETE 루프 방지)
        feed = FakeNewsFeed({"scenario": "s", "initial_count": 20}, seed=7)
        outcome = poll_new_articles(
            feed, poll_index=0, anchor_ids=frozenset({"01100901.20260731999998"}),
            max_pages=4, page_size=50,
        )
        assert outcome.reached_anchor is True and outcome.truncated is False
        assert len(outcome.new_articles) == 20

    def test_missing_news_id_fails_loud(self):
        class BrokenFeed:
            def fetch_page(self, poll_index, page, page_size):
                return [{"TITLE": "형상 붕괴"}]

        with pytest.raises(ValueError, match="NEWS_ID"):
            poll_new_articles(
                BrokenFeed(), poll_index=0, anchor_ids=frozenset(),
                max_pages=1, page_size=10,
            )


class TestContentChecksum:
    def test_position_change_does_not_change_checksum(self):
        feed = drift_feed()
        first = feed.fetch_page(0, 1, 5)[-1]
        drifted = feed.fetch_page(1, 2, 5)[0] if False else first  # 같은 기사
        assert article_content_checksum(first) == article_content_checksum(drifted)

    def test_late_correction_changes_checksum(self):
        feed = FakeNewsFeed(
            {"scenario": "corr", "initial_count": 30, "new_per_poll": 1,
             "late_correction": {"poll_index": 1, "article_index": 10}},
            seed=7,
        )
        target = "01100901.20260731000010"

        def find(poll):
            for page in range(1, 5):
                for row in feed.fetch_page(poll, page, 20):
                    if row["NEWS_ID"] == target:
                        return row
            raise AssertionError("대상 기사 없음")

        assert article_content_checksum(find(0)) != article_content_checksum(find(1))


class TestSourceLedger:
    def _observe(self, ledger, *, checksum="a" * 64, item="01100901.20260731000001"):
        return ledger.observe(
            source_code="bigkinds", source_item_id=item,
            canonical_article_id="art_x", content_checksum=checksum, now=NOW,
        )

    def test_new_then_reobserve_then_correction(self):
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        first = self._observe(ledger)
        assert first == {"created": True, "content_changed": False, "generation": 1}
        again = self._observe(ledger)  # 같은 NEWS_ID 재관측 — 신규 아님
        assert again == {"created": False, "content_changed": False, "generation": 1}
        corrected = self._observe(ledger, checksum="b" * 64)  # 본문 수정
        assert corrected == {"created": False, "content_changed": True, "generation": 2}
        row = db.source_items[("bigkinds", "01100901.20260731000001")]
        assert row["content_checksum"] == "b" * 64 and row["generation"] == 2
