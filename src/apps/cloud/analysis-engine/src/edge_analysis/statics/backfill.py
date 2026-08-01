"""백필 — 프레임의 빈 노드(전일 미국장·환율)를 며칠치로 채운다.

일회성 배관이지 수집기가 아니다. 정식 수집은 data-pipeline 레인 소관이고
(canonical 승격 포함 — 설계 §19 블로커 1), 여기서는 갭 공변량 회귀가
성립하는지 확인할 만큼만 가져온다. PIT 주의: FMP 일봉의 available_at 은
그 날 장마감으로 근사한다(DERIVED) — 등뼈 available_basis 규약.

사용:  python -m edge_analysis.statics.backfill <out_dir> [days]
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


def main(out_dir: str, days: int = 14) -> None:
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        sys.exit("FMP_API_KEY 없음")
    out = Path(out_dir)
    for sym, name in (("SPY", "us_market"), ("USDKRW", "fx_usdkrw")):
        n = write_parquet(fetch(sym, days, key), out / f"{name}.parquet")
        print(f"{name}: {n}행")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".tmp/causal-backfill",
         int(sys.argv[2]) if len(sys.argv) > 2 else 14)
