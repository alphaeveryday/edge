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
from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_run_manifest_key,
    quality_log_key,
)

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
# normalize_etf가 만들 수 있는 market. loader는 현재 KR만 소비하지만 US가 같은 manifest에 함께
# 있는 것은 정상이다(FMP 재활성화 시). 소비 범위와 manifest 어휘를 섞지 않는다.
_MANIFEST_MARKETS = frozenset(("KR", "US"))

_CREATED_SAMPLE_LIMIT = 5
# ETF 별 비중 합 정상 범위 — 1 근처여야 한다(부분 커버리지·정제 깨짐이면 크게 벗어난다).
_WEIGHT_SUM_LO, _WEIGHT_SUM_HI = 0.90, 1.10


def _fetched_at(value: object) -> datetime | None:
    """정상 ISO 시각을 UTC로 돌려준다. 결측·비문자열·파싱 실패는 None이다."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


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


def _manifest_partitions(storage: Storage, input_run_id: str) -> set[tuple[str, str]]:
    """normalize-etf 실행 로그가 증명한 canonical 파티션 집합.

    checked_date 를 추측하거나 quality log 전체를 LIST하지 않고 run_id 직접 키를 GET한다.
    없거나 불완전하면 범위를 넓히지 않고 실패한다(Rule 12).
    """
    key = canonical_run_manifest_key("etf_holdings", input_run_id)
    log = json.loads(storage.get_bytes(key).decode("utf-8"))
    if not isinstance(log, dict) or log.get("run_id") != input_run_id:
        raise ValueError(f"요청한 run_id의 manifest가 아니다: run_id={input_run_id}")
    if log.get("job_name") != "normalize_etf" or log.get("canonical_written") is not True:
        raise ValueError(f"완료된 normalize-etf manifest가 아니다: run_id={input_run_id}")
    raw = log.get("canonical_partitions")
    if not isinstance(raw, list):
        raise ValueError(f"canonical_partitions가 없는 구형 manifest다: run_id={input_run_id}")
    partitions: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"canonical_partitions 항목이 객체가 아니다: run_id={input_run_id}")
        market, as_of = item.get("market"), item.get("as_of_date")
        try:
            valid_date = (
                isinstance(as_of, str)
                and datetime.strptime(as_of, "%Y-%m-%d").strftime("%Y-%m-%d") == as_of
            )
        except ValueError:
            valid_date = False
        if market not in _MANIFEST_MARKETS or not valid_date:
            raise ValueError(f"canonical_partitions 항목이 유효하지 않다: {item!r}")
        partition = (market, as_of)
        if partition in partitions:
            raise ValueError(f"canonical_partitions 항목이 중복됐다: {item!r}")
        partitions.add(partition)
    return partitions


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
    if isinstance(weight_pct, bool):
        return None, False
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
    expected_etfs: frozenset[str] | None = None,
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 구성종목 → etf_holding_snapshot 적재. 성공 0, 장애 시 비0.

    input_run_id 지정 = 그 normalize-etf 실행이 쓴 파티션만 처리한다. from/to 는 명시 백필,
    셋 다 미지정은 호출자가 명시적으로 선택한 전체 스캔이다. 서로 섞으면 범위가 모호해 거부한다.
    """
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_unknown_etf = 0
    skipped_unknown_constituent = skipped_bad_weight = skipped_self = skipped_foreign_etf = 0
    skipped_unsupported_asset = 0
    skipped_unknown_asset_type = 0
    skipped_bad_fetched_at = 0
    deduplicated_rows = 0
    unsupported_asset_counts: dict[str, int] = {}
    unknown_asset_types: set[str] = set()
    already = created = updated = profiles_created = skipped_identity_conflict = 0
    created_sample: list[dict] = []
    unknown_etfs: set[str] = set()
    unknown_constituents: set[str] = set()
    weight_sums: dict[str, float] = {}  # "market:etf_id:as_of" → 비중 합(정상성 점검용)
    failures: list[dict] = []
    exit_code = 0

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        manifest = _manifest_partitions(storage, input_run_id) if input_run_id is not None else None
        # canonical 자연키 (market, etf_id, constituent_ticker, as_of_date) → 후보. MIC는
        # 정정 가능한 속성이므로 키에서 빼고, 같은 키가 여러 parquet에 걸리면 최신 fetched_at이 이긴다.
        candidates: dict[tuple[str, str, str, str], dict] = {}
        logical_rows: dict[tuple[str, str, str, str], tuple[dict, datetime]] = {}
        for market in _MICS_BY_MARKET:
            dates = (sorted(date for item_market, date in manifest if item_market == market)
                     if manifest is not None else [
                         d for d in _partition_dates(storage, market)
                         if (from_date is None or d >= from_date)
                         and (to_date is None or d <= to_date)
                     ])
            for date in dates:
                prefix = canonical_etf_holdings_partition(market, date)
                parquet_keys = [
                    key for key in storage.list_keys(prefix + "/") if key.endswith(".parquet")
                ]
                if manifest is not None and not parquet_keys:
                    raise ValueError(f"manifest 파티션에 parquet가 없다: market={market}, date={date}")
                partition_rows = 0
                for key in parquet_keys:
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        partition_rows += 1
                        read += 1
                        row_market, as_of = row.get("market"), row.get("as_of_date")
                        if any(
                            not isinstance(value, str) or not value.strip() or value != value.strip()
                            for value in (row_market, as_of)
                        ):
                            skipped_missing_identity += 1
                            continue
                        if row_market != market or as_of != date:
                            raise ValueError(
                                "canonical 행과 파티션이 일치하지 않는다: "
                                f"market={market}, date={date}, key={key}"
                            )
                        etf_id, ct = row.get("etf_id"), row.get("constituent_ticker")
                        identities = (etf_id, ct)
                        if any(
                            not isinstance(value, str) or not value.strip() or value != value.strip()
                            for value in identities
                        ):
                            # 정체성 없는 행은 키를 만들 수 없다 — 세고 뺀다(Rule 12).
                            # constituent_mic 결측은 여기서 막지 않는다 — 자산유형 판정 뒤 주식만
                            # 구성종목 해소 실패로 센다.
                            skipped_missing_identity += 1
                            continue
                        raw_fetched_at = row.get("fetched_at")
                        parsed_fetched_at = _fetched_at(raw_fetched_at)
                        if parsed_fetched_at is None:
                            skipped_bad_fetched_at += 1
                            continue
                        cand_key = (market, etf_id, ct, as_of)
                        prev = logical_rows.get(cand_key)
                        if prev is not None:
                            deduplicated_rows += 1
                            if parsed_fetched_at < prev[1]:
                                continue
                        logical_rows[cand_key] = (row, parsed_fetched_at)
                if manifest is not None and partition_rows == 0:
                    raise ValueError(f"manifest 파티션이 0행이다: market={market}, date={date}")

        for cand_key, (row, parsed_fetched_at) in logical_rows.items():
            market, etf_id, ct, _as_of = cand_key
            mic = row.get("constituent_mic")
            raw_asset_type = row.get("constituent_asset_type")
            asset_type = raw_asset_type if isinstance(raw_asset_type, str) else "UNKNOWN"
            if asset_type not in {"EQUITY", "CASH", "OPTION"}:
                skipped_unknown_asset_type += 1
                unknown_asset_types.add(asset_type)
                continue
            if asset_type in {"CASH", "OPTION"}:
                skipped_unsupported_asset += 1
                unsupported_asset_counts[asset_type] = unsupported_asset_counts.get(asset_type, 0) + 1
                continue
            if expected_etfs is not None and etf_id not in expected_etfs:
                skipped_foreign_etf += 1
                continue
            if not isinstance(mic, str) or not mic.strip() or mic != mic.strip():
                skipped_unknown_constituent += 1
                unknown_constituents.add(f"{market}:{ct}")
                continue
            ratio, weight_ok = _weight_ratio(row.get("weight_pct"))
            candidates[cand_key] = {
                "mic": mic, "weight_ratio": ratio, "weight_ok": weight_ok,
                "asset_type": asset_type,
                "available_at": row["fetched_at"],
                "fetched_at": parsed_fetched_at,
            }

        with connect(db) as conn:
            resolved = {
                market: _instrument_ids(conn, mics) for market, mics in _MICS_BY_MARKET.items()
            }
            ids_by_ticker: dict[str, dict[str, set[str]]] = {}
            for resolved_market, (_etf_ids, by_key) in resolved.items():
                market_ids: dict[str, set[str]] = {}
                for (_mic, ticker), instrument_id in by_key.items():
                    market_ids.setdefault(ticker, set()).add(instrument_id)
                ids_by_ticker[resolved_market] = market_ids
            profiled: set[str] = set()
            for (market, etf_id, ct, as_of), fact in sorted(candidates.items()):
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
                    stale_ids = sorted(
                        ids_by_ticker[market].get(ct, set()) - {constituent_instrument_id}
                    )
                    if stale_ids:
                        cur.execute(
                            "SELECT EXISTS (SELECT 1 FROM etf_holding_snapshot"
                            " WHERE etf_instrument_id = %s AND trade_date = %s"
                            " AND constituent_instrument_id = ANY(%s))",
                            (etf_instrument_id, as_of, stale_ids),
                        )
                        if cur.fetchone()[0]:
                            # instrument MIC 이전은 ADR-0027 별건이다. 보유행만 새 ID로 옮기면
                            # 마스터 정체성과 어긋나므로 중복 INSERT 대신 명시적 유실로 막는다.
                            skipped_identity_conflict += 1
                            continue
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
        "markets": list(_MICS_BY_MARKET), "input_run_id": input_run_id,
        "from_date": from_date, "to_date": to_date,
        "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_etf": skipped_unknown_etf,
        "unknown_etfs": sorted(unknown_etfs),
        "skipped_unknown_constituent": skipped_unknown_constituent,
        "skipped_unsupported_asset": skipped_unsupported_asset,
        "unsupported_asset_counts": dict(sorted(unsupported_asset_counts.items())),
        "skipped_unknown_asset_type": skipped_unknown_asset_type,
        "unknown_asset_types": sorted(unknown_asset_types),
        "skipped_bad_fetched_at": skipped_bad_fetched_at,
        # 동일 자연키의 여러 물리 행은 최신 fetched_at 한 건으로 수렴한 정상 중복이다.
        "deduplicated_rows": deduplicated_rows,
        # 마스터가 모르는 구성종목 목록 — instrument 마스터 확장(US 등)의 근거다.
        "unknown_constituents": sorted(unknown_constituents),
        "skipped_bad_weight": skipped_bad_weight,
        "skipped_self": skipped_self,
        "skipped_foreign_etf": skipped_foreign_etf,
        "etf_profiles_created": profiles_created,
        "already_present": already, "created": created, "updated": updated,
        "skipped_identity_conflict": skipped_identity_conflict,
        "weight_sum_anomalies": weight_sum_anomalies,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). ⚠️ `skipped_self`(ETF 가 자기 자신을 보유로 들고 온
        # 행 제외)와 `skipped_foreign_etf`(유니버스 뿌리 밖 ETF 의 행)는 **정상 동작이지 유실이
        # 아니다** — 유실로 세면 매 런 INCOMPLETE 가 된다. 다만 세어서 로그에는 남긴다:
        # 0 이 아닌 값은 "파티션에 대상 밖 ETF 가 있다"는 사실이라 조용히 버리면 안 된다.
        "ops": {
            "records_out": already + created + updated,
            "failed_records": (len(failures) + skipped_missing_identity + skipped_unknown_etf
                               + skipped_unknown_constituent + skipped_unknown_asset_type
                               + skipped_bad_fetched_at + skipped_bad_weight
                               + skipped_identity_conflict),
        },
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
