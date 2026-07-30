"""산업분류 원장(``instrument_classification``) 적재기.

왜 필요한가. 이 원장이 비면 인과 경로의 코호트가 통째로 비고, 준거집단이 없으면 모든
셀이 UNCERTAIN 으로 떨어진다 - 엔진은 정상 동작하는데 결론만 사라지는 실패라 사후에
원인을 찾기 어렵다. 그래서 원장을 채우는 경로를 명시적으로 둔다.

파이프라인이 아니라 별도 운영 경로다. 파이프라인은 분석 산출물(observation/route/run/
result)의 단일 writer 이고(ADR-0005), 원장 적재는 주기도 트랜잭션 경계도 다르다.

원천은 로컬에서 정규화한 FMP 산업 맵 CSV 다. 파일명에 타임스탬프가 붙으므로 경로는
인자로 받는다 - 하드코딩하면 다음 스냅샷에서 조용히 옛 파일을 다시 적재한다.

결측은 NULL 로 넣는다. 빈 문자열은 ''라는 하나의 산업으로 묶여 준거집단을 조용히
오염시킨다(마이그레이션 V202607291720 의 CHECK 가 이걸 거부한다).

해소 안 되는 티커는 버리지 않고 센다. 조용히 건너뛰면 적재량이 왜 적은지 알 수 없다.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..config import KST, PgConfig, PipelineError
from ..observability import log

# instrument 의 자연키는 (market_code, ticker) UNIQUE 다. 티커만으로 조회하면 시장이 다른
# 동일 티커에 붙을 수 있으므로 자연키를 그대로 쓴다. KRX 는 MIC 코드 XKRX(엔티티 시드와 동일).
_MARKET_CODE = "XKRX"
_CSV_GLOB = "fmp_kr_stock_industry_map_*.csv"
# 없는 컬럼을 조용히 NULL 로 채우면 원천 스키마가 바뀐 걸 적재량으로만 알게 된다.
_REQUIRED_COLUMNS = (
    "ticker", "fmp_sector", "fmp_industry", "market_cap",
    "listing_market", "is_primary_share_class",
)
_DATA_VERSION_MAX = 50  # instrument_classification.data_version 은 VARCHAR(50)
_UNRESOLVED_SAMPLE = 10

_UPSERT = (
    "INSERT INTO instrument_classification (instrument_id, as_of_date, sector_name,"
    " industry_name, market_cap, listing_market, is_primary_share, source, available_at,"
    " data_version) VALUES %s"
    " ON CONFLICT (instrument_id, as_of_date) DO UPDATE SET"
    " sector_name = EXCLUDED.sector_name, industry_name = EXCLUDED.industry_name,"
    " market_cap = EXCLUDED.market_cap, listing_market = EXCLUDED.listing_market,"
    " is_primary_share = EXCLUDED.is_primary_share, source = EXCLUDED.source,"
    " available_at = EXCLUDED.available_at, data_version = EXCLUDED.data_version"
    " RETURNING (xmax = 0)"
)


def _text(value: Any) -> str | None:
    """공백만·빈 값은 ``None``. '' 를 그대로 넣으면 준거집단이 오염된다."""
    text = str(value).strip() if value is not None else ""
    return text or None


def _market_cap(value: Any) -> Decimal | None:
    """결측·음수·비유한·파싱불가는 ``None``.

    0 으로 채우지 않는다 - market_cap 은 균형검정(SMD)의 공변량이라 채워 넣은 값이
    비교군을 조용히 기울인다. 음수·Infinity 는 CHECK 가 거부하는 값이기도 하다.
    """
    raw = _text(value)
    if raw is None:
        return None
    try:
        cap = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return cap if cap.is_finite() and cap >= 0 else None


def _flag(value: Any) -> bool | None:
    """불리언 컬럼은 nullable 이다 - 해석 못 하는 값은 False 가 아니라 ``None``."""
    raw = (_text(value) or "").lower()
    if raw in ("true", "t", "1", "y", "yes"):
        return True
    if raw in ("false", "f", "0", "n", "no"):
        return False
    return None


def normalize_ticker(value: Any) -> str:
    """CSV 티커를 DB 티커(6자리)로 맞춘다.

    ``005930.KS``(FMP 접미)와 ``5930``(엑셀이 앞 0 을 먹은 형태)이 둘 다 온다. 접미를
    남기거나 0 을 채우지 않으면 전 종목이 미해소로 떨어져 원장이 비어 있는 것과 같아진다.
    """
    ticker = (_text(value) or "").split(".")[0].strip()
    return ticker.zfill(6) if ticker.isdigit() else ticker.upper()


def latest_industry_csv(directory: str | Path) -> Path:
    """디렉터리에서 가장 최신 산업 맵 CSV. ``YYYYMMDD_HHMMSS`` 는 사전순 = 시간순이다."""
    matches = sorted(Path(directory).glob(_CSV_GLOB))
    if not matches:
        raise PipelineError(f"no {_CSV_GLOB} under {directory}")
    return matches[-1]


def source_stamp(path: str | Path) -> datetime | None:
    """파일명 끝의 ``_YYYYMMDD_HHMMSS``(KST) → datetime, 없으면 ``None``.

    ``available_at`` 의 기본값 재료다. 적재 시각을 쓰면 원천이 실제로 존재했던 시점보다
    늦게 기록되고, 그러면 PIT 감사에서 "이때 이 값을 알 수 있었나"에 답할 수 없다.
    """
    parts = Path(path).stem.split("_")
    try:
        return datetime.strptime("_".join(parts[-2:]), "%Y%m%d_%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None


def read_industry_csv(path: str | Path) -> list[dict[str, Any]]:
    """FMP 산업 맵 CSV → 적재 행. DB 는 보지 않는다(티커 해소는 ``load_classification``).

    티커가 빈 행도 버리지 않는다 - 해소 단계에서 미해소로 세어져야 건수가 맞는다.
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:  # BOM 붙은 내보내기 대비
        reader = csv.DictReader(handle)
        missing = [c for c in _REQUIRED_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise PipelineError(f"{path}: missing columns {missing}")
        return [
            {
                "ticker": normalize_ticker(row.get("ticker")),
                "sector_name": _text(row.get("fmp_sector")),
                "industry_name": _text(row.get("fmp_industry")),
                "market_cap": _market_cap(row.get("market_cap")),
                "listing_market": _text(row.get("listing_market")),
                "is_primary_share": _flag(row.get("is_primary_share_class")),
            }
            for row in reader
        ]


def instrument_index(conn) -> dict[str, str]:
    """ticker → instrument_id. 자연키 ``(market_code, ticker)`` 로 한 시장만 읽는다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, instrument_id FROM instrument WHERE market_code = %s",
            (_MARKET_CODE,),
        )
        return {normalize_ticker(ticker): str(iid) for ticker, iid in cur.fetchall()}


def load_classification(
    conn,
    rows: list[dict[str, Any]],
    *,
    as_of_date: date | str,
    source: str,
    data_version: str,
    available_at: datetime | str,
) -> dict[str, int]:
    """``instrument_classification`` UPSERT. **커밋은 호출자 몫**이다.

    한 스냅샷은 한 트랜잭션이어야 한다 - 중간 커밋은 절반만 갱신된 원장을 남기고,
    그 상태로 만든 준거집단은 시점이 섞인다.

    신규/갱신은 ``xmax = 0`` 으로 가른다. upsert 의 ``rowcount`` 는 둘을 구분하지 못해
    "적재는 됐는데 왜 늘지 않았나"를 답할 수 없다.

    Returns:
        loaded(신규)·updated(갱신)·unresolved(티커 미해소)·duplicate(배치 내 중복) 건수.
    """
    from psycopg2.extras import execute_values

    index = instrument_index(conn)
    as_of = as_of_date.isoformat() if isinstance(as_of_date, date) else str(as_of_date)
    version = str(data_version)[:_DATA_VERSION_MAX]  # 초과분은 잘라 적재 실패를 막는다

    # 같은 (instrument_id, as_of_date) 가 배치에 두 번 오면 Postgres 가 "cannot affect row
    # a second time" 로 배치 전체를 거부한다. 마지막 행이 이기게 하고 건수로 드러낸다.
    values: dict[str, tuple] = {}
    unresolved: list[str] = []
    duplicate = 0
    for row in rows:
        instrument_id = index.get(normalize_ticker(row.get("ticker")))
        if instrument_id is None:
            unresolved.append(str(row.get("ticker") or ""))
            continue
        if instrument_id in values:
            duplicate += 1
        values[instrument_id] = (
            instrument_id, as_of, row.get("sector_name"), row.get("industry_name"),
            row.get("market_cap"), row.get("listing_market"), row.get("is_primary_share"),
            source, available_at, version,
        )

    counts = {"loaded": 0, "updated": 0, "unresolved": len(unresolved), "duplicate": duplicate}
    if values:
        with conn.cursor() as cur:
            returned = execute_values(cur, _UPSERT, list(values.values()), fetch=True) or []
        counts["loaded"] = sum(1 for row in returned if row[0])
        counts["updated"] = len(returned) - counts["loaded"]
    if unresolved:
        # 표본을 함께 남긴다 - 건수만 있으면 원천 티커 형식이 바뀐 건지 상장폐지인지 모른다.
        log("classification.unresolved", count=len(unresolved),
            sample=sorted(set(unresolved))[:_UNRESOLVED_SAMPLE])
    return counts


def connect(pg: PgConfig):
    """적재용 psycopg2 커넥션(+ search_path).

    ``EventStore`` 를 쓰지 않는다 - 그쪽은 분석 산출물 writer 라 커밋 시점이 다르고,
    원장 적재기가 얹히면 단일 writer 경계가 흐려진다. 스키마 문자열은 config 가 검증한다.
    """
    import psycopg2

    conn = psycopg2.connect(
        host=pg.host,
        port=pg.port,
        dbname=pg.dbname,
        user=pg.user,
        password=pg.password,
    )
    with conn.cursor() as cur:
        cur.execute(f"SET search_path TO {pg.schema}")
    return conn
