"""1분 window artifact·manifest 쓰기 (ALPHA-665, 계획 §8 전반부).

S3 write 와 PostgreSQL commit 은 한 트랜잭션이 아니다(v0.7 9절). 기존 writer 는
generation key 를 쓴다. content_v2 계약은 변경된 재수집도 별도 후보에 보존하도록
내용 주소 key 를 제공한다. 후보 PUT 자체는 DB 승자 확정을 의미하지 않는다.

키는 lake/storage.py(경로 규약 SSOT)의 빌더가, 저장은 기존 Storage 프로토콜
(local|s3)이 담당한다. content_v2 writer/reader 연결은 후속 변경이다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from ..lake.storage import Storage, minute_content_artifact_key
from .models import KST, _json_default, canonical_json

_MANIFEST_FIELDS = frozenset(
    {"dataset", "session_id", "window_start", "window_end",
     "generation", "units", "artifact_key", "artifact_checksum"}
)
# manifest unit 4분류 어휘 — 이 목록의 정본. 복제하면 한쪽만 고쳐진다.
UNIT_CLASSES = frozenset({"received", "no_trade", "missing", "invalid"})


class ArtifactImmutabilityError(RuntimeError):
    """같은 key 에 다른 바이트 — 불변 artifact 계약 위반.

    결정성이 깨졌다는 뜻이라(같은 window·generation 인데 내용이 다름) 조용히 덮으면
    "같은 checksum → 재사용" 복구 판정이 오염된다. 덮지 않고 터뜨린다.
    """


class ArtifactWriteConflictError(RuntimeError):
    """조건부 충돌 뒤 객체를 확인하지 못한 일시 장애 — 이후 claim 에서 재시도한다."""


def sha256_bytes(data: bytes) -> str:
    """바이트의 sha256 hex — artifact·manifest checksum 유도의 단일 함수."""
    return hashlib.sha256(data).hexdigest()


def serialize_records(records: list[dict]) -> bytes:
    """window record 들의 결정적 ndjson 직렬화 — 한 행 = canonical_json 한 줄.

    artifact checksum 은 이 바이트에서 유도한다: 직렬화가 결정적이지 않으면 같은
    데이터가 다른 checksum 을 만들어 재실행 no-op 판정이 깨진다. record 는 dict 라
    **sort_keys 로 키 순서를 정규화한다** — 호출자의 조립 순서가 바이트에 새면
    의미상 같은 재실행이 ImmutabilityError 가 된다.
    """
    return "".join(
        json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                   allow_nan=False, sort_keys=True, default=_json_default) + "\n"
        for record in records
    ).encode("utf-8")


def build_window_manifest(
    *, dataset: str, session_id: str, window_start: datetime, window_end: datetime,
    generation: int, expected_unit_ids: list[str] | tuple[str, ...],
    units: dict[str, list[str]], artifact_key: str, artifact_checksum: str,
) -> dict:
    """window unit manifest — 소비자가 "무엇이 오고/무거래고/누락됐나"를 복원하는 정본.

    상세 unit 목록의 정본은 이 manifest 다(DB 의 missing_units 는 작은 cache —
    v0.7 10.2). 필드·목록 순서는 고정 — checksum 이 순서에 민감하다.
    """
    unknown = set(units) - UNIT_CLASSES
    if unknown:
        raise ValueError(f"manifest unit 분류 미지 키: {sorted(unknown)}")
    for cls, ids in units.items():
        # 문자열은 sorted() 가 문자 단위로 쪼개고, generator 는 검증이 소비해 버려
        # 빈 목록이 정본에 남는다 — list/tuple 만 받는다
        if not isinstance(ids, (list, tuple)) or not all(isinstance(u, str) for u in ids):
            raise ValueError(f"units[{cls!r}] 는 문자열 list/tuple 이어야 한다: {ids!r}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"units[{cls!r}] 에 중복 unit: {sorted(ids)}")
    seen: dict[str, str] = {}
    for cls, ids in units.items():
        for unit in ids:
            if unit in seen:
                # 분류는 상호 배타다 — 같은 unit 이 received 이자 missing 이면
                # QC 와 orphan 복구가 서로 다른 판정을 내린다
                raise ValueError(f"unit {unit!r} 가 {seen[unit]}/{cls} 에 중복 분류됐다")
            seen[unit] = cls
    if set(seen) != set(expected_unit_ids):
        # manifest 는 요청 universe 의 **완전분할**이다 — 조립에서 빠진 unit 은
        # 어느 분류에도 없어 recovery 가 무엇을 재조회할지 복원하지 못한다
        dropped = sorted(set(expected_unit_ids) - set(seen))
        extra = sorted(set(seen) - set(expected_unit_ids))
        raise ValueError(f"unit 분할 불일치 — 누락: {dropped[:5]}, 범위 밖: {extra[:5]}")
    for name, value in (("window_start", window_start), ("window_end", window_end)):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            # naive 를 astimezone 하면 호스트 로컬로 해석돼 환경별 checksum 이 갈린다
            raise ValueError(f"{name} 은 timezone-aware 여야 한다")
    return {
        "dataset": dataset,
        "session_id": session_id,
        "window_start": window_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window_end": window_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generation": generation,
        "units": {key: sorted(units.get(key, [])) for key in sorted(UNIT_CLASSES)},
        "artifact_key": artifact_key,
        "artifact_checksum": artifact_checksum,
    }


def serialize_manifest(manifest: dict) -> bytes:
    """manifest 의 결정적 직렬화 — canonical_json 규약의 바이트(checksum 유도용)."""
    return canonical_json(manifest).encode("utf-8")


def build_content_window_manifest(
    *, dataset: str, session_id: str, window_start: datetime, window_end: datetime,
    generation: int, expected_unit_ids: list[str] | tuple[str, ...],
    units: dict[str, list[str]], artifact_key: str, artifact_checksum: str,
) -> dict:
    """KR 세 minute 레인의 v2 manifest — 실행 메타데이터 없는 정규화된 의미 계약."""
    manifest = build_window_manifest(
        dataset=dataset, session_id=session_id, window_start=window_start,
        window_end=window_end, generation=generation, expected_unit_ids=expected_unit_ids,
        units=units, artifact_key=artifact_key, artifact_checksum=artifact_checksum,
    )
    manifest["schema_version"] = 2
    validate_content_window_manifest(manifest)
    return manifest


def validate_content_window_manifest(manifest: dict) -> None:
    """v2 형상·unit 분할·window 와 artifact 내용 주소의 일치를 검증한다.

    DB 기대 identity 및 저장 바이트 checksum 대조는 reader 의 책임이다. 이 검증은
    후보 자체의 일관성만 보장하며, DB 에 확정됐다는 인증이 아니다.
    """
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS | {"schema_version"}:
        raise ValueError("content manifest 필드 불일치")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 2:
        raise ValueError("content manifest schema_version 미지원")
    generation = manifest["generation"]
    if type(generation) is not int or generation < 1:
        raise ValueError("generation 은 양의 정수여야 한다")
    for field in ("dataset", "session_id", "window_start", "window_end",
                  "artifact_key", "artifact_checksum"):
        if not isinstance(manifest[field], str):
            raise ValueError(f"content manifest {field} 는 문자열이어야 한다")
    start = datetime.fromisoformat(manifest["window_start"])
    end = datetime.fromisoformat(manifest["window_end"])
    if (start.utcoffset() is None or end.utcoffset() is None
            or start.second or start.microsecond or end - start != timedelta(minutes=1)):
        raise ValueError("content manifest window 는 timezone-aware 1분 경계여야 한다")
    units = manifest["units"]
    if not isinstance(units, dict) or set(units) != UNIT_CLASSES:
        raise ValueError("content manifest units 는 네 분류를 모두 포함해야 한다")
    for ids in units.values():
        if not isinstance(ids, list) or not all(isinstance(unit, str) and unit for unit in ids):
            raise ValueError("content manifest unit 은 비어 있지 않은 문자열 list 여야 한다")
    normalized = build_window_manifest(
        dataset=manifest["dataset"], session_id=manifest["session_id"],
        window_start=start, window_end=end, generation=generation,
        expected_unit_ids=[unit for ids in units.values() for unit in ids], units=units,
        artifact_key=manifest["artifact_key"], artifact_checksum=manifest["artifact_checksum"],
    )
    normalized["schema_version"] = 2
    if normalized != manifest:
        raise ValueError("content manifest 는 UTC timestamp·정렬된 unit 이어야 한다")
    local_start = start.astimezone(KST)
    expected_key = minute_content_artifact_key(
        manifest["dataset"], "KR", local_start.date().isoformat(), manifest["session_id"],
        local_start.strftime("%H%M"), manifest["artifact_checksum"],
    )
    if manifest["artifact_key"] != expected_key:
        raise ValueError("content manifest artifact_key 가 window/내용 주소와 다르다")


def parse_manifest(data: bytes) -> dict:
    """manifest 왕복 파싱 — QC(PR 8)와 orphan reconciler(3-2)가 읽는다."""
    manifest = json.loads(data.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest 는 JSON 객체여야 한다")
    missing = _MANIFEST_FIELDS - manifest.keys()
    if missing:
        raise ValueError(f"manifest 필수 필드 누락: {sorted(missing)}")
    if "schema_version" in manifest:
        validate_content_window_manifest(manifest)
    return manifest


def put_immutable(storage: Storage, key: str, data: bytes) -> str:
    """결정적·불변 PUT — 같은 바이트 재실행은 no-op, 다른 바이트는 fail loud.

    반환값은 저장된 바이트의 sha256. 조건부 생성의 패자는 GET 으로 같은 내용만
    재사용한다. 충돌 뒤 객체가 없으면 최대 3회 재확인 후 일시 실패로 올린다.
    권한·네트워크 오류는 결손으로 취급하지 않고 그대로 전파한다.
    """
    checksum = sha256_bytes(data)
    for _ in range(3):
        if storage.put_bytes_if_version(key, data, None):
            return checksum
        existing, _version = storage.get_bytes_with_version(key)
        if existing is None:
            continue
        if sha256_bytes(existing) != checksum:
            raise ArtifactImmutabilityError(
                f"불변 key 에 다른 내용: {key} — 결정성 위반, 덮어쓰기 거부"
            )
        return checksum  # 재실행 no-op — artifact 재사용 (v0.7 9절 복구 표)
    raise ArtifactWriteConflictError(f"조건부 PUT 충돌 후 객체 확인 실패: {key}")
