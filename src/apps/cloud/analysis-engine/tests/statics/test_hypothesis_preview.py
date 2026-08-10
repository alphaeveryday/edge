"""Hypothesis preview exposes only the existing panel-test design."""
from __future__ import annotations

import json
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


def test_each_exposure_lists_only_compatible_modifiers_that_preview_accepts(monkeypatch):
    runtime = _runtime()
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.edge_test",
        lambda *_args, **_kwargs: SimpleNamespace(verdict="성립", n=42),
    )
    options = runtime.call("hypothesis.list_options", {"event_set_handle": "os_events"})
    exposure = options["exposures"][0]
    own_high = "condition:" + exposure["id"].removeprefix("feature:") + ":high_90"
    selected = runtime.call("hypothesis.list_options", {
        "event_set_handle": "os_events", "exposure_id": exposure["id"],
    })

    assert "modifiers" not in options
    assert own_high not in {modifier["id"] for modifier in selected["modifiers"]}
    assert selected["modifiers"]
    preview = runtime.call("hypothesis.preview", {
        "event_set_handle": "os_events",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": exposure["layers"][0],
        "exposure_id": exposure["id"],
        "modifier_id": selected["modifiers"][0]["id"],
    })
    assert preview["ok"] is True
    assert runtime.call("hypothesis.list_options", {
        "event_set_handle": "os_events", "modifier_id": own_high,
    })["error"]["code"] == "INVALID_ARGUMENTS"


def test_usual_model_tool_sequence_uses_the_server_scoped_event_set_and_reaches_a_preview_handle(monkeypatch):
    runtime = HypothesisPreviewRuntime(
        object(), _EventSets(), day="2026-08-07", default_event_set_handle="os_events")
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.edge_test",
        lambda *_args, **_kwargs: SimpleNamespace(verdict="성립", n=42),
    )
    replies = 0
    systems: list[str] = []

    def ask(system, _user):
        nonlocal replies
        systems.append(system)
        replies += 1
        if replies == 1:
            return {"tool": "hypothesis.list_options", "arguments": {}}
        if replies == 2:
            return {"tool": "hypothesis.preview", "arguments": {
                "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
                "outcome_id": "outcome:daily_return",
                "layer_id": "layer:고유",
                "exposure_id": "feature:배수/수준",
            }}
        return {"hypotheses": [{
            "preview_handle": next(iter(runtime._previews)),
            "intent": "PBR 수준에 따른 수익률 차이를 확인한다.",
        }]}

    valid, rejected = propose(
        ask, facts="f", event_types=["COMPANY.COMMERCIAL.MARKET_ENTRY"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve},
    )

    assert rejected == []
    assert len(valid) == 1
    assert valid[0].trigger.ident == "COMPANY.COMMERCIAL.MARKET_ENTRY"
    assert "빈 arguments 객체" in systems[0]
    wrong_handle = runtime.call(
        "hypothesis.list_options", {"event_set_handle": "os_company_entity"})
    assert wrong_handle["ok"] is False
    assert wrong_handle["retry"] == {"tool": "hypothesis.list_options", "arguments": {}}
    wrong_preview = runtime.call("hypothesis.preview", {
        "event_set_handle": "os_company_entity",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:배수/수준",
    })
    assert wrong_preview["ok"] is False
    assert wrong_preview["retry"] == {"tool": "hypothesis.preview", "arguments": {
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:배수/수준",
    }}
    assert "os_company_entity" not in json.dumps(wrong_preview)
    invalid_preview = runtime.call("hypothesis.preview", {
        "event_set_handle": "os_company_entity",
        "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
        "outcome_id": "outcome:daily_return",
        "layer_id": "layer:고유",
        "exposure_id": "feature:not/a-feature",
    })
    assert invalid_preview["retry"] == {"tool": "hypothesis.list_options", "arguments": {}}
    assert "not/a-feature" not in json.dumps(invalid_preview)


def test_unknown_final_preview_handle_requires_preview_tool_before_another_final_submission(monkeypatch):
    runtime = HypothesisPreviewRuntime(
        object(), _EventSets(), day="2026-08-07", default_event_set_handle="os_events")
    monkeypatch.setattr(
        "edge_analysis.statics.hypothesis_preview.edge_test",
        lambda *_args, **_kwargs: SimpleNamespace(verdict="성립", n=42),
    )
    prompts: list[str] = []
    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"hypotheses": [{"preview_handle": "hpr_...", "intent": "검정한다."}]},
        {"tool": "hypothesis.preview", "arguments": {
            "trigger_id": "event:COMPANY.COMMERCIAL.MARKET_ENTRY",
            "outcome_id": "outcome:daily_return",
            "layer_id": "layer:고유",
            "exposure_id": "feature:배수/수준",
        }},
        {"hypotheses": [{
            "preview_handle": lambda: next(iter(runtime._previews)),
            "intent": "검정한다.",
        }]},
    ))

    def ask(_system, user):
        prompts.append(user)
        reply = next(replies)
        if isinstance(reply.get("hypotheses"), list):
            for hypothesis in reply["hypotheses"]:
                handle = hypothesis.get("preview_handle")
                if callable(handle):
                    hypothesis["preview_handle"] = handle()
        return reply

    valid, rejected = propose(
        ask, facts="f", event_types=["COMPANY.COMMERCIAL.MARKET_ENTRY"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve},
    )

    assert len(valid) == 1
    assert any("UNKNOWN_PREVIEW_HANDLE" in reason for reason in rejected)
    assert "hypothesis.preview` 도구 호출" in prompts[2]
    assert "hpr_..." not in prompts[2]


def test_empty_final_with_previewable_options_is_rejected_and_retries_the_preview_tool():
    runtime = HypothesisPreviewRuntime(
        object(), _EventSets(), day="2026-08-07", default_event_set_handle="os_events")
    prompts: list[str] = []
    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"hypotheses": []},
        {"hypotheses": []},
    ))

    def ask(_system, user):
        prompts.append(user)
        return next(replies)

    valid, rejected = propose(
        ask, facts="f", event_types=["COMPANY.COMMERCIAL.MARKET_ENTRY"],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve},
    )

    assert valid == []
    assert any("READY preview" in reason for reason in rejected)
    assert "hypothesis.preview` 도구 호출" in prompts[2]


def test_empty_final_is_allowed_when_the_scoped_event_set_has_no_previewable_options():
    runtime = HypothesisPreviewRuntime(
        object(), _EventSets(event_types=()), day="2026-08-07", default_event_set_handle="os_events")
    replies = iter((
        {"tool": "hypothesis.list_options", "arguments": {}},
        {"hypotheses": []},
        {"hypotheses": []},
    ))

    valid, rejected = propose(
        lambda *_: next(replies), facts="f", event_types=[],
        object_tools={"specs": runtime.tool_specs(), "call": runtime.call,
                      "resolve_preview": runtime.resolve},
    )

    assert valid == []
    assert rejected == []


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
