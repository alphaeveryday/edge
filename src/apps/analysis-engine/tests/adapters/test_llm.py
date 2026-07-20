"""Tests for the analysis step's response validation.

The DeepSeek HTTP call is I/O and is not unit-tested; analyze() is exercised
with a fake client to pin that a malformed response fails loudly instead of
persisting an empty explanation.
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
    return analyze(_FakeClient(response), etf_ticker="091160", trade_date=date(2026, 7, 16),
                   decomp=_DECOMP, gate=_GATE, route_code="COMMON_FACTOR", events=[])


def test_analyze_wraps_a_valid_response():
    explanation = _analyze({"verdict": "시장·섹터 주도", "explain": "…"})

    assert explanation.explanation_type == "MIXED"


def test_analyze_rejects_a_response_missing_required_fields():
    with pytest.raises(PipelineError):
        _analyze({"headline": "no verdict, no body"})
