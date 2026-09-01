"""latest-good immutable artifact·CAS pointer 계약 테스트 (ALPHA-1047)."""

import json
from datetime import datetime, timezone

import pytest

from data_pipeline.lake import LocalStorage, collection_log_key
from data_pipeline.lake.latest_good import (
    LatestGoodError,
    inspect_collection_logs,
    parse_pointer,
    prepare_pointer,
    publish_pointer,
)

_NOW = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)


def _rows(fetched_at="2026-09-01T06:00:00+00:00"):
    return [{"id": "A", "fetched_at": fetched_at}]


def _plan(storage, *, dataset="etf_holdings", producer="normalize_etf",
          date="2026-09-01", run_id="run_1", data=b"parquet-one", rows=None):
    return prepare_pointer(
        storage, dataset=dataset, producer=producer, market="KR",
        as_of_date=date, run_id=run_id, artifact_bytes=data,
        rows=rows or _rows(), published_at=_NOW,
    )


def test_initial_pointer_creation_and_independent_dataset_advancement(tmp_path):
    """WHY: 세 alias가 독립이어야 한 producer 전진이 다른 last-good을 덮지 않는다."""
    storage = LocalStorage(tmp_path / "lake")
    holdings = _plan(storage)
    profile = _plan(
        storage, dataset="etf_profile", producer="normalize_etf_profile",
        run_id="run_2", data=b"profile-parquet",
    )
    instrument = _plan(
        storage, dataset="instrument_profile", producer="normalize_instrument_profile",
        run_id="run_3", data=b"instrument-parquet",
    )
    publish_pointer(storage, holdings)
    publish_pointer(storage, profile)
    publish_pointer(storage, instrument)

    saved_holdings = parse_pointer(storage.get_bytes(holdings.pointer_key))
    saved_profile = parse_pointer(storage.get_bytes(profile.pointer_key))
    saved_instrument = parse_pointer(storage.get_bytes(instrument.pointer_key))
    assert saved_holdings["dataset"] == "etf_holdings"
    assert saved_profile["dataset"] == "etf_profile"
    assert saved_instrument["dataset"] == "instrument_profile"
    assert len({
        saved_holdings["objects"][0]["key"],
        saved_profile["objects"][0]["key"],
        saved_instrument["objects"][0]["key"],
    }) == 3


def test_same_run_immutable_retry_accepts_identical_and_rejects_different_bytes(tmp_path):
    """WHY: 같은 run key를 다른 바이트로 덮으면 이미 검증한 last-good SHA가 거짓이 된다."""
    storage = LocalStorage(tmp_path / "lake")
    first = _plan(storage)
    retry = _plan(storage)
    assert retry.artifact == first.artifact

    with pytest.raises(LatestGoodError, match="immutable artifact 바이트 충돌"):
        _plan(storage, data=b"different-parquet")
    assert storage.get_bytes(first.artifact["key"]) == b"parquet-one"


def test_older_candidate_cannot_regress_and_same_order_same_sha_is_idempotent(tmp_path):
    """WHY: 늦게 끝난 과거 run이나 동일 재시도가 최신 alias를 뒤로 돌리면 안 된다."""
    storage = LocalStorage(tmp_path / "lake")
    current = _plan(storage, run_id="run_new")
    publish_pointer(storage, current)
    current_bytes = storage.get_bytes(current.pointer_key)

    older = _plan(
        storage, date="2026-08-31", run_id="run_old", data=b"older",
        rows=_rows("2026-08-31T06:00:00+00:00"),
    )
    assert older.action == "retain_older" and older.exit_code == 0
    publish_pointer(storage, older)
    same = _plan(storage, run_id="run_retry")
    assert same.action == "retain_idempotent" and same.exit_code == 0
    publish_pointer(storage, same)
    assert storage.get_bytes(current.pointer_key) == current_bytes


def test_same_order_different_sha_is_partial_conflict_and_retains_pointer(tmp_path):
    """WHY: 동순서 다른 내용은 어느 snapshot이 진실인지 정할 근거가 없어 임의 승격 금지."""
    storage = LocalStorage(tmp_path / "lake")
    current = _plan(storage, run_id="run_a")
    publish_pointer(storage, current)
    current_bytes = storage.get_bytes(current.pointer_key)

    conflict = _plan(storage, run_id="run_b", data=b"other-content")
    assert conflict.action == "retain_same_order_conflict"
    assert conflict.exit_code == 2
    publish_pointer(storage, conflict)
    assert storage.get_bytes(current.pointer_key) == current_bytes


def test_cas_loser_and_corrupt_current_pointer_fail_loud(tmp_path):
    """WHY: CAS 패자를 성공 처리하거나 손상 pointer를 초기값처럼 보면 검증 없는 alias가 선다."""
    class LosePointerCas(LocalStorage):
        def put_bytes_if_version(self, key, data, version):
            if key.endswith("/pointer.json"):
                return False
            return super().put_bytes_if_version(key, data, version)

    loser = LosePointerCas(tmp_path / "loser")
    with pytest.raises(LatestGoodError, match="CAS 소유권"):
        publish_pointer(loser, _plan(loser))
    assert [k for k in loser.list_keys("") if k.endswith("pointer.json")] == []

    corrupt = LocalStorage(tmp_path / "corrupt")
    seed = _plan(corrupt)
    corrupt.put_bytes(seed.pointer_key, b"not-json")
    with pytest.raises(LatestGoodError, match="JSON이 손상"):
        _plan(corrupt, run_id="run_2", data=b"next")
    assert corrupt.get_bytes(seed.pointer_key) == b"not-json"


def test_collection_log_must_match_raw_and_be_fully_successful(tmp_path):
    """WHY: raw가 있어도 collection partial이면 그 snapshot은 전량성이 없어 승격 불가."""
    storage = LocalStorage(tmp_path / "lake")
    success = collection_log_key("krx", "etf_holdings", "2026-09-01", "raw_1")
    partial = collection_log_key("kis", "etf_profile", "2026-09-01", "raw_1")
    storage.put_bytes(success, json.dumps({"status": "success"}).encode())
    storage.put_bytes(partial, json.dumps({"status": "partial"}).encode())

    check = inspect_collection_logs(storage, [partial, success, success])
    assert check.keys == tuple(sorted({success, partial}))
    assert check.statuses == ("partial", "success")
    assert check.complete is False

    bad = collection_log_key("krx", "instrument_profile", "2026-09-01", "raw_2")
    storage.put_bytes(bad, json.dumps({"status": "skipped"}).encode())
    with pytest.raises(LatestGoodError, match="상태가 모순"):
        inspect_collection_logs(storage, [bad])


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(schema_version=2),
    lambda p: p["objects"][0].update(sha256="A" * 64),
    lambda p: p["objects"][0].update(rows=-1),
    lambda p: p["partition"].update(as_of_date="2026-9-1"),
    lambda p: p.update(source_run_id="../escape"),
])
def test_pointer_parser_rejects_schema_identity_and_integrity_drift(tmp_path, mutate):
    """WHY: 소비자가 검증할 계약을 producer 단계부터 동일 parser로 고정한다."""
    storage = LocalStorage(tmp_path / "lake")
    pointer = _plan(storage).pointer
    mutate(pointer)
    with pytest.raises(LatestGoodError):
        parse_pointer(json.dumps(pointer).encode())
