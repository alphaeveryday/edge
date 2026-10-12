"""공시 1분 Worker 테스트 (ALPHA-875 PR B).

루프 골격(fence·drain·lane·claim 경합)은 `test_price_worker`·`test_news_worker` 가 이미
덮는다. 여기서는 **이 dataset 만 갖는 축**을 본다 — 전부 "틀려도 초록으로 보이는" 모양이라
반례가 없으면 관측되지 않는다:

1. 날짜창이 **세션 날짜(KST)** 에서 나오는가 — UTC 기본창이면 세션 날짜가 창 밖인데도
   window 는 VALID 로 확정된다(성공 위장).
2. 창 폭이 tick 마다 당일이고 **첫 tick 만** D-1 인가 — 일 콜 총량이 여기서 두 배로 갈린다.
3. 정제가 `raw/` 전량 스캔을 **안 하는가** — 하면 분 단위로 못 돌지만 기능은 정상으로 보인다.
4. 같은 rcept_no 집합을 다시 봤을 때 세대가 유지되는가 — manifest 에 시각·attempt 가 섞이면
   조용히 매 tick 오른다.
5. 다시 읽어도 같은 결과인 문서 거부가 커서를 막지 않되 VALID 로도 접히지 않는가(ALPHA-1154) —
   막으면 하루가 전량 재조회·INCOMPLETE 로 잠기고, 접으면 처리 못 한 공시가 원장에서 사라진다.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake.storage import LocalStorage, minute_poll_manifest_key
from data_pipeline.minute import disclosure_worker as dw
from data_pipeline.minute.commit import MinuteCommitter
from data_pipeline.minute.models import KST, plan_session_windows
from data_pipeline.minute.repository import MinuteLedger
from data_pipeline.minute.states import DATASET_DISCLOSURE_MINUTE, EXTENDED_HOURS_DATASETS

sys.path.insert(0, str(Path(__file__).parent))
from minutefakes import FakeMinuteDB  # noqa: E402

_DB = DbConfig(password="x")
# 세션 날짜를 **오늘이 아닌 고정 날짜**로 둔다 — 이게 이 파일의 핵심 장치다. 벽시계에서
# 유도한 창은 이 날짜와 절대 같아지지 않으므로, 창 단언이 시계 유도 회귀를 실제로 잡는다.
SESSION_DAY = date(2026, 8, 10)
SESSION_DATE = SESSION_DAY.isoformat()
# tick 의 now — 세션 날짜와 **다른 날**이다(상주 Worker 가 자정을 넘겨 돌 수 있다).
NOW = datetime(2026, 8, 11, 2, 30, tzinfo=KST)


class SpyStorage(LocalStorage):
    """`list_keys` 호출 프리픽스를 기록하는 스토리지 — 전량 스캔 부재를 구조로 단언한다."""

    def __init__(self, root):
        super().__init__(root)
        self.listed: list[str] = []

    def list_keys(self, prefix: str) -> list[str]:
        self.listed.append(prefix)
        return super().list_keys(prefix)


class StubSteps:
    """5스텝을 대신하는 스텁 — 무엇을 어떤 인자로 불렀는지 기록한다."""

    def __init__(self, *, rcept_nos=("20260810000001", "20260810000002"),
                 status="success", exit_code=0, raw_keys=None, truncated=False,
                 normalize_exit=0, segment_exit=0, cursor_safe=None,
                 cursor_candidate="20260810000002", cursor_safes=None,
                 cursor_candidates=None, list_total_count=None,
                 normalize_failures=(), segment_failures=(),
                 assemble_exit=0, assemble_report=None):
        self.rcept_nos = tuple(rcept_nos)
        self.status = status
        self.exit_code = exit_code
        self.truncated = truncated
        self.normalize_exit = normalize_exit
        self.segment_exit = segment_exit
        # 실물 스텝이 quality_log 에 쓰고 호출자에게 돌려주는 실패 기록(ALPHA-1154). 기본은
        # 비어 있다 — 종료 코드만 비0 이고 사유가 없으면 워커는 확정 거부로 읽지 않아야 한다.
        self.normalize_failures = list(normalize_failures)
        self.segment_failures = list(segment_failures)
        self.assemble_exit = assemble_exit
        self.assemble_report = assemble_report
        self.cursor_safe = cursor_safe
        self.cursor_candidate = cursor_candidate
        self.cursor_safes = list(cursor_safes or [])
        self.cursor_candidates = list(cursor_candidates or [])
        self.list_total_count = list_total_count
        self.raw_keys = (
            ["raw/source=dart/dataset=disclosures/market=KR/ingest_date=2026-08-10"
             "/run_id=r1/part-00000.ndjson"] if raw_keys is None else raw_keys
        )
        self.collect_windows: list[tuple[str, str]] = []
        self.collect_run_ids: list[str] = []
        self.collect_after: list[str | None] = []
        self.collect_document_indexes: list[dict[str, dict[str, str]] | None] = []
        self.normalize_calls: list[dict] = []
        self.segment_calls: list[dict] = []
        self.load_calls: list[dict] = []
        self.assemble_calls: list[dict] = []

    def collect(self, settings, storage, source, run_id, from_date=None, to_date=None,
                *, ingest_lane, after_rcept_no=None, known_rcept_nos=None,
                previous_total_count=None, existing_documents_by_market=None):
        # 실물 collect() 계약과 동일하게 필수다 — 워커가 이 인자를 빠뜨리면 여기서 죽어야
        # 배치 워터마크(ALPHA-987)의 레인 구분 전제가 픽스처 뒤로 숨지 않는다.
        assert ingest_lane == "minute"
        self.collect_windows.append((from_date, to_date))
        self.collect_run_ids.append(run_id)
        self.collect_after.append(after_rcept_no)
        self.collect_document_indexes.append(existing_documents_by_market)
        index = len(self.collect_after) - 1
        return {
            "exit_code": self.exit_code,
            "log": {"status": self.status, "error": None},
            "rcept_nos": self.rcept_nos,
            "raw_keys": list(self.raw_keys),
            "list_truncated": self.truncated,
            "cursor_safe": (
                self.cursor_safes[index] if index < len(self.cursor_safes)
                else self.cursor_safe if self.cursor_safe is not None
                else self.status not in {"error", "stopped", "skipped"} and not self.truncated
            ),
            "cursor_candidate": (
                self.cursor_candidates[index] if index < len(self.cursor_candidates)
                else self.cursor_candidate
            ),
            "cursor_found": after_rcept_no is not None,
            "list_pages_requested": 1,
            "list_rcept_nos_seen": self.rcept_nos,
            "list_total_count": (
                len(self.rcept_nos) if self.list_total_count is None
                else self.list_total_count
            ),
            "scan_complete": after_rcept_no is None,
            "incremental_stop_reason": (
                None if after_rcept_no is None else "total_unchanged_first_page"
            ),
            "existing_documents_by_market": existing_documents_by_market or {
                "KR": {"20260810000001": "raw/documents/20260810000001.zip"}
            },
        }

    def normalize(self, storage, run_id, input_run_id=None, *, raw_keys=None,
                  failures_out=None):
        self.normalize_calls.append({"run_id": run_id, "input_run_id": input_run_id,
                                     "raw_keys": raw_keys})
        if failures_out is not None:
            failures_out.extend(self.normalize_failures)
        return self.normalize_exit

    def segment(self, storage, run_id, input_run_id=None, *, raw_keys=None,
                failures_out=None):
        self.segment_calls.append({"run_id": run_id, "raw_keys": raw_keys})
        if failures_out is not None:
            failures_out.extend(self.segment_failures)
        return self.segment_exit

    def load(self, storage, run_id, *, db, input_run_id=None, from_date=None, to_date=None):
        self.load_calls.append({"run_id": run_id, "input_run_id": input_run_id,
                                "from": from_date, "to": to_date})
        return 0

    def assemble(self, storage, run_id, *, db, from_date=None, to_date=None, report=None):
        self.assemble_calls.append({"run_id": run_id, "from": from_date, "to": to_date})
        if report is not None and self.assemble_report is not None:
            report.update(self.assemble_report)
        return self.assemble_exit


def install(monkeypatch, steps: StubSteps) -> StubSteps:
    monkeypatch.setattr(dw.ingest_raw_disclosure, "collect", steps.collect)
    monkeypatch.setattr(dw.normalize_disclosure, "run", steps.normalize)
    monkeypatch.setattr(dw.normalize_disclosure_segment, "run", steps.segment)
    monkeypatch.setattr(dw.load_disclosure, "run", steps.load)
    monkeypatch.setattr(dw.assemble_disclosure_events, "run", steps.assemble)
    return steps


def build_worker(db, tmp_path, *, windows=3):
    ledger = MinuteLedger(db=_DB, connect_fn=db.connect)
    # 운영 planner와 같은 정책으로 390창을 만들고 테스트에 필요한 앞부분만 사용한다.
    planned = plan_session_windows(
        SESSION_DAY, universe=None,
        extended_hours=DATASET_DISCLOSURE_MINUTE in EXTENDED_HOURS_DATASETS,
    )
    assert len(planned) == 390, "장외 회수는 배치 소관이므로 상주는 390창이어야 한다"
    session_id, _ = ledger.plan_session(
        dataset=DATASET_DISCLOSURE_MINUTE, source_group="dart", session_date=SESSION_DAY,
        universe_version="disclosure-univ-v1", universe_hash="h" * 64,
        windows=planned[:windows],
    )
    storage = SpyStorage(tmp_path)
    worker = dw.DisclosureWorker(
        session_id=session_id,
        ledger=ledger,
        committer=MinuteCommitter(db=_DB, connect_fn=db.connect),
        storage=storage,
        settings=object(),  # 스텁 스텝은 settings 를 안 본다
        source=object(),
        config=dw.DisclosureWorkerConfig(
            worker_id="dw1", dataset=DATASET_DISCLOSURE_MINUTE, source_code="dart",
            market="KR", session_date=SESSION_DATE, session_day=SESSION_DAY,
            db=_DB, lease_seconds=300,
        ),
    )
    return worker, ledger, session_id, storage


def run_ticks(worker, start, count=4):
    return [worker.tick(start + timedelta(seconds=i)) for i in range(count)]


# ── 1. 날짜창은 세션 날짜에서 나온다 ────────────────────────────

def test_질의_창이_세션_날짜에서_나온다_벽시계가_아니다(tmp_path, monkeypatch):
    """🔴 이 트랙에서 제일 비싼 회귀다.

    `run.py` 의 스케줄 기본창(`default_window(now_utc)`)을 쓰면 08:00 KST = 23:00 UTC(D-1)
    이라 창이 `[D-2, D-1]` 이 되고, **세션 날짜가 창 밖인데 window 는 VALID 로 확정된다.**
    tick 의 now(8/11)와 세션 날짜(8/10)를 일부러 다른 날로 뒀으니, 시계에서 유도하는
    구현은 8/11 을 질의해 이 단언에서 죽는다.
    """
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    worker, _, _, _ = build_worker(db, tmp_path, windows=2)

    run_ticks(worker, NOW)

    assert steps.collect_windows, "수집이 아예 안 불렸다"
    for from_date, to_date in steps.collect_windows:
        # 끝은 **항상** 세션 날짜다 — 벽시계 날짜(2026-08-11)가 새어 들면 여기서 걸린다
        assert to_date == SESSION_DATE
        assert from_date in (SESSION_DATE, "2026-08-09")
    # 적재 창도 같은 축이어야 한다 — 여기만 벽시계면 방금 정제한 파티션을 안 읽는다
    assert steps.load_calls
    for call in steps.load_calls:
        assert call["to"] == SESSION_DATE
    assert steps.assemble_calls
    assert steps.assemble_calls == [
        {key: value for key, value in call.items() if key != "input_run_id"}
        for call in steps.load_calls
    ]
    assert all(call["input_run_id"] == call["run_id"] for call in steps.load_calls)


def test_창_폭은_당일이고_세션_첫_tick만_D_1을_포함한다(tmp_path, monkeypatch):
    """전량 대사의 콜 수는 창 폭에 비례한다. D-1은 중단 캐치업용이라 첫 poll의 내구화가
    끝난 뒤에는 당일 기준을 새로 만들고 이후 대사도 당일만 읽는다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    worker, _, _, storage = build_worker(db, tmp_path, windows=3)

    run_ticks(worker, NOW, count=6)

    assert len(steps.collect_windows) >= 2
    assert steps.collect_windows[0] == ("2026-08-09", SESSION_DATE), "첫 tick 이 D-1 을 안 봤다"
    for later in steps.collect_windows[1:]:
        assert later == (SESSION_DATE, SESSION_DATE), f"첫 tick 이후에 D-1 이 또 붙었다: {later}"


def test_첫_poll은_전량이고_이후에는_접수번호_경계까지만_읽는다(tmp_path, monkeypatch):
    # WHY(ALPHA-1068): 증분 경계가 실제 collect 호출에 안 실리면 상태 필드만 생기고 DART 호출은
    #      여전히 매 분 전량이다. 첫 전량 관측이 안전하게 끝난 뒤부터만 경계를 넘긴다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(cursor_candidate="R2"))
    worker, _, _, storage = build_worker(db, tmp_path, windows=3)

    run_ticks(worker, NOW, count=4)

    # 첫 D-1+D 기준은 다음 D-only 응답과 비교할 수 없으므로 질의 창이 바뀌는 두 번째 poll도 full.
    assert steps.collect_after[:3] == [None, None, "R2"]
    assert steps.collect_document_indexes[0] is None
    assert steps.collect_document_indexes[1] == {
        "KR": {"20260810000001": "raw/documents/20260810000001.zip"}
    }
    manifests = [json.loads(storage.get_bytes(key)) for key in sorted(
        storage.list_keys("operations_archive/minute_manifests/")
    )]
    scopes = [item["observation_scope"]["mode"] for item in manifests]
    assert scopes.count("full") == 2
    assert scopes.count("incremental") == len(scopes) - 2


def test_주기_대사는_증분_경계를_비우고_전량을_다시_본다(tmp_path, monkeypatch):
    # WHY: 접수일만 정렬되는 목록에서 늦게 끼어든 행과 기존 정정은 커서 아래에 올 수 있다.
    #      증분만 영구 반복하지 않고 설정된 주기마다 독립 전량 관측으로 회수해야 한다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(cursor_candidate="R2"))
    worker, _, _, _ = build_worker(db, tmp_path, windows=4)
    worker.config.full_reconcile_every_polls = 1

    run_ticks(worker, NOW, count=6)

    assert steps.collect_after[:4] == [None, None, "R2", None]


@pytest.mark.parametrize(
    "unsafe", ["raw", "ingest_exit", "normalize", "normalize_partial", "load"],
)
def test_내구화_전_실패는_커서를_전진시키지_않고_다음_window가_재시도한다(
        tmp_path, monkeypatch, unsafe):
    # WHY: 대상 본문 저장 또는 completed canonical/pending enqueue 전에 커서가 전진하면,
    #      다음 응답이 달라졌을 때 실패 공시가 경계 뒤에 갇혀 재시도되지 않는다.
    db = FakeMinuteDB()
    steps = StubSteps(
        cursor_candidate="R2",
        cursor_safe=unsafe != "raw",
        exit_code=1 if unsafe == "ingest_exit" else 0,
        normalize_exit=(1 if unsafe == "normalize" else 2 if unsafe == "normalize_partial" else 0),
    )
    install(monkeypatch, steps)
    if unsafe == "load":
        monkeypatch.setattr(dw.load_disclosure, "run", lambda *a, **k: 1)
    worker, _, _, _ = build_worker(db, tmp_path, windows=3)

    run_ticks(worker, NOW, count=4)

    assert len(steps.collect_after) >= 2
    assert steps.collect_after[:2] == [None, None]


def test_안전하지_않은_증분_poll은_다음_window를_즉시_전량_대사한다(tmp_path, monkeypatch):
    # WHY(edge-review): 기존 receipt의 변경 응답이 정제에 실패한 뒤 다음 페이지로 밀릴 수 있다.
    #      같은 total_count의 1페이지 제한을 반복하면 실패 건을 주기 대사 전까지 재시도하지 못한다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(cursor_safe=False))
    worker, _, _, _ = build_worker(db, tmp_path, windows=4)
    worker.prior_day_done = True
    worker.rcept_cursor = "R0"
    worker.baseline_query_window = (SESSION_DATE, SESSION_DATE)
    worker.config.full_reconcile_every_polls = 60

    run_ticks(worker, NOW, count=6)

    assert steps.collect_after[:2] == ["R0", None]


def test_증분_후속_예외도_전량_대사_시계를_멈추지_않는다(tmp_path, monkeypatch):
    # WHY(edge-review): collect가 성공해도 assemble/manifest/commit에서 예외가 날 수 있다.
    #      카운터를 함수 끝에서만 올리면 이 경로가 계속되는 동안 full 대사가 영구 연기된다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    monkeypatch.setattr(
        dw.assemble_disclosure_events, "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("assemble boom")),
    )
    worker, _, _, _ = build_worker(db, tmp_path, windows=4)
    worker.prior_day_done = True
    worker.rcept_cursor = "R0"
    worker.known_list_rcept_nos = {"R0"}
    worker.list_total_count = 1
    worker.baseline_query_window = (SESSION_DATE, SESSION_DATE)
    worker.config.full_reconcile_every_polls = 60

    run_ticks(worker, NOW, count=6)

    assert steps.collect_after[:2] == ["R0", None]
    assert worker.polls_since_full == 1
    assert worker.existing_documents_by_market == {
        "KR": {"20260810000001": "raw/documents/20260810000001.zip"}
    }


def test_assemble_exit_실패도_다음_window를_전량_대사한다(tmp_path, monkeypatch):
    # WHY(edge-review): assemble은 예외를 삼켜 exit 1로 반환한다. 예외 경로만 막으면 이 실패가
    #      안전한 증분으로 오인돼 기준을 복원하므로 실제 exit code까지 내구화 gate에 포함한다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    monkeypatch.setattr(dw.assemble_disclosure_events, "run", lambda *a, **k: 1)
    worker, _, _, _ = build_worker(db, tmp_path, windows=3)
    worker.prior_day_done = True
    worker.rcept_cursor = "R0"
    worker.known_list_rcept_nos = {"R0"}
    worker.list_total_count = 1
    worker.baseline_query_window = (SESSION_DATE, SESSION_DATE)

    run_ticks(worker, NOW, count=4)

    assert steps.collect_after[:2] == ["R0", None]


def test_잘못된_total_count는_증분_기준을_설치하지_않는다(tmp_path, monkeypatch):
    # WHY(edge-review): cursor와 known set만 설치되고 count가 비면 다음 호출이 paired-state
    #      검증에서 API 요청 전에 죽는다. 세 값을 함께 검증해 원자적으로 설치해야 한다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(list_total_count="??"))
    worker, _, _, _ = build_worker(db, tmp_path, windows=2)

    run_ticks(worker, NOW, count=3)

    assert steps.collect_after[:2] == [None, None]
    assert worker.rcept_cursor is None
    assert worker.list_total_count is None
    assert worker.baseline_query_window is None


def test_실패_응답_다음의_변경된_응답이_같은_경계에서_복구된다(tmp_path, monkeypatch):
    # WHY(ALPHA-1058/1068): 첫 응답의 본문 저장 실패 뒤 커서를 넘기면, 다음 응답이 정상으로
    #      바뀌어도 그 공시는 다시 오지 않는다. 실패한 poll은 경계 없이 재시도하고 정상
    #      응답이 canonical/pending까지 내구화된 뒤에만 새 경계를 채택한다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(
        cursor_safes=[False, True], cursor_candidates=["FAILED", "RECOVERED"]
    ))
    worker, _, _, _ = build_worker(db, tmp_path, windows=2)

    run_ticks(worker, NOW, count=3)

    assert steps.collect_after[:2] == [None, None]
    assert worker.rcept_cursor == "RECOVERED"


def test_수집이_실패한_tick은_D_1_캐치업을_소진하지_않는다(tmp_path, monkeypatch):
    """캐치업 플래그를 **커밋** 기준으로 세우면 안 된다 — 수집이 실패한 window 도 INVALID 로
    정상 커밋되므로, 그 기준이면 첫 tick 이 실패한 날 D-1 을 아무도 안 본다. 금요일 저녁
    제출분(`rcept_dt`=휴일 다음 영업일)이 영구 누락되는 경로가 정확히 여기다.

    ⚠️ 한 tick 은 window 하나가 아니다 — realtime 1 + recovery 1 을 처리하므로 호출 **위치**
    가 아니라 성질로 단언한다(그 착각이 이 파일을 처음 빨갛게 만들었다).
    """
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(status="error", exit_code=1))
    worker, _, _, _ = build_worker(db, tmp_path, windows=6)

    worker.tick(NOW)

    assert steps.collect_windows, "수집이 아예 안 불렸다"
    # 실패한 tick 이 몇 window 를 처리했든, 전부 캐치업 창을 질의했고 소진되지 않았다
    assert all(w == ("2026-08-09", SESSION_DATE) for w in steps.collect_windows)
    assert worker.prior_day_done is False, "실패한 tick 이 캐치업 창을 소진했다"

    # 수집이 회복되면 그 tick 이 D-1 을 다시 보고, 그때 비로소 소진된다
    steps.status, steps.exit_code = "success", 0
    before = len(steps.collect_windows)
    worker.tick(NOW + timedelta(seconds=1))

    recovered = steps.collect_windows[before:]
    assert recovered, "회복된 tick 이 아무 window 도 처리하지 않았다"
    assert recovered[0] == ("2026-08-09", SESSION_DATE), "캐치업을 못 본 채 넘어갔다"
    assert worker.prior_day_done is True
    # 같은 tick 의 그 다음 window 는 이미 당일 창이다(소진이 즉시 반영된다)
    assert all(w == (SESSION_DATE, SESSION_DATE) for w in recovered[1:])


def test_partial_은_캐치업을_소진하지_않는다(tmp_path, monkeypatch):
    """partial은 대상 행·본문 또는 감사 로그의 내구화 실패다. 시장 전체의 foreign 진단은
    별도 필드로 분리돼 success를 유지하므로, partial을 관대하게 소비할 이유가 없다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(status="partial", exit_code=1))
    worker, _, _, _ = build_worker(db, tmp_path, windows=6)

    worker.tick(NOW)

    assert worker.prior_day_done is False
    assert all(w == ("2026-08-09", SESSION_DATE) for w in steps.collect_windows)


def test_절단은_캐치업을_소진하지_않는다(tmp_path, monkeypatch):
    """절단은 "창을 다 읽지 못했다"라서 캐치업이 성립하지 않는다 — 예산 상한(주입된
    max_pages)에 닿은 경우가 여기다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(status="partial", exit_code=1, truncated=True))
    worker, _, _, _ = build_worker(db, tmp_path, windows=6)

    worker.tick(NOW)

    assert worker.prior_day_done is False, "절단인데 캐치업을 소진했다"
    assert all(w == ("2026-08-09", SESSION_DATE) for w in steps.collect_windows)


def test_안_봤으면_빈_성공이_아니다(tmp_path, monkeypatch):
    """`skipped`(소스 비활성·매핑 대상 0건)는 exit 0 이라 그냥 두면 VALID_EMPTY 로 접힌다 —
    그러면 하루 390 window 가 **공시 0건인 정상 거래일**로 확정된다. "그 창에 공시가 없었다"와
    "우리가 안 봤다"는 다른 사실이다(Rule 12)."""
    db = FakeMinuteDB()
    install(monkeypatch, StubSteps(status="skipped", rcept_nos=(), raw_keys=[]))
    worker, _, session_id, _ = build_worker(db, tmp_path, windows=1)

    states = run_ticks(worker, NOW)

    confirmed = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"]
    assert [r["data_status"] for r in confirmed] == ["INVALID"]
    assert "WINDOW_FAILED" in states, "안 본 window 가 성공 tick 으로 보고됐다"


def test_세대_불일치는_삼키지_않는다(tmp_path, monkeypatch):
    """`GenerationMismatchError` 는 `CommitRejectedError` 하위가 아니라 맨 RuntimeError 라
    catch-all 이 삼킨다. 공시는 뉴스가 뺀 세대 대조를 **일부러 남긴** dataset 이고, 그 불일치는
    예측 버그 신호다 — 삼키면 매 tick DART poll을 한 번씩 태우며 영원히 돈다. 공용 골격과
    같이 크게 죽어야 한다."""
    from data_pipeline.minute.commit import GenerationMismatchError

    db = FakeMinuteDB()
    install(monkeypatch, StubSteps())
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)
    monkeypatch.setattr(
        worker.committer, "commit_disclosure_window",
        lambda **kw: (_ for _ in ()).throw(GenerationMismatchError("예측 불일치")),
    )

    with pytest.raises(GenerationMismatchError):
        worker.tick(NOW)


# ── 2. 정제 입력 — raw 전량 스캔 부재 ──────────────────────────

def test_정제는_방금_쓴_키만_받고_raw_전량_스캔을_하지_않는다(tmp_path, monkeypatch):
    """`list_keys("raw/")` 는 버킷 전량 스캔이다 — 하루 390 tick 이 그걸 돌 수 없고,
    비용이 레이크 크기에 비례해 자란다. 기능은 정상으로 보이므로 **부재를 구조로** 단언한다
    (프리픽스 목록을 기록해서 — 호출 수를 세면 다른 스캔이 섞여도 통과한다)."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    worker, _, _, storage = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert steps.normalize_calls, "정제가 안 불렸다"
    for call in steps.normalize_calls:
        assert call["raw_keys"] == list(steps.raw_keys)
    for call in steps.segment_calls:
        assert call["raw_keys"] == list(steps.raw_keys)
    # Worker 가 raw 존을 스캔한 적이 없어야 한다
    assert not [p for p in storage.listed if p.startswith("raw/")], storage.listed


def test_실제_정제가_넘겨받은_키만_읽고_raw_를_LIST_하지_않는다(tmp_path):
    """⭐ 위 테스트는 정제를 스텁으로 갈아치우므로 **`raw/` 를 LIST 할 코드가 아예 안 돈다** —
    이름이 말하는 축을 지키지 못한다(리뷰 지적). 그 축은 **진짜** 정제 함수를 불러야만 지켜진다.

    여기서 `normalize_disclosure.run` 을 실물로 부르고 두 가지를 본다: ① 넘겨준 키만 읽는다
    ② `raw/` 프리픽스를 LIST 하지 않는다. 어느 스텝이든 `raw_keys` 를 무시하고 전량 스캔으로
    되돌아가면 이 테스트가 깨진다.
    """
    from data_pipeline.steps import normalize_disclosure, normalize_disclosure_segment

    storage = SpyStorage(tmp_path)
    # 규약에 맞는 raw 메타 키 하나를 실제로 앉힌다(대상 유형이 아니어서 본문 파싱까진 안 간다 —
    # 이 테스트가 보는 축은 **입력 선택**이다).
    key = ("raw/source=dart/dataset=disclosures/market=KR/ingest_date=2026-08-10"
           "/run_id=r1/part-00000.ndjson")
    row = {"rcept_no": "20260810000001", "report_nm": "분기보고서", "corp_code": "00126380",
           "stock_code": "005930", "our_ticker": "005930", "rcept_dt": "20260810",
           "is_target": False, "document_raw_path": None, "fetched_at": "2026-08-10T00:00:00+00:00"}
    storage.put_bytes(key, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    # 넘겨주지 **않은** 두 번째 파티션 — 전량 스캔으로 되돌아가면 이것도 읽힌다
    other = key.replace("run_id=r1", "run_id=r2")
    storage.put_bytes(other, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))

    for step in (normalize_disclosure, normalize_disclosure_segment):
        storage.listed.clear()
        assert step.run(storage, "run-x", "r1", raw_keys=[key]) == 0
        assert not [p for p in storage.listed if p.startswith("raw/")], (
            f"{step.__name__} 가 raw 존을 LIST 했다: {storage.listed}"
        )

    # 넘겨준 키 하나만 읽었다는 것을 quality_log 의 raw_files 로 확인한다(2 면 전량 스캔이다)
    logs = [k for k in storage.list_keys("operations_archive/data_quality_logs/") if k.endswith(".json")]
    assert logs
    for log_key in logs:
        assert json.loads(storage.get_bytes(log_key).decode("utf-8"))["raw_files"] == 1


def test_규약_밖_키를_넘기면_조용히_버리지_않는다(tmp_path):
    """넘겨받은 키를 미리 걸러내면 그 사고가 **사유 없이** 사라진다 — 전건이 걸러진 경우
    `records_read=0` + exit 0 이 되어 호출자가 VALID 로 확정한다(하루 390 window). 규약 밖 키는
    기존 loud 경로(`raw_read_error` + exit 1)로 남아야 한다."""
    from data_pipeline.steps import normalize_disclosure

    storage = SpyStorage(tmp_path)
    bad = "raw/source=dart/dataset=disclosures/oops.ndjson"  # run_id= 없음 = 규약 밖
    storage.put_bytes(bad, b'{"rcept_no":"1"}\n')

    assert normalize_disclosure.run(storage, "run-y", None, raw_keys=[bad]) == 1

    log_key = next(k for k in storage.list_keys("operations_archive/data_quality_logs/")
                   if k.endswith(".json"))
    log = json.loads(storage.get_bytes(log_key).decode("utf-8"))
    assert log["records_failed"] == 1
    assert log["failures"][0]["reasons"] == ["raw_read_error"]


def test_수집이_실패하면_정제와_적재를_부르지_않는다(tmp_path, monkeypatch):
    """수집이 사실상 실패한 창을 정제하면 부분 수집분을 canonical 로 밀어 올린다 —
    다음 tick 이 같은 창을 재독하므로 기다리는 것이 옳다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(status="stopped", exit_code=1))
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    states = run_ticks(worker, NOW)

    assert steps.normalize_calls == [] and steps.load_calls == []
    assert "WINDOW_FAILED" in states, "수집 실패가 tick 상태에 안 실렸다"


def test_raw_가_0건이어도_빈_manifest를_확정하고_적재는_돈다(tmp_path, monkeypatch):
    """적재의 canonical 창 스캔은 **의도된 백로그 회수 경로**다 — 직전 tick 의 정제는 됐는데
    적재가 깨진 경우를 여기서 줍는다. 정제도 빈 완료 manifest로 미실행과 정상 0건을 가른다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(rcept_nos=(), raw_keys=[]))
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert steps.normalize_calls and steps.segment_calls
    assert steps.normalize_calls[0]["raw_keys"] == []
    assert steps.segment_calls[0]["raw_keys"] == []
    assert steps.load_calls, "0건 창에서 적재 회수 경로가 사라졌다"


# ── 2-1. 확정 거부 문서 — 커서 전진과 전건 처리 완료는 다르다 (ALPHA-1154) ──────

_REJECTED_NO = "20260810000001"
_BODY_KEY = "raw/documents/20260810000001.zip"
_RAW_KEY = ("raw/source=dart/dataset=disclosures/market=KR/ingest_date=2026-08-10"
            "/run_id=r1/part-00000.ndjson")


def _normalize_reject(reasons, *, rcept_no=_REJECTED_NO):
    return {"rcept_no": rcept_no, "raw_key": _RAW_KEY, "document_raw_path": _BODY_KEY,
            "reasons": list(reasons)}


def _assemble_report(reasons, *, failures=()):
    """실물 `assemble_disclosure_events.run` 이 quality_log 기록 뒤 돌려주는 형상."""
    return {"skipped_required": 1, "failures": list(failures),
            "skipped_facts": [{"rcept_no": _REJECTED_NO, "fact_id": "dfact_1",
                               "document_id": "doc_1", "report_date": "2026-08-10",
                               "reasons": list(reasons)}]}


# 운영에서 하루를 잠근 세 모양: 10-02 정제 empty_parse, 사업부문 표 없음, 10-01 조립 필수 항목 결손
_CONFIRMED_CASES = {
    "normalize": dict(normalize_exit=2, normalize_failures=[_normalize_reject(
        # 게이트는 blocking 사유와 경고를 한 목록에 싣는다(실측 형상) — 확정 사유가 하나면 족하다
        ["empty_parse", "withheld_counterparty", "missing_amount_and_ratio"])]),
    "segment": dict(segment_exit=2, segment_failures=[
        _normalize_reject(["no_segments_parsed"])]),
    "assemble": dict(assemble_exit=1,
                     assemble_report=_assemble_report(["missing_contract_object"])),
}


@pytest.mark.parametrize("stage", sorted(_CONFIRMED_CASES))
def test_확정_거부_문서는_커서와_D_1_캐치업을_막지_않는다(tmp_path, monkeypatch, stage):
    """WHY(ALPHA-1154): 다시 읽어도 같은 결과인 문서 하나가 커서를 막으면, 그 문서가 질의 창을
    벗어날 때까지 **매 분 이틀치 전량**을 다시 읽고 같은 이유로 다시 실패한다(10-01·10-02 각
    390창, DART 목록 7~13쪽/분 — 평소의 약 10배). 막아서 얻는 것이 없는 실패는 막지 않는다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(cursor_candidate="R2", **_CONFIRMED_CASES[stage]))
    worker, _, _, _ = build_worker(db, tmp_path, windows=4)

    run_ticks(worker, NOW, count=6)

    # 첫 poll(D-1+D)과 창이 바뀐 두 번째 poll 만 전량이고, 그 뒤는 경계 뒤만 읽는다
    assert steps.collect_after[:4] == [None, None, "R2", "R2"]
    assert worker.prior_day_done is True
    assert steps.collect_windows[0] == ("2026-08-09", SESSION_DATE)
    assert all(w == (SESSION_DATE, SESSION_DATE) for w in steps.collect_windows[1:])
    # 정상 문서의 처리는 거부 문서와 무관하게 끝까지 간다 — 적재·조립이 매 poll 돈다
    assert len(steps.load_calls) == len(steps.collect_after)
    assert len(steps.assemble_calls) == len(steps.collect_after)


@pytest.mark.parametrize("stage", sorted(_CONFIRMED_CASES))
def test_거부_문서가_있는_창은_VALID가_아니고_재처리_근거가_manifest에_남는다(
        tmp_path, monkeypatch, stage):
    """WHY: 커서가 나아갔다는 것은 "다시 읽을 이유가 없다"이지 "전건 처리됐다"가 아니다. 거부를
    본 창이 VALID 로 확정되면 그 공시가 fact·이벤트가 되지 못한 사실이 원장에서 사라진다.
    접수번호·단계·사유·원문 위치가 남아야 파서·기준정보를 고친 뒤 그 문서를 찾아 다시 돌린다."""
    db = FakeMinuteDB()
    install(monkeypatch, StubSteps(**_CONFIRMED_CASES[stage]))
    worker, _, session_id, storage = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    confirmed = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"]
    assert [r["data_status"] for r in confirmed] == ["INCOMPLETE"]
    assert worker.rcept_cursor is not None, "이 테스트는 커서가 전진한 창의 표시를 본다"
    manifest = json.loads(storage.get_bytes(confirmed[0]["manifest_uri"]))
    assert manifest["rejected_count"] == 1
    [rejected] = manifest["rejected_documents"]
    assert rejected["stage"] == stage and rejected["rcept_no"] == _REJECTED_NO
    assert set(rejected["reasons"]) & dw._CONFIRMED_REJECT_REASONS
    # 원문 위치 — 정제 거부는 본문 객체 키, 조립 거부는 적재된 fact·문서 ID
    if stage == "assemble":
        assert (rejected["document_id"], rejected["fact_id"]) == ("doc_1", "dfact_1")
    else:
        assert rejected["document_raw_path"] == _BODY_KEY
        # 메타 ndjson 키는 run_id 를 담아 재시도마다 달라진다 — manifest 에 넣으면 같은 관측이
        # 다른 바이트가 되어 세대가 매 시도 오른다(`test_manifest_는_같은_관측이면_같은_바이트다`)
        assert "raw_key" not in rejected


@pytest.mark.parametrize("case", [
    "missing_body", "parse_error", "unknown_reason", "list_row_reason",
    "mixed_with_transient", "no_rcept_no", "no_reasons_reported",
    "assemble_exception", "assemble_log_lost", "assemble_build_error",
    "assemble_reference_data",
])
def test_일시_실패와_모르는_사유는_종전대로_커서를_막는다(tmp_path, monkeypatch, case):
    """WHY: 한 번 실패했다고 확정 거부로 접으면, 다시 읽어 풀릴 문서(본문 미도착·저장 오류)가
    경계 뒤에 갇혀 주기 대사 전까지 재시도되지 않는다(ALPHA-1058 의 재시도 계약). 확정으로 볼
    근거가 사유 코드로 명시되지 않은 실패는 전부 막는 쪽으로 둔다 — 새 사유가 생겨도 같다."""
    transient = {"rcept_no": "20260810000002", "raw_key": _RAW_KEY,
                 "reasons": ["missing_document_body"]}
    options = {
        "missing_body": dict(normalize_exit=2, normalize_failures=[transient]),
        # 본문 객체 읽기 실패가 파싱 예외와 같은 except 로 접힌다 — 일시일 수 있다
        "parse_error": dict(normalize_exit=2, normalize_failures=[
            _normalize_reject(["parse_error"])]),
        "unknown_reason": dict(segment_exit=2, segment_failures=[
            _normalize_reject(["reason_added_next_year"])]),
        # 목록 행에서 오는 값은 다음 poll 이 목록을 다시 읽으면 달라질 수 있다
        "list_row_reason": dict(normalize_exit=2, normalize_failures=[
            _normalize_reject(["bad_report_date"])]),
        # 확정 거부 옆에 일시 실패가 하나라도 있으면 그 단계 전체가 다시 읽을 대상이다
        "mixed_with_transient": dict(normalize_exit=2, normalize_failures=[
            _normalize_reject(["empty_parse"]), transient]),
        # 접수번호가 없으면 나중에 찾아 재처리할 수 없다 — 확정으로 접지 않는다
        "no_rcept_no": dict(normalize_exit=2, normalize_failures=[
            {"raw_key": _RAW_KEY, "reasons": ["empty_parse"]}]),
        "no_reasons_reported": dict(normalize_exit=2),
        # 건너뛴 fact 가 있어도 조립 트랜잭션이 롤백됐으면 그 창의 조립은 다시 해야 한다
        "assemble_exception": dict(assemble_exit=1, assemble_report=_assemble_report(
            ["missing_contract_object"],
            failures=[{"reason": "assembly_error", "error": "boom"}])),
        # quality_log 기록 실패 → 실물 스텝이 report 를 비워 돌려준다(근거가 남지 않았다)
        "assemble_log_lost": dict(assemble_exit=1),
        # 이벤트 유형 미등록 같은 설정 문제 — 문서의 문제가 아니다
        "assemble_build_error": dict(assemble_exit=1, assemble_report=_assemble_report(
            ["event_build_error"])),
        # 발행사 보통주 기준정보가 아직 없다 — 마스터가 채워지면 풀리는데, 캐치업을 소진하면
        # D-1 fact 는 조립 창에서 빠져 다시 시도되지 않는다(저녁 배치는 조립을 안 한다)
        "assemble_reference_data": dict(assemble_exit=1, assemble_report=_assemble_report(
            ["missing_supplier_instrument"])),
    }[case]
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(cursor_candidate="R2", **options))
    worker, _, _, _ = build_worker(db, tmp_path, windows=3)

    run_ticks(worker, NOW, count=4)

    assert len(steps.collect_after) >= 3
    assert all(after is None for after in steps.collect_after), steps.collect_after
    assert worker.prior_day_done is False and worker.rcept_cursor is None
    assert all(w == ("2026-08-09", SESSION_DATE) for w in steps.collect_windows)


def test_거부_문서_옆의_정상_문서는_빠지지_않는다_실제_정제(tmp_path, monkeypatch):
    """WHY: 커서를 풀어 주는 수정이 정상 문서를 잃으면 안 된다. 스텁이 아닌 **실물 정제**로 본다 —
    거부 문서보다 접수번호가 앞·뒤인 문서와, 커서 전진 **뒤에** 도착한 문서가 전부 canonical 에
    들어가는지. 그리고 그동안 같은 거부 문서 때문에 전량을 다시 읽지 않는지."""
    import io
    import zipfile

    from data_pipeline.lake import (canonical_supply_contract_fact_partition,
                                    raw_disclosure_document_key, raw_disclosure_partition)
    from data_pipeline.steps import normalize_disclosure

    good_html = ("<html><head><title>테스트/단일판매ㆍ공급계약체결</title></head><body><table>"
                 "<tr><td>계약상대방</td><td>한화에어로스페이스(주)</td></tr>"
                 "<tr><td>체결계약명</td><td>샘플 공급계약</td></tr>"
                 "<tr><td>계약금액</td><td>1,200,000,000원</td></tr>"
                 "<tr><td>매출액 대비</td><td>12.5</td></tr>"
                 "<tr><td>계약기간</td><td>2024.01.02 ~ 2025.03.04</td></tr>"
                 "</table></body></html>")
    empty_html = "<html><head><title>x/공급</title></head><body>표없음</body></html>"
    before, rejected, after, late = (f"2026081080000{n}" for n in (1, 2, 3, 4))
    bodies = {before: good_html, rejected: empty_html, after: good_html, late: good_html}

    db = FakeMinuteDB()
    # install 이 모듈 속성을 스텁으로 바꾸므로 실물을 **먼저** 잡아 둔다
    real_normalize = normalize_disclosure.run
    steps = install(monkeypatch, StubSteps())
    monkeypatch.setattr(dw.normalize_disclosure, "run", real_normalize)
    worker, _, session_id, storage = build_worker(db, tmp_path, windows=4)
    body_keys = {}
    for rcept_no, html in bodies.items():
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(f"{rcept_no}.xml", html.encode("euc-kr"))
        body_keys[rcept_no] = raw_disclosure_document_key(
            "dart", "KR", SESSION_DATE, "first", rcept_no)
        storage.put_bytes(body_keys[rcept_no], archive.getvalue())

    def collect(settings, storage_, source, run_id, from_date=None, to_date=None, *,
                ingest_lane, after_rcept_no=None, **_):
        # 전량 poll 은 창의 전 문서를, 증분 poll 은 경계 뒤 문서만 자기 run 파티션에 쓴다
        # (실물 `collect` 와 같은 모양 — 본문 객체는 처음 받은 키를 재사용한다).
        steps.collect_after.append(after_rcept_no)
        seen = [before, rejected, after] + ([late] if after_rcept_no is not None else [])
        observed = [no for no in seen if after_rcept_no is None or no > after_rcept_no]
        key = f"{raw_disclosure_partition('dart', 'KR', SESSION_DATE, run_id)}/part-00000.ndjson"
        storage_.put_bytes(key, "".join(json.dumps({
            "report_nm": "단일판매ㆍ공급계약체결", "rcept_no": no, "corp_code": "00406727",
            "corp_name": "테스트기업", "stock_code": "123456", "our_ticker": "123456",
            "rcept_dt": "20260810", "market": "KR", "is_target": True,
            "fetched_at": "2026-08-10T00:00:00+00:00", "document_raw_path": body_keys[no],
        }, ensure_ascii=False) + "\n" for no in observed).encode("utf-8"))
        return {
            "exit_code": 0, "log": {"status": "success", "error": None},
            "rcept_nos": tuple(observed), "raw_keys": [key], "list_truncated": False,
            "cursor_safe": True, "cursor_candidate": seen[-1],
            "cursor_found": after_rcept_no is not None, "list_pages_requested": 1,
            "list_rcept_nos_seen": tuple(seen), "list_total_count": len(seen),
            "scan_complete": after_rcept_no is None, "incremental_stop_reason": None,
        }

    monkeypatch.setattr(dw.ingest_raw_disclosure, "collect", collect)

    run_ticks(worker, NOW, count=6)

    # 같은 거부 문서 때문에 전량을 되풀이하지 않는다 — 세 번째 poll 부터 경계 뒤만 읽는다
    assert steps.collect_after[:3] == [None, None, after]
    assert None not in steps.collect_after[2:]
    # 앞·뒤 문서와 커서 전진 뒤 도착한 문서가 전부 canonical 에 있고, 거부 문서만 없다
    canonical = {
        row["rcept_no"]
        for key in storage.list_keys(canonical_supply_contract_fact_partition("2026-08-10") + "/")
        if key.endswith(".parquet")
        for row in normalize_disclosure._read_parquet_rows(storage.get_bytes(key))
    }
    assert canonical == {before, after, late}
    # 거부를 본 전량 창 둘은 INCOMPLETE 이고 근거가 남는다. 그 뒤 창은 자기 관측 범위만 말한다
    rows = sorted((r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"),
                  key=lambda r: r["manifest_uri"])
    manifests = [json.loads(storage.get_bytes(r["manifest_uri"])) for r in rows]
    full = [m for m in manifests if m["observation_scope"]["mode"] == "full"]
    assert len(full) == 2 and all(m["data_status"] == "INCOMPLETE" for m in full)
    for manifest in full:
        [entry] = manifest["rejected_documents"]
        assert entry["rcept_no"] == rejected and "empty_parse" in entry["reasons"]
        assert entry["document_raw_path"] == body_keys[rejected]
    later = [m for m in manifests if m["observation_scope"]["mode"] != "full"]
    assert later and all(m["rejected_count"] == 0 for m in later)
    assert {m["data_status"] for m in later} <= {"VALID", "VALID_EMPTY"}
    # quality_log 에는 메타 행 위치까지 남는다(run_id 로 찾는다) — 재처리의 입력 넷이 다 있다
    logs = [json.loads(storage.get_bytes(key)) for key in storage.list_keys(
        "operations_archive/data_quality_logs/") if key.endswith(".json")]
    recorded = [f for log in logs if log.get("job_name") == "normalize_disclosure"
                for f in log["failures"]]
    assert recorded and all(
        f["rcept_no"] == rejected and f["document_raw_path"] == body_keys[rejected]
        and f["raw_key"].endswith("part-00000.ndjson") and "empty_parse" in f["reasons"]
        for f in recorded)


# ── 3. 원장 확정 — 상태·checksum·세대 ──────────────────────────

def _window_rows(db, session_id):
    return [row for (sid, _), row in db.windows.items() if sid == session_id]


def test_관측_0건은_VALID_EMPTY_이고_실패로_세지_않는다(tmp_path, monkeypatch):
    """날짜창에 우리 유니버스 공시가 없는 건 정상이다(뉴스형). 실패로 세면 QC 가
    "소스가 죽었다"로 오독한다."""
    db = FakeMinuteDB()
    install(monkeypatch, StubSteps(rcept_nos=(), raw_keys=[]))
    worker, _, session_id, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    confirmed = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"]
    assert [r["data_status"] for r in confirmed] == ["VALID_EMPTY"]
    assert confirmed[0]["failed_unit_count"] == 0
    assert confirmed[0]["record_count"] == 0


def test_하위_스텝_실패는_INCOMPLETE_이고_소스_실패로_세지_않는다(tmp_path, monkeypatch):
    """적재가 깨진 것은 "그 폴링의 산출이 온전치 않다"이지 소스 장애가 아니다 — PR A 가
    INCOMPLETE 를 실패 unit 으로 세지 않는 근거다. VALID 로 접으면 성공 위장이다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    monkeypatch.setattr(dw.load_disclosure, "run",
                        lambda *a, **k: (steps.load_calls.append(k), 1)[1])
    worker, _, session_id, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    confirmed = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"]
    assert [r["data_status"] for r in confirmed] == ["INCOMPLETE"]
    # 소스 단위 실패가 아니다 — 실패 unit 0(그게 PR A 의 유도 규칙이다)
    assert confirmed[0]["failed_unit_count"] == 0


def test_dual_정제_한쪽_부분실패도_다른쪽을_실행하고_INCOMPLETE로_남긴다(tmp_path, monkeypatch):
    # WHY(ALPHA-1044): 공급계약 한 행 실패가 사업부문 성공 manifest 생성을 막으면 두 산출의
    # 계보가 결합된다. 둘 다 실행하되 normalize=2를 window 성공으로 접지 않아야 한다.
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(normalize_exit=2, segment_exit=0))
    worker, _, session_id, storage = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert steps.normalize_calls and steps.segment_calls
    assert steps.load_calls, "완료 manifest가 있는 부분 성공 범위를 적재하지 않았다"
    confirmed = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"]
    assert [r["data_status"] for r in confirmed] == ["INCOMPLETE"]
    manifest_key = next(k for k in storage.list_keys("operations_archive/minute_manifests/")
                        if k.endswith("poll.json"))
    assert json.loads(storage.get_bytes(manifest_key))["step_exits"]["normalize"] == 2
    assert json.loads(storage.get_bytes(manifest_key))["step_exits"]["segment"] == 0


def test_dual_정제_hard_failure는_둘다_실행하되_적재를_막는다(tmp_path, monkeypatch):
    """한 producer의 저장 실패 뒤 canonical 직접 스캔 loader를 돌리면 incomplete 범위가 DB로
    전파된다. 다른 producer는 독립 실행하되 하류 신뢰경계는 두 manifest가 모두 있어야 열린다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps(normalize_exit=1, segment_exit=0))
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert steps.normalize_calls and steps.segment_calls
    assert steps.load_calls == []


def test_dual_정제_한쪽_예외도_다른쪽을_실행하고_적재를_막는다(tmp_path, monkeypatch):
    """WHY(ALPHA-1044): producer가 exit 1로 접지 못한 예외도 다른 manifest 계보를 막으면
    안 된다. 다만 한쪽 manifest가 incomplete이므로 canonical loader 신뢰경계는 열지 않는다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    monkeypatch.setattr(
        dw.normalize_disclosure, "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("manifest put failed")),
    )
    worker, _, _, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    assert steps.segment_calls
    assert steps.load_calls == []


def test_소스_실패는_INVALID_이고_실패_unit_이_소스다(tmp_path, monkeypatch):
    db = FakeMinuteDB()
    install(monkeypatch, StubSteps(status="error", exit_code=1))
    worker, _, session_id, _ = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    confirmed = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"]
    assert [r["data_status"] for r in confirmed] == ["INVALID"]
    assert confirmed[0]["failed_unit_count"] == 1
    assert confirmed[0]["missing_units"] == ["dart"]


def test_checksum_은_rcept_집합이라_fetched_at_에_흔들리지_않는다(tmp_path, monkeypatch):
    """raw 메타 바이트를 해시하면 `fetched_at` 이 매 tick 달라 세대가 영원히 오른다.
    checksum 은 관측 **집합** 이어야 하고, 순서가 달라도 같아야 한다."""
    db = FakeMinuteDB()
    install(monkeypatch, StubSteps(rcept_nos=("20260810000002", "20260810000001")))
    worker, _, session_id, _ = build_worker(db, tmp_path, windows=1)
    run_ticks(worker, NOW)
    first = [r for r in _window_rows(db, session_id) if r["data_status"] != "DUE"][0]

    # 같은 집합을 **다른 순서**로 다시 본다 — 새 세션·새 워커, 같은 관측
    db2 = FakeMinuteDB()
    install(monkeypatch, StubSteps(rcept_nos=("20260810000001", "20260810000002")))
    worker2, _, session2, _ = build_worker(db2, tmp_path / "b", windows=1)
    run_ticks(worker2, NOW)
    second = [r for r in _window_rows(db2, session2) if r["data_status"] != "DUE"][0]

    assert first["checksum"] == second["checksum"]
    assert first["record_count"] == second["record_count"] == 2


def test_manifest_는_같은_관측이면_같은_바이트다(tmp_path):
    """세대는 records checksum **과** manifest checksum 이 둘 다 불변일 때만 유지된다
    (`repository._record_window_outcome_tx` 의 CASE). manifest 에 시각·attempt·run_id 를
    담으면 같은 rcept_no 집합을 다시 봐도 세대가 매 tick 오르고, PR A 가 세대 대조를 남긴
    의도(claim↔commit 사이의 다른 attempt 탐지)가 무의미해진다."""
    common = dict(
        dataset=DATASET_DISCLOSURE_MINUTE, session_id="s1", source_code="dart",
        window_start=datetime(2026, 8, 10, 9, 0, tzinfo=KST),
        window_end=datetime(2026, 8, 10, 9, 1, tzinfo=KST),
        query_from=SESSION_DATE, query_to=SESSION_DATE,
        rcept_nos=("20260810000001",), data_status="VALID",
        step_exits={"ingest": 0, "load": 0},
        observation_scope={"mode": "full", "after_rcept_no": None,
                           "cursor_found": False, "pages_requested": 2},
        rejected_documents=[{"stage": "normalize", "rcept_no": "20260810000001",
                             "reasons": ["empty_parse"],
                             "document_raw_path": "raw/documents/20260810000001.zip"}],
    )
    first = dw.build_poll_manifest(**common)
    second = dw.build_poll_manifest(**common)
    assert first == second
    # 시각류 키가 새로 들어오면 이 단언이 깨진다 — 그게 이 테스트의 목적이다
    assert not [k for k in first if "_at" in k or k in ("attempt", "run_id")], first


def test_세대_예측은_원장_규칙과_같다():
    """어긋나면 `GenerationMismatchError` 로 트랜잭션이 rollback 된다 — 조용히 맞추려
    들면(항상 +1) 세대가 관측 identity 를 못 나타낸다."""
    claim = {"generation": 3, "checksum": "c", "manifest_checksum": "m"}
    assert dw._predict_generation(claim, "c", "m") == 3       # 둘 다 불변 → 유지
    assert dw._predict_generation(claim, "c2", "m") == 4      # records 변화 → +1
    assert dw._predict_generation(claim, "c", "m2") == 4      # manifest 만 변화 → +1
    # 첫 확정(빈 window: checksum NULL·generation 0) → 1
    fresh = {"generation": 0, "checksum": None, "manifest_checksum": None}
    assert dw._predict_generation(fresh, "c", "m") == 1


def test_manifest_키는_attempt_축이라_재시도가_불변_위반이_아니다(tmp_path, monkeypatch):
    """세대 축 키를 쓰면 commit 실패 뒤 재poll 이 같은 키에 다른 바이트를 PUT 해
    그 window 가 영구히 막힌다(뉴스와 같은 근거). 실제로 attempt 자리에 쓰였는지 본다."""
    db = FakeMinuteDB()
    install(monkeypatch, StubSteps())
    worker, ledger, session_id, storage = build_worker(db, tmp_path, windows=1)

    run_ticks(worker, NOW)

    window_start = ledger.session_window_rows(session_id=session_id)[0][0]
    attempt = db.windows[(session_id, window_start)]["attempt_count"]
    key = minute_poll_manifest_key(
        DATASET_DISCLOSURE_MINUTE, "dart", "KR", SESSION_DATE,
        window_start.astimezone(KST).strftime("%H%M"), attempt,
    )
    # `parse_manifest` 는 **가격·iNAV** window manifest 검증자다(artifact_key·units 를
    # 요구한다) — 공시는 window 산출물이 없어 그 형상이 아니다. 뉴스 poll manifest 와
    # 같은 축이라 그쪽처럼 그대로 읽는다.
    manifest = json.loads(storage.get_bytes(key).decode("utf-8"))
    assert manifest["rcept_nos"] == ["20260810000001", "20260810000002"]
    assert manifest["query_window"] == ["2026-08-09", SESSION_DATE]
    assert manifest["observation_scope"]["mode"] == "full"
    assert manifest["step_exits"] == {
        "assemble": 0, "ingest": 0, "load": 0, "normalize": 0, "segment": 0}


def test_run_id_는_session_window_attempt_에서_결정적으로_나온다(tmp_path, monkeypatch):
    """run_id 가 raw 파티션·collection_log·quality_log 키에 들어간다 — 랜덤이면 재시도가
    매번 새 파티션을 남겨 정제 입력이 중복되고 레이크에 고아 객체가 쌓인다."""
    db = FakeMinuteDB()
    steps = install(monkeypatch, StubSteps())
    worker, _, _, _ = build_worker(db, tmp_path, windows=2)
    run_ticks(worker, NOW)

    # window 가 다르면 run_id 도 달라야 한다(같으면 두 window 가 한 파티션을 다툰다)
    assert len(set(steps.collect_run_ids)) == len(steps.collect_run_ids)
    # 정제·적재가 **수집과 같은 run_id** 를 받아야 그 런의 raw 만 스코프된다
    for i, call in enumerate(steps.normalize_calls):
        assert call["run_id"] == call["input_run_id"] == steps.collect_run_ids[i]


# ── 4. 설정 게이트 ────────────────────────────────────────────

def test_lease_가_최악_tick_을_못_덮으면_load_에서_죽는다():
    """현 SFN 은 슬롯 간격 3600초가 이 축을 통째로 가리고 있었다 — 1분 레인에서는
    in-flight claim 이 만료돼 recovery lane 이 같은 window 를 재청구하고, 원래 attempt 의
    commit 이 거부된다(ALPHA-706).

    ⚠️ 상수 초(22초 등)를 박지 않는다 — 공시의 window 비용은 **창 폭·일 건수 파생값**이라
    창을 당일로 좁히면 낡는다. 그래서 페이지·본문 예산에서 유도한다.
    """
    from pydantic import ValidationError

    from data_pipeline.config.models import MinuteDisclosureWorkerConfig as Cfg

    assert Cfg().lease_seconds == 300  # 기본은 자기 검증을 통과한다

    # 예산을 늘리면 최악 tick 이 lease 를 넘어 **load 시점에** 죽는다
    with pytest.raises(ValidationError, match="tick 최악 소요"):
        Cfg(max_pages_per_window=200)
    # 간격을 벌리는 것도 같은 축이다(pacing 을 올리면 window 가 길어진다)
    with pytest.raises(ValidationError, match="tick 최악 소요"):
        Cfg(min_interval_sec=8.0)
    # fence 는 heartbeat 주기 + 최악 tick 을 덮어야 한다(절반 규칙으로는 안 잡힌다)
    with pytest.raises(ValidationError, match="fence 가 처리 중 만료"):
        Cfg(lease_seconds=3600, session_lease_seconds=60, max_pages_per_window=100)


def test_recovery_0_은_금지다():
    """DRAINING 수렴은 recovery lane 만 연다(만료 고아 CLAIMED 회수) — 0 이면 ack_drain 이
    영구 거부돼 세션이 DRAINING 에 고착되고 EOD 가 영영 시작되지 않는다."""
    from pydantic import ValidationError

    from data_pipeline.config.models import MinuteDisclosureWorkerConfig as Cfg

    with pytest.raises(ValidationError):
        Cfg(recovery_budget_per_tick=0)


def _cli_settings(tmp_path, **worker_overrides):
    """`disclosure_worker_cli` 가 읽는 최소 설정 — 실제 config 모델을 쓴다(스텁 필드가
    실물과 갈리면 배선 테스트가 배선을 안 본다)."""
    from types import SimpleNamespace

    from data_pipeline.config.models import (
        DartDisclosureConfig,
        DartDisclosureSource,
        MinuteDisclosureWorkerConfig,
        StorageConfig,
    )

    return SimpleNamespace(
        db=_DB,
        dart_disclosure=DartDisclosureConfig(
            source=DartDisclosureSource(api_key="k" * 40)
        ),
        minute_disclosure_worker=MinuteDisclosureWorkerConfig(**worker_overrides),
        storage=StorageConfig(local_root=str(tmp_path)),
    )


def test_cli_가_pacing_과_페이지_예산을_실제로_배선한다(tmp_path, monkeypatch):
    """⭐ 필드가 존재한다는 단언은 배선을 안 본다 — `PoliteClient(min_interval=…)` 가 빠져도
    통과한다(리뷰 지적). 여기서는 **만들어진 소스**를 붙잡아 확인한다.

    특히 `max_pages_per_window` 는 **소스의 `max_pages` 로 주입**돼야 한다. 안 되면 실제
    순회 상한은 벤더 섹션의 500 이고 lease 검증은 예산(60)으로 계산하니, 접수 급증일에
    검증은 초록인 채 tick 이 lease 를 넘어 claim 을 탈취당한다(ALPHA-706 의 그 모드).
    """
    captured = {}

    class StubWorker:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def request_stop(self):
            pass

        def tick(self, now):
            return "DRAINED"

    monkeypatch.setattr(dw, "DisclosureWorker", StubWorker)
    monkeypatch.setattr(MinuteLedger, "session_snapshot", lambda self, **kw: {"phase": "ACTIVE"})

    settings = _cli_settings(tmp_path, min_interval_sec=2.5, timeout_sec=33.0,
                             max_pages_per_window=40, lease_seconds=900,
                             session_lease_seconds=1200)
    assert dw.disclosure_worker_cli(settings, session_date=SESSION_DATE, max_ticks=1) == 0

    source = captured["source"]
    assert source.client.min_interval == 2.5, "pacing 이 PoliteClient 에 안 실렸다"
    assert source.client.timeout == 33.0
    # 예산이 실제 순회 상한이 됐다 — 벤더 기본 500 이 아니다
    assert source.max_pages == 40, f"예산이 소스에 주입되지 않았다: {source.max_pages}"
    # 배치 경로의 정본은 안 바뀐다(같은 객체를 고쳐 쓰면 배치가 같이 좁아진다)
    assert settings.dart_disclosure.source.max_pages == 500
    # 유형 필터는 배치와 공유하는 정본을 그대로 쓴다
    assert source.report_name_filters == ["공급계약", "사업보고서"]


def test_cli_가_안_보는_상태로_기동하지_않는다(tmp_path, monkeypatch):
    """키가 없거나 소스가 꺼져 있으면 매 window 가 아무것도 관측하지 못한다 — `_classify` 가
    INVALID 로 잡지만 그건 마지막 방어선이고, 하루가 전건 INVALID 로 도는 건 정상이 아니다.
    `news_worker_cli` 가 같은 이유로 같은 게이트를 둔다."""
    monkeypatch.setattr(MinuteLedger, "session_snapshot", lambda self, **kw: {"phase": "ACTIVE"})

    no_key = _cli_settings(tmp_path)
    no_key.dart_disclosure.source.api_key = None
    with pytest.raises(SystemExit, match="api_key 없음"):
        dw.disclosure_worker_cli(no_key, session_date=SESSION_DATE, max_ticks=1)

    disabled = _cli_settings(tmp_path)
    disabled.dart_disclosure.source.enabled = False
    with pytest.raises(SystemExit, match="enabled=false"):
        dw.disclosure_worker_cli(disabled, session_date=SESSION_DATE, max_ticks=1)


def test_cli_가_예산_부족을_기동에서_거른다(tmp_path, monkeypatch):
    """예산이 실제 상한으로 주입되므로, 평상시 물량보다 작으면 **매 window 가 절단된다** —
    그건 조용한 데이터 손실이라 기동에서 죽는다. 두 섹션이 만나는 자리는 여기뿐이다
    (pydantic 검증자는 섹션을 못 넘는다)."""
    monkeypatch.setattr(MinuteLedger, "session_snapshot", lambda self, **kw: {"phase": "ACTIVE"})

    # 하루 1,100건 × 2일 ÷ page_count 100 = 22 페이지가 평상시 필요분이다
    settings = _cli_settings(tmp_path, max_pages_per_window=10)
    with pytest.raises(SystemExit, match="평상시 2일 창 페이지"):
        dw.disclosure_worker_cli(settings, session_date=SESSION_DATE, max_ticks=1)


def test_pacing_손잡이가_있다():
    """종전엔 `run.py` 가 `PoliteClient()` 를 인자 없이 만들어 재배포 없이는 못 조였다.
    DART 앱키를 세 스텝이 공유하고 `"020" 일 사용한도 초과`가 STOP 코드라, 조일 수단이
    없으면 한도에 닿는 순간 레인이 선다."""
    from data_pipeline.config.models import MinuteDisclosureWorkerConfig as Cfg

    # 기본은 종전 무인자 PoliteClient 와 **같은 값**이다 — 이 PR 이 유량을 바꾸지 않는다
    assert Cfg().min_interval_sec == 1.0 and Cfg().timeout_sec == 10.0
    # 조이는 것은 공짜가 아니다 — 간격을 벌리면 window 가 길어져 lease 를 같이 올려야 한다.
    # (그 결합이 곧 검증자의 존재 이유다. 예산이 실제 상한으로 주입되므로 이 산수는 실물이다.)
    assert Cfg(min_interval_sec=2.5, lease_seconds=600).min_interval_sec == 2.5
