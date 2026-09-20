"""정책상 미연결을 명부 누락·일반 기업 누락과 구분한다."""
import pytest
from edge_ontology import is_policy_excluded


@pytest.mark.parametrize("role,text,expected", [
    ("PERSON", "홍길동", True), ("INDUSTRY", "반도체", True),
    ("AUTHORITY", "정부", True), ("AUTHORITY", "금융 당국", True),
    ("AUTHORITY", "없는기관", False), ("AUTHORITY", "정부 및 금융위원회", False),
    ("ISSUER", "미등록회사", False), ("PERSON", None, False),
    ("PERSON", "  ", False), ("UNKNOWN", "정부", False),
])
def test_policy_exclusion_is_explicit(role, text, expected):
    assert is_policy_excluded(role, text) is expected
