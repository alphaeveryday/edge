"""공시 정제 Step2 — 공급계약 본문 파싱 + fact 게이트 + canonical 멱등 병합 (ALPHA-345).

raw disclosures(메타 ndjson + 본문 ZIP, ALPHA-344)를 읽어 **단일판매ㆍ공급계약 본문을
파싱**해 공통 공급계약 fact 로 정규화하고, 게이트(quality/disclosure.validate_supply_fact)를
통과하는지 검사한다. 검증 결과는 `data_quality_logs` 로 남긴다 — 몇 건 읽고/라우팅하고/통과/
탈락(blocking)/경고했는지와 사유를 드러내, 분석에 못 쓰는 fact 가 조용히 새거나 사라지지
않게 한다(AGENTS Rule 12).

게이트를 통과한 fact 는 `canonical/disclosures/supply_contract_fact/report_date=…/` 에
**rcept_no(14자리 접수번호=문서키) 정체성 키로 멱등 병합**한다. canonical 은 run_id 가 없어
같은 raw 를 몇 번 정제해도 결과가 같다. 같은 rcept_no 재적재(정정본 재수집)는 최신 fetched_at
이 이긴다. source_vendor(dart)는 현재 KR·DART 단독이라 파티션이 아니라 컬럼(provenance)이다.

파이프라인(가격·뉴스 정제와 동형):
  1. raw 메타 ndjson 스캔(is_raw_disclosure_key). --input-run-id 로 특정 수집 런만 재검증.
  2. report_nm 으로 **doc_type 라우팅** — 공급계약만 이 스텝이 처리(사업보고서 등은 스킵,
     segment fact 는 후속 스토리). 본문은 document_raw_path 의 ZIP 을 열어 euc-kr 디코딩·파싱.
  3. 파서 출력 + 메타 provenance(rcept_no·corp_code·ticker·corp_name·source_url·rcept_dt)
     조인 → fact 행.
  4. 게이트: 정체성(rcept_no)·시간축(report_date) blocking, 값 이상은 경고로 표면화.
  5. 통과 fact 를 report_date 파티션에 rcept_no 키로 멱등 병합. quality_log 기록.

파서(parse_dart_supply)는 팀원 정준영 프로토타입 이식 — 출처는 그 모듈 헤더 참조. graph
투영(supplier→customer edge·theme 링킹)은 edge 범위 밖(analysis-engine 소관)이라 안 한다.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_supply_contract_fact_partition,
    is_raw_disclosure_key,
    parse_raw_disclosure_key,
    quality_log_key,
)
from ..parse_dart_supply import extract_document_html, parse_supply
from ..quality import BLOCKING_REASONS_DISCLOSURE, validate_supply_fact

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_disclosure"
DATASET = "supply_contract_fact"

# 파서 버전 — 재파싱 추적용(파서 로직이 바뀌면 올린다). 이식 시점의 supply 파서 = v1.
PARSER_VERSION = "supply-v1"

# report_date 상한 여유 — 검증 실행일 기준 이 일수까지의 미래 접수일은 허용(수집 지연·TZ 여유).
_FUTURE_SLACK_DAYS = 2

# 공급계약 **체결** 공시 판정 키워드. report_nm 은 가운뎃점 ㆍ(U+318D)·꼬리 패딩·[기재정정]
# 접두가 있으나(실측), "공급계약"·"체결" 은 그 사이에서 온전한 부분문자열이라 NFKC 정규화 후
# 부분일치로 잡는다. **"체결" 을 함께 요구**해 같은 "공급계약" 을 포함하는 해지(공급계약해지)를
# 새 계약 fact 로 오적재하지 않는다 — raw 필터가 "공급계약" 부분일치라 해지도 raw 에 들어온다
# (bronze); doc_type 판별은 정제 소관이다(Codex P2). [기재정정]…체결 정정본은 "체결" 을 유지.
_SUPPLY_KEYWORDS = ("공급계약", "체결")


def _text(record: dict, key: str) -> str | None:
    """문자열 필드 안전 추출 — 비문자열(int·list 등)은 None 으로 정리(crash-before-gate 방지,
    각도 H). 결측·오염은 게이트가 사유로 잡게 한다(Rule 12)."""
    value = record.get(key)
    return value if isinstance(value, str) else None


def _is_supply_report(report_nm: object) -> bool:
    """report_nm 이 단일판매ㆍ공급계약 **체결** 유형인지 — NFKC 정규화 후 '공급계약'·'체결' 모두
    부분일치. 해지(공급계약해지)는 '체결' 이 없어 제외된다. 비문자열은 False(호출부가 별도로
    malformed 로 격리하므로 여기선 방어만)."""
    if not isinstance(report_nm, str):
        return False
    norm = unicodedata.normalize("NFKC", report_nm)
    return all(keyword in norm for keyword in _SUPPLY_KEYWORDS)


# DART list.json `rm`(비고, 결합코드) 의 obsolete 마커 — '정'=정정신고가 있어 대체된 원본
# (DART: "관련 보고서를 참조"), '철'=철회 간주. 이 원본은 정정본([기재정정]…체결)이 authoritative
# 라 canonical 에서 제외한다(안 그러면 원본+정정본이 서로 다른 rcept_no 로 둘 다 적재돼 같은
# 계약을 이중 계산). 다른 rm 코드(유·코·연 등)엔 이 글자가 없어 부분일치가 안전(Codex P2).
_OBSOLETE_RM_MARKERS = ("정", "철")


def _is_obsolete(rm: object) -> bool:
    """rm 이 정정으로 대체된 원본('정')·철회('철')를 표시하는지 — obsolete 원본은 canonical 제외."""
    return isinstance(rm, str) and any(marker in rm for marker in _OBSOLETE_RM_MARKERS)


def _norm_report_date(rcept_dt: object) -> str | None:
    """rcept_dt('YYYYMMDD') → 'YYYY-MM-DD'. 결측·비달력일·형식불량은 None(게이트가 사유로 잡음).

    문자열 슬라이싱만 하면 '20260231'(2월 31일)·'2026ABCD' 같은 비달력일이 통과한다 —
    strptime 으로 실재 달력일까지 검증하고, 파싱값을 되돌려 원문과 일치할 때만 정상으로 본다
    (zero-pad 미강제로 '202671' 이 통과하는 걸 왕복 검증으로 막는다 — 가격 정제와 동형)."""
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


def _to_fact(record: dict, parsed: dict, vendor: str) -> dict:
    """파서 출력 + raw 메타 provenance → 공통 공급계약 fact 행.

    corp_name 은 raw 메타(권위 provenance)를 쓴다(파서도 title 에서 뽑지만 raw 가 SSOT).
    계약기간(start·end)은 ISO 문자열로 직렬화한다(canonical 은 전 컬럼 명시 스키마).
    source_vendor(raw 키의 source=)는 파티션이 아니라 컬럼으로 보존한다 — 현재 KR·DART
    단독이나 다른 공시 소스 추가·canonical↔raw 감사에 provenance 가 필요하다(가격·뉴스 동형)."""
    start = parsed.get("start")
    end = parsed.get("end")
    return {
        "rcept_no": _text(record, "rcept_no"),
        "source_vendor": vendor,
        "corp_code": _text(record, "corp_code"),
        "ticker": _text(record, "our_ticker"),
        "corp_name": _text(record, "corp_name"),
        "counterparty": parsed.get("counterparty"),
        "counterparty_raw": parsed.get("counterparty_raw"),
        "counterparty_withheld": bool(parsed.get("counterparty_withheld")),
        "object": parsed.get("object"),
        "amount_krw": parsed.get("amount_krw"),
        "ratio_pct": parsed.get("ratio_pct"),
        "contract_start": start.isoformat() if start is not None else None,
        "contract_end": end.isoformat() if end is not None else None,
        "confidence": parsed.get("confidence"),
        "report_date": _norm_report_date(record.get("rcept_dt")),
        "source_url": _text(record, "source_url"),
        "parser_version": PARSER_VERSION,
        "fetched_at": _text(record, "fetched_at"),
    }


# ── canonical 적재 ───────────────────────────────────────
# 명시 스키마로 고정 — pyarrow 추론에 맡기면 all-None 컬럼이 null 타입으로 잡혀 기존 파티션과
# 병합 시 스키마가 충돌한다. 수치는 타입 지정(가격 정제 관례), 나머지는 string.
_CANONICAL_COLUMNS = (
    "rcept_no", "source_vendor", "corp_code", "ticker", "corp_name", "counterparty",
    "counterparty_raw", "counterparty_withheld", "object", "amount_krw", "ratio_pct",
    "contract_start", "contract_end", "confidence", "report_date", "source_url",
    "parser_version", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([
        ("rcept_no", pa.string()), ("source_vendor", pa.string()),
        ("corp_code", pa.string()), ("ticker", pa.string()),
        ("corp_name", pa.string()), ("counterparty", pa.string()), ("counterparty_raw", pa.string()),
        ("counterparty_withheld", pa.bool_()), ("object", pa.string()),
        ("amount_krw", pa.int64()), ("ratio_pct", pa.float64()),
        ("contract_start", pa.string()), ("contract_end", pa.string()),
        ("confidence", pa.string()), ("report_date", pa.string()),
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
    """'최신 우선' 정렬 키 — 실제 시각으로 비교한다(문자열 비교는 오프셋이 다르면 어긋난다).
    파싱 불가·결측·naive 는 각각 가장 오래된 것/UTC 로 안전 처리(가격·뉴스 정제와 동형)."""
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 report_date 파티션을 rcept_no 키로 멱등 병합. 같은 rcept_no 재적재(정정본 재수집)는
    최신 fetched_at 이 이기고, 동률이면 신규(멱등 재실행). 가격의 벤더 교차 충돌 분기는 없다 —
    공시는 정체성이 rcept_no(문서 유일키) 단일 벤더(dart)라 교차 오염이 없다."""
    acc: dict[str, dict] = {}
    for row in [*existing, *new_rows]:
        rcept_no = row["rcept_no"]
        prev = acc.get(rcept_no)
        if prev is None or _fetched_at(row) >= _fetched_at(prev):
            acc[rcept_no] = row
    return [acc[r] for r in sorted(acc)]


def _write_canonical(storage: Storage, passing: list[dict]) -> tuple[int, int]:
    """통과 fact 를 report_date 파티션별로 기존 canonical 과 rcept_no 키로 멱등 병합해 쓴다.
    반환: (쓴 파티션 수, 행 수)."""
    by_partition: dict[str, list[dict]] = defaultdict(list)
    for row in passing:
        # 게이트 통과행은 report_date 가 유효(결측·범위밖 아님)라 파티션이 결정적(멱등).
        by_partition[row["report_date"]].append(row)

    parts_written = rows_written = 0
    for report_date, new_rows in sorted(by_partition.items()):
        prefix = canonical_supply_contract_fact_partition(report_date)
        # 파티션의 기존 parquet 을 전부 읽어 병합한다. 이 스텝은 항상 part-00000 하나로 되써
        # 멱등을 지킨다(canonical 은 이 스텝만 쓰므로 part-00000 만 존재).
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
    """raw disclosures → 공급계약 파싱 → 게이트 → canonical 멱등 병합 + quality_log.
    성공 0, 장애 시 비0.

    input_run_id 지정 시 그 수집 런의 raw 만 **재검증**한다(canonical 은 안 씀 — 전체 런이
    authoritative). 미지정이면 raw disclosures 전체를 검증하고 canonical 을 멱등 적재한다
    (가격·뉴스 정제와 동형).
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    max_report_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_disclosure_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = routed = skipped_type = skipped_superseded = 0
    failures: list[dict] = []  # blocking·본문/파싱 실패 — canonical 제외 대상
    warnings: list[dict] = []  # non-blocking — 통과하되 값 이상을 로깅
    passing: list[dict] = []   # 게이트 통과 fact — 루프 뒤 canonical 로 멱등 병합
    exit_code = 0

    for raw_key in raw_keys:
        try:
            # 키 파싱도 try 안에 둔다 — 규약 밖 키(source= 누락 등)의 KeyError 가 런 전체를
            # 죽이지 않고 이 파티션만 격리되게(가격·뉴스 정제와 동일한 격리 의도).
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
                # 유효 JSON 이지만 객체가 아닌 행(null·배열·스칼라)은 record.get 에서 런 전체를
                # 죽인다 — 행 단위로 격리해 나머지 검증이 완료되게(격리≠은폐, Rule 12, 각도 H).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor != "dart":
                # 알 수 없는 공시 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            # doc_type 라우팅 — 공급계약만 이 스텝이 처리한다. raw 는 유형 필터로 좁혀졌지만
            # 정제도 report_nm 으로 재라우팅해 소스 필터 변화에 독립적으로 동작한다.
            report_nm = record.get("report_nm")
            if not isinstance(report_nm, str):
                # 비문자열 report_nm 은 오염된 메타 — 라우팅 판정 자체가 불가하다. skipped_type
                # (정상적인 비대상 유형)으로 침묵 흡수하지 않고 사유로 드러낸다(Rule 12, 각도 H).
                failures.append({"rcept_no": _text(record, "rcept_no"), "raw_key": raw_key,
                                 "reasons": ["malformed_report_nm"]})
                continue
            if not _is_supply_report(report_nm):
                # 대상 아님(사업보고서 등) — 스킵(실패 아님, segment fact 는 후속 스토리).
                skipped_type += 1
                continue
            if _is_obsolete(record.get("rm")):
                # 정정으로 대체됐거나(rm '정') 철회된(rm '철') 원본 — canonical 제외. 정정본
                # ([기재정정]…체결, 별도 rcept_no)이 authoritative 라 원본을 함께 적재하면 같은
                # 계약이 이중 계산된다. bronze raw 는 원본을 보존하고, 실패가 아니라 명시적 스킵으로
                # 카운트해 드러낸다(Rule 12, Codex P2).
                skipped_superseded += 1
                continue
            routed += 1

            rcept_no = _text(record, "rcept_no")
            ref = {"rcept_no": rcept_no, "raw_key": raw_key}
            # 본문 파싱 — document_raw_path(ZIP) 를 열어 euc-kr 디코딩·파싱한다. 본문 결측
            # (수집 시 doc fetch 실패로 path=None)·ZIP 오류·파싱 크래시는 행 단위로 격리한다
            # (각도 H — crash-before-gate 방지, 항상 quality_log).
            doc_path = _text(record, "document_raw_path")
            if not doc_path:
                failures.append({**ref, "reasons": ["missing_document_body"]})
                continue
            try:
                html = extract_document_html(storage.get_bytes(doc_path))
                parsed = parse_supply(html)
                fact = _to_fact(record, parsed, vendor)
                reasons = validate_supply_fact(fact, max_report_date=max_report_date)
            except Exception as exc:
                logger.exception("공급계약 본문 파싱 실패(격리): %s", ref)
                failures.append({**ref, "reasons": ["parse_error"], "error": str(exc)})
                continue

            ref = {"rcept_no": rcept_no, "ticker": fact["ticker"],
                   "report_date": fact["report_date"], "raw_key": raw_key}
            blocking = [r for r in reasons if r in BLOCKING_REASONS_DISCLOSURE]
            if blocking:
                # blocking 이 있으면 canonical 제외 — 경고까지 포함한 전체 사유를 남긴다.
                failures.append({**ref, "reasons": reasons})
                continue
            passing.append(fact)
            warn = [r for r in reasons if r not in BLOCKING_REASONS_DISCLOSURE]
            if warn:
                # 통과했지만 값 이상(비율 범위밖·금액 비양수·상대방 유보 등) — canonical 진입은
                # 시키되 품질 신호를 드러낸다(coerce-to-passing 방지, Rule 12).
                warnings.append({**ref, "reasons": warn})

    # 통과 fact 를 canonical 로 멱등 병합 — **전체 런(input_run_id=None)만** 쓴다. 스코프
    # 실행은 재검증(quality_log)만 하고 canonical 은 전체 raw 를 보는 멱등 런이 authoritative
    # 하게 쓴다(가격·뉴스 정제와 동형 — 스코프가 부분 파티션을 덮어써 멱등성을 흔들지 않게).
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
                "records_routed_supply": routed,
                "records_skipped_type": skipped_type,
                "records_skipped_superseded": skipped_superseded,
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
        # 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알린다.
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_disclosure 완료: raw_files=%d read=%d routed=%d skipped_type=%d "
        "skipped_superseded=%d "
        "passed=%d failed=%d warned=%d canonical_parts=%d canonical_rows=%d",
        len(raw_keys), read, routed, skipped_type, skipped_superseded, len(passing),
        len(failures), len(warnings), parts_written, canonical_rows,
    )
    return exit_code
