"""단일 시간창 분석의 순수 입력·봉·사유 계약.

I/O나 분석 방법을 담지 않는다. 원장이 확정한 1분 artifact를 읽어 같은 시간축으로
집계할 때 필요한 최소 좌표와 lineage만 고정한다.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

_SESSION_OPEN = time(9, 0)
_SESSION_CLOSE = time(15, 30)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]*")


def _text(value: str, field: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError(f"{field}가 비었다")
    return value


def _kst_minute(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(hours=9):
        raise ValueError(f"{field}는 KST(+09:00) aware datetime이어야 한다")
    if value.second or value.microsecond:
        raise ValueError(f"{field}는 분 경계여야 한다: {value.isoformat()}")
    return value


def _ohlcv(open_: Decimal, high: Decimal, low: Decimal, close: Decimal,
           volume: Decimal) -> None:
    values = (open_, high, low, close, volume)
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
        raise ValueError("OHLCV는 유한한 Decimal이어야 한다")
    if min(open_, high, low, close) <= 0 or volume < 0:
        raise ValueError("가격은 양수이고 volume은 비음수여야 한다")
    if low > min(open_, close) or high < max(open_, close) or low > high:
        raise ValueError("OHLC 범위가 모순이다")
    if volume == 0 and len({open_, high, low, close}) != 1:
        raise ValueError("무거래 봉(volume=0)은 OHLC가 같아야 한다")


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """당일 정규장 시가부터 요청 종료까지인 단일 분석창."""

    market: str
    session_id: str
    session_date: date
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "market", _text(self.market, "market"))
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        start = _kst_minute(self.start, "start")
        end = _kst_minute(self.end, "end")
        if start.date() != self.session_date or end.date() != self.session_date:
            raise ValueError("start/end와 session_date가 다르다")
        if start.time() != _SESSION_OPEN:
            raise ValueError("분석창 start는 정규장 시가 09:00이어야 한다")
        if end <= start or end.time() > _SESSION_CLOSE:
            raise ValueError("분석창 end는 09:00보다 뒤, 15:30 이하여야 한다")


@dataclass(frozen=True, slots=True)
class CommittedMinuteWindow:
    """minute_ingestion_window가 확정한 1분 artifact 좌표."""

    session_id: str
    start: datetime
    end: datetime
    generation: int
    checksum: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _text(self.session_id, "session_id"))
        start = _kst_minute(self.start, "start")
        end = _kst_minute(self.end, "end")
        if end - start != timedelta(minutes=1):
            raise ValueError("커밋 window는 정확히 1분이어야 한다")
        if self.generation < 1:
            raise ValueError("커밋 generation은 1 이상이어야 한다")
        if not _SHA256.fullmatch(self.checksum):
            raise ValueError("checksum은 lowercase sha256이어야 한다")


@dataclass(frozen=True, slots=True)
class MinuteBar:
    """확정된 1분 artifact에서 읽은 종목 봉 1개."""

    source: CommittedMinuteWindow
    unit_id: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_id", _text(self.unit_id, "unit_id"))
        _ohlcv(self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True, slots=True)
class AggregatedBar:
    """연속 1분봉 1~5개를 합친 분석용 봉과 원장 lineage."""

    unit_id: str
    start: datetime
    end: datetime
    observed_minutes: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    sources: tuple[CommittedMinuteWindow, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_id", _text(self.unit_id, "unit_id"))
        start = _kst_minute(self.start, "start")
        end = _kst_minute(self.end, "end")
        if not 1 <= self.observed_minutes <= 5:
            raise ValueError("observed_minutes는 1~5여야 한다")
        if end - start != timedelta(minutes=self.observed_minutes):
            raise ValueError("봉 길이와 observed_minutes가 다르다")
        if len(self.sources) != self.observed_minutes:
            raise ValueError("source window 수와 observed_minutes가 다르다")
        if len({source.session_id for source in self.sources}) != 1:
            raise ValueError("source windows의 session_id가 다르다")
        for index, source in enumerate(self.sources):
            expected = start + timedelta(minutes=index)
            if source.start != expected or source.end != expected + timedelta(minutes=1):
                raise ValueError("source windows가 연속된 봉 구간과 일치하지 않는다")
        _ohlcv(self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True, slots=True)
class AnalysisReason:
    """판정불가·재시도·열화 사유의 기계 코드와 운영자 문장."""

    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        code = _text(self.code, "code")
        if not _REASON_CODE.fullmatch(code):
            raise ValueError("reason code는 UPPER_SNAKE_CASE여야 한다")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "message", _text(self.message, "message"))


class WindowAggregationError(ValueError):
    """1분 입력이 완전한 공통 시간축을 만들 수 없을 때의 구조화 오류."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.reason = AnalysisReason(code, message, retryable)
        super().__init__(f"{self.reason.code}: {self.reason.message}")


def aggregate_window(
    spec: WindowSpec, minute_bars: tuple[MinuteBar, ...],
) -> tuple[AggregatedBar, ...]:
    """공통 1분축을 완전 5분봉과 마지막 부분 봉으로 집계한다.

    구성 unit 하나라도 중간 minute가 빠지면 전체를 거부한다. 서로 다른 길이의 계열을
    같은 팩터 시점으로 맞추면 결손이 0수익처럼 들어가므로 부분 성공을 만들지 않는다.
    """
    if not minute_bars:
        raise WindowAggregationError("EMPTY_MINUTE_BARS", "분석창 1분봉이 비었다")

    indexed: dict[tuple[datetime, str], MinuteBar] = {}
    unit_ids: set[str] = set()
    sources_by_minute: dict[datetime, CommittedMinuteWindow] = {}
    for bar in minute_bars:
        source = bar.source
        if source.session_id != spec.session_id:
            raise WindowAggregationError(
                "MINUTE_SESSION_MISMATCH",
                f"{bar.unit_id} {source.start.isoformat()} session={source.session_id}")
        if not spec.start <= source.start < spec.end:
            raise WindowAggregationError(
                "MINUTE_OUT_OF_WINDOW",
                f"{bar.unit_id} {source.start.isoformat()} not in "
                f"[{spec.start.isoformat()}, {spec.end.isoformat()})")
        key = (source.start, bar.unit_id)
        if key in indexed:
            raise WindowAggregationError(
                "DUPLICATE_MINUTE_BAR", f"{bar.unit_id} {source.start.isoformat()}")
        prior_source = sources_by_minute.get(source.start)
        if prior_source is not None and prior_source != source:
            raise WindowAggregationError(
                "MINUTE_SOURCE_MISMATCH",
                f"{source.start.isoformat()}에 서로 다른 generation/checksum")
        indexed[key] = bar
        unit_ids.add(bar.unit_id)
        sources_by_minute[source.start] = source

    expected_minutes: list[datetime] = []
    cursor = spec.start
    while cursor < spec.end:
        expected_minutes.append(cursor)
        cursor += timedelta(minutes=1)
    missing = [
        (minute, unit_id)
        for minute in expected_minutes
        for unit_id in sorted(unit_ids)
        if (minute, unit_id) not in indexed
    ]
    if missing:
        sample = ",".join(
            f"{unit_id}@{minute.strftime('%H:%M')}" for minute, unit_id in missing[:5])
        raise WindowAggregationError(
            "MISSING_MINUTE_BAR", f"count={len(missing)} sample={sample}")

    out: list[AggregatedBar] = []
    bucket_start = spec.start
    while bucket_start < spec.end:
        bucket_end = min(bucket_start + timedelta(minutes=5), spec.end)
        minutes = []
        cursor = bucket_start
        while cursor < bucket_end:
            minutes.append(cursor)
            cursor += timedelta(minutes=1)
        for unit_id in sorted(unit_ids):
            bars = [indexed[(minute, unit_id)] for minute in minutes]
            try:
                out.append(AggregatedBar(
                    unit_id=unit_id,
                    start=bucket_start,
                    end=bucket_end,
                    observed_minutes=len(minutes),
                    open=bars[0].open,
                    high=max(bar.high for bar in bars),
                    low=min(bar.low for bar in bars),
                    close=bars[-1].close,
                    volume=sum((bar.volume for bar in bars), Decimal("0")),
                    sources=tuple(bar.source for bar in bars),
                ))
            except ValueError as error:
                raise WindowAggregationError(
                    "AGGREGATED_BAR_INVALID",
                    f"{unit_id} {bucket_start.isoformat()} — {error}") from error
        bucket_start = bucket_end
    return tuple(out)


@dataclass(frozen=True, slots=True)
class WindowCoverage:
    """집계 전 공통 minute축의 관측·holdings·섹터 매핑 진단."""

    expected_minutes: int
    expected_units: int
    observed_pairs: int
    complete_units: int
    window_min_coverage: float
    price_weight_coverage: float | None
    sector_mapping_coverage: float | None
    missing_unit_ids: tuple[str, ...]
    missing_minutes: tuple[datetime, ...]
    unexpected_unit_ids: tuple[str, ...]


def diagnose_window(
    spec: WindowSpec,
    minute_bars: tuple[MinuteBar, ...],
    expected_unit_ids: tuple[str, ...],
    *,
    weights: Mapping[str, float] | None = None,
    sector_mapped_unit_ids: frozenset[str] = frozenset(),
) -> WindowCoverage:
    """결손을 채우거나 거부하지 않고 이후 집계가 판단할 수치와 목록을 낸다."""
    expected = tuple(str(unit).strip() for unit in expected_unit_ids)
    if not expected or any(not unit for unit in expected):
        raise ValueError("expected_unit_ids는 비지 않은 unit 목록이어야 한다")
    if len(set(expected)) != len(expected):
        raise ValueError("expected_unit_ids에 중복이 있다")
    expected_set = set(expected)

    normalized_weights: dict[str, float] = {}
    for unit, raw in (weights or {}).items():
        unit = str(unit).strip()
        if isinstance(raw, bool):
            raise ValueError(f"weight가 bool이다: {unit}")
        value = float(raw)
        if unit not in expected_set:
            raise ValueError(f"weight unit이 expected 밖이다: {unit}")
        if not unit or not math.isfinite(value) or value < 0:
            raise ValueError(f"weight가 유효하지 않다: {unit}={raw!r}")
        normalized_weights[unit] = value
    total_weight = sum(normalized_weights.values())
    if normalized_weights and total_weight <= 0:
        raise ValueError("weights 합은 양수여야 한다")

    expected_minutes: list[datetime] = []
    cursor = spec.start
    while cursor < spec.end:
        expected_minutes.append(cursor)
        cursor += timedelta(minutes=1)
    expected_minute_set = set(expected_minutes)
    observed: set[tuple[datetime, str]] = set()
    unexpected: set[str] = set()
    for bar in minute_bars:
        source = bar.source
        if source.session_id != spec.session_id:
            raise WindowAggregationError(
                "MINUTE_SESSION_MISMATCH",
                f"{bar.unit_id} {source.start.isoformat()} session={source.session_id}")
        if source.start not in expected_minute_set:
            raise WindowAggregationError(
                "MINUTE_OUT_OF_WINDOW", f"{bar.unit_id} {source.start.isoformat()}")
        pair = (source.start, bar.unit_id)
        if pair in observed:
            raise WindowAggregationError(
                "DUPLICATE_MINUTE_BAR", f"{bar.unit_id} {source.start.isoformat()}")
        observed.add(pair)
        if bar.unit_id not in expected_set:
            unexpected.add(bar.unit_id)

    per_minute = {
        minute: sum((minute, unit) in observed for unit in expected_set)
        for minute in expected_minutes
    }
    complete = {
        unit for unit in expected_set
        if all((minute, unit) in observed for minute in expected_minutes)
    }
    missing_units = tuple(sorted(expected_set - complete))
    missing_minutes = tuple(
        minute for minute in expected_minutes if per_minute[minute] < len(expected_set))
    min_coverage = min(per_minute.values()) / len(expected_set)
    price_weight_coverage = (
        sum(weight for unit, weight in normalized_weights.items() if unit in complete)
        / total_weight
        if normalized_weights else None
    )
    mapped = set(sector_mapped_unit_ids)
    sector_mapping_coverage = (
        sum(weight for unit, weight in normalized_weights.items() if unit in mapped)
        / total_weight
        if normalized_weights else None
    )
    return WindowCoverage(
        expected_minutes=len(expected_minutes),
        expected_units=len(expected_set),
        observed_pairs=sum(pair[1] in expected_set for pair in observed),
        complete_units=len(complete),
        window_min_coverage=min_coverage,
        price_weight_coverage=price_weight_coverage,
        sector_mapping_coverage=sector_mapping_coverage,
        missing_unit_ids=missing_units,
        missing_minutes=missing_minutes,
        unexpected_unit_ids=tuple(sorted(unexpected)),
    )
