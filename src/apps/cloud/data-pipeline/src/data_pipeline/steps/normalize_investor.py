"""투자자 수급 정제 Step2 — 정규화 + 수치·정체성 게이트 (ALPHA-482).

raw investor_flow_daily(KIS)를 읽어 **표준 투자자 순매수 행으로 정규화**한다. 가격
(normalize_price)과 동형이지만 정합성 게이트가 다르다 — OHLCV 같은 물리 불변식(고가≥저가 등)이
없다(순매수는 음수가 정상). 게이트는 **정체성(market·ticker·trade_date)과 수치 캐스팅**이다:
잘못된 날짜·결측 정체성·비수치 headline 은 탈락시키고 사유를 data_quality_logs 로 남긴다
(잘못된 수급이 조용히 사라지거나 canonical 을 오염시키지 않게, AGENTS Rule 12).

투자자 구분은 개인/외국인/기관계(headline)에 기관 세부(증권·투신·사모·은행·보험·종금·기금(연기금)·
기타법인·기타단체·기타)를 더해 KRX 급으로 편다. **headline 3종(개인·외국인·기관계)의 순매수
수량·대금은 필수** — 하나라도 비수치면 그 행은 탈락한다(가격의 OHLCV 필수와 같은 결). 기관 세부는
선택 — 결측·비수치면 그 컬럼만 null 로 두고 행은 살린다(raw 에 원본이 남아 유실 아님).

게이트를 통과한 행은 `canonical/market_data/investor_flow_daily` 에 **(market,ticker,trade_date)
정체성 키로 멱등 병합** 한다 — canonical 은 run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가
같다. 같은 벤더 재적재는 최신 fetched_at 이 이기고, 벤더 교차 같은 키 충돌은 fail-loud 한다
(현재 KR·KIS 단독이라 교차는 없지만 가격과 같은 안전장치를 유지한다).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..lake import Storage, is_raw_investor_key, parse_raw_investor_key, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_investor"
DATASET = "investor_flow_daily"

# market → 표준 통화(순매수 대금 태깅용). 가격과 동형 — FX 환산 없이 태깅만.
_CURRENCY = {"KR": "KRW"}

# 표준 투자자 구분 → (KIS 순매수 수량 키, 순매수 대금 키). 라이브 실측(2026-07-21) 필드명.
# 대부분 `_ntby_qty` 지만 사모·기타법인·기타단체는 `_ntby_vol`(거래량) 이름이다 — 무시 말고 정확히 잇는다.
_HEADLINE_GROUPS = {
    "individual": ("prsn_ntby_qty", "prsn_ntby_tr_pbmn"),        # 개인
    "foreign": ("frgn_ntby_qty", "frgn_ntby_tr_pbmn"),          # 외국인
    "institution_total": ("orgn_ntby_qty", "orgn_ntby_tr_pbmn"),  # 기관계
}
_SUB_GROUPS = {
    "financial_invest": ("scrt_ntby_qty", "scrt_ntby_tr_pbmn"),  # 증권(금융투자)
    "investment_trust": ("ivtr_ntby_qty", "ivtr_ntby_tr_pbmn"),  # 투신
    "pension": ("fund_ntby_qty", "fund_ntby_tr_pbmn"),          # 기금=연기금등
    "private_fund": ("pe_fund_ntby_vol", "pe_fund_ntby_tr_pbmn"),  # 사모
    "bank": ("bank_ntby_qty", "bank_ntby_tr_pbmn"),            # 은행
    "insurance": ("insu_ntby_qty", "insu_ntby_tr_pbmn"),        # 보험
    "merchant_bank": ("mrbn_ntby_qty", "mrbn_ntby_tr_pbmn"),    # 종금
    "other_corp": ("etc_corp_ntby_vol", "etc_corp_ntby_tr_pbmn"),  # 기타법인
    "other_org": ("etc_orgt_ntby_vol", "etc_orgt_ntby_tr_pbmn"),  # 기타단체
    "other": ("etc_ntby_qty", "etc_ntby_tr_pbmn"),             # 기타
}
_ALL_GROUPS = {**_HEADLINE_GROUPS, **_SUB_GROUPS}


def _dedup(reasons: list[str]) -> list[str]:
    """사유 코드 중복 제거(첫 등장 순서 보존)."""
    seen: dict[str, None] = {}
    for r in reasons:
        seen.setdefault(r, None)
    return list(seen)


def _to_int(raw: dict, key: str, reasons: list[str] | None):
    """KIS 원본 필드 → 정수 순매수(수량·대금). 값은 zero-pad 문자열(음수 가능).

    reasons 가 주어지면(headline 필수 필드) 결측=missing_field·비수치=non_numeric 로 기록하고
    None 을 돌려준다(행 탈락 유도). reasons 가 None 이면(기관 세부 선택 필드) 못 쓰는 값은 조용히
    None 으로 정리한다 — 원본은 raw 에 남아 유실이 아니다(bronze 안전망).
    """
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if reasons is not None:
            reasons.append("missing_field")
        return None
    if isinstance(value, bool):
        # bool 은 int 하위형이라 float(True)=1.0 로 조용히 통과한다 — 수치 필드의 불리언은 드리프트.
        if reasons is not None:
            reasons.append("non_numeric")
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        if reasons is not None:
            reasons.append("non_numeric")
        return None
    if not math.isfinite(num) or not num.is_integer():
        # NaN/Inf·소수 순매수는 드리프트(순매수는 정수 카운트) — 조용히 통과시키지 않는다(Rule 12).
        if reasons is not None:
            reasons.append("non_numeric")
        return None
    return int(num)


def _norm_trade_date(raw: dict, reasons: list[str]) -> str | None:
    """stck_bsop_date(YYYYMMDD) → 'YYYY-MM-DD'. 결측=missing_field, 형식 불량=bad_trade_date.

    문자열 슬라이싱이 아니라 strptime 왕복으로 실재 달력일까지 검증한다 — '20260231'(2월 31일)·
    zero-pad 누락('202671')을 조용히 통과시키지 않는다(normalize_price 와 동형, Rule 12).
    """
    value = raw.get("stck_bsop_date")
    if not value or (isinstance(value, str) and not value.strip()):
        reasons.append("missing_field")
        return None
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        reasons.append("bad_trade_date")
        return None
    if parsed.strftime("%Y%m%d") != text:
        reasons.append("bad_trade_date")
        return None
    return parsed.isoformat()


def _blank(value: object) -> bool:
    """정체성 값이 사실상 비었는가 — None·비문자열·공백만 문자열(설정 NonBlankStr 과 동형)."""
    return not (isinstance(value, str) and value.strip())


def _normalize(vendor: str, raw: dict) -> tuple[dict, list[str]]:
    """벤더 raw 행 → 표준 투자자 순매수 행 + 정규화 사유. headline 필수·기관세부 선택."""
    reasons: list[str] = []
    market = raw.get("market")
    row: dict = {
        "market": market,
        "ticker": raw.get("our_ticker"),
        "trade_date": _norm_trade_date(raw, reasons),
        "currency": _CURRENCY.get(market) if isinstance(market, str) else None,
        "source_vendor": vendor,
        "fetched_at": raw.get("fetched_at"),
    }
    # headline 3종(개인·외국인·기관계)의 수량·대금은 필수 — 비수치면 reasons 에 기록해 행 탈락.
    for name, (qty_key, val_key) in _HEADLINE_GROUPS.items():
        row[f"net_qty_{name}"] = _to_int(raw, qty_key, reasons)
        row[f"net_val_{name}"] = _to_int(raw, val_key, reasons)
    # 기관 세부는 선택 — 못 쓰는 값은 null(reasons=None). 행을 탈락시키지 않는다.
    for name, (qty_key, val_key) in _SUB_GROUPS.items():
        row[f"net_qty_{name}"] = _to_int(raw, qty_key, None)
        row[f"net_val_{name}"] = _to_int(raw, val_key, None)
    # market·ticker 는 canonical 정체성 키의 일부다 — 없으면 키를 만들 수 없어 canonical 로 못
    # 간다. 결측·미지원 market 을 missing_field/unsupported_market 로 드러낸다(Rule 12).
    if _blank(row["market"]):
        reasons.append("missing_field")
    elif row["market"] not in _CURRENCY:
        reasons.append("unsupported_market")
    if _blank(row["ticker"]):
        reasons.append("missing_field")
    return row, _dedup(reasons)


# canonical 표준행 컬럼 — 명시 스키마로 고정한다(pyarrow 추론에 맡기면 all-None 컬럼이
# null 타입으로 잡혀 기존 파티션(int)과 병합 시 스키마가 충돌한다).
_NET_COLUMNS = tuple(
    f"net_{kind}_{name}" for name in _ALL_GROUPS for kind in ("qty", "val")
)
_CANONICAL_COLUMNS = (
    "market", "ticker", "trade_date", *_NET_COLUMNS, "currency", "source_vendor", "fetched_at",
)


def _canonical_schema():
    import pyarrow as pa

    fields = [
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
    ]
    fields += [(col, pa.int64()) for col in _NET_COLUMNS]
    fields += [
        ("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ]
    return pa.schema(fields)


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


_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _fetched_at(row: dict) -> datetime:
    """'최신 우선' 정렬 키 — 실제 시각으로 비교한다(문자열 비교는 오프셋이 다르면 어긋난다).
    파싱 불가·결측·naive 는 각각 가장 오래된 것/UTC 로 안전 처리한다(normalize_price 와 동형)."""
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict], collisions: list[dict]) -> list[dict]:
    """한 (market,trade_date) 파티션을 ticker 키로 병합. 기존→신규 순으로 적용해 신규가 같은
    벤더면 최신 fetched_at 로 이기고, 벤더 교차 충돌은 fail-loud 로 제외한다(가격과 동형)."""
    acc: dict[str, dict] = {}
    conflicted: set[str] = set()
    for row in [*existing, *new_rows]:
        ticker = row["ticker"]
        if ticker in conflicted:
            continue
        prev = acc.get(ticker)
        if prev is None:
            acc[ticker] = row
            continue
        if prev["source_vendor"] != row["source_vendor"]:
            # 벤더 교차 같은 키 — 조용히 하나 고르지 않고 둘 다 빼 충돌로 드러낸다(Rule 12).
            conflicted.add(ticker)
            acc.pop(ticker, None)
            collisions.append({
                "market": row["market"], "ticker": ticker, "trade_date": row["trade_date"],
                "vendors": sorted({prev["source_vendor"], row["source_vendor"]}),
            })
            continue
        if _fetched_at(row) >= _fetched_at(prev):
            acc[ticker] = row
    return [acc[t] for t in sorted(acc)]


def _write_canonical(storage: Storage, passing: list[dict], collisions: list[dict]) -> tuple[int, int]:
    """통과 행을 (market,trade_date) 파티션별로 기존 canonical 과 멱등 병합해 쓴다.
    반환: (쓴 파티션 수, 쓴 행 수)."""
    from ..lake import canonical_investor_flow_partition

    by_partition: dict[tuple[str, str], list[dict]] = {}
    for row in passing:
        by_partition.setdefault((row["market"], row["trade_date"]), []).append(row)

    parts_written = rows_written = 0
    for (market, trade_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_investor_flow_partition(market, trade_date)
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows, collisions)
        storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(merged))
        parts_written += 1
        rows_written += len(merged)
    return parts_written, rows_written


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw investor_flow_daily → 정규화 → 게이트 → quality_log. 성공 0, 스토리지 장애 시 비0.

    input_run_id 지정 시 그 수집 런의 raw 만 읽어 canonical 을 멱등 적재한다(SFN 경로).
    미지정이면 전체를 읽는다 — 백필·복구 수단이다(가격 정제와 동형).
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_investor_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = 0
    failures: list[dict] = []
    passing: list[dict] = []
    exit_code = 0

    for raw_key in raw_keys:
        try:
            vendor = parse_raw_investor_key(raw_key)["source"]
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
                # 유효 JSON 이지만 객체가 아닌 행은 _normalize 의 raw.get 에서 AttributeError 로
                # 런을 죽인다 — 한 행이 검증 잡을 무너뜨리지 않게 격리한다(Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor != "kis":
                # 알 수 없는 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(현재 KIS 단독).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row, reasons = _normalize(vendor, record)
            except Exception as exc:
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue
            if reasons:
                failures.append({
                    "market": row["market"], "ticker": row["ticker"],
                    "trade_date": row["trade_date"], "source_vendor": vendor,
                    "reasons": reasons, "raw_key": raw_key,
                })
                continue
            passing.append(row)

    collisions: list[dict] = []
    parts_written = canonical_rows = 0
    canonical_written = True
    try:
        parts_written, canonical_rows = _write_canonical(storage, passing, collisions)
    except Exception:
        logger.exception("canonical 적재 실패")
        canonical_written = False
        exit_code = 1
    if collisions:
        logger.error("canonical 벤더 교차 충돌 %d건 — 해당 키 canonical 제외", len(collisions))
        exit_code = exit_code or 1

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
                "failures": failures,
                "canonical_written": canonical_written,
                "canonical_partitions_written": parts_written,
                "canonical_rows_written": canonical_rows,
                "vendor_collisions": collisions,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_investor 완료: raw_files=%d read=%d passed=%d failed=%d "
        "canonical_parts=%d canonical_rows=%d collisions=%d",
        len(raw_keys), read, len(passing), len(failures),
        parts_written, canonical_rows, len(collisions),
    )
    return exit_code
