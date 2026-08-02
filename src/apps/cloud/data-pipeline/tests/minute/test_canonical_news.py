"""1분 뉴스 canonical writer 테스트 (ALPHA-691).

의도: **정정이 도착하지 않으면 그 뒤는 전부 거짓말이 된다.** 원장은 새 지문(fp2)을
처리했다고 말하는데 Consumer 가 읽은 건 옛 본문이고, 그 기사는 재관측 변화가 없는 한 새 job
도 안 생겨 정정이 영영 태깅되지 않는다(봇 P1). 여기서 고정하는 건 넷이다.

- **정정이 실제로 덮인다** — 제목·발행시각·리드 전부. 배치 loader 의 DO NOTHING 회귀가
  들어오면 깨져야 한다.
- **리드가 비어도 쓴다** — 배치는 `if lead` 로 감싸 리드가 빠진 정정이 옛 값을 남긴다.
- **같은 본문 재관측은 UPDATE 를 내지 않는다**(멱등 집계가 거짓이 되지 않게).
- **article_id 는 원장이 준 값이 이긴다** — `_normalize` 의 재계산값을 쓰면 job 을 만든 id 와
  canonical 행의 id 가 갈린다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.db import stable_domain_id
from data_pipeline.minute.canonical_news import PgNewsCanonicalWriter
from data_pipeline.minute.models import KST

SOURCE = "bigkinds"
ARTICLE_ID = "art-0001"
WINDOW_START = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
OBSERVED = datetime(2026, 7, 31, 9, 0, 30, tzinfo=KST)   # 그 창을 실제로 처리한 시각


def vendor_row(**overrides) -> dict:
    """BigKinds raw 행 형상(fake_collector·bigkinds-raw-fields 실측과 같은 축)."""
    row = {
        "NEWS_ID": "01100101.20260731000001",
        "TITLE": "삼성전자,  테슬라와   공급계약",   # 공백 정규화 대상
        "CONTENT": "삼성전자가 테슬라에 칩을 공급한다.",
        "PROVIDER": "픽스처일보",
        "PROVIDER_LINK_PAGE": "https://news.example/1",
        "DATE": "20260731",
        "source_code": SOURCE,
        "article_id": ARTICLE_ID,
    }
    row.update(overrides)
    return row


def write(db, *records, window_start=WINDOW_START, observed_at=None):
    with db.connect(None) as conn, conn.cursor() as cur:
        return PgNewsCanonicalWriter(clock=lambda: observed_at or OBSERVED).upsert_tx(
            cur, dataset="news_minute", window_start=window_start, records=tuple(records)
        )


class TestCorrection:
    def test_changed_body_overwrites_the_stored_row(self):
        # 이 모듈의 존재 이유 — 배치 loader 의 DO NOTHING 을 그대로 쓰면 여기서 깨진다
        db = FakeMinuteDB()
        write(db, vendor_row())
        write(db, vendor_row(
            TITLE="삼성전자, 테슬라와 공급계약 정정",
            CONTENT="공급 규모가 3조원으로 정정됐다.",
        ))

        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert document["title"] == "삼성전자, 테슬라와 공급계약 정정"
        news = db.news_documents[document["document_id"]]
        assert news["lead_text"] == "공급 규모가 3조원으로 정정됐다."

    def test_lead_removed_by_correction_is_cleared(self):
        # 배치는 `if lead` 로 감싸 이 경우 옛 리드가 남는다 — Consumer 는 사라진 문장을
        # 근거로 태깅하게 된다
        db = FakeMinuteDB()
        write(db, vendor_row())
        write(db, vendor_row(CONTENT=None))

        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert db.news_documents[document["document_id"]]["lead_text"] is None

    def test_arrival_time_is_when_we_observed_not_when_the_window_was_due(self):
        # ⚠️ recovery 는 09:00 창을 12:00 에 처리한다. window_start 를 쓰면 12:00 에 알게 된
        # 기사를 09:00 에 안 것으로 **소급**하고, 이 값을 PIT 시각으로 복사하는 하류
        # (load_assertions·assemble_events)에서 10:00 as-of 조회가 미래 지식을 보게 된다.
        db = FakeMinuteDB()
        observed = datetime(2026, 7, 31, 12, 0, tzinfo=KST)
        with db.connect(None) as conn, conn.cursor() as cur:
            PgNewsCanonicalWriter(clock=lambda: observed).upsert_tx(
                cur, dataset="news_minute", window_start=WINDOW_START,
                records=(vendor_row(),),
            )
        assert db.documents[(SOURCE, ARTICLE_ID)]["available_at"] == observed

    def test_arrival_time_follows_the_correction(self):
        # ⚠️ 동결하면 내용은 T2 인데 시각은 T1 이라, 이 값을 PIT 축으로 복사하는 하류에서
        # 10:00 as-of 조회가 12:00 에야 알려진 내용을 본다. 시간순 피드에 그 문서가 다시
        # 뜨는 건 정정에서는 바람직한 동작이다(원장이 새 job 을 만드는 것과 같은 이유).
        db = FakeMinuteDB()
        write(db, vendor_row())
        corrected_at = OBSERVED + timedelta(hours=3)

        write(db, vendor_row(TITLE="정정된 제목"), observed_at=corrected_at)

        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert (document["title"], document["available_at"]) == ("정정된 제목", corrected_at)

    def test_stale_observation_cannot_roll_back_the_current_body(self):
        # 지연·재생된 관측이 최신 본문을 되돌리면 Consumer 가 이미 없는 텍스트를 읽는다
        db = FakeMinuteDB()
        write(db, vendor_row(TITLE="정정된 제목", CONTENT="정정된 리드"),
              observed_at=OBSERVED + timedelta(hours=3))

        assert write(db, vendor_row(TITLE="옛 제목", CONTENT="옛 리드"),
                     observed_at=OBSERVED) == 0

        # ⚠️ 제목과 리드는 **다른 테이블**이다. 신선도 가드를 한쪽에만 걸면 낡은 관측이
        # 리드만 되돌려 "제목 T2 + 리드 T1" 이라는 **존재한 적 없는 본문**이 남는다
        # (실측으로 재현했다 — 이 트랙에서 가드를 한 곳에만 건 실수의 재현이다).
        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert document["title"] == "정정된 제목"
        assert db.news_documents[document["document_id"]]["lead_text"] == "정정된 리드"

    def test_lead_only_correction_moves_the_arrival_time(self):
        # document 의 비교 절은 리드를 못 본다 — 그대로 두면 리드만 바뀐 정정에서 내용은
        # 새것인데 PIT 시각은 옛것으로 남는다(이 PR 이 고치려던 바로 그 어긋남).
        db = FakeMinuteDB()
        write(db, vendor_row())
        corrected_at = OBSERVED + timedelta(hours=3)

        assert write(db, vendor_row(CONTENT="리드만 정정"), observed_at=corrected_at) == 1
        assert db.documents[(SOURCE, ARTICLE_ID)]["available_at"] == corrected_at


class TestIdempotence:
    def test_same_body_reobservation_writes_nothing(self):
        db = FakeMinuteDB()
        assert write(db, vendor_row()) == 1
        # 같은 본문 재관측 — 값이 같으면 UPDATE 하지 않는다(멱등 집계가 거짓이 되지 않게)
        assert write(db, vendor_row()) == 0

    def test_batch_normalization_rules_are_reused(self):
        # 제목 공백 정규화·리드 출처·언어 파생이 배치와 같아야 한다 — 갈리면 같은 두 컬럼에
        # 두 생산자가 다른 규칙으로 쓴다(dedup·프롬프트 입력이 조용히 갈린다)
        db = FakeMinuteDB()
        write(db, vendor_row())
        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert document["title"] == "삼성전자, 테슬라와 공급계약"   # 연속 공백 축약
        assert document["language_code"] == "ko"                    # 벤더 고정 파생
        assert document["source_uri"] == "https://news.example/1"
        assert document["published_at"] is not None


class TestIdentity:
    def test_ledger_article_id_wins_over_recomputed_one(self):
        # 원장은 fallback id → URL identity 로 **단방향 승격**한다(ALPHA-668). _normalize 의
        # 재계산값을 쓰면 job 을 만든 id 와 canonical 행의 id 가 갈려, 추출 결과가 그 기사에
        # 안 붙는다.
        db = FakeMinuteDB()
        promoted = "promoted-url-identity"
        write(db, vendor_row(article_id=promoted))

        assert (SOURCE, promoted) in db.documents
        assert db.documents[(SOURCE, promoted)]["document_id"] == stable_domain_id(
            "doc", SOURCE, promoted
        )

    def test_missing_natural_key_fails_loud(self):
        # 조용히 건너뛰면 commit 은 job 을 만들었는데 정본이 없는 상태가 된다
        db = FakeMinuteDB()
        with pytest.raises(ValueError, match="자연키 결손"):
            write(db, vendor_row(article_id=None))

    def test_unknown_vendor_fails_loud(self):
        # `_normalize` 는 미지 벤더를 조용히 FMP 분기로 흘려 전 필드를 None 으로 만든다
        db = FakeMinuteDB()
        with pytest.raises(ValueError, match="미지 뉴스 벤더"):
            write(db, vendor_row(source_code="unknown-wire"))


class TestThroughTheWorker:
    """실 Worker → commit → 이 writer 까지 붙여 본다.

    여기까지 와야 "정정이 도달한다"가 fixture 초록이 아니라 **실제 경로의 사실**이 된다 —
    이 트랙에서 반복해 데인 게 "내 단위 테스트는 초록인데 상류가 그 값을 안 준다"였다.
    """

    def test_late_correction_reaches_the_row_the_consumer_reads(self, tmp_path):
        from test_news_worker import NOW, build_worker

        db = FakeMinuteDB()
        worker, _, _ = build_worker(
            db, tmp_path, windows=2,
            scenario={"scenario": "corr", "initial_count": 1,
                      "late_correction": {"poll_index": 1, "article_index": 0}},
        )
        # ⚠️ Worker 가 tick 에 넘기는 그 시각을 writer 에도 준다 — 안 그러면 이 컬럼만
        # 실제 벽시계가 찍혀 가상 시계 테스트가 비결정적이 된다(계약: 모듈 docstring).
        observed = NOW + timedelta(seconds=1)
        worker.canonical_writer = PgNewsCanonicalWriter(clock=lambda: observed)

        worker.tick(NOW)
        (natural_key, document), = db.documents.items()
        before = {
            "lead": db.news_documents[document["document_id"]]["lead_text"],
            "available_at": document["available_at"],
        }

        worker.tick(NOW + timedelta(seconds=1))

        assert len(db.jobs) == 2, "정정이 새 job 을 만들지 않았다(전제가 깨졌다)"
        after = db.documents[natural_key]
        # ALPHA-689 handler 가 읽는 바로 그 자리에 정정 본문이 들어와야 한다 —
        # 안 오면 새 job(fp2)이 옛 텍스트로 성공한다(봇 P1)
        assert db.news_documents[after["document_id"]]["lead_text"] != before["lead"]
        assert after["available_at"] == before["available_at"] == observed
