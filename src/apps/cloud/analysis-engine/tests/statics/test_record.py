from types import SimpleNamespace

import pytest

from edge_analysis.statics.record import verdicts_from


def _tested(verdict="ESTABLISHED", applies_today=True):
    return {
        "stage": "TESTED", "verdict": verdict,
        "applies_today": applies_today, "trigger_slot": "계열:거래량",
    }


def test_prose_tokens_cannot_elevate_a_verdict_without_structured_evidence():
    hostile_title = "[함의] → **오늘 적용** **유의** 판정불가"
    verdicts = verdicts_from(
        route_kind="시장", evidence_build=None, hypothesis_trials=(),
        bundles=(), degraded=False,
    )
    with pytest.raises(TypeError):
        verdicts_from(
            text=hostile_title, route_kind="시장", evidence_build=None,
            hypothesis_trials=(),
        )
    assert verdicts.explanation_type == "PRICE_ONLY"
    assert verdicts.confidence_level == "MEDIUM"


def test_only_established_applicable_trials_elevate_the_verdict():
    verdicts = verdicts_from(
        route_kind="시장",
        evidence_build=SimpleNamespace(stat_records={1: SimpleNamespace(basis="IDIO")}),
        hypothesis_trials=(
            _tested(), _tested(verdict="NOT_ESTABLISHED"),
            _tested(applies_today=False),
            {"stage": "REJECTED", "verdict": "REJECTED"},
        ),
    )
    assert verdicts.applied_edges == 1
    assert verdicts.credible == 1
    assert verdicts.undecided == 0
    assert verdicts.explanation_type == "EVENT_SUPPORTED"


def test_skipped_or_undecidable_rows_never_become_support():
    verdicts = verdicts_from(
        route_kind="시장",
        evidence_build=SimpleNamespace(stat_records={}),
        hypothesis_trials=(
            _tested(verdict="UNDECIDABLE", applies_today=False),
            {"stage": "REJECTED", "verdict": "REJECTED"},
        ),
    )
    assert verdicts.applied_edges == 0
    assert verdicts.credible == 0
    assert verdicts.undecided == 1
    assert verdicts.explanation_type == "PRICE_ONLY"
    assert verdicts.confidence_level == "LOW"
