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
        con.execute("""
            CREATE TABLE event_rows(
                source_event_id TEXT, instrument_id TEXT, event_type_code TEXT,
                trade_date DATE, available_at TIMESTAMP
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
    assert (thin.reason, thin.historical_n, thin.min_n) == (
        "HISTORY_BELOW_MIN", 3, 4)


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
        as_of="2026-08-07 12:05:00", today=-0.036, min_n=4)

    # NaN 행이 유한값처럼 세어지면 4건이 되어 READY 로 뒤집힌다 — 그 회귀가
    # 이 단언을 깨뜨린다.
    assert (result.status, result.reason, result.historical_n) == (
        "UNAVAILABLE", "HISTORY_BELOW_MIN", 3)


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
