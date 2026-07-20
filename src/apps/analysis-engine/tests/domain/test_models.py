"""Tests for the Explanation domain model.

The DeepSeek response is an untyped JSON object; Explanation gives it typed
accessors while preserving the raw payload for the run archive (ALPHA-407
fields such as key_evidence/unexplained are otherwise lost by the DB mapping).
"""

from edge_analysis.domain.models import Explanation


def test_summary_prefers_explain_then_falls_back_to_summary():
    assert Explanation({"explain": "A", "summary": "B"}).summary == "A"
    assert Explanation({"summary": "B"}).summary == "B"


def test_explanation_type_maps_verdict_and_defaults_to_uncertain():
    assert Explanation({"verdict": "공식 이벤트 선행"}).explanation_type == "EVENT_SUPPORTED"
    assert Explanation({"verdict": "unknown"}).explanation_type == "UNCERTAIN"


def test_is_valid_requires_verdict_and_a_body():
    assert Explanation({"verdict": "x", "explain": "y"}).is_valid
    assert not Explanation({"verdict": "x"}).is_valid
