"""EOD 세션 QC 테스트 (ALPHA-693, 계획 §13).

의도: 누락은 **확정되지 않으면 존재하지 않는 것과 같다**. Worker 는 drain 에서 claim 을
반납만 하고 떠나므로, 처리 못 한 window 는 `DUE` 로 남아 "아직 안 한 것"처럼 보인다.
여기서 고정하는 건 넷이다.

- **DUE 잔존은 MISSING 으로 확정된다** — 그래야 원장이 하루의 결손을 말할 수 있다.
- **살아 있는 세션은 건드리지 않는다** — phase 가드가 빠지면 이 명령 하나가 그날 데이터를
  통째로 죽인다(처리 대기 중인 window 가 claim 대상에서 빠진다).
- **되돌릴 수 없는 것은 FINALIZED 뿐** — 중간에 죽은 QC·실패한 QC 는 다시 들어갈 수 있어야
  한다(QC 에는 lease 가 없어서, 막으면 그 세션은 누구도 끝낼 수 없다).
- **원장이 스스로와 모순이면 확정하지 않는다** — 결손(MISSING)은 판정 결과지만, CLAIMED
  잔존·계획 개수 불일치는 판정을 믿을 수 없다는 뜻이다.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage
from data_pipeline.minute.eod import SessionQc, SessionQcRejected
from data_pipeline.minute.models import KST
from data_pipeline.minute.repository import MinuteLedger

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 16, 0, tzinfo=KST)
_OPEN = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
# plan_session 은 half-open (start, end) 쌍을 받는다 — 원장 계약 그대로 쓴다
WINDOW_PAIRS = tuple(
    (_OPEN + timedelta(minutes=n), _OPEN + timedelta(minutes=n + 1)) for n in range(3)
)
WINDOWS = tuple(start for start, _ in WINDOW_PAIRS)


def make_session(db, *, statuses, phase="DRAINED", expected=None):
    """window 상태를 지정해 세션 하나를 만든다 — 원장 API 로 만들 수 없는 결손 상태를
    직접 놓는다(그 상태에 이르는 경로는 Worker·drain 테스트가 이미 고정한다)."""
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version="v1", universe_hash="h1",
        windows=WINDOW_PAIRS[:len(statuses)],
    )
    for index, (window_start, status) in enumerate(zip(WINDOWS, statuses, strict=True)):
        window = db.windows[(session_id, window_start)]
        window["data_status"] = status
        window["generation"] = 0 if status in ("DUE", "CLAIMED") else index + 1
        window["checksum"] = None if status in ("DUE", "CLAIMED") else f"c{index}"
    db.sessions[session_id]["phase"] = phase
    if expected is not None:
        db.sessions[session_id]["expected_window_count"] = expected
    return ledger, session_id


def make_qc(db, tmp_path):
    return SessionQc(
        ledger=MinuteLedger(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(tmp_path), source="toss", market="KR",
    )


class TestMissingConfirmation:
    def test_due_becomes_missing_and_session_finalizes(self, tmp_path):
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "DUE", "VALID_EMPTY"))

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)

        assert result["ok"] is True
        assert result["missing_confirmed"] == 1
        assert result["counts"]["MISSING"] == 1
        assert result["counts"]["DUE"] == 0
        assert db.sessions[session_id]["phase"] == "FINALIZED"
        assert db.sessions[session_id]["final_checksum"] == result["final_checksum"]

    def test_live_session_windows_are_untouched(self, tmp_path):
        # ⚠️ 가장 위험한 오작동: 살아 있는 세션에 QC 를 잘못 겨누면 처리 대기 중인 window 가
        # 전부 MISSING 이 돼 claim 대상에서 빠진다 — 그날 데이터가 통째로 사라진다.
        db = FakeMinuteDB()
        ledger, session_id = make_session(
            db, statuses=("VALID", "DUE", "DUE"), phase="ACTIVE"
        )
        with pytest.raises(SessionQcRejected):
            make_qc(db, tmp_path).run(session_id=session_id, now=NOW)

        assert ledger.confirm_missing_windows(session_id=session_id) == 0
        assert [w["data_status"] for w in db.windows.values()].count("DUE") == 2
        assert db.sessions[session_id]["phase"] == "ACTIVE"

    def test_counts_cover_every_status_even_at_zero(self, tmp_path):
        # 0 건인 축이 결과에서 사라지면 "없었다"와 "안 셌다"가 같아진다(Rule 12)
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "VALID", "VALID"))
        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert set(result["counts"]) == {
            "VALID", "VALID_EMPTY", "INCOMPLETE", "MISSING", "INVALID", "DUE", "CLAIMED",
        }
        assert result["complete_count"] == 3


class TestReentry:
    """되돌릴 수 없는 건 FINALIZED 뿐 — 그 밖의 자리는 다시 들어갈 수 있어야 한다."""

    def test_crashed_qc_can_be_rerun(self, tmp_path):
        # QC 에는 lease 가 없다. QC_RUNNING 재진입을 막으면 중간에 죽은 세션을 **누구도**
        # 끝낼 수 없다(사람이 DB 를 직접 고치는 것 말고는 경로가 없다).
        db = FakeMinuteDB()
        _, session_id = make_session(
            db, statuses=("VALID", "DUE", "VALID"), phase="QC_RUNNING"
        )
        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is True
        assert db.sessions[session_id]["phase"] == "FINALIZED"

    def test_failed_qc_can_be_rerun_after_the_cause_is_fixed(self, tmp_path):
        db = FakeMinuteDB()
        _, session_id = make_session(
            db, statuses=("VALID", "CLAIMED", "VALID"), phase="DRAINED"
        )
        qc = make_qc(db, tmp_path)
        first = qc.run(session_id=session_id, now=NOW)
        assert (first["ok"], db.sessions[session_id]["phase"]) == (False, "FAILED")

        # 원인을 고친 뒤 다시 판정할 수 있어야 한다
        db.windows[(session_id, WINDOWS[1])]["data_status"] = "MISSING"
        second = qc.run(session_id=session_id, now=NOW)
        assert (second["ok"], db.sessions[session_id]["phase"]) == (True, "FINALIZED")

    def test_finalized_session_is_not_reopened(self, tmp_path):
        # 확정된 하루를 다시 여는 경로는 정정(새 세대)이지 재QC 가 아니다
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "VALID", "VALID"))
        qc = make_qc(db, tmp_path)
        qc.run(session_id=session_id, now=NOW)
        with pytest.raises(SessionQcRejected, match="FINALIZED"):
            qc.run(session_id=session_id, now=NOW)
        assert db.sessions[session_id]["phase"] == "FINALIZED"


class TestInvariantViolations:
    """결손은 판정 결과고, 모순은 판정을 못 믿는다는 뜻이다 — 둘을 같게 다루지 않는다."""

    def test_missing_windows_do_not_block_finalize(self, tmp_path):
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("MISSING", "MISSING", "VALID"))
        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert (result["ok"], result["violations"]) == (True, [])
        assert result["counts"]["MISSING"] == 2

    def test_claimed_leftover_fails_instead_of_being_folded_into_missing(self, tmp_path):
        # ack_drain 이 CLAIMED 잔존을 거부하므로 DRAINED 세션엔 있을 수 없다. 있다면
        # drain 을 우회한 경로가 있다는 뜻이고, MISSING 으로 접으면 그 경로가 숨는다.
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "CLAIMED", "VALID"))
        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("CLAIMED" in v for v in result["violations"])
        assert db.sessions[session_id]["phase"] == "FAILED"

    def test_window_count_mismatch_fails(self, tmp_path):
        # 계획보다 행이 적으면 planner 가 덜 만든 것이다 — 그 세션의 "완전함" 판정은
        # 보이는 행만 근거로 하므로 신뢰할 수 없다
        db = FakeMinuteDB()
        _, session_id = make_session(
            db, statuses=("VALID", "VALID", "VALID"), expected=390
        )
        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("계획" in v for v in result["violations"])

    def test_unknown_status_is_not_silently_dropped(self, tmp_path):
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "VALID", "VALID"))
        db.windows[(session_id, WINDOWS[1])]["data_status"] = "WEIRD"
        with pytest.raises(ValueError, match="미지 data_status"):
            make_qc(db, tmp_path).run(session_id=session_id, now=NOW)


class TestFinalChecksum:
    def test_same_result_same_checksum(self, tmp_path):
        checksums = []
        for _ in range(2):
            db = FakeMinuteDB()
            _, session_id = make_session(db, statuses=("VALID", "MISSING", "VALID_EMPTY"))
            checksums.append(
                make_qc(db, tmp_path).run(session_id=session_id, now=NOW)["final_checksum"]
            )
        assert checksums[0] == checksums[1]

    def test_swapped_statuses_change_the_checksum(self, tmp_path):
        # 집계만 해시하면 두 window 의 상태가 서로 뒤바뀐 세션이 같은 값을 갖는다 —
        # 그러면 "같은 결과"라는 판정이 거짓이 된다
        results = []
        for statuses in (("VALID", "MISSING", "VALID"), ("MISSING", "VALID", "VALID")):
            db = FakeMinuteDB()
            _, session_id = make_session(db, statuses=statuses)
            results.append(
                make_qc(db, tmp_path).run(session_id=session_id, now=NOW)["final_checksum"]
            )
        assert results[0] != results[1]

    def test_checksum_is_timezone_normalized(self, tmp_path):
        # 같은 순간을 다른 오프셋으로 저장해도 같은 세션이다 — 아니면 배포 환경마다
        # 다른 checksum 이 나와 재실행 no-op 판정이 깨진다
        db_kst = FakeMinuteDB()
        _, kst_id = make_session(db_kst, statuses=("VALID", "VALID", "VALID"))
        kst = make_qc(db_kst, tmp_path).run(session_id=kst_id, now=NOW)["final_checksum"]

        db_utc = FakeMinuteDB()
        _, utc_id = make_session(db_utc, statuses=("VALID", "VALID", "VALID"))
        for window_start in WINDOWS:
            row = db_utc.windows[(utc_id, window_start)]
            row["window_start"] = window_start.astimezone(timezone.utc)
        utc = make_qc(db_utc, tmp_path).run(session_id=utc_id, now=NOW)["final_checksum"]
        assert kst == utc
