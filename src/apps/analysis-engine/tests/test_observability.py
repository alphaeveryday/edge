"""Tests for observability helpers."""

from edge_analysis.observability import stable_id


def test_stable_id_is_deterministic():
    # Identical input must produce an identical id so idempotent (ON CONFLICT)
    # upserts converge across reruns.
    assert stable_id("cob", "pmt_X") == stable_id("cob", "pmt_X")


def test_stable_id_differs_for_different_input():
    # Distinct material must produce distinct ids, or two triggers' lineage
    # would collapse onto the same row.
    assert stable_id("cob", "a") != stable_id("cob", "b")
