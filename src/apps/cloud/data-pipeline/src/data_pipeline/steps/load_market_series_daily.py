"""시장 시계열 일별 값 적재 — canonical 업종지수 일봉 → market_series_daily (ALPHA-1260).

`canonical/market_data/sector_index_daily` 를 읽어 `market_series` 에 등록된 지수의 시가·고가·
저가·종가를 Cloud DB 에 적재한다. 수집·정제(ALPHA-1254) → 이 스텝이 체인의 끝이다.

**무엇을 싣는가**: `market_series` 에서 `source_code='KRX'`·`status='ACTIVE'` 인 시계열만이다.
canonical 의 `code`(KRX 업종코드)를 `source_series_id` 로 찾는다. 등록되지 않은 코드는 싣지 않고
센다 — canonical 은 업종지수 전부를 싣지만 DB 에 둘 지수는 마스터가 정한다. 그래서 미등록은
실패가 아니다(NAV 로더의 미등록 ETF 와 다른 점). 대신 등록된 시계열이 하나도 없으면 치명 장애(1)로
끝낸다 — 적재할 대상이 없는 실행을 0건 성공으로 보고하지 않는다(Rule 12).

**멱등·정정**: PK `(market_series_id, trade_date)`. 값이 같으면 아무것도 바꾸지 않아 `available_at`
이 처음 적재한 관측 시각으로 남는다 — canonical 은 같은 거래일을 다시 받을 때마다 `fetched_at` 을
최신으로 바꾸므로(NAV 와 같은 병합), 값이 같은데도 따라가면 "언제 알았나"가 재수집 때마다 뒤로
밀린다. 값이 달라졌고 더 늦게 관측한 것이면 값과 시각을 함께 갱신한다(벤더 정정). 더 이른 관측은
기존 값을 되돌리지 못한다. 두 경우 다 `unchanged` 로 센다.

**입력 범위**: NAV 로더와 같은 읽기를 쓴다(`load_etf_nav._input_rows`). 정기 경로는 정제 manifest
가 지목한 직접 parquet 와 winner 만 읽고, 날짜창·전체 스캔은 명시 복구 경로다.

**커밋 경계는 실행 전체다.** 값 검증을 적재 전에 하므로 행 단위 격리(savepoint)는 두지 않는다 —
DB 오류는 실행 전체를 롤백하고 치명 장애(1)로 끝낸다.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect
from ..lake import Storage, quality_log_key
from . import load_etf_nav as _core
from .normalize_sector_index_daily import SPEC as _PRODUCER

logger = logging.getLogger(__name__)

JOB_NAME = "load_market_series_daily"
DATASET = "market_series_load"
_PARTIAL_EXIT_CODE = 2

# canonical `code` 가 속한 식별 체계 — market_series.source_code.
_SOURCE_CODE = "KRX"
_VALUES = ("open", "high", "low", "close")

# 정제 사양에서 그대로 가져온다 — 이름·행 키·경로를 따로 적으면 정제가 바뀔 때 조용히 어긋난다.
_INPUT = _core.InputSpec(
    dataset=_PRODUCER.dataset,
    producer=_PRODUCER.job_name,
    row_key=_PRODUCER.row_key,
    partition=_PRODUCER.canonical_partition,
)

_UPSERT = (
    "INSERT INTO market_series_daily (market_series_id, trade_date, open_value, high_value,"
    " low_value, close_value, available_at, data_version)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (market_series_id, trade_date) DO UPDATE"
    " SET open_value = EXCLUDED.open_value, high_value = EXCLUDED.high_value,"
    "     low_value = EXCLUDED.low_value, close_value = EXCLUDED.close_value,"
    "     available_at = EXCLUDED.available_at, data_version = EXCLUDED.data_version"
    " WHERE (market_series_daily.open_value, market_series_daily.high_value,"
    "        market_series_daily.low_value, market_series_daily.close_value)"
    "       IS DISTINCT FROM (EXCLUDED.open_value, EXCLUDED.high_value,"
    "                         EXCLUDED.low_value, EXCLUDED.close_value)"
    "   AND market_series_daily.available_at < EXCLUDED.available_at"
    " RETURNING (xmax <> 0) AS was_update"
)


def _series_ids(conn) -> dict[str, str]:
    """source_series_id(KRX 업종코드) → market_series_id. 등록된 활성 시계열만."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_series_id, market_series_id FROM market_series"
            " WHERE source_code = %s AND status = 'ACTIVE'",
            (_SOURCE_CODE,),
        )
        return {str(code): str(series_id) for code, series_id in cur.fetchall()}


def _load_violation(fact: dict) -> str | None:
    """지수 값은 양수·유한이어야 한다. 종가는 필수, 시가·고가·저가는 없을 수 있다."""
    for name in _VALUES:
        value = fact[name]
        if value is None and name != "close":
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"invalid_{name}"
        if not math.isfinite(value) or value <= 0:
            return f"invalid_{name}"
    return None


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 업종지수 일봉 → market_series_daily. 성공 0, 행 부분 실패 2, 치명 장애 1."""
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_unregistered = skipped_load_violation = 0
    created = updated = unchanged = 0
    load_violations: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date와 to_date는 함께 써야 한다")
        # (code, trade_date) → 적재 후보. 같은 키가 여러 parquet 에 걸리면 최신 fetched_at 이
        # 이긴다 — canonical 병합과 같은 규칙이다(load_etf_nav 와 같은 근거).
        candidates: dict[tuple[str, str], dict] = {}
        _, _, input_rows = _core._input_rows(
            storage, input_run_id=input_run_id, from_date=from_date, to_date=to_date,
            spec=_INPUT,
        )
        for market, partition_date, row, selected in input_rows:
            if not selected:
                continue
            read += 1
            code, trade_date = row.get(_INPUT.row_key), row.get("trade_date")
            if not (isinstance(code, str) and code.strip() == code and code
                    and row.get("market") == market and trade_date == partition_date):
                skipped_missing_identity += 1
                continue
            fetched_at = row.get("fetched_at") or ""
            prev = candidates.get((code, trade_date))
            if prev is not None and fetched_at < prev["fetched_at"]:
                continue
            candidates[(code, trade_date)] = {
                **{name: row.get(name) for name in _VALUES}, "fetched_at": fetched_at,
            }

        with connect(db) as conn:
            series = _series_ids(conn)
            if not series:
                raise ValueError(
                    f"market_series 에 source_code={_SOURCE_CODE} 활성 시계열이 없다 — 적재할 대상이 없다"
                )
            with conn.cursor() as cur:
                for (code, trade_date), fact in sorted(candidates.items()):
                    series_id = series.get(code)
                    if series_id is None:
                        skipped_unregistered += 1
                        continue
                    violation = _load_violation(fact)
                    if violation is not None:
                        skipped_load_violation += 1
                        load_violations.append(
                            {"code": code, "trade_date": trade_date, "reason": violation}
                        )
                        continue
                    cur.execute(_UPSERT, (
                        series_id, trade_date, fact["open"], fact["high"], fact["low"],
                        fact["close"], fact["fetched_at"] or started_at.isoformat(),
                        input_run_id or run_id,
                    ))
                    row = cur.fetchone()
                    if row is None:
                        unchanged += 1
                    elif row[0]:
                        updated += 1
                    else:
                        created += 1
    except Exception as exc:
        # 커밋 경계는 실행 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("시장 시계열 일별 값 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created = updated = unchanged = 0
        exit_code = 1

    if exit_code == 0 and (skipped_missing_identity + skipped_load_violation):
        exit_code = _PARTIAL_EXIT_CODE

    finished_at = datetime.now(timezone.utc)
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "input_run_id": input_run_id, "from_date": from_date, "to_date": to_date,
        "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        # 마스터에 없는 코드 — 실패가 아니라 마스터가 고른 범위 밖이다.
        "skipped_unregistered": skipped_unregistered,
        "skipped_load_violation": skipped_load_violation,
        "load_violations": load_violations,
        "created": created, "updated": updated, "unchanged": unchanged,
        "failures": failures, "exit_code": exit_code,
        "ops": {
            "records_out": created + updated + unchanged,
            "failed_records": (len(failures) + skipped_missing_identity
                               + skipped_load_violation),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_market_series_daily 완료: read=%d created=%d updated=%d unchanged=%d "
        "unregistered=%d load_violation=%d skipped_identity=%d",
        read, created, updated, unchanged,
        skipped_unregistered, skipped_load_violation, skipped_missing_identity,
    )
    return exit_code
