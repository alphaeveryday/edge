"""가격 정제 Step2 — 정규화 + OHLCV 정합성 게이트 (ALPHA-133 / S032).

raw price_daily(FMP·KIS 두 벤더, 이형 스키마)를 읽어 **표준 OHLCV 행으로 정규화**하고,
물리 정합성 게이트(quality/price.validate_ohlcv)를 통과하는지 검사한다. 검증 결과는
`data_quality_logs` 로 남긴다 — 몇 건 읽고/통과/탈락했는지와 **탈락 사유**를 드러내
잘못된 가격이 조용히 사라지지 않게 한다(AGENTS Rule 12).

게이트를 통과한 행은 `canonical/market_data/price_daily` 에 **(market,ticker,trade_date)
정체성 키로 멱등 병합** 한다 — canonical 은 run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가
같다. 같은 벤더 재적재는 최신 fetched_at 이 이기고, **벤더 교차 같은 키 충돌은 fail-loud**
(통화 오염 방지 — 조용히 하나 고르지 않고 quality_log 에 드러낸다). 탈락 행은 quality_log 에
사유와 함께 남긴다 — 잘못된 가격이 조용히 사라지거나 canonical 을 오염시키지 않게 한다.

정규화가 흡수하는 벤더 이형(raw 무변형으로 보존된 원본):
  - FMP: date="YYYY-MM-DD", open/high/low/close/volume/adjClose = 수치
  - KIS: stck_bsop_date="YYYYMMDD", stck_oprc/hgpr/lwpr/clpr/acml_vol = 문자열(adj 없음)
벤더 판별은 raw 키의 source= 파티션으로 한다(레코드 내용 아님 — 키가 규약의 SSOT).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import datetime, timezone

from ..lake import (
    Storage,
    canonical_run_manifest_key,
    is_raw_price_key,
    parse_raw_price_key,
    quality_log_key,
)
from ..quality import validate_ohlcv

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_price"
DATASET = "price_daily"
_PARTIAL_EXIT_CODE = 2

# market → 표준 통화. 통화는 FX 환산하지 않고 market 별로 태깅만 한다(환산은 의미 파괴).
_CURRENCY = {"US": "USD", "KR": "KRW"}

# 표준행의 가격 4필드 + 거래량. 벤더별 원본 키는 아래 _FIELD_MAP 이 잇는다.
_FMP_MAP = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
_KIS_MAP = {
    "open": "stck_oprc", "high": "stck_hgpr", "low": "stck_lwpr",
    "close": "stck_clpr", "volume": "acml_vol",
}


def _dedup(reasons: list[str]) -> list[str]:
    """사유 코드 중복 제거(첫 등장 순서 보존) — 필드 여러 개가 같은 사유여도 코드는 1개."""
    seen: dict[str, None] = {}
    for r in reasons:
        seen.setdefault(r, None)
    return list(seen)


def _to_number(raw: dict, key: str, reasons: list[str], *, as_int: bool = False):
    """벤더 원본 필드 → 수치(float|int). 결측=missing_field, 비수치=non_numeric 로 사유 기록."""
    value = raw.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        reasons.append("missing_field")
        return None
    if isinstance(value, bool):
        # bool 은 int 의 하위형이라 float(True)=1.0 로 조용히 통과한다 — 수치 필드의
        # 불리언은 스키마 드리프트다(비수치로 드러낸다, Rule 12).
        reasons.append("non_numeric")
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        reasons.append("non_numeric")
        return None
    if not math.isfinite(num):
        # NaN/Infinity — json.loads 는 이 리터럴을 float 로 파싱하고, NaN 비교는 전부
        # False 라 OHLCV 게이트를 조용히 통과한다(잘못된 봉이 '정상'으로 인증됨). 여기서
        # 막지 않으면 이 스토리가 막으려는 바로 그 오염이 canonical 로 흘러간다(Rule 12).
        reasons.append("non_numeric")
        return None
    if as_int and not num.is_integer():
        # 거래량은 정수 카운트다 — '-0.5'/'100.5' 같은 소수 거래량은 드리프트다. 특히
        # int() 는 -0.5 를 0 으로 잘라 음수 거래량을 게이트(volume<0) 앞에서 숨긴다
        # (0 은 정상 통과). 소수 거래량은 비수치로 드러낸다(Rule 12).
        reasons.append("non_numeric")
        return None
    return int(num) if as_int else num


def _norm_trade_date(raw: dict, key: str, reasons: list[str], *, kis: bool) -> str | None:
    """trade_date 정규화 → 'YYYY-MM-DD'. 결측=missing_field, 형식 불량=bad_trade_date."""
    value = raw.get(key)
    if not value or (isinstance(value, str) and not value.strip()):
        reasons.append("missing_field")
        return None
    text = str(value).strip()
    # 벤더별 원본 포맷은 다르지만(KIS 'YYYYMMDD' vs FMP 'YYYY-MM-DD'), 둘 다 strptime 으로
    # 실재 달력일인지까지 검증한다 — 문자열 슬라이싱만 하면 '20260231'(2월 31일) 같은
    # 비달력일이 통과한다. validate_ohlcv 는 날짜를 안 보므로, 여기서 안 막으면 quality_log
    # 가 존재하지 않는 거래일을 '정상'으로 인증한다(Rule 12).
    fmt = "%Y%m%d" if kis else "%Y-%m-%d"
    try:
        parsed = datetime.strptime(text, fmt).date()
    except ValueError:
        reasons.append("bad_trade_date")
        return None
    # strptime 은 zero-pad 를 강제하지 않아 '202671'(6자리)·'2026-7-1' 같은 드리프트도
    # 2026-07-01 로 파싱한다 — 파싱값을 같은 포맷으로 되돌려 원문과 정확히 일치할 때만
    # 정상으로 본다(왕복 검증). 어긋나면 정규 포맷이 아니므로 bad_trade_date.
    if parsed.strftime(fmt) != text:
        reasons.append("bad_trade_date")
        return None
    return parsed.isoformat()


def _normalize(vendor: str, raw: dict) -> tuple[dict, list[str]]:
    """벤더 raw 행 → 표준 OHLCV 행 + 정규화(결측·비수치·날짜) 사유. 사유 있으면 게이트 생략."""
    reasons: list[str] = []
    # KIS 만 자체 맵을 쓴다 — 나머지(FMP·Yahoo)는 같은 필드 이름(date/open/…/adjClose)이라
    # FMP 맵을 공유한다. 새 벤더가 FMP 형태로 raw 를 내면 여기 손댈 게 없다.
    field_map = _KIS_MAP if vendor == "kis" else _FMP_MAP
    is_kis = vendor == "kis"
    date_key = "stck_bsop_date" if is_kis else "date"

    market = raw.get("market")
    row = {
        "market": market,
        "ticker": raw.get("our_ticker"),
        "trade_date": _norm_trade_date(raw, date_key, reasons, kis=is_kis),
        "open": _to_number(raw, field_map["open"], reasons),
        "high": _to_number(raw, field_map["high"], reasons),
        "low": _to_number(raw, field_map["low"], reasons),
        "close": _to_number(raw, field_map["close"], reasons),
        "volume": _to_number(raw, field_map["volume"], reasons, as_int=True),
        # FMP 만 수정종가를 준다 — KIS 는 없어 null(다운스트림이 close 로 폴백).
        "adj_close": None if is_kis else _adj_close(raw),
        # market 이 문자열일 때만 통화 조회한다 — 배열/객체 같은 unhashable market 을 dict
        # 키로 쓰면 TypeError 로 런이 죽는다. 비문자열 market 은 아래 _blank 가 missing_field
        # 로 분류하므로, 여기선 통화만 None 으로 두고 크래시를 피한다(Rule 12).
        "currency": _CURRENCY.get(market) if isinstance(market, str) else None,
        "source_vendor": vendor,
        "fetched_at": raw.get("fetched_at"),
    }
    # market·ticker 는 canonical 정체성 키(market,ticker,trade_date)의 일부다 — 없으면 그
    # 행은 키를 만들 수 없어 canonical 로 못 간다. validate_ohlcv 는 이 둘을 안 보므로, 여기서
    # 결측을 missing_field 로 드러내지 않으면 정체성 없는 행이 passed 로 인증된다(Rule 12).
    # 공백/비문자열도 결측으로 본다(설정 NonBlankStr 관례와 일치 — '  ' 로 위장 못 하게).
    if _blank(row["market"]):
        reasons.append("missing_field")
    elif row["market"] not in _CURRENCY:
        # 비어있진 않지만 정규화가 지원하지 않는 market('ZZ' 등)은 통화 계약을 만들 수 없어
        # (currency=None) 표준행이 불완전하다 — passed 로 위장하지 않는다(Rule 12). 정규화는
        # 현재 US/KR 만 지원하므로 지원 집합(_CURRENCY 키)으로 명시 검증한다.
        reasons.append("unsupported_market")
    if _blank(row["ticker"]):
        reasons.append("missing_field")
    return row, _dedup(reasons)


def _blank(value: object) -> bool:
    """정체성 값이 사실상 비었는가 — None·비문자열·공백만 문자열(설정 NonBlankStr 과 동형)."""
    return not (isinstance(value, str) and value.strip())


def _adj_close(raw: dict) -> float | None:
    """FMP adjClose(있으면). 정합성 게이트 대상이 아닌 참고 필드라 값이 나빠도 행을 탈락
    시키진 않지만, 없거나 비수치·비유한(NaN/Inf)·bool 이면 그 못 쓰는 값을 그대로 싣지
    않고 null 로 정리한다(_to_number 와 같은 위생 기준 — 다운스트림이 NaN adj_close 를
    읽지 않게)."""
    value = raw.get("adjClose")
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


# canonical 표준행 컬럼 — 명시 스키마로 고정한다(pyarrow 추론에 맡기면 all-None 컬럼이
# null 타입으로 잡혀 기존 파티션(float)과 병합 시 스키마가 충돌한다).
_CANONICAL_COLUMNS = (
    "market", "ticker", "trade_date", "open", "high", "low", "close",
    "volume", "adj_close", "currency", "source_vendor", "fetched_at",
)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
        ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()),
        ("close", pa.float64()), ("volume", pa.int64()), ("adj_close", pa.float64()),
        ("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
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


_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _fetched_at(row: dict) -> datetime:
    """'최신 우선' 정렬 키 — 실제 시각으로 비교한다. 문자열 비교는 오프셋이 다르면 어긋난다
    (예: '…+09:00'(01:00Z) vs '…+00:00'(05:00Z) 를 사전순 비교하면 stale 이 이긴다). 파싱
    불가·결측·naive(오프셋 없음)는 각각 가장 오래된 것/UTC 로 안전하게 처리한다."""
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict], collisions: list[dict]) -> list[dict]:
    """한 (market,trade_date) 파티션을 ticker 키로 병합. 기존→신규 순으로 적용해 신규가
    같은 벤더면 최신 fetched_at 로 이기고, 벤더 교차 충돌은 fail-loud 로 제외한다(§6b).

    collisions 에 교차 충돌을 append(호출부가 quality_log·exit_code 에 반영)."""
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
            # 벤더 교차 같은 키 — 조용히 하나 고르면 USD 를 KRW 로 태깅하는 오염이 된다.
            # 둘 다 canonical 에서 빼고 충돌로 드러낸다(§6b fail-loud, Rule 12).
            conflicted.add(ticker)
            acc.pop(ticker, None)
            collisions.append({
                "market": row["market"], "ticker": ticker, "trade_date": row["trade_date"],
                "vendors": sorted({prev["source_vendor"], row["source_vendor"]}),
            })
            continue
        # 같은 벤더 재적재 → 최신 fetched_at 우선(정정 반영). 동률이면 신규(멱등 재실행).
        if _fetched_at(row) >= _fetched_at(prev):
            acc[ticker] = row
    return [acc[t] for t in sorted(acc)]


def _write_canonical(
    storage: Storage, passing: list[dict], collisions: list[dict]
) -> tuple[list[dict], int]:
    """통과 행을 (market,trade_date) 파티션별로 기존 canonical 과 멱등 병합해 쓴다.
    반환: (이번 실행의 성공 winner manifest 항목, 병합 뒤 canonical 행 수)."""
    from ..lake import canonical_price_daily_partition

    by_partition: dict[tuple[str, str], list[dict]] = {}
    for row in passing:
        by_partition.setdefault((row["market"], row["trade_date"]), []).append(row)

    partitions: list[dict] = []
    rows_written = 0
    for (market, trade_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_price_daily_partition(market, trade_date)
        # 파티션의 기존 parquet 을 전부 읽어 병합한다(여러 파트가 있어도 유실 없이 읽는다).
        # 이 스텝은 항상 part-00000 하나로 되써 멱등을 지킨다 — Storage 에 delete 가 없어
        # 외부가 만든 다른 파트명은 못 지우지만, canonical 은 이 스텝만 쓰므로 part-00000 만 존재한다.
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        # manifest 범위는 기존 canonical 변경분이 아니라 현재 실행의 gate 통과 winner다.
        # 현재 실행 winner와 실제 canonical 병합을 따로 계산한다. 후자는 원본 new_rows를
        # 모두 받아야 기존 canonical과 새 벤더의 충돌 키도 canonical에서 빠진다.
        current_collisions: list[dict] = []
        canonical_collisions: list[dict] = []
        current_winners = _merge_partition([], new_rows, current_collisions)
        merged = _merge_partition(existing, new_rows, canonical_collisions)
        for collision in [*current_collisions, *canonical_collisions]:
            if collision not in collisions:
                collisions.append(collision)
        key = f"{prefix}/part-00000.parquet"
        parquet_bytes = _write_parquet_rows(merged)
        storage.put_bytes(key, parquet_bytes)
        digest = hashlib.sha256(parquet_bytes).hexdigest()
        if hashlib.sha256(storage.get_bytes(key)).hexdigest() != digest:
            raise OSError(f"canonical parquet 무결성 검증 실패: {key}")

        conflicted = {
            item["ticker"] for item in collisions
            if item["market"] == market and item["trade_date"] == trade_date
        }
        winner_ids = [
            {"ticker": ticker}
            for ticker in sorted({row["ticker"] for row in current_winners} - conflicted)
        ]
        if winner_ids:
            partitions.append({
                "market": market,
                "trade_date": trade_date,
                "key": key,
                "sha256": digest,
                "winner_ids": winner_ids,
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
    """raw price_daily → 정규화 → 게이트 → quality_log.

    성공 0, 격리된 행·벤더 충돌 2, 저장·무결성 실패 1.

    input_run_id 지정 시 **그 수집 런의 raw 만** 읽어 canonical 을 멱등 적재한다
    (ALPHA-389 — SFN 이 이 경로로 돈다). 미지정이면 전체를 읽는다 — 백필·복구 수단이다.
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    manifest_key = canonical_run_manifest_key(DATASET, run_id)
    manifest_initialized = True
    exit_code = 0
    try:
        # 같은 run 재시도가 실패해도 이전 완료 범위를 승인하지 않도록 먼저 무효화한다.
        storage.put_bytes(manifest_key, _manifest_bytes(run_id, False, []))
    except Exception:
        logger.exception("canonical run manifest 초기화 실패")
        manifest_initialized = False
        exit_code = 1

    try:
        raw_keys = [k for k in storage.list_keys("raw/") if is_raw_price_key(k)]
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
    passing: list[dict] = []  # 게이트 통과 행 — 루프 뒤 canonical 로 멱등 병합

    for raw_key in raw_keys:
        try:
            # 키 파싱도 try 안에 둔다 — 규약 밖 키(source= 누락 등)의 KeyError 가 런
            # 전체를 죽이지 않고 이 파티션만 격리되게(격리 의도 일관).
            vendor = parse_raw_price_key(raw_key)["source"]
            lines = storage.get_bytes(raw_key).decode("utf-8").splitlines()
        except Exception as exc:
            # raw 읽기/키 파싱 실패는 감사에 드러내고 계속(한 파티션 장애가 전체를 죽이지 않게).
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
                # 유효 JSON 이지만 객체가 아닌 행(null·배열·스칼라)은 _normalize 의 raw.get 에서
                # AttributeError 로 런 전체를 죽여 quality_log 조차 못 남긴다 — 한 행이 검증
                # 잡을 통째로 무너뜨리지 않게 행 단위 실패로 격리한다(격리≠은폐, Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor not in ("fmp", "kis", "yahoo"):
                # 알 수 없는 가격 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                # yahoo 는 로컬 전용 수집이지만 raw 형태가 FMP 와 같아 같은 경로로 정제된다.
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row, reasons = _normalize(vendor, record)
                if not reasons:
                    reasons = validate_ohlcv(row)
            except Exception as exc:
                # 예기치 못한 행 단위 크래시도 배치를 죽이지 않게 격리한다 — 검증 잡의 계약은
                # '한 이상치 행이 잡을 무너뜨리지 않고 항상 quality_log 를 남긴다'(Rule 12).
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

    # 통과 행을 canonical 로 멱등 병합 적재 — **스코프든 전체 런이든 쓴다**(ALPHA-389).
    #
    # 아래 교차 충돌 fail-loud 는 그대로 살아 있다 — 한 키에 두 벤더가 오면 둘 다 뺀다.
    # 다만 **'충돌로 비워진 키를 단일 벤더 스코프가 되살리는' 경우는 이제 못 막는다**:
    # 충돌이 나면 두 행을 다 빼 canonical 이 비고, 그러면 다음 스코프 런의 existing 에
    # 비교 상대가 없다. 예전엔 스코프가 canonical 을 아예 안 써서 자연히 막혔다.
    #
    # 그 하위 케이스를 포기하는 근거: **선행 조건인 '벤더 겹침'이 설계상 없다.** 벤더는
    # 서로 다른 종목을 받는다 — sources.toml `[price.source.symbol_map]` 은 US 심볼만 두고
    # ("가격은 ADR 의 USD 시세를 KR 종목 가격으로 쓰면 통화·거래시간이 어긋난다"), KR 은
    # KIS 소관이라 (market,ticker,trade_date) 가 겹치지 않는다. 불가능한 전제 뒤의 케이스를
    # 지키려고 정제를 영구히 O(전체 raw) 로 둘 이유가 없다(Rule 2·Rule 7).
    # 벤더가 실제로 겹치게 되면 전체 런·같은 런 스코프가 여전히 충돌을 fail-loud 로 잡는다.
    collisions: list[dict] = []
    partitions: list[dict] = []
    canonical_rows = 0
    canonical_written = False
    if manifest_initialized:
        try:
            partitions, canonical_rows = _write_canonical(storage, passing, collisions)
            canonical_written = True
        except Exception:
            logger.exception("canonical 적재·무결성 검증 실패")
            exit_code = 1
    if collisions:
        logger.error("canonical 벤더 교차 충돌 %d건 — 해당 키 winner 제외", len(collisions))

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
                    "failed_records": len(failures) + len(collisions),
                },
                "failures": failures,
                "canonical_written": canonical_written,
                "canonical_partitions": partitions,
                "canonical_partitions_written": len(partitions),
                "canonical_rows_written": canonical_rows,
                "manifest_winners": manifest_winners,
                "vendor_collisions": collisions,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        # 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알린다.
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        quality_written = False
        exit_code = 1

    if canonical_written and quality_written and exit_code != 1:
        try:
            # 완료 marker 전 같은 구조의 incomplete manifest를 왕복 검증한다. 이후 완료
            # 바이트도 다시 읽어 손상된 marker를 consumer에게 공개하지 않는다.
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

    if (failures or collisions) and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    logger.info(
        "normalize_price 완료: raw_files=%d read=%d passed=%d failed=%d "
        "canonical_parts=%d canonical_rows=%d winners=%d collisions=%d",
        len(raw_keys), read, len(passing), len(failures),
        len(partitions), canonical_rows, manifest_winners, len(collisions),
    )
    return exit_code
