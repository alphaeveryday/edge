"""백필 — 프레임의 빈 노드(전일 미국장·환율·5분봉)를 며칠치로 채운다.

일회성 배관이지 수집기가 아니다. 정식 수집은 data-pipeline 레인 소관이고
(canonical 승격 포함 — 설계 §19 블로커 1), 여기서는 갭 공변량 회귀가
성립하는지 확인할 만큼만 가져온다. PIT 주의: FMP 일봉의 available_at 은
그 날 장마감으로 근사한다(DERIVED) — 등뼈 available_basis 규약.

**5분봉이 왜 여기 있나 (19R)**: 봉·사건·문서 셋의 도달 구간이 겹치지 않아
`news()` 가 19라운드 동안 한 번도 답을 못 냈다. 봉 2022-11~2026-07-16 ·
7월 사건 available_at 07-26~07-31 · 문서 07-08~ → 삼각 정렬 실패.
봉을 07-31 까지 늘리는 것이 유일한 교집합을 만든다 (FMP 는 5분봉을 최근
10영업일만 준다 - 그 창이 정확히 필요한 구간이다).

사용:  python -m edge_analysis.statics.ops.backfill <out_dir> [days] [tickers…]
       (FMP_API_KEY env 필요)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

FMP = ("https://financialmodelingprep.com/stable/historical-price-eod/full"
       "?symbol={sym}&from={d0}&to={d1}&apikey={key}")   # v3 는 폐기 (sources.toml 규약)


def fetch(sym: str, days: int, key: str) -> list[dict]:
    d1, d0 = date.today(), date.today() - timedelta(days=days)
    url = FMP.format(sym=sym, d0=d0, d1=d1, key=key)
    with urllib.request.urlopen(url, timeout=60) as r:
        payload = json.load(r)
    # 평평한 배열 또는 {symbol, historical:[...]} 두 형태 방어 — fmp_price.py 와 동일.
    rows = payload["historical"] if isinstance(payload, dict) else payload
    return [{"date": x["date"], "open": x.get("open"), "close": x.get("close"),
             "prev_close": x.get("previousClose"), "change_pct": x.get("changePercent")}
            for x in rows]


def write_parquet(rows: list[dict], out: Path) -> int:
    import duckdb
    if not rows:
        return 0
    con = duckdb.connect()
    con.execute("CREATE TABLE t (date VARCHAR, open DOUBLE, close DOUBLE, "
                "prev_close DOUBLE, change_pct DOUBLE)")
    con.executemany("INSERT INTO t VALUES (?,?,?,?,?)",
                    [(r["date"], r["open"], r["close"], r["prev_close"], r["change_pct"])
                     for r in rows])
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY t TO '{out.as_posix()}' (FORMAT parquet)")
    return len(rows)


def fetch_bars(sym: str, days: int, key: str) -> list[tuple]:
    """5분봉. FMP 는 최근 ~10영업일만 준다 - 그 이전은 S3 canonical 소관이다."""
    d1, d0 = date.today(), date.today() - timedelta(days=days)
    url = (f"https://financialmodelingprep.com/stable/historical-chart/5min"
           f"?symbol={sym}&from={d0}&to={d1}&apikey={key}")
    with urllib.request.urlopen(url, timeout=60) as r:
        rows = json.load(r)
    return [(sym, x["date"], float(x["open"]), float(x["high"]), float(x["low"]),
             float(x["close"]), int(x["volume"])) for x in rows if x.get("close")]


def merge_bars(sym: str, rows: list[tuple], out: Path) -> tuple[int, str]:
    """기존 파일에 **덧붙인다**. 같은 시각은 기존 값을 남긴다 - 백필이 원본을
    덮으면 재실행마다 값이 흔들리고, 그건 결정론 위반이다."""
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE b (symbol VARCHAR, datetime VARCHAR, open DOUBLE, "
                "high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT)")
    if out.is_file():
        con.execute(f"INSERT INTO b SELECT * FROM read_parquet('{out.as_posix()}')")
    before = con.execute("SELECT count(*) FROM b").fetchone()[0]
    con.executemany("INSERT INTO b SELECT ?,?,?,?,?,?,? WHERE NOT EXISTS "
                    "(SELECT 1 FROM b x WHERE x.symbol=? AND x.datetime=?)",
                    [r + (r[0], r[1]) for r in rows])
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM b ORDER BY datetime) TO '{out.as_posix()}' (FORMAT parquet)")
    n, hi = con.execute("SELECT count(*), max(datetime) FROM b").fetchone()
    return n - before, str(hi)[:10]


def main(out_dir: str, days: int = 14, tickers: tuple[str, ...] = ()) -> None:
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        sys.exit("FMP_API_KEY 없음")
    out = Path(out_dir)
    for sym, name in (("SPY", "us_market"), ("USDKRW", "fx_usdkrw")):
        n = write_parquet(fetch(sym, days, key), out / f"{name}.parquet")
        print(f"{name}: {n}행")
    for t in tickers:
        added, hi = merge_bars(t, fetch_bars(t, days, key), out / "bars" / f"{t}.parquet")
        print(f"bars {t}: +{added}행 · 최신 {hi}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".tmp/causal-backfill",
         int(sys.argv[2]) if len(sys.argv) > 2 else 14,
         tuple(sys.argv[3:]))
