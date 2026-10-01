"""ALPHA-1135: 확정된 가격 세션을 다시 열어 재수집한다 — 실 PostgreSQL.

틀린 봉으로 봉인된 하루(ALPHA-1127, 08-04~09-29)를 고칠 유일한 문이 `reopen_session` 이다.
가짜 커넥션이 아니라 실 PG 에서 재는 이유: 거부가 **트랜잭션째** 되돌아가는지(일부 창만
DUE 로 남는 반쯤 열린 세션)와 `FOR UPDATE` 잠금 아래 판정이 실제 스키마 CHECK 를 통과하는지는
fake 로 증명되지 않는다.
"""

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from test_minute_content_recovery import ChangingCollector

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")

# 픽스처 세션은 2026-09-29 — 그 다음 날을 오늘로 둔다(재오픈은 지난 날짜만 연다)
TODAY = datetime(2026, 9, 30).date()


@pytest.fixture
def finalized(tmp_path):
    """두 창을 커밋하고 EOD 가 봉인한 상태(FINALIZED + final_checksum)로 만든 가격 세션."""
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect
    from data_pipeline.lake import LocalStorage
    from data_pipeline.minute.commit import MinuteCommitter
    from data_pipeline.minute.models import KST, WINDOW_SETTLE_SEC, Universe
    from data_pipeline.minute.repository import MinuteLedger
    from data_pipeline.minute.worker import PriceWorker, WorkerConfig

    db = DbConfig(host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
                  name="edge", user="edge", password="edge", sslmode="disable")
    start = datetime(2026, 9, 29, 9, 0, tzinfo=KST)
    universe = Universe(universe_version="reopen-test", etf_ids=("500000",),
                        constituent_ids=("100000",))
    ledger = MinuteLedger(db=db)
    windows = [(start + timedelta(minutes=i), start + timedelta(minutes=i + 1)) for i in range(2)]
    sid, _ = ledger.plan_session(
        dataset="price_minute", source_group="alpha1135-reopen-test", session_date=start.date(),
        universe_version=universe.universe_version, universe_hash=universe.universe_hash,
        windows=windows,
    )
    collector = ChangingCollector()

    def worker():
        return PriceWorker(
            session_id=sid, ledger=ledger, committer=MinuteCommitter(db=db),
            storage=LocalStorage(tmp_path), collector=collector,
            config=WorkerConfig(worker_id="w1", dataset="price_minute", source="kis", market="KR",
                                session_date="2026-09-29", run_id="t", lease_seconds=60,
                                recovery_budget_per_tick=0, artifact_format="content_v2",
                                universe=universe, is_backfill=True,
                                trigger_schema_version="test", destination="test"))

    now = start + timedelta(minutes=2, seconds=WINDOW_SETTLE_SEC)
    first = worker()
    while first.tick(now) == "PROCESSED":
        pass
    with connect(db) as c:
        # EOD 가 봉인한 모양 — drain·QC 경로는 eod 테스트 소관이라 결과 상태만 만든다
        c.execute("UPDATE minute_ingestion_session SET phase='FINALIZED', final_checksum=%s, "
                  "final_generation=1, lease_expires_at=NULL WHERE session_id=%s",
                  ("f" * 64, sid))
    h = SimpleNamespace(db=db, connect=connect, sid=sid, start=start, now=now, ledger=ledger,
                        worker=worker, collector=collector, storage=LocalStorage(tmp_path))
    try:
        yield h
    finally:
        with connect(db) as c:
            c.execute("DELETE FROM dataset_commit_outbox WHERE payload->>'session_id'=%s", (sid,))
            c.execute("DELETE FROM price_window_job WHERE session_id=%s", (sid,))
            c.execute("DELETE FROM minute_window_artifact_commit WHERE session_id=%s", (sid,))
            c.execute("DELETE FROM minute_ingestion_window WHERE session_id=%s", (sid,))
            c.execute("DELETE FROM minute_ingestion_session WHERE session_id=%s", (sid,))


def _session(h):
    with h.connect(h.db) as c:
        return c.execute("SELECT phase, worker_fencing_token, final_checksum, final_generation "
                         "FROM minute_ingestion_session WHERE session_id=%s", (h.sid,)).fetchone()


def _windows(h):
    with h.connect(h.db) as c:
        return c.execute("SELECT window_start, data_status, generation, checksum "
                         "FROM minute_ingestion_window WHERE session_id=%s ORDER BY window_start",
                         (h.sid,)).fetchall()


def test_reopened_session_recollects_and_bumps_only_changed_windows(finalized):
    """연 세션은 지난 날짜 Worker 가 다시 집고, 값이 바뀐 창만 새 세대가 된다.

    이게 재수집의 전부다 — 재오픈이 창의 generation·checksum 을 지우면 바뀌지 않은 창까지
    새 세대가 되고(옛 세대와 같은 바이트의 중복 이력), 세션을 ACTIVE 로 안 올리면 Worker 가
    fence 를 못 잡아 아무것도 안 돈다(FINALIZED 는 claim 대상이 아니다).
    """
    h = finalized
    from data_pipeline.minute.repository import MinuteLedger  # noqa: F401 — fixture 와 같은 모듈

    before_token = _session(h)[1]
    result = h.ledger.reopen_session(session_id=h.sid, window_starts=None, today=TODAY)
    assert result["reopened_windows"] == 2
    assert result["previous_final_checksum"] == "f" * 64   # 감사 근거로 호출자에게 돌아간다
    phase, token, final_checksum, final_generation = _session(h)
    assert phase == "ACTIVE" and token == before_token + 1
    # 남기면 QC 재실행이 "이미 확정"으로 읽고 옛 판정을 돌려준다
    assert final_checksum is None and final_generation is None
    assert [r[1] for r in _windows(h)] == ["DUE", "DUE"]
    assert [r[2] for r in _windows(h)] == [1, 1]           # 세대는 그대로 — 재커밋이 대조한다

    h.collector.variant = "B"                               # 고쳐진 값(종가 100→101)
    w = h.worker()
    assert w.tick(h.now + timedelta(hours=1)) == "PROCESSED"
    assert w.tick(h.now + timedelta(hours=1)) == "PROCESSED"
    rows = _windows(h)
    assert [r[1] for r in rows] == ["VALID", "VALID"]
    assert [r[2] for r in rows] == [2, 2]


def test_reopen_of_selected_windows_leaves_the_rest_sealed(finalized):
    # ALPHA-1128 이 쓰는 모양 — 마감 창 하나만 다시 받는다. 나머지를 DUE 로 돌리면 콜만 탄다
    h = finalized
    second = h.start + timedelta(minutes=1)
    assert h.ledger.reopen_session(session_id=h.sid, window_starts=[second], today=TODAY)["reopened_windows"] == 1
    assert [r[1] for r in _windows(h)] == ["VALID", "DUE"]


def test_unknown_window_rejects_the_whole_reopen(finalized):
    """지목한 창 하나라도 원장에 없으면 **아무것도** 안 바뀐다.

    부분 적용을 허용하면 오타(`1529`→`1592`)가 "일부만 열림 + 세션 ACTIVE"로 접힌다 — 그
    세션은 DUE 하나 없이 ACTIVE 로 남아 drain 전까지 확정이 풀린 채다.
    """
    from data_pipeline.minute.repository import SessionReopenRejected

    h = finalized
    before = (_session(h), _windows(h))
    with pytest.raises(SessionReopenRejected, match="원장에 있다"):
        h.ledger.reopen_session(session_id=h.sid,
                                window_starts=[h.start, h.start + timedelta(minutes=33)],
                                today=TODAY)
    assert (_session(h), _windows(h)) == before


@pytest.mark.parametrize("phase", ["ACTIVE", "DRAINING", "DRAINED", "QC_RUNNING"])
def test_only_finalized_sessions_are_reopened(finalized, phase):
    # 살아 있는 세션에 걸면 Worker 의 fence 를 뺏고, QC 중이면 판정과 다툰다
    from data_pipeline.minute.repository import SessionReopenRejected

    h = finalized
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_session SET phase=%s WHERE session_id=%s", (phase, h.sid))
    before = _windows(h)
    with pytest.raises(SessionReopenRejected, match="FINALIZED·FAILED"):
        h.ledger.reopen_session(session_id=h.sid, window_starts=None, today=TODAY)
    assert _windows(h) == before


def test_non_price_sessions_are_rejected(finalized):
    # 소급 TR 이 있는 건 가격뿐이다 — 업종지수를 열면 아무도 못 채우는 DUE 가 남는다
    from data_pipeline.minute.repository import SessionReopenRejected

    h = finalized
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_session SET dataset='sector_index_minute' "
                  "WHERE session_id=%s", (h.sid,))
    with pytest.raises(SessionReopenRejected, match="가격 세션만"):
        h.ledger.reopen_session(session_id=h.sid, window_starts=None, today=TODAY)
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_session SET dataset='price_minute' WHERE session_id=%s",
                  (h.sid,))


def test_early_drain_keeps_reopened_windows_and_fails_qc_instead_of_sealing_missing(finalized):
    """재수집 전에 drain 이 걸려도 옛 확정분을 MISSING 으로 봉인하지 않는다(리뷰 지적).

    QC 는 "도래한 DUE = 누락"으로 MISSING 확정한다. 재오픈한 창도 DUE 라 구분이 없으면
    옛 VALID 값이 있던 창이 결손으로 봉인되고 재청구 대상에서도 빠진다 — 데이터는 S3 에
    남아 있는데 원장이 그걸 버린다. 원장은 generation ≥ 1 인 DUE 를 남기고 QC 가 세션을
    FAILED 로 세운다. FAILED 는 다시 열려 마저 받을 수 있어야 한다.
    """
    from data_pipeline.minute.eod import SessionQc

    h = finalized
    h.ledger.reopen_session(session_id=h.sid, window_starts=None, today=TODAY)
    h.ledger.request_drain(session_id=h.sid, now=h.now)     # Worker 가 한 창도 안 받은 채
    assert h.worker().tick(h.now + timedelta(hours=1)) == "DRAINED"
    result = SessionQc(ledger=h.ledger, storage=h.storage).run(
        session_id=h.sid, now=h.now + timedelta(hours=2))
    assert result["phase"] == "FAILED" and result["missing_confirmed"] == 0
    assert any("재오픈 뒤 다시 받지 못한 창 2건" in v for v in result["violations"])
    assert [(r[1], r[2]) for r in _windows(h)] == [("DUE", 1), ("DUE", 1)]   # 옛 확정분 보존

    # FAILED 를 다시 열어 끝까지 받으면 정상 경로로 돌아온다
    assert h.ledger.reopen_session(session_id=h.sid, window_starts=None, today=TODAY)["reopened_windows"] == 2
    w = h.worker()
    assert w.tick(h.now + timedelta(hours=3)) == "PROCESSED"
    assert w.tick(h.now + timedelta(hours=3)) == "PROCESSED"
    assert [r[1] for r in _windows(h)] == ["VALID", "VALID"]


def test_never_committed_due_is_still_confirmed_missing(finalized):
    # 위 가드가 진짜 누락(한 번도 커밋 안 된 창)까지 살려 두면 QC 가 모든 결손 날을 FAILED 로
    # 세운다 — 가드는 generation 으로만 가른다
    from data_pipeline.minute.eod import SessionQc

    h = finalized
    with h.connect(h.db) as c:
        c.execute("UPDATE minute_ingestion_window SET data_status='DUE', generation=0, "
                  "checksum=NULL, manifest_uri=NULL, manifest_checksum=NULL "
                  "WHERE session_id=%s AND window_start=%s", (h.sid, h.start + timedelta(minutes=1)))
        c.execute("DELETE FROM minute_window_artifact_commit WHERE session_id=%s AND window_start=%s",
                  (h.sid, h.start + timedelta(minutes=1)))
        c.execute("UPDATE minute_ingestion_session SET phase='DRAINED' WHERE session_id=%s", (h.sid,))
    result = SessionQc(ledger=h.ledger, storage=h.storage).run(
        session_id=h.sid, now=h.now + timedelta(hours=2))
    assert result["missing_confirmed"] == 1
    assert [r[1] for r in _windows(h)] == ["VALID", "MISSING"]


@pytest.mark.parametrize("today_offset", [0, -1])
def test_today_or_future_session_is_rejected(finalized, today_offset):
    """오늘(이후) 세션은 열지 않는다(봇 P1).

    Worker 는 날짜만으로 소급 경로를 고른다(`session_date < 오늘`). 마감 뒤 오늘 세션을 열면
    당일 TR 로 다시 받는데, 당일 TR 은 종가 단일가 봉을 세션 안에서 주지 않아(ALPHA-1128)
    같은 단일가 전 값을 재커밋·재봉인한다 — 고친 줄 알지만 아무것도 안 바뀐다.
    """
    from data_pipeline.minute.repository import SessionReopenRejected

    h = finalized
    before = (_session(h), _windows(h))
    with pytest.raises(SessionReopenRejected, match="지난 날짜"):
        h.ledger.reopen_session(session_id=h.sid, window_starts=None,
                                today=h.start.date() + timedelta(days=today_offset))
    assert (_session(h), _windows(h)) == before
