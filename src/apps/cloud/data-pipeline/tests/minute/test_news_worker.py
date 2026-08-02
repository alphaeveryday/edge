"""News Worker loop 테스트 (ALPHA-669, 계획 §10 Canonical/job·테스트 해당분).

의도: 뉴스 유실은 조용하다 — 기사가 안 온 건지 안 본 건지 사후에 구분되지 않는다.
그래서 여기서 고정하는 건 "무엇이 job 이 되는가"의 권위다:

- 신규 판정은 **원장**이 한다 — anchor 뒤(위치로는 신규가 아닌) 재부상 기사도 job 이 된다.
- truncated poll 은 성공 anchor 를 덮지 않는다 — 못 따라잡은 구간이 다음 poll 의 목표로 남는다.
- 재관측은 job 을 만들지 않고(VALID_EMPTY, 빈 메시지 금지), 본문 정정은 만든다.
- raw page 는 판정 **전에** 저장된다 — 컨트롤러가 터져도 벤더 원본이 남는다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.fake_collector import FakeNewsFeed
from data_pipeline.minute.models import KST, plan_session_windows
from data_pipeline.minute.news_overlap import NewsSourceLedger
from data_pipeline.minute.news_worker import NewsWorker, NewsWorkerConfig
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.parse import news_article_id

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 9, 10, tzinfo=KST)  # 앞쪽 window 들이 전부 due


class RecordingCanonicalWriter:
    """canonical article upsert 경계 — 실 구현은 기존 (source_code, article_id) upsert."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    def upsert_tx(self, cur, *, dataset, window_start, records):
        for record in records:
            self.rows[(record["source_code"], record["article_id"])] = record
        return len(records)


def build_worker(db, tmp_path, *, scenario=None, feed=None, worker_id="w1",
                 windows=1, **config_overrides):
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    session_id, _ = ledger.plan_session(
        dataset="news_minute", source_group="bigkinds", session_date=SESSION_DATE,
        universe_version="news-univ-v1", universe_hash="h" * 64,
        windows=plan_session_windows(SESSION_DATE, universe=None)[:windows],
    )
    config = NewsWorkerConfig(
        worker_id=worker_id, dataset="news_minute", source_code="bigkinds",
        market="KR", session_date="2026-07-31", run_id="run_n",
        destination="news-extraction-realtime", tagger_version="v4-pro",
        ontology_version="onto-1",
        # tick 당 poll 1회로 고정 — anchor 진행을 tick 단위로 관측하기 위해
        **{"recovery_budget_per_tick": 0, **config_overrides},
    )
    worker = NewsWorker(
        session_id=session_id, ledger=ledger,
        news_ledger=NewsSourceLedger(db=_DB, connect_fn=db.connect),
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(root=tmp_path),
        feed=feed or FakeNewsFeed(scenario or {"scenario": "normal", "initial_count": 3}, seed=7),
        canonical_writer=RecordingCanonicalWriter(),
        config=config,
    )
    return worker, ledger, session_id


def statuses(db):
    return {
        w["window_start"].astimezone(KST).strftime("%H%M"): w["data_status"]
        for w in db.windows.values()
    }


class TestFirstPollAndJobs:
    def test_seed_poll_creates_job_per_article(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, session_id = build_worker(db, tmp_path)
        assert worker.tick(NOW) == "PROCESSED"
        assert statuses(db) == {"0900": "VALID"}
        assert len(db.jobs) == 3 and len(db.outbox) == 3
        assert len(worker.canonical_writer.rows) == 3
        # 성공 poll 이면 두 anchor 가 같다 = 따라잡을 구간 없음
        anchor = db.anchors[(session_id, "bigkinds")]
        assert anchor["success_anchor_ids"] == anchor["head_anchor_ids"] != []

    def test_job_identity_is_article_content_fingerprint(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path)
        worker.tick(NOW)
        job = next(iter(db.jobs.values()))
        assert job["source_code"] == "bigkinds"
        assert job["tagger_version"] == "v4-pro" and job["ontology_version"] == "onto-1"
        # input_fingerprint 는 추출 입력(제목·본문·발행시각) 해시다 — 노출 위치가
        # 바뀌었다고 재추출이 도는 것을 막는 값
        assert len(job["input_fingerprint"]) == 64
        event = db.outbox[f"NewsExtractionRequested:{job['job_id']}:0"]
        assert event["destination"] == "news-extraction-realtime"
        assert event["payload"]["article_id"] == job["article_id"]

    def test_raw_pages_stored_at_fetch_time(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path)
        worker.tick(NOW)
        pages = [k for k in worker.storage.list_keys("raw/") if k.endswith(".ndjson")]
        # page 1(기사 3건) + 빈 page 2(피드 끝 신호) 둘 다 원본으로 남는다
        assert len(pages) == 2
        assert all("/dataset=news_minute/" in k and "/attempt=1/" in k for k in pages)
        first = worker.storage.get_bytes(pages[0]).decode().splitlines()
        assert len(first) == 3 and json.loads(first[0])["NEWS_ID"]

    def test_poll_manifest_records_judgement(self, tmp_path):
        # EOD reconciliation 은 "무엇을 받았나"(raw)만으론 완전성을 못 따진다 —
        # 어느 anchor 를 목표로 몇 page 를 읽고 닿았는지가 판정 근거다
        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path)
        worker.tick(NOW)
        [key] = [k for k in worker.storage.list_keys("operations_archive/")]
        manifest = json.loads(worker.storage.get_bytes(key))
        assert manifest["truncated"] is False and manifest["reached_anchor"] is True
        assert manifest["pages_used"] == 2 and len(manifest["articles"]) == 3
        assert manifest["raw_page_keys"] == sorted(
            k for k in worker.storage.list_keys("raw/") if k.endswith(".ndjson")
        )
        window = next(iter(db.windows.values()))
        assert window["manifest_uri"] == key


class TestLedgerIsTheAuthority:
    def test_reobservation_creates_no_job(self, tmp_path):
        # 같은 NEWS_ID·같은 본문 재관측은 신규가 아니다 → VALID_EMPTY, 빈 메시지 금지
        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, windows=2)
        worker.tick(NOW)
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        assert sorted(statuses(db).values()) == ["VALID", "VALID_EMPTY"]
        assert len(db.jobs) == 3 and len(db.outbox) == 3

    def test_article_behind_anchor_still_becomes_job(self, tmp_path):
        # ⚠️ ALPHA-668 의 핵심 계약: 소스가 직전 head 를 상단에 재부상시키면 신규분이
        # anchor **뒤**에 온다. 위치(frontier)로 job 을 고르면 그 기사가 유실된다 —
        # 판정 권위는 관측 전량을 보는 원장이다.
        class ResurfacingFeed:
            """poll 1 에서 기존 기사(anchor)를 맨 위로 올리고 신규를 그 뒤에 둔다."""

            def __init__(self):
                self.poll = 0

            def fetch_page(self, poll_index, page, page_size):
                old = {"NEWS_ID": "01100901.20260731000000", "DATE": "20260731",
                       "TITLE": "기존", "CONTENT": "본문 A", "PROVIDER": "픽스처일보",
                       "PROVIDER_LINK_PAGE": "https://news.example/0"}
                fresh = {"NEWS_ID": "01100901.20260731000001", "DATE": "20260731",
                         "TITLE": "신규", "CONTENT": "본문 B", "PROVIDER": "픽스처일보",
                         "PROVIDER_LINK_PAGE": "https://news.example/1"}
                if page > 1:
                    return []
                return [old] if poll_index == 0 else [old, fresh]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=ResurfacingFeed(), windows=2)
        worker.tick(NOW)
        assert len(db.jobs) == 1
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        assert len(db.jobs) == 2, "anchor 뒤에 온 신규 기사가 job 이 되지 않았다"

    def test_duplicate_exposure_yields_one_job(self, tmp_path):
        # 같은 기사가 page 안에서 두 번 노출돼도(서버 dedup 실패·drift) job 은 하나
        db = FakeMinuteDB()
        worker, _, _ = build_worker(
            db, tmp_path,
            scenario={"scenario": "dup", "initial_count": 3,
                      "duplicate": {"poll_index": 0, "position": 1, "of_index": 0}},
        )
        worker.tick(NOW)
        assert len(db.jobs) == 3 and len(db.source_items) == 3

    def test_late_correction_creates_new_job(self, tmp_path):
        # 같은 URL·같은 NEWS_ID 인데 본문이 바뀌면 추출 입력이 달라진다 → 새 job
        db = FakeMinuteDB()
        worker, _, _ = build_worker(
            db, tmp_path, windows=2,
            scenario={"scenario": "corr", "initial_count": 3,
                      "late_correction": {"poll_index": 1, "article_index": 0}},
        )
        worker.tick(NOW)
        worker.tick(NOW + timedelta(seconds=1))
        assert len(db.jobs) == 4 and len(db.outbox) == 4
        corrected = [i for i in db.source_items.values() if i["generation"] == 2]
        assert len(corrected) == 1
        assert statuses(db)["0901"] == "VALID"  # 정정도 신규 job 이라 VALID


class TestTruncationAndRecovery:
    def test_truncated_poll_keeps_success_anchor_and_next_poll_catches_up(self, tmp_path):
        # page budget 을 넘긴 poll 은 INCOMPLETE 로 드러내고(성공 위장 금지) 성공
        # anchor 를 전진시키지 않는다 — 그 상태가 곧 recovery 예약이고, 다음 poll 이
        # recovery 예산으로 더 깊이 읽어 따라잡는다
        db = FakeMinuteDB()
        worker, _, session_id = build_worker(
            db, tmp_path, windows=2,
            scenario={"scenario": "burst", "initial_count": 5},
            page_size=2, max_pages=1, recovery_max_pages=8,
        )
        # realtime lane 은 **최신** due window 부터다 — 0901 → 0900 순으로 poll 한다
        assert worker.tick(NOW) == "PROCESSED"
        assert statuses(db)["0901"] == "INCOMPLETE"
        anchor = db.anchors[(session_id, "bigkinds")]
        assert anchor["success_anchor_ids"] == []          # 성공 anchor 미전진
        assert len(anchor["head_anchor_ids"]) == 2         # head 만 전진
        assert len(db.jobs) == 2                           # 읽은 만큼은 즉시 job

        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        assert statuses(db)["0900"] == "VALID"
        anchor = db.anchors[(session_id, "bigkinds")]
        assert anchor["success_anchor_ids"] == anchor["head_anchor_ids"]
        assert len(db.jobs) == 5, "따라잡기 poll 이 나머지 기사를 걷지 못했다"

    def test_burst_after_catchup_does_not_lose_earlier_frontier(self, tmp_path):
        # 따라잡은 뒤 burst 가 나면 성공 anchor 는 **직전 성공 지점**으로 남아야 한다 —
        # truncated poll 의 head 로 덮으면 그 사이 구간이 조회 범위 밖으로 사라진다
        db = FakeMinuteDB()
        worker, _, session_id = build_worker(
            db, tmp_path, windows=3,
            scenario={"scenario": "burst", "initial_count": 2,
                      "bursts": [{"poll_index": 1, "count": 8}]},
            page_size=2, max_pages=2, recovery_max_pages=8,
        )
        worker.tick(NOW)  # seed — 2건, page budget 안에서 끝 → 성공
        caught_up = db.anchors[(session_id, "bigkinds")]["success_anchor_ids"]
        worker.tick(NOW + timedelta(seconds=1))  # burst 8건 — 1 page 로는 anchor 미도달
        anchor = db.anchors[(session_id, "bigkinds")]
        assert anchor["success_anchor_ids"] == caught_up
        assert anchor["head_anchor_ids"] != caught_up
        worker.tick(NOW + timedelta(seconds=2))  # recovery 예산으로 따라잡기
        assert sorted(statuses(db).values()) == ["INCOMPLETE", "VALID", "VALID"]
        assert len(db.jobs) == 10  # 초기 2 + burst 8 전량


    def test_empty_response_does_not_erase_anchor(self, tmp_path):
        # 소스가 잠깐 빈 응답을 주면 frontier 에 대해 아무것도 증명되지 않는다 —
        # anchor 를 비우면 다음 poll 이 seed 로 되돌아가 예산을 통째로 태운다
        class BlinkingFeed:
            def __init__(self):
                self.rows = [
                    {"NEWS_ID": "01100901.20260731000000", "DATE": "20260731",
                     "TITLE": "t", "CONTENT": "c", "PROVIDER": "p",
                     "PROVIDER_LINK_PAGE": "https://news.example/0"}
                ]

            def fetch_page(self, poll_index, page, page_size):
                if poll_index == 1 or page > 1:
                    return []
                return list(self.rows)

        db = FakeMinuteDB()
        worker, _, session_id = build_worker(db, tmp_path, feed=BlinkingFeed(), windows=2)
        worker.tick(NOW)
        before = dict(db.anchors[(session_id, "bigkinds")])
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        after = db.anchors[(session_id, "bigkinds")]
        assert after["success_anchor_ids"] == before["success_anchor_ids"] != []
        assert after["head_anchor_ids"] == before["head_anchor_ids"]
        # 빈 응답은 anchor 연속성을 증명하지 못한다 → INCOMPLETE(성공 위장 금지).
        # 다만 anchor 를 지우지 않았으므로 다음 poll 은 seed 가 아니라 이어서 간다.
        assert statuses(db)["0900"] == "INCOMPLETE"


class TestQualityGate:
    def test_unanalyzable_article_is_observed_but_gets_no_job(self, tmp_path):
        # 제목 없는 기사는 배치 정제가 canonical 진입을 막는다(quality.validate_news_meta)
        # — realtime 경로가 그 게이트를 우회해 LLM job 을 만들면 안 된다. 관측은 남긴다:
        # 나중에 제목이 채워지면 content_changed 로 job 이 생겨야 하기 때문.
        def row(index, title):
            return {"NEWS_ID": f"01100901.2026073100000{index}", "DATE": "20260731",
                    "TITLE": title, "CONTENT": "c", "PROVIDER": "p",
                    "PROVIDER_LINK_PAGE": f"https://news.example/{index}"}

        class MixedQualityFeed:
            def fetch_page(self, poll_index, page, page_size):
                return [] if page > 1 else [row(0, "정상 기사"), row(1, "   ")]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=MixedQualityFeed())
        assert worker.tick(NOW) == "PROCESSED"
        assert len(db.source_items) == 2, "차단 기사도 관측 원장에는 남아야 한다"
        assert len(db.jobs) == 1 and len(db.outbox) == 1
        assert statuses(db) == {"0900": "VALID"}  # 정상 소수 — window 실패가 아니다
        [key] = worker.storage.list_keys("operations_archive/")
        manifest = json.loads(worker.storage.get_bytes(key))
        assert manifest["quality_blocked"] == [
            ["01100901.20260731000001", ["missing_title"]]
        ]

    def test_future_dated_article_still_gets_a_job(self, tmp_path):
        # 미래 발행일은 **판정 기준이 날마다 움직이는** 사유다(session_date 상대).
        # 이걸로 job 을 막으면, 내용이 그대로인 그 기사는 다음 세션에 조건이 풀려도
        # 재관측이 created/content_changed 를 안 내 영구히 추출되지 않는다. 그래서
        # 기록만 하고 job 은 만든다 — 봇 리뷰 P2 회귀 방지.
        class FutureDatedFeed:
            def fetch_page(self, poll_index, page, page_size):
                if page > 1:
                    return []
                return [{"NEWS_ID": "01100901.20260805000000", "DATE": "20260805",
                         "TITLE": "내일 기사", "CONTENT": "c", "PROVIDER": "p",
                         "PROVIDER_LINK_PAGE": "https://news.example/f"}]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=FutureDatedFeed())
        assert worker.tick(NOW) == "PROCESSED"
        assert len(db.jobs) == 1, "시각 상대 사유로 job 을 막으면 영구 누락이 된다"
        # 사유 자체는 기록에 남는다 — EOD 가 하루 단위로 판정할 입력이다
        [key] = worker.storage.list_keys("operations_archive/")
        manifest = json.loads(worker.storage.get_bytes(key))
        assert manifest["quality_blocked"] == [
            ["01100901.20260805000000", ["implausible_published_at"]]
        ]

    def test_titleless_article_does_not_stall_the_lane(self, tmp_path):
        # 제목 없는 행이 poll 판정 단계에서 터지면 그 행은 소스에 남아 있으므로 **매
        # poll 이 같은 자리에서 죽는다** — 뉴스 레인 전체가 영구히 멈춘다. 관측은 하되
        # job 만 막아야 한다(품질 게이트가 판정). 봇 리뷰 P2 회귀 방지.
        def row(index, **overrides):
            article = {"NEWS_ID": f"01100901.2026073100000{index}", "DATE": "20260731",
                       "TITLE": f"제목 {index}", "CONTENT": "c", "PROVIDER": "p",
                       "PROVIDER_LINK_PAGE": f"https://news.example/{index}"}
            article.update(overrides)
            return article

        class TitlelessFeed:
            def fetch_page(self, poll_index, page, page_size):
                if page > 1:
                    return []
                # 제목이 아예 없는 행(키 결측)과 None 인 행 — 둘 다 정상 기사와 섞여 온다
                titleless = row(1)
                del titleless["TITLE"]
                return [row(0), titleless, row(2, TITLE=None)]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=TitlelessFeed(), windows=2)
        assert worker.tick(NOW) == "PROCESSED", "제목 없는 행 하나가 poll 을 죽였다"
        assert len(db.source_items) == 3, "제목 없는 행도 관측 원장에는 남아야 한다"
        assert len(db.jobs) == 1  # 정상 기사만 추출 대상
        assert statuses(db)["0901"] == "VALID"
        # 다음 poll 도 같은 행을 다시 보지만 레인은 계속 흐른다
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        assert statuses(db)["0900"] == "VALID_EMPTY"

    def test_stale_observation_creates_neither_canonical_nor_job(self, tmp_path):
        # 순서가 뒤집혀 도착한 관측(원장이 더 최신을 보유)은 realtime 경로에서 아무것도
        # 만들지 않는다: 본문은 원장이 이미 거부했고, 우리가 든 행은 옛 텍스트라 canonical
        # 에 쓰면 최신본을 되돌리고, 안 쓰고 job 만 만들면 canonical 없는 article_id 의
        # 추출이 큐에 오른다. identity 승격만 원장에 남기고 정리는 EOD(PR 8) 소관이다.
        def row(url):
            article = {"NEWS_ID": "01100901.20260731000000", "DATE": "20260731",
                       "TITLE": "t", "CONTENT": "c", "PROVIDER": "p"}
            if url:
                article["PROVIDER_LINK_PAGE"] = url
            return article

        class LatePromotingFeed:
            def fetch_page(self, poll_index, page, page_size):
                if page > 1:
                    return []
                return [row(None if poll_index == 0 else "https://news.example/0")]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=LatePromotingFeed(), windows=2)
        worker.tick(NOW)
        # 원장이 더 최신 관측(다른 본문)을 이미 보유한 상태를 만든다
        item = db.source_items[("bigkinds", "01100901.20260731000000")]
        item["last_seen_at"] = NOW + timedelta(minutes=10)
        item["content_checksum"] = "f" * 64
        worker.canonical_writer.rows.clear()
        jobs_before = set(db.jobs)
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        assert set(db.jobs) == jobs_before, "canonical 없는 승격 ID 로 job 을 만들었다"
        assert not worker.canonical_writer.rows, "stale 본문이 canonical 을 되돌렸다"
        # 원장은 최신 본문을 지키고 identity 승격만 반영한다
        assert item["content_checksum"] == "f" * 64
        assert item["canonical_article_id"] == news_article_id(
            {"PROVIDER_LINK_PAGE": "https://news.example/0"}
        )


class TestFailureIsolation:
    def test_feed_failure_isolated_and_retried(self, tmp_path):
        db = FakeMinuteDB()

        class OnceExploding:
            def __init__(self):
                self.calls = 0

            def fetch_page(self, poll_index, page, page_size):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("BigKinds 500")
                return [] if page > 1 else [
                    {"NEWS_ID": "01100901.20260731000000", "DATE": "20260731",
                     "TITLE": "t", "CONTENT": "c", "PROVIDER": "p",
                     "PROVIDER_LINK_PAGE": "https://news.example/0"}
                ]

        worker, _, _ = build_worker(db, tmp_path, feed=OnceExploding())
        assert worker.tick(NOW) == "WINDOW_FAILED"  # 크게 기록, 루프는 산다
        assert statuses(db) == {"0900": "CLAIMED"} and not db.jobs
        assert worker.tick(NOW + timedelta(seconds=61)) == "PROCESSED"  # lease 만료 후 재청구
        assert statuses(db) == {"0900": "VALID"} and len(db.jobs) == 1

    def test_raw_page_survives_controller_rejection(self, tmp_path):
        # 판정이 터져도(형상 위반) 벤더 원본은 남아야 한다 — 보존이 판정보다 먼저다
        class MalformedFeed:
            def fetch_page(self, poll_index, page, page_size):
                return [{"NEWS_ID": "", "DATE": "20260731", "TITLE": "t"}]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=MalformedFeed())
        assert worker.tick(NOW) == "WINDOW_FAILED"
        assert [k for k in worker.storage.list_keys("raw/") if k.endswith(".ndjson")]

    def test_conflicting_identity_is_quarantined_not_lane_stopping(self, tmp_path):
        # 같은 NEWS_ID 에 다른 URL identity 가 붙는 계약 위반은 소스에 남아 **매 poll**
        # 재관측된다 — poll 전체를 실패시키면 그 한 건이 뉴스 레인을 영구히 막는다.
        # 한 건만 격리하고(INVALID + missing_units 로 조회 가능) 나머지는 계속 흐른다.
        def row(index, url):
            return {"NEWS_ID": f"01100901.2026073100000{index}", "DATE": "20260731",
                    "TITLE": f"t{index}", "CONTENT": f"c{index}", "PROVIDER": "p",
                    "PROVIDER_LINK_PAGE": url}

        class IdentityFlippingFeed:
            def fetch_page(self, poll_index, page, page_size):
                if page > 1:
                    return []
                if poll_index == 0:
                    return [row(0, "https://news.example/0")]
                # 0번은 URL 이 뒤집혀 충돌, 1번은 정상 신규
                return [row(0, "https://other.example/0"), row(1, "https://news.example/1")]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path, feed=IdentityFlippingFeed(), windows=3)
        worker.tick(NOW)
        assert len(db.jobs) == 1
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        window = db.windows[
            next(k for k, w in db.windows.items()
                 if w["window_start"].astimezone(KST).strftime("%H%M") == "0901")
        ]
        assert window["data_status"] == "INVALID"
        # missing_units 축은 unit(=source) 이다 — 기사 ID 를 넣으면 unit 집합과
        # 대조하는 QC 가 어긋난다. 기사 단위 상세는 로그·원장·raw 로 재현된다.
        assert window["missing_units"] == ["bigkinds"]
        # 충돌 행의 원장 매핑은 그대로다 — 격리가 identity 를 덮어쓰지 않는다
        assert db.source_items[("bigkinds", "01100901.20260731000000")][
            "canonical_article_id"
        ] == news_article_id({"PROVIDER_LINK_PAGE": "https://news.example/0"})
        assert len(db.jobs) == 2, "충돌 한 건이 같은 poll 의 정상 기사까지 막았다"
        # 레인은 살아 있다 — 다음 poll 도 돈다
        assert worker.tick(NOW + timedelta(seconds=2)) == "PROCESSED"

    def test_repoll_creates_no_duplicate_job_or_event(self, tmp_path):
        # 같은 window 재수집(EOD 지시·crash 후 재시도)은 기사 identity 로 접힌다 —
        # 세대는 poll 마다 오르지만 job/event 는 늘지 않는다
        db = FakeMinuteDB()
        worker, _, _ = build_worker(db, tmp_path)
        worker.tick(NOW)
        outbox_before = dict(db.outbox)
        window = next(iter(db.windows.values()))
        window["data_status"] = "DUE"
        assert worker.tick(NOW + timedelta(seconds=1)) == "PROCESSED"
        assert db.outbox == outbox_before and len(db.jobs) == 3
        assert window["data_status"] == "VALID_EMPTY"  # 신규 0건


class TestProductionDefaults:
    """다른 테스트는 tick 당 poll 1회로 고정하지만, 운영 기본값은 한 tick 에 두 lane 을
    돌려 **두 poll 이 같은 `now` 를 공유**한다 — 그 경로를 여기서 고정한다."""

    def test_two_lanes_in_one_tick_share_now_and_both_deliver(self, tmp_path):
        db = FakeMinuteDB()
        worker, _, _ = build_worker(
            db, tmp_path, windows=2, recovery_budget_per_tick=1,
            scenario={"scenario": "normal", "initial_count": 2, "new_per_poll": 1},
        )
        assert worker.tick(NOW) == "PROCESSED"
        assert sorted(statuses(db).values()) == ["VALID", "VALID"]
        # poll 0(기사 2건) + poll 1(신규 1건) — 같은 tick 안에서 둘 다 전달된다
        assert len(db.jobs) == 3

    def test_same_tick_content_conflict_retries_instead_of_quarantine(self, tmp_path):
        # 같은 tick 의 두 poll 이 같은 기사의 다른 본문을 보면 어느 쪽이 최신인지
        # 증명할 수 없다(ALPHA-668 이 fail loud 로 정한 지점). 이건 **일시** 충돌이라
        # 격리(INVALID 확정)가 아니라 재시도로 풀어야 한다 — 다음 tick 이 처리한다.
        class FlipFlopFeed:
            def fetch_page(self, poll_index, page, page_size):
                if page > 1:
                    return []
                return [{"NEWS_ID": "01100901.20260731000000", "DATE": "20260731",
                         "TITLE": "t", "CONTENT": f"본문 rev{poll_index}", "PROVIDER": "p",
                         "PROVIDER_LINK_PAGE": "https://news.example/0"}]

        db = FakeMinuteDB()
        worker, _, _ = build_worker(
            db, tmp_path, feed=FlipFlopFeed(), windows=2, recovery_budget_per_tick=1,
        )
        assert worker.tick(NOW) == "WINDOW_FAILED"
        assert statuses(db)["0900"] == "CLAIMED"  # 격리 아님 — 재청구 대상으로 남는다
        assert db.windows[
            next(k for k, w in db.windows.items()
                 if w["window_start"].astimezone(KST).strftime("%H%M") == "0901")
        ]["data_status"] == "VALID"
        # lease 만료 후 재청구 — 다른 now 라 정정으로 판정된다
        assert worker.tick(NOW + timedelta(seconds=61)) == "PROCESSED"
        assert statuses(db)["0900"] == "VALID" and len(db.jobs) == 2

    def test_shallow_recovery_budget_is_rejected(self, tmp_path):
        # 따라잡기 poll 이 평상시보다 얕으면 성공 anchor 에 영영 못 닿아 lag 이
        # 영구화된다 — 설정 시점에 드러낸다
        db = FakeMinuteDB()
        with pytest.raises(ValueError, match="recovery_max_pages"):
            build_worker(db, tmp_path, max_pages=4, recovery_max_pages=2)


class TestLoopLifecycle:
    def test_restart_recovers_backlog_with_new_fence(self, tmp_path):
        # 상주 프로세스 교체 — 밀린 window 는 새 fence 로 이어서 처리된다
        db = FakeMinuteDB()
        first, _, session_id = build_worker(db, tmp_path, windows=3)
        first.tick(NOW)
        later = NOW + timedelta(seconds=301)  # session lease 만료
        replacement, _, _ = build_worker(
            db, tmp_path, worker_id="w2", windows=3, recovery_budget_per_tick=2,
        )
        replacement.session_id = session_id
        assert replacement.tick(later) == "PROCESSED"
        assert set(statuses(db).values()) <= {"VALID", "VALID_EMPTY"}

    def test_drain_converges(self, tmp_path):
        db = FakeMinuteDB()
        worker, ledger, session_id = build_worker(db, tmp_path)
        worker.tick(NOW)
        ledger.request_drain(session_id=session_id, now=NOW)
        assert worker.tick(NOW + timedelta(seconds=30)) == "DRAINING"
        assert db.sessions[session_id]["phase"] == "DRAINED"
