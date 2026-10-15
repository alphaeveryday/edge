"""공시 배치 raw 실행의 exact-key manifest 계약."""

from __future__ import annotations

import json

from ..lake import Storage, raw_run_manifest_key

DATASET = "disclosures"
PRODUCER = "ingest_raw_disclosure"


def bytes_for(run_id: str, raw_written: bool, raw_keys: list[str]) -> bytes:
    """Manifest payload를 결정적인 JSON bytes로 직렬화한다."""
    return json.dumps({
        "run_id": run_id,
        "producer": PRODUCER,
        "raw_written": raw_written,
        "raw_keys": raw_keys,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")


def key(run_id: str) -> str:
    """공시 raw run manifest의 직접 조회 key를 반환한다."""
    return raw_run_manifest_key(DATASET, run_id)


def write_completed(storage: Storage, run_id: str, raw_keys: list[str]) -> None:
    """완료 manifest를 쓰고 저장된 bytes가 exact payload인지 확인한다."""
    manifest_key = key(run_id)
    payload = bytes_for(run_id, True, raw_keys)
    storage.put_bytes(manifest_key, payload)
    if storage.get_bytes(manifest_key) != payload:
        raise ValueError("raw run manifest 완료 bytes 무결성 검증 실패")


def load(storage: Storage, run_id: str) -> list[str]:
    """완료 manifest를 직접 GET하고 exact raw key 계보를 검증한다."""
    manifest = json.loads(storage.get_bytes(key(run_id)).decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("raw run manifest는 object여야 한다")
    if manifest.get("run_id") != run_id or manifest.get("producer") != PRODUCER:
        raise ValueError("raw run manifest 계보가 일치하지 않는다")
    if manifest.get("raw_written") is not True:
        raise ValueError("raw run manifest가 완료되지 않았다")
    raw_keys = manifest.get("raw_keys")
    if not isinstance(raw_keys, list) or any(
        not isinstance(item, str) or not item for item in raw_keys
    ):
        raise ValueError("raw run manifest raw_keys 형상이 잘못됐다")
    if len(raw_keys) != len(set(raw_keys)):
        raise ValueError("raw run manifest raw_keys가 중복됐다")
    for raw_key in raw_keys:
        parts = raw_key.split("/")
        if (
            len(parts) != 7
            or parts[0] != "raw"
            or not parts[1].startswith("source=")
            or parts[1] == "source="
            or parts[2] != f"dataset={DATASET}"
            or not parts[3].startswith("market=")
            or parts[3] == "market="
            or not parts[4].startswith("ingest_date=")
            or parts[4] == "ingest_date="
            or not parts[5].startswith("run_id=")
            or parts[5] == "run_id="
            or not parts[6].startswith("part-")
            or parts[6] == "part-.ndjson"
            or not parts[6].endswith(".ndjson")
        ):
            raise ValueError(f"공시 raw key가 아니다: {raw_key}")
        if parts[5].removeprefix("run_id=") != run_id:
            raise ValueError(f"raw key run_id 계보가 다르다: {raw_key}")
    return raw_keys
