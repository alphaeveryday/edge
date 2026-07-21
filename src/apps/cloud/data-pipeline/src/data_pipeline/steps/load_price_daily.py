"""가격 적재 — canonical 가격 → price_daily (ALPHA-377).

`canonical/market_data/price_daily/market=…/trade_date=…` 을 읽어 금융상품·거래일 grain 의
일별 가격 원장을 Cloud Event Store 에 적재한다. 수집 → 정제(normalize_price) → 이 스텝이
체인의 끝이다. load_etf_nav 와 같은 모델이다 — 표만 다르다.

**멱등**: PK `(instrument_id, trade_date)` 가 곧 멱등의 근거다 — 같은 값 재적재는
`ON CONFLICT … DO UPDATE … WHERE (…) IS DISTINCT FROM (…)` 의 WHERE 가 걸러내 아무 행도
반환하지 않고 already 로 세어진다. 벤더 정정(값이 실제로 바뀐 경우)만 UPDATE 로 흐른다 —
canonical 이 최신 fetched_at 으로 수렴시키므로(normalize 의 _merge_partition) 마트가 DO
NOTHING 이면 두 계층이 영구 불일치한다(load_etf_nav 와 같은 근거).

**instrument_id 해소**: canonical 의 `(market, ticker)` → `instrument` 조회다. 시장별 MIC
매핑을 거쳐 `(market_code, ticker)` 로 찾는다 — ETF·개별주식 **양쪽 다**(NAV 로더와 달리
instrument_type 을 안 건다). `(market_code, ticker)` 는 유일 자연키(uq_instrument_market_ticker)라
시장을 고정하면 ticker 로 유일하다. 마스터에 없는 종목은 **적재하지 않고 센다** — FK 위반으로
배치를 죽이는 대신 "마스터가 아직 이 종목을 모른다"는 사실을 수치로 드러낸다(Rule 12).

**적재 컬럼과 의도적 결측(NULL)**:
  * close_price ← canonical close, adjusted_close_price ← canonical adj_close, volume ← canonical volume
  * `turnover_value` · `price_basis` = **NULL**. canonical price_daily 가 나르지 않고(정제가
    KIS acml_tr_pbmn 을 보존하지 않는다) 소비처도 없다 — 있지도 않은 값을 지어내지 않는다.
  * `simple_return` · `log_return` = **NULL**. 전일 종가가 필요한 **파생 피처**이고, 첫 거래일·
    상장일·거래정지·액면분할 같은 경계 처리가 얽힌다 — 그건 피처 레이어 소관이지 원장 적재의
    일이 아니다(feature-layer-separation-plan). 이 로더는 관측된 가격만 옮긴다.

**CHECK 방어**: canonical 게이트가 이미 OHLCV 를 걸렀지만(close>0·volume>=0), adj_close 는
정합성 게이트 대상이 아닌 참고 필드라 0·음수가 통과할 수 있다 — `ck_price_daily_values` 를
위반하는 행은 적재 전에 격리하고 센다(FK/CHECK 위반으로 런 전체를 롤백시키지 않는다, Rule 12).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect
from ..lake import Storage, canonical_price_daily_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_price_daily"
DATASET = "price_daily"

# 적재 대상 시장 → MIC(ISO 10383). instrument 마스터가 KR 만 있어(US 는 ALPHA-371 로 MIC 미비)
# 지금은 하나다 — US 를 넣어도 전량 미등록으로 걸린다.
_MIC_BY_MARKET = {"KR": "XKRX"}

_CREATED_SAMPLE_LIMIT = 5


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical 가격 파티션의 trade_date 목록(정렬)."""
    marker = canonical_price_daily_partition(market, "")  # ".../trade_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    return sorted(dates)


def _instrument_ids(conn, mic: str) -> dict[str, str]:
    """(그 시장의) ticker → instrument_id. ETF·개별주식 양쪽 다.

    `(market_code, ticker)` 가 유일 자연키라 시장을 고정하면 ticker 로 유일하다. NAV 로더와
    달리 instrument_type 을 안 건다 — 가격은 ETF 도 개별주식도 다 있다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument WHERE market_code = %s",
            (mic,),
        )
        return {str(t): str(i) for t, i in cur.fetchall()}


def _pos_finite(value) -> bool:
    """None 아닌 값이 유한 양수인가. 비수치는 False 로 본다 — 예외를 던지지 않는다.

    이 함수는 격리 **게이트**의 일부다. float() 가 여기서 ValueError 를 던지면 바깥 try 가
    그걸 load_error 로 잡아 **정상 행까지 전체 롤백**해, 게이트가 막아야 할 crash-before-gate
    가 게이트 자신에서 터진다(Rule 12). 그래서 변환 실패는 예외가 아니라 '위반'으로 다룬다.
    typed parquet(float64) 에선 도달하지 않지만, 게이트는 스스로 죽지 않아야 한다.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(num) and num > 0


def _non_negative(value) -> bool:
    """None 아닌 값이 음수 아닌 수인가. 비수치는 False(위반) — 예외를 던지지 않는다(_pos_finite 와 같은 근거)."""
    try:
        return float(value) >= 0
    except (TypeError, ValueError):
        return False


def _check_violation(fact: dict) -> str | None:
    """ck_price_daily_values 를 위반하면 사유, 아니면 None. 적재 전 방어선(Rule 12).

    canonical 게이트가 close>0·volume>=0 은 이미 보장하지만 adj_close 는 참고 필드라
    0·음수·비유한이 통과할 수 있다. 위반 행을 넣으면 CHECK 로 배치가 죽으니 격리한다.
    """
    close, adj, volume = fact["close_price"], fact["adjusted_close_price"], fact["volume"]
    if close is not None and not _pos_finite(close):
        return "bad_close_price"
    if adj is not None and not _pos_finite(adj):
        return "bad_adjusted_close_price"
    if volume is not None and not _non_negative(volume):
        return "bad_volume"
    return None


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 가격 → price_daily 적재. 성공 0, 장애 시 비0.

    창(from/to) 미지정 = trade_date 전체 스캔. 멱등 skip 이라 재실행 비용은 신규분뿐이고,
    놓친 날짜도 다음 런이 자연 회복한다(load-etf-nav 와 같은 모델).
    """
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_unknown_instrument = skipped_check_violation = 0
    already = created = updated = 0
    created_sample: list[dict] = []
    unknown_instruments: set[str] = set()
    check_violations: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        # (market, ticker, trade_date) → 적재 후보. 같은 키가 여러 parquet 에 걸리면 최신
        # fetched_at 이 이긴다 — canonical 병합(_merge_partition)과 같은 규칙이다.
        candidates: dict[tuple[str, str, str], dict] = {}
        for market in _MIC_BY_MARKET:
            dates = [
                d for d in _partition_dates(storage, market)
                if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)
            ]
            for date in dates:
                prefix = canonical_price_daily_partition(market, date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        read += 1
                        ticker, trade_date = row.get("ticker"), row.get("trade_date")
                        if not ticker or not trade_date:
                            # canonical 게이트가 이미 거른 조합이라 여기 오면 안 되는 행이다.
                            # 그래도 세고 뺀다 — 정체성 없는 행은 키를 만들 수 없다(Rule 12).
                            skipped_missing_identity += 1
                            continue
                        fetched_at = row.get("fetched_at")
                        cand_key = (market, ticker, trade_date)
                        prev = candidates.get(cand_key)
                        if prev is not None and (fetched_at or "") < prev["fetched_at_raw"]:
                            continue
                        candidates[cand_key] = {
                            "close_price": row.get("close"),
                            "adjusted_close_price": row.get("adj_close"),
                            "volume": row.get("volume"),
                            # available_at = '우리가 이 관측을 쓸 수 있게 된 시각'. 수집 시각이
                            # 가장 보수적인 근사다(load-etf-nav 와 같은 규약).
                            "available_at": fetched_at or started_at.isoformat(),
                            "fetched_at_raw": fetched_at or "",
                        }

        with connect(db) as conn:
            instruments = {
                market: _instrument_ids(conn, mic) for market, mic in _MIC_BY_MARKET.items()
            }
            for (market, ticker, trade_date), fact in sorted(candidates.items()):
                instrument_id = instruments[market].get(ticker)
                if instrument_id is None:
                    # 마스터 미등록 — FK 위반으로 배치를 죽이는 대신 사실을 수치로 남긴다.
                    skipped_unknown_instrument += 1
                    unknown_instruments.add(f"{market}:{ticker}")
                    continue
                violation = _check_violation(fact)
                if violation is not None:
                    # CHECK 위반 행은 격리 — 넣으면 ck_price_daily_values 로 배치가 죽는다.
                    skipped_check_violation += 1
                    check_violations.append({
                        "market": market, "ticker": ticker,
                        "trade_date": trade_date, "reason": violation,
                    })
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO price_daily (instrument_id, trade_date, close_price,"
                        " adjusted_close_price, volume, available_at, data_version)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (instrument_id, trade_date) DO UPDATE"
                        " SET close_price = EXCLUDED.close_price,"
                        "     adjusted_close_price = EXCLUDED.adjusted_close_price,"
                        "     volume = EXCLUDED.volume, available_at = EXCLUDED.available_at,"
                        "     data_version = EXCLUDED.data_version"
                        " WHERE (price_daily.close_price, price_daily.adjusted_close_price,"
                        "        price_daily.volume) IS DISTINCT FROM"
                        "       (EXCLUDED.close_price, EXCLUDED.adjusted_close_price, EXCLUDED.volume)"
                        " RETURNING (xmax <> 0) AS was_update",
                        (instrument_id, trade_date, fact["close_price"],
                         fact["adjusted_close_price"], fact["volume"],
                         fact["available_at"], run_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        # 값이 같아 UPDATE 조건이 걸러낸 경우 — 재실행의 정상 경로다.
                        already += 1
                        continue
                    if row[0]:
                        # xmax<>0 = 기존 행을 갱신했다(벤더 정정이 마트까지 흘렀다).
                        updated += 1
                        continue
                created += 1
                if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                    created_sample.append({
                        "instrument_id": instrument_id, "ticker": ticker,
                        "trade_date": trade_date, "close_price": fact["close_price"],
                    })
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("가격 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, updated, created_sample = 0, 0, []
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "markets": list(_MIC_BY_MARKET), "from_date": from_date, "to_date": to_date,
        "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_instrument": skipped_unknown_instrument,
        # 마스터가 모르는 종목 목록 — instrument 마스터 확장이 얼마나 필요한지의 근거다.
        "unknown_instruments": sorted(unknown_instruments),
        "skipped_check_violation": skipped_check_violation,
        "check_violations": check_violations,
        "already_present": already, "created": created, "updated": updated,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = exit_code or 1

    logger.info(
        "load_price_daily 완료: read=%d created=%d updated=%d already=%d "
        "unknown_instrument=%d(%d종) check_violation=%d skipped_identity=%d",
        read, created, updated, already,
        skipped_unknown_instrument, len(unknown_instruments),
        skipped_check_violation, skipped_missing_identity,
    )
    return exit_code
