"""adaptive overlap 컨트롤러·source item 관측 테스트 (ALPHA-668, 계획 §10 전반부).

의도: 시각 커서가 없는 소스에서 증분의 정확성은 전적으로 anchor frontier 판정에
달렸다 — dedupe 가 깨지면 중복 LLM 호출, anchor 판정이 깨지면 기사 유실이 조용히
일어난다. truncation 을 성공으로 위장하는 경로가 최악이다(Rule 12).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.minute.fake_collector import FakeNewsFeed
from data_pipeline.minute.models import KST
from data_pipeline.minute.news_overlap import (
    NewsPage,
    NewsSourceLedger,
    article_content_checksum,
    poll_new_articles,
)
from data_pipeline.parse import news_article_id

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

    def test_partial_page_does_not_hide_later_pages(self):
        # BigKinds 는 soft cap 때문에 짧은 page 뒤에도 다음 page 가 있다. 빈 page 외의
        # 길이로 끝을 추측하면 뒤의 신규 기사와 anchor를 success로 유실한다.
        class SoftCappedFeed:
            pages = {
                1: [{"NEWS_ID": "new-2", "TITLE": "n2", "CONTENT": "c2"}],
                2: [
                    {"NEWS_ID": "new-1", "TITLE": "n1", "CONTENT": "c1"},
                    {"NEWS_ID": "anchor", "TITLE": "a", "CONTENT": "ca"},
                ],
            }

            def fetch_page(self, poll_index, page, page_size):
                return self.pages.get(page, [])

        outcome = poll_new_articles(
            SoftCappedFeed(), poll_index=1, anchor_ids=frozenset({"anchor"}),
            max_pages=3, page_size=2,
        )
        assert [row["NEWS_ID"] for row in outcome.new_articles] == ["new-2", "new-1"]
        assert outcome.reached_anchor is True and outcome.truncated is False

    def test_explicit_non_empty_last_page_completes_seed(self):
        # BigKinds isLimitPage는 non-empty 마지막 page에도 온다. rows만 전달해 이
        # 신호를 잃으면 max_pages=1의 완전한 seed가 영구 INCOMPLETE가 된다.
        class ExplicitLastPageFeed:
            def fetch_page(self, poll_index, page, page_size):
                return NewsPage(
                    rows=({"NEWS_ID": "only", "TITLE": "t", "CONTENT": "c"},),
                    is_last=True,
                )

        outcome = poll_new_articles(
            ExplicitLastPageFeed(), poll_index=0, anchor_ids=frozenset(),
            max_pages=1, page_size=100,
        )
        assert [row["NEWS_ID"] for row in outcome.new_articles] == ["only"]
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
        assert len(ids) == len(set(ids)) == 3  # 중복만 제거하고 정상 신규 3건은 보존

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
        # realtime은 이 head로 전진하고, 이전 성공 anchor는 recovery용으로 보존한다.
        assert outcome.next_anchor_ids[0] == feed.fetch_page(1, 1, 1)[0]["NEWS_ID"]

    def test_vanished_anchor_at_feed_end_is_incomplete(self):
        # feed 끝이어도 직전 성공 anchor를 못 봤으면 연속성을 증명하지 못했다.
        # 성공으로 접지 않아 recovery/EOD가 보존기간 밖 누락을 진단하게 한다.
        feed = FakeNewsFeed({"scenario": "s", "initial_count": 20}, seed=7)
        outcome = poll_new_articles(
            feed, poll_index=0, anchor_ids=frozenset({"01100901.20260731999998"}),
            max_pages=4, page_size=50,
        )
        assert outcome.reached_anchor is False and outcome.truncated is True
        assert len(outcome.new_articles) == 20

    def test_page_one_is_not_refetched_for_next_anchor(self):
        # live feed는 호출 사이 새 기사가 끼어든다. page 1을 다시 읽어 미처리 ID를
        # next anchor로 저장하면 다음 poll도 그 ID에서 멈춰 영구 유실된다.
        class MovingFeed:
            calls = 0

            def fetch_page(self, poll_index, page, page_size):
                assert page == 1
                self.calls += 1
                if self.calls > 1:
                    return [
                        {"NEWS_ID": "late", "TITLE": "l", "CONTENT": "cl"},
                        {"NEWS_ID": "new", "TITLE": "n", "CONTENT": "cn"},
                    ]
                return [
                    {"NEWS_ID": "new", "TITLE": "n", "CONTENT": "cn"},
                    {"NEWS_ID": "anchor", "TITLE": "a", "CONTENT": "ca"},
                ]

        feed = MovingFeed()
        outcome = poll_new_articles(
            feed, poll_index=1, anchor_ids=frozenset({"anchor"}),
            max_pages=1, page_size=2,
        )
        assert feed.calls == 1
        assert outcome.next_anchor_ids == ("new", "anchor")

    def test_anchor_page_rows_are_observed_for_late_correction(self):
        # 신규 frontier는 첫 anchor에서 끝나도 이미 받은 page 전체를 원장에 관측해야
        # anchor overlap 안 기존 기사의 본문 정정을 발견할 수 있다.
        feed = FakeNewsFeed(
            {"scenario": "corr", "initial_count": 30,
             "late_correction": {"poll_index": 1, "article_index": 10}},
            seed=7,
        )
        anchors = anchors_from(feed, 0)
        outcome = poll_new_articles(
            feed, poll_index=1, anchor_ids=anchors, max_pages=2, page_size=20,
        )
        observed = {row["NEWS_ID"]: row for row in outcome.observed_articles}
        target = "01100901.20260731000010"
        before = next(
            row for row in feed.fetch_page(0, 1, 20) if row["NEWS_ID"] == target
        )
        assert outcome.new_articles == ()
        assert article_content_checksum(observed[target]) != article_content_checksum(before)

    def test_conflicting_duplicate_payload_fails_loud(self):
        # 같은 NEWS_ID의 상충 row를 first-only로 접으면 본문 정정이나 URL identity
        # 변경이 원장에 도달하지 않는다. 어느 쪽이 최신인지 증거가 없어 실패가 안전하다.
        class ConflictingDuplicateFeed:
            def fetch_page(self, poll_index, page, page_size):
                return NewsPage(
                    rows=(
                        {"NEWS_ID": "same", "TITLE": "t", "CONTENT": "old"},
                        {"NEWS_ID": "same", "TITLE": "t", "CONTENT": "new"},
                    ),
                    is_last=True,
                )

        with pytest.raises(ValueError, match="payload.*충돌"):
            poll_new_articles(
                ConflictingDuplicateFeed(), poll_index=1, anchor_ids=frozenset(),
                max_pages=1, page_size=100,
            )

    def test_equivalent_canonical_urls_do_not_conflict(self):
        # raw URL 표기가 달라도 정본 normalize_url/news_article_id가 같은 identity면
        # duplicate 충돌이 아니다.
        class EquivalentUrlFeed:
            def fetch_page(self, poll_index, page, page_size):
                return NewsPage(
                    rows=(
                        {"NEWS_ID": "same", "TITLE": "t", "CONTENT": "c",
                         "PROVIDER_LINK_PAGE": "https://NEWS.example/a/?utm_source=x"},
                        {"NEWS_ID": "same", "TITLE": "t", "CONTENT": "c",
                         "PROVIDER_LINK_PAGE": "https://news.example/a"},
                    ),
                    is_last=True,
                )

        outcome = poll_new_articles(
            EquivalentUrlFeed(), poll_index=0, anchor_ids=frozenset(),
            max_pages=1, page_size=100,
        )
        assert [row["NEWS_ID"] for row in outcome.observed_articles] == ["same"]

    @pytest.mark.parametrize("bad_id", [None, "", "   ", 123])
    def test_missing_or_malformed_news_id_fails_loud(self, bad_id):
        class BrokenFeed:
            def fetch_page(self, poll_index, page, page_size):
                return [{"NEWS_ID": bad_id, "TITLE": "형상 붕괴"}]

        with pytest.raises(ValueError, match="NEWS_ID"):
            poll_new_articles(
                BrokenFeed(), poll_index=0, anchor_ids=frozenset(),
                max_pages=1, page_size=10,
            )


class TestContentChecksum:
    def test_position_change_does_not_change_checksum(self):
        feed = drift_feed()
        first = feed.fetch_page(0, 1, 5)[-1]
        # 같은 내용에 위치/provenance만 바꿔 실제 반례를 만든다. 자기 자신과 비교하면
        # checksum이 전체 row를 해시하도록 회귀해도 이 테스트가 무조건 통과한다.
        drifted = {**first, "page": 2, "fetched_at": "later"}
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

    def test_published_date_correction_changes_checksum(self):
        article = {"NEWS_ID": "same", "TITLE": "제목", "CONTENT": "본문",
                   "DATE": "20260731"}
        corrected = {**article, "DATE": "20260801"}
        assert article_content_checksum(article) != article_content_checksum(corrected)

    def test_missing_content_is_distinct_from_literal_none(self):
        base = {"NEWS_ID": "same", "TITLE": "제목", "DATE": "20260731"}
        assert article_content_checksum(base) != article_content_checksum(
            {**base, "CONTENT": "None"}
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [("TITLE", None), ("TITLE", 1), ("TITLE", []), ("TITLE", {}),
         ("CONTENT", 1), ("CONTENT", []), ("CONTENT", {})],
    )
    def test_invalid_content_field_types_fail_loud(self, field, value):
        # f-string 강제는 None과 "None" 같은 다른 입력을 같은 checksum으로 접어
        # content correction을 숨긴다.
        article = {"TITLE": "제목", "CONTENT": "본문"}
        article[field] = value
        with pytest.raises(ValueError, match=field):
            article_content_checksum(article)


class TestSourceLedger:
    def _observe(
        self, ledger, *, checksum="a" * 64, item="01100901.20260731000001",
        canonical_article_id=None, canonical_id_from_url=False, now=NOW,
    ):
        if canonical_article_id is None:
            canonical_article_id = news_article_id({"NEWS_ID": item})
        return ledger.observe(
            source_code="bigkinds", source_item_id=item,
            canonical_article_id=canonical_article_id,
            canonical_id_from_url=canonical_id_from_url,
            content_checksum=checksum, now=now,
        )

    def test_new_then_reobserve_then_correction(self):
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        fallback_id = news_article_id({"NEWS_ID": "01100901.20260731000001"})
        first = self._observe(ledger)
        assert first == {
            "created": True, "content_changed": False, "canonical_changed": False,
            "stale": False, "generation": 1, "canonical_article_id": fallback_id,
        }
        again = self._observe(
            ledger, now=NOW + timedelta(seconds=1),
        )  # 같은 NEWS_ID 재관측 — 신규 아님
        assert again == {
            "created": False, "content_changed": False, "canonical_changed": False,
            "stale": False, "generation": 1, "canonical_article_id": fallback_id,
        }
        corrected = self._observe(
            ledger, checksum="b" * 64, now=NOW + timedelta(seconds=2),
        )  # 본문 수정
        assert corrected == {
            "created": False, "content_changed": True, "canonical_changed": False,
            "stale": False, "generation": 2, "canonical_article_id": fallback_id,
        }
        row = db.source_items[("bigkinds", "01100901.20260731000001")]
        assert row["content_checksum"] == "b" * 64 and row["generation"] == 2

    def test_same_time_conflicting_content_fails_loud(self):
        # 두 lane이 같은 tick now를 공유할 때 상충 payload를 선착순으로 고르면 결과가
        # lock 순서에 의존한다. 최신을 증명할 수 없으므로 충돌을 드러낸다.
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        self._observe(ledger, item="item", checksum="b" * 64)
        with pytest.raises(ValueError, match="같은 관측 시각"):
            self._observe(ledger, item="item", checksum="a" * 64)
        assert db.source_items[("bigkinds", "item")]["content_checksum"] == "b" * 64

    def test_reobserve_updates_canonical_identity(self):
        # URL이 뒤늦게 생기면 canonical parser가 NEWS_ID fallback 대신 URL identity를
        # 만든다. source mapping과 새 article job 판단이 그 변경을 볼 수 있어야 한다.
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        self._observe(ledger)
        changed = self._observe(
            ledger, canonical_article_id="art_url", canonical_id_from_url=True,
            now=NOW + timedelta(seconds=1),
        )
        row = db.source_items[("bigkinds", "01100901.20260731000001")]
        assert changed["canonical_changed"] is True
        assert changed["content_changed"] is False
        assert row["canonical_article_id"] == "art_url"

        fallback = self._observe(
            ledger, canonical_id_from_url=False, now=NOW + timedelta(seconds=2),
        )
        assert fallback["canonical_changed"] is False
        assert fallback["canonical_article_id"] == "art_url"
        assert row["canonical_article_id"] == "art_url"

    def test_older_same_content_can_upgrade_fallback_to_url(self):
        # 오래된 recovery라도 content가 최신 원장과 같고 URL identity만 더 강하면
        # checksum/last_seen은 보존하면서 fallback mapping을 단방향 승격할 수 있다.
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        self._observe(ledger, item="item", now=NOW + timedelta(seconds=1))
        upgraded = self._observe(
            ledger, item="item", canonical_article_id="art_url",
            canonical_id_from_url=True, now=NOW,
        )
        row = db.source_items[("bigkinds", "item")]
        assert upgraded["stale"] is True and upgraded["canonical_changed"] is True
        assert row["canonical_article_id"] == "art_url"
        assert row["last_seen_at"] == NOW + timedelta(seconds=1)

    def test_observe_tx_can_join_worker_commit_transaction(self):
        # source 관측이 먼저 독립 commit되면 그 직후 crash에서 재시도가 created=False로
        # job enqueue를 건너뛴다. 5-2 Worker가 같은 cursor에 조합할 tx 조각을 고정한다.
        db = FakeMinuteDB()
        before = db.connect_calls
        with db.connect(_DB) as conn, conn.cursor() as cur:
            fallback_id = news_article_id({"NEWS_ID": "item"})
            result = NewsSourceLedger._observe_tx(
                cur, source_code="bigkinds", source_item_id="item",
                canonical_article_id=fallback_id, canonical_id_from_url=False,
                content_checksum="a" * 64, now=NOW,
            )
        assert db.connect_calls == before + 1
        assert result["created"] is True

    def test_stale_observation_still_reports_url_conflict(self):
        # 늦게 도착한 관측이라도 같은 NEWS_ID 에 다른 URL identity 가 붙은 사실은
        # 유효하다 — stale 로 먼저 접으면 별도 canonical/job 가능성이 사라진다
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        item = "01100901.20260731000001"
        url_a = news_article_id({"PROVIDER_LINK_PAGE": "https://news.example/a"})
        url_b = news_article_id({"PROVIDER_LINK_PAGE": "https://news.example/b"})
        self._observe(ledger, item=item)  # fallback identity 로 최초 관측
        self._observe(
            ledger, item=item, canonical_article_id=url_a, canonical_id_from_url=True,
            now=NOW + timedelta(seconds=5),
        )  # URL identity 승격
        with pytest.raises(ValueError, match="URL canonical identity"):
            self._observe(
                ledger, item=item, canonical_article_id=url_b,
                canonical_id_from_url=True, now=NOW,  # 지연 도착(stale)
            )

    def test_naive_now_rejected(self):
        # TIMESTAMPTZ 는 naive 를 서버 tz 로 접어 저장한다 — 다음 stale 비교가 터진다
        db = FakeMinuteDB()
        ledger = NewsSourceLedger(db=_DB, connect_fn=db.connect)
        with pytest.raises(ValueError, match="timezone-aware"):
            self._observe(ledger, now=datetime(2026, 7, 31, 9, 5))


class TestAnchorValidation:
    def test_corrupt_anchor_fails_loud(self):
        # 손상된 커서는 어떤 ID 와도 안 맞아 매 poll 이 truncated 로 끝난다 —
        # 원인이 안 드러난 채 recovery 만 영구 반복되는 것을 막는다
        feed = drift_feed()
        for corrupt in (frozenset({""}), frozenset({" "}), frozenset({" id"})):
            with pytest.raises(ValueError, match="anchor_ids"):
                poll_new_articles(
                    feed, poll_index=1, anchor_ids=corrupt, max_pages=2, page_size=50,
                )
