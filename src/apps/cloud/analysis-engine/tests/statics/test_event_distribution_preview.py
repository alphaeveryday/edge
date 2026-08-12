"""Event-distribution previews must keep one anchored event and a PIT-safe history."""
from __future__ import annotations

from types import SimpleNamespace

import duckdb
import pytest

from edge_analysis.statics.hypothesize import (_EVENT_DISTRIBUTION_PREVIEW_SYSTEM,
                                               propose)
from edge_analysis.statics.hypothesis_preview import (EventCandidate,
                                                       EventDistributionPreview,
                                                       EventDistributionPreviewResult,
                                                       HypothesisPreviewRuntime,
                                                       PreviewExecutionError,
                                                       event_distribution_preview)


class _Lake:
    """Production preview must use the lake's PIT SQL boundary, never ``con``."""

    def __init__(self) -> None:
        con = duckdb.connect()
        self._con = con
        self.queries: list[str] = []
        # 운영과 같은 형이다: RDB available_at 은 TIMESTAMPTZ 이고 duck 세션은
        # Asia/Seoul 이다(statics/duck.py). naive TIMESTAMP 픽스처는 TZ 캐스팅
        # 오류(TIMESTAMPTZ→TIME 직접 캐스팅 불가)를 숨긴다.
        con.execute("SET TimeZone='Asia/Seoul'")
        con.execute("""
            CREATE TABLE event_rows(
                source_event_id TEXT, instrument_id TEXT, event_type_code TEXT,
                trade_date DATE, available_at TIMESTAMPTZ
            )
        """)
        con.execute("""
            CREATE TABLE daily_rows(instrument_id TEXT, trade_date DATE, ar DOUBLE)
        """)
        con.execute("""
            INSERT INTO event_rows VALUES
              ('old_a', 'A', 'CONTRACT.CANCEL', DATE '2026-07-01', TIMESTAMP '2026-07-01 10:00:00'),
              ('old_a_duplicate', 'A', 'CONTRACT.CANCEL', DATE '2026-07-01', TIMESTAMP '2026-07-01 11:00:00'),
              ('old_b', 'B', 'CONTRACT.CANCEL', DATE '2026-07-02', TIMESTAMP '2026-07-02 10:00:00'),
              ('other_type', 'C', 'CONTRACT.SIGN', DATE '2026-07-03', TIMESTAMP '2026-07-03 10:00:00'),
              ('future', 'D', 'CONTRACT.CANCEL', DATE '2026-07-04', TIMESTAMP '2026-08-08 09:00:00'),
              ('anchor', 'A', 'CONTRACT.CANCEL', DATE '2026-08-07', TIMESTAMP '2026-08-07 10:31:00'),
              ('anchor', 'COUNTERPARTY', 'CONTRACT.CANCEL', DATE '2026-08-07', TIMESTAMP '2026-08-07 10:31:00')
        """)
        con.execute("""
            INSERT INTO daily_rows VALUES
              ('A', DATE '2026-07-01', -0.01),
              ('B', DATE '2026-07-02',  0.02),
              ('C', DATE '2026-07-03', -0.90),
              ('D', DATE '2026-07-04', -0.80),
              ('A', DATE '2026-08-07', -0.03)
        """)

    def sql(self, query: str) -> list[tuple]:
        assert query.startswith("WITH")
        self.queries.append(query)
        return self._con.execute(query).fetchall()


def _lake() -> _Lake:
    return _Lake()


def test_distribution_preview_binds_anchor_to_same_type_deduplicated_pit_history(monkeypatch):
    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview._base",
                        lambda *_args: """WITH
                        v_event AS (
                            SELECT * FROM event_rows
                            WHERE available_at <= TIMESTAMP '2026-08-07 12:05:00'
                        ),
                        v_daily AS (SELECT * FROM daily_rows)
                        """)
    lake = _lake()
    result = event_distribution_preview(
        lake, source_event_id="anchor", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", today=-0.036, min_n=2,
    )

    assert result.status == "READY" and result.reason == "READY"
    preview = result.distribution
    assert preview is not None
    assert preview.n == 2
    assert preview.mean == 0.005
    assert preview.today == -0.036
    assert preview.percentile == 0.0
    assert len(lake.queries) == 2, "today must come from the committed minute return, never v_daily"


def test_distribution_preview_uses_the_supplied_committed_minute_observation():
    class _Lake:
        def sql(self, _query: str) -> list[tuple]:
            raise AssertionError("missing current minute return must not query v_daily")

    result = event_distribution_preview(
        _Lake(), source_event_id="anchor", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", today=None, min_n=2,
    )
    assert result.status == "UNAVAILABLE"
    assert result.reason == "TODAY_RETURN_UNAVAILABLE"


def test_distribution_preview_names_missing_anchor_and_thin_history(monkeypatch):
    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview._base",
                        lambda *_args: """WITH
                        v_event AS (SELECT * FROM event_rows),
                        v_daily AS (SELECT * FROM daily_rows)
                        """)
    missing = event_distribution_preview(
        _lake(), source_event_id="missing", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", today=-0.036, min_n=2)
    thin = event_distribution_preview(
        _lake(), source_event_id="anchor", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", today=-0.036, min_n=4)

    assert (missing.reason, missing.anchor_count) == ("ANCHOR_NOT_FOUND", 0)
    # 표본 2 = A(07-01)·B(07-02). 'future'(관측 2026-08-08) 는 실효 거래일이
    # 달력에 없어 제외된다 — 명목일 조인이던 시절엔 미래 관측 사건이 과거 표본에
    # 새던 PIT 누수였다(ALPHA-932 실효 거래일 축).
    assert (thin.reason, thin.historical_n, thin.min_n) == (
        "HISTORY_BELOW_MIN", 2, 4)


def test_distribution_preview_excludes_non_finite_history(monkeypatch):
    """NaN 과거 수익률은 표본이 아니다 — 비교가 전부 False 라 mean·percentile 을
    조용히 오염시키고도 READY 로 통과한다. 유한값만 세어 min_n 미달이면 분포가
    없는 것과 같아야 한다."""
    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview._base",
                        lambda *_args: """WITH
                        v_event AS (SELECT * FROM event_rows),
                        v_daily AS (SELECT * FROM daily_rows)
                        """)
    lake = _lake()
    lake._con.execute("""
        INSERT INTO event_rows VALUES
          ('old_e', 'E', 'CONTRACT.CANCEL', DATE '2026-07-05',
           TIMESTAMP '2026-07-05 10:00:00')
    """)
    lake._con.execute(
        "INSERT INTO daily_rows VALUES ('E', DATE '2026-07-05', 'NaN'::DOUBLE)")

    result = event_distribution_preview(
        lake, source_event_id="anchor", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", today=-0.036, min_n=3)

    # 표본 후보 3 = A(07-01)·B(07-02)·E(07-05, NaN). NaN 행이 유한값처럼 세어지면
    # 3건이 되어 READY 로 뒤집힌다 — 그 회귀가 이 단언을 깨뜨린다.
    assert (result.status, result.reason, result.historical_n) == (
        "UNAVAILABLE", "HISTORY_BELOW_MIN", 2)


def test_weekend_event_anchors_to_the_next_trading_day(monkeypatch):
    """일요일(2026-08-09) 사건은 월요일(08-10) 설명에서 앵커가 선다 — event_date=
    설명일 요구가 주말·전일 사건을 전멸시키던 ALPHA-932 의 회귀 가드. 반대로 이미
    지난 거래일에 시장이 반응한 사건(금요일 08-07 장중)은 오늘과 비교할 수 없어
    ANCHOR_NOT_CURRENT."""
    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview._base",
                        lambda *_args: """WITH
                        v_event AS (SELECT * FROM event_rows),
                        v_daily AS (SELECT * FROM daily_rows)
                        """)
    lake = _lake()
    lake._con.execute("""
        INSERT INTO event_rows VALUES
          ('sunday', 'A', 'CONTRACT.CANCEL', DATE '2026-08-09',
           TIMESTAMP '2026-08-09 15:26:00'),
          ('old_sat', 'F', 'CONTRACT.CANCEL', DATE '2026-07-04',
           TIMESTAMP '2026-07-04 12:00:00')
    """)
    # F 의 다음 거래일(07-06) 봉 — 명목일(토요일 07-04) 조인이면 이 표본이 없다.
    lake._con.execute(
        "INSERT INTO daily_rows VALUES ('F', DATE '2026-07-06', 0.05)")

    sunday = event_distribution_preview(
        lake, source_event_id="sunday", instrument_id="A", day="2026-08-10",
        as_of="2026-08-10 09:44:00", today=-0.02, min_n=2)
    stale = event_distribution_preview(
        lake, source_event_id="anchor", instrument_id="A", day="2026-08-10",
        as_of="2026-08-10 09:44:00", today=-0.02, min_n=2)

    # 일요일 15:26 관측 → 실효 거래일 = 다음 거래일(월요일 = 설명일). 달력에
    # 08-08(토)·08-09(일) 거래일이 없으므로 currency 판정이 주말을 건너뛴다.
    # 과거 표본 4 = A(07-01)·B(07-02)·A(08-07 — 'anchor' 사건의 실효 거래일)·
    # F(07-06 — 토요일 사건이 다음 거래일 봉으로 매핑된 표본. 명목일 조인 회귀는
    # 이 표본을 잃어 n=3 이 된다).
    assert sunday.status == "READY"
    assert sunday.distribution is not None and sunday.distribution.n == 4
    # 금요일(08-07) 장중 사건은 그날 세션이 이미 완결됐다 — 월요일 수익률과
    # 비교하면 사건-반응 축이 어긋난다.
    assert (stale.status, stale.reason) == ("UNAVAILABLE", "ANCHOR_NOT_CURRENT")


def test_effective_day_close_boundary_and_instrument_calendar(monkeypatch):
    """두 변이를 죽이는 표본: ① 15:30 **정각** 관측은 당일 종가에 선행할 수 없어
    다음 거래일로 밀린다(> 로 되돌리면 G 의 표본값이 0.09→0.07 로 어긋나 mean 이
    깨진다) ② 실효 거래일 달력은 **그 종목의** 거래일이다(전 시장 달력로 되돌리면
    거래정지였던 H 가 자기 봉 없는 날에 매핑돼 표본에서 사라져 n 이 깨진다)."""
    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview._base",
                        lambda *_args: """WITH
                        v_event AS (SELECT * FROM event_rows),
                        v_daily AS (SELECT * FROM daily_rows)
                        """)
    lake = _lake()
    lake._con.execute("""
        INSERT INTO event_rows VALUES
          ('at_close', 'G', 'CONTRACT.CANCEL', DATE '2026-07-01',
           TIMESTAMP '2026-07-01 15:30:00'),
          ('h_event', 'H', 'CONTRACT.CANCEL', DATE '2026-07-06',
           TIMESTAMP '2026-07-06 10:00:00')
    """)
    lake._con.execute("""
        INSERT INTO daily_rows VALUES
          ('G', DATE '2026-07-01', 0.07),
          ('G', DATE '2026-07-02', 0.09),
          ('X', DATE '2026-07-07', 0.99),
          ('H', DATE '2026-07-08', 0.11)
    """)

    result = event_distribution_preview(
        lake, source_event_id="anchor", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", today=-0.036, min_n=4)

    # 표본 4 = A(07-01, -0.01)·B(07-02, 0.02)·G(정각 관측 → 07-02, 0.09)·
    # H(자기 재개일 07-08, 0.11 — 전 시장 달력이면 X 의 07-07 로 매핑돼 봉이 없어
    # 탈락한다). mean 은 G 가 0.09(다음날)일 때만 맞는다.
    assert result.status == "READY"
    assert result.distribution is not None
    assert result.distribution.n == 4
    assert result.distribution.mean == pytest.approx(0.0525)


def test_distribution_preview_fails_loudly_when_the_pit_surface_is_unavailable(monkeypatch):
    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview._base", lambda *_args: "WITH x AS")

    class _UnavailableLake:
        def sql(self, _query: str) -> list[tuple]:
            raise RuntimeError("database unavailable")

    with pytest.raises(PreviewExecutionError, match="EVENT_DISTRIBUTION_UNAVAILABLE"):
        event_distribution_preview(
            _UnavailableLake(), source_event_id="anchor", instrument_id="A", day="2026-08-07",
            as_of="2026-08-07 12:05:00", today=-0.036, min_n=2,
        )


def test_llm_can_only_submit_a_ready_current_event_distribution_preview(monkeypatch):
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )
    def preview(*_args, **kwargs):
        assert kwargs["today"] == -0.036
        distribution = EventDistributionPreview(
            "anchor", "A", "CONTRACT.CANCEL", 41, -0.031, -0.036, 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    candidate_id = next(iter(runtime._candidate_by_id))
    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"tool": "hypothesis.preview", "arguments": {
            "candidate_id": candidate_id,
            "outcome_id": "outcome:market_adjusted_return_day_0",
        }},
        {"hypotheses": [{"preview_handle": None, "intent": "계약 해지의 과거 반응을 확인한다."}]},
    ))
    systems: list[str] = []

    def ask(system, _user):
        systems.append(system)
        reply = next(replies)
        if "hypotheses" in reply:
            reply["hypotheses"][0]["preview_handle"] = next(iter(runtime._previews))
        return reply

    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    assert rejected == []
    assert len(valid) == 1
    assert valid[0].preview_handle.startswith("hpr_")
    assert runtime.distribution_attempts()["anchor"] == {
        "preview_status": "READY", "preview_reason": "READY",
        "historical_n": 41, "min_n": 30,
        "handle": valid[0].preview_handle,
    }
    assert "event_candidates" in systems[0]
    assert "exposure_id" not in systems[0]


def test_llm_can_preview_and_submit_up_to_three_distinct_events(monkeypatch):
    """복수 후보 preview·복수 제출(상한 3, ALPHA-938) — 런당 한 사건만 고르라던
    프롬프트가 스레드-런 그룹 67개 중 6개(9%)만 preview 하게 만들던 실측의 해소.
    서로 다른 두 사건이 각각 READY preview 를 받아 한 hypotheses 배열로 제출된다."""
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("evt_a", "thread_a", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
            EventCandidate("evt_b", "thread_b", "B", "증설 발표",
                           "2026-08-07T11:02:00"),
        ), current_event_returns={"A": -0.036, "B": 0.021},
    )

    def preview(*_args, **kwargs):
        source_event_id = kwargs["source_event_id"]
        distribution = EventDistributionPreview(
            source_event_id, kwargs["instrument_id"], "CONTRACT.CANCEL",
            41, -0.031, kwargs["today"], 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    by_event = {c.source_event_id: cid
                for cid, c in runtime._candidate_by_id.items()}
    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"tool": "hypothesis.preview", "arguments": {
            "candidate_id": by_event["evt_a"],
            "outcome_id": "outcome:market_adjusted_return_day_0",
        }},
        {"tool": "hypothesis.preview", "arguments": {
            "candidate_id": by_event["evt_b"],
            "outcome_id": "outcome:market_adjusted_return_day_0",
        }},
        {"hypotheses": [
            {"preview_handle": None, "intent": "계약 해지의 과거 반응을 확인한다."},
            {"preview_handle": None, "intent": "증설 발표의 과거 반응을 확인한다."},
        ]},
    ))
    systems: list[str] = []

    def ask(system, _user):
        systems.append(system)
        reply = next(replies)
        if "hypotheses" in reply:
            for hypothesis, handle in zip(reply["hypotheses"], runtime._previews):
                hypothesis["preview_handle"] = handle
        return reply

    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    assert rejected == []
    assert len(valid) == 2
    assert {v.preview_handle for v in valid} == set(runtime._previews)
    # 두 후보 모두 시도가 원장에 남는다 — 퍼널이 스레드 단위로 접힌다.
    attempts = runtime.distribution_attempts()
    assert attempts["evt_a"]["preview_status"] == "READY"
    assert attempts["evt_b"]["preview_status"] == "READY"
    # 프롬프트 계약: 복수 상한이 명시돼야 모델이 두 번째 후보를 시도한다.
    assert "최대 3개" in systems[0]


def test_submissions_beyond_the_cap_are_rejected_with_reason(monkeypatch):
    """상한 3은 프롬프트 계약이 아니라 서버 게이트다 — 4개 제출이 오면 앞 3개만
    수용하고 초과분은 사유째 원장 행이 된다(프롬프트만으로는 게이트가 아니다)."""
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=tuple(
            EventCandidate(f"evt_{i}", f"thread_{i}", chr(65 + i), f"사건 {i}",
                           "2026-08-07T10:31:00")
            for i in range(4)
        ), current_event_returns={chr(65 + i): 0.01 for i in range(4)},
    )

    def preview(*_args, **kwargs):
        distribution = EventDistributionPreview(
            kwargs["source_event_id"], kwargs["instrument_id"], "CONTRACT.CANCEL",
            41, -0.031, kwargs["today"], 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    candidate_ids = list(runtime._candidate_by_id)
    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        *({"tool": "hypothesis.preview", "arguments": {
            "candidate_id": cid,
            "outcome_id": "outcome:market_adjusted_return_day_0",
        }} for cid in candidate_ids),
        {"hypotheses": [
            {"preview_handle": None, "intent": f"{i}번 확인."} for i in range(4)
        ]},
    ))

    def ask(_system, _user):
        reply = next(replies)
        if "hypotheses" in reply:
            for hypothesis, handle in zip(reply["hypotheses"], runtime._previews):
                hypothesis["preview_handle"] = handle
        return reply

    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    # 제출 순서 **앞** 3개가 수용된다(마지막·임의 3개 수용으로 바뀌는 회귀 차단).
    submitted_order = list(runtime._previews)[:4]
    assert [v.preview_handle for v in valid] == submitted_order[:3]
    # 초과 사유는 요약 1행 - 건별로 늘어놓으면 재시도 지시문의 rejected[-6:] 창에서
    # 실제 실패 사유를 밀어낸다.
    assert rejected == ["제출 상한 3개 초과 - 제출 순서 뒤의 1건을 기각합니다"]


def test_fourth_distinct_preview_is_refused_at_the_tool(monkeypatch):
    """상한은 제출뿐 아니라 **도구 실행 시점**에도 강제된다 — 제출만 자르면 왕복
    예산 안에서 4·5번째 preview SQL 이 그대로 실행돼 프롬프트 계약("최대 3개")과
    어긋난다. 같은 사건 재조회는 새 비용 축이 아니라 상한에 안 걸린다."""
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=tuple(
            EventCandidate(f"evt_{i}", f"thread_{i}", chr(65 + i), f"사건 {i}",
                           "2026-08-07T10:31:00")
            for i in range(4)
        ), current_event_returns={chr(65 + i): 0.01 for i in range(4)},
    )

    def preview(*_args, **kwargs):
        distribution = EventDistributionPreview(
            kwargs["source_event_id"], kwargs["instrument_id"], "CONTRACT.CANCEL",
            41, -0.031, kwargs["today"], 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    candidate_ids = list(runtime._candidate_by_id)
    outcome = "outcome:market_adjusted_return_day_0"
    for cid in candidate_ids[:3]:
        assert runtime.call("hypothesis.preview", {
            "candidate_id": cid, "outcome_id": outcome})["ok"] is True

    fourth = runtime.call("hypothesis.preview", {
        "candidate_id": candidate_ids[3], "outcome_id": outcome})
    fourth_retry = runtime.call("hypothesis.preview", {
        "candidate_id": candidate_ids[3], "outcome_id": outcome})
    again = runtime.call("hypothesis.preview", {
        "candidate_id": candidate_ids[0], "outcome_id": outcome})

    assert fourth["ok"] is False
    assert fourth["error"]["code"] == "PREVIEW_LIMIT_EXCEEDED"
    # 거부 기록이 "이미 시도함"으로 읽혀 재요청이 상한을 우회하면 안 된다.
    assert fourth_retry["ok"] is False
    assert again["ok"] is True
    # 거부도 퍼널에 남는다(fail-loud) - 미요청(PREVIEW_NOT_REQUESTED)과 구분된다.
    assert runtime.distribution_attempts()["evt_3"]["preview_reason"] == (
        "PREVIEW_LIMIT_EXCEEDED")


def test_distribution_mode_refuses_objectset_calls_and_still_reaches_preview(monkeypatch):
    """분포 모드에서 objectset 호출은 실행 없이 거부돼야 한다(ALPHA-970).

    WHY: 사건 집합은 서버가 고정하는데 호출이 정상 실행(ok=true)되자 모델이 도구
    라운드 전부를 `objectset.create` 반복으로 소진하고 사건 id 를 핸들로 위조
    제출해 분포 문장이 하루 전멸했다(2026-08-12 장중 10/10 실측). 거부 사유가
    list_options→preview 경로로 유도하는 교정 신호다.
    """
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )

    def preview(*_args, **kwargs):
        distribution = EventDistributionPreview(
            "anchor", "A", "CONTRACT.CANCEL", 41, -0.031, -0.036, 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    candidate_id = next(iter(runtime._candidate_by_id))
    runtime_calls: list[str] = []
    inner_call = runtime.call

    def spying_call(name, arguments):
        runtime_calls.append(name)
        return inner_call(name, arguments)

    replies = iter((
        {"tool": "objectset.create", "arguments": {"kind": "COMPANY_ENTITY"}},
        # objectset 만 막으면 위임 경로의 news.* 로 같은 낭비가 우회된다(Codex P2)
        # - hypothesis.* 외 전부가 거부돼야 한다.
        {"tool": "news.find_threads", "arguments": {}},
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"tool": "hypothesis.preview", "arguments": {
            "candidate_id": candidate_id,
            "outcome_id": "outcome:market_adjusted_return_day_0",
        }},
        {"hypotheses": [{"preview_handle": None, "intent": "계약 해지의 과거 반응 확인."}]},
    ))
    seen_users: list[str] = []

    def ask(system, user):
        # 금지문·핸들 규칙이 시스템 프롬프트에 있어야 모델이 첫 라운드부터 교정된다.
        assert "호출하지 마라" in system
        # 어포던스 제거 - 금지문 뒤의 도구 계약이 objectset 을 다시 광고하면 모델이
        # 그쪽을 따른다(리뷰 R1). 스펙·오퍼 예시에 objectset 이 없어야 한다.
        assert "objectset.create" not in system
        seen_users.append(user)
        reply = next(replies)
        if "hypotheses" in reply:
            reply["hypotheses"][0]["preview_handle"] = next(iter(runtime._previews))
        return reply

    # 운영(etfcell)처럼 objectset 스펙을 섞어 넣는다 - hypothesis.* 필터가 실제로
    # 거르는지 이 스펙이 검증한다(리뷰 R2: 순수 hypothesis 스펙만 넣으면 필터가
    # 제거돼도 단언이 통과한다).
    objectset_spec = {"name": "objectset.create",
                      "description": "Create a PIT object set.",
                      "input_schema": {"type": "object"}}
    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": [objectset_spec, *runtime.tool_specs()],
                      "call": spying_call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    assert rejected == []
    assert len(valid) == 1
    # objectset 호출은 런타임까지 내려가지 않는다 - 실행 없는 거부다.
    assert runtime_calls == ["hypothesis.list_options", "hypothesis.preview"]
    # 거부 사유가 되물음에 실려 모델을 preview 경로로 유도한다.
    assert sum("TOOL_NOT_AVAILABLE" in u.split("[ObjectSet 결과", 1)[-1]
               for u in seen_users[1:]) >= 1
    assert all("TOOL_NOT_AVAILABLE" in u for u in seen_users[2:])


def test_duplicate_tool_calls_are_refused_without_consuming_budget(monkeypatch):
    """직전과 동일한 도구 호출은 실행·예산 소모 없이 교정된다(ALPHA-970).

    WHY: objectset 가드 후에도 모델이 `hypothesis.list_options` 를 6연발해 예산을
    소진하고 preview 직전에 멈추는 런이 실측됐다(2026-08-12 verify970). 도구가
    결정론이라 반복은 순수 낭비다 - 예산을 태우면 preview 에 쓸 라운드가 사라진다.
    """
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )

    def preview(*_args, **kwargs):
        distribution = EventDistributionPreview(
            "anchor", "A", "CONTRACT.CANCEL", 41, -0.031, -0.036, 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    candidate_id = next(iter(runtime._candidate_by_id))
    runtime_calls: list[str] = []
    inner_call = runtime.call

    def spying_call(name, arguments):
        runtime_calls.append(name)
        return inner_call(name, arguments)

    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"tool": "hypothesis.list_options", "arguments": {}},   # 중복 - 반려
        {"tool": "hypothesis.list_options", "arguments": {}},   # 중복 - 반려
        {"tool": "hypothesis.preview", "arguments": {
            "candidate_id": candidate_id,
            "outcome_id": "outcome:market_adjusted_return_day_0",
        }},
        {"hypotheses": [{"preview_handle": None, "intent": "계약 해지의 과거 반응 확인."}]},
    ))
    seen_users: list[str] = []

    def ask(system, user):
        seen_users.append(user)
        reply = next(replies)
        if "hypotheses" in reply:
            reply["hypotheses"][0]["preview_handle"] = next(iter(runtime._previews))
        return reply

    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": runtime.tool_specs(), "call": spying_call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    assert rejected == []
    assert len(valid) == 1
    # 중복 호출은 런타임까지 내려가지 않는다 - 실행이 한 번뿐이다.
    assert runtime_calls == ["hypothesis.list_options", "hypothesis.preview"]
    # 반려 사유가 되물음에 실려 다음 단계로 유도한다. 예산 표기는 실행분만 센다.
    assert sum("[도구 반려]" in u for u in seen_users) >= 1
    assert not any("[ObjectSet 결과 3/" in u for u in seen_users)


def test_duplicate_refusal_cap_forces_submission_and_is_observed(monkeypatch):
    """중복 교정 상한(2회) 초과는 제출 단계로 강제 전환된다 - 무한 왕복 방지가 이
    가드의 존재 이유라 상한 경로 자체를 단언한다(리뷰 R1). 반려는 rejects·
    tool_result 로 관측된다(Rule 12 - 가드가 끊은 런과 정상 제출 런이 원장에서
    구분돼야 한다)."""
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )
    runtime_calls: list[str] = []
    inner_call = runtime.call

    def spying_call(name, arguments):
        runtime_calls.append(name)
        return inner_call(name, arguments)

    listopt = {"tool": "hypothesis.list_options", "arguments": {}}
    replies = iter((
        dict(listopt), dict(listopt), dict(listopt),   # 실행 1 + 반려 2
        dict(listopt),                                  # 상한 초과 - _OBJECT_DONE
        {"hypotheses": []},                             # 제출 단계(미제출)
        {"hypotheses": []},                             # 재질의 턴
    ))
    seen_users: list[str] = []
    records: list[dict] = []
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesize.record",
        lambda event, **fields: records.append({"event": event, **fields}))

    def ask(system, user):
        seen_users.append(user)
        return next(replies)

    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": runtime.tool_specs(), "call": spying_call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    assert valid == []
    assert runtime_calls == ["hypothesis.list_options"]   # 실행은 1회뿐
    final_user = seen_users[-1]
    assert final_user.count("[도구 반려]") == 2            # 상한 2회까지만 교정
    # 상한 초과는 제출 강제 전환이다 - _OBJECT_DONE 문구 자체를 단언한다.
    assert "왕복 상한 소진" in final_user
    # 반려는 예산을 소모하지 않는다 - 실행 표기는 1/6 하나뿐이어야 한다.
    assert "[ObjectSet 결과 1/" in final_user
    assert "[ObjectSet 결과 2/" not in final_user
    # 반려도 관측이다(Rule 12) - 상한 초과분 포함 3회가 원장에 남는다.
    refusals = [r for r in records if r.get("event") == "hypothesis.tool_result"
                and r.get("error") == "DUPLICATE_TOOL_CALL"]
    assert len(refusals) == 3
    # 무예산 계약은 예산 카운터 원장으로 직접 단언한다 - 실행 1회만 세야 한다.
    rounds = [r for r in records if r.get("event") == "hypothesize.objectset_rounds"]
    assert rounds and rounds[-1]["rounds"] == 1


def test_failed_call_may_be_retried_with_identical_arguments(monkeypatch):
    """직전 호출이 실패(ok=false)였다면 동일 인자 재시도는 정당하다 - 일시 오류
    복구 경로를 가드가 없애면 안 된다(리뷰 R1)."""
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )
    calls: list[str] = []
    flaky = {"n": 0}
    inner_call = runtime.call

    def flaky_call(name, arguments):
        calls.append(name)
        flaky["n"] += 1
        if flaky["n"] == 1:
            return {"ok": False, "error": "EXECUTION_FAILED"}
        return inner_call(name, arguments)

    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},   # 실패
        {"tool": "hypothesis.list_options", "arguments": {}},   # 재시도 - 실행돼야 함
        {"hypotheses": []},
        {"hypotheses": []},
    ))

    def ask(system, user):
        return next(replies)

    propose(ask, facts="f", event_types=["CONTRACT.CANCEL"],
            object_tools={"specs": runtime.tool_specs(), "call": flaky_call,
                          "resolve_preview": runtime.resolve,
                          "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM})

    assert calls == ["hypothesis.list_options", "hypothesis.list_options"]


def test_duplicate_guard_state_survives_proposal_retry_turns(monkeypatch):
    """중복 판정 상태는 제안 재시도 턴 경계에서 초기화되지 않는다(검증 라운드).

    WHY: 도구 결과는 결정론이라 턴이 바뀌어도 직전 성공 호출의 반복은 낭비다 -
    턴마다 초기화되면 재시도 턴 첫 호출이 반려 없이 예산을 소모한다.
    """
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )
    runtime_calls: list[str] = []
    inner_call = runtime.call

    def spying_call(name, arguments):
        runtime_calls.append(name)
        return inner_call(name, arguments)

    listopt = {"tool": "hypothesis.list_options", "arguments": {}}
    replies = iter((
        dict(listopt),                    # 턴1 - 실행
        dict(listopt),                    # 턴1 - 반려 1
        dict(listopt),                    # 턴1 - 반려 2 (상한 도달)
        {"hypotheses": []},               # 턴1 미제출 - 재시도
        dict(listopt),                    # 턴2 첫 호출 - 상한 초과 → _OBJECT_DONE
        {"hypotheses": []},
    ))
    seen_users: list[str] = []

    def ask(system, user):
        seen_users.append(user)
        return next(replies)

    propose(ask, facts="f", event_types=["CONTRACT.CANCEL"],
            object_tools={"specs": runtime.tool_specs(), "call": spying_call,
                          "resolve_preview": runtime.resolve,
                          "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM})

    assert runtime_calls == ["hypothesis.list_options"]   # 턴2 반복도 실행 안 됨
    # 반려 상한(2회)은 턴 누적이다 - 턴2 의 반복은 추가 교정 없이 곧장 제출 강제
    # 전환된다(교정 문구 2회 + 상한 소진 문구). 턴마다 refusals 가 0 으로 초기화되면
    # 턴2 에서 반려 문구가 3회째 찍혀 이 단언이 깨진다.
    final_user = seen_users[-1]
    assert final_user.count("[도구 반려]") == 2
    assert "왕복 상한 소진" in final_user


def test_advancing_to_a_new_call_resets_the_refusal_counter(monkeypatch):
    """다른 호출로 전진하면 교정 카운터가 리셋된다(Codex P2) - 이월 카운트가 이후
    별개 중복의 첫 교정을 조기 제출 전환으로 바꾸면 다른 후보를 preview 할 기회가
    사라진다. 실행이 예산을 소모하므로 리셋해도 왕복은 유한하다."""
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ), current_event_returns={"A": -0.036},
    )

    def preview(*_args, **kwargs):
        distribution = EventDistributionPreview(
            "anchor", "A", "CONTRACT.CANCEL", 41, -0.031, -0.036, 0.42)
        return EventDistributionPreviewResult(
            "READY", "READY", distribution, 1, 41, 30)

    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview", preview)
    candidate_id = next(iter(runtime._candidate_by_id))
    listopt = {"tool": "hypothesis.list_options", "arguments": {}}
    prev = {"tool": "hypothesis.preview", "arguments": {
        "candidate_id": candidate_id,
        "outcome_id": "outcome:market_adjusted_return_day_0"}}
    replies = iter((
        dict(listopt), dict(listopt), dict(listopt),   # 실행 1 + 반려 2 (상한 도달)
        dict(prev),                                     # 전진 - 카운터 리셋돼야
        dict(prev),                                     # preview 중복 - 첫 교정이어야
        {"hypotheses": [{"preview_handle": None, "intent": "확인."}]},
    ))
    seen_users: list[str] = []

    def ask(system, user):
        seen_users.append(user)
        reply = next(replies)
        if "hypotheses" in reply:
            reply["hypotheses"][0]["preview_handle"] = next(iter(runtime._previews))
        return reply

    valid, rejected = propose(
        ask, facts="f", event_types=["CONTRACT.CANCEL"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve,
                      "preview_system": _EVENT_DISTRIBUTION_PREVIEW_SYSTEM},
    )

    # 리셋이 없으면 preview 중복이 3회째 카운트로 _OBJECT_DONE 을 트리거해
    # 제출이 강제되고 이 READY 제출 경로가 깨진다.
    assert rejected == []
    assert len(valid) == 1
    assert seen_users[-1].count("[도구 반려]") == 3   # listopt 2회 + preview 1회 교정
