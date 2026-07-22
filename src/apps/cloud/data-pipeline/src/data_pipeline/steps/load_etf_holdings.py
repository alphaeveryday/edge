"""ETF 구성종목 적재 — canonical ETF 구성종목 → etf_holding_snapshot (ALPHA-379).

`canonical/holdings/etf_holdings/market=…/as_of_date=…` 을 읽어 ETF·거래일·구성종목 grain 의
보유 비중을 Cloud Event Store 에 적재한다. 수집(ALPHA-370) → 정제(normalize_etf) → 이 스텝이
체인의 끝이다. load_etf_nav 와 같은 모델이다 — 키가 하나 더(구성종목) 있을 뿐이다.

**두 FK 해소**: 한 행이 `etf_instrument_id`(그 ETF)와 `constituent_instrument_id`(구성종목)를
모두 `instrument` 에서 찾아야 한다. `(market_code, ticker)` 가 유일 자연키(uq_instrument_market_ticker).
  * ETF = `etf_id` → instrument WHERE type='ETF'. ETF 는 canonical 에 per-row MIC 이 없고 전부
    XKRX 라 ticker 로 유일하다.
  * 구성종목 = **`(constituent_mic, constituent_ticker)`** → instrument. canonical 이 실어 주는
    `constituent_mic`(KRX MKT_ID→MIC, normalize_etf 흡수)로 **정확한 자연키**를 만든다. KR 은
    KOSPI=XKRX·KOSDAQ=XKOS·KONEX=XKON 3개 MIC 을 쓰는데(구성종목엔 KOSDAQ 이 섞인다), MIC 을
    함께 보면 XKRX·XKOS 동명 종목이 있어도 모호함 없이 갈린다(가격 로더 ALPHA-377 은 canonical
    에 MIC 이 없어 ticker 로 세 MIC 를 훑어야 했지만, 여기선 MIC 을 알아 그럴 필요가 없다).
어느 쪽이든 마스터에 없으면 **적재하지 않고 센다** — FK 위반으로 배치를 죽이는 대신 무엇이
미등록인지 수치로 드러낸다(Rule 12). US 는 구성종목이 대량 미등록(ALPHA-371)이라 그 수가
곧 마스터 확장의 근거다.

**etf_profile 선행 생성**: `etf_holding_snapshot.etf_instrument_id` 는 `etf_profile(instrument_id)`
를 참조한다 — `db.ensure_etf_profile` 로 ETF 당 한 번 보장한다(load_etf_nav 와 같은 근거,
`etf_type` 은 비운다 — ALPHA-378). 구성종목 쪽은 `instrument` 직접 참조라 선행이 없다.

**weight_ratio = weight_pct / 100**: canonical 은 퍼센트(COMPST_RTO·weightPercentage)로 나른다.
`ck_etf_holding_weight_ratio` 는 [0,1] 을 요구하므로 비율로 환산한다. **결측(비중 미보고)은 정당한
NULL 로 적재**하지만 NaN·Infinity·비수치는 **오염**이라 결측과 구별해 bad_weight 로 격리한다(둘을
None 으로 뭉개면 위장 적재된다, Rule 12). 환산 후 범위를 벗어나거나(정제/수집 깨짐) ETF 가
자기 자신을 담는(ck_etf_holding_not_self) 행도 적재 전 격리한다.

**멱등**: PK `(etf_instrument_id, constituent_instrument_id, trade_date)` 로 수렴한다 —
`ON CONFLICT … DO UPDATE … WHERE weight_ratio IS DISTINCT FROM …` 라 같은 값 재적재는 already,
비중 정정만 UPDATE 로 흐른다(load_etf_nav 와 같은 근거).

ponytail: 스냅샷 delete 시맨틱은 없다 — 재수집에서 어떤 구성종목이 빠져도 마트의 옛 행은
남는다(normalize_etf 가 같은 한계를 명시). 유니버스·정정 빈도가 낮아 무해하며, 필요해지면
etf·trade_date 스냅샷 통째 교체가 별건이다.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, ensure_etf_profile
from ..lake import Storage, canonical_etf_holdings_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_etf_holdings"
DATASET = "etf_holding_snapshot"

# 적재 대상 시장(레이크 지역 키) → 그 시장의 MIC(ISO 10383) 집합. canonical 은 지역 "KR" 만
# 주고 MIC 는 없는데, KR 은 KRX 산하 세 시장이 다른 MIC 를 쓴다(KOSPI=XKRX·KOSDAQ=XKOS·
# KONEX=XKON). 구성종목엔 KOSDAQ·KONEX 종목이 섞이므로 XKRX 만 보면 그들이 마스터에 있어도
# unknown 으로 조용히 버려진다(load_instruments 는 constituent_mic 로 XKOS·XKON 에도 적재한다).
# ETF 자신은 전부 XKRX 지만 구성종목 때문에 세 MIC 를 다 조회한다(ALPHA-377 Codex P1 과 동종).
# US 는 구성종목이 마스터에 없어(ALPHA-371) 여기 넣어도 전량 미등록으로 걸린다.
_MICS_BY_MARKET = {"KR": ("XKRX", "XKOS", "XKON")}

_CREATED_SAMPLE_LIMIT = 5
# ETF 별 비중 합 정상 범위 — 1 근처여야 한다(부분 커버리지·정제 깨짐이면 크게 벗어난다).
_WEIGHT_SUM_LO, _WEIGHT_SUM_HI = 0.90, 1.10


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical 구성종목 파티션의 as_of_date 목록(정렬)."""
    marker = canonical_etf_holdings_partition(market, "")  # ".../as_of_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    return sorted(dates)


def _instrument_ids(conn, mics: tuple[str, ...]) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """(그 지역 MIC 집합의) ETF ticker→id 와 (market_code, ticker)→id 두 맵.

    구성종목 FK 는 canonical 이 실어 주는 `constituent_mic`(KRX MKT_ID → MIC, normalize_etf 가
    흡수) 과 ticker 로 **정확한 자연키 `(market_code, ticker)`** 로 해소한다 — ticker 단독으로
    시장을 가로질러 찾지 않으므로 XKRX·XKOS 동명 종목이 섞여도 모호함이 없다(ALPHA-379 Codex
    지적). ETF 자신은 canonical 에 per-row MIC 이 없고 전부 XKRX 라 ticker(type='ETF')로 찾는다.
    한 번의 조회로 둘 다 만든다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT market_code, ticker, instrument_id, instrument_type FROM instrument"
            " WHERE market_code = ANY(%s)",
            (list(mics),),
        )
        rows = cur.fetchall()
    by_key = {(str(mc), str(t)): str(i) for mc, t, i, _ in rows}
    etf_ids = {str(t): str(i) for _, t, i, typ in rows if typ == "ETF"}
    return etf_ids, by_key


def _weight_ratio(weight_pct) -> tuple[float | None, bool]:
    """퍼센트 → (비율, ok). **결측과 오염을 구분한다**(Rule 12).

    weight_pct=None 은 '비중 미보고'라 정당한 결측 → `(None, True)`, weight_ratio NULL 로 적재한다
    (ck 가 NULL 허용). 하지만 NaN·Infinity·비수치는 **오염**이다 — None 으로 뭉개면 정당한 결측과
    구별할 수 없어 bad_weight 게이트를 우회해 '비중 미보고'로 위장 적재된다(Codex 지적). 그래서
    `(None, False)` 로 돌려 호출부가 bad_weight 로 격리하게 한다."""
    if weight_pct is None:
        return None, True
    try:
        pct = float(weight_pct)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(pct):
        return None, False
    return pct / 100.0, True


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 구성종목 → etf_holding_snapshot 적재. 성공 0, 장애 시 비0.

    창(from/to) 미지정 = as_of_date 전체 스캔. 멱등 skip 이라 재실행 비용은 신규분뿐이고,
    놓친 날짜도 다음 런이 자연 회복한다(load-etf-nav 와 같은 모델).
    """
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_unknown_etf = 0
    skipped_unknown_constituent = skipped_bad_weight = skipped_self = 0
    already = created = updated = profiles_created = 0
    created_sample: list[dict] = []
    unknown_etfs: set[str] = set()
    unknown_constituents: set[str] = set()
    weight_sums: dict[str, float] = {}  # "market:etf_id:as_of" → 비중 합(정상성 점검용)
    failures: list[dict] = []
    exit_code = 0

    try:
        # (market, etf_id, constituent_mic, constituent_ticker, as_of_date) → 후보. constituent_mic
        # 를 키에 **포함**한다 — 빼면 XKRX·XKOS 동명 종목 두 보유행이 한 후보로 뭉쳐 하나가
        # 유실된다(Codex 지적). 같은 키가 여러 parquet 에 걸리면 최신 fetched_at 이 이긴다.
        candidates: dict[tuple[str, str, str, str, str], dict] = {}
        for market in _MICS_BY_MARKET:
            dates = [
                d for d in _partition_dates(storage, market)
                if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)
            ]
            for date in dates:
                prefix = canonical_etf_holdings_partition(market, date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        read += 1
                        etf_id, ct = row.get("etf_id"), row.get("constituent_ticker")
                        mic, as_of = row.get("constituent_mic"), row.get("as_of_date")
                        if not etf_id or not ct or not as_of:
                            # 정체성 없는 행은 키를 만들 수 없다 — 세고 뺀다(Rule 12).
                            # constituent_mic 결측은 여기서 막지 않는다 — 비상장(원화현금 등)은
                            # MIC 이 없고 아래 해소에서 자연히 unknown 으로 걸린다.
                            skipped_missing_identity += 1
                            continue
                        fetched_at = row.get("fetched_at")
                        ratio, weight_ok = _weight_ratio(row.get("weight_pct"))
                        cand_key = (market, etf_id, mic or "", ct, as_of)
                        prev = candidates.get(cand_key)
                        if prev is not None and (fetched_at or "") < prev["fetched_at_raw"]:
                            continue
                        candidates[cand_key] = {
                            "mic": mic, "weight_ratio": ratio, "weight_ok": weight_ok,
                            "available_at": fetched_at or started_at.isoformat(),
                            "fetched_at_raw": fetched_at or "",
                        }

        with connect(db) as conn:
            resolved = {
                market: _instrument_ids(conn, mics) for market, mics in _MICS_BY_MARKET.items()
            }
            profiled: set[str] = set()
            for (market, etf_id, _mic_key, ct, as_of), fact in sorted(candidates.items()):
                etf_ids, by_key = resolved[market]
                etf_instrument_id = etf_ids.get(etf_id)
                if etf_instrument_id is None:
                    skipped_unknown_etf += 1
                    unknown_etfs.add(f"{market}:{etf_id}")
                    continue
                # 구성종목은 canonical 이 실어 준 MIC 로 정확한 자연키 (market_code, ticker) 해소.
                # MIC 결측(비상장)·미등록은 매칭 실패로 unknown 에 걸린다.
                constituent_instrument_id = by_key.get((fact["mic"], ct)) if fact["mic"] else None
                if constituent_instrument_id is None:
                    skipped_unknown_constituent += 1
                    unknown_constituents.add(f"{fact['mic'] or market}:{ct}")
                    continue
                if etf_instrument_id == constituent_instrument_id:
                    # ck_etf_holding_not_self — ETF 가 자기 자신을 담을 수 없다.
                    skipped_self += 1
                    continue
                if not fact["weight_ok"]:
                    # 비중이 NaN·Infinity·비수치 — 오염이다. 결측(정당한 NULL)과 달리 격리한다(Rule 12).
                    skipped_bad_weight += 1
                    continue
                ratio = fact["weight_ratio"]
                if ratio is not None and not (0.0 <= ratio <= 1.0):
                    # ck_etf_holding_weight_ratio 위반 — 정제/수집이 깨진 신호. 격리하고 센다.
                    skipped_bad_weight += 1
                    continue
                if ratio is not None:
                    sum_key = f"{market}:{etf_id}:{as_of}"
                    weight_sums[sum_key] = weight_sums.get(sum_key, 0.0) + ratio
                if etf_instrument_id not in profiled:
                    # FK 선행. ETF 당 한 번만 시도한다.
                    if ensure_etf_profile(conn, etf_instrument_id):
                        profiles_created += 1
                    profiled.add(etf_instrument_id)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO etf_holding_snapshot (etf_instrument_id,"
                        " constituent_instrument_id, trade_date, weight_ratio,"
                        " available_at, data_version) VALUES (%s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (etf_instrument_id, constituent_instrument_id, trade_date)"
                        " DO UPDATE SET weight_ratio = EXCLUDED.weight_ratio,"
                        "     available_at = EXCLUDED.available_at,"
                        "     data_version = EXCLUDED.data_version"
                        " WHERE etf_holding_snapshot.weight_ratio"
                        "       IS DISTINCT FROM EXCLUDED.weight_ratio"
                        " RETURNING (xmax <> 0) AS was_update",
                        (etf_instrument_id, constituent_instrument_id, as_of, ratio,
                         fact["available_at"], run_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        already += 1
                        continue
                    if row[0]:
                        updated += 1
                        continue
                created += 1
                if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                    created_sample.append({
                        "etf_instrument_id": etf_instrument_id, "etf_id": etf_id,
                        "constituent_ticker": ct, "trade_date": as_of, "weight_ratio": ratio,
                    })
    except Exception as exc:
        # 커밋 경계는 런 전체다 — 예외면 롤백이라 부분 적재가 없다. 트레이스백으로 죽는 대신
        # 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("구성종목 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, updated, created_sample, profiles_created = 0, 0, [], 0
        weight_sums = {}
        exit_code = 1

    # ETF·거래일별 비중 합이 상식 범위(≈1)를 벗어나면 드러낸다 — 부분 커버리지·정제 깨짐의 신호.
    # 게이트가 아니라 감사 신호다(적재는 하되 로그로 알린다).
    weight_sum_anomalies = [
        {"key": k, "weight_sum": round(v, 6)}
        for k, v in sorted(weight_sums.items())
        if not (_WEIGHT_SUM_LO <= v <= _WEIGHT_SUM_HI)
    ]

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "markets": list(_MICS_BY_MARKET), "from_date": from_date, "to_date": to_date,
        "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_etf": skipped_unknown_etf,
        "unknown_etfs": sorted(unknown_etfs),
        "skipped_unknown_constituent": skipped_unknown_constituent,
        # 마스터가 모르는 구성종목 목록 — instrument 마스터 확장(US 등)의 근거다.
        "unknown_constituents": sorted(unknown_constituents),
        "skipped_bad_weight": skipped_bad_weight,
        "skipped_self": skipped_self,
        "etf_profiles_created": profiles_created,
        "already_present": already, "created": created, "updated": updated,
        "weight_sum_anomalies": weight_sum_anomalies,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
    }
    if weight_sum_anomalies:
        logger.warning("ETF 비중 합 이상 %d건 — 부분 커버리지/정제 점검 필요", len(weight_sum_anomalies))
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = exit_code or 1

    logger.info(
        "load_etf_holdings 완료: read=%d created=%d updated=%d already=%d profiles_created=%d "
        "unknown_etf=%d unknown_constituent=%d bad_weight=%d self=%d "
        "skipped_identity=%d anomalies=%d",
        read, created, updated, already, profiles_created,
        skipped_unknown_etf, skipped_unknown_constituent,
        skipped_bad_weight, skipped_self, skipped_missing_identity, len(weight_sum_anomalies),
    )
    return exit_code
