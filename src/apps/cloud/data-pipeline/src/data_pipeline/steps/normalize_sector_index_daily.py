"""업종지수 일봉 정제 Step2 — 정규화 + fact 게이트 + canonical 멱등 병합 (ALPHA-1254).

raw sector_index_daily(KIS 단일 벤더)를 fact 행(market·code·trade_date·OHLC)으로 정규화해
`canonical/market_data/sector_index_daily` 에 쓴다. 병합·manifest·재시도 장치는 NAV 정제의
것을 그대로 쓴다(`normalize_etf_nav.FactDataset`) — KIS 단일 벤더·KR 단일 시장·거래일 grain·
행 키 하나라는 형상이 같고, 갈리는 것은 이름·경로·정규화·게이트뿐이다.

행 키 `code` 는 KRX 업종코드다(raw `index_code`). KIS 지수코드는 어댑터 밖으로 나오지 않는다.

싣지 않는 것:
  - 거래량·거래대금(`acml_vol`·`acml_tr_pbmn`) — 단위(천 주·백만 원으로 보인다)를 실측으로
    확정하지 못했다. 저장 계약은 수량을 주, 금액을 원으로 두므로 추정 단위로 환산하면 틀린
    값이 사실로 남는다. raw 에 원형으로 남아 있다.
  - 통화 — 지수 값은 금액이 아니라 포인트다.
"""

from __future__ import annotations

from ..lake import (
    Storage,
    canonical_sector_index_daily_partition,
    is_raw_sector_index_daily_key,
    parse_raw_sector_index_daily_key,
)
from ..quality import validate_sector_index_daily
from . import normalize_etf_nav as _core

JOB_NAME = "normalize_sector_index_daily"
DATASET = "sector_index_daily"

_COLUMNS = (
    "market", "code", "trade_date", "open", "high", "low", "close", "source_vendor", "fetched_at",
)


def _schema():
    import pyarrow as pa

    # 명시 고정 — 추론에 맡기면 all-None 컬럼이 null 타입이 돼 기존 파티션과 충돌한다.
    return pa.schema([
        ("market", pa.string()), ("code", pa.string()), ("trade_date", pa.string()),
        ("open", pa.float64()), ("high", pa.float64()),
        ("low", pa.float64()), ("close", pa.float64()),
        ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])


def _normalize(vendor: str, record: dict) -> dict:
    """raw 행 → fact 행. 검증은 게이트 소관이다. 수치는 NAV 와 같은 변환(finite-or-None)."""
    return {
        "market": record.get("market"),
        "code": _core._text(record, "index_code"),
        "trade_date": _core._norm_trade_date(record),
        "open": _core._nav_number(record.get("bstp_nmix_oprc")),
        "high": _core._nav_number(record.get("bstp_nmix_hgpr")),
        "low": _core._nav_number(record.get("bstp_nmix_lwpr")),
        "close": _core._nav_number(record.get("bstp_nmix_prpr")),
        "source_vendor": vendor,
        "fetched_at": _core._text(record, "fetched_at"),
    }


SPEC = _core.FactDataset(
    dataset=DATASET,
    job_name=JOB_NAME,
    vendor="kis",
    row_key="code",
    value_fields=("open", "high", "low", "close"),
    conflict_reason="same_timestamp_value_conflict",
    conflict_values_key="values",
    columns=_COLUMNS,
    schema=_schema,
    is_raw_key=is_raw_sector_index_daily_key,
    parse_raw_key=parse_raw_sector_index_daily_key,
    canonical_partition=canonical_sector_index_daily_partition,
    normalize=_normalize,
    validate=validate_sector_index_daily,
)


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw sector_index_daily → canonical. 성공 0, 행 부분 실패 2, 저장 실패 1."""
    return _core.run(storage, run_id, input_run_id, spec=SPEC)
