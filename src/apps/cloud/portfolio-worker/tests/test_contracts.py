import pytest
from edge_portfolio.contracts import AnalysisInput, calculate, validate_section


def test_weights_are_calculated_not_generated():
    request = AnalysisInput(holdings=[{"name": "VOO", "amount_krw": 300}, {"name": "QQQ", "amount_krw": 100}], horizon="10년 이상")
    result = calculate(request)
    assert [h["weight_pct"] for h in result["holdings"]] == [75, 25]
    assert result["expected_return_1y"] is None


def test_fabricated_quote_cannot_be_published():
    with pytest.raises(ValueError, match="passage"):
        validate_section("outlook", {"paragraphs": ["가" * 250, "나" * 250], "evidence": [{"source_id": "s1", "passage_id": "invented"}], "limitations": []}, [{"id": "s1", "passages": []}])


def test_length_limit_is_enforced_not_silently_truncated():
    with pytest.raises(ValueError, match="characters"):
        validate_section("question", {"paragraphs": ["가" * 251, "나" * 251], "evidence": [], "limitations": []}, [])


def test_quote_is_copied_by_code_without_model_reformatting():
    text = 'Information Technology | 38.0 %'
    result = validate_section('portfolio', {'paragraphs': ['가' * 250, '나' * 250], 'evidence': [{'source_id': 's1', 'passage_id': 'p1'}], 'limitations': []}, [{'id': 's1', 'passages': [{'id': 'p1', 'text': text}]}])
    assert result['evidence'][0]['quote'] == text
