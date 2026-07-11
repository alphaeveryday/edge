"""공시 정제 Step2 — 사업보고서 사업부문별 매출 파싱 + fact 게이트 + canonical 멱등 병합
(ALPHA-346).

raw disclosures(메타 ndjson + 본문 ZIP, ALPHA-344)를 읽어 **사업보고서 '사업의 내용'의
사업부문별 매출 표를 파싱**(parse_segments)해 공통 사업부문 fact 로 정규화하고, 게이트를
통과하는지 검사한다(quality/disclosure.validate_segment_fact). 검증 결과는 data_quality_logs
로 남긴다(각도 H — 조용히 새거나 사라지지 않게, Rule 12).

공급계약 정제(normalize_disclosure)와 동형이나 두 가지가 다르다:
  1. **1 문서 → N 행**(한 사업보고서에 사업부문 여러 개) — 문서를 파싱해 나온 각 부문을 fact 로.
  2. **행키 = (rcept_no, segment_ordinal)** — segment_name 은 한 문서 안에서 유일하지 않다
     (제품/용역 sub-row 로 같은 부문이 여러 번 나옴). 그래서 파스 순서 인덱스(ordinal)로 키를
     잡아 유일성·멱등을 보장한다(segment_name 은 데이터 컬럼).

파서(parse_dart_segment)는 팀원 정준영 프로토타입(segments-v2) 이식 — graph 투영·theme
링킹은 범위 밖. 본문 euc-kr ZIP 추출은 공급계약 파서(extract_document_html)를 재사용한다.
정정 supersession(point-in-time)은 이 스텝 범위 밖(공급계약과 동일 — 다운스트림 소관).
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_business_segment_fact_partition,
    is_raw_disclosure_key,
    parse_raw_disclosure_key,
    quality_log_key,
)
from ..parse_dart_segment import PARSER_VERSION, parse_segments
from ..parse_dart_supply import extract_document_html
from ..quality import BLOCKING_REASONS_SEGMENT, validate_segment_fact

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_disclosure_segment"
DATASET = "business_segment_fact"

_FUTURE_SLACK_DAYS = 2
_BUSINESS_REPORT_KEYWORD = "사업보고서"


# ponytail: normalize_disclosure 의 _text·_norm_report_date 와 같은 3~10줄 헬퍼 — 스텝 간
# private import 커플링 대신 재기술한다(소스 self-contained 관례, 로직 동일).
def _text(record: dict, key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _is_business_report(report_nm: object) -> bool:
    """report_nm 이 사업보고서 유형인지 — NFKC 정규화 후 '사업보고서' 부분일치([기재정정] 포함)."""
    return isinstance(report_nm, str) and _BUSINESS_REPORT_KEYWORD in unicodedata.normalize("NFKC", report_nm)


def _norm_report_date(rcept_dt: object) -> str | None:
    """rcept_dt('YYYYMMDD') → 'YYYY-MM-DD'. 결측·비달력일·형식불량은 None(게이트가 잡음).
    strptime 왕복 검증으로 '20260231'·'202671' 같은 비달력일·미패딩을 막는다(가격 정제 동형)."""
    if not isinstance(rcept_dt, str) or not rcept_dt.strip():
        return None
    text = rcept_dt.strip()
    try:
        parsed = datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None
    if parsed.strftime("%Y%m%d") != text:
        return None
    return parsed.isoformat()


def _to_facts(record: dict, vendor: str, segments: list[dict]) -> list[dict]:
    """파서 rows + raw 메타 provenance → 사업부문 fact 행 리스트(부문당 1행).

    segment_ordinal(파스 순서)이 (rcept_no, segment_ordinal) 행키의 일부다 — segment_name 은
    한 문서 안에서 유일하지 않아 키가 못 된다. corp_name·source_url 등은 메타 provenance."""
    base = {
        "rcept_no": _text(record, "rcept_no"),
        "source_vendor": vendor,
        "corp_code": _text(record, "corp_code"),
        "ticker": _text(record, "our_ticker"),
        "corp_name": _text(record, "corp_name"),
        "report_date": _norm_report_date(record.get("rcept_dt")),
        "source_url": _text(record, "source_url"),
        "parser_version": PARSER_VERSION,
        "fetched_at": _text(record, "fetched_at"),
    }
    facts: list[dict] = []
    for ordinal, seg in enumerate(segments):
        facts.append({
            **base,
            "segment_ordinal": ordinal,
            "segment_name": seg.get("segment_name"),
            "revenue_krw": seg.get("revenue_krw"),
            "revenue_share_pct": seg.get("revenue_share_pct"),
            "share_basis": seg.get("share_basis"),
            "period": seg.get("period"),
        })
    return facts


# ── canonical 적재 ───────────────────────────────────────
_CANONICAL_COLUMNS = (
    "rcept_no", "source_vendor", "corp_code", "ticker", "corp_name", "segment_ordinal",
    "segment_name", "revenue_krw", "revenue_share_pct", "share_basis", "period",
    "report_date", "source_url", "parser_version", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([
        ("rcept_no", pa.string()), ("source_vendor", pa.string()),
        ("corp_code", pa.string()), ("ticker", pa.string()), ("corp_name", pa.string()),
        ("segment_ordinal", pa.int64()), ("segment_name", pa.string()),
        ("revenue_krw", pa.int64()), ("revenue_share_pct", pa.float64()),
        ("share_basis", pa.string()), ("period", pa.string()), ("report_date", pa.string()),
        ("source_url", pa.string()), ("parser_version", pa.string()), ("fetched_at", pa.string()),
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


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 report_date 파티션을 (rcept_no, segment_ordinal) 키로 멱등 병합. 같은 키 재적재는
    최신 fetched_at 우선, 동률이면 신규(멱등 재실행).

    ponytail: 재파싱이 부문 수를 줄이면(파서 로직 변경 등) 옛 높은 ordinal 행이 남는 얕은
    staleness 가 있다(같은 입력 재실행은 결정적이라 안정). 부문 수 감소는 파서 변경 시에만
    생기고, 그땐 parser_version 이 바뀌므로 후속 재적재로 정리 — full-tombstone 은 후속."""
    acc: dict[tuple, dict] = {}
    for row in [*existing, *new_rows]:
        key = (row["rcept_no"], row["segment_ordinal"])
        prev = acc.get(key)
        if prev is None or _fetched_at(row) >= _fetched_at(prev):
            acc[key] = row
    return [acc[k] for k in sorted(acc, key=lambda k: (str(k[0]), int(k[1])))]


def _write_canonical(storage: Storage, passing: list[dict]) -> tuple[int, int]:
    by_partition: dict[str, list[dict]] = defaultdict(list)
    for row in passing:
        by_partition[row["report_date"]].append(row)

    parts_written = rows_written = 0
    for report_date, new_rows in sorted(by_partition.items()):
        prefix = canonical_business_segment_fact_partition(report_date)
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
    """raw disclosures → 사업보고서 사업부문 파싱 → 게이트 → canonical 멱등 병합 + quality_log.
    성공 0, 장애 시 비0. input_run_id 지정 시 그 수집 런만 재검증(canonical 은 전체 런이 authoritative)."""
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    max_report_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_disclosure_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = routed = skipped_type = segments_extracted = 0
    failures: list[dict] = []
    warnings: list[dict] = []
    passing: list[dict] = []
    exit_code = 0

    for raw_key in raw_keys:
        try:
            vendor = parse_raw_disclosure_key(raw_key)["source"]
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
            if vendor != "dart":
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            report_nm = record.get("report_nm")
            if not isinstance(report_nm, str):
                failures.append({"rcept_no": _text(record, "rcept_no"), "raw_key": raw_key,
                                 "reasons": ["malformed_report_nm"]})
                continue
            if not _is_business_report(report_nm):
                # 대상 아님(공급계약 등) — 스킵(실패 아님, 공급계약은 normalize-disclosure 소관).
                skipped_type += 1
                continue
            routed += 1

            rcept_no = _text(record, "rcept_no")
            ref = {"rcept_no": rcept_no, "raw_key": raw_key}
            doc_path = _text(record, "document_raw_path")
            if not doc_path:
                failures.append({**ref, "reasons": ["missing_document_body"]})
                continue
            try:
                html = extract_document_html(storage.get_bytes(doc_path))
                segments, _stats = parse_segments(html)
                facts = _to_facts(record, vendor, segments)
            except Exception as exc:
                logger.exception("사업부문 본문 파싱 실패(격리): %s", ref)
                failures.append({**ref, "reasons": ["parse_error"], "error": str(exc)})
                continue

            if not facts:
                # 사업부문 표를 하나도 못 뽑음(malformed·해당 섹션 없음) — canonical 가치 없음.
                failures.append({**ref, "reasons": ["no_segments_parsed"]})
                continue
            segments_extracted += len(facts)

            for fact in facts:
                fref = {"rcept_no": rcept_no, "segment_ordinal": fact["segment_ordinal"],
                        "segment_name": fact["segment_name"], "report_date": fact["report_date"],
                        "raw_key": raw_key}
                reasons = validate_segment_fact(fact, max_report_date=max_report_date)
                blocking = [r for r in reasons if r in BLOCKING_REASONS_SEGMENT]
                if blocking:
                    failures.append({**fref, "reasons": reasons})
                    continue
                passing.append(fact)
                warn = [r for r in reasons if r not in BLOCKING_REASONS_SEGMENT]
                if warn:
                    warnings.append({**fref, "reasons": warn})

    parts_written = canonical_rows = 0
    canonical_written = input_run_id is None
    if canonical_written:
        try:
            parts_written, canonical_rows = _write_canonical(storage, passing)
        except Exception:
            logger.exception("canonical 적재 실패")
            exit_code = 1
    else:
        logger.info("스코프(--input-run-id) 실행 — 재검증만, canonical 은 전체 런이 쓴다")

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
                "records_routed_business_report": routed,
                "records_skipped_type": skipped_type,
                "segments_extracted": segments_extracted,
                "records_passed": len(passing),
                "records_failed": len(failures),
                "records_warned": len(warnings),
                "failures": failures,
                "warnings": warnings,
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
        "normalize_disclosure_segment 완료: raw_files=%d read=%d routed=%d skipped_type=%d "
        "segments=%d passed=%d failed=%d warned=%d canonical_parts=%d canonical_rows=%d",
        len(raw_keys), read, routed, skipped_type, segments_extracted, len(passing),
        len(failures), len(warnings), parts_written, canonical_rows,
    )
    return exit_code
