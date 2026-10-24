"""DB 승자 좌표로만 minute artifact 를 읽는 가격 소비자·롤업 공통 경계."""

from datetime import datetime, timezone
import re

from ..lake.storage import (
    Storage, canonical_price_minute_artifact_key, canonical_etf_inav_minute_artifact_key,
    canonical_sector_index_minute_artifact_key,
)
from .artifacts import UNIT_CLASSES, build_window_manifest, parse_manifest, sha256_bytes
from .models import KST

_LEGACY_KEYS = {
    "price_minute": canonical_price_minute_artifact_key,
    "etf_inav_minute": canonical_etf_inav_minute_artifact_key,
    "sector_index_minute": canonical_sector_index_minute_artifact_key,
}


class ArtifactReadError(RuntimeError):
    """확정 좌표의 읽기·무결성 실패 — 소비자가 재시도/실패로 드러낸다."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def read_window_artifact(
    storage: Storage, *, dataset: str, market: str, session_id: str,
    window_start: datetime, window_end: datetime, generation: int,
    checksum: str, manifest_uri: str | None, manifest_checksum: str | None,
) -> bytes:
    """구/신형 승자 manifest 와 저장 바이트를 검증한다. LIST·신형 fallback 은 없다."""
    context = f"dataset={dataset} session={session_id} window={window_start.isoformat()}"

    def fail(message: str, code: str = "MANIFEST_INVALID") -> ArtifactReadError:
        """DB 좌표를 보존한 읽기 오류를 만든다."""
        return ArtifactReadError(f"{context} {message}", code=code)

    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise fail("확정 artifact checksum 오류", "ARTIFACT_CHECKSUM_MISMATCH")
    local_start = window_start.astimezone(KST)
    key = _LEGACY_KEYS[dataset](market, local_start.date().isoformat(),
                                local_start.strftime("%H%M"), generation)
    if manifest_uri is None:
        if manifest_checksum is not None:
            raise fail("manifest URI 없이 checksum 만 존재한다")
        # manifest 좌표가 모두 없는 구형 원장만 generation 경로 호환을 허용한다.
    else:
        if not manifest_uri or not isinstance(manifest_checksum, str):
            raise fail(f"manifest 좌표 불완전 key={manifest_uri}")
        try:
            manifest_bytes = storage.get_bytes(manifest_uri)
        except Exception as error:
            raise fail(f"manifest 를 읽지 못했다 key={manifest_uri}", "MANIFEST_NOT_FOUND") from error
        if sha256_bytes(manifest_bytes) != manifest_checksum:
            raise fail(f"manifest checksum 불일치 key={manifest_uri}", "MANIFEST_CHECKSUM_MISMATCH")
        try:
            manifest = parse_manifest(manifest_bytes)
            expected = {
                "dataset": dataset, "session_id": session_id, "generation": generation,
                "window_start": window_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "window_end": window_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "artifact_checksum": checksum,
            }
            if type(manifest["generation"]) is not int or any(manifest[k] != v for k, v in expected.items()):
                raise ValueError("manifest identity 가 DB 승자와 다르다")
            units = manifest["units"]
            if not isinstance(units, dict) or set(units) != UNIT_CLASSES:
                raise ValueError("manifest unit 분류 오류")
            if any(not isinstance(ids, list) for ids in units.values()):
                raise ValueError("manifest unit 목록 오류")
            build_window_manifest(
                dataset=dataset, session_id=session_id, window_start=window_start,
                window_end=window_end, generation=generation,
                expected_unit_ids=[u for ids in units.values() for u in ids], units=units,
                artifact_key=manifest["artifact_key"], artifact_checksum=checksum,
            )
            if "schema_version" not in manifest and manifest["artifact_key"] != key:
                raise ValueError("legacy artifact_key 가 DB 승자와 다르다")
            key = manifest["artifact_key"]
        except (ValueError, TypeError, KeyError) as error:
            raise fail(f"manifest 계약 위반 key={manifest_uri}: {error}") from error
    try:
        data = storage.get_bytes(key)
    except Exception as error:
        raise fail(f"artifact 를 읽지 못했다 key={key}", "ARTIFACT_NOT_FOUND") from error
    if sha256_bytes(data) != checksum:
        raise fail(f"artifact checksum 불일치 key={key}", "ARTIFACT_CHECKSUM_MISMATCH")
    return data
