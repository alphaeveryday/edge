"""DataGuide 일봉 이력 → price_daily 일회성 적재 (ALPHA-1148).

DB `price_daily` 의 이력은 2022-11 부터이고 2025-07-16 이전은 5분봉을 집산한 값이라 종가
일치율이 88~92% 다. 레이크 임시 존에 DataGuide 일봉 스냅샷(1980~, 시가·고가·저가·종가·
수정주가·거래량)이 이미 있으므로 그것을 종목 마스터에 있는 종목만 골라 한 번 싣는다.

**일회성이다.** 원천은 `draft/curated/source=dataguide/dataset=price_daily` 의 스냅샷 한 장이고
공급 계약·갱신 담당이 없다(ADR-0057 §5 가 정본 존 승격을 막는 이유). 그래서 canonical 로
올리지 않고 DB 로 바로 싣되, 출처를 `data_version` 에 남겨 구분·롤백할 수 있게 한다. 스냅샷
이후 구간은 KIS 일일 수집(load_price_daily)이 맡는다 — 겹치는 2025-07-17~2026-07-31 의
100,205행에서 두 원천의 시가·고가·저가·종가가 전부 같음을 확인했다(2026-10-02 실측).

**충돌 규칙** — 같은 (종목, 거래일) 행이 이미 있을 때:
  * 없으면 삽입한다.
  * `data_version` 이 `fmp_5min` 으로 시작하면(5분봉 집산 행) 교체하고, `simple_return`·
    `log_return` 을 비운다 — 옛 종가로 계산된 값이라 남기면 새 종가와 어긋난다. 교체 전 행은
    묶음마다 `operations_archive/replaced_rows/` 에 gzip ndjson 으로 먼저 남긴다 — 삽입한 행은
    `data_version` 으로 지우면 되돌아가지만 덮어쓴 값은 이 보존본으로만 되돌릴 수 있다.
  * 이 스텝이 넣은 행(같은 `data_version`)은 다시 써도 같은 값이다(재실행 멱등).
  * 그 밖의 행(KIS 일일 적재분)은 건드리지 않는다. 종가·거래량·`available_at` 이 그대로다.

**적재 컬럼**: open·high·low·close 는 원주가, adjusted_close 는 스냅샷 기준 수정주가다.
`price_basis` 에 그 기준일을 남긴다 — 스냅샷 뒤에 분할·권리락이 생기면 수정주가가 낡는다.
`available_at` 은 거래일 15:30 KST 다(과거 행의 기존 관례 — 5분봉 적재기와 같다). 실제로
우리가 값을 얻은 시각이 아니므로, 과거 시점 재현에 이 행을 쓸 때는 그 점을 감안한다.

원천 형식: 항목마다 wide CSV 한 개(행=거래일, 열=`A`+종목코드). 여섯 파일을 열 번호·행
번호로 나란히 읽으므로, 싣기 전에 전 파일을 한 번 훑어 열 구성과 날짜 행이 같은지 확인하고
하나라도 어긋나면 한 행도 싣지 않는다(한 파일만 밀려도 다른 종목·다른 날의 값이 붙는다).

커밋은 `_CHUNK_DATES` 거래일 단위다. 중간에 실패하면 앞 묶음은 남고, 같은 인자로 다시
돌리면 이어서 채운다(멱등). `dry_run` 은 임시 표에 올려 분류만 세고 price_daily 는 쓰지 않는다.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect
from ..lake import (
    Storage,
    draft_dataguide_price_item_prefix,
    quality_log_key,
    replaced_rows_snapshot_key,
)
from .load_price_daily import _MICS_BY_MARKET, _instrument_ids, _non_negative, _pos_finite

logger = logging.getLogger(__name__)

JOB_NAME = "backfill_price_daily_dataguide"
DATASET = "price_daily_dataguide_backfill"
MARKET = "KR"
_PARTIAL_EXIT_CODE = 2

# price_daily 컬럼 → DataGuide 항목 코드(reference/items_price.csv).
ITEMS = {
    "open_price": "S41000030F",
    "high_price": "S41000040F",
    "low_price": "S41000050F",
    "close_price": "S41000060F",
    "adjusted_close_price": "S410000700",
    "volume": "S41000080F",
}
_PRICE_COLUMNS = ("open_price", "high_price", "low_price", "close_price", "adjusted_close_price")
# 교체해도 되는 기존 행의 data_version 접두 — 5분봉 집산 적재기(analysis-engine
# adapters/price_daily.py)가 찍는 값이다.
_REPLACEABLE_VERSION_PREFIX = "fmp_5min"
# 한 번에 커밋하는 거래일 수. 마스터 약 2,700종목이면 묶음당 13만 행 남짓이다.
_CHUNK_DATES = 50
_SAMPLE_LIMIT = 10

_STAGE_COLUMNS = ("instrument_id", "trade_date", *_PRICE_COLUMNS, "volume")


def _read_item(storage: Storage, as_of_date: str, item_code: str) -> bytes:
    """항목 하나의 wide CSV(gzip) 바이트를 읽는다. 파일이 정확히 하나가 아니면 실패한다."""
    prefix = draft_dataguide_price_item_prefix(MARKET, as_of_date, item_code)
    keys = [k for k in storage.list_keys(prefix + "/") if k.endswith(".csv.gz")]
    if len(keys) != 1:
        raise ValueError(f"DataGuide 항목 파일이 하나가 아니다: item={item_code}, keys={keys}")
    return storage.get_bytes(keys[0])


def _text(data: bytes):
    """gzip 바이트를 줄 단위 텍스트 스트림으로 연다."""
    return gzip.open(io.BytesIO(data), "rt", encoding="utf-8")


def _aligned_layout(blobs: dict[str, bytes]) -> list[str]:
    """여섯 파일의 머리행과 날짜 열이 같은지 확인하고 공통 머리행을 돌려준다. 다르면 실패한다.

    적재 전에 전부 본다 — 읽으며 확인하면 어긋남을 만나기 전 묶음이 이미 커밋돼 있다.
    날짜는 달력일이고 오름차순이어야 한다(기간 필터가 문자열 비교다).
    """
    layouts: dict[str, tuple[list[str], list[str]]] = {}
    for column, data in blobs.items():
        with _text(data) as stream:
            header = _split(stream.readline())
            layouts[column] = (header, [line[:11] for line in stream])
    header, dates = layouts["close_price"]
    if header[:1] != ["date"]:
        raise ValueError("DataGuide 항목 파일에 date 열이 없다")
    for column, (other_header, other_dates) in layouts.items():
        if other_header != header:
            raise ValueError(f"DataGuide 항목 파일의 열 구성이 서로 다르다: {column}")
        if other_dates != dates:
            raise ValueError(f"DataGuide 항목 파일의 날짜 행이 서로 다르다: {column}")
    previous = ""
    for cell in dates:
        trade_date = cell[:10]
        try:
            valid = datetime.strptime(trade_date, "%Y-%m-%d").strftime("%Y-%m-%d") == trade_date
        except ValueError:
            valid = False
        if not valid or cell[10:] != ",":
            raise ValueError(f"달력일로 시작하지 않는 행: {cell!r}")
        if trade_date <= previous:
            raise ValueError(f"날짜 행이 오름차순이 아니다: {previous} → {trade_date}")
        previous = trade_date
    return header


def _split(line: str) -> list[str]:
    """CSV 한 줄을 필드로 나눈다. 원천에 인용부호가 없어 쉼표 분할로 충분하다."""
    return line.rstrip("\r\n").split(",")


def _violation(values: dict) -> str | None:
    """ck_price_daily_values·ck_price_daily_ohl 을 위반하면 사유, 아니면 None.

    위반 행을 임시 표에 올리면 INSERT 가 묶음 전체를 CHECK 로 죽인다 — 미리 빼고 센다(Rule 12).
    거래량은 BIGINT 라 정수가 아닌 값도 위반으로 본다.
    """
    for column in _PRICE_COLUMNS:
        if values[column] is not None and not _pos_finite(values[column]):
            return f"bad_{column}"
    volume = values["volume"]
    if volume is not None:
        if not _non_negative(volume) or float(volume) != int(float(volume)):
            return "bad_volume"
    return None


def _apply_chunk(cur, rows: list[tuple], *, data_version: str, price_basis: str,
                 dry_run: bool, storage: Storage, snapshot_key: str) -> dict[str, int]:
    """한 묶음을 임시 표에 올려 분류를 세고, dry_run 이 아니면 price_daily 에 반영한다.

    분류는 반영 전에 센다: new(행 없음)·replaced(5분봉 집산 행)·rewritten(이 스텝의 기존 행)·
    kept(그 밖의 기존 행 — 손대지 않음). 반영된 행 수가 new+replaced+rewritten 과 다르면
    충돌 규칙이 센 것과 다르게 동작한 것이므로 실패시킨다. 교체할 행이 있으면 덮기 **전에**
    그 행들을 snapshot_key 에 남긴다 — 저장이 실패하면 덮지 않는다.
    """
    cur.execute("TRUNCATE dataguide_price_stage")
    with cur.copy(
        f"COPY dataguide_price_stage ({', '.join(_STAGE_COLUMNS)}) FROM STDIN"
    ) as copy:
        for row in rows:
            copy.write_row(row)
    replaceable = _REPLACEABLE_VERSION_PREFIX + "%"
    cur.execute(
        "SELECT count(*) FILTER (WHERE p.instrument_id IS NULL),"
        "       count(*) FILTER (WHERE p.data_version LIKE %(replaceable)s),"
        "       count(*) FILTER (WHERE p.data_version = %(version)s),"
        "       count(*) FILTER (WHERE p.instrument_id IS NOT NULL"
        "                          AND p.data_version NOT LIKE %(replaceable)s"
        "                          AND p.data_version <> %(version)s)"
        " FROM dataguide_price_stage s"
        " LEFT JOIN price_daily p USING (instrument_id, trade_date)",
        {"replaceable": replaceable, "version": data_version},
    )
    new, replaced, rewritten, kept = cur.fetchone()
    counts = {"new": new, "replaced": replaced, "rewritten": rewritten, "kept": kept}
    if dry_run:
        return counts
    if replaced:
        cur.execute(
            "SELECT row_to_json(p)::text FROM price_daily p"
            " JOIN dataguide_price_stage s USING (instrument_id, trade_date)"
            " WHERE p.data_version LIKE %(replaceable)s"
            " ORDER BY p.instrument_id, p.trade_date",
            {"replaceable": replaceable},
        )
        lines = [line for (line,) in cur.fetchall()]
        if len(lines) != replaced:
            raise RuntimeError(f"보존할 교체 대상 행 수가 분류와 다르다: {len(lines)} != {replaced}")
        storage.put_bytes(snapshot_key, gzip.compress(("\n".join(lines) + "\n").encode("utf-8")))
    cur.execute(
        "INSERT INTO price_daily (instrument_id, trade_date, open_price, high_price, low_price,"
        " close_price, adjusted_close_price, volume, price_basis, available_at, data_version)"
        " SELECT s.instrument_id, s.trade_date, s.open_price, s.high_price, s.low_price,"
        "        s.close_price, s.adjusted_close_price, s.volume, %(basis)s,"
        "        (s.trade_date + TIME '15:30') AT TIME ZONE 'Asia/Seoul', %(version)s"
        " FROM dataguide_price_stage s"
        " ON CONFLICT (instrument_id, trade_date) DO UPDATE"
        " SET open_price = EXCLUDED.open_price, high_price = EXCLUDED.high_price,"
        "     low_price = EXCLUDED.low_price, close_price = EXCLUDED.close_price,"
        "     adjusted_close_price = EXCLUDED.adjusted_close_price, volume = EXCLUDED.volume,"
        "     price_basis = EXCLUDED.price_basis, available_at = EXCLUDED.available_at,"
        "     data_version = EXCLUDED.data_version, simple_return = NULL, log_return = NULL"
        " WHERE price_daily.data_version LIKE %(replaceable)s"
        "    OR price_daily.data_version = EXCLUDED.data_version",
        {"basis": price_basis, "version": data_version, "replaceable": replaceable},
    )
    if cur.rowcount != new + replaced + rewritten:
        raise RuntimeError(
            f"반영 행 수가 분류와 다르다: rowcount={cur.rowcount}, "
            f"new={new}, replaced={replaced}, rewritten={rewritten}"
        )
    return counts


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    as_of_date: str,
    from_date: str,
    to_date: str,
    dry_run: bool = False,
) -> int:
    """DataGuide 스냅샷의 [from_date, to_date] 일봉을 price_daily 에 싣는다. 성공 0, 행 격리 2, 장애 1.

    as_of_date 는 스냅샷 파티션(`as_of_date=`)이다. 마스터에 없는 종목의 열은 읽지 않는다.
    """
    started_at = datetime.now(timezone.utc)
    data_version = f"dataguide-{as_of_date.replace('-', '')}"
    price_basis = f"raw_close;adj_asof={as_of_date}"
    totals = {"new": 0, "replaced": 0, "rewritten": 0, "kept": 0}
    dates_read = rows_read = chunks_done = 0
    columns_total = columns_in_master = 0
    skipped_check_violation = skipped_orphan_cell = 0
    check_violations: list[dict] = []
    ambiguous_tickers: list[str] = []
    master_without_column: list[str] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        if from_date > to_date:
            raise ValueError(f"from_date 가 to_date 보다 늦다: {from_date} > {to_date}")
        blobs = {column: _read_item(storage, as_of_date, code) for column, code in ITEMS.items()}
        header = _aligned_layout(blobs)
        columns_total = len(header) - 1
        streams = {column: _text(data) for column, data in blobs.items()}
        for stream in streams.values():
            stream.readline()                       # 머리행은 위에서 확인했다

        with connect(db) as conn:
            ids, ambiguous = _instrument_ids(conn, _MICS_BY_MARKET[MARKET])
            # 열 인덱스 → instrument_id. 열 이름은 'A'+종목코드다. MIC 를 가로질러 겹친 ticker 는
            # 어느 종목인지 알 수 없어 싣지 않는다(load_price_daily 와 같은 근거).
            targets: list[tuple[int, str]] = []
            seen: set[str] = set()
            for index, name in enumerate(header):
                ticker = name[1:] if index and name.startswith("A") else None
                if ticker is None or ticker not in ids:
                    continue
                seen.add(ticker)
                if ticker in ambiguous:
                    ambiguous_tickers.append(ticker)
                    continue
                targets.append((index, ids[ticker]))
            columns_in_master = len(targets)
            master_without_column = sorted(set(ids) - seen)

            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TEMP TABLE dataguide_price_stage ("
                    " instrument_id TEXT NOT NULL, trade_date DATE NOT NULL,"
                    " open_price NUMERIC, high_price NUMERIC, low_price NUMERIC,"
                    " close_price NUMERIC, adjusted_close_price NUMERIC, volume BIGINT)"
                )
                chunk: list[tuple] = []
                chunk_dates = 0

                def flush() -> None:
                    """쌓인 묶음을 반영하고 커밋한다."""
                    nonlocal chunk, chunk_dates, chunks_done
                    if chunk:
                        counts = _apply_chunk(
                            cur, chunk, data_version=data_version, price_basis=price_basis,
                            dry_run=dry_run, storage=storage,
                            snapshot_key=replaced_rows_snapshot_key(DATASET, run_id, chunks_done),
                        )
                        for key, value in counts.items():
                            totals[key] += value
                    conn.commit()
                    chunks_done += 1
                    chunk, chunk_dates = [], 0

                # 행 정렬은 _aligned_layout 이 보장했다 — 같은 번호의 줄은 같은 거래일이다.
                for row_lines in zip(*streams.values(), strict=True):
                    lines = dict(zip(streams, row_lines))
                    trade_date = lines["close_price"][:10]
                    if trade_date < from_date or trade_date > to_date:
                        continue
                    fields = {column: _split(line) for column, line in lines.items()}
                    if any(len(f) != len(header) for f in fields.values()):
                        raise ValueError(f"열 수가 머리행과 다른 행: {trade_date}")
                    dates_read += 1
                    for index, instrument_id in targets:
                        values = {column: fields[column][index] or None for column in ITEMS}
                        if values["close_price"] is None:
                            # 종가가 없으면 그날 그 종목은 거래일이 아니다(상장 전·폐지 후).
                            # 종가 없이 다른 값만 있는 칸은 행을 만들 수 없어 센다.
                            if any(v is not None for v in values.values()):
                                skipped_orphan_cell += 1
                            continue
                        rows_read += 1
                        reason = _violation(values)
                        if reason is not None:
                            skipped_check_violation += 1
                            if len(check_violations) < _SAMPLE_LIMIT:
                                check_violations.append({
                                    "column": header[index], "trade_date": trade_date,
                                    "reason": reason,
                                })
                            continue
                        volume = values["volume"]
                        chunk.append((
                            instrument_id, trade_date,
                            *(values[column] for column in _PRICE_COLUMNS),
                            None if volume is None else int(float(volume)),
                        ))
                    chunk_dates += 1
                    if chunk_dates >= _CHUNK_DATES:
                        flush()
                flush()
    except Exception as exc:
        # 앞서 커밋된 묶음은 남는다 — 같은 인자로 다시 돌리면 이어서 채운다(멱등).
        logger.exception("DataGuide 일봉 적재 실패")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        exit_code = 1

    if exit_code == 0 and (skipped_check_violation or skipped_orphan_cell or ambiguous_tickers):
        exit_code = _PARTIAL_EXIT_CODE

    written = 0 if dry_run else totals["new"] + totals["replaced"] + totals["rewritten"]
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": as_of_date, "from_date": from_date, "to_date": to_date,
        "dry_run": dry_run, "data_version": data_version, "price_basis": price_basis,
        "columns_total": columns_total, "columns_in_master": columns_in_master,
        "master_without_column": len(master_without_column),
        "master_without_column_sample": master_without_column[:_SAMPLE_LIMIT],
        "ambiguous_tickers": sorted(ambiguous_tickers),
        "dates_read": dates_read, "rows_read": rows_read, "chunks_done": chunks_done,
        "created": totals["new"], "replaced_5min": totals["replaced"],
        "rewritten": totals["rewritten"], "kept_existing": totals["kept"],
        "skipped_check_violation": skipped_check_violation,
        "check_violations_sample": check_violations,
        "skipped_orphan_cell": skipped_orphan_cell,
        "failures": failures, "exit_code": exit_code,
        "ops": {
            "records_out": written,
            "failed_records": len(failures) + skipped_check_violation + skipped_orphan_cell,
        },
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "backfill_price_daily_dataguide 완료: dry_run=%s dates=%d rows=%d created=%d "
        "replaced_5min=%d rewritten=%d kept_existing=%d check_violation=%d orphan=%d",
        dry_run, dates_read, rows_read, totals["new"], totals["replaced"], totals["rewritten"],
        totals["kept"], skipped_check_violation, skipped_orphan_cell,
    )
    return exit_code
