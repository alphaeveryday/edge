"""분석 스텝의 응답 검증 테스트.

DeepSeek HTTP 호출은 I/O 라 여기서 유닛테스트하지 않는다. analyze() 는 fake client 로
돌려, 잘못된 응답이 빈 설명을 영속하는 대신 fail-loud 하는지 고정한다.
"""

from datetime import date

import pytest

from edge_analysis.adapters.llm import analyze
from edge_analysis.config import PipelineError
from edge_analysis.domain.models import Decomposition, PriceTrigger

_GATE = PriceTrigger("pmt_1", 0.05, "abs", abs_gate=True, rel_gate=False)
_DECOMP = Decomposition(members=[], proxy_ret=0.05, covered_weight=1.0, total_weight=1.0,
                        coverage=1.0, top1=None, top3=None, advancing=0, total_priced=0,
                        n_constituents=0)


class _FakeClient:
    def __init__(self, response):
        self._response = response

    def complete_json(self, system, user):
        return self._response


def _analyze(response):
    return analyze(_FakeClient(response), etf_ticker="091160", etf_name="테스트 ETF",
                   name_by_ticker={}, trade_date=date(2026, 7, 16),
                   decomp=_DECOMP, gate=_GATE, route_code="COMMON_FACTOR", events=[])


def test_analyze_wraps_a_valid_response():
    explanation = _analyze({"verdict": "시장·섹터 주도", "explain": "…"})

    assert explanation.explanation_type == "MIXED"


def test_analyze_rejects_a_response_missing_required_fields():
    with pytest.raises(PipelineError):
        _analyze({"headline": "no verdict, no body"})
