"""KIS 지수업종 분류 원천 관측의 데이터셋 규칙 (ALPHA-1130) — 마스터 파일 정규화·수집·명세.

저장·정제·적재 공통 경로는 `source_observations` 에 있다. 계약 정본은 docs/design/etf-data-storage-plan.md §10.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone

from ..lake import Storage, canonical_sector_classification_partition
from ..sources import kis_sector_master
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

def _normalize_sector(objects: list[dict], raw_manifest: dict) -> tuple[list[dict], list[dict]]:
    """마스터 두 시장 + 업종코드 표 → 종목별 분류 스냅샷. `0000` 은 분류 없음(NULL 코드)이다.

    as_of_date 는 그 파일을 받은 KST 날짜다(원천이 기준일을 주지 않는다 — 현재값). 이름을 못 찾은 코드는
    코드를 남기고 이름만 비우며 사유를 센다. 업종코드 표를 못 받은 실행도 코드는 적재한다.
    이름을 붙인 행의 수신·가시시각은 마스터와 업종명 표 중 **늦게 받은 쪽**이다 — 이름 표를 받기 전 시각의 조회가
    아직 받지 않은 이름을 보지 않게. 근거 키(raw_key)는 행을 만든 마스터 파일이다.
    """
    names: dict[str, str] = {}
    names_fetched_at: str | None = None
    rejects: list[dict] = []
    name_files = [o for o in objects if o["request"].get("file") == kis_sector_master.SECTOR_NAME_FILE]
    if name_files:
        try:
            names, warnings = kis_sector_master.parse_sector_names(name_files[0]["body"])
            names_fetched_at = name_files[0]["fetched_at"]
        except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
            # 업종명 표가 깨져도 코드는 싣는다(이름만 비운다) — HTTP 실패 때와 같은 부분 처리.
            names, warnings = {}, [f"sector_name_file_unreadable:{type(exc).__name__}"]
        rejects.extend({"reasons": [w]} for w in warnings)
    else:
        rejects.append({"reasons": ["sector_name_file_missing"]})
    rows, unnamed = [], set()
    for obj in objects:
        file_name = obj["request"].get("file")
        if file_name not in kis_sector_master.MASTER_FILES:
            continue
        try:
            parsed, bad = kis_sector_master.parse_master(file_name, obj["body"])
        except (zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
            rejects.append({"raw_key": obj["key"], "reasons": ["master_file_unreadable"], "error": type(exc).__name__})
            continue
        rejects.extend({**b, "raw_key": obj["key"]} for b in bad)
        received = max((t for t in (obj["fetched_at"], names_fetched_at) if t), key=datetime.fromisoformat)
        # 기준일은 합성된 수신시각에서 — 이름 표가 KST 자정을 넘겨 도착하면 마스터 날짜와 갈려 DB CHECK 가 거부한다.
        as_of = datetime.fromisoformat(received).astimezone(KST).date().isoformat()
        for item in parsed:
            row = {**item, "as_of_date": as_of, "taxonomy": "KIS_INDEX_SECTOR",
                   "received_at": received, "available_at": received,
                   "availability_basis": "received", "raw_key": obj["key"], "raw_sha256": obj["sha256"]}
            for level in ("large", "medium", "small"):
                raw_code = item[f"raw_{level}_code"]
                code = None if raw_code == "0000" else raw_code
                row[f"{level}_code"], row[f"{level}_name"] = code, names.get(code) if code else None
                if code and code not in names:
                    unnamed.add(code)
            rows.append(row)
    rejects.extend({"reasons": ["sector_name_not_found"], "code": c} for c in sorted(unnamed))
    return rows, rejects


SECTOR = DatasetSpec(
    dataset="sector_classification",
    columns=(Column("market", "str"), Column("instrument_code", "str"), Column("standard_code", "str"),
             Column("name_kr", "str"), Column("security_group", "str"), Column("as_of_date", "date"),
             Column("large_code", "str"), Column("large_name", "str"), Column("medium_code", "str"),
             Column("medium_name", "str"), Column("small_code", "str"), Column("small_name", "str"),
             Column("raw_large_code", "str"), Column("raw_medium_code", "str"),
             Column("raw_small_code", "str"), Column("taxonomy", "str"), *_PROVENANCE),
    key=("market", "instrument_code", "as_of_date"),
    partition=lambda r: canonical_sector_classification_partition(r["market"], r["as_of_date"]),
    normalize=_normalize_sector,
    table="sector_classification",
    collection_vendor="kis",
)


def collect_sector(storage: Storage, client, base_url: str, run_id: str, *, now: datetime | None = None) -> int:
    """마스터 ZIP 3개를 받아 그대로 남긴다. 현재값만 주는 원천이라 날짜 인자가 없다(백필 불가)."""
    from ..failures import SafeFailureError
    from ..sources.http import StopFetch

    from ..ops.trading_calendar import is_trading_day

    producer = "ingest_raw_sector"
    done = existing_raw_manifest(storage, SECTOR.dataset, run_id)
    if done is not None:
        return record_already_collected(storage, SECTOR, run_id, producer, done)
    started_at = now or datetime.now(timezone.utc)
    if not is_trading_day(started_at.astimezone(KST).date()):
        # 휴장일엔 받지 않는다(원장 kr_trading_calendar=True 의 전제 — 일을 하지 않는 날만 SKIPPED 다).
        return write_raw_run(storage, SECTOR, run_id, producer=producer, objects=[], started_at=started_at,
                             request_scope={"files": []}, skipped_reason="non_trading_day")
    objects = []
    for file_name in (*kis_sector_master.MASTER_FILES, kis_sector_master.SECTOR_NAME_FILE):
        market = kis_sector_master.MASTER_FILES.get(file_name, ("KR", 0))[0]
        request = {"vendor": "kis", "file": file_name}
        try:
            data = client.request("GET", f"{base_url}/{file_name}", headers={}, decode=False)
            status, detail = "ok", None
            fetched_at = datetime.now(timezone.utc).isoformat()   # 받은 뒤 — as_of_date·가시시각의 근거
        except (StopFetch, SafeFailureError) as exc:
            data, status = None, "error"
            detail = f"http_{exc.status}" if isinstance(exc, StopFetch) else str(exc)
            fetched_at = datetime.now(timezone.utc).isoformat()
        objects.append(RawObject("kis", "market", market, file_name.removesuffix(".zip").replace(".", "_"),
                                 "zip", data, request, status, detail, fetched_at))
    return write_raw_run(storage, SECTOR, run_id, producer=producer, objects=objects, started_at=started_at,
                         request_scope={"files": [o.request["file"] for o in objects]})
