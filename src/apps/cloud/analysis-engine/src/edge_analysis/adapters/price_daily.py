"""일봉 가격 원장(``price_daily``) 적재기 - 원천은 로컬 5분봉 parquet.

왜 필요한가. 인과 하네스의 SQL 이 ``price_daily.simple_return`` 으로 시장초과수익률을
만든다(``causal_data.py`` 의 ``xs``/``ex`` CTE). 이 원장이 비면 AR 이 전부 NULL 이 되고
모든 셀이 UNCERTAIN 으로 떨어진다 - 엔진은 정상 동작하는데 결론만 사라지는 실패다.
그래서 클라우드 원장과 같은 스키마의 로컬 Postgres 를 실제 데이터로 채우는 경로를 둔다.

``data-pipeline`` 의 ``steps/load_price_daily.py`` 와 대상 테이블은 같지만 원천이 다르다
(그쪽은 canonical 레이크의 이미 일별인 가격, 이쪽은 로컬 원시 5분봉). 겹치는 건 UPSERT
규약뿐이라 그 규약만 맞춘다.

**volume 의미: 봉별 거래량이다(누적 아님).** 5분봉의 volume 이 일 누적일 수 있다는 의심을
독립 원천(``fmp_daily_kr/prices_daily_kr.parquet``)과 대조해 기각했다.

* 누적이면 하루 안에서 단조 증가해야 한다 - 005930 은 896 거래일 중 **893 일이 비단조**다.
* ``max(volume)/일봉 volume`` 중앙값 **0.09**(약 11배 과소) - 누적의 마지막 값이라면 1.0 이어야 한다.
* ``sum(volume)/일봉 volume`` 중앙값 **0.92**(무작위 10 종목 6,086 거래일, 2023-10 이후) - 봉별이다.

그래서 ``sum`` 을 쓴다. 다만 같은 대조에서 **2023-10-01 이전 volume 은 원천이 깨져 있다**:
sum/일봉 비가 2022-11~12 는 **114배**, 2023-01~09 는 **79배**다(마지막 오염일 2023-09-27,
첫 정상일 2023-10-04 로 경계가 깨끗하다). 100배 틀린 수를 넣으면 거래대금·회전율이 전부
오염되므로 그 구간 volume 은 NULL 로 둔다 - 모르는 것을 아는 척하지 않는다.
(과제 지시가 근거로 든 005930 2022-11-10 의 09:05 봉 volume=13,987,606 은 파일 첫날의
부분 수집분이자 이 오염 구간 값이다. 누적의 증거가 아니었다.)

``turnover_value``·``price_basis``·``adjusted_close_price`` 는 INSERT 컬럼에서 아예 뺀다.
5분봉은 기준가도 수정주가도 나르지 않아서다 - ``close * volume`` 은 거래대금의 근사일 뿐이고,
근사를 원장에 넣으면 소비처가 그걸 사실로 읽는다(``steps/load_price_daily.py`` 와 같은 근거).
컬럼을 빼면 다른 적재기가 채운 값을 NULL 로 덮지도 않는다.

종가의 충실도 한계를 기록해 둔다. 같은 대조에서 마지막 봉 close 가 일봉 종가와 일치하는
비율은 88~92% 다 - 원천에 그날의 뒤쪽 봉이 빠진 날(마지막 봉이 10:00·14:55 인 날)이 있어서다.
그 날은 마지막 봉이 곧 그날 우리가 아는 마지막 가격이므로 그대로 쓴다.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from itertools import groupby
from pathlib import Path
from typing import Any

from ..config import KST, PipelineError
from ..observability import log
from .classification import instrument_index
from .universe import bare_ticker

# 없는 컬럼을 조용히 NULL 로 채우면 원천 스키마가 바뀐 걸 적재량으로만 알게 된다.
_REQUIRED_COLUMNS = ("symbol", "datetime", "open", "high", "low", "close", "volume")
_DATA_VERSION_MAX = 50  # price_daily.data_version 은 VARCHAR(50)
_UNRESOLVED_SAMPLE = 10
# KRX 정규장 종료 15:30 KST(종가 단일가 15:20-15:30 포함, 5분봉의 마지막 봉도 15:30 이다).
# available_at 은 '이 관측을 쓸 수 있게 된 시각'이라 장 마감 전이면 미래를 본 셈이 된다.
_MARKET_CLOSE = time(15, 30)
# 이 날짜부터의 volume 만 믿는다(위 모듈 docstring 의 대조 근거). 앞 구간은 NULL.
_VOLUME_TRUSTED_FROM = date(2023, 10, 1)
# 종목당 커밋이면 파일 수(1272)만큼 fsync 가 늘어난다. 배치 크기는 여기 한 곳만 본다.
COMMIT_BATCH_FILES = 50

# 갱신 조건(``IS DISTINCT FROM``)을 걸지 않는다 - 값이 안 바뀐 재적재가 아무 행도 돌려주지
# 않으면 호출자의 "한 건도 안 붙었다" 판정이 정상 재실행을 실패로 읽는다.
_UPSERT = (
    "INSERT INTO price_daily (instrument_id, trade_date, close_price, simple_return,"
    " log_return, volume, available_at, data_version) VALUES %s"
    " ON CONFLICT (instrument_id, trade_date) DO UPDATE SET"
    " close_price = EXCLUDED.close_price, simple_return = EXCLUDED.simple_return,"
    " log_return = EXCLUDED.log_return, volume = EXCLUDED.volume,"
    " available_at = EXCLUDED.available_at, data_version = EXCLUDED.data_version"
    " RETURNING (xmax = 0)"
)


@dataclass(frozen=True, slots=True)
class DailyBar:
    """일자·종목 grain 의 일봉. ``open_price``·``high_price``·``low_price`` 는 집산 근거를
    검증하기 위해 들고 있다 - ``price_daily`` 에는 해당 컬럼이 없어 적재되지 않는다."""

    ticker: str
    trade_date: date
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float
    volume: int | None
    simple_return: float | None
    log_return: float | None


def _price(value: Any) -> float | None:
    """유한 양수만 가격으로 인정. 0·음수·NaN 종가는 ``ck_price_daily_values`` 가 거부하고
    수익률도 무한대·정의불가로 만든다 - 배치 전체가 죽기 전에 여기서 걸러낸다."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _trade_date(value: Any) -> date:
    """``'2022-11-10 09:00:00'``(KST) → 거래일. 시각은 봉 정렬에만 쓰고 버린다."""
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise PipelineError(f"unparsable datetime {text!r}") from exc


def _available_at(trade_date: date) -> datetime:
    """PIT 기준 시각. 거래일 장 마감(15:30 KST) - 그 전이면 종가를 미리 안 셈이 된다."""
    return datetime.combine(trade_date, _MARKET_CLOSE, KST)


def aggregate_daily(rows: Iterable[dict[str, Any]]) -> list[DailyBar]:
    """5분봉 행 → 일봉. 순수 함수(DB·파일 접근 없음)라 집산 경계를 테스트로 고정한다.

    일자당 open=첫 봉 open, high=max, low=min, close=**마지막 봉 close**, volume=봉별 합
    (2023-10-01 이전은 원천이 깨져 NULL - 모듈 docstring 의 근거).

    수익률은 **종목별 시계열의 직전 거래일** 종가 기준이다. 달력 전일을 쓰면 휴장 다음날이
    전부 NULL 이 되고, 원천에 없는 날을 0 으로 채우면 '움직이지 않았다'는 거짓이 된다.
    시계열 첫날은 직전 종가가 없으므로 NULL 이다 - 0 은 보합을 주장하는 거짓이다.

    종목이 섞인 파일도 같은 코드로 옳게 처리한다((티커, 일자)로 묶고 티커별로 이어붙인다) -
    수익률이 종목 경계를 넘으면 값이 조용히 엉킨다.
    """
    keyed = [(bare_ticker(str(row["symbol"])), str(row["datetime"]), row) for row in rows]
    keyed.sort(key=lambda item: (item[0], item[1]))

    bars: list[DailyBar] = []
    previous_close: dict[str, float] = {}
    for (ticker, stamp), group in groupby(keyed, key=lambda item: (item[0], item[1][:10])):
        day = [row for _, _, row in group]
        closes = [price for price in (_price(row["close"]) for row in day) if price is not None]
        if not closes:
            continue  # 그날 쓸 수 있는 종가가 없으면 원장 행을 만들지 않는다
        opens = [price for price in (_price(row["open"]) for row in day) if price is not None]
        highs = [price for price in (_price(row["high"]) for row in day) if price is not None]
        lows = [price for price in (_price(row["low"]) for row in day) if price is not None]

        trade_date = _trade_date(stamp)
        # ponytail: 부분 수집일(뒤쪽 봉 결측)의 종가는 근사다. 정확도가 문제가 되면
        # fmp_daily_kr/prices_daily_kr.parquet(독립 일봉 원천)으로 종가만 덮는 경로를 붙인다.
        close = closes[-1]
        volume = _daily_volume(day, trade_date)
        prior = previous_close.get(ticker)
        # close·prior 가 모두 양수라 비는 양수다 → simple_return > -1, log_return 유한.
        # ck_price_daily_values 의 수익률 조건은 이 성질로 자동 충족된다.
        bars.append(DailyBar(
            ticker=ticker,
            trade_date=trade_date,
            open_price=opens[0] if opens else None,
            high_price=max(highs) if highs else None,
            low_price=min(lows) if lows else None,
            close_price=close,
            volume=volume,
            simple_return=close / prior - 1.0 if prior is not None else None,
            log_return=math.log(close / prior) if prior is not None else None,
        ))
        previous_close[ticker] = close
    return bars


def _daily_volume(day: list[dict[str, Any]], trade_date: date) -> int | None:
    """일 거래량 = 봉별 volume 의 합. 신뢰 구간 밖이거나 음수 합이면 NULL(=모른다)."""
    if trade_date < _VOLUME_TRUSTED_FROM:
        return None
    total = 0
    for row in day:
        value = row.get("volume")
        if value is None:
            continue
        try:
            total += int(value)
        except (TypeError, ValueError):
            return None
    # BIGINT 이라 NaN/Inf 는 불가능하지만 음수는 원천 파손의 신호다. 넣으면 volume >= 0
    # CHECK 가 배치 전체를 죽인다.
    return total if total >= 0 else None


def read_daily_bars(path: str | Path) -> list[DailyBar]:
    """5분봉 parquet 한 개 → 일봉. DB 는 보지 않는다(티커 해소는 ``load_price_daily``)."""
    import pyarrow.parquet as pq  # 무거운 임포트는 호출 시점에 - classification 과 같은 규약

    table = pq.read_table(path)
    missing = [column for column in _REQUIRED_COLUMNS if column not in table.column_names]
    if missing:
        raise PipelineError(f"{path}: missing columns {missing}")
    return aggregate_daily(table.select(_REQUIRED_COLUMNS).to_pylist())


def source_version(path: str | Path) -> str:
    """원천 스냅샷 식별자. 5분봉 파일명은 티커라 버전이 못 되므로 수확 시각(mtime, KST)을 쓴다."""
    stamp = datetime.fromtimestamp(Path(path).stat().st_mtime, KST)
    return f"fmp_5min_{stamp:%Y%m%d}"


def load_price_daily(
    conn,
    bars: Iterable[DailyBar],
    *,
    source: str,
    data_version: str,
) -> dict[str, int]:
    """``price_daily`` UPSERT. **커밋은 호출자 몫**이다.

    신규/갱신은 ``xmax = 0`` 으로 가른다. upsert 의 ``rowcount`` 는 둘을 구분하지 못해
    "적재는 됐는데 왜 늘지 않았나"를 답할 수 없다.

    ``source`` 는 로그에만 남는다 - ``price_daily`` 에는 source 컬럼이 없다. 인자로 받는
    이유는 어느 원천이 이 건수를 만들었는지 운영 로그에서 답해야 하기 때문이다.

    Returns:
        loaded(신규)·updated(갱신)·unresolved(티커 미해소 행)·duplicate(배치 내 중복) 건수.
    """
    from psycopg2.extras import execute_values

    index = instrument_index(conn)
    version = str(data_version)[:_DATA_VERSION_MAX]  # 초과분은 잘라 적재 실패를 막는다

    # 같은 (instrument_id, trade_date) 가 배치에 두 번 오면 Postgres 가 "cannot affect row
    # a second time" 로 배치 전체를 거부한다. 마지막 행이 이기게 하고 건수로 드러낸다.
    values: dict[tuple[str, date], tuple] = {}
    unresolved: list[str] = []
    duplicate = 0
    for bar in bars:
        instrument_id = index.get(bar.ticker)
        if instrument_id is None:
            unresolved.append(bar.ticker)
            continue
        key = (instrument_id, bar.trade_date)
        if key in values:
            duplicate += 1
        values[key] = (
            instrument_id, bar.trade_date, bar.close_price, bar.simple_return,
            bar.log_return, bar.volume, _available_at(bar.trade_date), version,
        )

    counts = {"loaded": 0, "updated": 0, "unresolved": len(unresolved), "duplicate": duplicate}
    if values:
        with conn.cursor() as cur:
            returned = execute_values(cur, _UPSERT, list(values.values()), fetch=True) or []
        counts["loaded"] = sum(1 for row in returned if row[0])
        counts["updated"] = len(returned) - counts["loaded"]
    if unresolved:
        # 표본을 함께 남긴다 - 건수만 있으면 원천 티커 형식이 바뀐 건지 미상장인지 모른다.
        log("price_daily.unresolved", count=len(unresolved), source=source,
            sample=sorted(set(unresolved))[:_UNRESOLVED_SAMPLE])
    return counts
