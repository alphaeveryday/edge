"""Explanation 도메인 모델 테스트.

DeepSeek 응답은 타입 없는 JSON 객체다. Explanation 은 타입 있는 접근자를 주면서
원본 payload 를 보존한다 — DB 매핑에서 잃는 필드(key_evidence·unexplained 등,
ALPHA-407)가 런 아카이브에 남아야 하기 때문이다.
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
