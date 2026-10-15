"""배치 공시 raw manifest의 exact-scope/fail-loud 계약."""

import json

import pytest

from data_pipeline.lake import LocalStorage
from data_pipeline.steps import disclosure_raw_manifest
from data_pipeline.steps import normalize_disclosure, normalize_disclosure_segment


class _ReadSpy:
    def __init__(self, inner):
        self.inner = inner
        self.get_calls: list[str] = []
        self.list_calls: list[str] = []

    def get_bytes(self, key):
        self.get_calls.append(key)
        return self.inner.get_bytes(key)

    def list_keys(self, prefix):
        self.list_calls.append(prefix)
        return self.inner.list_keys(prefix)

    def put_bytes(self, key, data):
        return self.inner.put_bytes(key, data)


@pytest.mark.parametrize("step", [normalize_disclosure, normalize_disclosure_segment])
def test_input_run_id_gets_only_completed_manifest_without_raw_list(tmp_path, step):
    # WHY(ALPHA-1054): input_run_id를 raw/ LIST 뒤 문자열 필터로 해석하면 버킷 성장 비용을
    # 치르고, 범위 근거도 producer가 실제 쓴 exact key가 아니라 경로 추측으로 되돌아간다.
    inner = LocalStorage(tmp_path / "lake")
    inner.put_bytes(
        disclosure_raw_manifest.key("R1"),
        disclosure_raw_manifest.bytes_for("R1", True, []),
    )
    storage = _ReadSpy(inner)

    assert step.run(storage, "N1", input_run_id="R1") == 0
    assert storage.get_calls[0] == disclosure_raw_manifest.key("R1")
    assert not [key for key in storage.get_calls if key.startswith("raw/")]
    assert "raw/" not in storage.list_calls


@pytest.mark.parametrize("step", [normalize_disclosure, normalize_disclosure_segment])
@pytest.mark.parametrize(
    "damage", ["missing", "json", "incomplete", "wrong_run", "duplicate", "malformed_key"]
)
def test_bad_manifest_fails_without_falling_back_to_historical_raw(tmp_path, step, damage):
    # WHY(ALPHA-1054): 권위 manifest 오류를 과거 raw 스캔으로 복구하면 현재 run이 승인하지 않은
    # 관측까지 canonical에 섞인다. 결손·불완전·손상은 manifest GET 뒤 exit 1로 닫혀야 한다.
    inner = LocalStorage(tmp_path / "lake")
    key = disclosure_raw_manifest.key("R1")
    if damage == "json":
        inner.put_bytes(key, b"{")
    elif damage != "missing":
        manifest = json.loads(disclosure_raw_manifest.bytes_for("R1", True, []))
        if damage == "incomplete":
            manifest["raw_written"] = False
        elif damage == "wrong_run":
            manifest["run_id"] = "OLD"
        elif damage == "duplicate":
            raw_key = (
                "raw/source=dart/dataset=disclosures/market=KR/ingest_date=2026-08-29/"
                "run_id=R1/part-00000.ndjson"
            )
            manifest["raw_keys"] = [raw_key, raw_key]
        elif damage == "malformed_key":
            manifest["raw_keys"] = [
                "raw/source=dart/dataset=disclosures/market=KR/ingest_date=2026-08-29/"
                "run_id=OLD/run_id=R1/part-00000.ndjson"
            ]
        inner.put_bytes(key, json.dumps(manifest).encode())
    storage = _ReadSpy(inner)

    assert step.run(storage, "N1", input_run_id="R1") == 1
    assert storage.get_calls == [key]
    assert "raw/" not in storage.list_calls
