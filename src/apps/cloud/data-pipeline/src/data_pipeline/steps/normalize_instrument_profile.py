"""종목기본정보 정제 Step2 — 정규화 + canonical 멱등 병합 (ALPHA-829).

raw instrument_profile(KRX OpenAPI `*_isu_base_info`)을 읽어 종목 마스터를 만들 최소
사실로 정규화한다. 소비자는 `load_instruments`(ALPHA-830)이고, 이 canonical 이 곧
전종목 `entity`·`instrument` 의 재료다.

**명칭 선택 — 이게 이 데이터셋을 도입한 이유다.** `ISU_ABBRV`(종목약명, "현대차")를
표시명으로, `ISU_NM`(종목명, "현대자동차보통주")을 법적 명칭으로 둔다. 뉴스 표기에 가까운
것은 **약명** 쪽이고(엔티티 해소가 여기 붙는다), 법적 명칭은 감사·대조용이라 둘 다 보존한다.
DART 정식사명(`에스케이바이오팜`)과 다른 축이라는 점이 소스 선택의 근거였다(ALPHA-829).

**시간축은 벤더 기준일(`bas_dd`)이지 수집일이 아니다.** KRX 가 당일 조회를 막아 basDd 는
직전 거래일이라, 수집일을 as_of 로 쓰면 마스터가 하루 앞선 날짜를 주장한다.
⚠️ 두 날짜가 항상 다르지는 않다(수집일은 UTC, basDd 는 KST 파생 — 08~09시 KST 실행에서
우연히 같아진다). "다르니까 구분된다"에 기대지 말고 `bas_dd` 만 읽는다.

`ISU_SRT_CD` 형태 판정은 `parse.krx_short_code` 하나로 간다(ALPHA-463) — 문자 섞인 신형
단축코드(`0093A0`)도 통과해야 하므로 `isdigit()` 로 거르지 않는다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..lake import (
    Storage,
    canonical_instrument_profile_partition,
    collection_log_key,
    is_raw_instrument_profile_key,
    parse_raw_instrument_profile_key,
    quality_log_key,
)
from ..lake.latest_good import (
    PointerPlan,
    inspect_collection_logs,
    max_fetched_at,
    prepare_pointer,
    publish_pointer,
)
from ..parse import KR_MIC_BY_BOARD, krx_short_code

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_instrument_profile"
DATASET = "instrument_profile"
_PARTIAL_EXIT_CODE = 2

_CANONICAL_COLUMNS = (
    "market", "as_of_date", "ticker", "market_code", "isin", "display_name", "legal_name",
    "english_name", "board", "security_group", "share_class", "listed_date",
    "listed_shares",
    "fetched_at",
)

_OLDEST = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    # 전부 string 이다 — 상장주식수는 자릿수가 크고 벤더가 콤마를 섞어 주므로 canonical 은
    # 원문 보존에 그치고, 수치 변환은 쓰는 쪽이 한다(가격·재무 canonical 과 같은 관례).
    return pa.schema([(c, pa.string()) for c in _CANONICAL_COLUMNS])


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io

    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _write_parquet_rows(rows: list[dict]) -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _CANONICAL_COLUMNS} for r in rows], schema=_canonical_schema()
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def _as_of_date(bas_dd: object) -> str | None:
    """`20260806` → `2026-08-06`. 형태가 아니면 None(그 행은 탈락)."""
    if not isinstance(bas_dd, str) or len(bas_dd) != 8 or not bas_dd.isdigit():
        return None
    return f"{bas_dd[:4]}-{bas_dd[4:6]}-{bas_dd[6:]}"


def normalize_row(raw: dict) -> tuple[dict | None, str | None]:
    """raw 1행 → canonical 행. 실패 시 (None, 사유).

    탈락 사유는 호출부가 quality log 에 분포로 남긴다 — 조용히 버리지 않는다(Rule 12).
    """
    ticker = krx_short_code(raw.get("ISU_SRT_CD"))
    if not ticker:
        return None, "bad_ticker"
    as_of_date = _as_of_date(raw.get("bas_dd"))
    if not as_of_date:
        return None, "bad_bas_dd"
    display_name = _text(raw.get("ISU_ABBRV"))
    if not display_name:
        # 표시명이 없으면 이 데이터셋의 존재 이유가 사라진다(엔티티 해소가 붙을 키가 없다).
        return None, "missing_display_name"
    market = _text(raw.get("market")) or "KR"
    board = _text(raw.get("board"))
    market_code = KR_MIC_BY_BOARD.get(board or "")
    if not market_code:
        # MIC 없이는 instrument 가 될 수 없다(`instrument.market_code NOT NULL`). 조용히
        # None 으로 통과시키면 마스터 로더가 그 행을 말없이 버려 종목이 사라진다 —
        # KRX 가 시장을 늘리면 여기서 사유와 함께 드러나야 한다(Rule 12).
        return None, "unknown_board"
    return {
        "market": market,
        "as_of_date": as_of_date,
        "ticker": ticker,
        # 거래소 MIC. **값의 SSOT 는 `parse.KR_MIC_BY_BOARD`** — 구성종목 정제가 벤더
        # MKT_ID 로 만드는 것과 같은 값이어야 한다. 갈리면 같은 종목이 두 market_code 로
        # 마스터에 두 번 선다(자연키가 `(market_code, ticker)`).
        "market_code": market_code,
        "isin": _text(raw.get("ISU_CD")),
        "display_name": display_name,
        "legal_name": _text(raw.get("ISU_NM")),
        "english_name": _text(raw.get("ISU_ENG_NM")),
        "board": board,
        "security_group": _text(raw.get("SECUGRP_NM")),
        # 주식종류(보통주·구형우선주·신형우선주·종류주권). canonical 은 상장 **종목**을
        # 전부 보존하고, 무엇을 마스터에 세울지는 소비자가 정한다 — 실측 2,872종 중
        # 113종이 우선주 계열이다(ALPHA-830).
        "share_class": _text(raw.get("KIND_STKCERT_TP_NM")),
        "listed_date": _text(raw.get("LIST_DD")),
        "listed_shares": _text(raw.get("LIST_SHRS")),
        "fetched_at": _text(raw.get("fetched_at")),
    }, None


def _fetched_at(row: dict) -> datetime:
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 (market, as_of_date) 파티션을 ticker 키로 멱등 병합(최신 fetched_at 우선).

    재실행이 같은 기준일을 다시 받아도 행이 늘지 않고, **한 기준일이 여러 런에 걸쳐
    채워져도 앞선 행이 살아남는다**(etf_profile 과 같은 모델).
    """
    acc: dict[str, dict] = {}
    for row in [*existing, *new_rows]:
        key = row["ticker"]
        prev = acc.get(key)
        if prev is None or _fetched_at(row) >= _fetched_at(prev):
            acc[key] = row
    return [acc[k] for k in sorted(acc, key=str)]


def _cross_board_collisions(rows: list[dict]) -> dict[str, list[str]]:
    """같은 ticker 가 서로 다른 board 로 온 경우 — {ticker: [board, ...]}.

    `market` 은 항상 "KR" 이라 board 는 파티션을 가르지 않는다. 그래서 같은 단축코드가 두
    시장에서 오면 병합이 **조용히 한쪽을 덮는다**(같은 런은 fetched_at 이 같아 나중 것이
    이긴다). KRX 단축코드는 시장 간 유일한 것으로 알려져 있지만 그건 우리가 강제하는
    불변식이 아니다 — 깨지면 종목 하나가 잘못된 시장 이름을 달거나 사라지므로, 덮기 전에
    수를 세어 로그로 드러낸다(Rule 12).

    ⚠️ **이번 런이 낸 행들 사이만 본다.** 파티션에 이미 있던 행과의 충돌은 여기 안 잡힌다
    — 그건 같은 종목의 정상적인 재수집과 형태가 같아서(같은 ticker·다른 fetched_at) 구분할
    근거가 없다. 이 함수가 잡는 것은 **한 런 안에서 두 시장이 같은 코드를 준** 경우다.
    """
    boards_by_ticker: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row.get("board"):
            boards_by_ticker[row["ticker"]].add(row["board"])
    return {t: sorted(b) for t, b in boards_by_ticker.items() if len(b) > 1}


def _write_canonical(
    storage: Storage, rows: list[dict],
) -> tuple[list[dict[str, str]], int, list[tuple[str, str, bytes, list[dict]]]]:
    """shared canonical 병합 결과와 이 실행이 쓴 merged snapshot bytes를 반환한다."""
    by_partition: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_partition[(row["market"], row["as_of_date"])].append(row)

    partitions: list[dict[str, str]] = []
    candidates: list[tuple[str, str, bytes, list[dict]]] = []
    rows_written = 0
    for (market, as_of_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_instrument_profile_partition(market, as_of_date)
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows)
        data = _write_parquet_rows(merged)
        storage.put_bytes(f"{prefix}/part-00000.parquet", data)
        partitions.append({"market": market, "as_of_date": as_of_date})
        candidates.append((market, as_of_date, data, merged))
        rows_written += len(merged)
    return partitions, rows_written, candidates


def _collection_keys(raw_keys: list[str]) -> list[str]:
    keys = []
    for raw_key in raw_keys:
        parsed = parse_raw_instrument_profile_key(raw_key)
        if parsed["market"] == "KR":
            keys.append(collection_log_key(
                parsed["source"], DATASET, parsed["ingest_date"], parsed["run_id"],
            ))
    return keys


def _prepare_latest_good(
    storage: Storage, run_id: str,
    candidates: list[tuple[str, str, bytes, list[dict]]],
) -> PointerPlan | None:
    kr_candidates = [
        (as_of_date, max_fetched_at(rows), data, rows)
        for market, as_of_date, data, rows in candidates if market == "KR"
    ]
    if not kr_candidates:
        return None
    as_of_date, _, data, rows = max(kr_candidates, key=lambda item: (item[0], item[1]))
    return prepare_pointer(
        storage, dataset=DATASET, producer=JOB_NAME, market="KR",
        as_of_date=as_of_date, run_id=run_id, artifact_bytes=data, rows=rows,
    )


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw instrument_profile → canonical + latest-good pointer. 성공 0, partial 2, fatal 1."""
    started_at = datetime.now(timezone.utc)
    rows_read = 0
    dropped: dict[str, int] = {}
    normalized: list[dict] = []
    exit_code = 0
    failures: list[dict] = []
    raw_list_ok = True
    try:
        keys = [k for k in storage.list_keys("raw/") if is_raw_instrument_profile_key(k)]
    except Exception as exc:
        logger.exception("raw 목록 조회 실패")
        keys = []
        failures.append({"reasons": ["raw_list_error"], "error": str(exc)})
        exit_code = 1
        raw_list_ok = False
    if input_run_id is not None:
        try:
            keys = [k for k in keys
                    if parse_raw_instrument_profile_key(k)["run_id"] == input_run_id]
        except Exception as exc:
            logger.exception("raw key 파싱 실패")
            keys = []
            failures.append({"reasons": ["raw_key_error"], "error": str(exc)})
            exit_code = 1

    collection_check = None
    collection_error = None
    if input_run_id is not None and keys:
        try:
            collection_check = inspect_collection_logs(storage, _collection_keys(keys))
        except Exception as exc:
            logger.exception("matching collection log 검증 실패")
            collection_error = str(exc)
            exit_code = 1

    for key in sorted(keys):
        try:
            lines = storage.get_bytes(key).decode("utf-8").splitlines()
        except Exception as exc:
            logger.exception("raw 읽기 실패: %s", key)
            failures.append({"raw_key": key, "reasons": ["raw_read_error"], "error": str(exc)})
            exit_code = 1
            continue
        for line in lines:
            if not line.strip():
                continue
            rows_read += 1
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                dropped["malformed_json"] = dropped.get("malformed_json", 0) + 1
                continue
            if not isinstance(raw, dict):
                dropped["not_object"] = dropped.get("not_object", 0) + 1
                continue
            try:
                row, reason = normalize_row(raw)
            except Exception:
                logger.exception("행 정규화 실패(격리): %s", key)
                dropped["row_error"] = dropped.get("row_error", 0) + 1
                continue
            if row is None:
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
            normalized.append(row)

    collisions = _cross_board_collisions(normalized)
    partitions: list[dict[str, str]] = []
    candidates: list[tuple[str, str, bytes, list[dict]]] = []
    rows_written = 0
    canonical_written = False
    if raw_list_ok:
        try:
            partitions, rows_written, candidates = _write_canonical(storage, normalized)
            canonical_written = True
        except Exception as exc:
            logger.exception("canonical 적재 실패")
            failures.append({"reasons": ["canonical_write_error"], "error": str(exc)})
            exit_code = 1

    collection_incomplete = collection_check is not None and not collection_check.complete
    if (dropped or collisions or collection_incomplete) and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    plan: PointerPlan | None = None
    pointer_error = collection_error
    if input_run_id is None:
        pointer_action = "retain_unscoped_recovery"
    elif exit_code == 1:
        pointer_action = "retain_fatal"
    elif exit_code == _PARTIAL_EXIT_CODE:
        pointer_action = "retain_partial"
    elif not keys or not partitions:
        pointer_action = "retain_empty"
    else:
        try:
            plan = _prepare_latest_good(storage, run_id, candidates)
            pointer_action = plan.action if plan is not None else "retain_no_kr_candidate"
            if plan is not None:
                exit_code = plan.exit_code
        except Exception as exc:
            logger.exception("latest-good artifact/pointer 준비 실패")
            pointer_error = str(exc)
            pointer_action = "retain_fatal"
            exit_code = 1

    checked_date = started_at.isoformat()[:10]
    finished_at = datetime.now(timezone.utc)
    latest_good = plan.quality_fields() if plan is not None else {
        "candidate": None,
        "artifact": None,
        "pointer_key": None,
        "pointer_base_version": None,
        "pointer_intended_action": pointer_action,
    }
    latest_good.update({
        "collection_log_keys": list(collection_check.keys) if collection_check else [],
        "collection_statuses": list(collection_check.statuses) if collection_check else [],
        "error": pointer_error,
    })
    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
        "input_run_id": input_run_id,
        "rows_read": rows_read, "rows_normalized": len(normalized),
        "dropped_by_reason": dropped,
        "cross_board_ticker_collisions": collisions,
        "canonical_written": canonical_written,
        "canonical_partitions": partitions,
        "partitions_written": len(partitions), "rows_written": rows_written,
        "latest_good": latest_good,
        "failures": failures, "exit_code": exit_code,
        "ops": {"records_out": rows_written,
                "failed_records": sum(dropped.values()) + len(failures)},
    }
    quality_key = quality_log_key(DATASET, checked_date, run_id)
    try:
        storage.put_bytes(
            quality_key, json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    except Exception:
        logger.exception("품질 로그 기록 실패")
        return 1

    if plan is not None and plan.action == "advance" and exit_code == 0:
        try:
            publish_pointer(storage, plan)
        except Exception as exc:
            logger.exception("latest-good pointer CAS publish 실패")
            exit_code = 1
            log["exit_code"] = 1
            log["latest_good"]["pointer_publish_error"] = str(exc)
            log["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                storage.put_bytes(
                    quality_key, json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"),
                )
            except Exception:
                logger.exception("pointer 실패 뒤 quality_log 최종 상태 정정 실패")
    logger.info(
        "normalize_instrument_profile 완료: read=%d normalized=%d written=%d dropped=%s",
        rows_read, len(normalized), rows_written, dropped,
    )
    return exit_code
