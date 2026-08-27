"""장중 투자자 추정 적재 — canonical investor_flow_intraday → 동명 테이블 (ALPHA-768).

정상 경로는 `normalize_investor_estimate`가 같은 run_id로 확정한 manifest의 직접 parquet key와
winner만 읽어 금융상품·거래일·**슬롯** grain의 장중 추정 순매수를 Cloud Event Store에 적재한다.
manifest가 없거나 손상되면 canonical 전체 스캔으로 넓히지 않고 실패한다. 수집(ALPHA-767) →
정제(normalize_investor_estimate) → 이 스텝이 체인의 끝이다.

EOD 적재(`load_etf_flow`, ALPHA-385)와 같은 모델이라 instrument 해소·MIC 규약·게이트 헬퍼를
그대로 import 한다. **다른 것은 키 축 하나다** — 정체성에 `asof_slot` 이 붙어 하루 4~5 슬롯이
한 종목·한 날짜에 공존한다. 그래서 후보 dict 키·UPSERT 충돌 대상·창 프루닝을 새로 쓴다.

**멱등**: PK `(instrument_id, trade_date, asof_slot)` 이 곧 멱등의 근거다 — 같은 값 재적재는
`ON CONFLICT … DO UPDATE … WHERE (…) IS DISTINCT FROM (…)` 의 WHERE 가 걸러 already 로 세어진다.
벤더가 가집계를 고친 경우(값이 실제로 바뀐 경우)만 UPDATE 로 흐른다 — canonical 이 최신
fetched_at 으로 수렴시키므로 마트가 DO NOTHING 이면 두 계층이 영구 불일치한다.
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
    canonical_investor_flow_intraday_partition,
    canonical_run_manifest_key,
    quality_log_key,
)
from .load_etf_flow import _MICS_BY_MARKET, _instrument_ids, _is_int, _read_parquet_rows
from .normalize_investor import _fetched_at

logger = logging.getLogger(__name__)

JOB_NAME = "load_investor_intraday"
_PARTIAL_EXIT_CODE = 2
# 품질 로그 dataset. normalize_investor_estimate 가 canonical dataset 을 "investor_flow_intraday"
# 로 쓰는데 SFN 은 모든 스텝에 같은 run_id 를 넘긴다 — 로그 키가 같으면 이 로더가 정제 로그를
# 덮어써 records_failed 증거가 사라진다(load_etf_flow 와 같은 근거). "_load" 로 분리.
DATASET = "investor_flow_intraday_load"

# canonical 수량 3종(정제 어휘 순서 그대로). 전부 NOT NULL — 벤더가 주는 전부라 하나라도
# 없으면 그 행에 남는 관측이 없다. 순서·이름은 normalize_investor_estimate._QTY_FIELDS 와 일치.
_NET_COLUMNS = ("net_qty_foreign_est", "net_qty_institution_est", "net_qty_total_est")

_CREATED_SAMPLE_LIMIT = 5

# 테이블이 BIGINT 라 이 범위 밖 값은 INSERT 가 죽는다(정제의 int64 게이트와 같은 경계).
_BIGINT_MIN, _BIGINT_MAX = -(2**63), 2**63 - 1

_ALL_INSERT_COLUMNS = (
    "instrument_id", "trade_date", "asof_slot", *_NET_COLUMNS, "available_at", "data_version",
)
# 정정만 UPDATE 하도록 DISTINCT 비교는 수량 3컬럼만 본다(available_at·data_version 은 메타라
# 값 변화 판정에서 뺀다 — load_etf_flow 와 같은 규약).
_UPSERT_SQL = (
    f"INSERT INTO investor_flow_intraday ({', '.join(_ALL_INSERT_COLUMNS)})"
    f" VALUES ({', '.join(['%s'] * len(_ALL_INSERT_COLUMNS))})"
    " ON CONFLICT (instrument_id, trade_date, asof_slot) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in (*_NET_COLUMNS, "available_at", "data_version"))
    + " WHERE ("
    + ", ".join(f"investor_flow_intraday.{c}" for c in _NET_COLUMNS)
    + ") IS DISTINCT FROM ("
    + ", ".join(f"EXCLUDED.{c}" for c in _NET_COLUMNS)
    + ") RETURNING (xmax <> 0) AS was_update"
)


def _partition_dates(storage: Storage, market: str) -> list[str]:
    """canonical 장중 추정 파티션의 trade_date 목록(정렬)."""
    marker = canonical_investor_flow_intraday_partition(market, "")  # ".../trade_date="
    dates = {key[len(marker):].split("/", 1)[0] for key in storage.list_keys(marker)}
    dates.discard("")
    return sorted(dates)


def _manifest_partitions(
    storage: Storage, input_run_id: str,
) -> list[tuple[str, str, str, str, frozenset[tuple[str, str]]]]:
    """NormalizeInvestorEstimate가 승인한 직접 key와 현재 실행 winner 범위."""
    manifest_key = canonical_run_manifest_key("investor_flow_intraday", input_run_id)
    manifest = json.loads(storage.get_bytes(manifest_key).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("run_id") != input_run_id:
        raise ValueError(f"요청한 run_id의 manifest가 아니다: run_id={input_run_id}")
    if (manifest.get("producer") != "normalize_investor_estimate"
            or manifest.get("canonical_written") is not True):
        raise ValueError(f"완료된 normalize-investor-estimate manifest가 아니다: run_id={input_run_id}")
    raw = manifest.get("canonical_partitions")
    if not isinstance(raw, list):
        raise ValueError(f"canonical_partitions가 없는 manifest다: run_id={input_run_id}")

    partitions: list[tuple[str, str, str, str, frozenset[tuple[str, str]]]] = []
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
        expected_key = (
            f"{canonical_investor_flow_intraday_partition(market, trade_date)}"
            "/part-00000.parquet"
        )
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
        winner_ids: list[tuple[str, str]] = []
        for winner in raw_winners:
            if not isinstance(winner, dict) or set(winner) != {"ticker", "asof_slot"}:
                raise ValueError(f"winner_ids 항목이 유효하지 않다: {item!r}")
            ticker, asof_slot = winner.get("ticker"), winner.get("asof_slot")
            if not all(
                isinstance(value, str) and value.strip() and value == value.strip()
                for value in (ticker, asof_slot)
            ):
                raise ValueError(f"winner_ids 항목이 유효하지 않다: {item!r}")
            winner_ids.append((ticker, asof_slot))
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
) -> Iterator[tuple[str, str, dict, bool]]:
    """물리로 읽은 행과 현재 manifest의 논리 범위 여부를 함께 낸다."""
    if input_run_id is not None:
        for market, trade_date, key, expected_sha256, winner_ids in _manifest_partitions(
            storage, input_run_id,
        ):
            found: set[tuple[str, str]] = set()
            parquet_bytes = storage.get_bytes(key)
            if hashlib.sha256(parquet_bytes).hexdigest() != expected_sha256:
                raise ValueError(
                    f"canonical 바이트가 manifest 이후 바뀌었다: key={key}, "
                    f"run_id={input_run_id}"
                )
            for row in _read_parquet_rows(parquet_bytes):
                winner_id = (row.get("ticker"), row.get("asof_slot"))
                selected = winner_id in winner_ids
                if selected:
                    if winner_id in found:
                        raise ValueError(
                            f"manifest winner가 canonical에 중복됐다: key={key}, winner={winner_id!r}"
                        )
                    if row.get("market") != market or row.get("trade_date") != trade_date:
                        raise ValueError(
                            f"manifest winner의 canonical 파티션 정체성이 다르다: "
                            f"key={key}, winner={winner_id!r}"
                        )
                    found.add(winner_id)
                yield market, trade_date, row, selected
            missing = winner_ids - found
            if missing:
                raise ValueError(
                    f"manifest winner가 canonical에 없다: key={key}, winners={sorted(missing)!r}"
                )
        return

    for market in _MICS_BY_MARKET:
        dates = [
            date for date in _partition_dates(storage, market)
            if (from_date is None or date >= from_date) and (to_date is None or date <= to_date)
        ]
        for trade_date in dates:
            prefix = canonical_investor_flow_intraday_partition(market, trade_date)
            for key in storage.list_keys(prefix + "/"):
                if not key.endswith(".parquet"):
                    continue
                for row in _read_parquet_rows(storage.get_bytes(key)):
                    yield market, trade_date, row, True


def _load_violation(fact: dict, trade_date: str) -> str | None:
    """적재 전 방어선(Rule 12). 위반이면 사유, 아니면 None.

    canonical 은 수량 셋의 int64·거래일 형식·`fetched_at` 파싱 가능을 모두 보장하지만, 손상
    parquet·스키마 드리프트가 오면 INSERT 가 NOT NULL·타입·날짜 변환 오류로 **배치 전체를
    롤백**시킨다 — 게이트가 그 행만 격리한다(게이트는 스스로 예외로 죽지 않는다).

    형제 로더(`load_etf_flow`)보다 검사가 둘 많다(거래일 형식·available_at). 그쪽은 EOD 경로의
    기록값을 움직이지 않으려고 손대지 않았고, 여기선 같은 유형이 canonical 을 뚫고 오면 DATE·
    TIMESTAMPTZ 변환 오류로 정상 행까지 함께 죽으므로 새 코드에 방어선을 둔다(Rule 3).
    """
    for col in _NET_COLUMNS:
        v = fact[col]
        if v is None:
            return "missing_headline"
        if not _is_int(v):
            return "non_numeric"
        if not (_BIGINT_MIN <= v <= _BIGINT_MAX):
            # 파이썬 int 는 무한정이라 `_is_int` 를 통과한다 — BIGINT 를 넘으면 INSERT 가
            # 범위 오류로 죽어 정상 행까지 든 트랜잭션이 롤백된다(정제도 같은 이유로 막는다).
            return "out_of_range"
    if not isinstance(fact["available_at"], str):
        # `str()` 로 감싸 검사하면 정수 20260805 가 fromisoformat 을 통과한 뒤 **원래 정수**가
        # TIMESTAMPTZ 에 바인딩된다 — 검사한 값과 넣는 값이 달라지는 형태다.
        return "bad_available_at"
    try:
        parsed = datetime.strptime(trade_date, "%Y-%m-%d")
    except ValueError:
        return "bad_trade_date"
    if parsed.strftime("%Y-%m-%d") != trade_date:
        # zero-pad 왕복까지 본다 — `"2026-8-5"` 는 strptime 을 통과하지만 canonical 에선
        # `"2026-08-05"` 와 **다른 후보 키**이면서 PostgreSQL DATE 로는 같은 값이다. 둘 다
        # 적재되면 뒤에 온 불량 행이 최신성 판정을 우회해 정상 값을 덮는다.
        return "bad_trade_date"
    try:
        available_at = datetime.fromisoformat(fact["available_at"])
    except ValueError:
        return "bad_available_at"
    if available_at.tzinfo is None or available_at.utcoffset() is None:
        # 날짜만·naive datetime도 fromisoformat은 받지만 '언제 이용 가능했는지'가 정해지지
        # 않는다. PostgreSQL 세션 timezone에 따라 다른 순간으로 저장되면 PIT가 깨진다.
        return "bad_available_at"
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
    """canonical 장중 추정 → investor_flow_intraday 적재. 성공 0, 장애 시 비0.

    input_run_id는 정제 manifest의 직접 key와 winner만 처리한다. from/to는 명시 복구 범위이고,
    셋 다 미지정은 호출자가 명시적으로 선택한 전체 스캔이다. 서로 섞으면 거부한다.
    """
    started_at = datetime.now(timezone.utc)
    physical_read = read = 0
    skipped_missing_identity = skipped_unknown_instrument = skipped_load_violation = 0
    skipped_ambiguous_ticker = 0
    already = created = updated = 0
    created_sample: list[dict] = []
    unknown_instruments: set[str] = set()
    ambiguous_tickers: set[str] = set()
    load_violations: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        if input_run_id is not None and (from_date is not None or to_date is not None):
            raise ValueError("input_run_id와 from/to는 함께 쓸 수 없다")
        # (market, ticker, trade_date, asof_slot) → 적재 후보. ⚠️ 슬롯이 키에 없으면 하루의
        # 마지막 슬롯이 앞 슬롯을 덮어 장중 추이가 사라진다. 같은 키가 여러 parquet 에 걸리면
        # 최신 fetched_at 이 이긴다 — canonical 병합과 같은 규칙이다.
        candidates: dict[tuple[str, str, str, str], dict] = {}
        for market, partition_date, row, selected in _input_rows(
            storage, input_run_id=input_run_id, from_date=from_date, to_date=to_date,
        ):
            physical_read += 1
            if not selected:
                continue
            read += 1
            ticker, trade_date = row.get("ticker"), row.get("trade_date")
            asof_slot = row.get("asof_slot")
            if not all(isinstance(v, str) and v.strip()
                       for v in (ticker, trade_date, asof_slot)):
                # 정체성 3축은 비어있지 않은 문자열이어야 한다. 비문자열(드리프트로
                # 실린 int slot 등)·공백을 통과시키면 아래 sorted(candidates) 에서
                # str/int 비교가 TypeError 를 내 바깥 try 가 load_error 로 잡아 **정상
                # 행까지 전체 롤백**한다 — 게이트가 스스로 죽는다(Rule 12).
                skipped_missing_identity += 1
                continue
            if row.get("market") != market or trade_date != partition_date:
                # manifest 정상 경로는 _input_rows가 날짜까지 hard-fail한다. 복구 스캔에서도
                # 시장이 파티션과 어긋난 손상 행을 다른 MIC 종목에 붙이지 않는다.
                skipped_missing_identity += 1
                continue
            fetched_at = row.get("fetched_at")
            cand_key = (market, ticker, trade_date, asof_slot)
            prev = candidates.get(cand_key)
            # '최신 우선'은 **실제 시각**으로 가른다(정제의 병합과 같은 규칙).
            # 문자열 비교는 오프셋이 다르면 어긋나 — '…T10:00+09:00'(01:00Z)이
            # '…T02:00+00:00'보다 크게 읽혀 더 오래된 추정치가 DB 에 남는다.
            if prev is not None and _fetched_at(row) < _fetched_at(prev):
                continue
            fact = {col: row.get(col) for col in _NET_COLUMNS}
            # available_at = '우리가 이 관측을 쓸 수 있게 된 시각'. 수집 시각이 가장
            # 보수적인 근사다(load_etf_flow 와 같은 규약). ⚠️ 슬롯 시각이 아니다 —
            # asof_slot 은 도메인 미관측이라 시각으로 해석하지 않는다.
            # 결측은 실행 시각으로 대체하지 않고 아래 게이트에서 드러낸다.
            fact["available_at"] = fetched_at
            fact["fetched_at"] = fetched_at
            candidates[cand_key] = fact

        with connect(db) as conn:
            instruments = {
                market: _instrument_ids(conn, mics) for market, mics in _MICS_BY_MARKET.items()
            }
            for (market, ticker, trade_date, asof_slot), fact in sorted(candidates.items()):
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
                violation = _load_violation(fact, trade_date)
                if violation is not None:
                    skipped_load_violation += 1
                    load_violations.append({
                        "market": market, "ticker": ticker, "trade_date": trade_date,
                        "asof_slot": asof_slot, "reason": violation,
                    })
                    continue
                params = [instrument_id, trade_date, asof_slot]
                params += [fact[col] for col in _NET_COLUMNS]
                params += [fact["available_at"], run_id]
                with conn.cursor() as cur:
                    cur.execute("SAVEPOINT investor_intraday_row")
                    try:
                        cur.execute(_UPSERT_SQL, params)
                        result = cur.fetchone()
                    except Exception as exc:
                        # 한 종목의 DB 제약/값 오류가 다른 winner의 성공을 롤백하지 않도록 이
                        # 행만 되돌린다. 연결 자체가 죽어 ROLLBACK이 실패하면 바깥 hard failure다.
                        cur.execute("ROLLBACK TO SAVEPOINT investor_intraday_row")
                        cur.execute("RELEASE SAVEPOINT investor_intraday_row")
                        failures.append({
                            "market": market, "ticker": ticker, "trade_date": trade_date,
                            "asof_slot": asof_slot, "reasons": ["row_load_error"],
                            "error": str(exc),
                        })
                        exit_code = _PARTIAL_EXIT_CODE
                        continue
                    cur.execute("RELEASE SAVEPOINT investor_intraday_row")
                    if result is None:
                        # 값이 같아 UPDATE 조건이 걸러낸 경우 — 재실행의 정상 경로다.
                        already += 1
                        continue
                    if result[0]:
                        # xmax<>0 = 기존 행을 갱신했다(벤더 가집계 정정이 마트까지 흘렀다).
                        updated += 1
                        continue
                created += 1
                if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                    created_sample.append({
                        "instrument_id": instrument_id, "ticker": ticker,
                        "trade_date": trade_date, "asof_slot": asof_slot,
                        "net_qty_total_est": fact["net_qty_total_est"],
                    })
    except Exception as exc:
        # 연결·manifest·canonical 범위 오류는 hard failure다. DB 행 오류만 위 savepoint에서
        # 격리하고, 이 경로는 트랜잭션 전체가 롤백됐다고 보수적으로 기록한다.
        logger.exception("장중 투자자 추정 적재 실패(롤백)")
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
        "load_investor_intraday 완료: read=%d created=%d updated=%d already=%d "
        "unknown_instrument=%d(%d종) ambiguous=%d load_violation=%d skipped_identity=%d",
        read, created, updated, already,
        skipped_unknown_instrument, len(unknown_instruments),
        skipped_ambiguous_ticker, skipped_load_violation, skipped_missing_identity,
    )
    return exit_code
