"""5분봉 수집 — FMP stable → raw parquet → canonical/market_data/intraday_5m.

왜 이 코드가 저장소에 있나
------------------------
`canonical/market_data/intraday_5m` 을 쌓은 주체는 `.tmp/collect/` 의 **git 미추적**
스크립트와 사람의 손이었다. 파티션은 2026-07-31 에서 멈췄고, 그 기계가 죽으면 5분봉을
갱신할 방법이 함께 사라진다. data-pipeline 레인에 canonical 5분봉 잡이 없으므로
(open-source-backfill §4 "data-pipeline 레인에 보고할 버그") 갱신 절차를 코드로 남긴다.
승격 원본은 비교용으로 `.tmp/collect/` 에 남겨 뒀다 — `fetch_5min_gap.py`(수집),
`normalize_intraday.py`(raw→canonical), `verify_intraday.py`·`_dup_check.py`·
`_verify_join.py`(검증).

왜 1주 청크 + 잘림 재분할인가
---------------------------
FMP stable `historical-chart` 는 응답에 **행 상한**이 있어 요청 구간이 길면 오래된 쪽을
말없이 잘라내고 최신분만 준다(docs/design/open-source-backfill.md §4 실측:
`005930.KS` 를 2026-07-17..07-31 로 부르면 07-22 부터만 오고, 07-17..07-21 로 좁히면
07-20·07-21 만 온다). 원 수집기가 이 상한에 잘려 raw 가 KR 2026-07-16 · US
2026-06-26 에서 끊겼는데, 응답은 200 이고 행도 있어서 **아무도 실패를 보지 못했다.**

그래서 세 겹으로 막는다.
1. 요청을 `CHUNK_DAYS`(1주) 이하로 쪼갠다.
2. 응답이 요청 구간의 **오래된 쪽을 덮는지** 검사한다(`truncation_reason`).
3. 못 덮으면 하루 단위까지 반씩 재분할해 다시 부른다 — 위 실측처럼 5일 요청도 잘리므로
   1주 분할만으로는 부족하고 **검출이 있어야** 한다.

빈 응답은 잘림이 아니다. 상장폐지·거래정지 종목은 실제로 0행이라, 둘을 같은 문장으로
흘리지 않고 사유를 나눠 센다(`CollectResult.empty` vs `notes`). "데이터가 없다"와
"수집이 잘렸다"가 한 문장으로 나오는 것이 이 표가 20라운드 싸운 실패 양식이다.

좌표계·단위 (승격 원본 실측 그대로 — 바꾸면 기존 파티션과 어긋난다)
--------------------------------------------------------------
- `ts`: 해당 시장의 **현지 벽시계 naive**. KR=KST(09:00~15:30), US=ET(09:30~15:55).
  `market` 열과 함께 읽어야 의미가 선다.
- `available_at = ts + 5분`. 봉이 닫히는 순간 값이 확정된다 — 근사가 아니다.
- `open/high/low/close`: 해당 시장 통화의 주당 가격 원값(KR=KRW, US=USD). `volume`: 주.
- `ticker`: KR 은 `.KS`/`.KQ` 접미사를 떼고(레이크 조인 키), US 는 원본 그대로.

사용
----
    python -m edge_analysis.collect.intraday --market kr --from 2026-08-01 --to 2026-08-04
    python -m edge_analysis.collect.intraday --market us --from 2026-08-03 --to 2026-08-03 \
        --symbols us_symbols.txt
    python -m edge_analysis.collect.intraday --market kr --from 2026-08-04 --to 2026-08-04 \
        --verify-only
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..adapters.lake import LAKE_PRICE_PREFIX
from ..config import PipelineError
from ..statics.core.duck import s3_secret_sql, session_pragmas

# v3 는 폐기다(sources.toml 규약) — stable 만 부른다.
FMP_URL = "https://financialmodelingprep.com/stable/historical-chart/5min"
FMP_SECRET_ID = "edge-dev-data-pipeline/fmp/api-key"   # JSON 의 .apikey
FMP_KEY_ENV = "FMP_API_KEY"
DEFAULT_REGION = "ap-northeast-2"
LAKE_BUCKET_ENV = "ALPHAMALE_LAKE_BUCKET"
DEFAULT_BUCKET = "edge-dev-pipeline-lake"

CANONICAL_PREFIX = "canonical/market_data/intraday_5m"
# raw 조각은 기존 prefix 를 그대로 쓴다. normalize_intraday.py 의 글롭이 이 두 경로를
# 함께 읽으므로, 새 prefix 를 만들면 전체 재정규화 경로가 이 수집분을 못 본다.
RAW_PREFIX = {"KR": "raw/kr_intraday/fmp_5min_gap", "US": "raw/fmp_5min_us_gap"}
VENDOR = "fmp"

CHUNK_DAYS = 7          # 요청 1건의 상한. 이보다 길면 응답이 잘린다.
MIN_CHUNK_DAYS = 1      # 재분할 바닥. 하루는 더 쪼갤 수 없다.
HTTP_TIMEOUT = 30
MAX_RETRY = 3
RETRY_STATUS = (429, 500, 502, 503, 504)
# 잘림 사유에만 붙는 표지. `CollectResult.truncations` 가 이것으로 세므로 문구를 바꾸면
# 집계도 함께 바뀐다 — 부재 사유("빈 응답")에는 절대 넣지 않는다.
TRUNCATION_MARK = "FMP 행 상한 잘림"

# 정규장 창(현지 벽시계). 봉 개수 기대치는 여기서 **유도한다** — 상수로 박으면
# 창이 바뀔 때 두 곳이 어긋난다.
SESSION_WINDOW = {"KR": ("09:00", "15:30"), "US": ("09:30", "15:55")}
# KR 만 접미사를 뗀다. US 원본은 접미사가 없다.
TICKER_EXPR = {"KR": r"regexp_replace(symbol, '\.(KS|KQ)$', '')", "US": "symbol"}

# 검증 문턱. 봉 개수는 **하한 비율**로 본다 - KR 은 종가 단일가(15:30) 앞에 빈 창이
# 있어 기대치와 정확히 같지 않다. 등호를 요구하면 정상일이 매일 실패한다.
MIN_BAR_RATIO = 0.9
MAX_PARTIAL_RATIO = 0.1


def _day(text: str) -> date:
    """``YYYY-MM-DD`` 파싱. 형식이 어긋나면 죽는다 — 날짜를 조용히 흘리면 구간이 밀린다."""
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PipelineError(f"invalid date {text!r}; expected YYYY-MM-DD") from exc


def expected_bars(market: str) -> int:
    """정규장 창을 5분으로 나눈 하루 봉 개수(양끝 포함). US=78 은 실측과 일치한다."""
    start, end = SESSION_WINDOW[market]
    span = (int(end[:2]) - int(start[:2])) * 60 + int(end[3:]) - int(start[3:])
    return span // 5 + 1


def bucket_url(prefix: str, *, bucket: str | None = None) -> str:
    """``s3://<버킷>/<prefix>``. 버킷은 env 로 받는다 — 하드코딩하면 계정을 못 옮긴다."""
    return f"s3://{bucket or os.environ.get(LAKE_BUCKET_ENV) or DEFAULT_BUCKET}/{prefix}"


def canonical_sql(market: str, trade_date: str | None = None, *,
                  base: str | None = None) -> str:
    """canonical intraday_5m 을 읽는 FROM 절. ``trade_date`` 를 주면 그 하루 파티션만.

    ``hive_partitioning=true``: market·trade_date 는 경로에만 있고 파일 안에는 없다.
    """
    root = base or bucket_url(CANONICAL_PREFIX)
    part = f"market={market}/trade_date={trade_date}" if trade_date else f"market={market}/**"
    return f"read_parquet('{root}/{part}/*.parquet', hive_partitioning=true)"


def daily_sql(market: str, trade_date: str, *, base: str | None = None) -> str:
    """canonical price_daily 의 하루 파티션 FROM 절(종가 대조용)."""
    root = base or bucket_url(LAKE_PRICE_PREFIX)
    return (f"read_parquet('{root}/market={market}/trade_date={trade_date}/*.parquet',"
            f" hive_partitioning=true)")


# ── 수집 ────────────────────────────────────────────────────────────────────

def week_chunks(date_from: str, date_to: str, days: int = CHUNK_DAYS) -> list[tuple[str, str]]:
    """요청 구간을 ``days`` 일 이하 조각으로 자른다(양끝 포함, 빈틈·겹침 없음).

    왜 자르나: FMP 행 상한에 걸리면 오래된 쪽이 조용히 사라진다(모듈 도크스트링).
    조각 경계는 거래일이 아니라 달력일로 센다 — 휴장일을 알 필요가 없고, 휴장 구간은
    빈 응답으로 돌아와 사유로 남는다.
    """
    if days < 1:
        raise PipelineError(f"chunk days must be >= 1, got {days}")
    start, end = _day(date_from), _day(date_to)
    if end < start:
        raise PipelineError(f"empty range {date_from}..{date_to}")
    out: list[tuple[str, str]] = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        out.append((cur.isoformat(), stop.isoformat()))
        cur = stop + timedelta(days=1)
    return out


def _halve(date_from: str, date_to: str) -> list[tuple[str, str]]:
    """구간을 앞·뒤 절반으로 쪼갠다. 하루면 쪼갤 수 없어 그대로 돌려준다."""
    start, end = _day(date_from), _day(date_to)
    span = (end - start).days + 1
    if span <= MIN_CHUNK_DAYS:
        return [(date_from, date_to)]
    return week_chunks(date_from, date_to, span // 2)


def _row_day(row: dict[str, Any]) -> str:
    """응답 행의 날짜 부분. FMP 는 ``date`` 에 ``'YYYY-MM-DD HH:MM:SS'`` 를 준다."""
    return str(row.get("date", ""))[:10]


def truncation_reason(rows: Sequence[dict[str, Any]], date_from: str,
                      date_to: str) -> str | None:
    """응답이 요청 구간의 **오래된 쪽**을 덮는지 본다. 못 덮으면 사유, 덮으면 None.

    FMP 는 상한을 넘기면 오래된 쪽을 자르고 최신분만 준다. 그래서 응답 최소일이 요청
    시작일보다 늦으면 잘림을 의심한다 — 휴장으로 실제로 늦게 시작하는 경우와 구분되지
    않지만, 그 구분은 **더 좁혀 다시 물어봐야만** 생긴다(그때 빈 응답이면 진짜 부재다).
    의심 쪽으로 기울이는 이유: 잘린 응답을 정상으로 믿으면 표가 조용히 비고, 한 번 더
    부르는 비용은 요청 몇 건이다.

    최신 쪽(``date_to`` 미달)은 보지 않는다. 상한은 오래된 쪽을 자르고, 최신 쪽 부재는
    아직 열리지 않은 장이라는 정상 상태다.

    빈 응답도 None 이다 — 잘림이 아니라 부재이고, 호출부가 따로 센다.
    """
    if not rows:
        return None
    got = min(_row_day(r) for r in rows if _row_day(r))
    if not got or got <= date_from:
        return None
    return (f"요청 {date_from}..{date_to} 인데 {got} 부터만 왔다"
            f"(행 {len(rows)}) — {TRUNCATION_MARK} 의심")


def _fetch_json(symbol: str, date_from: str, date_to: str, key: str) -> list[dict[str, Any]]:
    """5분봉 한 요청. 429/5xx 는 지수 백오프로 재시도하고, 끝내 실패하면 **죽는다**.

    원본은 모든 예외를 삼켜 ``[]`` 로 돌렸다. 그러면 인증 실패·쿼터 소진이 "이 종목은
    데이터가 없다"와 똑같이 보인다. 404(모르는 심볼)만 부재로 인정한다.
    """
    url = (f"{FMP_URL}?symbol={urllib.parse.quote(symbol)}&from={date_from}"
           f"&to={date_to}&apikey={urllib.parse.quote(key)}")
    for attempt in range(MAX_RETRY):
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            if exc.code in RETRY_STATUS and attempt < MAX_RETRY - 1:
                time.sleep(2 ** attempt)
                continue
            raise PipelineError(f"FMP {exc.code} {symbol} {date_from}..{date_to}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt < MAX_RETRY - 1:
                time.sleep(2 ** attempt)
                continue
            raise PipelineError(f"FMP 호출 실패 {symbol} {date_from}..{date_to}: {exc}") from exc
        if isinstance(payload, dict):   # {"Error Message": ...} 형태
            raise PipelineError(f"FMP 오류 응답 {symbol}: {str(payload)[:160]}")
        # FMP 는 symbol 을 응답에 넣지 않는다 — 요청한 심볼을 직접 박는다.
        for row in payload:
            row["symbol"] = symbol
        return payload
    raise PipelineError(f"FMP 재시도 소진 {symbol} {date_from}..{date_to}")


Fetcher = Callable[[str, str, str, str], list[dict[str, Any]]]


def collect_symbol(symbol: str, date_from: str, date_to: str, key: str = "", *,
                   fetch: Fetcher | None = None, days: int = CHUNK_DAYS,
                   ) -> tuple[list[dict[str, Any]], list[str]]:
    """한 종목의 구간 전체. 1주 청크로 부르고 잘리면 하루까지 반씩 재분할한다.

    잘린 응답은 **버리고** 절반을 다시 부른다. 절반들의 합집합이 잘린 응답을 포함하므로
    버려서 잃는 봉이 없고, 남겨두면 같은 봉이 두 번 쌓인다.

    반환: (행, 사유 목록). 사유는 잘림·재분할·빈 응답을 사람이 읽을 문장으로 남긴 것이다.
    """
    call = fetch or _fetch_json
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    pending = deque(week_chunks(date_from, date_to, days))
    while pending:
        frm, to = pending.popleft()
        got = call(symbol, frm, to, key)
        why = truncation_reason(got, frm, to)
        if why and (_day(to) - _day(frm)).days + 1 > MIN_CHUNK_DAYS:
            halves = _halve(frm, to)
            notes.append(f"{symbol} {why} → {len(halves)}조각 재분할")
            pending.extendleft(reversed(halves))
            continue
        if not got:
            notes.append(f"{symbol} {frm}..{to}: 빈 응답 — 데이터 부재")
        elif why:
            notes.append(f"{symbol} {why} → 하루라 더 못 쪼갠다")
        rows.extend(got)
    return rows, notes


@dataclass(frozen=True, slots=True)
class CollectResult:
    """수집 결과 + **부재의 사유**. 행만 돌려주면 0행과 실패가 구분되지 않는다.

    시장·구간은 담지 않는다 — 아무도 읽지 않는 필드는 여기서 결함이다. 호출부가 이미
    안다.
    """

    rows: list[dict[str, Any]]
    requested: int                                      # 요청한 심볼 수(부재 비율의 분모)
    empty: list[str] = field(default_factory=list)      # 0행으로 끝난 심볼
    notes: list[str] = field(default_factory=list)      # 잘림·재분할·부재 사유

    @property
    def truncations(self) -> list[str]:
        """잘림으로 판정된 사유만. 하나라도 있으면 재분할이 실제로 돌았다는 증거다."""
        return [n for n in self.notes if TRUNCATION_MARK in n]


def collect(date_from: str, date_to: str, symbols: Sequence[str], *,
            key: str = "", workers: int = 8, fetch: Fetcher | None = None,
            days: int = CHUNK_DAYS, log: Callable[[str], None] = print) -> CollectResult:
    """심볼 목록 전체를 청크 분할로 수집한다. 유니버스가 비면 죽는다.

    빈 유니버스로 0행을 쌓고 "데이터가 없다"고 말하는 것이 금지된 실패 양식이라,
    입력 자체가 비었으면 여기서 사유와 함께 멈춘다.
    """
    if not symbols:
        raise PipelineError("수집 대상 심볼이 0개 — 유니버스를 먼저 확인해라")

    rows: list[dict[str, Any]] = []
    empty: list[str] = []
    notes: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(collect_symbol, s, date_from, date_to, key,
                            fetch=fetch, days=days): s for s in symbols}
        for i, (fut, sym) in enumerate(futs.items(), 1):
            got, why = fut.result()
            rows.extend(got)
            notes.extend(why)
            if not got:
                empty.append(sym)
            if i % 200 == 0:
                log(f"  {i}/{len(symbols)} rows={len(rows)}")
    return CollectResult(rows=rows, requested=len(symbols), empty=empty, notes=notes)


# ── 적재 ────────────────────────────────────────────────────────────────────

def connect(*, s3: bool = True):
    """DuckDB 연결. 세션 pragma·S3 시크릿은 ``statics.duck`` 의 공용 함수를 쓴다.

    ``s3=False`` 는 오프라인 경로(테스트·로컬 parquet)다 — 자격증명이 없는 곳에서
    시크릿 생성이 죽지 않게 **명시적으로** 끈다. 조용히 무시하지 않는 이유: S3 에 쓰는
    경로가 자격증명 없이 계속 돌면 마지막 COPY 에서야 실패한다.
    """
    import duckdb

    con = duckdb.connect()
    for pragma in session_pragmas():
        con.execute(pragma)
    # 왜: 한 달치 파티션을 끝까지 열어둬야 파티션당 파일이 1개로 떨어진다.
    con.execute("SET partitioned_write_max_open_files=2000")
    # 왜: 순서 보존 버퍼가 파티션 쓰기 버퍼와 겹쳐 작은 기계에서 OOM 을 냈다.
    # canonical 은 파티션으로 읽는 표라 파일 내부 행 순서에 의미가 없다.
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET enable_progress_bar=false")
    if s3:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(s3_secret_sql())
    return con


def _bar_tuple(row: dict[str, Any]) -> tuple[Any, ...] | None:
    """응답 행 → raw 열 순서. 종가가 없는 행은 봉이 아니라 자리표시라 버린다."""
    if row.get("close") is None:
        return None
    stamp = str(row.get("date", "")).replace("T", " ")[:19]
    if len(stamp) != 19:
        raise PipelineError(f"unexpected 5m timestamp {row.get('date')!r} for {row.get('symbol')!r}")
    vol = row.get("volume")
    return (row["symbol"], stamp, row.get("open"), row.get("high"), row.get("low"),
            row["close"], None if vol is None else int(vol))


def stage_rows(con, rows: Sequence[dict[str, Any]], *, table: str = "bars") -> int:
    """수집 행을 raw 열 구조 그대로 임시 테이블에 넣는다.

    열·타입을 기존 raw parquet(``symbol VARCHAR, datetime VARCHAR, ohlc DOUBLE,
    volume BIGINT``)에 맞춘다 — 한 열이라도 타입이 다르면 글롭 합치기가 깨진다.
    ``open``/``close`` 등은 DuckDB 예약어라 큰따옴표가 필요하다.
    """
    con.execute(f'CREATE OR REPLACE TABLE {table} (symbol VARCHAR, datetime VARCHAR,'
                ' "open" DOUBLE, "high" DOUBLE, "low" DOUBLE, "close" DOUBLE,'
                ' volume BIGINT)')
    tuples = [t for t in (_bar_tuple(r) for r in rows) if t is not None]
    if tuples:
        con.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?)", tuples)
    return len(tuples)


def publish_raw(con, market: str, tag: str, *, table: str = "bars",
                base: str | None = None) -> str:
    """스테이지를 raw 조각 parquet 으로 올린다(계보 보존 — FMP 재호출 없이 재정규화).

    조각 이름에 ``tag`` 를 넣어 같은 prefix 안에서 실행끼리 덮어쓰지 않게 한다.
    """
    dest = f"{base or bucket_url(RAW_PREFIX[market.upper()])}/gap_{tag}.parquet"
    con.execute(f"COPY {table} TO '{dest}' (FORMAT parquet, COMPRESSION zstd)")
    return dest


def canonical_select(market: str, *, table: str = "bars") -> str:
    """raw → canonical 열 매핑. 열 순서·타입은 기존 파티션과 **한 글자도** 달라선 안 된다.

    ``CAST(... AS TIMESTAMP)`` 를 명시하는 이유: 레이크의 다른 canonical 표가 전부
    TIMESTAMP(parquet TIMESTAMP_MICROS)다. 한 표만 NS 로 나가면 표 간 조인이 깨진다.

    ``DISTINCT ON (ticker, ts)``: 봉의 정체성은 (ticker, ts) 하나다. 재분할 재요청·
    prefix 겹침으로 같은 봉이 두 번 들어올 수 있어 여기서 접는다. 같은 벤더의 같은
    봉이라 어느 쪽을 남겨도 값이 같다.
    """
    mkt = market.upper()
    return f"""
        SELECT DISTINCT ON (ticker, ts) *
        FROM (
            SELECT '{mkt}'                              AS market,
                   {TICKER_EXPR[mkt]}                   AS ticker,
                   symbol                               AS source_symbol,
                   CAST(datetime AS TIMESTAMP)          AS ts,
                   "open", "high", "low", "close", volume,
                   '{VENDOR}'                           AS source_vendor,
                   CAST(CAST(datetime AS TIMESTAMP) + INTERVAL 5 MINUTE AS TIMESTAMP)
                                                        AS available_at,
                   datetime[1:10]                       AS trade_date
            FROM {table}
            WHERE datetime IS NOT NULL
        )
    """


def publish_canonical(con, market: str, *, table: str = "bars",
                      dest: str | None = None) -> int:
    """canonical 파티션(market/trade_date)으로 올린다. 반환은 쓴 행 수.

    ``OVERWRITE_OR_IGNORE``: 같은 날을 다시 수집하면 그 날 파티션만 갈아탄다. 다른
    날 파티션은 건드리지 않는다 — 접두사를 비우면 이력이 사라진다.
    """
    target = dest or bucket_url(CANONICAL_PREFIX)
    sel = canonical_select(market, table=table)
    n = con.execute(f"SELECT count(*) FROM ({sel})").fetchone()[0]
    if not n:
        raise PipelineError(f"canonical 적재 0행 — {market} 스테이지가 비었다")
    con.execute(f"""
        COPY ({sel}) TO '{target}'
        (FORMAT parquet, PARTITION_BY (market, trade_date), OVERWRITE_OR_IGNORE true,
         FILENAME_PATTERN 'part-{{i}}', COMPRESSION zstd)
    """)
    return int(n)


def load_symbols(con, market: str, *, path: str | None = None,
                 base: str | None = None) -> list[str]:
    """수집 대상 심볼. 파일이 있으면 그것, 없으면 canonical 최신 파티션을 이어받는다.

    왜 파티션에서 가져오나: 컨테이너에 심볼 목록 파일을 넣어두면 그 파일이 곧 또 하나의
    미추적 진실이 된다. 이미 쌓은 표의 ``source_symbol`` 이 곧 지금까지의 유니버스다.
    한계: 신규 상장은 이 경로로 들어오지 않는다 — 유니버스 확장은 파일(``--symbols``)로
    명시해야 한다.
    """
    if path:
        text = Path(path).read_text(encoding="utf-8")
        return [s.strip() for s in text.splitlines() if s.strip()]
    src = canonical_sql(market.upper(), base=base)
    got = con.execute(
        f"SELECT DISTINCT source_symbol FROM {src}"
        f" WHERE trade_date = (SELECT max(trade_date) FROM {src}) ORDER BY 1"
    ).fetchall()
    if not got:
        raise PipelineError(
            f"{market} canonical 파티션이 비어 유니버스를 못 만든다 — --symbols 로 줘라")
    return [r[0] for r in got]


# ── 검증 ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class VerifyReport:
    """검증 결과. ``violations`` 가 비면 통과, ``skipped`` 는 **못 한 검사의 사유**다."""

    market: str
    trade_date: str
    n_rows: int
    n_tickers: int
    dup_bars: int
    bars_per_day: dict[int, int]          # 봉 개수 → 그 개수인 티커 수 (분포)
    joined: int
    mismatched: int
    violations: tuple[str, ...]
    skipped: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """위반이 하나도 없을 때만 참. 검사를 건너뛴 것은 통과가 아니라 ``skipped`` 다."""
        return not self.violations


def verify(con, *, market: str, trade_date: str, intraday: str, daily: str | None = None,
           min_bar_ratio: float = MIN_BAR_RATIO,
           max_partial_ratio: float = MAX_PARTIAL_RATIO) -> VerifyReport:
    """하루치 canonical 5분봉 검증. ``intraday``/``daily`` 는 FROM 절 식이다.

    세 가지를 본다.
    1. **일봉 종가 == 그날 마지막 5분봉 종가.** 조인 키(ticker)가 어긋나거나 봉이 잘리면
       여기서 먼저 터진다. 조인 0행도 위반이다 — 0행은 "일치율 100%"가 아니라 대조 실패다.
    2. **(ticker, ts) 중복 0.** 봉의 정체성이 중복이면 하류 집계가 조용히 틀린다.
    3. **하루 창 수 분포.** 기대치의 ``min_bar_ratio`` 미만인 티커가 ``max_partial_ratio``
       를 넘으면 잘린 수집이다. 최빈값 자체가 미달이면 전 종목이 균일하게 잘린 것이라
       따로 적는다 — 이때 1번은 통과할 수 있다(상한은 오래된 쪽을 자르므로 마지막 봉은
       살아 있다). 등호가 아니라 비율인 이유는 상수 절에 적었다.
    """
    mkt = market.upper()
    violations: list[str] = []
    skipped: list[str] = []

    try:
        n_rows, n_tickers, dup = con.execute(
            "SELECT count(*), count(DISTINCT ticker),"
            f" count(*) - count(DISTINCT (ticker, ts)) FROM {intraday}").fetchone()
    except Exception as exc:            # noqa: BLE001 - 파티션 부재도 사유로 말한다
        # 파티션이 아예 없다. 휴장일일 수도 있지만 **조용히 통과시키지 않는다** — 이 표가
        # 2026-07-31 에서 멈춘 것을 아무도 못 본 이유가 '없는 날'과 '안 쌓인 날'을
        # 구분하지 않았기 때문이다. 휴장이면 검증 대상 날짜를 좁혀 부르는 게 맞다.
        return VerifyReport(
            market=mkt, trade_date=trade_date, n_rows=0, n_tickers=0, dup_bars=0,
            bars_per_day={}, joined=0, mismatched=0,
            violations=(f"{mkt} {trade_date}: 파티션을 읽을 수 없다"
                        f"(휴장 또는 미적재) — {type(exc).__name__}: {str(exc)[:120]}",),
            skipped=("파티션 부재로 나머지 검사 전부 미실행",))
    n_rows, n_tickers, dup = int(n_rows), int(n_tickers), int(dup)
    if not n_rows:
        violations.append(f"{mkt} {trade_date}: 행 0 — 수집이 안 됐다(부재를 통과로 읽지 않는다)")
    if dup:
        violations.append(f"(ticker, ts) 중복 {dup:,}행 — 봉의 정체성이 겹쳤다")

    hist = {int(n): int(k) for n, k in con.execute(
        f"SELECT n, count(*) FROM (SELECT ticker, count(*) AS n FROM {intraday}"
        f" GROUP BY 1) GROUP BY 1 ORDER BY 1").fetchall()}
    floor = max(1, int(expected_bars(mkt) * min_bar_ratio))
    if hist:
        partial = sum(k for n, k in hist.items() if n < floor)
        modal = max(hist.items(), key=lambda kv: (kv[1], kv[0]))[0]
        if partial > n_tickers * max_partial_ratio:
            violations.append(
                f"봉 {floor}개 미만인 티커 {partial}/{n_tickers}"
                f"(허용 {max_partial_ratio:.0%}) — 수집이 부분적으로 잘렸다")
        if modal < floor:
            violations.append(
                f"최빈 봉 개수 {modal} < {floor}(기대 {expected_bars(mkt)}) — "
                f"전 종목이 균일하게 짧다. 요청 구간이 잘린 채로 적재됐다")

    joined = mismatched = 0
    if daily is None:
        skipped.append("일봉 소스 미지정 — 종가 대조 생략(통과가 아니다)")
    else:
        try:
            joined, matched = con.execute(f"""
                WITH last_bar AS (SELECT ticker, last("close" ORDER BY ts) AS bar_close
                                  FROM {intraday} GROUP BY 1),
                     d AS (SELECT ticker, "close" AS daily_close FROM {daily})
                SELECT count(*), sum(CASE WHEN bar_close = daily_close THEN 1 ELSE 0 END)
                FROM d JOIN last_bar USING (ticker)
            """).fetchone()
            joined, matched = int(joined), int(matched or 0)
        except Exception as exc:            # noqa: BLE001 - 부재도 사유로 남긴다
            skipped.append(f"일봉 대조 불가: {type(exc).__name__}: {str(exc)[:120]}")
        else:
            mismatched = joined - matched
            if not joined:
                violations.append(
                    "일봉·5분봉 조인 0행 — 대조 실패다(일치율 100% 가 아니다). "
                    "ticker 정규화나 일봉 파티션을 봐라")
            elif mismatched:
                sample = con.execute(f"""
                    WITH last_bar AS (SELECT ticker, last("close" ORDER BY ts) AS bar_close
                                      FROM {intraday} GROUP BY 1),
                         d AS (SELECT ticker, "close" AS daily_close FROM {daily})
                    SELECT ticker, daily_close, bar_close FROM d JOIN last_bar USING (ticker)
                    WHERE bar_close <> daily_close ORDER BY ticker LIMIT 5
                """).fetchall()
                violations.append(
                    f"일봉 종가 != 마지막 5분봉 종가 {mismatched}/{joined}종목 — "
                    + ", ".join(f"{t}(일봉 {d} vs 봉 {b})" for t, d, b in sample))

    return VerifyReport(market=mkt, trade_date=trade_date, n_rows=n_rows,
                        n_tickers=n_tickers, dup_bars=dup, bars_per_day=hist,
                        joined=joined, mismatched=mismatched,
                        violations=tuple(violations), skipped=tuple(skipped))


def trade_dates(rows: Sequence[dict[str, Any]]) -> list[str]:
    """수집분에 실제로 들어온 거래일. 요청 구간이 아니라 **온 것**으로 검증 대상을 만든다."""
    return sorted({_row_day(r) for r in rows if _row_day(r)})


# ── 실행 ────────────────────────────────────────────────────────────────────

def run(market: str, date_from: str, date_to: str, *, symbols_path: str | None = None,
        workers: int = 8, tag: str | None = None, verify_only: bool = False,
        min_bar_ratio: float = MIN_BAR_RATIO, max_partial_ratio: float = MAX_PARTIAL_RATIO,
        log: Callable[[str], None] = print) -> list[VerifyReport]:
    """수집 → raw 적재 → canonical 적재 → 하루별 검증. 위반이 있으면 죽는다.

    검증을 마지막에 두고 실패를 예외로 올리는 이유: 잘린 하루가 canonical 에 남으면
    하류는 그것을 "그날은 조용했다"로 읽는다. 적재된 뒤에라도 소리를 내야 한다.

    ``min_bar_ratio`` 를 호출부까지 뚫어 둔 이유: 반기장(미국 조기 마감·국내 수능일 지연
    개장)은 봉이 정상적으로 짧다. 문턱을 못 낮추면 그런 날 파이프라인이 잘림과 구분 없이
    막힌다 — 문턱을 내리는 것은 사람이 사유를 알고 하는 선택이라 기본값은 엄격하게 둔다.
    """
    mkt = market.upper()
    con = connect()
    reports: list[VerifyReport] = []
    # 검증 대상 날짜: 수집했으면 **실제로 온 거래일**, 검증만이면 요청한 달력일 전부.
    days = [d.isoformat() for d in _dates_between(date_from, date_to)]

    if not verify_only:
        syms = load_symbols(con, mkt, path=symbols_path)
        log(f"[collect] {mkt} {date_from}..{date_to} symbols={len(syms)}")
        got = collect(date_from, date_to, syms, key=api_key(), workers=workers, log=log)
        log(f"[collect] {len(got.rows):,}행 · 빈 응답 {len(got.empty)}/{got.requested}종목 · "
            f"잘림 재분할 {len(got.truncations)}건")
        for note in got.truncations[:20]:
            log(f"  ! {note}")
        staged = stage_rows(con, got.rows)
        log(f"[stage] {staged:,}행")
        log(f"[raw] {publish_raw(con, mkt, tag or f'{date_from}_{date_to}')}")
        log(f"[canonical] {publish_canonical(con, mkt):,}행")
        days = trade_dates(got.rows)

    for day in days:
        rep = verify(con, market=mkt, trade_date=day,
                     intraday=canonical_sql(mkt, day), daily=daily_sql(mkt, day),
                     min_bar_ratio=min_bar_ratio, max_partial_ratio=max_partial_ratio)
        reports.append(rep)
        log(f"[verify] {mkt} {day} rows={rep.n_rows:,} tickers={rep.n_tickers} "
            f"dup={rep.dup_bars} 봉분포={dict(sorted(rep.bars_per_day.items()))} "
            f"일봉대조={rep.joined - rep.mismatched}/{rep.joined}")
        for why in rep.skipped:
            log(f"  ~ {why}")
        for why in rep.violations:
            log(f"  X {why}")

    bad = [f"{r.trade_date}: {w}" for r in reports for w in r.violations]
    if bad:
        raise PipelineError("검증 실패 — " + " | ".join(bad[:10]))
    return reports


def _dates_between(date_from: str, date_to: str) -> list[date]:
    """달력일 나열(양끝 포함)."""
    start, end = _day(date_from), _day(date_to)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def api_key() -> str:
    """FMP 키. ``FMP_API_KEY`` 가 있으면 그것, 없으면 Secrets Manager.

    왜 boto3: 원본은 ``aws`` CLI 를 subprocess 로 불렀고 프로파일을 ``work`` 로 박아
    뒀다. 컨테이너엔 CLI 도 ``~/.aws/config`` 도 없다 — 태스크 역할로 직접 읽는다.
    """
    key = os.environ.get(FMP_KEY_ENV, "").strip()
    if key:
        return key
    import boto3

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE") or None,
                            region_name=os.environ.get("AWS_REGION", DEFAULT_REGION))
    raw = session.client("secretsmanager").get_secret_value(
        SecretId=FMP_SECRET_ID)["SecretString"]
    key = str(json.loads(raw).get("apikey", "")).strip()
    if not key:
        raise PipelineError(f"{FMP_SECRET_ID} 에 apikey 가 없다")
    return key


def main(argv: Sequence[str] | None = None) -> int:
    """CLI. 검증 실패는 비0 종료다 — 잘린 하루를 성공으로 보고하지 않는다."""
    import argparse

    p = argparse.ArgumentParser(description="5분봉 수집 → canonical/market_data/intraday_5m")
    p.add_argument("--market", choices=["kr", "us"], required=True)
    p.add_argument("--from", dest="date_from", required=True)
    p.add_argument("--to", dest="date_to", required=True)
    p.add_argument("--symbols", default=os.environ.get("INTRADAY_SYMBOLS_FILE"),
                   help="심볼 목록 파일(한 줄 하나). 없으면 canonical 최신 파티션을 이어받는다")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--tag", default=None, help="raw 조각 이름(기본: <from>_<to>)")
    p.add_argument("--verify-only", action="store_true", help="수집 없이 검증만")
    p.add_argument("--min-bar-ratio", type=float, default=MIN_BAR_RATIO,
                   help="하루 봉 개수 하한 비율(반기장처럼 정상적으로 짧은 날에만 낮춘다)")
    p.add_argument("--max-partial-ratio", type=float, default=MAX_PARTIAL_RATIO,
                   help="봉이 모자란 티커 허용 비율")
    a = p.parse_args(argv)
    try:
        run(a.market, a.date_from, a.date_to, symbols_path=a.symbols,
            workers=a.workers, tag=a.tag, verify_only=a.verify_only,
            min_bar_ratio=a.min_bar_ratio, max_partial_ratio=a.max_partial_ratio)
    except PipelineError as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
