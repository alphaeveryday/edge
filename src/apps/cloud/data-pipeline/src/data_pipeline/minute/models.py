"""1분 파이프라인 공통 request/result 계약 + 세션 window 계획 (계획 §4·§6).

가격·뉴스 Worker 가 같은 계약을 쓴다. `unit_ids` 는 가격에서 instrument ID, 뉴스에서
source ID 를 담는 중립 필드다.

불변식(생성 시점에 fail-loud — config/models.py 결):
- 모든 datetime 은 timezone-aware 다. naive 는 거부한다.
- window 는 half-open `[start, end)` 이고 `start < end` 다.
- 요청 처리 중에 현재 시각으로 window 를 재계산하지 않는다. clock 은 scheduler 와
  테스트가 주입한다(계획 §4).

결정적 checksum/ID 는 v0.7 10.6절 규약을 따른다: 고정 필드 순서의 UTF-8 JSON array,
UTC RFC3339 `Z` timestamp, lowercase SHA-256.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from ..config.models import NonBlankStr

KST = ZoneInfo("Asia/Seoul")

# KRX 정규장 09:00–15:30 (KST) = 390분. half-open 이라 마지막 window 는 [15:29, 15:30).
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)
WINDOWS_PER_SESSION = 390

ExecutionMode = Literal["one_shot", "resident"]


# ── 결정적 직렬화·checksum (v0.7 10.6절) ──


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime 은 직렬화하지 않는다")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"결정적 직렬화 불가 타입: {type(value).__name__}")


def canonical_json(payload: object) -> str:
    """고정 필드 순서(호출자가 array 순서로 고정)·UTC `Z`·공백 없는 결정적 JSON."""
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), default=_json_default
    )


def content_checksum(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ── 공통 계약 (계획 §4) ──


class CollectionRequest(BaseModel):
    """한 window 수집 요청. scheduler/테스트가 window 와 clock 을 확정해서 넘긴다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: NonBlankStr
    window_start: AwareDatetime
    window_end: AwareDatetime
    run_id: UUID
    session_id: UUID
    execution_mode: ExecutionMode
    universe_version: NonBlankStr
    unit_ids: tuple[NonBlankStr, ...]
    failure_injection: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> CollectionRequest:
        if self.window_start >= self.window_end:
            raise ValueError(
                f"half-open window 위반: start({self.window_start.isoformat()}) < "
                f"end({self.window_end.isoformat()}) 여야 한다"
            )
        if len(set(self.unit_ids)) != len(self.unit_ids):
            raise ValueError("unit_ids 에 중복이 있다 — 수량 계약이 깨진다")
        return self


class CollectionResult(BaseModel):
    """한 window 수집 결과. checksum 은 데이터에서만 유도한다 — 실행 시각과 무관."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NonBlankStr
    expected_count: int
    succeeded_count: int
    failed_count: int
    retry_count: int
    artifact_uri: str
    manifest_checksum: str
    result_checksum: str
    watermark_before: AwareDatetime | None
    watermark_after: AwareDatetime | None
    generation: int
    stage_timestamps: dict[str, AwareDatetime]

    @model_validator(mode="after")
    def _validate(self) -> CollectionResult:
        for name in ("expected_count", "succeeded_count", "failed_count", "retry_count"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 은 음수일 수 없다")
        if self.generation < 1:
            raise ValueError("generation 은 1부터 시작한다")
        if self.succeeded_count + self.failed_count > self.expected_count:
            raise ValueError("succeeded+failed 가 expected 를 초과한다")
        return self


# ── 세션 window 계획 ──


def plan_session_windows(
    session_date: date, tz: ZoneInfo = KST
) -> tuple[tuple[datetime, datetime], ...]:
    """정규장 하루의 명시적 1분 half-open window 목록 (KST 390개)."""
    open_at = datetime.combine(session_date, SESSION_OPEN, tzinfo=tz)
    close_at = datetime.combine(session_date, SESSION_CLOSE, tzinfo=tz)
    windows: list[tuple[datetime, datetime]] = []
    cursor = open_at
    while cursor < close_at:
        windows.append((cursor, cursor + timedelta(minutes=1)))
        cursor += timedelta(minutes=1)
    return tuple(windows)


# ── universe fixture 계약 ──


class Universe(BaseModel):
    """ETF + 고유 구성종목 universe. version 은 session 의 고정 속성이다(계획 §7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_version: NonBlankStr
    etf_ids: tuple[NonBlankStr, ...]
    constituent_ids: tuple[NonBlankStr, ...]

    @model_validator(mode="after")
    def _validate(self) -> Universe:
        etfs, constituents = set(self.etf_ids), set(self.constituent_ids)
        if len(etfs) != len(self.etf_ids) or len(constituents) != len(self.constituent_ids):
            raise ValueError("universe 내부에 중복 ID 가 있다")
        if etfs & constituents:
            raise ValueError(f"ETF/구성종목 ID 가 겹친다: {sorted(etfs & constituents)[:5]}")
        return self

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return self.etf_ids + self.constituent_ids

    @property
    def universe_hash(self) -> str:
        return content_checksum([self.universe_version, list(self.unit_ids)])


def load_universe(path: Path) -> Universe:
    return Universe.model_validate(json.loads(path.read_text(encoding="utf-8")))
