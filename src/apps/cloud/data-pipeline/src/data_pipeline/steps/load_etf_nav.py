"""ETF NAV 적재 — canonical ETF NAV → etf_nav_daily (ALPHA-383).

`canonical/market_data/etf_nav/market=…/trade_date=…` 을 읽어 ETF·거래일 grain 의 공식 NAV
관측값을 Cloud Event Store 에 적재한다. 수집 ALPHA-380 → 정제 ALPHA-382 → 이 스텝이 체인의 끝이다.

**멱등**: PK `(etf_instrument_id, trade_date)` 가 곧 멱등의 근거다 — 같은 값 재적재는
`ON CONFLICT … DO UPDATE … WHERE nav IS DISTINCT FROM EXCLUDED.nav` 의 WHERE 가 걸러내
아무 행도 반환하지 않고 already 로 세어진다. 사전 스냅샷 조회 방식과 달리 동시 실행에도
원자적이다(load_documents 와 같은 모델).

**정정은 반영한다** — canonical 은 같은 (etf, trade_date) 를 최신 `fetched_at` 으로 수렴시키므로
(normalize 의 `_merge_partition`), 마트가 DO NOTHING 이면 벤더 정정 후에도 첫 값이 영구히 남아
**두 계층이 영구 불일치**한다(edge-review 지적). 그래서 값이 실제로 달라졌을 때만 UPDATE 한다 —
`WHERE nav IS DISTINCT FROM EXCLUDED.nav` 라 같은 값 재적재는 rowcount 0 으로 already 에 세어져
멱등 집계가 그대로 의미를 갖는다. 이 테이블은 PK 가 (etf, trade_date) 라 이력을 담을 축이 없다 —
정정 이력이 필요해지면 그건 SCD 설계라 별건이고, 그때까지는 마트가 canonical 을 따라간다.

**etf_instrument_id 해소**: canonical 의 `(market, etf_id)` → `instrument` 조회다. 시장별 MIC
매핑을 거쳐 `(market_code, ticker, instrument_type='ETF')` 로 찾는다. 마스터에 없는 ETF 는
**적재하지 않고 센다** — FK 위반으로 배치를 죽이는 대신 "마스터가 아직 이 ETF 를 모른다"는
사실을 수치로 드러낸다(Rule 12). 실제로 지금 시드에는 ETF instrument 가 091160 하나뿐이라
대부분이 여기 걸린다 — 그 수가 곧 ETF 마스터 생성(ALPHA-379 계열)이 필요하다는 근거다.

**etf_profile 선행 생성**: `etf_nav_daily.etf_instrument_id` 는 `etf_profile(instrument_id)` 를
참조하는데, `etf_profile` 행을 만드는 코드가 저장소에 없었다(ALPHA-378 이 `etf_type` NOT NULL 을
푼 이유). 그래서 **이미 instrument 가 있는 ETF 에 한해** `db.ensure_etf_profile` 로 프로필 행을
보장한다. 새 instrument 를 만들지는 않는다 — 마스터 생성은 이름 출처 문제가 얽혀 있어
(canonical holdings 에 ETF 표시명이 없다) ALPHA-379 소관이다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, ensure_etf_profile
from ..lake import Storage, canonical_etf_nav_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_etf_nav"
DATASET = "etf_nav_daily"

# 적재 대상 시장 → MIC(ISO 10383). NAV 수집이 KR 전용이라 지금은 하나다 —
# US 는 instrument 마스터가 없어(ALPHA-371) 여기 넣어도 전량 미등록으로 걸린다.
_MIC_BY_MARKET = {"KR": "XKRX"}

_CREATED_SAMPLE_LIMIT = 5


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical NAV 파티션의 trade_date 목록(정렬)."""
    marker = canonical_etf_nav_partition(market, "")  # ".../trade_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    return sorted(dates)


def _etf_instrument_ids(conn, mic: str) -> dict[str, str]:
    """(그 시장의) ticker → instrument_id. ETF 만.

    `(market_code, ticker)` 가 식별 자연키라 시장을 고정하면 ticker 로 유일하다 —
    load_price_triggers 가 ticker 만으로 조회해 복수 후보를 걱정해야 했던 것과 달리(ALPHA-448)
    여기선 canonical 이 market 을 함께 주므로 그 모호성이 없다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument"
            " WHERE market_code = %s AND instrument_type = 'ETF'",
            (mic,),
        )
        return {str(t): str(i) for t, i in cur.fetchall()}


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical NAV → etf_nav_daily 적재. 성공 0, 장애 시 비0.

    창(from/to) 미지정 = trade_date 전체 스캔. 멱등 skip 이라 재실행 비용은 신규분뿐이고,
    놓친 날짜도 다음 런이 자연 회복한다(load-documents·load-price-triggers 와 같은 모델).
    """
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_unknown_etf = 0
    already = created = updated = profiles_created = 0
    created_sample: list[dict] = []
    unknown_etfs: set[str] = set()
    failures: list[dict] = []
    exit_code = 0

    try:
        # (market, etf_id, trade_date) → 적재 후보. 같은 키가 여러 parquet(과거 잔존 part 등)에
        # 걸리면 **최신 fetched_at 이 이긴다** — canonical 병합(_merge_partition)과 같은 규칙이다.
        # 파일 순서로 마지막 값을 집으면 오래된 NAV 가 마트에 고착될 수 있다(edge-review 지적).
        candidates: dict[tuple[str, str, str], dict] = {}
        for market in _MIC_BY_MARKET:
            dates = [
                d for d in _partition_dates(storage, market)
                if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)
            ]
            for date in dates:
                prefix = canonical_etf_nav_partition(market, date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        read += 1
                        etf_id, trade_date, nav = (
                            row.get("etf_id"), row.get("trade_date"), row.get("nav"),
                        )
                        if not etf_id or not trade_date or nav is None:
                            # canonical 게이트가 이미 거른 조합이라 여기 오면 안 되는 행이다.
                            # 그래도 세고 뺀다 — 넣으면 NOT NULL·CHECK 위반으로 배치가 죽는다.
                            skipped_missing_identity += 1
                            continue
                        fetched_at = row.get("fetched_at")
                        cand_key = (market, etf_id, trade_date)
                        prev = candidates.get(cand_key)
                        if prev is not None and (fetched_at or "") < prev["fetched_at_raw"]:
                            continue
                        candidates[cand_key] = {
                            "nav": nav,
                            # available_at = '우리가 이 관측을 쓸 수 있게 된 시각'. 수집 시각이
                            # 가장 보수적인 근사다(load-documents 와 같은 규약).
                            "available_at": fetched_at or started_at.isoformat(),
                            "fetched_at_raw": fetched_at or "",
                        }

        with connect(db) as conn:
            instruments = {
                market: _etf_instrument_ids(conn, mic) for market, mic in _MIC_BY_MARKET.items()
            }
            profiled: set[str] = set()
            for (market, etf_id, trade_date), fact in sorted(candidates.items()):
                instrument_id = instruments[market].get(etf_id)
                if instrument_id is None:
                    # 마스터 미등록 — FK 위반으로 배치를 죽이는 대신 사실을 수치로 남긴다.
                    skipped_unknown_etf += 1
                    unknown_etfs.add(f"{market}:{etf_id}")
                    continue
                if instrument_id not in profiled:
                    # FK 선행. ETF 당 한 번만 시도한다(거래일 수만큼 반복 INSERT 금지).
                    if ensure_etf_profile(conn, instrument_id):
                        profiles_created += 1
                    profiled.add(instrument_id)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO etf_nav_daily (etf_instrument_id, trade_date, nav,"
                        " available_at, data_version) VALUES (%s, %s, %s, %s, %s)"
                        " ON CONFLICT (etf_instrument_id, trade_date) DO UPDATE"
                        " SET nav = EXCLUDED.nav, available_at = EXCLUDED.available_at,"
                        "     data_version = EXCLUDED.data_version"
                        " WHERE etf_nav_daily.nav IS DISTINCT FROM EXCLUDED.nav"
                        " RETURNING (xmax <> 0) AS was_update",
                        (instrument_id, trade_date, fact["nav"], fact["available_at"], run_id),
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
                        "etf_instrument_id": instrument_id, "ticker": etf_id,
                        "trade_date": trade_date, "nav": fact["nav"],
                    })
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("NAV 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, updated, created_sample, profiles_created = 0, 0, [], 0
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "markets": list(_MIC_BY_MARKET), "from_date": from_date, "to_date": to_date,
        "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_etf": skipped_unknown_etf,
        # 마스터가 모르는 ETF 목록 — ETF 마스터 생성(ALPHA-379)이 얼마나 필요한지의 근거다.
        "unknown_etfs": sorted(unknown_etfs),
        "etf_profiles_created": profiles_created,
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
        "load_etf_nav 완료: read=%d created=%d updated=%d already=%d profiles_created=%d "
        "unknown_etf=%d(%d종) skipped_identity=%d",
        read, created, updated, already, profiles_created,
        skipped_unknown_etf, len(unknown_etfs), skipped_missing_identity,
    )
    return exit_code
