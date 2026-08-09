"""Model output is untrusted data, never an executable query surface."""

import pytest

from edge_analysis.statics.model_contract import (MAX_MODEL_LIST_ITEMS,
                                                   MAX_MODEL_RESPONSE_BYTES,
                                                   ModelLimitError, ModelSchemaError,
                                                   ModelShapeError, list_field,
                                                   validate_model_output)


@pytest.mark.parametrize("key", ["sql", "query", "view_name"])
def test_executable_root_keys_are_rejected_even_when_empty(key):
    """Presence is the violation: an empty value must not create a bypass."""
    with pytest.raises(ModelSchemaError, match="MODEL_SCHEMA_REJECTED") as caught:
        validate_model_output({key: "", "hypotheses": []})

    assert caught.value.keys == (key,)


def test_hypothesis_stage_uses_the_shared_model_contract():
    from edge_analysis.statics.hypothesize import propose

    with pytest.raises(ModelSchemaError):
        propose(lambda *_: {"query": "SELECT 1"}, facts="x", event_types=[])


def test_hypothesis_retry_cannot_bypass_the_shared_model_contract():
    from edge_analysis.statics.hypothesize import propose

    replies = iter([{"hypotheses": []}, {"hypotheses": [], "sql": "SELECT 1"}])
    with pytest.raises(ModelSchemaError):
        propose(lambda *_: next(replies), facts="x", event_types=[])


def test_hypotheses_wrong_shape_fails_loud_instead_of_looking_empty():
    from edge_analysis.statics.hypothesize import propose

    with pytest.raises(ModelShapeError, match="MODEL_SHAPE_REJECTED"):
        propose(lambda *_: {"hypotheses": {"not": "an array"}},
                facts="x", event_types=[])


def test_valid_payload_cannot_hide_an_executable_extra_root():
    with pytest.raises(ModelSchemaError):
        validate_model_output({"hypotheses": [], "sql": ""})


def test_non_object_model_response_fails_with_a_structured_shape_error():
    with pytest.raises(ModelShapeError, match=r"MODEL_SHAPE_REJECTED: \$ must be an object"):
        validate_model_output([])  # type: ignore[arg-type]


def test_nested_search_arguments_are_not_confused_with_root_execution_fields():
    output = {"tool": "news.find_threads", "arguments": {"query": "반도체"}}
    assert validate_model_output(output) is output


def test_plain_stage_uses_the_shared_model_contract():
    from edge_analysis.statics.plain import narrate_plain

    with pytest.raises(ModelSchemaError):
        narrate_plain(lambda *_: {"view_name": "v_event"}, {})


class _FinishedMachine:
    done = True

    def brief(self):
        return "done"


def test_expressive_generate_stage_fails_loud_on_executable_output():
    from edge_analysis.statics.expressive import generate

    with pytest.raises(ModelSchemaError):
        generate(lambda *_: {"sql": "SELECT 1"}, _FinishedMachine(), facts="x")


def test_expressive_generate_does_not_swallow_a_non_object_response():
    from edge_analysis.statics.expressive import generate

    with pytest.raises(ModelShapeError):
        generate(lambda *_: [], _FinishedMachine(), facts="x")


def test_expressive_score_stage_fails_loud_on_executable_output():
    from edge_analysis.statics.expressive import score

    with pytest.raises(ModelSchemaError):
        score(lambda *_: {"query": "SELECT 1"}, "claim", event_types=[])


def test_verifier_stage_uses_the_shared_model_contract(monkeypatch):
    from edge_analysis.statics import verifier

    monkeypatch.setattr(verifier, "slot_menu", lambda *_: {})
    with pytest.raises(ModelSchemaError):
        verifier.design(lambda *_: {"sql": "SELECT 1"}, object(),
                        etype="COMPANY.PRODUCT.LAUNCH", day="2026-08-05", layer="고유")


def test_plain_claims_must_be_an_array_on_primary_and_retry():
    from edge_analysis.statics.plain import narrate_plain

    with pytest.raises(ModelShapeError):
        narrate_plain(lambda *_: {"claims": {}}, {})

    replies = iter([{"claims": []}, {"claims": {}}])
    with pytest.raises(ModelShapeError):
        narrate_plain(lambda *_: next(replies), {})


def test_verifier_probes_must_be_an_array_on_primary_and_retry(monkeypatch):
    from edge_analysis.statics import verifier

    monkeypatch.setattr(verifier, "slot_menu", lambda *_: {"actor": [("A", 1)]})
    with pytest.raises(ModelShapeError):
        verifier.design(lambda *_: {"probes": {}}, object(), etype="X",
                        day="2026-08-05", layer="고유")

    replies = iter([{"probes": []}, {"probes": {}}])
    with pytest.raises(ModelShapeError):
        verifier.design(lambda *_: next(replies), object(), etype="X",
                        day="2026-08-05", layer="고유", max_probes=1)


def test_object_tool_arguments_must_be_an_object_without_calling_the_tool():
    from edge_analysis.statics.hypothesize import propose

    calls = []
    with pytest.raises(ModelShapeError):
        propose(lambda *_: {"tool": "news.find_threads", "arguments": []},
                facts="x", event_types=["X"],
                object_tools={"specs": [], "call": lambda *a: calls.append(a)})
    assert calls == []

    replies = iter([
        {"tool": "news.find_threads", "arguments": {}},
        {"tool": "news.list_events", "arguments": []},
    ])
    with pytest.raises(ModelShapeError):
        propose(lambda *_: next(replies), facts="x", event_types=["X"],
                object_tools={
                    "specs": [],
                    "call": lambda *args: calls.append(args) or {"ok": True},
                })
    assert len(calls) == 1


def test_expressive_slots_must_be_an_object():
    from edge_analysis.statics.expressive import score

    with pytest.raises(ModelShapeError):
        score(lambda *_: {"slots": []}, "claim", event_types=[])


def test_oversized_model_response_fails_before_it_can_reach_an_archive():
    with pytest.raises(ModelLimitError, match="MODEL_LIMIT_REJECTED"):
        validate_model_output({"hypotheses": ["x" * MAX_MODEL_RESPONSE_BYTES]})


def test_non_json_values_fail_at_the_model_boundary():
    with pytest.raises(ModelShapeError, match="JSON-serializable"):
        validate_model_output({"hypotheses": [{"value": object()}]})


def test_unbounded_rejected_hypotheses_fail_instead_of_growing_the_ledger():
    output = {"hypotheses": [{}] * (MAX_MODEL_LIST_ITEMS + 1)}
    with pytest.raises(ModelLimitError, match="MODEL_LIMIT_REJECTED"):
        list_field(output, "hypotheses")

    from edge_analysis.statics.hypothesize import propose
    with pytest.raises(ModelLimitError, match="MODEL_LIMIT_REJECTED"):
        propose(lambda *_: output, facts="x", event_types=[])
