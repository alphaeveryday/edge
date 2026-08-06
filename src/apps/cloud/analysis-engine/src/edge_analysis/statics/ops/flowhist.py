"""수급 일간 이력 — curated DataGuide 투자자별 매매를 PIT 모양으로.

지금 패널의 수급 노출(`fl_cum20`)은 `s3_investor_value` 에서 온다. 그 표는
**2025-06-02 ~ 2026-07-31, 14개월**뿐이다(실측 1,356,004행 · 366종목). 20일 창을
쌓고 나면 쓸 수 있는 패널 행이 얇다.

curated `investor_flow_daily` 는 같은 값을 **1980년부터** 준다(12,366 거래일 ×
8,708 종목). 모양이 `date × ticker` 넓은 표라 fin.py 와 같은 UNPIVOT 이 필요하다.

**왜 대금(순매수대금)인가**: 수량은 주가 수준에 오염된다(같은 1만 주라도 종목마다
금액이 다르다). 대금이 크기 정규화(시총 나누기)와 바로 맞는다.

사용:  python -m edge_analysis.statics.ops.flowhist <out_dir> [시작연도]
"""
from __future__ import annotations

import sys
from pathlib import Path

BUCKET = "edge-dev-pipeline-lake"
PREFIX = "draft/curated/source=dataguide/dataset=investor_flow_daily/"

# item_code → 컬럼. 순매수 **대금**만(만원). 수량은 주가 수준에 오염된다.
#
# **한 항목만 싣는다.** 6항목을 FULL JOIN 하면 645M 셀(12,366일 × 8,708종목 × 6)이라
# 30분에도 안 끝났다(실측). 패널이 실제로 쓰는 것은 외국인 순매수 하나뿐이고,
# 나머지는 쓸 자리가 생길 때 같은 방식으로 한 장씩 더 만들면 된다.
ITEMS = {"CI20113020": "for_net"}   # 외국인총합계 순매수대금
FROM_YEAR = 2022   # RDB price_daily 가 2022-11 부터라 그 이전 행은 패널이 못 쓴다


def _keys(codes: list[str]) -> dict[str, str]:
    import json
    import subprocess
    out = subprocess.run(
        ["aws", "--profile", "work", "s3api", "list-objects-v2", "--bucket", BUCKET,
         "--prefix", PREFIX, "--query", "Contents[].Key", "--output", "json"],
        capture_output=True, check=True)
    got: dict[str, str] = {}
    for k in json.loads(out.stdout.decode("utf-8")):
        c = k.split("item=")[-1].split("/")[0] if "item=" in k else ""
        if c in codes:
            got[c] = k
    return got


def build_sql(keys: dict[str, str], out: Path, from_year: int) -> str:
    parts, cols = [], []
    for code, col in ITEMS.items():
        if code not in keys:
            continue
        cols.append(col)
        uri = f"s3://{BUCKET}/{keys[code]}"
        parts.append(f"""_{col} AS (
    SELECT TRY_CAST(date AS DATE) AS trade_date, ltrim(ticker, 'A') AS ticker,
           TRY_CAST(v AS DOUBLE) * 1e4 AS {col}
    FROM (UNPIVOT (SELECT * FROM read_csv('{uri}', all_varchar = true,
                                          hive_partitioning = false))
          ON COLUMNS(* EXCLUDE date) INTO NAME ticker VALUE v)
    WHERE v IS NOT NULL AND v <> '' AND date >= '{from_year}-01-01'
)""")
    joins = "\n".join(f"FULL JOIN _{c} USING (trade_date, ticker)" if i else f"FROM _{c}"
                      for i, c in enumerate(cols))
    return f"""COPY (
WITH {',\n'.join(parts)}
SELECT trade_date, ticker, {', '.join(cols)}
{joins}
) TO '{out.as_posix()}' (FORMAT PARQUET)"""


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    from ..core.duck import CausalLake
    out = Path(sys.argv[1]) / "flow_daily.parquet"
    from_year = int(sys.argv[2]) if len(sys.argv) > 2 else FROM_YEAR
    out.parent.mkdir(parents=True, exist_ok=True)
    keys = _keys(list(ITEMS))
    lake = CausalLake()
    lake.con.execute(build_sql(keys, out, from_year))
    q = f"read_parquet('{out.as_posix()}')"
    n, d0, d1, tk = lake.con.execute(
        f"SELECT count(*), min(trade_date), max(trade_date), count(DISTINCT ticker) FROM {q}"
    ).fetchone()
    print(f"flow_daily.parquet  {n:,}행 · {d0}~{d1} · {tk:,}종목")
    for c in ITEMS.values():
        got = lake.con.execute(f"SELECT count({c}) FROM {q}").fetchone()[0]
        print(f"  {c:<10} {got:>10,} ({got / n:.0%})")


if __name__ == "__main__":
    main()
