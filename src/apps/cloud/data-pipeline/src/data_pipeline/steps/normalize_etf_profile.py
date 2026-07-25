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
    is_raw_etf_profile_key,
    parse_raw_etf_profile_key,
    quality_log_key,
)
from ..quality import BLOCKING_REASONS_ETF_PROFILE, validate_etf_profile

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_etf_profile"
DATASET = "etf_profile"

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


def _write_canonical(storage: Storage, passing: list[dict]) -> tuple[int, int]:
    """통과 행을 (market,as_of_date) 파티션별로 멱등 병합해 쓴다. 반환: (파티션 수, 행 수)."""
    by_partition: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in passing:
        by_partition[(row["market"], row["as_of_date"])].append(row)

    parts_written = rows_written = 0
    for (market, as_of_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_etf_profile_partition(market, as_of_date)
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows)
        storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(merged))
        parts_written += 1
        rows_written += len(merged)
    return parts_written, rows_written


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw etf_profile → 정규화 → 게이트 → canonical 멱등 병합 + quality_log."""
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_etf_profile_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = 0
    failures: list[dict] = []
    passing: list[dict] = []
    exit_code = 0

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

    parts_written = canonical_rows = 0
    canonical_written = True
    try:
        parts_written, canonical_rows = _write_canonical(storage, passing)
    except Exception:
        logger.exception("canonical 적재 실패")
        canonical_written = False
        exit_code = 1

    try:
        storage.put_bytes(
            quality_log_key(DATASET, checked_date, run_id),
            json.dumps({
                "run_id": run_id, "job_name": JOB_NAME, "dataset": DATASET,
                "input_run_id": input_run_id,
                "raw_files": len(raw_keys), "records_read": read,
                "records_passed": len(passing), "records_failed": len(failures),
                # 원장 관측용 공통 봉투(ALPHA-181) — 통과 행이 산출, 탈락 행이 유실이다.
                "ops": {"records_out": len(passing), "failed_records": len(failures)},
                "failures": failures,
                "canonical_written": canonical_written,
                "canonical_partitions_written": parts_written,
                "canonical_rows_written": canonical_rows,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_etf_profile 완료: raw_files=%d read=%d passed=%d failed=%d "
        "canonical_parts=%d canonical_rows=%d",
        len(raw_keys), read, len(passing), len(failures), parts_written, canonical_rows,
    )
    return exit_code
