"""Hypothesis preview exposes only the existing panel-test design."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from edge_analysis.statics.hypothesize import propose
from edge_analysis.statics.hypothesis_preview import (HypothesisPreviewRuntime,
                                                       PreviewResolutionError)
from edge_analysis.statics.paneltest import FEATURES
from edge_analysis.statics.vocab import ExposureSource, HypothesisTuple, Trigger


class _EventSets:
    def __init__(self, event_types=("COMPANY.COMMERCIAL.MARKET_ENTRY",)) -> None:
        self.event_types = event_types
        self.calls: list[str] = []

    def event_type_codes(self, handle: str) -> tuple[str, ...]:
        self.calls.append(handle)
        if handle != "os_events":
            raise ValueError("unknown event set")
        return self.event_types


def _runtime() -> HypothesisPreviewRuntime:
    return HypothesisPreviewRuntime(object(), _EventSets(), day="2026-08-07")


def test_list_options_is_the_exact_panel_feature_registry_without_channel_vocabulary():
    options = _runtime().call("hypothesis.list_options", {"event_set_handle": "os_events"})

    assert options["ok"] is True
    assert {row["id"] for row in options["exposures"]} == {
        f"feature:{family}/{transform}" for family, transform in FEATURES
    }
    assert "channels" not in options
    assert options["triggers"] == [{
        "id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "label": "COMPANY.COMMERCIAL.MARKET_ENTRY 사건",
    }]


def test_preview_rejects_unknown_handle_or_option_before_running_the_verifier(monkeypatch):
    runtime = _runtime()
    called = False

    def edge_test(*_args, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(verdict="성립", n=42)

    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview.edge_test", edge_test)

    bad_handle = runtime.call("hypothesis.list_options", {"event_set_handle": "os_missing"})
    bad_option = runtime.call("hypothesis.preview", {
        "event_set_handle": "os_events",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:not/a-feature",
    })

    assert bad_handle["error"]["code"] == "HANDLE_NOT_FOUND"
    assert bad_option["error"]["code"] == "OPTION_NOT_ALLOWED"
    assert called is False


def test_preview_describes_the_selected_existing_verifier_path_without_a_control_group(monkeypatch):
    runtime = _runtime()
    seen = {}

    def edge_test(_lake, hypothesis, day, **_kwargs):
        seen["hypothesis"] = hypothesis
        seen["day"] = day
        return SimpleNamespace(verdict="성립", n=42)

    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview.edge_test", edge_test)
    preview = runtime.call("hypothesis.preview", {
        "event_set_handle": "os_events",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:배수/수준",
        "modifier_id": "condition:거시/민감도:high_90",
    })

    assert preview["ok"] is True and preview["status"] == "READY"
    assert preview["sample"] == {"historical_event_observations": 42}
    assert "PBR 수준" in preview["summary"]
    assert "거래일별 층화" in preview["method"]
    assert "사건 없는" not in preview["summary"]
    assert "대조군" not in preview["summary"]
    assert seen["hypothesis"].exposure.ident == "배수"
    assert seen["hypothesis"].conditions[0].ident == "거시"
    assert seen["day"] == "2026-08-07"
    assert runtime.resolve(preview["handle"]).hypothesis == seen["hypothesis"]


def test_unavailable_preview_is_never_ready(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.edge_test",
        lambda *_args, **_kwargs: SimpleNamespace(verdict="판정불가", n=12),
    )

    preview = runtime.call("hypothesis.preview", {
        "event_set_handle": "os_events",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:배수/수준",
    })

    assert preview["available"] is False
    assert preview["status"] == "UNAVAILABLE"


def test_final_submission_rejects_legacy_tuple_and_accepts_only_a_ready_frozen_preview():
    frozen = HypothesisTuple(
        conditions=(), trigger=Trigger("점", "COMPANY.COMMERCIAL.MARKET_ENTRY"),
        channel="Q수량", exposure=ExposureSource("속성", "배수", "수준"),
        outcome="수익률", layer="고유",
    )
    calls: list[str] = []

    def resolve(handle: str):
        calls.append(handle)
        if handle != "hpr_ready":
            raise PreviewResolutionError("UNKNOWN_PREVIEW_HANDLE")
        return SimpleNamespace(hypothesis=frozen, summary="준비된 검정")

    legacy, rejected = propose(
        lambda *_: {"hypotheses": [{
            "trigger": {"kind": "점", "ident": "COMPANY.COMMERCIAL.MARKET_ENTRY"},
            "channel": "Q수량",
        }]},
        facts="f", event_types=["COMPANY.COMMERCIAL.MARKET_ENTRY"],
        object_tools={"specs": [], "call": lambda *_: {"ok": True},
                      "resolve_preview": resolve},
    )
    systems: list[str] = []

    def preview_ask(system, _user):
        systems.append(system)
        return {"hypotheses": [{
            "preview_handle": "hpr_ready", "intent": "PBR 수준별 수익률 차이를 확인한다.",
        }]}

    accepted, rejected_ready = propose(
        preview_ask,
        facts="f", event_types=["COMPANY.COMMERCIAL.MARKET_ENTRY"],
        object_tools={"specs": [], "call": lambda *_: {"ok": True},
                      "resolve_preview": resolve},
    )

    assert legacy == [] and any("preview_handle" in reason for reason in rejected)
    assert accepted[0].trigger == frozen.trigger
    assert accepted[0].exposure == frozen.exposure
    assert accepted[0].channel == frozen.channel
    assert accepted[0].intent == "PBR 수준별 수익률 차이를 확인한다."
    assert rejected_ready == []
    assert calls == ["hpr_ready"]
    assert "channel" not in systems[0].lower() and "채널" not in systems[0]


def test_forged_cross_runtime_and_not_ready_handles_cannot_be_resolved_or_run(monkeypatch):
    event_sets = _EventSets()
    first = HypothesisPreviewRuntime(object(), event_sets, day="2026-08-07")
    second = HypothesisPreviewRuntime(object(), event_sets, day="2026-08-07")
    calls = 0

    def edge_test(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(verdict="성립", n=42)

    monkeypatch.setattr("edge_analysis.statics.hypothesis_preview.edge_test", edge_test)
    ready = first.call("hypothesis.preview", {
        "event_set_handle": "os_events",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:배수/수준",
    })
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.edge_test",
        lambda *_args, **_kwargs: SimpleNamespace(verdict="판정불가", n=12),
    )
    not_ready = second.call("hypothesis.preview", {
        "event_set_handle": "os_events",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:배수/수준",
    })

    with pytest.raises(PreviewResolutionError, match="UNKNOWN_PREVIEW_HANDLE"):
        first.resolve("hpr_forged")
    with pytest.raises(PreviewResolutionError, match="UNKNOWN_PREVIEW_HANDLE"):
        second.resolve(ready["handle"])
    with pytest.raises(PreviewResolutionError, match="PREVIEW_NOT_READY"):
        second.resolve(not_ready["handle"])

    assert calls == 1
