"""가격 적재 — canonical 가격 → price_daily (ALPHA-377).

정상 경로는 NormalizePrice manifest가 지목한 KR direct parquet와 현재 winner만 읽어
금융상품·거래일 grain의 일별 가격 원장을 Cloud Event Store에 적재한다. manifest 결손·손상은
canonical 전체 스캔으로 넓히지 않고 실패한다. 날짜창·전체 스캔은 명시 복구 경로에만 남긴다.
수집 → 정제(normalize_price) → 이 스텝이 체인의 끝이다.

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

import hashlib
import json
import logging
import math
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect
from ..lake import (
    Storage,
    canonical_price_daily_partition,
    canonical_run_manifest_key,
    quality_log_key,
)

logger = logging.getLogger(__name__)

JOB_NAME = "load_price_daily"
_PARTIAL_EXIT_CODE = 2
# 품질 로그 dataset. normalize_price 도 canonical dataset 을 "price_daily" 로 쓰는데, SFN 은
# 모든 스텝에 같은 run_id 를 넘긴다(statemachine.tf `$.run_id`). 로그 키가 같으면 normalize→load
# 순서에서 이 로더가 정제 로그를 덮어써 records_failed·vendor_collisions 증거가 사라진다
# (Codex 지적). load_etf_nav 는 마트 테이블명("etf_nav_daily")이 canonical dataset("etf_nav")과
# 달라 자연히 갈렸지만, 가격은 둘 다 "price_daily" 라 명시적으로 "_load" 로 분리한다.
DATASET = "price_daily_load"

# 적재 대상 시장(레이크 지역 키) → 그 시장의 MIC(ISO 10383) 집합. canonical 가격은 지역 "KR"
# 만 주고 MIC 는 없는데, KR 은 KRX 산하 세 시장이 서로 다른 MIC 를 쓴다(KOSPI=XKRX·KOSDAQ=XKOS·
# KONEX=XKON). XKRX 만 보면 load_instruments 가 constituent_mic 로 XKOS·XKON 에 적재한 KOSDAQ/
# KONEX 종목이 마스터에 있어도 unknown 으로 조용히 버려진다(Codex P1 지적) — 세 MIC 를 다 본다.
# US 는 instrument 마스터가 없어(ALPHA-371) 넣어도 전량 미등록으로 걸린다.
_MICS_BY_MARKET = {"KR": ("XKRX", "XKOS", "XKON")}

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


def _manifest_partitions(
    storage: Storage, input_run_id: str,
) -> tuple[int, int, list[tuple[str, str, str, str, frozenset[str]]]]:
    """NormalizePrice가 승인한 전체 파티션·winner 수와 KR 직접 key/winner 범위."""
    manifest_key = canonical_run_manifest_key("price_daily", input_run_id)
    manifest = json.loads(storage.get_bytes(manifest_key).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("run_id") != input_run_id:
        raise ValueError(f"요청한 run_id의 manifest가 아니다: run_id={input_run_id}")
    if (manifest.get("producer") != "normalize_price"
            or manifest.get("canonical_written") is not True):
        raise ValueError(f"완료된 normalize-price manifest가 아니다: run_id={input_run_id}")
    raw = manifest.get("canonical_partitions")
    if not isinstance(raw, list):
        raise ValueError(f"canonical_partitions가 없는 manifest다: run_id={input_run_id}")

    selected: list[tuple[str, str, str, str, frozenset[str]]] = []
    winner_total = 0
    seen_partitions: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"canonical_partitions 항목이 객체가 아니다: run_id={input_run_id}")
        market, trade_date = item.get("market"), item.get("trade_date")
        try:
            valid_date = (
                isinstance(trade_date, str)
                and datetime.strptime(trade_date, "%Y-%m-%d").strftime("%Y-%m-%d") == trade_date
            )
        except ValueError:
            valid_date = False
        if market not in {"KR", "US"} or not valid_date:
            raise ValueError(f"canonical_partitions 항목이 유효하지 않다: {item!r}")
        partition = (market, trade_date)
        if partition in seen_partitions:
            raise ValueError(f"canonical_partitions 항목이 중복됐다: {item!r}")
        seen_partitions.add(partition)

        parquet_key = item.get("key")
        expected_key = f"{canonical_price_daily_partition(market, trade_date)}/part-00000.parquet"
        if parquet_key != expected_key:
            raise ValueError(f"canonical 직접 키가 파티션과 일치하지 않는다: {item!r}")
        parquet_sha256 = item.get("sha256")
        if not (
            isinstance(parquet_sha256, str)
            and len(parquet_sha256) == 64
            and all(char in "0123456789abcdef" for char in parquet_sha256)
        ):
            raise ValueError(f"canonical sha256이 유효하지 않다: {item!r}")
        raw_winners = item.get("winner_ids")
        if not isinstance(raw_winners, list) or not raw_winners:
            raise ValueError(f"winner_ids가 없는 manifest 파티션이다: {item!r}")
        winner_ids: list[str] = []
        for winner in raw_winners:
            if not isinstance(winner, dict) or set(winner) != {"ticker"}:
                raise ValueError(f"winner_ids 항목이 유효하지 않다: {item!r}")
            ticker = winner.get("ticker")
            if not (isinstance(ticker, str) and ticker.strip() and ticker == ticker.strip()):
                raise ValueError(f"winner_ids 항목이 유효하지 않다: {item!r}")
            winner_ids.append(ticker)
        if winner_ids != sorted(set(winner_ids)):
            raise ValueError(f"winner_ids가 정렬·고유 목록이 아니다: {item!r}")
        winner_total += len(winner_ids)
        # producer는 KR·US를 모두 싣지만 이 loader의 현재 DB 지원 범위는 KR뿐이다.
        if market in _MICS_BY_MARKET:
            selected.append(
                (market, trade_date, parquet_key, parquet_sha256, frozenset(winner_ids))
            )
    return len(raw), winner_total, selected


def _input_rows(
    storage: Storage,
    *,
    input_run_id: str | None,
    from_date: str | None,
    to_date: str | None,
) -> tuple[int, int, int, int, Iterator[tuple[str, str, dict, bool]]]:
    """manifest 파티션·winner 전체/선택 건수와 물리 행·논리 선택 여부."""
    if input_run_id is not None:
        partition_total, winner_total, partitions = _manifest_partitions(storage, input_run_id)

        def manifest_rows() -> Iterator[tuple[str, str, dict, bool]]:
            """선택된 KR direct parquet의 물리 행과 winner 여부를 검증해 낸다."""
            for market, trade_date, key, expected_sha256, winner_ids in partitions:
                found: set[str] = set()
                parquet_bytes = storage.get_bytes(key)
                if hashlib.sha256(parquet_bytes).hexdigest() != expected_sha256:
                    raise ValueError(
                        f"canonical 바이트가 manifest 이후 바뀌었다: key={key}, "
                        f"run_id={input_run_id}"
                    )
                for row in _read_parquet_rows(parquet_bytes):
                    ticker = row.get("ticker")
                    selected = ticker in winner_ids
                    if selected:
                        if ticker in found:
                            raise ValueError(
                                f"manifest winner가 canonical에 중복됐다: key={key}, winner={ticker!r}"
                            )
                        if row.get("market") != market or row.get("trade_date") != trade_date:
                            raise ValueError(
                                f"manifest winner의 canonical 파티션 정체성이 다르다: "
                                f"key={key}, winner={ticker!r}"
                            )
                        found.add(ticker)
                    yield market, trade_date, row, selected
                missing = winner_ids - found
                if missing:
                    raise ValueError(
                        f"manifest winner가 canonical에 없다: key={key}, winners={sorted(missing)!r}"
                    )

        return (
            partition_total,
            len(partitions),
            winner_total,
            sum(len(partition[4]) for partition in partitions),
            manifest_rows(),
        )

    def recovery_rows() -> Iterator[tuple[str, str, dict, bool]]:
        """명시 복구 범위의 KR canonical parquet 행을 낸다."""
        for market in _MICS_BY_MARKET:
            dates = [
                date for date in _partition_dates(storage, market)
                if (from_date is None or date >= from_date) and (to_date is None or date <= to_date)
            ]
            for trade_date in dates:
                prefix = canonical_price_daily_partition(market, trade_date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        yield market, trade_date, row, True

    return 0, 0, 0, 0, recovery_rows()


def _instrument_ids(conn, mics: tuple[str, ...]) -> tuple[dict[str, str], set[str]]:
    """(그 지역 MIC 집합의) ticker → instrument_id, 그리고 MIC 를 가로질러 겹친 ticker 집합.

    ETF·개별주식 양쪽 다(NAV 로더와 달리 instrument_type 을 안 건다 — 가격은 둘 다 있다).
    `(market_code, ticker)` 는 유일 자연키지만 ticker **단독**은 MIC 를 가로질러 겹칠 수 있다.
    KRX 종목코드는 세 시장에 걸쳐 유일해 실제로는 안 겹치지만, 겹치면 어느 시장 종목의
    가격인지 알 수 없다 — 조용히 하나 고르면 KOSPI 가격을 동명 KOSDAQ 종목에 붙이는 오염이
    되므로, 겹친 ticker 를 드러내 해소에서 뺀다(Rule 12).
    """
    ids: dict[str, str] = {}
    ambiguous: set[str] = set()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument WHERE market_code = ANY(%s)",
            (list(mics),),
        )
        for ticker, instrument_id in cur.fetchall():
            t = str(ticker)
            if t in ids and ids[t] != str(instrument_id):
                ambiguous.add(t)
            ids[t] = str(instrument_id)
    return ids, ambiguous


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
    input_run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 가격 → price_daily 적재. 성공 0, 행 부분 실패 2, 장애 1.

    input_run_id는 정제 manifest의 KR direct key와 winner만 처리한다. from/to는 명시 복구,
    셋 다 미지정은 호출자가 명시한 전체 스캔이다.
    """
    started_at = datetime.now(timezone.utc)
    physical_read = read = 0
    manifest_partitions_total = manifest_partitions_selected = 0
    manifest_winners_total = manifest_winners_selected = 0
    skipped_missing_identity = skipped_unknown_instrument = skipped_check_violation = 0
    skipped_ambiguous_ticker = 0
    already = created = updated = 0
    created_sample: list[dict] = []
    unknown_instruments: set[str] = set()
    ambiguous_tickers: set[str] = set()
    check_violations: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date와 to_date는 함께 써야 한다")
        # (market, ticker, trade_date) → 적재 후보. 같은 키가 여러 parquet 에 걸리면 최신
        # fetched_at 이 이긴다 — canonical 병합(_merge_partition)과 같은 규칙이다.
        candidates: dict[tuple[str, str, str], dict] = {}
        (
            manifest_partitions_total,
            manifest_partitions_selected,
            manifest_winners_total,
            manifest_winners_selected,
            input_rows,
        ) = _input_rows(storage, input_run_id=input_run_id,
                        from_date=from_date, to_date=to_date)
        for market, partition_date, row, selected in input_rows:
            physical_read += 1
            if not selected:
                continue
            read += 1
            ticker, trade_date = row.get("ticker"), row.get("trade_date")
            if not ticker or not trade_date:
                # canonical 게이트가 이미 거른 조합이라 여기 오면 안 되는 행이다.
                # 그래도 세고 뺀다 — 정체성 없는 행은 키를 만들 수 없다(Rule 12).
                skipped_missing_identity += 1
                continue
            if row.get("market") != market or trade_date != partition_date:
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
                market: _instrument_ids(conn, mics) for market, mics in _MICS_BY_MARKET.items()
            }
            for (market, ticker, trade_date), fact in sorted(candidates.items()):
                ids, ambiguous = instruments[market]
                if ticker in ambiguous:
                    # 같은 ticker 가 두 MIC 에 걸쳐 존재 — 어느 시장 종목인지 알 수 없어
                    # 조용히 붙이면 오염이다(Rule 12). 적재하지 않고 드러낸다.
                    skipped_ambiguous_ticker += 1
                    ambiguous_tickers.add(f"{market}:{ticker}")
                    continue
                instrument_id = ids.get(ticker)
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
                    cur.execute("SAVEPOINT price_daily_row")
                    try:
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
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT price_daily_row")
                        cur.execute("RELEASE SAVEPOINT price_daily_row")
                        failures.append({
                            "market": market, "ticker": ticker, "trade_date": trade_date,
                            "reasons": ["row_load_error"], "error": str(exc),
                        })
                        exit_code = _PARTIAL_EXIT_CODE
                        continue
                    cur.execute("RELEASE SAVEPOINT price_daily_row")
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
        already, created, updated, created_sample = 0, 0, 0, []
        exit_code = 1

    if exit_code == 0 and (
        skipped_missing_identity + skipped_unknown_instrument
        + skipped_ambiguous_ticker + skipped_check_violation
    ):
        exit_code = _PARTIAL_EXIT_CODE

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "markets": list(_MICS_BY_MARKET), "input_run_id": input_run_id,
        "from_date": from_date, "to_date": to_date,
        "manifest_partitions_total": manifest_partitions_total,
        "manifest_partitions_selected": manifest_partitions_selected,
        "manifest_winners_total": manifest_winners_total,
        "manifest_winners_selected": manifest_winners_selected,
        "physical_rows_read": physical_read, "logical_rows_read": read, "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_instrument": skipped_unknown_instrument,
        # 마스터가 모르는 종목 목록 — instrument 마스터 확장이 얼마나 필요한지의 근거다.
        "unknown_instruments": sorted(unknown_instruments),
        "skipped_ambiguous_ticker": skipped_ambiguous_ticker,
        "ambiguous_tickers": sorted(ambiguous_tickers),
        "skipped_check_violation": skipped_check_violation,
        "check_violations": check_violations,
        "already_present": already, "created": created, "updated": updated,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). 적재 탈락은 전부 in-band 유실이다 — 정체성 결측·
        # 미등록 종목·모호 ticker·CHECK 위반 모두 실제 입력 행이 DB 에 안 들어간 것이라 failed 로
        # 센다. 안 세면 부분 유실이 VALID 로 위장된다(edge-review G/H). 어느 skipped 버킷이
        # 유실인지는 이 스텝만 아니까 판정이 여기 산다.
        "ops": {
            "records_out": already + created + updated,
            "failed_records": (len(failures) + skipped_check_violation
                               + skipped_unknown_instrument + skipped_ambiguous_ticker
                               + skipped_missing_identity),
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_price_daily 완료: read=%d created=%d updated=%d already=%d "
        "unknown_instrument=%d(%d종) ambiguous=%d check_violation=%d skipped_identity=%d",
        read, created, updated, already,
        skipped_unknown_instrument, len(unknown_instruments),
        skipped_ambiguous_ticker, skipped_check_violation, skipped_missing_identity,
    )
    return exit_code
