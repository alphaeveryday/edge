"""ETF NAV 정제 Step2 — 정규화 + fact 게이트 + canonical 멱등 병합 (ALPHA-382).

raw etf_nav(KIS 단일 벤더)를 읽어 **공통 NAV fact 행으로 정규화**하고(etf_id·거래일·nav),
게이트(quality/etf_nav.validate_etf_nav)를 통과하는지 검사한다. 검증 결과는
data_quality_logs 로 남긴다(각도 H — 조용히 새거나 사라지지 않게, Rule 12).

구성종목 정제(normalize_etf)와 같은 골격이되 셋이 다르다:
  - **시간축이 trade_date**(스냅샷 기준일 as_of_date 가 아니라 거래일 시계열). 그래서 canonical
    존도 holdings 가 아니라 market_data 다(canonical_etf_nav_partition 주석 참고).
  - **참고 필드가 없다.** raw 는 `stck_clpr`(종가)·`dprt`(괴리율)도 주지만 canonical 은
    `nav` 만 낸다 — 파생 지표는 다운스트림(analysis-engine) 소관이라 quality/etf.py:15 가
    이미 그은 선이다. 종가는 이미 canonical/market_data/price_daily 가 벤더 원본으로 갖는다.
  - 그래서 게이트 사유가 전부 blocking 이다(경고 없음). NAV 는 값 자체가 fact 라 값이 나쁘면
    그 행은 쓸 데가 없다 — 마트 CHECK(nav > 0)를 여기서 미리 강제한다.

정체성 키 (market,etf_id,trade_date) 중 (market,trade_date)가 파티션, etf_id 가 파티션 내
행 키다. 벤더 교차 충돌 가드가 없다 — NAV 수집은 단일 벤더(kis)·단일 시장(KR)이라 같은
(etf_id,trade_date)를 두 벤더가 만들 수 없다(구성종목 정제와 같은 근거).

**날짜창 백필이 멱등의 이유다**: 수집(ingest-raw-nav --from/--to)이 한 번에 구간 거래일을
전부 받으므로 같은 (etf_id,trade_date)가 여러 run 의 raw 에 중복 유입된다. 병합은 최신
fetched_at 우선, 동률이면 신규(재실행 멱등).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_etf_nav_partition,
    canonical_run_manifest_key,
    canonical_run_partition_key,
    is_raw_etf_nav_key,
    parse_raw_etf_nav_key,
    quality_log_key,
)
from ..quality import BLOCKING_REASONS_ETF_NAV, validate_etf_nav

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_etf_nav"
DATASET = "etf_nav"
_PARTIAL_EXIT_CODE = 2
_CANONICAL_CAS_ATTEMPTS = 8

_FUTURE_SLACK_DAYS = 2

# market → 표준 통화. 통화는 FX 환산하지 않고 market 별로 태깅만 한다(가격·구성종목 정제 동형).
_CURRENCY = {"KR": "KRW"}


def _text(record: dict, key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _nav_number(value: object) -> float | None:
    """NAV 문자열 → finite float 또는 None. 못 읽는 값은 None(게이트가 missing_nav 로 잡음).

    KIS 는 수치를 전부 문자열로 준다('108746.33'). 콤마·공백·빈문자열·비수치·비유한(NaN/Inf)을
    흡수한다 — 특히 float('nan') 은 어떤 비교도 False 라 `nav <= 0` 게이트를 조용히 통과하고
    (각도 H coerce-to-passing), bool 은 float(True)=1.0 이 양수라 역시 통과한다. 둘 다 차단한다.
    구성종목 정제의 _ref_number 와 동형이되, NAV 는 참고 필드가 아니라 fact 라 None 이 곧 탈락이다.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        if not s or s == "-":
            return None
        value = s
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _norm_trade_date(record: dict) -> str | None:
    """거래일(stck_bsop_date 'YYYYMMDD') → 'YYYY-MM-DD'. 결측·형식불량은 None.

    strptime 왕복 검증으로 '20260231'(비달력일)·'202671'(미패딩)을 막는다 — strptime 은
    미패딩을 관대하게 받아들여 엉뚱한 날짜 파티션을 만들 수 있다(가격·구성종목 정제 동형).
    """
    raw = record.get("stck_bsop_date")
    if not isinstance(raw, str) or not raw.strip():
        return None
    text, fmt = raw.strip(), "%Y%m%d"
    try:
        parsed = datetime.strptime(text, fmt).date()
    except ValueError:
        return None
    if parsed.strftime(fmt) != text:
        return None
    return parsed.isoformat()


def _normalize(vendor: str, record: dict) -> dict:
    """벤더 raw 행 → 공통 NAV fact 행. 검증은 하지 않는다(게이트 소관, 구성종목 정제 동형).

    market·etf_id 는 수집 provenance(ingest 가 붙인 market·our_etf_id)에서, nav 는 문자열
    원본에서 수치로, 거래일은 _norm_trade_date 로 만든다."""
    market = record.get("market")
    return {
        "market": market,
        "etf_id": _text(record, "our_etf_id"),
        "trade_date": _norm_trade_date(record),
        "nav": _nav_number(record.get("nav")),
        # market 이 문자열일 때만 통화 조회 — 배열/객체 market 을 dict 키로 쓰면 TypeError 로
        # 런이 죽는다. 비문자열 market 은 게이트가 missing_market 으로 잡는다(구성종목과 동형).
        "currency": _CURRENCY.get(market) if isinstance(market, str) else None,
        "source_vendor": vendor,
        "fetched_at": _text(record, "fetched_at"),
    }


# ── canonical 적재 ───────────────────────────────────────
_CANONICAL_COLUMNS = (
    "market", "etf_id", "trade_date", "nav", "currency", "source_vendor", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    # 스키마를 명시 고정한다 — 추론에 맡기면 all-None 컬럼이 null 타입이 돼 기존 파티션과
    # 스키마가 충돌한다(가격·구성종목 정제와 같은 이유).
    return pa.schema([
        ("market", pa.string()), ("etf_id", pa.string()),
        ("trade_date", pa.string()), ("nav", pa.float64()),
        ("currency", pa.string()),
        ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])


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


def _merge_partition(
    existing: list[dict], new_rows: list[dict], conflicts: list[dict],
) -> list[dict]:
    """한 파티션을 etf_id 키로 병합한다. 최신 fetched_at 이 이기며, 최신 시각에 서로
    다른 NAV가 있으면 어느 값도 임의 선택하지 않고 해당 ID를 제외한다.

    NAV 는 구성종목과 달리 **tombstone 문제가 없다** — 한 (etf_id,trade_date)의 NAV 는 확정
    단일값이라 '사라지는 하위 행'이 없고, 벤더 정정은 같은 키를 더 늦은 fetched_at 으로
    덮어쓰는 것으로 그대로 표현된다.
    """
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in [*existing, *new_rows]:
        grouped[row["etf_id"]].append(row)

    merged: list[dict] = []
    for etf_id in sorted(grouped):
        rows = grouped[etf_id]
        latest = max(_fetched_at(row) for row in rows)
        latest_rows = [row for row in rows if _fetched_at(row) == latest]
        navs = {row["nav"] for row in latest_rows}
        if len(navs) > 1:
            sample = latest_rows[0]
            conflicts.append({
                "market": sample["market"], "etf_id": etf_id,
                "trade_date": sample["trade_date"],
                "fetched_at": sample.get("fetched_at"), "navs": sorted(navs),
                "reasons": ["same_timestamp_nav_conflict"],
            })
            continue
        merged.append(latest_rows[-1])
    return merged


def _write_canonical(
    storage: Storage, passing: list[dict], conflicts: list[dict],
    superseded: list[dict], run_id: str,
) -> tuple[list[dict], int]:
    """통과 행을 (market,trade_date) 파티션별로 기존 canonical 과 멱등 병합해 쓴다.
    반환: (이번 실행 winner manifest 파티션, 병합 뒤 canonical 행 수)."""
    by_partition: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in passing:
        by_partition[(row["market"], row["trade_date"])].append(row)

    partitions: list[dict] = []
    rows_written = 0
    for (market, trade_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_etf_nav_partition(market, trade_date)
        current_conflicts: list[dict] = []
        current_winners = _merge_partition([], new_rows, current_conflicts)
        # part 를 누적하지 않고 항상 하나로 되쓴다 — 재실행이 part-00001, 00002… 를 쌓으면
        # 병합 결과가 아니라 중복이 남는다(가격·구성종목 정제와 동형).
        key = f"{prefix}/part-00000.parquet"
        legacy_rows: list[dict] = []
        for other_key in storage.list_keys(prefix + "/"):
            if other_key.endswith(".parquet") and other_key != key:
                legacy_rows.extend(_read_parquet_rows(storage.get_bytes(other_key)))

        # shared canonical은 read-modify-write다. S3 ETag 조건부 PUT으로 한 writer만 이기게 하고,
        # 진 writer는 최신 바이트를 다시 읽어 병합한다. 무조건 PUT이면 두 실행이 같은 이전판을
        # 읽은 뒤 서로의 ID를 잃어버릴 수 있다.
        for _ in range(_CANONICAL_CAS_ATTEMPTS):
            current_bytes, version = storage.get_bytes_with_version(key)
            existing = list(legacy_rows)
            if current_bytes is not None:
                existing.extend(_read_parquet_rows(current_bytes))
            canonical_conflicts: list[dict] = []
            merged = _merge_partition(existing, new_rows, canonical_conflicts)
            parquet_bytes = _write_parquet_rows(merged)
            if storage.put_bytes_if_version(key, parquet_bytes, version):
                break
        else:
            raise OSError(f"canonical CAS 재시도 소진: {key}")

        for conflict in [*current_conflicts, *canonical_conflicts]:
            if conflict not in conflicts:
                conflicts.append(conflict)
        digest = hashlib.sha256(parquet_bytes).hexdigest()
        # shared canonical 은 다음 정제 런이 덮어쓴다. 완료 manifest가 그 가변 객체를 가리키면
        # 다음 런과 consumer가 겹칠 때 앞 manifest의 SHA가 깨진다. 같은 바이트를 run-scoped
        # artifact로 확정하고 하류에는 이 불변 키만 공개한다(공시 manifest와 같은 규약).
        run_key = canonical_run_partition_key(DATASET, run_id, trade_date)
        storage.put_bytes(run_key, parquet_bytes)
        if hashlib.sha256(storage.get_bytes(run_key)).hexdigest() != digest:
            raise OSError(f"run canonical parquet 무결성 검증 실패: {run_key}")
        conflicted = {
            item["etf_id"] for item in conflicts
            if item["market"] == market and item["trade_date"] == trade_date
        }
        merged_by_id = {row["etf_id"]: row for row in merged}
        current_by_id = {row["etf_id"]: row for row in current_winners}
        for etf_id, current in sorted(current_by_id.items()):
            if etf_id in conflicted or merged_by_id.get(etf_id) == current:
                continue
            canonical = merged_by_id.get(etf_id)
            superseded.append({
                "market": market, "etf_id": etf_id, "trade_date": trade_date,
                "current_fetched_at": current.get("fetched_at"),
                "canonical_fetched_at": canonical.get("fetched_at") if canonical else None,
                "reason": "superseded_by_canonical",
            })
        winner_ids = [
            {"etf_id": etf_id}
            for etf_id, current in sorted(current_by_id.items())
            if etf_id not in conflicted and merged_by_id.get(etf_id) == current
        ]
        if winner_ids:
            partitions.append({
                "market": market, "trade_date": trade_date, "key": run_key,
                "sha256": digest, "winner_ids": winner_ids,
            })
        rows_written += len(merged)
    return partitions, rows_written


def _manifest_bytes(run_id: str, canonical_written: bool, partitions: list[dict]) -> bytes:
    return json.dumps({
        "run_id": run_id,
        "producer": JOB_NAME,
        "canonical_written": canonical_written,
        "canonical_partitions": partitions,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw etf_nav → canonical winner manifest. 성공 0, 행 부분 실패 2, 저장 실패 1.

    input_run_id 지정 시 **그 수집 런의 raw 만** 읽어 canonical 을 멱등 적재한다
    (ALPHA-389 — SFN 이 이 경로로 돈다). 미지정이면 전체를 읽는다 — 백필·복구 수단이다."""
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    max_trade_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    manifest_key = canonical_run_manifest_key(DATASET, run_id)
    manifest_initialized = True
    exit_code = 0
    try:
        storage.put_bytes(manifest_key, _manifest_bytes(run_id, False, []))
    except Exception:
        logger.exception("canonical run manifest 초기화 실패")
        manifest_initialized = False
        exit_code = 1

    try:
        raw_keys = [k for k in storage.list_keys("raw/") if is_raw_etf_nav_key(k)]
    except Exception as exc:
        logger.exception("raw 목록 조회 실패")
        raw_keys = []
        failures = [{"raw_key": None, "reasons": ["raw_list_error"], "error": str(exc)}]
        exit_code = 1
    else:
        failures = []
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = 0
    passing: list[dict] = []

    for raw_key in raw_keys:
        try:
            vendor = parse_raw_etf_nav_key(raw_key)["source"]
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
                # 유효 JSON 이지만 객체가 아닌 행(null·배열·스칼라)은 _normalize 의 record.get 에서
                # AttributeError 로 런 전체를 죽인다 — 행 단위 실패로 격리한다(각도 H, Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor != "kis":
                # 알 수 없는 NAV 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row = _normalize(vendor, record)
                reasons = validate_etf_nav(row, max_trade_date=max_trade_date)
            except Exception as exc:
                # 예기치 못한 행 단위 크래시도 배치를 죽이지 않게 격리한다(Rule 12).
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue

            if reasons:
                # NAV 는 전 사유가 blocking 이다(참고 필드 없음) — 경고 경로가 없다.
                failures.append({
                    "market": row["market"], "etf_id": row["etf_id"],
                    "trade_date": row["trade_date"], "source_vendor": vendor,
                    "raw_key": raw_key, "reasons": reasons,
                })
                continue
            passing.append(row)

    # 스코프든 전체 런이든 canonical 을 쓴다(ALPHA-389) — 병합이 기존 행을 읽어 합친다.
    conflicts: list[dict] = []
    superseded: list[dict] = []
    partitions: list[dict] = []
    canonical_rows = 0
    canonical_written = False
    if manifest_initialized:
        try:
            partitions, canonical_rows = _write_canonical(
                storage, passing, conflicts, superseded, run_id,
            )
            canonical_written = True
        except Exception:
            logger.exception("canonical 적재·무결성 검증 실패")
            exit_code = 1

    manifest_winners = sum(len(part["winner_ids"]) for part in partitions)

    quality_written = True
    try:
        storage.put_bytes(
            quality_log_key(DATASET, checked_date, run_id),
            json.dumps({
                "run_id": run_id,
                "job_name": JOB_NAME,
                "dataset": DATASET,
                "input_run_id": input_run_id,
                "raw_files": len(raw_keys),
                "records_read": read,
                "records_passed": len(passing),
                "records_failed": len(failures),
                # 원장 관측용 공통 봉투(ALPHA-181) — 통과 행이 산출, 탈락 행이 유실이다.
                "ops": {
                    "records_out": manifest_winners,
                    "failed_records": len(failures) + len(conflicts),
                },
                "failures": failures,
                "canonical_written": canonical_written,
                "canonical_partitions": partitions,
                "canonical_partitions_written": len(partitions),
                "canonical_rows_written": canonical_rows,
                "manifest_winners": manifest_winners,
                "same_timestamp_conflicts": conflicts,
                "superseded_current_rows": superseded,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        quality_written = False
        exit_code = 1

    if canonical_written and quality_written and exit_code != 1:
        try:
            pending = _manifest_bytes(run_id, False, partitions)
            storage.put_bytes(manifest_key, pending)
            if storage.get_bytes(manifest_key) != pending:
                raise OSError("incomplete manifest 무결성 검증 실패")
            completed = _manifest_bytes(run_id, True, partitions)
            storage.put_bytes(manifest_key, completed)
            if storage.get_bytes(manifest_key) != completed:
                raise OSError("완료 manifest 무결성 검증 실패")
        except Exception:
            logger.exception("canonical run manifest 기록·무결성 검증 실패")
            exit_code = 1
            try:
                storage.put_bytes(manifest_key, _manifest_bytes(run_id, False, partitions))
            except Exception:
                logger.exception("손상 manifest 무효화 실패")

    if (failures or conflicts) and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    logger.info(
        "normalize_etf_nav 완료: raw_files=%d read=%d passed=%d failed=%d "
        "canonical_parts=%d canonical_rows=%d winners=%d conflicts=%d",
        len(raw_keys), read, len(passing), len(failures), len(partitions), canonical_rows,
        manifest_winners, len(conflicts),
    )
    return exit_code
