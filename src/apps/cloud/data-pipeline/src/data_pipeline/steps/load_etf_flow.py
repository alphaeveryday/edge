"""투자자 수급 적재 — canonical investor_flow → investor_flow_daily (ALPHA-385).

`canonical/market_data/investor_flow_daily/market=…/trade_date=…` 을 읽어 금융상품·거래일
grain 의 투자자별 순매수 원장을 Cloud Event Store 에 적재한다. 수집 → 정제(normalize_investor,
ALPHA-482) → 이 스텝이 체인의 끝이다. load_price_daily(377)와 같은 모델이다 — 표와 컬럼 수만
다르다(가격 3값 대신 투자자 13종 × 수량·대금 26값). 개별주식 단위라 FK 는 instrument 다(가격과
동일 — etf_profile 이 아니다).

**멱등**: PK `(instrument_id, trade_date)` 가 곧 멱등의 근거다 — 같은 값 재적재는
`ON CONFLICT … DO UPDATE … WHERE (…) IS DISTINCT FROM (…)` 의 WHERE 가 걸러내 아무 행도
반환하지 않고 already 로 세어진다. 벤더 정정(순매수 값이 실제로 바뀐 경우)만 UPDATE 로 흐른다 —
canonical 이 최신 fetched_at 으로 수렴시키므로 마트가 DO NOTHING 이면 두 계층이 영구 불일치한다.

**instrument_id 해소**: canonical 의 `(market, ticker)` → `instrument` 조회다. canonical 은
지역 "KR" 만 주고 MIC 는 없어, KRX 세 시장 MIC(XKRX·XKOS·XKON) 를 다 훑어 ticker 로 찾는다
(가격 로더와 같은 근거 — XKRX 만 보면 KOSDAQ/KONEX 종목이 조용히 unknown 으로 버려진다).
마스터에 없는 종목은 **적재하지 않고 센다** — FK 위반으로 배치를 죽이는 대신 사실을 수치로 남긴다.

**적재 게이트**: canonical 은 int64 타입이라 정상 경로에선 위반이 없지만, 게이트는 스스로 죽지
않아야 한다(load_price_daily 와 같은 근거, Rule 12):
  * headline(개인·외국인·기관계) 6컬럼이 NOT NULL 인데 결측이면 INSERT 가 NOT NULL 로 배치를
    죽인다 — 격리하고 센다(normalize 가 headline 필수를 보장하지만 손상 parquet 방어).
  * 순매수 값이 존재하는데 정수가 아니면(스키마 드리프트) 타입 오류로 배치가 죽는다 — 격리한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect
from ..lake import (
    Storage,
    canonical_investor_flow_partition,
    canonical_run_manifest_key,
    quality_log_key,
)

logger = logging.getLogger(__name__)

JOB_NAME = "load_etf_flow"
_PARTIAL_EXIT_CODE = 2
# 품질 로그 dataset. normalize_investor 도 canonical dataset 을 "investor_flow_daily" 로 쓰는데,
# SFN 은 모든 스텝에 같은 run_id 를 넘긴다 — 로그 키가 같으면 이 로더가 정제 로그를 덮어써
# records_failed·vendor_collisions 증거가 사라진다(load_price_daily GH-1 교훈). "_load" 로 분리.
DATASET = "investor_flow_load"

# 적재 대상 시장(레이크 지역 키) → 그 시장의 MIC(ISO 10383) 집합. canonical 은 지역 "KR" 만 주고
# MIC 는 없는데, KR 은 KRX 산하 세 시장이 서로 다른 MIC 를 쓴다(KOSPI=XKRX·KOSDAQ=XKOS·KONEX=
# XKON). XKRX 만 보면 KOSDAQ/KONEX 종목이 마스터에 있어도 unknown 으로 조용히 버려진다 — 세 MIC
# 를 다 본다(load_price_daily 와 같은 근거). US 는 instrument 마스터가 없어(ALPHA-371) 넣어도
# 전량 미등록으로 걸린다.
_MICS_BY_MARKET = {"KR": ("XKRX", "XKOS", "XKON")}

# 표준 투자자 구분 13종(정제 어휘 순서 그대로). headline 3종은 canonical 필수 → 테이블 NOT NULL.
# 나머지 10종은 선택 → nullable. 순서·이름은 normalize_investor._ALL_GROUPS 와 정확히 일치해야
# canonical 컬럼과 1:1 미러다.
_HEADLINE_CATEGORIES = ("individual", "foreign", "institution_total")
_SUB_CATEGORIES = (
    "financial_invest", "investment_trust", "pension", "private_fund", "bank",
    "insurance", "merchant_bank", "other_corp", "other_org", "other",
)
_CATEGORIES = (*_HEADLINE_CATEGORIES, *_SUB_CATEGORIES)
# 카테고리당 수량·대금 → 26 컬럼. canonical 병합 순서(name→qty,val)와 동일.
_NET_COLUMNS = tuple(f"net_{kind}_{c}" for c in _CATEGORIES for kind in ("qty", "val"))
_HEADLINE_COLUMNS = tuple(f"net_{kind}_{c}" for c in _HEADLINE_CATEGORIES for kind in ("qty", "val"))

_CREATED_SAMPLE_LIMIT = 5

# INSERT/UPSERT SQL 은 26 net 컬럼을 세 번(컬럼·SET·DISTINCT) 손으로 쓰면 어긋나기 쉬워
# 컬럼 튜플에서 생성한다. 정정만 UPDATE 하도록 DISTINCT 비교는 net 값 26컬럼만 본다
# (available_at·data_version 은 메타라 값 변화 판정에서 뺀다 — load_price_daily 와 같은 규약).
_ALL_INSERT_COLUMNS = ("instrument_id", "trade_date", *_NET_COLUMNS, "available_at", "data_version")
_UPSERT_SQL = (
    f"INSERT INTO investor_flow_daily ({', '.join(_ALL_INSERT_COLUMNS)})"
    f" VALUES ({', '.join(['%s'] * len(_ALL_INSERT_COLUMNS))})"
    " ON CONFLICT (instrument_id, trade_date) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in (*_NET_COLUMNS, "available_at", "data_version"))
    + " WHERE ("
    + ", ".join(f"investor_flow_daily.{c}" for c in _NET_COLUMNS)
    + ") IS DISTINCT FROM ("
    + ", ".join(f"EXCLUDED.{c}" for c in _NET_COLUMNS)
    + ") RETURNING (xmax <> 0) AS was_update"
)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical 투자자 수급 파티션의 trade_date 목록(정렬)."""
    marker = canonical_investor_flow_partition(market, "")  # ".../trade_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    return sorted(dates)


def _manifest_partitions(
    storage: Storage, input_run_id: str,
) -> list[tuple[str, str, str, str, frozenset[str]]]:
    """NormalizeInvestor가 승인한 직접 key와 현재 실행 winner 범위."""
    manifest_key = canonical_run_manifest_key("investor_flow_daily", input_run_id)
    manifest = json.loads(storage.get_bytes(manifest_key).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("run_id") != input_run_id:
        raise ValueError(f"요청한 run_id의 manifest가 아니다: run_id={input_run_id}")
    if (manifest.get("producer") != "normalize_investor"
            or manifest.get("canonical_written") is not True):
        raise ValueError(f"완료된 normalize-investor manifest가 아니다: run_id={input_run_id}")
    raw = manifest.get("canonical_partitions")
    if not isinstance(raw, list):
        raise ValueError(f"canonical_partitions가 없는 manifest다: run_id={input_run_id}")

    partitions: list[tuple[str, str, str, str, frozenset[str]]] = []
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
        if market not in _MICS_BY_MARKET or not valid_date:
            raise ValueError(f"canonical_partitions 항목이 유효하지 않다: {item!r}")
        partition = (market, trade_date)
        if partition in seen_partitions:
            raise ValueError(f"canonical_partitions 항목이 중복됐다: {item!r}")
        seen_partitions.add(partition)

        parquet_key = item.get("key")
        expected_key = f"{canonical_investor_flow_partition(market, trade_date)}/part-00000.parquet"
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
        partitions.append(
            (market, trade_date, parquet_key, parquet_sha256, frozenset(winner_ids))
        )
    return partitions


def _input_rows(
    storage: Storage,
    *,
    input_run_id: str | None,
    from_date: str | None,
    to_date: str | None,
) -> tuple[int, int, Iterator[tuple[str, str, dict, bool]]]:
    """manifest 파티션·winner 수와 물리 행·논리 선택 여부를 분리해 낸다."""
    if input_run_id is not None:
        partitions = _manifest_partitions(storage, input_run_id)

        def manifest_rows() -> Iterator[tuple[str, str, dict, bool]]:
            """승인된 direct parquet의 물리 행과 winner 선택 여부를 검증해 낸다."""
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
            len(partitions),
            sum(len(partition[4]) for partition in partitions),
            manifest_rows(),
        )

    def recovery_rows() -> Iterator[tuple[str, str, dict, bool]]:
        """명시된 기존 복구 범위의 canonical parquet 행을 낸다."""
        for market in _MICS_BY_MARKET:
            dates = [
                date for date in _partition_dates(storage, market)
                if (from_date is None or date >= from_date) and (to_date is None or date <= to_date)
            ]
            for trade_date in dates:
                prefix = canonical_investor_flow_partition(market, trade_date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        yield market, trade_date, row, True

    return 0, 0, recovery_rows()


def _instrument_ids(conn, mics: tuple[str, ...]) -> tuple[dict[str, str], set[str]]:
    """(그 지역 MIC 집합의) ticker → instrument_id, 그리고 MIC 를 가로질러 겹친 ticker 집합.

    ETF·개별주식 양쪽 다. `(market_code, ticker)` 는 유일 자연키지만 ticker **단독**은 MIC 를
    가로질러 겹칠 수 있다 — 겹치면 어느 시장 종목의 수급인지 알 수 없어, 조용히 하나 고르면
    오염이 되므로 겹친 ticker 를 드러내 해소에서 뺀다(load_price_daily 와 같은 근거, Rule 12).
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


def _is_int(value) -> bool:
    """정수인가(bool 제외). canonical int64 의 정상값만 True — 존재하는 비정수는 스키마 드리프트."""
    return isinstance(value, int) and not isinstance(value, bool)


def _load_violation(fact: dict) -> str | None:
    """적재 전 방어선(Rule 12). NOT NULL 헤드라인 결측 / 순매수 값 비정수면 사유, 아니면 None.

    canonical 은 headline 필수·int64 를 보장하지만, 손상 parquet·스키마 드리프트가 오면 INSERT
    가 NOT NULL·타입 오류로 배치 전체를 롤백시킨다 — 게이트가 그 행만 격리한다(게이트는 스스로
    예외로 죽지 않는다).
    """
    for col in _HEADLINE_COLUMNS:
        if fact[col] is None:
            return "missing_headline"
    for col in _NET_COLUMNS:
        v = fact[col]
        if v is not None and not _is_int(v):
            return "non_numeric"
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
    """canonical 투자자 수급 → investor_flow_daily 적재. 성공 0, 행 부분 실패 2, 장애 1.

    input_run_id는 정제 manifest의 직접 key와 winner만 처리한다. from/to는 명시 복구,
    셋 다 미지정은 호출자가 명시한 전체 스캔이다.
    """
    started_at = datetime.now(timezone.utc)
    physical_read = read = 0
    manifest_partitions = manifest_winners = 0
    skipped_missing_identity = skipped_unknown_instrument = skipped_load_violation = 0
    skipped_ambiguous_ticker = 0
    already = created = updated = 0
    created_sample: list[dict] = []
    unknown_instruments: set[str] = set()
    ambiguous_tickers: set[str] = set()
    load_violations: list[dict] = []
    failures: list[dict] = []
    exit_code = 0
    confirmed_data_version = input_run_id or run_id

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date와 to_date는 함께 써야 한다")
        # (market, ticker, trade_date) → 적재 후보. 같은 키가 여러 parquet 에 걸리면 최신
        # fetched_at 이 이긴다 — canonical 병합과 같은 규칙이다.
        candidates: dict[tuple[str, str, str], dict] = {}
        manifest_partitions, manifest_winners, input_rows = _input_rows(
            storage, input_run_id=input_run_id, from_date=from_date, to_date=to_date,
        )
        for market, partition_date, row, selected in input_rows:
            physical_read += 1
            if not selected:
                continue
            read += 1
            ticker, trade_date = row.get("ticker"), row.get("trade_date")
            if not (isinstance(ticker, str) and ticker.strip()
                    and isinstance(trade_date, str) and trade_date.strip()):
                # 정체성은 비어있지 않은 문자열이어야 한다. 비문자열·공백은 격리한다.
                skipped_missing_identity += 1
                continue
            if row.get("market") != market or trade_date != partition_date:
                # manifest 경로는 _input_rows가 fatal로 막고, 복구 경로도 다른 파티션 행을
                # 잘못된 종목에 붙이지 않는다.
                skipped_missing_identity += 1
                continue
            fetched_at = row.get("fetched_at")
            cand_key = (market, ticker, trade_date)
            prev = candidates.get(cand_key)
            if prev is not None and (fetched_at or "") < prev["fetched_at_raw"]:
                continue
            fact = {col: row.get(col) for col in _NET_COLUMNS}
            # 기존 명시 복구 동작을 보존한다. 결측 시 실행 시각을 쓰는 역사적 계약이다.
            fact["available_at"] = fetched_at or started_at.isoformat()
            fact["fetched_at_raw"] = fetched_at or ""
            candidates[cand_key] = fact

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
                violation = _load_violation(fact)
                if violation is not None:
                    # NOT NULL/타입 위반 행은 격리 — 넣으면 배치가 죽는다(Rule 12).
                    skipped_load_violation += 1
                    load_violations.append({
                        "market": market, "ticker": ticker,
                        "trade_date": trade_date, "reason": violation,
                    })
                    continue
                params = [instrument_id, trade_date]
                params += [fact[col] for col in _NET_COLUMNS]
                params += [fact["available_at"], confirmed_data_version]
                with conn.cursor() as cur:
                    cur.execute("SAVEPOINT investor_flow_row")
                    try:
                        cur.execute(_UPSERT_SQL, params)
                        row = cur.fetchone()
                        if row is None:
                            # 값이 같은 재확정 winner도 현재 manifest를 성공 소비한 범위다.
                            # data_version을 그대로 두면 이전 run의 잔존 행과 구분할 수 없다.
                            cur.execute(
                                "UPDATE investor_flow_daily SET data_version = %s"
                                " WHERE instrument_id = %s AND trade_date = %s",
                                (confirmed_data_version, instrument_id, trade_date),
                            )
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT investor_flow_row")
                        cur.execute("RELEASE SAVEPOINT investor_flow_row")
                        failures.append({
                            "market": market, "ticker": ticker, "trade_date": trade_date,
                            "reasons": ["row_load_error"], "error": str(exc),
                        })
                        exit_code = _PARTIAL_EXIT_CODE
                        continue
                    cur.execute("RELEASE SAVEPOINT investor_flow_row")
                    if row is None:
                        # 값은 같아도 위 savepoint 안에서 현재 manifest 계보를 stamp했다.
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
                        "trade_date": trade_date,
                        "net_qty_individual": fact["net_qty_individual"],
                    })
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("투자자 수급 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        already, created, updated, created_sample = 0, 0, 0, []
        exit_code = 1

    if exit_code == 0 and (
        skipped_missing_identity + skipped_unknown_instrument
        + skipped_ambiguous_ticker + skipped_load_violation
    ):
        exit_code = _PARTIAL_EXIT_CODE

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "markets": list(_MICS_BY_MARKET), "input_run_id": input_run_id,
        "from_date": from_date, "to_date": to_date,
        "manifest_partitions": manifest_partitions, "manifest_winners": manifest_winners,
        "physical_rows_read": physical_read, "logical_rows_read": read, "rows_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_unknown_instrument": skipped_unknown_instrument,
        # 마스터가 모르는 종목 목록 — instrument 마스터 확장이 얼마나 필요한지의 근거다.
        "unknown_instruments": sorted(unknown_instruments),
        "skipped_ambiguous_ticker": skipped_ambiguous_ticker,
        "ambiguous_tickers": sorted(ambiguous_tickers),
        "skipped_load_violation": skipped_load_violation,
        "load_violations": load_violations,
        "already_present": already, "created": created, "updated": updated,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
        # 원장 관측용 공통 봉투(ALPHA-181). 적재 탈락 4종 전부 in-band 유실이다.
        "ops": {
            "records_out": already + created + updated,
            "failed_records": (len(failures) + skipped_missing_identity
                               + skipped_unknown_instrument + skipped_ambiguous_ticker
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
        "load_etf_flow 완료: read=%d created=%d updated=%d already=%d "
        "unknown_instrument=%d(%d종) ambiguous=%d load_violation=%d skipped_identity=%d",
        read, created, updated, already,
        skipped_unknown_instrument, len(unknown_instruments),
        skipped_ambiguous_ticker, skipped_load_violation, skipped_missing_identity,
    )
    return exit_code
