"""ETF 가격변동 트리거 적재 — canonical 일봉 → price_movement_trigger (ALPHA-406).

분석 SFN(alphamale-etf-daily-v1)의 RDS 영속 전제 체인
`price_movement_trigger → etf_contribution_observation → explanation_route` 의 **첫 고리**다.
트리거 산출은 가격의 결정적 변환(일수익률 + 게이트 비교)이라 파이프라인 소관이고, 후속 두
테이블은 Analysis Engine 산출이라 여기서 만들지 않는다.

canonical `market_data/price_daily/market=…` 에서 대상 ETF 의 종가를 모아 거래일마다
직전 거래일 대비 수익률을 계산하고, **absolute gate(|r| >= threshold)를 통과한 날만**
행을 만든다. 게이트 미통과 일자는 행이 없는 게 스키마의 의도다("이 행이 없는 날에는
후속 분석 행이 없을 수 있다") — 대신 그 수를 로그로 남긴다(조용히 버리지 않음).

**게이트 정책은 잠정이다** — 임계값·relative gate 규칙은 로직 소유자 합의 대상이라
설정([price_triggers])으로 빼고, detection_policy_version 으로 어느 정책의 행인지 남긴다.
relative gate 는 미구현(항상 false — CHECK 제약이 absolute 만으로 성립).

**멱등**: (etf_instrument_id, trade_date) 가 이미 있으면 skip 한다. detected_at 도
장 마감(KST 15:30) 고정이라 재실행이 uq(etf, trade_date, detected_at)를 흔들지 않는다.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..config import DbConfig, PriceTriggersConfig
from ..db import connect, domain_id
from ..lake import Storage, canonical_price_daily_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_price_triggers"
DATASET = "price_movement_trigger"

# 결정적 detected_at — KRX 정규장 마감. 시각을 런타임 시계로 찍으면 재실행마다
# uq(etf, trade_date, detected_at)의 세 번째 키가 달라져 중복 행이 쌓인다.
_MARKET_CLOSE_KST = "T15:30:00+09:00"


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical 일봉 파티션의 trade_date 목록(오름차순). 경로는 빌더로만 만든다."""
    marker = canonical_price_daily_partition(market, "")  # ".../trade_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def _etf_close(storage: Storage, market: str, trade_date: str, ticker: str) -> float | None:
    """해당 거래일 파티션에서 대상 ETF 의 종가. 행이 없으면 None(수집 이전 날짜 등)."""
    prefix = canonical_price_daily_partition(market, trade_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            if row.get("ticker") == ticker:
                close = row.get("close")
                # normalize 의 _to_number 와 같은 위생 기준(bool 차단 + isfinite) — inf 는
                # `> 0` 을 통과해 수익률 inf(CHECK 위반, 런 전체 롤백)나 분모 inf(가짜 -100%
                # 트리거 커밋)를 만든다. 0/음수·NaN·bool 과 함께 원료 결측 취급으로 뺀다.
                if (isinstance(close, (int, float)) and not isinstance(close, bool)
                        and math.isfinite(close) and close > 0):
                    return float(close)
                return None
    return None


def _resolve_etf_instrument_id(conn, ticker: str) -> str | None:
    """instrument 마스터에서 ETF 의 도메인 ID. 없으면 None — 호출부가 fail-loud 한다.

    analysis-engine daily_pipeline 과 같은 조회(ticker + instrument_type='ETF')를 써서
    두 소비자가 같은 행을 보게 한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instrument_id FROM instrument WHERE ticker = %s AND instrument_type = 'ETF'",
            (ticker,),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def _existing_trade_dates(conn, etf_instrument_id: str) -> set[str]:
    """이미 적재된 trade_date — (etf, trade_date) 존재 시 skip 이 멱등의 근거다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date FROM price_movement_trigger WHERE etf_instrument_id = %s",
            (etf_instrument_id,),
        )
        return {d.isoformat() if hasattr(d, "isoformat") else str(d) for (d,) in cur.fetchall()}


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    config: PriceTriggersConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 일봉 → 가격변동 트리거 적재. 성공 0, 장애 시 비0.

    창(from/to) 미지정이면 canonical 전체를 훑는다 — 멱등 skip 이라 재실행 비용은 신규분
    뿐이고, 알림을 놓친 날도 다음 런이 자연 회복한다.
    # ponytail: 전체 스캔은 파티션 수(연 ~250)에 선형 — 파티션이 수년치로 늘면 기본 창 도입
    """
    started_at = datetime.now(timezone.utc)
    considered = missing_price = missing_prev = gated_out = 0
    already = created = 0
    created_rows: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    # 종가는 날짜당 1회만 읽는다(직전 거래일 종가가 다음 날짜의 분모로 재사용된다).
    closes: dict[str, float | None] = {}

    def close_of(date: str) -> float | None:
        if date not in closes:
            closes[date] = _etf_close(storage, config.market, date, config.etf_ticker)
        return closes[date]

    try:
        dates = _partition_dates(storage, config.market)
        # canonical 최초 날짜는 분모(직전 거래일)가 레이크에 없다 — 구조적 제외라 결손
        # 신호(missing_prev)에 섞지 않는다(매 런 +1 상수 노이즈가 실제 결손을 묻는다).
        prev_by_date = {dates[i]: dates[i - 1] for i in range(1, len(dates))}
        targets = [d for d in dates[1:]
                   if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]

        with connect(db) as conn:
            etf_instrument_id = _resolve_etf_instrument_id(conn, config.etf_ticker)
            if etf_instrument_id is None:
                # 트리거는 ETF 도메인 ID 에 매달린다 — 마스터가 없으면 만들 수 있는 게 없다.
                # 조용히 0건 성공으로 끝나면 전제 결손이 안 보이므로 fail-loud 하되, 아래
                # 로그 블록까지 내려가 quality log 는 남긴다("결과는 항상 로그"). ETF 행의
                # 출처는 load-instruments(EQUITY 만 적재)가 아니라 시드 마이그레이션이다.
                logger.error(
                    "instrument 마스터에 ETF 가 없다: ticker=%s — 스키마 시드 마이그레이션"
                    "(V202607150004 seed_entity_master_kr) 적용 여부 확인", config.etf_ticker)
                failures.append({"reasons": ["missing_etf_master"],
                                 "error": f"instrument 에 ETF ticker={config.etf_ticker} 없음"})
                exit_code = 1
                targets = []
                etf_instrument_id = ""  # 아래 루프는 targets=[] 라 닿지 않는다

            have = _existing_trade_dates(conn, etf_instrument_id) if targets else set()
            for date in targets:
                considered += 1
                if date in have:
                    already += 1
                    continue
                close = close_of(date)
                if close is None:
                    missing_price += 1
                    continue
                prev_close = close_of(prev_by_date[date])
                if prev_close is None:
                    missing_prev += 1
                    continue
                observed_return = close / prev_close - 1.0
                if abs(observed_return) < config.abs_threshold:
                    gated_out += 1
                    continue
                trigger_id = domain_id("pmt")
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO price_movement_trigger (price_movement_trigger_id,"
                        " etf_instrument_id, trade_date, detected_at, observed_return,"
                        " market_relative_return, absolute_gate_triggered,"
                        " relative_gate_triggered, detection_policy_version, detection_reason)"
                        " VALUES (%s, %s, %s, %s, %s, NULL, TRUE, FALSE, %s, %s)",
                        (
                            trigger_id,
                            etf_instrument_id,
                            date,
                            f"{date}{_MARKET_CLOSE_KST}",
                            observed_return,
                            config.policy_version,
                            f"|{observed_return:.6f}| >= {config.abs_threshold} (absolute gate)",
                        ),
                    )
                created += 1
                created_rows.append({"trade_date": date, "observed_return": observed_return,
                                     "price_movement_trigger_id": trigger_id})
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("트리거 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, created_rows = 0, []
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "market": config.market, "etf_ticker": config.etf_ticker,
        "abs_threshold": config.abs_threshold, "policy_version": config.policy_version,
        "dates_considered": considered, "missing_price": missing_price,
        "missing_prev": missing_prev, "gated_out": gated_out,
        "already_present": already, "created": created, "created_rows": created_rows,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_price_triggers: considered=%d missing_price=%d missing_prev=%d gated_out=%d"
        " already=%d created=%d failures=%d",
        considered, missing_price, missing_prev, gated_out, already, created, len(failures),
    )
    return exit_code
