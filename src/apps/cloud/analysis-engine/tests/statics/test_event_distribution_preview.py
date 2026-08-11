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
