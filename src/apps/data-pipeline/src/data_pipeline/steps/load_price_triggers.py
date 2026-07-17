"""ETF 가격변동 트리거 적재 — 구성종목 가중 proxy 게이트 (ALPHA-406 → 411 정본화).

분석 SFN 의 RDS 영속 전제 체인 `price_movement_trigger → etf_contribution_observation →
explanation_route` 의 첫 고리이자, 이 테이블의 **단일 writer** 다(ALPHA-411 — 분석엔진의
트리거 쓰기는 소비 전환으로 제거된다).

**게이트 정책 정본은 분석엔진 L0 다**(결정 2026-07-17): observed_return 은 ETF 자체 종가
수익률이 아니라 **구성종목 가중 proxy 수익률** — canonical holdings 의 가중치와 구성종목
일봉 수익률로 `Σ(weight·ret) / Σ(weight)` (가격이 있는 부분집합에 한정한 coverage 정규화,
analysis-engine daily_pipeline.compute_decomposition 과 같은 산식) — 이고 임계값은
3%(`l0-abs-v1`, 설정 [price_triggers])다. detection_reason 도 엔진 포맷을 따른다.

detected_at·ID·멱등은 구현 소관이라 파이프라인 방식을 유지한다: 장 마감(KST 15:30) 고정
(엔진의 런타임 시계는 재실행마다 uq 세 번째 키를 흔든다), `pmt_<ULID>`(ADR-0027),
(etf, trade_date) 존재 시 skip.

**정책 이행(자동)**: 기존 행의 detection_policy_version 이 현재 정책과 다르면 —
observation 참조가 **없을 때만** 지우고 새 정책으로 재평가한다(0.5% 잠정 계열이 여기서
정리된다). 참조가 있으면 분석 계보 보존이 우선이라 남기고 센다(stale_policy_kept).
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..config import DbConfig, PriceTriggersConfig
from ..db import connect, domain_id
from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_price_daily_partition,
    quality_log_key,
)

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


def _partition_values(storage: Storage, marker: str) -> list[str]:
    """마커 프리픽스 뒤의 파티션 값 목록(오름차순). 경로는 빌더로만 만든다(레이크 규약)."""
    values: set[str] = set()
    for key in storage.list_keys(marker):
        value = key[len(marker):].split("/", 1)[0]
        if value:
            values.add(value)
    return sorted(values)


def _num(value: object) -> float | None:
    """수치 위생 — normalize 의 _to_number 와 같은 기준(bool 차단 + isfinite).

    inf 는 비교 게이트를 조용히 통과해 CHECK 위반(런 전체 롤백)이나 가짜 수익률 커밋을
    만든다 — 결측 취급이 유일하게 안전하다(coerce-to-passing 금지).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _closes(storage: Storage, market: str, trade_date: str) -> dict[str, float]:
    """해당 거래일 파티션의 ticker → 종가(양수·유한만)."""
    out: dict[str, float] = {}
    prefix = canonical_price_daily_partition(market, trade_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            ticker = row.get("ticker")
            close = _num(row.get("close"))
            if ticker and close is not None and close > 0:
                out[str(ticker)] = close
    return out


def _holdings(storage: Storage, market: str, as_of_date: str, etf_id: str) -> list[tuple[str, float]]:
    """해당 기준일 스냅샷에서 대상 ETF 의 (구성종목 티커, 가중치 fraction) 목록."""
    holdings: list[tuple[str, float]] = []
    prefix = canonical_etf_holdings_partition(market, as_of_date)
    for key in storage.list_keys(prefix + "/"):
        if not key.endswith(".parquet"):
            continue
        for row in _read_parquet_rows(storage.get_bytes(key)):
            if str(row.get("etf_id")) != etf_id or not row.get("constituent_ticker"):
                continue
            weight_pct = _num(row.get("weight_pct"))
            if weight_pct is None or weight_pct < 0:
                continue
            holdings.append((str(row["constituent_ticker"]), weight_pct / 100.0))
    return holdings


def _proxy_return(
    holdings: list[tuple[str, float]],
    closes: dict[str, float],
    prev_closes: dict[str, float],
) -> float | None:
    """구성종목 가중 proxy 수익률 — 엔진 compute_decomposition 과 같은 산식.

    가격이 있는 부분집합에 한정해 Σ(weight·ret)/Σ(weight) 로 coverage 정규화한다.
    가격이 하나도 없으면 None(트리거 판단 불능 — 결측으로 센다).
    """
    num = den = 0.0
    for ticker, weight in holdings:
        close = closes.get(ticker)
        prev_close = prev_closes.get(ticker)
        if close is None or prev_close is None:
            continue
        num += weight * (close / prev_close - 1.0)
        den += weight
    return (num / den) if den > 0 else None


def _resolve_etf_instrument_id(conn, ticker: str) -> str | None:
    """instrument 마스터에서 ETF 의 도메인 ID. 없으면 None — 호출부가 fail-loud 한다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instrument_id FROM instrument WHERE ticker = %s AND instrument_type = 'ETF'",
            (ticker,),
        )
        row = cur.fetchone()
    return str(row[0]) if row else None


def _existing_triggers(conn, etf_instrument_id: str) -> dict[str, tuple[str, str, bool]]:
    """trade_date → (policy_version, trigger_id, observation 참조 여부).

    (etf, trade_date) 존재가 멱등의 근거이고, 정책·참조 여부가 이행 판단의 근거다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.trade_date, t.detection_policy_version, t.price_movement_trigger_id,"
            " EXISTS (SELECT 1 FROM etf_contribution_observation o"
            "         WHERE o.price_movement_trigger_id = t.price_movement_trigger_id)"
            " FROM price_movement_trigger t WHERE t.etf_instrument_id = %s",
            (etf_instrument_id,),
        )
        return {
            (d.isoformat() if hasattr(d, "isoformat") else str(d)): (str(p), str(i), bool(o))
            for d, p, i, o in cur.fetchall()
        }


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    config: PriceTriggersConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 일봉·holdings → proxy 게이트 → 트리거 적재. 성공 0, 장애 시 비0.

    창(from/to) 미지정이면 canonical 전체를 훑는다 — 멱등 skip 이라 재실행 비용은 신규분
    뿐이고, 알림을 놓친 날도 다음 런이 자연 회복한다.
    # ponytail: 전체 스캔은 파티션 수(연 ~250)에 선형 — 파티션이 수년치로 늘면 기본 창 도입
    """
    started_at = datetime.now(timezone.utc)
    considered = missing_holdings = missing_price = gated_out = 0
    already = created = replaced_stale_policy = stale_policy_kept = 0
    created_rows: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    closes_cache: dict[str, dict[str, float]] = {}

    def closes_of(date: str) -> dict[str, float]:
        if date not in closes_cache:
            closes_cache[date] = _closes(storage, config.market, date)
        return closes_cache[date]

    holdings_cache: dict[str, list[tuple[str, float]]] = {}

    def holdings_as_of(as_of: str) -> list[tuple[str, float]]:
        if as_of not in holdings_cache:
            holdings_cache[as_of] = _holdings(storage, config.market, as_of, config.etf_ticker)
        return holdings_cache[as_of]

    try:
        price_marker = canonical_price_daily_partition(config.market, "")
        dates = _partition_values(storage, price_marker)
        # canonical 최초 날짜는 분모(직전 거래일)가 레이크에 없다 — 구조적 제외라 결손
        # 신호에 섞지 않는다(매 런 +1 상수 노이즈가 실제 결손을 묻는다).
        prev_by_date = {dates[i]: dates[i - 1] for i in range(1, len(dates))}
        targets = [d for d in dates[1:]
                   if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]

        # holdings 기준일: 거래일 이하의 최신 스냅샷(없으면 최초) — 엔진과 같은 선택 규칙.
        holdings_dates = _partition_values(
            storage, canonical_etf_holdings_partition(config.market, ""))

        with connect(db) as conn:
            etf_instrument_id = _resolve_etf_instrument_id(conn, config.etf_ticker)
            if etf_instrument_id is None:
                # 트리거는 ETF 도메인 ID 에 매달린다 — 마스터가 없으면 만들 수 있는 게 없다.
                # 조용히 0건 성공으로 끝나면 전제 결손이 안 보이므로 fail-loud 하되, 아래
                # 로그 블록까지 내려가 quality log 는 남긴다("결과는 항상 로그").
                logger.error(
                    "instrument 마스터에 ETF 가 없다: ticker=%s — 스키마 시드 마이그레이션"
                    "(V202607150004 seed_entity_master_kr) 적용 여부 확인", config.etf_ticker)
                failures.append({"reasons": ["missing_etf_master"],
                                 "error": f"instrument 에 ETF ticker={config.etf_ticker} 없음"})
                exit_code = 1
                targets = []
                etf_instrument_id = ""  # 아래 루프는 targets=[] 라 닿지 않는다

            existing = _existing_triggers(conn, etf_instrument_id) if targets else {}
            for date in targets:
                considered += 1
                state = existing.get(date)
                if state is not None:
                    policy, trigger_id, has_observation = state
                    if policy == config.policy_version:
                        already += 1
                        continue
                    if has_observation:
                        # 구정책 행이지만 분석 계보(observation→route→explanation)가 매달려
                        # 있다 — 지우면 설명 이력이 끊긴다. 보존하고 수치로 드러낸다.
                        stale_policy_kept += 1
                        continue
                    # 구정책·무참조 — 잠정 정책(0.5%) 계열 정리. 지우고 새 정책으로 재평가.
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM price_movement_trigger"
                            " WHERE price_movement_trigger_id = %s",
                            (trigger_id,),
                        )
                    replaced_stale_policy += 1

                as_of_eligible = [x for x in holdings_dates if x <= date]
                as_of = (as_of_eligible[-1] if as_of_eligible
                         else (holdings_dates[0] if holdings_dates else None))
                holdings = holdings_as_of(as_of) if as_of else []
                if not holdings:
                    missing_holdings += 1
                    continue

                proxy_ret = _proxy_return(holdings, closes_of(date),
                                          closes_of(prev_by_date[date]))
                if proxy_ret is None:
                    missing_price += 1
                    continue
                if abs(proxy_ret) < config.abs_threshold:
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
                            proxy_ret,
                            config.policy_version,
                            # 엔진 l0_gate 와 같은 사유 포맷 — 정책 정본 추적성.
                            f"abs|{proxy_ret:.4f}|>={config.abs_threshold}",
                        ),
                    )
                created += 1
                created_rows.append({"trade_date": date, "observed_return": proxy_ret,
                                     "price_movement_trigger_id": trigger_id})
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("트리거 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, created_rows = 0, []
        replaced_stale_policy = 0
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "market": config.market, "etf_ticker": config.etf_ticker,
        "abs_threshold": config.abs_threshold, "policy_version": config.policy_version,
        "dates_considered": considered, "missing_holdings": missing_holdings,
        "missing_price": missing_price, "gated_out": gated_out,
        "already_present": already, "replaced_stale_policy": replaced_stale_policy,
        "stale_policy_kept": stale_policy_kept,
        "created": created, "created_rows": created_rows,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_price_triggers: considered=%d missing_holdings=%d missing_price=%d gated_out=%d"
        " already=%d replaced_stale=%d stale_kept=%d created=%d failures=%d",
        considered, missing_holdings, missing_price, gated_out, already,
        replaced_stale_policy, stale_policy_kept, created, len(failures),
    )
    return exit_code
