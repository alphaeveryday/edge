"""공통 JSONL instrumentation writer (계획 §6).

아키텍처 후보 비교·실측이 같은 필드 집합으로 기록되도록 필드 계약을 코드로 고정한다.
누락·미지 필드는 조용히 흘리지 않고 즉시 실패한다(fail-loud). datetime 은 UTC RFC3339
`Z` 로 직렬화한다 — checksum/ID 유도와 같은 규약(v0.7 10.6절).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .models import _json_default

JSONL_FIELDS: tuple[str, ...] = (
    "architecture",
    "execution_mode",
    "dataset",
    "run_id",
    "session_id",
    "window_start",
    "window_end",
    "scheduled_at",
    "dispatch_requested_at",
    "process_started_at",
    "collection_started_at",
    "collection_finished_at",
    "persisted_at",
    "qc_finished_at",
    "completed_at",
    "expected_count",
    "succeeded_count",
    "failed_count",
    "retry_count",
    "backlog_windows",
    "watermark_before",
    "watermark_after",
    "duplicate_count",
    "missing_count",
    "failure_injection",
    "result_checksum",
    "fixture_version",
    "seed",
    "environment",
)


class JsonlInstrumentationWriter:
    """한 record = 한 줄. 필드 집합은 JSONL_FIELDS 와 정확히 일치해야 한다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: Mapping[str, object]) -> None:
        missing = set(JSONL_FIELDS) - record.keys()
        unknown = record.keys() - set(JSONL_FIELDS)
        if missing or unknown:
            raise ValueError(
                f"JSONL 필드 계약 위반 — 누락: {sorted(missing)}, 미지: {sorted(unknown)}"
            )
        line = json.dumps(
            {key: record[key] for key in JSONL_FIELDS},  # 필드 순서 고정
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,  # NaN/Infinity 는 표준 JSON 이 아니다 — canonical_json 과 동일
            default=_json_default,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
