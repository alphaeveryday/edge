"""Event-distribution previews must keep one anchored event and a PIT-safe history."""
from __future__ import annotations

from types import SimpleNamespace

import duckdb

from edge_analysis.statics.hypothesize import (_EVENT_DISTRIBUTION_PREVIEW_SYSTEM,
                                               propose)
from edge_analysis.statics.hypothesis_preview import (EventCandidate,
                                                       EventDistributionPreview,
                                                       HypothesisPreviewRuntime,
                                                       event_distribution_preview)


def _lake() -> SimpleNamespace:
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE v_event(
            source_event_id TEXT, instrument_id TEXT, event_type_code TEXT,
            trade_date DATE, available_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE v_daily(instrument_id TEXT, trade_date DATE, ar DOUBLE)
    """)
    con.execute("""
        INSERT INTO v_event VALUES
          ('old_a', 'A', 'CONTRACT.CANCEL', DATE '2026-07-01', TIMESTAMP '2026-07-01 10:00:00'),
          ('old_a_duplicate', 'A', 'CONTRACT.CANCEL', DATE '2026-07-01', TIMESTAMP '2026-07-01 11:00:00'),
          ('old_b', 'B', 'CONTRACT.CANCEL', DATE '2026-07-02', TIMESTAMP '2026-07-02 10:00:00'),
          ('other_type', 'C', 'CONTRACT.SIGN', DATE '2026-07-03', TIMESTAMP '2026-07-03 10:00:00'),
          ('future', 'D', 'CONTRACT.CANCEL', DATE '2026-07-04', TIMESTAMP '2026-08-08 09:00:00'),
          ('anchor', 'A', 'CONTRACT.CANCEL', DATE '2026-08-07', TIMESTAMP '2026-08-07 10:31:00'),
          ('anchor', 'COUNTERPARTY', 'CONTRACT.CANCEL', DATE '2026-08-07', TIMESTAMP '2026-08-07 10:31:00')
    """)
    con.execute("""
        INSERT INTO v_daily VALUES
          ('A', DATE '2026-07-01', -0.01),
          ('B', DATE '2026-07-02',  0.02),
          ('C', DATE '2026-07-03', -0.90),
          ('D', DATE '2026-07-04', -0.80),
          ('A', DATE '2026-08-07', -0.03)
    """)
    return SimpleNamespace(con=con)


def test_distribution_preview_binds_anchor_to_same_type_deduplicated_pit_history():
    preview = event_distribution_preview(
        _lake(), source_event_id="anchor", instrument_id="A", day="2026-08-07",
        as_of="2026-08-07 12:05:00", min_n=2,
    )

    assert preview is not None
    assert preview.n == 2
    assert preview.mean == 0.005
    assert preview.today == -0.03
    assert preview.percentile == 0.0


def test_llm_can_only_submit_a_ready_current_event_distribution_preview(monkeypatch):
    event_sets = SimpleNamespace(as_of="2026-08-07T12:05:00", call=lambda *_: {})
    runtime = HypothesisPreviewRuntime(
        object(), event_sets, day="2026-08-07", candidates=(
            EventCandidate("anchor", "thread_1", "A", "공급계약 해지",
                           "2026-08-07T10:31:00"),
        ),
    )
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.event_distribution_preview",
        lambda *_args, **_kwargs: EventDistributionPreview(
            "anchor", "A", "CONTRACT.CANCEL", 41, -0.031, -0.036, 0.42),
    )
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
    assert "event_candidates" in systems[0]
    assert "exposure_id" not in systems[0]
