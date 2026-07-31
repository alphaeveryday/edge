"""1분 window artifact·manifest 쓰기 (ALPHA-665, 계획 §8 전반부).

S3 write 와 PostgreSQL commit 은 한 트랜잭션이 아니다(v0.7 9절) — 그래서 artifact 는
**결정적·불변 key 에 결정적 바이트**여야 한다: PUT 후 DB commit 전에 죽으면 재실행이
같은 key/checksum 을 재사용하고, correction 은 새 generation key 를 쓴다.

키는 lake/storage.py(경로 규약 SSOT)의 빌더가, 저장은 기존 Storage 프로토콜
(local|s3)이 담당한다 — 새 백엔드를 만들지 않는다. fenced commit transaction 과
orphan reconciler 는 후속(3-2).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..lake.storage import Storage
from .models import _json_default, canonical_json

_MANIFEST_FIELDS = frozenset(
    {"dataset", "session_id", "window_start", "window_end",
     "generation", "units", "artifact_key", "artifact_checksum"}
)
_UNIT_CLASSES = frozenset({"received", "no_trade", "missing", "invalid"})


class ArtifactImmutabilityError(RuntimeError):
    """같은 key 에 다른 바이트 — 불변 artifact 계약 위반.

    결정성이 깨졌다는 뜻이라(같은 window·generation 인데 내용이 다름) 조용히 덮으면
    "같은 checksum → 재사용" 복구 판정이 오염된다. 덮지 않고 터뜨린다.
    """


def sha256_bytes(data: bytes) -> str:
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
    generation: int, units: dict[str, list[str]], artifact_key: str, artifact_checksum: str,
) -> dict:
    """window unit manifest — 소비자가 "무엇이 오고/무거래고/누락됐나"를 복원하는 정본.

    상세 unit 목록의 정본은 이 manifest 다(DB 의 missing_units 는 작은 cache —
    v0.7 10.2). 필드·목록 순서는 고정 — checksum 이 순서에 민감하다.
    """
    unknown = set(units) - _UNIT_CLASSES
    if unknown:
        raise ValueError(f"manifest unit 분류 미지 키: {sorted(unknown)}")
    for cls, ids in units.items():
        # 문자열을 그대로 주면 sorted() 가 문자 단위 목록으로 조용히 쪼갠다
        if isinstance(ids, str) or not all(isinstance(u, str) for u in ids):
            raise ValueError(f"units[{cls!r}] 는 문자열 목록이어야 한다: {ids!r}")
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
        "units": {key: sorted(units.get(key, [])) for key in sorted(_UNIT_CLASSES)},
        "artifact_key": artifact_key,
        "artifact_checksum": artifact_checksum,
    }


def serialize_manifest(manifest: dict) -> bytes:
    return canonical_json(manifest).encode("utf-8")


def parse_manifest(data: bytes) -> dict:
    """manifest 왕복 파싱 — QC(PR 8)와 orphan reconciler(3-2)가 읽는다."""
    manifest = json.loads(data.decode("utf-8"))
    missing = _MANIFEST_FIELDS - manifest.keys()
    if missing:
        raise ValueError(f"manifest 필수 필드 누락: {sorted(missing)}")
    return manifest


def put_immutable(storage: Storage, key: str, data: bytes) -> str:
    """결정적·불변 PUT — 같은 바이트 재실행은 no-op, 다른 바이트는 fail loud.

    반환값은 저장된 바이트의 sha256(=artifact checksum). 존재 확인은 list_keys 로
    한다 — 백엔드별 not-found 예외(local FileNotFoundError vs S3 ClientError)를
    삼키다 진짜 장애를 miss 로 오독하는 경로를 피한다. 재실행은 예외 경로라
    GET-비교 비용은 무시할 수 있다.

    천장: 존재확인→PUT 은 원자적이지 않다 — 동시 이중 writer 는 상위의 window
    claim/fence 가 막고(같은 window 한 claim), 결정성이 유지되는 한 동시 PUT 도
    같은 바이트라 무해하다. S3 조건부 PUT(If-None-Match) 도입은 스테이징 실측
    (계획 §16)과 함께 판단한다.
    """
    checksum = sha256_bytes(data)
    if key in storage.list_keys(key):
        if sha256_bytes(storage.get_bytes(key)) != checksum:
            raise ArtifactImmutabilityError(
                f"불변 key 에 다른 내용: {key} — 결정성 위반, 덮어쓰기 거부"
            )
        return checksum  # 재실행 no-op — artifact 재사용 (v0.7 9절 복구 표)
    storage.put_bytes(key, data)
    return checksum
