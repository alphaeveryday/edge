"""ETF 구성종목 정제 Step2 — 정규화 + fact 게이트 + 전량 스냅샷 교체 (ALPHA-1059).

raw etf_holdings(FMP·KRX 두 벤더, 이형 스키마)를 읽어 **공통 구성종목 fact 행으로 정규화**
하고(ETF 식별자·구성종목·비중·기준일), 게이트(quality/etf.validate_etf_holding)를 통과하는지
검사한다. 검증 결과는 data_quality_logs 로 남긴다(각도 H — 조용히 새거나 사라지지 않게, Rule 12).

가격 정제(normalize_price)의 벤더 라우팅(source= 로 판별)과 공시 fact 정제(normalize_
disclosure_segment)의 blocking/경고 분리·canonical 멱등 병합을 합친 형태다:
  - 정체성(market·etf_id·구성종목·as_of_date)이 없으면 blocking(해당 수집본 교체 보류).
  - 비중·주식수·평가금액은 참고 필드 — KRX 해외기초 ETF 는 비중·평가금액을 대시(-)로 줘
    정규화가 null 로 정리하고(결측은 경고 안 냄), 값이 있어도 범위 이상은 경고로만 표면화한다.
    구성종목 자체는 보존해 canonical 로 넘긴다(멤버십·주식수는 유효 신호, 비중 파생은 다운스트림).

정규화가 흡수하는 벤더 이형(raw 무변형으로 보존된 원본):
  - FMP(US): asset·name·isin·sharesNumber·weightPercentage·marketValue·updatedAt(datetime)
  - KRX(KR): COMPST_ISU_CD·COMPST_ISU_NM·COMPST_ISU_CD2·COMPST_ISU_CU1_SHRS·COMPST_RTO·
    VALU_AMT·MKT_ID·SECUGRP_ID + trd_dd(우리가 지정한 기준일). 수치는 콤마 포함 문자열일
    수 있어 정규화가
    흡수하고, MKT_ID(STK/KSQ)는 MIC(ISO 10383)로 흡수한다 — FMP 는 거래소를 안 줘 MIC 가 None.
벤더 판별은 raw 키의 source= 파티션으로 한다(레코드 내용 아님 — 키가 규약의 SSOT).

정체성 키 (market,etf_id,constituent_ticker,as_of_date) 중 (market,as_of_date)가 파티션,
(etf_id,constituent_ticker)가 파티션 내 행 키다. market-스코프 파티션이라 한 파티션엔 한
벤더만 온다(US=fmp·KR=krx disjoint) — 가격 정제의 벤더 교차 충돌 가드가 여긴 불필요하다.
constituent 자동 수집(targets 확장)은 이 스텝 범위 밖 — canonical 이 '어떤 구성종목이 있나'의
단일 출처이고, 유니버스 확장은 별도 오케스트레이션의 명시적 결정이다(정규화는 targets 를 안 본다).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_etf_holdings_partition,
    canonical_run_manifest_key,
    collection_log_key,
    is_raw_etf_key,
    parse_raw_etf_key,
    quality_log_key,
)
from ..lake.latest_good import (
    PointerPlan,
    CollectionCheck,
    max_fetched_at,
    prepare_pointer,
    publish_pointer,
)
from ..parse import KR_MIC_BY_BOARD
from ..quality import BLOCKING_REASONS_ETF, validate_etf_holding

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_etf"
DATASET = "etf_holdings"

_FUTURE_SLACK_DAYS = 2
_PARTIAL_EXIT_CODE = 2

# market → 표준 통화. 통화는 FX 환산하지 않고 market 별로 태깅만 한다(가격 정제와 동형).
_CURRENCY = {"US": "USD", "KR": "KRW"}

# 벤더별 원본 키 → 공통 스키마 필드. 구성종목 식별자·이름·참고 수치를 잇는다(가격 _FIELD_MAP 동형).
_FMP_FIELDS = {
    "constituent_ticker": "asset", "constituent_isin": "isin", "constituent_name": "name",
    "weight_pct": "weightPercentage", "shares": "sharesNumber", "market_value": "marketValue",
}
_KRX_FIELDS = {
    "constituent_ticker": "COMPST_ISU_CD", "constituent_isin": "COMPST_ISU_CD2",
    "constituent_name": "COMPST_ISU_NM", "weight_pct": "COMPST_RTO",
    "shares": "COMPST_ISU_CU1_SHRS", "market_value": "VALU_AMT",
}

# KRX MKT_ID(벤더 거래소 구분) → MIC(ISO 10383, 국제표준 거래소 식별자).
#
# **어휘를 발명하지 않는다** — ADR-0027 이 `market_code = MIC` 를 이미 채택했고, 벤더 이형
# (STK/KSQ)을 표준으로 흡수하는 게 정규화의 정의다. 파티션의 `market=KR` 은 **지역**이지
# 거래소가 아니라서(ADR-0027) 이 정보를 대신하지 못한다 — 실측상 KODEX 반도체 구성종목 35종
# 중 **28종이 코스닥**이라, 시장을 지역으로 뭉개면 다운스트림이 틀린 거래소로 적재한다.
#
# 매핑 밖 값은 None 이 되고 호출부가 경고를 남긴다(Rule 12 — KRX 가 코드를 늘리면 알아야 한다).
# MIC **값**은 `parse.KR_MIC_BY_BOARD` 가 SSOT 다(ALPHA-829) — KRX 를 읽는 경로가 둘인데
# 시장을 아는 축이 달라(여기 응답의 MKT_ID, 공식 OpenAPI 는 어댑터가 부른 엔드포인트),
# 값을 각자 들면 한쪽만 고쳐도
# 안 잡히고 같은 종목이 두 market_code 로 마스터에 두 번 선다. 여기 표는 **벤더 코드→보드**
# 매핑만 담당한다.
_KRX_MIC_BY_MKT_ID = {
    "STK": KR_MIC_BY_BOARD["KOSPI"],  # 유가증권시장
    "KSQ": KR_MIC_BY_BOARD["KOSDAQ"],
    "KNX": KR_MIC_BY_BOARD["KONEX"],
}


def _text(record: dict, key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _krx_mic(record: dict) -> str | None:
    """KRX 구성종목의 거래소 MIC. 비상장(원화현금 등)은 MKT_ID 가 비어 None 이다.

    None 만으로 비상장 자산이라고 단정하지 않는다. 원화현금은 MKT_ID·SECUGRP_ID 가 모두
    비지만 새 시장 코드도 MIC 매핑 전에는 None 이므로, 자산 유형은 `_constituent_asset_type`이
    원본 코드 조합으로 별도 판정한다.
    """
    mkt_id = _text(record, "MKT_ID")
    if not mkt_id:
        return None
    if mkt_id == "DRV" and _text(record, "SECUGRP_ID") == "OP":
        return None
    mic = _KRX_MIC_BY_MKT_ID.get(mkt_id)
    if mic is None:
        # 조용히 None 으로 뭉개면 새 시장이 생겼을 때 전 종목이 시장 없이 적재된다.
        logger.warning("알 수 없는 KRX MKT_ID=%r — MIC 없이 통과(매핑 추가 필요)", mkt_id)
    return mic


def _constituent_asset_type(vendor: str, record: dict) -> str:
    """벤더 원본 코드 → 적재 지원 여부를 판단할 최소 자산 어휘.

    KRX 2026-08-24 실측 조합만 확정적으로 분류한다. 나머지를 지원 제외로 낙관하지 않고
    UNKNOWN으로 남겨 하류의 failed_records 그물이 계속 작동하게 한다.
    """
    if vendor != "krx":
        return "UNKNOWN"
    raw_secugrp_id = record.get("SECUGRP_ID")
    raw_mkt_id = record.get("MKT_ID")
    secugrp_id = raw_secugrp_id if isinstance(raw_secugrp_id, str) else None
    mkt_id = raw_mkt_id if isinstance(raw_mkt_id, str) else None
    ticker = _text(record, "COMPST_ISU_CD")
    if secugrp_id == "ST" and mkt_id in _KRX_MIC_BY_MKT_ID:
        return "EQUITY"
    if ticker == "KRD010010001" and secugrp_id == "" and mkt_id == "":
        return "CASH"
    if secugrp_id == "OP" and mkt_id == "DRV":
        return "OPTION"
    return "UNKNOWN"


def _ref_number(value: object) -> float | None:
    """참고 수치(비중·주식수·평가금액) → finite float 또는 None.

    KRX 해외기초의 대시(-)·결측·콤마문자열·비수치·비유한(NaN/Inf)을 전부 흡수한다 — 참고
    필드라 못 쓰는 값은 canonical 을 오염시키지 않게 null 로 정리한다(가격 _adj_close 위생과
    동형). bool 은 float(True)=1.0 로 조용히 통과하므로 수치 아님으로 본다(각도 H)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        # KRX 는 수치를 콤마 포함 문자열('1,234')·대시('-')로 줄 수 있다 — 흡수한다.
        s = value.strip().replace(",", "")
        if not s or s == "-":
            return None
        value = s
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _norm_as_of(vendor: str, record: dict) -> str | None:
    """기준일(as-of) → 'YYYY-MM-DD'. 결측·형식불량은 None(게이트가 missing/bad 로 잡음).

    FMP updatedAt 은 datetime('2026-07-11 09:07:03')이라 앞 10자(날짜부)를, KRX trd_dd 는
    'YYYYMMDD'라 그대로 파싱한다. strptime 왕복 검증으로 '20260231'·'202671' 같은 비달력일·
    미패딩을 막는다(가격·사업부문 정제 동형)."""
    if vendor == "fmp":
        raw = record.get("updatedAt")
        if not isinstance(raw, str) or not raw.strip():
            return None
        text, fmt = raw.strip()[:10], "%Y-%m-%d"
    else:  # krx
        raw = record.get("trd_dd")
        if not isinstance(raw, str) or not raw.strip():
            return None
        text, fmt = raw.strip(), "%Y%m%d"
    try:
        parsed = datetime.strptime(text, fmt).date()
    except ValueError:
        return None
    if parsed.strftime(fmt) != text:
        return None
    return parsed.isoformat()


def _normalize(vendor: str, record: dict) -> dict:
    """벤더 raw 행 → 공통 구성종목 fact 행. 검증은 하지 않는다(게이트 소관, 사업부문 정제 동형).

    market·etf_id 는 수집 provenance(ingest 가 붙인 our_etf_id·market)에서, 구성종목·참고
    수치는 벤더별 _FIELDS 로, 기준일은 _norm_as_of 로 만든다."""
    fields = _FMP_FIELDS if vendor == "fmp" else _KRX_FIELDS
    market = record.get("market")
    return {
        "market": market,
        "etf_id": _text(record, "our_etf_id"),
        "constituent_ticker": _text(record, fields["constituent_ticker"]),
        "constituent_isin": _text(record, fields["constituent_isin"]),
        "constituent_name": _text(record, fields["constituent_name"]),
        # 거래소 MIC — KRX 만 준다. FMP 는 거래소·자산유형 필드를 아예 안 줘(실측: symbol·
        # asset·name·isin·securityCusip·sharesNumber·weightPercentage·marketValue·updatedAt
        # 이 전부) None 이다. 한 벤더만 채우는 nullable 은 기존 관례(해외기초 대시 비중)와 같다.
        "constituent_mic": _krx_mic(record) if vendor == "krx" else None,
        "constituent_asset_type": _constituent_asset_type(vendor, record),
        "weight_pct": _ref_number(record.get(fields["weight_pct"])),
        "shares": _ref_number(record.get(fields["shares"])),
        "market_value": _ref_number(record.get(fields["market_value"])),
        # market 이 문자열일 때만 통화 조회 — 배열/객체 market 을 dict 키로 쓰면 TypeError 로 런이
        # 죽는다. 비문자열 market 은 게이트가 missing_market 으로 잡으므로 여기선 통화만 None.
        "currency": _CURRENCY.get(market) if isinstance(market, str) else None,
        "as_of_date": _norm_as_of(vendor, record),
        "source_vendor": vendor,
        "fetched_at": _text(record, "fetched_at"),
    }


# ── canonical 적재 ───────────────────────────────────────
_CANONICAL_COLUMNS = (
    "market", "etf_id", "constituent_ticker", "constituent_isin", "constituent_name",
    "constituent_mic", "constituent_asset_type", "weight_pct", "shares", "market_value",
    "currency", "as_of_date",
    "source_vendor", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([
        ("market", pa.string()), ("etf_id", pa.string()),
        ("constituent_ticker", pa.string()), ("constituent_isin", pa.string()),
        ("constituent_name", pa.string()), ("constituent_mic", pa.string()),
        ("constituent_asset_type", pa.string()),
        ("weight_pct", pa.float64()),
        ("shares", pa.float64()), ("market_value", pa.float64()),
        ("currency", pa.string()), ("as_of_date", pa.string()),
        ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])


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


def _fetched_at(row: dict) -> datetime:
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else _OLDEST


def _snapshot_rows(rows: list[dict]) -> list[dict]:
    """한 전량 수집본의 구성종목을 정렬한다. 중복·시각 혼합은 전량성 위반이다."""
    if len({row["as_of_date"] for row in rows}) != 1:
        raise ValueError("ETF snapshot has mixed as_of_date")
    if len({_fetched_at(row) for row in rows}) != 1:
        raise ValueError("ETF snapshot has mixed fetched_at")
    if len({row["constituent_ticker"] for row in rows}) != len(rows):
        raise ValueError("ETF snapshot has duplicate constituents")
    return sorted(rows, key=lambda row: row["constituent_ticker"])


def _same_snapshot(left: list[dict], right: list[dict]) -> bool:
    """런 provenance를 제외한 canonical 전체 내용이 같은지 비교한다."""
    def _values(rows):
        return sorted((tuple(row.get(column) for column in _CANONICAL_COLUMNS) for row in rows),
                      key=lambda values: values[2])
    return _values(left) == _values(right)


def _merge_partition(
    existing: list[dict], new_rows: list[dict], *, rebuild: bool = False,
) -> list[dict]:
    """검증된 수집본을 ETF 단위로 교체한다(ALPHA-1059). 다른 ETF·최신 정정은 보존한다.

    new_rows의 _collection_key는 raw의 source/run 정체성이다. 전체 복구에서도 서로 다른
    수집본을 행별 union하지 않는다. 같은 시각의 다른 raw 내용은 순서를 증명할 수 없어 거부한다.
    전체 raw 복구(rebuild)는 선택된 전량본으로 기존 오염도 교체한다. 스코프 실행은 역행하지 않는다.
    """
    acc: dict[str, list[dict]] = defaultdict(list)
    seen = set()
    for row in existing:
        identity = tuple(row.get(column) for column in _CANONICAL_COLUMNS)
        if identity not in seen:
            acc[row["etf_id"]].append(row)
            seen.add(identity)
    snapshots: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in new_rows:
        snapshots[(row["etf_id"], row["_collection_key"])].append(row)
    # 같은 ETF의 최신 완전본 하나를 먼저 고른다. 중간 정정은 canonical에 노출하지 않는다.
    selected: dict[str, list[dict]] = {}
    for (etf_id, _), rows in sorted(
        snapshots.items(), key=lambda item: max(_fetched_at(row) for row in item[1]), reverse=True,
    ):
        rows = _snapshot_rows(rows)
        previous = selected.get(etf_id)
        if previous is None or _fetched_at(rows[0]) > _fetched_at(previous[0]):
            selected[etf_id] = rows
        elif _fetched_at(rows[0]) == _fetched_at(previous[0]) and not _same_snapshot(rows, previous):
            raise ValueError(f"ETF snapshot same-order conflict: {etf_id}")
    if rebuild and acc.keys() - selected.keys():
        raise ValueError("full recovery cannot certify existing ETFs without a complete raw snapshot: "
                         + ",".join(sorted(acc.keys() - selected.keys())))
    for etf_id, rows in selected.items():
        previous = acc.get(etf_id, [])
        previous_time = max((_fetched_at(row) for row in previous), default=_OLDEST)
        candidate_time = _fetched_at(rows[0])
        if rebuild or candidate_time > previous_time:
            acc[etf_id] = rows
        elif candidate_time == previous_time and not _same_snapshot(rows, previous):
            raise ValueError(f"ETF snapshot same-order conflict: {etf_id}")
    return [row for etf_id in sorted(acc) for row in sorted(
        acc[etf_id], key=lambda row: row["constituent_ticker"],
    )]


def _write_canonical(
    storage: Storage, passing: list[dict], *, rebuild: bool = False,
    recovery_versions: dict[str, str | None] | None = None,
) -> tuple[list[dict[str, str]], int, list[tuple[str, str, bytes, list[dict]]]]:
    """검증된 전량 수집본을 파티션별로 ETF 단위 교체해 쓴다.
    반환: (쓴 파티션 식별자, 쓴 행 수, 이 실행이 직렬화한 merged snapshot 후보)."""
    by_partition: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in passing:
        by_partition[(row["market"], row["as_of_date"])].append(row)

    partitions: list[dict[str, str]] = []
    candidates: list[tuple[str, str, bytes, list[dict]]] = []
    rows_written = 0
    for (market, as_of_date), new_rows in sorted(by_partition.items()):
        prefix = canonical_etf_holdings_partition(market, as_of_date)
        target_key = f"{prefix}/part-00000.parquet"
        current, version = storage.get_bytes_with_version(target_key)
        if recovery_versions is not None and version != recovery_versions.get(target_key):
            raise ValueError(f"canonical changed during raw recovery: {target_key}")
        existing = _read_parquet_rows(current) if current is not None else []
        # 이전 시도가 target 쓰기 뒤 구형 part 삭제에서 실패했을 수 있다. 이번 전량 raw와
        # 정확히 일치하는 target ETF에는 구형 part를 다시 합치지 않고 정리만 재시도한다.
        incoming = _merge_partition([], new_rows)
        confirmed = {
            etf_id for etf_id in {row["etf_id"] for row in incoming}
            if _same_snapshot([row for row in existing if row["etf_id"] == etf_id],
                              [row for row in incoming if row["etf_id"] == etf_id])
        }
        partition_keys = []
        for key in storage.list_keys(prefix + "/"):
            relative = key.removeprefix(prefix + "/")
            if relative.startswith("part-") and relative.endswith(".parquet") and "/" not in relative:
                partition_keys.append(key)
                if key != target_key:
                    existing.extend(row for row in _read_parquet_rows(storage.get_bytes(key))
                                    if row["etf_id"] not in confirmed)
        merged = _merge_partition(existing, new_rows, rebuild=rebuild)
        data = _write_parquet_rows(merged)
        if not storage.put_bytes_if_version(target_key, data, version):
            raise ValueError(f"canonical snapshot concurrent write: {target_key}")
        # canonical 파티션은 단일 파일 계약이다. 구형 part를 남기면 로더가 새 스키마와 함께
        # 다시 읽어 중복·UNKNOWN 유실로 계측한다. 새 파일 기록 성공 뒤라 교체 중 데이터도 잃지 않는다.
        stale_keys = [key for key in partition_keys if key != target_key]
        if stale_keys:
            storage.delete_keys(stale_keys)
        partitions.append({"market": market, "as_of_date": as_of_date})
        candidates.append((market, as_of_date, data, merged))
        rows_written += len(merged)
    return partitions, rows_written, candidates


def _collection_key(raw_key: str) -> str:
    """KR/FMP raw와 정확히 짝인 수집 로그 key."""
    parsed = parse_raw_etf_key(raw_key)
    return collection_log_key(parsed["source"], DATASET, parsed["ingest_date"], parsed["run_id"])


def _complete_rows(
    storage: Storage, raw_keys: list[str], passing: list[dict], failures: list[dict],
    read_counts: dict[str, int], raw_sha256: dict[str, str],
) -> tuple[list[dict], CollectionCheck]:
    """전량 증거가 있는 수집 런만 반영한다. 부분 행은 raw에 보존하고 canonical은 유지한다."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for raw_key in raw_keys:
        try:
            grouped[_collection_key(raw_key)].append(raw_key)
        except (KeyError, ValueError):
            # run의 raw_read_error가 이미 기록된 경로다. 품질 로그까지 진행한다.
            continue
    bad_raw = {failure.get("raw_key") for failure in failures}
    accepted: set[str] = set()
    by_collection: dict[str, dict[tuple, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in passing:
        by_collection[row["_collection_key"]][(row["market"], row["etf_id"])].append(row)
    statuses = []
    for key, keys in sorted(grouped.items()):
        status = "unverified"
        try:
            log = json.loads(storage.get_bytes(key))
            parsed = parse_raw_etf_key(keys[0])
            status = log.get("status")
            count = sum(read_counts.get(raw_key, 0) for raw_key in keys)
            complete = (
                status == "success" and count > 0
                and log.get("run_id") == parsed["run_id"]
                and log.get("source_vendor") == parsed["source"]
                and all(type(log.get(field)) is int and log[field] == count
                        for field in ("records_fetched", "records_saved"))
                and type(log.get("records_failed_etfs")) is int
                and log["records_failed_etfs"] == 0
                and log.get("raw_sha256") == {raw_key: raw_sha256.get(raw_key) for raw_key in keys}
                and all(raw_key in raw_sha256 for raw_key in keys)
            )
            if not complete:
                raise ValueError("collection status/identity/count/hash does not prove a full snapshot")
            for rows in by_collection[key].values():
                _snapshot_rows(rows)
        except Exception as exc:
            failures.append({"raw_key": keys[0], "collection_log_key": key,
                             "reasons": ["incomplete_collection"], "error": str(exc)})
            continue
        finally:
            statuses.append(status)
        if not bad_raw.intersection(keys):
            accepted.add(key)
    return ([row for row in passing if row["_collection_key"] in accepted],
            CollectionCheck(tuple(sorted(grouped)), tuple(statuses), len(accepted) == len(grouped)))


def _prepare_latest_good(
    storage: Storage, run_id: str,
    candidates: list[tuple[str, str, bytes, list[dict]]],
) -> PointerPlan | None:
    """이번 실행이 쓴 KR 파티션 중 (as_of_date,max_fetched_at) 최신 snapshot을 준비한다."""
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
    """raw etf_holdings → canonical + latest-good pointer. 성공 0, partial 2, fatal 1.

    input_run_id 지정 시 **그 수집 런의 raw 만** 읽어 canonical 을 멱등 적재한다
    (ALPHA-389 — SFN 이 이 경로로 돈다). 이 run-scoped 경로만 포인터를 전진시킨다.
    미지정 전체 복구는 검증된 최신 전량본으로 shared canonical의 기존 오염도 교체한다.
    전체 복구는 포인터를 전진시키지 않는다. 누락·실패 런은 품질 로그와 비0 종료로 드러낸다."""
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    max_as_of_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    failures: list[dict] = []
    exit_code = 0
    raw_list_ok = True
    recovery_versions = None
    try:
        if input_run_id is None:
            # raw inventory 이후 완료된 정상 정정을 전체 복구의 과거 후보로 덮지 않는다.
            recovery_versions = {
                key: storage.get_bytes_with_version(key)[1]
                for key in storage.list_keys("canonical/holdings/etf_holdings/")
                if key.endswith("/part-00000.parquet")
            }
        raw_keys = [k for k in storage.list_keys("raw/") if is_raw_etf_key(k)]
    except Exception as exc:
        logger.exception("raw 목록 조회 실패")
        raw_keys = []
        failures.append({"raw_key": None, "reasons": ["raw_list_error"], "error": str(exc)})
        exit_code = 1
        raw_list_ok = False
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = 0
    warnings: list[dict] = []
    passing: list[dict] = []
    read_counts: dict[str, int] = defaultdict(int)
    raw_sha256: dict[str, str] = {}

    for raw_key in raw_keys:
        try:
            raw_partition = parse_raw_etf_key(raw_key)
            vendor = raw_partition["source"]
            data = storage.get_bytes(raw_key)
            raw_sha256[raw_key] = hashlib.sha256(data).hexdigest()
            lines = data.decode("utf-8").splitlines()
        except Exception as exc:
            logger.exception("raw 읽기/키 파싱 실패: %s", raw_key)
            failures.append({"raw_key": raw_key, "reasons": ["raw_read_error"], "error": str(exc)})
            exit_code = 1
            continue
        for line in lines:
            if not line.strip():
                continue
            read += 1
            read_counts[raw_key] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                failures.append({"raw_key": raw_key, "reasons": ["unparseable_json"]})
                continue
            if not isinstance(record, dict):
                # 유효 JSON 이지만 객체가 아닌 행(null·배열·스칼라)은 _normalize 의 record.get 에서
                # AttributeError 로 런 전체를 죽인다 — 행 단위 실패로 격리한다(각도 H, Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor not in ("fmp", "krx"):
                # 알 수 없는 ETF 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row = _normalize(vendor, record)
                reasons = validate_etf_holding(row, max_as_of_date=max_as_of_date)
            except Exception as exc:
                # 예기치 못한 행 단위 크래시도 배치를 죽이지 않게 격리한다(Rule 12).
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue

            if row["market"] != raw_partition["market"] or (vendor, row["market"]) not in {
                ("krx", "KR"), ("fmp", "US"),
            }:
                failures.append({"raw_key": raw_key, "reasons": ["source_market_mismatch"]})
                continue
            if _fetched_at(row) == _OLDEST:
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["bad_fetched_at"]})
                continue

            ref = {"market": row["market"], "etf_id": row["etf_id"],
                   "constituent_ticker": row["constituent_ticker"],
                   "as_of_date": row["as_of_date"], "source_vendor": vendor, "raw_key": raw_key}
            if any(isinstance(row[field], str) and row[field].strip()
                   and row[field] != row[field].strip()
                   for field in ("etf_id", "constituent_ticker")):
                failures.append({**ref, "reasons": ["identity_whitespace"]})
                continue
            blocking = [r for r in reasons if r in BLOCKING_REASONS_ETF]
            if blocking:
                failures.append({**ref, "reasons": reasons})
                continue
            row["_collection_key"] = _collection_key(raw_key)
            passing.append(row)
            warn = [r for r in reasons if r not in BLOCKING_REASONS_ETF]
            if warn:
                warnings.append({**ref, "reasons": warn})

    # 부분 런은 shared canonical도 건드리지 않는다. exit 2 뒤 하류가 실행돼도
    # 빈 manifest를 읽어 DB status/version을 그대로 유지한다.
    eligible, collection_check = _complete_rows(
        storage, raw_keys, passing, failures, read_counts, raw_sha256,
    )
    partitions: list[dict[str, str]] = []
    candidates: list[tuple[str, str, bytes, list[dict]]] = []
    canonical_rows = 0
    canonical_written = False
    if raw_list_ok and exit_code != 1:
        try:
            partitions, canonical_rows, candidates = _write_canonical(
                storage, eligible, rebuild=input_run_id is None, recovery_versions=recovery_versions,
            )
            storage.put_bytes(
                canonical_run_manifest_key(DATASET, run_id),
                json.dumps({
                    "run_id": run_id,
                    "job_name": JOB_NAME,
                    "canonical_written": True,
                    "canonical_partitions": partitions,
                }, ensure_ascii=False).encode("utf-8"),
            )
            canonical_written = True
        except Exception:
            logger.exception("canonical 적재 실패")
            exit_code = 1

    collection_incomplete = collection_check is not None and not collection_check.complete
    if (failures or collection_incomplete) and exit_code == 0:
        exit_code = _PARTIAL_EXIT_CODE

    plan: PointerPlan | None = None
    pointer_error = None
    if input_run_id is None:
        pointer_action = "retain_unscoped_recovery"
    elif exit_code == 1:
        pointer_action = "retain_fatal"
    elif exit_code == _PARTIAL_EXIT_CODE:
        pointer_action = "retain_partial"
    elif not raw_keys or not partitions:
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
    quality_key = quality_log_key(DATASET, checked_date, run_id)
    quality_payload = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "dataset": DATASET,
        "input_run_id": input_run_id,
        "raw_files": len(raw_keys),
        "records_read": read,
        "records_passed": len(passing),
        "records_eligible": len(eligible),
        "records_failed": len(failures),
        "ops": {"records_out": len(eligible), "failed_records": len(failures)},
        "records_warned": len(warnings),
        "failures": failures,
        "warnings": warnings,
        "canonical_written": canonical_written,
        "canonical_partitions": partitions,
        "canonical_partitions_written": len(partitions),
        "canonical_rows_written": canonical_rows,
        "latest_good": latest_good,
        "exit_code": exit_code,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
    }
    try:
        storage.put_bytes(quality_key, json.dumps(quality_payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        return 1

    # alias CAS가 마지막 필수 storage mutation이다. 성공 뒤 quality/manifest를 다시 쓰지 않는다.
    if plan is not None and plan.action == "advance" and exit_code == 0:
        try:
            publish_pointer(storage, plan)
        except Exception as exc:
            logger.exception("latest-good pointer CAS publish 실패")
            exit_code = 1
            quality_payload["exit_code"] = 1
            quality_payload["latest_good"]["pointer_publish_error"] = str(exc)
            quality_payload["finished_at"] = datetime.now(timezone.utc).isoformat()
            try:
                storage.put_bytes(
                    quality_key, json.dumps(quality_payload, ensure_ascii=False).encode("utf-8"),
                )
            except Exception:
                logger.exception("pointer 실패 뒤 quality_log 최종 상태 정정 실패")

    logger.info(
        "normalize_etf 완료: raw_files=%d read=%d passed=%d failed=%d warned=%d "
        "canonical_parts=%d canonical_rows=%d",
        len(raw_keys), read, len(passing), len(failures), len(warnings),
        len(partitions), canonical_rows,
    )
    return exit_code
