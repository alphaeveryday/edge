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
from data_pipeline.minute.models import KST, Universe, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger

_DB = DbConfig(password="x")
SESSION_DATE = date(2026, 7, 31)
NOW = datetime(2026, 7, 31, 16, 0, tzinfo=KST)
_OPEN = datetime(2026, 7, 31, 9, 0, tzinfo=KST)
# ⚠️ **실제 planner 가 만드는 하루**를 쓴다(390 window). 짧은 가짜 세션(3분)으로 두면
# 계획의 양 끝을 보는 게이트를 만들 수 없고, 그 게이트가 빠진 채로도 테스트가 초록이다 —
# 즉 "첫 분이 통째로 빠진 계획"의 반례를 영영 못 만든다.
UNIVERSE = Universe(universe_version="v1", etf_ids=("E1",), constituent_ids=("C1",))
WINDOW_PAIRS = plan_session_windows(SESSION_DATE, universe=UNIVERSE)
WINDOWS = tuple(start for start, _ in WINDOW_PAIRS)


def make_session(db, *, statuses=(), phase="DRAINED"):
    """하루치(390 window) 세션 하나. `statuses` 는 **앞쪽 window 부터** 덮어쓰고 나머지는
    VALID 다 — 원장 API 로 만들 수 없는 결손 상태를 직접 놓는다(거기 이르는 경로는
    Worker·drain 테스트가 이미 고정한다)."""
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    session_id, _ = ledger.plan_session(
        dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
        universe_version="v1", universe_hash="h1", windows=WINDOW_PAIRS,
    )
    for index, window_start in enumerate(WINDOWS):
        status = statuses[index] if index < len(statuses) else "VALID"
        window = db.windows[(session_id, window_start)]
        window["data_status"] = status
        window["generation"] = 0 if status in ("DUE", "CLAIMED") else index + 1
        window["checksum"] = None if status in ("DUE", "CLAIMED") else f"c{index}"
    db.sessions[session_id]["phase"] = phase
    return ledger, session_id


def make_qc(db, tmp_path):
    return SessionQc(
        ledger=MinuteLedger(db=_DB, connect_fn=db.connect),
        storage=LocalStorage(tmp_path),
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

        assert ledger.confirm_missing_windows(
            session_id=session_id, fence_token=1, now=NOW
        ) == 0
        assert [w["data_status"] for w in db.windows.values()].count("DUE") == 2
        assert db.sessions[session_id]["phase"] == "ACTIVE"

    def test_early_drain_does_not_seal_windows_that_have_not_come_due(self, tmp_path):
        # ⚠️ phase 가드를 **우회하는** 경로다: 장중에 request_drain 이 잘못 호출되면
        # Worker 는 새 claim 을 멈추고, CLAIMED 만 없으면 ack_drain 이 DRAINED 를 만든다.
        # 그 상태로 QC 를 돌리면 아직 오지도 않은 분까지 MISSING 으로 확정하고 하루가
        # 봉인된다 — 되돌릴 수 없는 봉인이라 여기서 막아야 한다.
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "DUE", "DUE"))
        mid_session = _OPEN + timedelta(minutes=1)   # 두 번째 window 가 아직 안 닫힌 시각

        result = make_qc(db, tmp_path).run(session_id=session_id, now=mid_session)

        assert result["ok"] is False                  # 확정하지 않는다
        assert result["missing_confirmed"] == 0       # 미도래 분을 죽이지 않는다
        assert any("DUE 잔존" in v for v in result["violations"])
        assert db.sessions[session_id]["phase"] == "FAILED"
        assert [w["data_status"] for w in db.windows.values()].count("DUE") == 2

    def test_counts_cover_every_status_even_at_zero(self, tmp_path):
        # 0 건인 축이 결과에서 사라지면 "없었다"와 "안 셌다"가 같아진다(Rule 12)
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "VALID", "VALID"))
        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert set(result["counts"]) == {
            "VALID", "VALID_EMPTY", "INCOMPLETE", "MISSING", "INVALID", "DUE", "CLAIMED",
        }
        assert result["complete_count"] == len(WINDOWS)


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

    def test_finalized_session_reports_the_recorded_verdict_again(self, tmp_path):
        # 확정 커밋 직후 출력 전에 죽은 실행의 재시도다. 거부하면 정상 확정된 하루가
        # 재시도마다 실패로 보이고 첫 판정을 복원할 경로도 없다 — 다시 열지 않는 것과
        # 이미 확정된 사실을 보고하는 것은 다르다.
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "VALID", "VALID"))
        qc = make_qc(db, tmp_path)
        first = qc.run(session_id=session_id, now=NOW)

        second = qc.run(session_id=session_id, now=NOW)
        assert (second["ok"], second["phase"], second["reused"]) == (True, "FINALIZED", True)
        assert second["final_checksum"] == first["final_checksum"]
        assert db.sessions[session_id]["phase"] == "FINALIZED"

    def test_stale_run_cannot_overwrite_a_later_verdict(self, tmp_path):
        # ⚠️ 재진입을 열면 ABA 가 생긴다: A 가 스냅샷을 뜬 뒤 멈추고 → B 가 FAILED 로 →
        # C 가 다시 들어오면, A 의 늦은 확정이 "지금 QC_RUNNING 이다"만 보고 성공해
        # **C 의 판정을 낡은 checksum 으로 덮는다**(FINALIZED 는 단방향이다).
        db = FakeMinuteDB()
        ledger, session_id = make_session(db, statuses=("VALID", "MISSING", "VALID"))
        stale = ledger.begin_qc(session_id=session_id, now=NOW)      # 실행 A
        ledger.begin_qc(session_id=session_id, now=NOW)              # 실행 C 재진입

        assert ledger.finalize_session(
            session_id=session_id, fence_token=stale["fence_token"],
            final_checksum="stale", final_generation=1, now=NOW,
        ) is False
        assert ledger.fail_session_qc(
            session_id=session_id, fence_token=stale["fence_token"], now=NOW
        ) is False
        assert db.sessions[session_id]["phase"] == "QC_RUNNING"

    def test_stale_run_cannot_confirm_missing(self, tmp_path):
        # ⚠️ 되돌릴 수 없는 쓰기는 finalize 가 아니라 **여기**다. 소유권을 잃은 낡은 QC 가
        # 다른 now 로 미도래 window 를 MISSING 으로 찍으면 새 실행이 그걸 정상으로 보고
        # 조기 확정한다 — 토큰 검사는 세션 행을 잠근 뒤에 해야 한다(비잠금이면 옛 스냅샷).
        db = FakeMinuteDB()
        ledger, session_id = make_session(db, statuses=("VALID", "DUE", "DUE"))
        stale = ledger.begin_qc(session_id=session_id, now=NOW)   # 실행 A
        ledger.begin_qc(session_id=session_id, now=NOW)           # 실행 B 가 소유권 인수

        assert ledger.confirm_missing_windows(
            session_id=session_id, fence_token=stale["fence_token"], now=NOW
        ) == 0
        assert [w["data_status"] for w in db.windows.values()].count("DUE") == 2

    def test_session_that_never_drained_is_rejected(self, tmp_path):
        db = FakeMinuteDB()
        _, session_id = make_session(
            db, statuses=("VALID", "VALID", "VALID"), phase="ACTIVE"
        )
        with pytest.raises(SessionQcRejected, match="자격"):
            make_qc(db, tmp_path).run(session_id=session_id, now=NOW)


class TestSnapshotIsFrozen:
    """QC 는 fence 를 안 잡는다 — 그게 안전한 건 Worker 경로가 phase 로 이미 닫혀서다.

    이 전제가 깨지면(누가 저 phase 집합을 넓히면) QC 는 **움직이는 원장을 스냅샷으로
    착각**해, 확정한 뒤에 들어온 결과가 영영 반영되지 않는다. 문서로만 두면 조용히 깨진다.
    """

    def test_worker_cannot_re_enter_a_finalized_session(self, tmp_path):
        db = FakeMinuteDB()
        ledger, session_id = make_session(db, statuses=("VALID", "DUE", "VALID"))
        make_qc(db, tmp_path).run(session_id=session_id, now=NOW)

        # fence 를 못 잡으면 claim·기록 경로가 통째로 닫힌다(전부 fence 검사 뒤에 있다)
        assert ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=60
        ) is None

    def test_worker_cannot_re_enter_a_session_under_qc(self, tmp_path):
        db = FakeMinuteDB()
        ledger, session_id = make_session(
            db, statuses=("VALID", "DUE", "VALID"), phase="QC_RUNNING"
        )
        assert ledger.acquire_worker_fence(
            session_id=session_id, worker_id="w1", now=NOW, lease_seconds=60
        ) is None


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

    def test_planner_gap_fails_even_though_expected_count_matches(self, tmp_path):
        # ⚠️ `expected_window_count` 는 계획 시점에 COUNT(*) 로 덮어써진다 — 그 값과
        # 대조하는 게이트는 planner 가 분을 빠뜨려도 **항상 통과**한다(389==389).
        # 빠진 분은 행 자체가 없어 어떤 상태 집계에도 안 잡히므로, 간격으로만 드러난다.
        db = FakeMinuteDB()
        _, session_id = make_session(db, statuses=("VALID", "VALID", "VALID"))
        del db.windows[(session_id, WINDOWS[1])]          # planner 가 한 분을 빠뜨렸다

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("간격" in v for v in result["violations"])

    def test_missing_first_window_fails_even_though_the_rest_is_contiguous(self, tmp_path):
        # ⚠️ 간격만 보는 게이트가 놓치는 자리다 — 첫 분이 통째로 빠지면 나머지는 완벽히
        # 연속이라 구멍이 안 보이고, expected_window_count 도 COUNT(*) 로 따라간다.
        db = FakeMinuteDB()
        _, session_id = make_session(db)
        del db.windows[(session_id, WINDOWS[0])]

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("계획 범위" in v for v in result["violations"])

    def test_missing_last_window_fails(self, tmp_path):
        db = FakeMinuteDB()
        _, session_id = make_session(db)
        del db.windows[(session_id, WINDOWS[-1])]

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("계획 범위" in v for v in result["violations"])

    def test_extended_plan_missing_its_first_hour_is_rejected(self, tmp_path):
        # ⚠️ 양 끝을 **따로** 보면 통과하는 교차 조합이다: 시간외 계획(08:00~20:00)에서
        # 앞 1시간이 빠지면 09:00~20:00 이 되는데, 09:00 도 20:00 도 각각은 정상 값이다.
        db = FakeMinuteDB()
        extended = Universe(
            universe_version="v1", etf_ids=("E1",), constituent_ids=("C1",),
            extended_hours_ids=("C1",),
        )
        pairs = plan_session_windows(SESSION_DATE, universe=extended)
        ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
        session_id, _ = ledger.plan_session(
            dataset="price_minute", source_group="toss", session_date=SESSION_DATE,
            universe_version="v2", universe_hash="h2", windows=pairs[60:],
        )
        for start, _end in pairs[60:]:
            row = db.windows[(session_id, start)]
            row.update(data_status="VALID", generation=1, checksum="c")
        db.sessions[session_id]["phase"] = "DRAINED"

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("계획 범위" in v for v in result["violations"])

    def test_windows_from_another_trading_day_are_rejected(self, tmp_path):
        # ⚠️ 나머지 검사는 전부 **시각만** 본다 — 다른 거래일 행이 섞여도 거래시간·개수가
        # 같으면 통과한다. plan_session 은 session_date 와 windows 를 독립으로 받아 그
        # 둘이 맞는지 안 보므로, 어긋난 planner·백필이 채운 행이 그대로 확정된다.
        db = FakeMinuteDB()
        _, session_id = make_session(db)
        shifted = {}
        for key, row in list(db.windows.items()):
            if row["session_id"] != session_id:
                continue
            row["window_start"] = row["window_start"] - timedelta(days=1)
            shifted[(key[0], row["window_start"])] = row
            del db.windows[key]
        db.windows.update(shifted)

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("세션 날짜" in v for v in result["violations"])

    def test_empty_plan_is_not_a_complete_day(self, tmp_path):
        # 행이 없으면 집계도 0, 간격도 없다 — 공허참으로 "완전한 하루"가 된다
        db = FakeMinuteDB()
        _, session_id = make_session(db)
        for window_start in WINDOWS:
            del db.windows[(session_id, window_start)]

        result = make_qc(db, tmp_path).run(session_id=session_id, now=NOW)
        assert result["ok"] is False
        assert any("행이 하나도 없다" in v for v in result["violations"])

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
