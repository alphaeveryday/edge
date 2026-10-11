"""시장 시계열 일별 값 적재 — canonical 업종지수 일봉 → market_series_daily (ALPHA-1260).

`canonical/market_data/sector_index_daily` 를 읽어 `market_series` 에 등록된 지수의 시가·고가·
저가·종가를 Cloud DB 에 적재한다. 수집·정제(ALPHA-1254) → 이 스텝이 체인의 끝이다.

**무엇을 싣는가**: `market_series` 에서 `source_code='KRX'`·`status='ACTIVE'` 인 시계열만이다.
canonical 의 `code`(KRX 업종코드)를 `source_series_id` 로 찾는다. 등록되지 않은 코드는 싣지 않고
센다 — canonical 은 업종지수 전부를 싣지만 DB 에 둘 지수는 마스터가 정한다. 그래서 미등록은
실패가 아니다(NAV 로더의 미등록 ETF 와 다른 점). 대신 등록된 시계열이 하나도 없으면 치명 장애(1)로
끝낸다 — 적재할 대상이 없는 실행을 0건 성공으로 보고하지 않는다(Rule 12).

**멱등·정정**: PK `(market_series_id, trade_date)`. 규칙은 하나다 — **더 늦은 관측만 행을 바꾼다.**
더 늦은 관측이면 값·`available_at`·`data_version` 을 그 관측으로 맞추고(값이 같아도 시각은 올라간다),
같거나 더 이른 관측은 아무것도 바꾸지 못한다. canonical 이 최신 `fetched_at` 으로 수렴하는 것과 같은
축이고 `etf_nav_daily` 와 같은 규약이다. 그래서 `available_at` 은 "처음 안 시각"이 아니라 **이 값을
마지막으로 확인한 관측 시각**이다 — 처음 안 시각으로 두면 같은 값의 재확인이 기록되지 않아, 그 사이의
옛 관측(다른 값)이 뒤늦게 재시도될 때 최신 값을 되돌린다. 처음 안 시각이 필요해지면 컬럼을 더한다.

**후보 선택**: 한 (code, trade_date) 에 행이 여럿이면(복구 범위의 잔존 part 등) canonical 병합과 같은
규칙으로 고른다 — 최신 관측이 이기고, 같은 관측 시각에 값이 다르면 어느 쪽도 싣지 않는다. 관측 시각
(`fetched_at`)이 없거나 시간대가 없는 행은 순서를 매길 수 없으므로 그 키를 싣지 않는다. 둘 다 부분 실패(2)다.

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
    " WHERE market_series_daily.available_at < EXCLUDED.available_at"
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


def _observed_at(value: object) -> datetime | None:
    """canonical `fetched_at`(ISO 8601) → 관측 시각. 없거나 시간대가 없으면 None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _winner(facts: list[dict]) -> tuple[dict | None, str | None]:
    """한 (code, trade_date) 의 후보들 → (적재할 값, 거부 사유). 모듈 도크스트링 "후보 선택"."""
    if any(fact["observed_at"] is None for fact in facts):
        return None, "invalid_fetched_at"
    latest = max(fact["observed_at"] for fact in facts)
    top = [fact for fact in facts if fact["observed_at"] == latest]
    if any(fact != top[0] for fact in top[1:]):
        return None, "same_timestamp_value_conflict"
    return top[0], _load_violation(top[0])


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
    manifest_partitions = manifest_winners = 0
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
        # (code, trade_date) → 그 키의 모든 후보. 무엇을 고를지는 등록 여부를 안 뒤에 정한다 —
        # 싣지도 않을 미등록 코드의 결함을 이 실행의 실패로 세지 않기 위해서다.
        candidates: dict[tuple[str, str], list[dict]] = {}
        manifest_partitions, manifest_winners, input_rows = _core._input_rows(
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
            candidates.setdefault((code, trade_date), []).append({
                **{name: row.get(name) for name in _VALUES},
                "observed_at": _observed_at(row.get("fetched_at")),
            })

        with connect(db) as conn:
            series = _series_ids(conn)
            if not series:
                raise ValueError(
                    f"market_series 에 source_code={_SOURCE_CODE} 활성 시계열이 없다 — 적재할 대상이 없다"
                )
            with conn.cursor() as cur:
                for (code, trade_date), facts in sorted(candidates.items()):
                    series_id = series.get(code)
                    if series_id is None:
                        skipped_unregistered += len(facts)
                        continue
                    fact, violation = _winner(facts)
                    if violation is not None:
                        skipped_load_violation += 1
                        load_violations.append(
                            {"code": code, "trade_date": trade_date, "reason": violation}
                        )
                        continue
                    cur.execute(_UPSERT, (
                        series_id, trade_date, fact["open"], fact["high"], fact["low"],
                        fact["close"], fact["observed_at"], input_run_id or run_id,
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
        "manifest_partitions": manifest_partitions, "manifest_winners": manifest_winners,
        "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        # 마스터에 없는 코드 — 실패가 아니라 마스터가 고른 범위 밖이다.
        "skipped_unregistered": skipped_unregistered,
        "skipped_load_violation": skipped_load_violation,
        "load_violations": load_violations,
        # updated = 더 늦은 관측으로 맞춘 행(값이 같은 재확인 포함), unchanged = 같거나 더 이른 관측.
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
