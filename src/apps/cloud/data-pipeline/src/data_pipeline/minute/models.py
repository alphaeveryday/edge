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
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ..config.models import NonBlankStr
from .states import RESULT_STATUSES, WINDOW_VALID, WINDOW_VALID_EMPTY

KST = ZoneInfo("Asia/Seoul")

# KRX 정규장 09:00–15:30 (KST) = 390분. half-open 이라 마지막 window 는 [15:29, 15:30).
SESSION_OPEN = time(9, 0)
SESSION_CLOSE = time(15, 30)
WINDOWS_PER_SESSION = 390

# NXT 시간외까지 거래되는 종목은 08:00–20:00 (KST) = 720분이다 (2026-08-01 실측·결정,
# `.dev/toss-openapi-실측.md`). ⚠️ 이건 **상품군 축이 아니라 종목별 속성**이다 — ETF·지수는
# 물론 개별주 001527 도 15:30 이 마지막이었다. 그래서 클래스는 규칙이 아니라 universe 가
# 선언한다(Universe.extended_hours_ids).
EXTENDED_OPEN = time(8, 0)
EXTENDED_CLOSE = time(20, 0)
WINDOWS_PER_EXTENDED_SESSION = 720

ExecutionMode = Literal["one_shot", "resident"]


class MinuteContractError(ValueError):
    """결정적 계약 위반 — 같은 입력으로 재시도해도 같은 결과다.

    Worker 는 이걸 window 실패로 접지 않고 **전파**한다: 재시도로 안 풀리는 것을
    재시도 경로에 넣으면 그 window 가 lease 만료마다 같은 요청·같은 오류를 영원히
    반복하고(소스 호출도 계속된다) 아무도 고치러 가지 않는다. 회복 불가 불변식
    위반을 크게 죽여 드러내는 기존 처리(GenerationMismatch·ArtifactImmutability)와
    같은 축이다.
    """


# ── 결정적 직렬화·checksum (v0.7 10.6절) ──


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        # tzinfo 존재만으론 부족하다 — utcoffset() 이 None 이면 Python 기준 naive 다
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("naive datetime 은 직렬화하지 않는다")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"결정적 직렬화 불가 타입: {type(value).__name__}")


def canonical_json(payload: object) -> str:
    """고정 필드 순서(호출자가 array 순서로 고정)·UTC `Z`·공백 없는 결정적 JSON.

    NaN/Infinity 는 표준 JSON 이 아니라 거부한다(allow_nan=False) — 비정상 값에
    유효한 checksum 을 부여하지 않는다.
    """
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def content_checksum(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ── 공통 계약 (계획 §4) ──

# strict=True: '3'(str)·True(bool) 가 수량으로 조용히 강제되는 것을 막는다
Count = Annotated[int, Field(strict=True, ge=0)]
Generation = Annotated[int, Field(strict=True, ge=1)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CollectionRequest(BaseModel):
    """한 window 수집 요청. scheduler/테스트가 window 와 clock 을 확정해서 넘긴다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: NonBlankStr
    window_start: AwareDatetime
    window_end: AwareDatetime
    # ADR-0027 계열 TEXT 도메인 ID (ops·minute 원장의 stable_domain_id 와 같은 축) —
    # UUID 타입으로 좁히면 원장이 만든 `msn_<hash>` session_id 가 여기서 거부된다
    run_id: NonBlankStr
    session_id: NonBlankStr
    execution_mode: ExecutionMode
    universe_version: NonBlankStr
    unit_ids: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
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
    expected_count: Count
    succeeded_count: Count
    failed_count: Count
    retry_count: Count
    artifact_uri: NonBlankStr
    manifest_checksum: Sha256Hex
    result_checksum: Sha256Hex
    watermark_before: AwareDatetime | None
    watermark_after: AwareDatetime | None
    generation: Generation
    stage_timestamps: Annotated[dict[str, AwareDatetime], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate(self) -> CollectionResult:
        if self.status not in RESULT_STATUSES:
            # 수집 결과 어휘만 허용 — 실행 축(SUCCEEDED)·원장 축(DUE/CLAIMED/MISSING)·
            # ops 관측 축(UNKNOWN)이 오면 window CHECK 저장 전 여기서 터진다
            raise ValueError(f"status {self.status!r} 는 수집 결과 어휘가 아니다")
        if self.succeeded_count + self.failed_count != self.expected_count:
            # 합이 모자라면 미분류 unit 이 조용히 사라진 것이다 — VALID 위장 금지
            raise ValueError(
                f"unit 분류 불완전: succeeded({self.succeeded_count})+failed"
                f"({self.failed_count}) != expected({self.expected_count})"
            )
        if self.failed_count > 0 and self.status in (WINDOW_VALID, WINDOW_VALID_EMPTY):
            # 실패가 있는데 VALID 면 status 만 믿는 소비자가 누락 window 를 정상 확정한다
            raise ValueError(f"failed_count={self.failed_count} 인데 status={self.status}")
        return self


# ── 세션 window 계획 ──


def plan_session_windows(
    session_date: date, *, universe: Universe | None
) -> tuple[tuple[datetime, datetime], ...]:
    """하루의 명시적 1분 half-open window 목록 (KST).

    범위는 universe 가 정한다 — 시간외 거래 종목이 하나라도 있으면 08:00–20:00(720개),
    없으면 정규장 09:00–15:30(390개)다. 계획 범위와 window 별 기대 유니버스
    (`Universe.units_at`)는 **같은 규칙**에서 나와야 한다: 계획이 더 넓으면 기대가 빈
    window 가 생기고, 좁으면 시간외 분이 아예 관측되지 않는다.

    `universe` 는 기본값 없이 **항상 명시**한다(가격 universe 가 없는 뉴스 세션은
    `None`). 기본값을 두면 시간외 종목이 있는 세션의 planner 가 인자를 빠뜨렸을 때
    390개만 계획되고, Worker 는 claim 할 행 자체가 없어 시간외 구간이 **아무 실패
    신호 없이** 누락된다.

    tz 는 KST 고정이다 — 거래시간 상수가 KST 로 정의됐고, `units_at` 도 KST 로 읽는다.
    한쪽만 다른 tz 로 부르면 계획과 기대가 통째로 어긋난다.
    """
    extended = bool(universe and universe.extended_hours_ids)
    open_at = datetime.combine(
        session_date, EXTENDED_OPEN if extended else SESSION_OPEN, tzinfo=KST
    )
    close_at = datetime.combine(
        session_date, EXTENDED_CLOSE if extended else SESSION_CLOSE, tzinfo=KST
    )
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
    etf_ids: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    constituent_ids: Annotated[tuple[NonBlankStr, ...], Field(min_length=1)]
    # 08:00–20:00 을 거래하는 unit (NXT 시간외). 나머지는 09:00–15:30 이다. 비어 있으면
    # 전 종목 정규장 — 지금까지의 390 계획과 같다. 값은 실측으로 채운다(추측 금지):
    # 잘못 넣으면 그 종목의 시간외 window 가 영구 INCOMPLETE 이고, 빠뜨리면 그 종목의
    # 시간외 분이 조용히 관측되지 않는다.
    extended_hours_ids: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _validate(self) -> Universe:
        etfs, constituents = set(self.etf_ids), set(self.constituent_ids)
        if len(etfs) != len(self.etf_ids) or len(constituents) != len(self.constituent_ids):
            raise ValueError("universe 내부에 중복 ID 가 있다")
        if etfs & constituents:
            raise ValueError(f"ETF/구성종목 ID 가 겹친다: {sorted(etfs & constituents)[:5]}")
        extended = set(self.extended_hours_ids)
        if len(extended) != len(self.extended_hours_ids):
            raise ValueError("extended_hours_ids 에 중복 ID 가 있다")
        # universe 밖 ID 는 아무 window 에서도 수집되지 않는다 — 오타를 조용히 넘기면
        # 그 종목이 시간외에 안 잡히는 이유를 아무도 못 찾는다
        if unknown := extended - (etfs | constituents):
            raise ValueError(f"extended_hours_ids 가 universe 밖 ID 를 담았다: {sorted(unknown)[:5]}")
        return self

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return self.etf_ids + self.constituent_ids

    def units_at(self, window_start: datetime) -> tuple[str, ...]:
        """그 window 에 캔들이 존재해야 하는 unit — 완전성 판정의 기대 집합이다.

        정규장 안이면 전 종목, 그 밖(프리·애프터)이면 시간외 거래 종목만이다. 이걸
        시각과 무관하게 전 종목으로 두면 15:30 이 마지막인 종목이 시간외 window 마다
        missing 으로 잡혀 그 window 가 영원히 INCOMPLETE 로 남는다(2026-08-02 dev 실증).

        경계 판정은 **KST** 로 한다(거래시간 상수와 같은 축). naive datetime 은 거부한다
        — `astimezone` 이 호스트 로컬로 해석해 같은 입력이 배포 환경마다 다른 기대 집합을
        내고, 그건 조용한 누락으로 이어진다(원장 계약 전반의 aware-only 규약과 같은 결).

        거래시간 밖은 raise 다 — 조용히 빈 집합을 주면 "기대할 게 없는 window" 와
        "계획이 잘못돼 만들어진 window" 가 같은 결과가 된다.
        """
        if window_start.tzinfo is None or window_start.tzinfo.utcoffset(window_start) is None:
            raise MinuteContractError(
                f"window_start 는 timezone-aware 여야 한다: {window_start!r}"
            )
        local = window_start.astimezone(KST).time()
        if SESSION_OPEN <= local < SESSION_CLOSE:
            return self.unit_ids
        if EXTENDED_OPEN <= local < EXTENDED_CLOSE and self.extended_hours_ids:
            return self.extended_hours_ids
        raise MinuteContractError(
            f"window {window_start.isoformat()} 는 이 universe 의 거래시간 밖이다 "
            f"— 계획되지 않아야 할 window 다"
        )

    @property
    def universe_hash(self) -> str:
        """멤버십 identity — 입력 순서에 불변(같은 구성·다른 순서 = 같은 hash).

        거래시간 클래스도 identity 에 넣는다: 멤버가 같아도 클래스가 다르면 기대
        유니버스와 window 계획이 달라진다 — 빼면 session 의 universe 충돌 가드가
        그 변경을 같은 universe 로 통과시킨다.

        단 **선언이 없으면 축 자체를 넣지 않는다** — identity 는 "이 universe 가 뜻하는
        기대"이고, 시간외 종목이 없는 universe 의 기대는 이 축이 생기기 전과 같다.
        빈 목록을 넣으면 구성이 똑같은 기존 session 이 배포만으로 다른 hash 가 돼
        UniverseConflictError 로 그날 재계획·재기동이 통째로 막힌다.
        """
        parts = [self.universe_version, sorted(self.etf_ids), sorted(self.constituent_ids)]
        if self.extended_hours_ids:
            parts.append(sorted(self.extended_hours_ids))
        return content_checksum(parts)


def load_universe(path: Path) -> Universe:
    return Universe.model_validate(json.loads(path.read_text(encoding="utf-8")))
