"""후보 주소는 재수집 변화와 세대를 분리하고, 잘못된 manifest 를 인증하지 않는다."""

from datetime import datetime, timedelta

import pytest

from data_pipeline.lake import minute_content_artifact_key, minute_content_manifest_key
from data_pipeline.minute.artifacts import (
    build_content_window_manifest,
    parse_manifest,
    serialize_manifest,
    sha256_bytes,
)
from data_pipeline.minute.models import KST

START = datetime(2026, 9, 7, 9, 0, tzinfo=KST)
DATASETS = ("price_minute", "etf_inav_minute", "sector_index_minute")


def _manifest(dataset="price_minute", data=b"bars", generation=1, units=None):
    checksum = sha256_bytes(data)
    return build_content_window_manifest(
        dataset=dataset, session_id="msn_x", window_start=START,
        window_end=START + timedelta(minutes=1), generation=generation,
        expected_unit_ids=["a", "b"], units=units or {"received": ["b", "a"]},
        artifact_key=minute_content_artifact_key(dataset, "KR", "2026-09-07", "msn_x", "0900", checksum),
        artifact_checksum=checksum,
    )


def _manifest_key(manifest):
    return minute_content_manifest_key(
        manifest["dataset"], "KR", "2026-09-07", "msn_x", "0900",
        manifest["generation"], sha256_bytes(serialize_manifest(manifest)),
    )


@pytest.mark.parametrize("dataset", DATASETS)
def test_changed_response_uses_another_candidate_in_same_generation(dataset):
    partial = _manifest(dataset, b"partial", units={"received": ["a"], "missing": ["b"]})
    recovered = _manifest(dataset, b"recovered")
    assert partial["generation"] == recovered["generation"] == 1
    assert partial["artifact_key"] != recovered["artifact_key"]
    assert _manifest_key(partial) != _manifest_key(recovered)
    assert parse_manifest(serialize_manifest(recovered)) == recovered
    assert "/session_id=msn_x/window=0900/content=" in recovered["artifact_key"]
    assert recovered["artifact_key"].endswith("/inav.ndjson" if dataset == "etf_inav_minute" else "/bars.ndjson")
    assert _manifest_key(recovered).startswith("operations_archive/minute_manifests/")
    assert "canonical_run_artifacts" not in _manifest_key(recovered)


def test_classification_correction_reuses_artifact_but_changes_manifest():
    partial = _manifest(units={"received": ["a"], "missing": ["b"]})
    corrected = _manifest(generation=2, units={"received": ["a"], "no_trade": ["b"]})
    assert partial["artifact_key"] == corrected["artifact_key"]
    assert _manifest_key(partial) != _manifest_key(corrected)


def test_return_to_old_content_keeps_new_business_generation():
    first, second, third = _manifest(), _manifest(data=b"changed", generation=2), _manifest(generation=3)
    assert first["artifact_key"] == third["artifact_key"] != second["artifact_key"]
    assert len({_manifest_key(m) for m in (first, second, third)}) == 3


def test_manifest_normalizes_units_and_has_no_attempt_identity():
    first = _manifest(units={"received": ["b", "a"]})
    second = _manifest(units={"missing": [], "received": ["a", "b"]})
    assert serialize_manifest(first) == serialize_manifest(second)
    assert set(first) == {
        "schema_version", "dataset", "session_id", "window_start", "window_end",
        "generation", "units", "artifact_key", "artifact_checksum",
    }


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", 3), ("schema_version", True), ("schema_version", 2.0),
    ("generation", 0), ("generation", True), ("generation", 1.0),
    ("dataset", "news"), ("dataset", None), ("session_id", "../other"),
    ("session_id", "msn_other"), ("artifact_checksum", "z" * 64),
    ("artifact_checksum", "a" * 64), ("artifact_key", "legacy/bars.ndjson"),
    ("window_start", "2026-09-07T00:00:00"), ("window_start", None),
    ("window_start", "2026-09-07T00:00:01Z"),
    ("window_end", "2026-09-07T00:02:00Z"),
    ("units", []), ("units", {"received": ["a", "b"]}),
    ("units", {"received": "ab", "no_trade": [], "missing": [], "invalid": []}),
    ("units", {"received": ["a"], "no_trade": ["a"], "missing": [], "invalid": []}),
    ("units", {"received": ["a", "a"], "no_trade": [], "missing": [], "invalid": []}),
    ("units", {"received": [""], "no_trade": [], "missing": [], "invalid": []}),
])
def test_malformed_v2_fails_loud(field, value):
    manifest = _manifest()
    manifest[field] = value
    with pytest.raises(ValueError):
        parse_manifest(serialize_manifest(manifest))


@pytest.mark.parametrize("field", ["attempt", "worker_id", "manifest_uri", "created_at"])
def test_execution_metadata_cannot_change_semantic_hash(field):
    manifest = _manifest()
    manifest[field] = "different-attempt"
    with pytest.raises(ValueError, match="필드"):
        parse_manifest(serialize_manifest(manifest))


@pytest.mark.parametrize("value", [None, [], 1, "manifest"])
def test_non_object_is_rejected(value):
    with pytest.raises(ValueError, match="JSON 객체"):
        parse_manifest(serialize_manifest(value))


@pytest.mark.parametrize(("index", "value"), [
    (0, "news"), (1, "KR/other"), (2, "20260907"), (2, "2026-02-30"),
    (3, "../session"), (4, "900"), (4, "2400"), (5, "A" * 64),
])
def test_candidate_key_rejects_ambiguous_partition(index, value):
    args = ["price_minute", "KR", "2026-09-07", "msn_x", "0900", "a" * 64]
    args[index] = value
    with pytest.raises(ValueError):
        minute_content_artifact_key(*args)


def test_sessions_cannot_share_candidate_keys():
    args = ["price_minute", "KR", "2026-09-07", "msn_x", "0900", "a" * 64]
    first = minute_content_artifact_key(*args)
    args[3] = "msn_y"
    assert minute_content_artifact_key(*args) != first
