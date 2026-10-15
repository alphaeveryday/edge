"""장중 투자자 추정 정제 Step2 — 정규화 + 수치·정체성 게이트 (ALPHA-768).

raw `investor_flow_intraday`(KIS HHPTJ04160200)를 읽어 표준 장중 추정 행으로 정규화한다.
EOD 정제(`normalize_investor`)와 같은 결의 게이트지만 **정체성 키가 한 축 많다**:

| | EOD `investor_flow_daily` | 장중 `investor_flow_intraday` |
|---|---|---|
| 정체성 | (market, ticker, trade_date) | (market, ticker, trade_date, **asof_slot**) |
| 시간축 원본 | `stck_bsop_date`(응답의 거래일) | `asof_date`(수집이 붙인 provenance) + `bsop_hour_gb` |
| 값 | 확정 순매수 수량·대금 13종 | **가집계 추정 수량 3종**(개인·기관세부·대금 없음) |

그래서 `_merge_partition`·스키마·필드그룹을 인자로 갈아끼울 수 없다 — 공용 헬퍼(`_to_int`·
`_fetched_at`·`_blank`·`_dedup`)만 EOD 정제에서 가져다 쓰고 나머지는 이 축으로 새로 쓴다
(`ingest_raw_investor` 가 `ingest_price_raw._kr_holdings_universe` 를 import 하는 선례).

**대금 컬럼이 없다.** 벤더가 `frgn`·`orgn`·`sum` 가집계 **수량**만 주고 순매수 대금을 주지 않아
EOD 의 백만원→원 환산(`_NET_VALUE_SCALE`)도, `currency` 태깅도 이 데이터셋엔 대상이 없다.
없는 값을 지어내지 않는다 — 대금이 필요한 소비자는 EOD 확정치를 봐야 한다.

**`asof_slot` 은 TEXT 로 원문 보존한다.** `bsop_hour_gb` 의 도메인은 2026-08-06 dev 실측에서
**`"1"`~`"5"` 한 자리 코드**로 확인됐다(`"0930"` 같은 시각 문자열이 아니다 — 슬롯 1 은 기관
순매수가 284/284 전건 0이라 09:30 외국인 갱신에 대응한다). 그래도 TEXT 를 유지한다: 관측이
하루치라 도메인이 닫혔다는 근거가 못 되고, 시각으로 파싱하면 형식을 잘못 가정해 정체성 키를
오염시키는데 소급 재조회가 없는 소스라 사후 정정이 불가하다.

⚠️ **이 코드가 시각 문자열을 전제하면 안 되는 이유이기도 하다** — 소비 표면의 시점 클램프는
`asof_slot` 이 아니라 `available_at` 을 쓴다(analysis-engine `sql_surface.v_flow_intraday`).
코드를 TEXT 비교로 자르면 항상 거짓이 되어 뷰가 조용히 빈다.

**정정 정책 = 최신값 덮어쓰기**(EOD 와 같은 모델). 벤더가 가집계를 고치면 canonical 이 최신
fetched_at 으로 수렴한다. 같은 슬롯의 앞선 관측은 남기지 않는다 — 원본은 raw 에 있다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from ..lake import (
    Storage,
    canonical_run_manifest_key,
    is_raw_investor_estimate_key,
    parse_raw_investor_key,
    quality_log_key,
)
from .normalize_investor import _blank, _dedup, _fetched_at, _to_int

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_investor_estimate"
DATASET = "investor_flow_intraday"
_PARTIAL_EXIT_CODE = 2

# 표준 컬럼 → KIS 장중 추정 필드(실측: `.dev/etf-flow-collection-plan.md` §2.5). `fake`=가집계라
# 컬럼명에 `_est` 를 남겨 **표면에서 잠정임이 읽히게** 한다 — EOD 확정 컬럼과 이름이 같으면
# 소비자가 어느 쪽을 본 것인지 사후에 구분할 수 없다.
# 셋 다 필수다: 벤더가 주는 전부라 하나라도 없으면 그 행에 남는 관측이 없다(EOD headline 과 같은 결).
_QTY_FIELDS = {
    "net_qty_foreign_est": "frgn_fake_ntby_qty",       # 외국인 추정 순매수 수량(주)
    "net_qty_institution_est": "orgn_fake_ntby_qty",   # 기관 추정(세분 없음 — 벤더 미제공)
    "net_qty_total_est": "sum_fake_ntby_qty",          # 벤더 가집계 합
}

_CANONICAL_COLUMNS = (
    "market", "ticker", "trade_date", "asof_slot", *_QTY_FIELDS, "source_vendor", "fetched_at",
)


def _norm_trade_date(raw: dict, reasons: list[str]) -> str | None:
    """`asof_date`(YYYY-MM-DD) 검증. 결측=missing_field, 형식 불량=bad_trade_date.

    EOD 와 달리 이 값은 **응답이 아니라 수집이 붙인 provenance** 다(API 에 날짜 필드가 없다,
    ALPHA-767). 이미 ISO 형식으로 오지만 그대로 믿지 않고 strptime 왕복으로 실재 달력일까지
    검증한다 — 손상된 raw·수기 편집이 '2026-02-31' 을 거래일 파티션으로 만들면 그 파티션은
    영영 대조 불가다(소급 재조회 없음).
    """
    value = raw.get("asof_date")
    if not value or (isinstance(value, str) and not value.strip()):
        reasons.append("missing_field")
        return None
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        reasons.append("bad_trade_date")
        return None
    if parsed.isoformat() != text:
        reasons.append("bad_trade_date")
        return None
    return text


def _norm_slot(raw: dict, reasons: list[str]) -> str | None:
    """`bsop_hour_gb` → asof_slot(원문 문자열). 결측·공백=missing_field, 비문자열=bad_asof_slot.

    수집도 슬롯 없는 행을 실패로 기록하지만(`kis_investor_estimate`) 행 자체는 bronze 에
    보존한다 — 정제가 다시 막는 이유는 그 보존된 행이 canonical 로 새면 **어느 시점 값인지
    모르는 행**이 되기 때문이다.

    ⚠️ **비문자열을 `str()` 로 받지 않는다.** 이 필드는 자유 텍스트라 어떤 문자열이든 통과하는
    유일한 정체성 축이고, 그래서 타입 드리프트가 사유 없이 새 PK 로 인증된다: JSON 숫자 `930`
    을 `"930"` 으로 받으면 정상 `"0930"` 과 **별도 행**이 되어 같은 관측이 둘로 갈리고, 정정이
    기존 행을 못 덮는다(소급 재조회가 없어 사후 병합도 불가). `[]`·NaN 도 `"[]"`·`"nan"` 이라는
    그럴듯한 슬롯이 된다. 형식이 미관측일수록 **모양이 다른 것은 드러내야** 한다(Rule 12).
    """
    value = raw.get("bsop_hour_gb")
    if value is None:
        reasons.append("missing_field")
        return None
    if not isinstance(value, str):
        reasons.append("bad_asof_slot")
        return None
    text = value.strip()
    if not text:
        reasons.append("missing_field")
        return None
    if "\x00" in text:
        # NUL 은 PostgreSQL TEXT 가 **저장 자체를 거부**하는 유일한 문자다 — 여기서 통과시키면
        # 적재 INSERT 가 예외로 죽어 정상 슬롯까지 든 트랜잭션이 통째로 롤백된다. 자유 텍스트
        # 축이라 로더가 뒤에서 걸러낼 방법이 없으니(비공백이면 유효한 슬롯이다) 여기가 마지막이다.
        reasons.append("bad_asof_slot")
        return None
    return text


# canonical 수량은 int64 다 — 범위를 넘는 값은 parquet 직렬화에서 OverflowError 로 **파티션
# 쓰기 전체**를 실패시킨다(통과 행까지 안 써진다). 게이트는 스스로 죽지 않아야 하므로 여기서
# 그 행만 격리한다(로더의 `_load_violation` 과 같은 근거).
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def _norm_fetched_at(raw: dict, reasons: list[str]) -> str | None:
    """`fetched_at` 검증(원문 보존). 결측=missing_field, 파싱 불가=bad_fetched_at.

    이 값은 두 곳에서 정본이다 — canonical 병합의 '최신 우선' 정렬 키이자, 적재의
    `available_at`(PIT 클램프 재료) 원본이다. 그래서 불량값이 통과하면 조용히 두 번 틀린다:
    병합에서 그 행이 항상 지고(`_fetched_at` 이 파싱 실패를 가장 오래된 것으로 취급),
    적재에서는 TIMESTAMPTZ 변환 오류로 **정상 행까지 든 트랜잭션 전체가 롤백**된다.
    결측도 마찬가지다 — 로더가 실행 시각으로 대체해 관측 가능 시각을 지어낸다.

    **오프셋(tzinfo)까지 요구한다.** `fromisoformat` 은 `"2026-08-05"`(날짜만)·
    `"2026-08-05T09:30:00"`(naive)도 받는데, 둘 다 '언제'가 정해지지 않은 값이다: 날짜만이면
    자정으로 굳어 실제 수집보다 이른 available_at 이 되어 **PIT 조회가 아직 없던 관측을 노출**
    하고, naive 는 병합(UTC 가정)과 PostgreSQL TIMESTAMPTZ 변환(세션 시간대)이 **서로 다른
    순간으로 해석**해 두 계층이 조용히 어긋난다. 수집기는 항상 오프셋을 붙여 쓴다.
    """
    value = raw.get("fetched_at")
    if value is None or (isinstance(value, str) and not value.strip()):
        reasons.append("missing_field")
        return None
    if not isinstance(value, str):
        reasons.append("bad_fetched_at")
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        reasons.append("bad_fetched_at")
        return None
    if parsed.tzinfo is None:
        reasons.append("bad_fetched_at")
        return None
    return value.strip()


def _normalize(vendor: str, raw: dict) -> tuple[dict, list[str]]:
    """벤더 raw 행 → 표준 장중 추정 행 + 정규화 사유. 수량 3종 전부 필수."""
    reasons: list[str] = []
    row: dict = {
        "market": raw.get("market"),
        "ticker": raw.get("our_ticker"),
        "trade_date": _norm_trade_date(raw, reasons),
        "asof_slot": _norm_slot(raw, reasons),
        "source_vendor": vendor,
        "fetched_at": _norm_fetched_at(raw, reasons),
    }
    for column, key in _QTY_FIELDS.items():
        value = _to_int(raw, key, reasons)
        if value is not None and not (_INT64_MIN <= value <= _INT64_MAX):
            # 파이썬 int 는 무한정이라 _to_int 는 통과시킨다 — canonical 스키마가 못 담는다.
            reasons.append("out_of_range")
            value = None
        row[column] = value
    # market·ticker 도 정체성 키다 — 없으면 키를 못 만들어 canonical 로 못 간다(EOD 와 동형).
    # 시장은 KR 만이다: KIS 장중 추정은 KRX 전용이고, 다른 시장 라벨이 오면 그건 드리프트다.
    if _blank(row["market"]):
        reasons.append("missing_field")
    elif row["market"] != "KR":
        reasons.append("unsupported_market")
    if _blank(row["ticker"]):
        reasons.append("missing_field")
    return row, _dedup(reasons)


def _canonical_schema():
    import pyarrow as pa

    fields = [(c, pa.string()) for c in ("market", "ticker", "trade_date", "asof_slot")]
    fields += [(c, pa.int64()) for c in _QTY_FIELDS]
    fields += [("source_vendor", pa.string()), ("fetched_at", pa.string())]
    return pa.schema(fields)


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


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 (market, trade_date) 파티션을 **(ticker, asof_slot)** 키로 병합.

    ⚠️ EOD 처럼 ticker 단독으로 병합하면 하루 4~5 슬롯 중 마지막만 남아 **장중 추이가 통째로
    사라진다** — 이 데이터셋의 존재 이유가 그 추이다.

    같은 키의 재관측은 최신 fetched_at 이 이긴다(정정 = 최신값 덮어쓰기). 벤더 교차 충돌
    처리는 두지 않는다 — 아래 run() 이 `vendor != "kis"` 를 unsupported_vendor 로 먼저 막아
    한 파티션에 두 벤더가 들어올 경로가 없다(닿지 않는 분기를 흉내 내지 않는다).
    """
    acc: dict[tuple[str, str], dict] = {}
    for row in [*existing, *new_rows]:
        key = (row["ticker"], row["asof_slot"])
        prev = acc.get(key)
        if prev is None or _fetched_at(row) >= _fetched_at(prev):
            acc[key] = row
    return [acc[k] for k in sorted(acc)]


def _write_canonical(storage: Storage, passing: list[dict]) -> tuple[list[dict], int]:
    """통과 행을 (market, trade_date) 파티션별로 기존 canonical 과 멱등 병합해 쓴다.
    반환: (이번 실행의 성공 winner manifest 항목, 병합 뒤 canonical 행 수)."""
    from ..lake import canonical_investor_flow_intraday_partition

    by_partition: dict[tuple[str, str], list[dict]] = {}
    for row in passing:
        by_partition.setdefault((row["market"], row["trade_date"]), []).append(row)

    partitions: list[dict] = []
    rows_written = 0
    for (market, trade_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_investor_flow_intraday_partition(market, trade_date)
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows)
        key = f"{prefix}/part-00000.parquet"
        parquet_bytes = _write_parquet_rows(merged)
        storage.put_bytes(key, parquet_bytes)
        partitions.append({
            "market": market,
            "trade_date": trade_date,
            "key": key,
            # canonical key는 다음 normalize가 덮어쓸 수 있다. consumer가 이 run이 확정한
            # 바이트와 같은지 확인해야 앞 run_id에 뒤 run 값을 붙이는 계보 오염을 막는다.
            "sha256": hashlib.sha256(parquet_bytes).hexdigest(),
            # 값이 바뀐 행만이 아니라 이번 실행에서 게이트를 통과해 확정한 모든 논리 키다.
            # 같은 값 재확정도 하류의 현재 실행 범위이므로 포함하고, 중복 관측은 한 번만 남긴다.
            "winner_ids": [
                {"ticker": ticker, "asof_slot": asof_slot}
                for ticker, asof_slot in sorted({
                    (row["ticker"], row["asof_slot"]) for row in new_rows
                })
            ],
        })
        rows_written += len(merged)
    return partitions, rows_written


def _manifest_bytes(run_id: str, canonical_written: bool, partitions: list[dict]) -> bytes:
    return json.dumps({
        "run_id": run_id,
        "producer": JOB_NAME,
        "canonical_written": canonical_written,
        "canonical_partitions": partitions,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw investor_flow_intraday → 정규화 → 게이트 → quality_log.

    성공 0, 격리된 입력 실패 2, 저장 실패 1. 부분 실패 2는 장중 수급 SFN이 성공 winner의
    적재를 계속한 뒤 전체 실행을 실패로 마감하는 제어 신호다.

    input_run_id 지정 시 그 수집 런(=한 슬롯)의 raw 만 읽는다. 미지정이면 전체를 읽는다 —
    같은 날 앞 슬롯을 다시 훑어도 멱등이라 결과가 같다(EOD 정제와 동형).
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    read = 0
    failures: list[dict] = []
    passing: list[dict] = []
    exit_code = 0
    manifest_key = canonical_run_manifest_key(DATASET, run_id)
    manifest_initialized = True
    try:
        # raw 목록부터 실패해도 같은 run의 이전 성공 범위를 승인하지 않도록 가장 먼저 무효화한다.
        storage.put_bytes(manifest_key, _manifest_bytes(run_id, False, []))
    except Exception:
        logger.exception("canonical run manifest 초기화 실패")
        manifest_initialized = False
        exit_code = 1

    try:
        raw_keys = [k for k in storage.list_keys("raw/") if is_raw_investor_estimate_key(k)]
    except Exception as exc:
        logger.exception("raw 목록 조회 실패")
        raw_keys = []
        failures.append({"raw_key": None, "reasons": ["raw_list_error"], "error": str(exc)})
        exit_code = 1
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    for raw_key in raw_keys:
        try:
            vendor = parse_raw_investor_key(raw_key)["source"]
            lines = storage.get_bytes(raw_key).decode("utf-8").splitlines()
        except Exception as exc:
            logger.exception("raw 읽기/키 파싱 실패: %s", raw_key)
            failures.append({"raw_key": raw_key, "reasons": ["raw_read_error"], "error": str(exc)})
            exit_code = 1
            continue
        for line in lines:
            if not line.strip():
                continue
            read += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                failures.append({"raw_key": raw_key, "reasons": ["unparseable_json"]})
                continue
            if not isinstance(record, dict):
                # 유효 JSON 이지만 객체가 아닌 행은 _normalize 의 raw.get 에서 AttributeError 로
                # 런을 죽인다 — 한 행이 검증 잡을 무너뜨리지 않게 격리한다(Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor != "kis":
                # 알 수 없는 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(현재 KIS 단독).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row, reasons = _normalize(vendor, record)
            except Exception as exc:
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue
            if reasons:
                failures.append({
                    "market": row["market"], "ticker": row["ticker"],
                    "trade_date": row["trade_date"], "asof_slot": row["asof_slot"],
                    "source_vendor": vendor, "reasons": reasons, "raw_key": raw_key,
                })
                continue
            passing.append(row)

    partitions: list[dict] = []
    canonical_rows = 0
    canonical_written = False
    if manifest_initialized:
        try:
            partitions, canonical_rows = _write_canonical(storage, passing)
            canonical_written = True
        except Exception:
            logger.exception("canonical 적재 실패")
            exit_code = 1

    quality_written = True
    try:
        storage.put_bytes(
            quality_log_key(DATASET, checked_date, run_id),
            json.dumps({
                "run_id": run_id,
                "job_name": JOB_NAME,
                "dataset": DATASET,
                "input_run_id": input_run_id,
                "raw_files": len(raw_keys),
                "records_read": read,
                "records_passed": len(passing),
                "records_failed": len(failures),
                # 원장 관측용 공통 봉투(ALPHA-181) — 통과 행이 산출, 탈락 행이 유실이다.
                "ops": {"records_out": len(passing), "failed_records": len(failures)},
                "failures": failures,
                "canonical_written": canonical_written,
                "canonical_partitions": partitions,
                "canonical_partitions_written": len(partitions),
                "canonical_rows_written": canonical_rows,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        quality_written = False
        exit_code = exit_code or 1

    # 행 실패는 다른 성공 winner의 계보를 폐기하지 않는다. canonical과 quality가 온전히
    # 기록됐으면 성공 범위를 commit하되, 아래 반환 코드는 비0으로 부분 실패를 SFN에 드러낸다.
    if canonical_written and quality_written and exit_code != 1:
        try:
            storage.put_bytes(manifest_key, _manifest_bytes(run_id, True, partitions))
        except Exception:
            logger.exception("canonical run manifest 기록 실패")
            exit_code = 1

    if failures and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    logger.info(
        "normalize_investor_estimate 완료: raw_files=%d read=%d passed=%d failed=%d "
        "canonical_parts=%d canonical_rows=%d",
        len(raw_keys), read, len(passing), len(failures), len(partitions), canonical_rows,
    )
    return exit_code
