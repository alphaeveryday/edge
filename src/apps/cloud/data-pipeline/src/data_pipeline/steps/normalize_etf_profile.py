"""ETF 프로필 정제 Step2 — 정규화 + 게이트 + canonical 멱등 병합 (ALPHA-462).

raw etf_profile(KIS `CTPF1604R`)을 읽어 ETF 마스터를 만들 최소 사실로 정규화한다.
소비자는 `load_instruments` 로, 이 canonical 이 곧 ETF `entity`·`instrument`·`etf_profile` 의
재료다 — 그래서 게이트가 다른 정제보다 보수적이다(quality/etf_profile.py 참고).

명칭 선택: `prdt_abrv_name`("KODEX 200")을 표시명으로, `prdt_name`("삼성 KODEX200 증권상장지수
투자신탁[주식]")을 법적 명칭으로 둔다. 화면·설명문에 쓰이는 건 약명이고, 법적 명칭은 감사·대조용
이라 둘 다 보존한다. `pdno`("00000A069500")는 패딩된 내부 코드라 **티커로 쓰지 않는다** —
티커는 수집 provenance 의 `our_etf_id`(우리 유니버스가 곧 진실)다.

시간축은 수집 기준일(as_of_date)이다. 개명이 일어나면 새 기준일 스냅샷이 최신을 말하고,
마스터 로더는 최신 기준일을 읽는다(구성종목 canonical 과 같은 모델).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..lake import (
    Storage,
    canonical_etf_profile_partition,
    collection_log_key,
    is_raw_etf_profile_key,
    parse_raw_etf_profile_key,
    quality_log_key,
)
from ..lake.latest_good import (
    PointerPlan,
    inspect_collection_logs,
    max_fetched_at,
    prepare_pointer,
    publish_pointer,
)
from ..quality import validate_etf_profile

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_etf_profile"
DATASET = "etf_profile"
_PARTIAL_EXIT_CODE = 2

_CURRENCY = {"KR": "KRW"}


def _text(record: dict, key: str) -> str | None:
    value = record.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normalize(vendor: str, record: dict) -> dict:
    """벤더 raw 행 → 공통 ETF 프로필 행. 검증은 하지 않는다(게이트 소관)."""
    market = record.get("market")
    return {
        "market": market,
        "etf_id": _text(record, "our_etf_id"),
        "isin": _text(record, "std_pdno"),
        "display_name": _text(record, "prdt_abrv_name"),
        "legal_name": _text(record, "prdt_name"),
        "english_name": _text(record, "prdt_eng_abrv_name"),
        "product_class": _text(record, "prdt_clsf_name"),
        "currency": _CURRENCY.get(market) if isinstance(market, str) else None,
        "as_of_date": (_text(record, "fetched_at") or "")[:10] or None,
        "source_vendor": vendor,
        "fetched_at": _text(record, "fetched_at"),
    }


# ── canonical 적재 ───────────────────────────────────────
_CANONICAL_COLUMNS = (
    "market", "etf_id", "isin", "display_name", "legal_name", "english_name",
    "product_class", "currency", "as_of_date", "source_vendor", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([(c, pa.string()) for c in _CANONICAL_COLUMNS])


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _write_parquet_rows(rows: list[dict]) -> bytes:
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _CANONICAL_COLUMNS} for r in rows], schema=_canonical_schema()
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _fetched_at(row: dict) -> datetime:
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 (market,as_of_date) 파티션을 etf_id 키로 멱등 병합(최신 fetched_at 우선)."""
    acc: dict[str, dict] = {}
    for row in [*existing, *new_rows]:
        key = row["etf_id"]
        prev = acc.get(key)
        if prev is None or _fetched_at(row) >= _fetched_at(prev):
            acc[key] = row
    return [acc[k] for k in sorted(acc, key=str)]


def _write_canonical(storage: Storage, passing: list[dict]) -> tuple[list[dict[str, str]], int]:
    """통과 행을 파티션별로 병합한다. 반환 식별자는 latest-good candidate 범위다."""
    by_partition: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in passing:
        by_partition[(row["market"], row["as_of_date"])].append(row)

    partitions: list[dict[str, str]] = []
    rows_written = 0
    for (market, as_of_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_etf_profile_partition(market, as_of_date)
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows)
        storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(merged))
        partitions.append({"market": market, "as_of_date": as_of_date})
        rows_written += len(merged)
    return partitions, rows_written


def _collection_keys(raw_keys: list[str]) -> list[str]:
    keys = []
    for raw_key in raw_keys:
        parsed = parse_raw_etf_profile_key(raw_key)
        if parsed["market"] == "KR":
            keys.append(collection_log_key(
                parsed["source"], DATASET, parsed["ingest_date"], parsed["run_id"],
            ))
    return keys


def _prepare_latest_good(
    storage: Storage, run_id: str, partitions: list[dict[str, str]],
) -> PointerPlan | None:
    candidates: list[tuple[str, str, bytes, list[dict]]] = []
    for part in partitions:
        if part["market"] != "KR":
            continue
        key = f"{canonical_etf_profile_partition('KR', part['as_of_date'])}/part-00000.parquet"
        data = storage.get_bytes(key)
        rows = _read_parquet_rows(data)
        candidates.append((part["as_of_date"], max_fetched_at(rows), data, rows))
    if not candidates:
        return None
    as_of_date, _, data, rows = max(candidates, key=lambda item: (item[0], item[1]))
    return prepare_pointer(
        storage, dataset=DATASET, producer=JOB_NAME, market="KR",
        as_of_date=as_of_date, run_id=run_id, artifact_bytes=data, rows=rows,
    )


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw etf_profile → canonical + latest-good pointer. 성공 0, partial 2, fatal 1."""
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    failures: list[dict] = []
    exit_code = 0
    raw_list_ok = True
    try:
        raw_keys = [k for k in storage.list_keys("raw/") if is_raw_etf_profile_key(k)]
    except Exception as exc:
        logger.exception("raw 목록 조회 실패")
        raw_keys = []
        failures.append({"raw_key": None, "reasons": ["raw_list_error"], "error": str(exc)})
        exit_code = 1
        raw_list_ok = False
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    collection_check = None
    collection_error = None
    if input_run_id is not None and raw_keys:
        try:
            collection_check = inspect_collection_logs(storage, _collection_keys(raw_keys))
        except Exception as exc:
            logger.exception("matching collection log 검증 실패")
            collection_error = str(exc)
            exit_code = 1

    read = 0
    passing: list[dict] = []

    for raw_key in raw_keys:
        try:
            vendor = parse_raw_etf_profile_key(raw_key)["source"]
            lines = storage.get_bytes(raw_key).decode("utf-8").splitlines()
        except Exception as exc:
            logger.exception("raw 읽기/키 파싱 실패: %s", raw_key)
            failures.append({"raw_key": raw_key, "reasons": ["raw_read_error"], "error": str(exc)})
            exit_code = 1
            continue
        for line in lines:
            if not line.strip():
                continue
            read += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                failures.append({"raw_key": raw_key, "reasons": ["unparseable_json"]})
                continue
            if not isinstance(record, dict):
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor != "kis":
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row = _normalize(vendor, record)
                reasons = validate_etf_profile(row)
                if not reasons and not row["as_of_date"]:
                    # 시간축이 없으면 파티션을 못 만든다(fetched_at 결측 — 수집이 깨진 신호).
                    reasons = ["missing_as_of_date"]
            except Exception as exc:
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue

            if reasons:
                failures.append({
                    "market": row["market"], "etf_id": row["etf_id"],
                    "display_name": row["display_name"], "source_vendor": vendor,
                    "raw_key": raw_key, "reasons": reasons,
                })
                continue
            passing.append(row)

    partitions: list[dict[str, str]] = []
    canonical_rows = 0
    canonical_written = False
    if raw_list_ok:
        try:
            partitions, canonical_rows = _write_canonical(storage, passing)
            canonical_written = True
        except Exception:
            logger.exception("canonical 적재 실패")
            exit_code = 1

    collection_incomplete = collection_check is not None and not collection_check.complete
    if (failures or collection_incomplete) and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    plan: PointerPlan | None = None
    pointer_error = collection_error
    if input_run_id is None:
        pointer_action = "retain_unscoped_recovery"
    elif exit_code == 1:
        pointer_action = "retain_fatal"
    elif exit_code == _PARTIAL_EXIT_CODE:
        pointer_action = "retain_partial"
    elif not raw_keys or not partitions:
        pointer_action = "retain_empty"
    else:
        try:
            plan = _prepare_latest_good(storage, run_id, partitions)
            pointer_action = plan.action if plan is not None else "retain_no_kr_candidate"
            if plan is not None:
                exit_code = plan.exit_code
        except Exception as exc:
            logger.exception("latest-good artifact/pointer 준비 실패")
            pointer_error = str(exc)
            pointer_action = "retain_fatal"
            exit_code = 1

    finished_at = datetime.now(timezone.utc)
    latest_good = plan.quality_fields() if plan is not None else {
        "candidate": None,
        "artifact": None,
        "pointer_key": None,
        "pointer_base_version": None,
        "pointer_intended_action": pointer_action,
    }
    latest_good.update({
        "collection_log_keys": list(collection_check.keys) if collection_check else [],
        "collection_statuses": list(collection_check.statuses) if collection_check else [],
        "error": pointer_error,
    })
    quality_key = quality_log_key(DATASET, checked_date, run_id)
    quality_payload = {
        "run_id": run_id, "job_name": JOB_NAME, "dataset": DATASET,
        "input_run_id": input_run_id,
        "raw_files": len(raw_keys), "records_read": read,
        "records_passed": len(passing), "records_failed": len(failures),
        "ops": {"records_out": len(passing), "failed_records": len(failures)},
        "failures": failures,
        "canonical_written": canonical_written,
        "canonical_partitions": partitions,
        "canonical_partitions_written": len(partitions),
        "canonical_rows_written": canonical_rows,
        "latest_good": latest_good,
        "exit_code": exit_code,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
    }
    try:
        storage.put_bytes(quality_key, json.dumps(quality_payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        return 1

    if plan is not None and plan.action == "advance" and exit_code == 0:
        try:
            publish_pointer(storage, plan)
        except Exception as exc:
            logger.exception("latest-good pointer CAS publish 실패")
            exit_code = 1
            quality_payload["exit_code"] = 1
            quality_payload["latest_good"]["pointer_publish_error"] = str(exc)
            quality_payload["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                storage.put_bytes(
                    quality_key, json.dumps(quality_payload, ensure_ascii=False).encode("utf-8"),
                )
            except Exception:
                logger.exception("pointer 실패 뒤 quality_log 최종 상태 정정 실패")

    logger.info(
        "normalize_etf_profile 완료: raw_files=%d read=%d passed=%d failed=%d "
        "canonical_parts=%d canonical_rows=%d",
        len(raw_keys), read, len(passing), len(failures), len(partitions), canonical_rows,
    )
    return exit_code
