"""매크로 5계열 원천 관측의 데이터셋 규칙 (ALPHA-1130) — 정규화·수집 창·명세.

저장·정제·적재 공통 경로는 `source_observations` 에 있다. 계약 정본은 docs/design/etf-data-storage-plan.md §10.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..lake import Storage, canonical_macro_observation_partition
from ..sources import macro_series
from .source_observations import (
    _PROVENANCE,
    KST,
    Column,
    DatasetSpec,
    RawObject,
    existing_raw_manifest,
    record_already_collected,
    write_raw_run,
)

def _normalize_macro(objects: list[dict], raw_manifest: dict) -> tuple[list[dict], list[dict]]:
    """ok 응답 → 관측 행. 요청 창 밖·미완 기간은 거부로 센다(값을 버리는 이유를 남긴다)."""
    rows, rejects = [], []
    for obj in objects:
        series_id = obj["series_id"]
        series = macro_series.SERIES[series_id]
        window = raw_manifest["request_scope"]["windows"][series_id]
        try:
            good, bad = macro_series.parse(series_id, obj["body"])
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError) as exc:
            # 한 응답의 파손이 다른 계열의 정제를 막지 않게 그 응답만 거부한다.
            rejects.append({"series_id": series_id, "raw_key": obj["key"], "reasons": ["unparseable_response"],
                            "error": type(exc).__name__})
            continue
        for item in bad:
            rejects.append({"series_id": series_id, "raw_key": obj["key"], **item})
        for item in good:
            day = item["observation_date"]
            reasons = []
            if not window["from"] <= day <= window["to"]:
                reasons.append("outside_request_window")
            if not macro_series.period_complete(series_id, day, obj["fetched_at"]):
                reasons.append("incomplete_period")
            if reasons:
                rejects.append({"series_id": series_id, "observation_date": day, "raw_key": obj["key"],
                                "reasons": reasons})
                continue
            rows.append({"series_id": series_id, "observation_date": day, "value": item["value"],
                         "unit": series.unit, "source_vendor": series.vendor,
                         "source_series": series.source_series,
                         # 공급자가 공표 시각을 주지 않는다 — 가시시각 = 실제 수신시각(2026-09-30 결정).
                         "received_at": obj["fetched_at"], "available_at": obj["fetched_at"],
                         "availability_basis": "received",
                         "raw_key": obj["key"], "raw_sha256": obj["sha256"]})
    return rows, rejects


MACRO = DatasetSpec(
    dataset="macro_observation",
    columns=(Column("series_id", "str"), Column("observation_date", "date"), Column("value", "str"),
             Column("unit", "str"), Column("source_vendor", "str"), Column("source_series", "str"),
             *_PROVENANCE),
    key=("series_id", "observation_date"),
    partition=lambda r: canonical_macro_observation_partition(r["series_id"], r["observation_date"]),
    normalize=_normalize_macro,
    table="macro_observation",
    collection_vendor="multi",
)


def macro_window(series_id: str, today_kst: date, from_date: str | None, to_date: str | None
                 ) -> tuple[date, date]:
    """정기 수집은 (어제 − 계열 소급일 ~ 어제), 백필은 명시한 관측 기간. 오늘은 넣지 않는다 —
    진행 중 관측은 확정값이 아니다. 백필 끝날짜가 오늘 이후면 거부한다(미래를 과거로 라벨하지 않게)."""
    series = macro_series.SERIES[series_id]
    yesterday = today_kst - timedelta(days=1)
    if from_date is None and to_date is None:
        start, end = yesterday - timedelta(days=series.lookback_days - 1), yesterday
    elif from_date is None or to_date is None:
        raise SystemExit("백필은 --from 과 --to 를 함께 준다")
    else:
        start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
        if end > yesterday:
            raise SystemExit(f"--to({end})가 어제({yesterday}) 이후다 — 진행 중·미래 관측은 수집하지 않는다")
        if start > end:
            # 월초 맞춤 전에 검사한다 — 맞춘 뒤엔 역전된 월 안 창(02-20~02-01)이 유효한 창으로 바뀐다.
            raise SystemExit(f"관측 기간이 역전됐다: {start} > {end}")
    # 월별 계열은 월 단위로 요청·검사한다(관측일=기준월 1일). 창 시작을 월초로 맞추지 않으면 요청에 포함된
    # 첫 달이 정제의 창 검사에서 떨어진다.
    if series.frequency == "M":
        start = start.replace(day=1)
    return start, end


def collect_macro(storage: Storage, source, run_id: str, *, series_ids: list[str],
                  from_date: str | None, to_date: str | None, now: datetime | None = None) -> int:
    """계열별 요청 창을 부르고 응답을 그대로 남긴다. 키 없는 계열은 부르지 않고 실패로 드러낸다."""
    producer = "ingest_raw_macro"
    done = existing_raw_manifest(storage, MACRO.dataset, run_id)
    if done is not None:
        return record_already_collected(storage, MACRO, run_id, producer, done)
    started_at = now or datetime.now(timezone.utc)
    today_kst = started_at.astimezone(KST).date()
    unknown = sorted(set(series_ids) - set(macro_series.SERIES))
    if unknown:
        raise SystemExit(f"모르는 매크로 계열: {unknown}")
    windows = {s: macro_window(s, today_kst, from_date, to_date) for s in series_ids}
    objects: list[RawObject] = []
    missing = set(source.missing_credentials(series_ids))
    for series_id in series_ids:
        series = macro_series.SERIES[series_id]
        start, end = windows[series_id]
        if series_id in missing:
            objects.append(RawObject(series.vendor, "series_id", series_id, series_id, "json", None,
                                     {"vendor": series.vendor}, "error", "missing_credentials",
                                     started_at.isoformat()))
            continue
        for w_start, w_end in macro_series.request_windows(series, start, end):
            result = source.fetch(series_id, w_start, w_end)
            objects.append(RawObject(series.vendor, "series_id", series_id,
                                     f"{series_id}-{w_start:%Y%m%d}-{w_end:%Y%m%d}", "json",
                                     result.body, result.request, result.status, result.detail,
                                     result.fetched_at))
    scope = {"series": series_ids,
             "windows": {s: {"from": a.isoformat(), "to": b.isoformat()} for s, (a, b) in windows.items()},
             "mode": "backfill" if from_date else "regular"}
    return write_raw_run(storage, MACRO, run_id, producer=producer, objects=objects,
                         started_at=started_at, request_scope=scope)
