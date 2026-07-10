"""가격 정제 Step2 — 정규화 + OHLCV 정합성 게이트 (ALPHA-133 / S032).

raw price_daily(FMP·KIS 두 벤더, 이형 스키마)를 읽어 **표준 OHLCV 행으로 정규화**하고,
물리 정합성 게이트(quality/price.validate_ohlcv)를 통과하는지 검사한다. 검증 결과는
`data_quality_logs` 로 남긴다 — 몇 건 읽고/통과/탈락했는지와 **탈락 사유**를 드러내
잘못된 가격이 조용히 사라지지 않게 한다(AGENTS Rule 12).

이 스텝(PR1)은 **검증까지만** 한다 — 통과 행을 canonical 로 적재하는 멱등 병합은 후속
(PR2 / S006·S007) 소관이라 여기서 쓰지 않는다. quality_log 자체가 검증 결과 sink 다.

정규화가 흡수하는 벤더 이형(raw 무변형으로 보존된 원본):
  - FMP: date="YYYY-MM-DD", open/high/low/close/volume/adjClose = 수치
  - KIS: stck_bsop_date="YYYYMMDD", stck_oprc/hgpr/lwpr/clpr/acml_vol = 문자열(adj 없음)
벤더 판별은 raw 키의 source= 파티션으로 한다(레코드 내용 아님 — 키가 규약의 SSOT).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..lake import Storage, is_raw_price_key, parse_raw_price_key, quality_log_key
from ..quality import validate_ohlcv

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_price"
DATASET = "price_daily"

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
    return int(num) if as_int else num


def _norm_trade_date(raw: dict, key: str, reasons: list[str], *, kis: bool) -> str | None:
    """trade_date 정규화 → 'YYYY-MM-DD'. 결측=missing_field, 형식 불량=bad_trade_date."""
    value = raw.get(key)
    if not value or (isinstance(value, str) and not value.strip()):
        reasons.append("missing_field")
        return None
    text = str(value).strip()
    if kis:
        # KIS 는 'YYYYMMDD'(8자리) — 하이픈 형식으로 통일.
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        reasons.append("bad_trade_date")
        return None
    # FMP 는 이미 'YYYY-MM-DD' — 형식만 확인(파싱 실패는 드리프트).
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError:
        reasons.append("bad_trade_date")
        return None


def _normalize(vendor: str, raw: dict) -> tuple[dict, list[str]]:
    """벤더 raw 행 → 표준 OHLCV 행 + 정규화(결측·비수치·날짜) 사유. 사유 있으면 게이트 생략."""
    reasons: list[str] = []
    field_map = _FMP_MAP if vendor == "fmp" else _KIS_MAP
    is_kis = vendor == "kis"
    date_key = "stck_bsop_date" if is_kis else "date"

    row = {
        "market": raw.get("market"),
        "ticker": raw.get("our_ticker"),
        "trade_date": _norm_trade_date(raw, date_key, reasons, kis=is_kis),
        "open": _to_number(raw, field_map["open"], reasons),
        "high": _to_number(raw, field_map["high"], reasons),
        "low": _to_number(raw, field_map["low"], reasons),
        "close": _to_number(raw, field_map["close"], reasons),
        "volume": _to_number(raw, field_map["volume"], reasons, as_int=True),
        # FMP 만 수정종가를 준다 — KIS 는 없어 null(다운스트림이 close 로 폴백).
        "adj_close": None if is_kis else _adj_close(raw),
        "currency": _CURRENCY.get(raw.get("market")),
        "source_vendor": vendor,
        "fetched_at": raw.get("fetched_at"),
    }
    return row, _dedup(reasons)


def _adj_close(raw: dict) -> float | None:
    """FMP adjClose(있으면). 없거나 비수치면 null — 정합성 게이트 대상 아님(참고 필드)."""
    value = raw.get("adjClose")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw price_daily → 정규화 → 게이트 → quality_log. 성공 0, 스토리지 장애 시 비0.

    input_run_id 지정 시 그 수집 런의 raw 만, 아니면 raw price 전체를 검증한다(멱등).
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_price_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = passed = 0
    failures: list[dict] = []
    exit_code = 0

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
            if vendor not in ("fmp", "kis"):
                # 알 수 없는 가격 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            row, reasons = _normalize(vendor, record)
            if not reasons:
                reasons = validate_ohlcv(row)
            if reasons:
                failures.append({
                    "market": row["market"], "ticker": row["ticker"],
                    "trade_date": row["trade_date"], "source_vendor": vendor,
                    "reasons": reasons, "raw_key": raw_key,
                })
                continue
            passed += 1

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
                "records_passed": passed,
                "records_failed": len(failures),
                "failures": failures,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        # 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알린다.
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_price 완료: raw_files=%d read=%d passed=%d failed=%d",
        len(raw_keys), read, passed, len(failures),
    )
    return exit_code
