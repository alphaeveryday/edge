"""세 마스터 입력의 latest-good immutable artifact·CAS pointer 계약 (ALPHA-1047).

shared canonical은 계속 현재 상태를 제공하지만 mutable이라 부분 실패 런이 바이트를 바꿀 수
있다. LoadInstruments의 안전한 입력은 이 모듈이 확정한 run-scoped artifact만 가리킨다.
범용 manifest 계층이 아니라 etf_holdings·etf_profile·instrument_profile 세 데이터셋의 작은
공용 계약이다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .storage import Storage, latest_good_artifact_key, latest_good_pointer_key

SCHEMA_VERSION = 1
PARTIAL_EXIT_CODE = 2
MARKET = "KR"

PRODUCERS = {
    "etf_holdings": "normalize_etf",
    "etf_profile": "normalize_etf_profile",
    "instrument_profile": "normalize_instrument_profile",
}

_POINTER_FIELDS = {
    "schema_version", "dataset", "producer", "market", "partition",
    "source_run_id", "max_fetched_at", "objects", "published_at",
}
_OBJECT_FIELDS = {"key", "sha256", "rows"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class LatestGoodError(RuntimeError):
    """포인터·artifact·collection 무결성 또는 CAS 실패 — producer exit 1."""


@dataclass(frozen=True)
class CollectionCheck:
    """requested raw와 정확히 짝인 collection log들의 완전성 판정."""

    keys: tuple[str, ...]
    statuses: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class PointerPlan:
    """quality 기록 뒤 마지막 mutation으로 publish할 포인터 계획."""

    pointer_key: str
    pointer: dict
    pointer_bytes: bytes
    artifact: dict
    base_version: str | None
    action: str
    exit_code: int

    def quality_fields(self) -> dict:
        """quality log에 남길 candidate·artifact·CAS 의도. 결과 쓰기는 CAS보다 앞이다."""
        return {
            "candidate": self.pointer,
            "artifact": self.artifact,
            "pointer_key": self.pointer_key,
            "pointer_base_version": self.base_version,
            "pointer_intended_action": self.action,
        }


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise LatestGoodError(f"latest-good {field}가 빈 문자열이다")
    return value


def _iso_date(value: object, field: str) -> date:
    text = _text(value, field)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise LatestGoodError(f"latest-good {field}가 ISO 날짜가 아니다: {text!r}") from exc
    if parsed.isoformat() != text:
        raise LatestGoodError(f"latest-good {field}가 정규 ISO 날짜가 아니다: {text!r}")
    return parsed


def _aware_datetime(value: object, field: str) -> datetime:
    text = _text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LatestGoodError(f"latest-good {field}가 ISO 시각이 아니다: {text!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LatestGoodError(f"latest-good {field}가 timezone-aware가 아니다: {text!r}")
    return parsed


def parse_pointer(
    data: bytes, *, expected_dataset: str | None = None,
    expected_producer: str | None = None, expected_market: str | None = None,
) -> dict:
    """포인터 JSON과 artifact 정체성을 엄격 검증해 dict로 반환한다."""
    try:
        pointer = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LatestGoodError("latest-good pointer JSON이 손상됐다") from exc
    if not isinstance(pointer, dict) or set(pointer) != _POINTER_FIELDS:
        fields = sorted(pointer) if isinstance(pointer, dict) else type(pointer).__name__
        raise LatestGoodError(f"latest-good pointer 필드가 다르다: {fields}")
    if pointer["schema_version"] != SCHEMA_VERSION:
        raise LatestGoodError(f"latest-good schema_version이 다르다: {pointer['schema_version']!r}")

    dataset = _text(pointer["dataset"], "dataset")
    producer = _text(pointer["producer"], "producer")
    market = _text(pointer["market"], "market")
    if dataset not in PRODUCERS or PRODUCERS[dataset] != producer:
        raise LatestGoodError(f"latest-good dataset/producer 조합이 다르다: {dataset}/{producer}")
    if market != MARKET:
        raise LatestGoodError(f"latest-good market은 KR이어야 한다: {market!r}")
    if expected_dataset is not None and dataset != expected_dataset:
        raise LatestGoodError(f"latest-good dataset 불일치: {dataset!r}")
    if expected_producer is not None and producer != expected_producer:
        raise LatestGoodError(f"latest-good producer 불일치: {producer!r}")
    if expected_market is not None and market != expected_market:
        raise LatestGoodError(f"latest-good market 불일치: {market!r}")

    partition = pointer["partition"]
    if not isinstance(partition, dict) or set(partition) != {"as_of_date"}:
        raise LatestGoodError("latest-good partition은 as_of_date 하나여야 한다")
    as_of_date = _iso_date(partition["as_of_date"], "partition.as_of_date").isoformat()
    run_id = _text(pointer["source_run_id"], "source_run_id")
    if not _RUN_ID.fullmatch(run_id):
        raise LatestGoodError(f"latest-good source_run_id가 key segment로 안전하지 않다: {run_id!r}")
    _aware_datetime(pointer["max_fetched_at"], "max_fetched_at")
    _aware_datetime(pointer["published_at"], "published_at")

    objects = pointer["objects"]
    if not isinstance(objects, list) or len(objects) != 1 or not isinstance(objects[0], dict):
        raise LatestGoodError("latest-good objects는 단일 Parquet 객체여야 한다")
    obj = objects[0]
    if set(obj) != _OBJECT_FIELDS:
        raise LatestGoodError(f"latest-good object 필드가 다르다: {sorted(obj)}")
    key = _text(obj["key"], "objects.key")
    expected_key = latest_good_artifact_key(dataset, market, as_of_date, run_id)
    if key != expected_key:
        raise LatestGoodError(f"latest-good artifact 정체성이 다르다: {key!r}")
    if not isinstance(obj["sha256"], str) or not _SHA256.fullmatch(obj["sha256"]):
        raise LatestGoodError("latest-good artifact sha256이 lowercase hex가 아니다")
    if isinstance(obj["rows"], bool) or not isinstance(obj["rows"], int) or obj["rows"] < 0:
        raise LatestGoodError("latest-good artifact rows가 음이 아닌 정수가 아니다")
    return pointer


def serialize_pointer(pointer: dict) -> bytes:
    """검증된 포인터의 결정적 JSON 바이트."""
    parse_pointer(json.dumps(pointer, ensure_ascii=False).encode("utf-8"))
    return json.dumps(
        pointer, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def max_fetched_at(rows: list[dict]) -> str:
    """완전한 artifact 행 전체의 최대 fetched_at을 UTC ISO로 반환한다."""
    if not rows:
        raise LatestGoodError("latest-good candidate가 비어 있다")
    values: list[datetime] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise LatestGoodError(f"latest-good candidate row[{index}]가 객체가 아니다")
        values.append(_aware_datetime(row.get("fetched_at"), f"row[{index}].fetched_at"))
    return max(values).astimezone(timezone.utc).isoformat()


def inspect_collection_logs(storage: Storage, keys: list[str]) -> CollectionCheck:
    """raw가 존재할 때 그 exact collection log를 GET하고 success 전량성을 판정한다."""
    unique = tuple(sorted(set(keys)))
    if not unique:
        raise LatestGoodError("raw가 있는데 matching collection log key가 없다")
    statuses: list[str] = []
    for key in unique:
        try:
            payload = json.loads(storage.get_bytes(key).decode("utf-8"))
        except Exception as exc:
            raise LatestGoodError(f"collection log 읽기/파싱 실패: {key}") from exc
        if not isinstance(payload, dict) or payload.get("status") not in {
            "success", "partial", "stopped", "error",
        }:
            raise LatestGoodError(f"raw와 collection log 상태가 모순된다: {key}")
        statuses.append(payload["status"])
    return CollectionCheck(unique, tuple(statuses), all(s == "success" for s in statuses))


def _put_immutable(storage: Storage, key: str, data: bytes) -> str:
    """동일 key 동일 바이트만 재사용하고 다른 바이트는 덮지 않는다."""
    digest = hashlib.sha256(data).hexdigest()
    current, _ = storage.get_bytes_with_version(key)
    if current is None:
        if not storage.put_bytes_if_version(key, data, None):
            current, _ = storage.get_bytes_with_version(key)
        else:
            current = data
    if current != data:
        raise LatestGoodError(f"latest-good immutable artifact 바이트 충돌: {key}")
    readback = storage.get_bytes(key)
    if readback != data or hashlib.sha256(readback).hexdigest() != digest:
        raise LatestGoodError(f"latest-good immutable artifact readback 불일치: {key}")
    return digest


def prepare_pointer(
    storage: Storage, *, dataset: str, producer: str, market: str,
    as_of_date: str, run_id: str, artifact_bytes: bytes, rows: list[dict],
    published_at: datetime | None = None,
) -> PointerPlan:
    """immutable artifact를 확정하고 현재 alias와 비교한 CAS publish 계획을 만든다."""
    if PRODUCERS.get(dataset) != producer or market != MARKET:
        raise LatestGoodError(f"latest-good producer 범위가 아니다: {dataset}/{producer}/{market}")
    _iso_date(as_of_date, "candidate.as_of_date")
    if not _RUN_ID.fullmatch(run_id):
        raise LatestGoodError(f"latest-good run_id가 key segment로 안전하지 않다: {run_id!r}")
    fetched_at = max_fetched_at(rows)
    artifact_key = latest_good_artifact_key(dataset, market, as_of_date, run_id)
    digest = _put_immutable(storage, artifact_key, artifact_bytes)
    artifact = {"key": artifact_key, "sha256": digest, "rows": len(rows)}
    now = published_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise LatestGoodError("latest-good published_at이 timezone-aware가 아니다")
    pointer = {
        "schema_version": SCHEMA_VERSION,
        "dataset": dataset,
        "producer": producer,
        "market": market,
        "partition": {"as_of_date": as_of_date},
        "source_run_id": run_id,
        "max_fetched_at": fetched_at,
        "objects": [artifact],
        "published_at": now.astimezone(timezone.utc).isoformat(),
    }
    pointer_bytes = serialize_pointer(pointer)
    pointer_key = latest_good_pointer_key(dataset, market)
    current_bytes, base_version = storage.get_bytes_with_version(pointer_key)
    action, exit_code = "advance", 0
    if current_bytes is not None:
        current = parse_pointer(
            current_bytes, expected_dataset=dataset,
            expected_producer=producer, expected_market=market,
        )
        current_order = (
            _iso_date(current["partition"]["as_of_date"], "current.as_of_date"),
            _aware_datetime(current["max_fetched_at"], "current.max_fetched_at"),
        )
        candidate_order = (
            _iso_date(as_of_date, "candidate.as_of_date"),
            _aware_datetime(fetched_at, "candidate.max_fetched_at"),
        )
        if candidate_order < current_order:
            action = "retain_older"
        elif candidate_order == current_order:
            if current["objects"][0]["sha256"] == digest:
                action = "retain_idempotent"
            else:
                action, exit_code = "retain_same_order_conflict", PARTIAL_EXIT_CODE
    return PointerPlan(
        pointer_key, pointer, pointer_bytes, artifact, base_version, action, exit_code,
    )


def publish_pointer(storage: Storage, plan: PointerPlan) -> None:
    """advance 계획만 조건부 PUT한다. False는 경쟁 writer 승리로도 성공 취급하지 않는다."""
    if plan.action != "advance":
        return
    if not storage.put_bytes_if_version(
        plan.pointer_key, plan.pointer_bytes, plan.base_version,
    ):
        raise LatestGoodError(f"latest-good pointer CAS 소유권을 잃었다: {plan.pointer_key}")
