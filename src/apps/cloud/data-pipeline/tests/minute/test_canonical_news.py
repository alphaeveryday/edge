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

    def test_lead_stamp_marks_who_observed_the_current_lead(self):
        # ALPHA-696 — 이 시각이 없으면 배치가 자기 레이크의 옛 리드로 되돌릴 근거를 못 가진다.
        # 리드가 붙은 첫 관측은 **주장**이다(배치가 덮지 못하게 막아야 한다).
        db = FakeMinuteDB()
        write(db, vendor_row())

        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert db.news_documents[document["document_id"]]["lead_observed_at"] == OBSERVED

    def test_new_row_without_a_lead_claims_nothing(self):
        # ⚠️ 리드 없는 새 행에 시각을 찍으면 배치가 **정상 스니펫을 갖고 와도 영구 차단**된다
        # — ALPHA-628·695 가 되찾아 온 리드를 이 축이 새로 잃는다. NULL = 미주장이라야 배치가
        # 그 빈 자리를 채울 수 있다.
        db = FakeMinuteDB()
        write(db, vendor_row(CONTENT=None))

        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert db.news_documents[document["document_id"]]["lead_observed_at"] is None

    def test_whitespace_only_lead_claims_nothing(self):
        # ALPHA-848 — 위 테스트의 구멍. 정본 정규화(`normalize_news:199`)는
        # `" ".join(lead.split())` 이라 공백뿐인 리드에 **`None` 이 아니라 `""`** 를 낸다.
        # `is None` 으로 가르면 실질 빈 리드가 축을 선점해, 배치가 진짜 스니펫을 갖고 와도
        # 자기 `fetched_at` 이 더 오래됐으면 영구 차단된다 — 위 테스트가 막으려던 바로
        # 그 상태가 `""` 로 우회된다. 배치는 `if doc["lead_text"]` 라 `""` 를 쓰지도 않아
        # 아무도 그 자리를 채우지 못한다.
        db = FakeMinuteDB()
        write(db, vendor_row(CONTENT="   "))

        document = db.documents[(SOURCE, ARTICLE_ID)]
        row = db.news_documents[document["document_id"]]
        assert not row["lead_text"], "공백 리드가 실질 값으로 저장됐다(전제가 깨졌다)"
        assert row["lead_observed_at"] is None

    def test_clearing_an_existing_lead_is_a_claim(self):
        # 반대쪽 — 있던 리드를 지우는 정정은 **지금 알게 된 사실**이다. 여기서 시각이 안 남으면
        # 배치의 `IS NULL` 절이 열려 옛 리드가 복원되고, 그때부터 배치가 계속 이기는 고착이 된다.
        db = FakeMinuteDB()
        write(db, vendor_row())
        cleared = datetime(2026, 7, 31, 10, 0, tzinfo=KST)
        write(db, vendor_row(CONTENT=None), observed_at=cleared)

        news = db.news_documents[db.documents[(SOURCE, ARTICLE_ID)]["document_id"]]
        assert news["lead_text"] is None
        assert news["lead_observed_at"] == cleared

    def test_reobserving_the_same_lead_does_not_advance_the_stamp(self):
        # ⚠️ 매 관측마다 밀어 올리면 이 레인이 장중 내내 시각을 끌고 가, 레인이 못 본 진짜
        # 정정(기사가 목록 1페이지에서 밀려난 경우)을 배치가 영영 못 싣는다 — 축이 무효가 된다.
        db = FakeMinuteDB()
        write(db, vendor_row())
        write(db, vendor_row(), observed_at=datetime(2026, 7, 31, 15, 0, tzinfo=KST))

        news = db.news_documents[db.documents[(SOURCE, ARTICLE_ID)]["document_id"]]
        assert news["lead_observed_at"] == OBSERVED

    def test_an_unclaimed_slot_takes_the_first_real_lead_stamp(self):
        # 미주장(NULL) 자리에 리드가 처음 붙는 충돌 갈래 — ALPHA-858 전까지 이 경로를 밟는
        # 테스트가 없었다. 배치가 채우기 전에 1분 레인이 먼저 스니펫을 잡는 흔한 형상이고,
        # 여기서 시각이 안 찍히면 이후 배치가 자기 옛 리드로 언제든 덮어쓴다.
        # ⚠️ 단조 가드가 저장값 NULL 을 만나는 유일한 자리이기도 하다 — PG 의 `GREATEST` 는
        # NULL 을 무시하지만 파이썬 `max(None, x)` 는 TypeError 다. 픽스처가 운영 SQL 과
        # 갈리면 이 갈래가 조용히 검증 밖으로 빠진다.
        db = FakeMinuteDB()
        write(db, vendor_row(CONTENT=None))            # 리드 없는 새 행 = 미주장
        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert db.news_documents[document["document_id"]]["lead_observed_at"] is None

        later = datetime(2026, 7, 31, 11, 0, tzinfo=KST)
        write(db, vendor_row(), observed_at=later)      # 이제 진짜 리드가 붙는다

        news = db.news_documents[document["document_id"]]
        assert news["lead_text"] == "삼성전자가 테슬라에 칩을 공급한다."
        assert news["lead_observed_at"] == later, "미주장 자리에 첫 주장이 안 찍혔다"

    def test_a_backward_clock_does_not_drag_the_stamp_back(self):
        # ALPHA-858 — `observed_at` 은 벽시계라 컨테이너 시계가 뒤로 조정되면 이 축이 역행한다.
        # 그러면 배치의 `저장값 <= 자기 fetched_at` 절이 열려 **1분 경로가 반영한 정정을 배치가
        # 레이크의 옛 리드로 되돌린다** — ALPHA-696 이 막으려던 P1 재현이다.
        # ⚠️ 내용은 여전히 이번 관측이 이긴다(계약 ①). 막는 건 시각뿐이다 — 시각으로 내용
        # 쓰기를 막으면 모듈 docstring 이 되돌렸다고 적은 그 P1 이 반대편에서 돌아온다.
        db = FakeMinuteDB()
        write(db, vendor_row())                         # 시각 = OBSERVED(09:00:30)
        skewed = datetime(2026, 7, 31, 8, 0, tzinfo=KST)   # 시계가 한 시간 뒤로
        write(db, vendor_row(CONTENT="공급 규모가 3조원으로 정정됐다."), observed_at=skewed)

        news = db.news_documents[db.documents[(SOURCE, ARTICLE_ID)]["document_id"]]
        assert news["lead_text"] == "공급 규모가 3조원으로 정정됐다.", \
            "내용 쓰기가 시각으로 막혔다 — 1분 경로는 쓰기 가드를 걸지 않는다(ALPHA-696 ①)"
        assert news["lead_observed_at"] == OBSERVED, \
            "시각이 뒤로 밀렸다 — 배치가 옛 리드를 복원할 창이 열린다(ALPHA-858)"

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

    def test_late_observation_still_writes_content_but_never_rewinds_time(self):
        # ⚠️ 저장된 **시각**으로 내용 쓰기를 막으면 안 된다. 배치는
        # `available_at = fetched_at or published_at` 이라 fetched_at 결손 시 **미래
        # 발행일**을 싣는다 — 그러면 "시각은 미래인데 내용은 옛것"인 행이 생기고, 시각만
        # 보고 건너뛰면 원장은 fp2 를 확정했는데 Consumer 는 옛 본문을 읽는다(이 모듈이
        # 막으려던 P1 그대로다). 내용은 이번 관측을 따르고, 시각만 단조로 둔다.
        db = FakeMinuteDB()
        future_stamp = OBSERVED + timedelta(days=2)     # 배치가 미래 발행일을 실은 형상
        db.documents[(SOURCE, ARTICLE_ID)] = {
            "document_id": stable_domain_id("doc", SOURCE, ARTICLE_ID),
            "source_code": SOURCE, "source_document_id": ARTICLE_ID,
            "title": "옛 제목", "language_code": "ko",
            "published_at": "2026-07-31T00:00:00+00:00",
            "available_at": future_stamp, "source_uri": "https://news.example/1",
        }

        write(db, vendor_row(TITLE="정정된 제목", CONTENT="정정된 리드"),
              observed_at=OBSERVED)

        document = db.documents[(SOURCE, ARTICLE_ID)]
        assert document["title"] == "정정된 제목"                       # 내용은 최신 관측
        assert db.news_documents[document["document_id"]]["lead_text"] == "정정된 리드"
        assert document["available_at"] == future_stamp                 # 시각은 안 밀린다

    def test_first_real_lead_moves_the_arrival_time(self):
        # 배치가 리드 없이 넣은 문서에 실제 리드가 처음 붙는 것은 **내용 변경**이다 —
        # 그 리드는 지금 알게 된 것이라 도착 시각이 따라가야 한다(as-of 가 어긋나지 않게).
        db = FakeMinuteDB()
        write(db, vendor_row(CONTENT=None))
        document = db.documents[(SOURCE, ARTICLE_ID)]
        del db.news_documents[document["document_id"]]      # 배치가 만든 형상
        document["available_at"] = OBSERVED
        later = OBSERVED + timedelta(hours=3)

        assert write(db, vendor_row(CONTENT="이제 붙은 리드"), observed_at=later) == 1
        assert db.documents[(SOURCE, ARTICLE_ID)]["available_at"] == later

    def test_arrival_time_never_moves_backwards(self):
        # ⚠️ available_at 을 쓰는 경로가 셋이다(부모 upsert·리드 보정 UPDATE·최초 INSERT).
        # 한 곳만 규칙이 달라도 그 경로로 시각이 뒤로 가고, 그러면 과거 as-of 구간에서
        # 문서가 사라진다. 리드만 바뀌는 경우가 특히 위험하다 — 부모 upsert 가 no-op 이라
        # 보정 UPDATE 가 단독으로 시각을 쓴다.
        db = FakeMinuteDB()
        newer = OBSERVED + timedelta(hours=3)
        write(db, vendor_row(CONTENT="최신 리드"), observed_at=newer)

        write(db, vendor_row(CONTENT="뒤늦게 도착한 다른 리드"), observed_at=OBSERVED)

        assert db.documents[(SOURCE, ARTICLE_ID)]["available_at"] == newer

    def test_new_child_row_alone_does_not_move_the_arrival_time(self):
        # 배치는 리드가 없으면 news_document 를 안 만든다 → document 만 있는 문서가 있다.
        # 같은 내용·NULL 리드로 재관측하면 자식 행이 **생기지만** 내용은 그대로다 —
        # rowcount 만 보면 1 이라 도착 시각을 밀고, 그러면 과거 as-of 구간에서 문서가 사라진다.
        db = FakeMinuteDB()
        write(db, vendor_row(CONTENT=None))
        document = db.documents[(SOURCE, ARTICLE_ID)]
        del db.news_documents[document["document_id"]]      # 배치가 만든 형상(자식 행 없음)
        document["available_at"] = OBSERVED

        write(db, vendor_row(CONTENT=None), observed_at=OBSERVED + timedelta(hours=3))
        assert db.documents[(SOURCE, ARTICLE_ID)]["available_at"] == OBSERVED

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

    def test_naive_clock_is_rejected(self):
        # naive 를 TIMESTAMPTZ 에 넣으면 세션 tz 로 해석돼 배포마다 값이 달라지고,
        # 다음 관측의 비교에서 aware 값과 만나 TypeError 로 window 가 반복 롤백된다.
        db = FakeMinuteDB()
        with pytest.raises(ValueError, match="timezone-aware"):
            write(db, vendor_row(), observed_at=datetime(2026, 7, 31, 9, 0))

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
