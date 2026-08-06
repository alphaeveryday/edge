"""재무 연간 패널 — curated DataGuide 재무제표를 PIT 규율로.

`draft/curated/…/dataset=financial_statements` 는 **항목당 파일 하나**이고 각 파일이
`year × ticker` 넓은 표다(1981~, 3,801 종목, 692 항목). 이 모양은 세 가지를 요구한다:

1. **녹이기**: 넓은 표 → (year, ticker, 값). 항목별 파일을 옆으로 붙인다.
2. **선견 차단**: 파티션 `as_of_date` 는 **수집일**이지 공시일이 아니다. FY Y 재무를
   Y년 중에 쓰면 선견이다. 한국 사업보고서 법정 기한이 결산 후 90일이므로
   **Y+1년 4월 1일부터 가용**으로 잡는다(보수적·결정론적). `available_from` 을
   행에 박아 두고, as-of 조인은 뷰가 한다 - 규율이 데이터에 있어야 질의가 못 어긴다.
3. **항목 선별**: 692 개 중 계열족 `재무파생` 이 실제로 쓸 느린 상태만. 다 실으면
   어휘가 아니라 열 목록이 된다.

사용:  python -m edge_analysis.statics.ops.fin <out_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

BUCKET = "edge-dev-pipeline-lake"
PREFIX = "draft/curated/source=dataguide/dataset=financial_statements/"

# item_code → 컬럼. 전부 비율·증가율이라 종목 크기와 무관하다(정규화 불필요).
ITEMS = {
    "M000102001": "debt_ratio",      # 부채비율                 - 레버리지
    "M000102009": "borrow_dep",      # 차입금의존도             - SVB 형 취약 상태
    "M000102010": "netdebt_dep",     # 순부채의존도
    "M000104004": "int_cover",       # 이자보상배율(배)         - 금리 충격 내성
    "M000105003": "cf_assets",       # 현금흐름(TTM)/자산총계   - 현금 창출력
    "M000312001": "roe",             # ROE
    "M000306003": "roa",             # ROA
    "M000303001": "op_margin",       # 영업이익률
    "M000304003": "net_margin",      # 순이익률
    "M000201001": "rev_growth",      # 매출액증가율 YoY
    "M000204001": "op_growth",       # 영업이익증가율 YoY
    "M000615001": "payout",          # 배당성향
}

REPORT_LAG_MONTH = 4   # 결산(12월) + 법정 90일 → 이듬해 4월 1일부터 가용


def _keys(names: list[str]) -> dict[str, str]:
    """item_code → S3 키. 파일명이 `<code>_<한글명>.csv.gz` 라 코드로 찾는다."""
    import json
    import subprocess
    out = subprocess.run(
        ["aws", "--profile", "work", "s3api", "list-objects-v2", "--bucket", BUCKET,
         "--prefix", PREFIX, "--query", "Contents[].Key", "--output", "json"],
        capture_output=True, check=True)
    found: dict[str, str] = {}
    for k in json.loads(out.stdout.decode("utf-8")):
        code = k.split("item=")[-1].split("/")[0] if "item=" in k else ""
        if code in names:
            found[code] = k
    return found


def build_sql(keys: dict[str, str], out: Path) -> str:
    """항목별 넓은 표를 UNPIVOT 으로 녹여 옆으로 붙인다.

    UNPIVOT 은 컬럼 이름을 모른 채 전 컬럼을 녹일 수 있다(`ON COLUMNS(* EXCLUDE year)`)
    - 종목이 3,801 개라 이름을 나열할 수 없다.
    """
    parts = []
    for code, col in ITEMS.items():
        if code not in keys:
            continue
        uri = f"s3://{BUCKET}/{keys[code]}"
        parts.append(f"""_{col} AS (
    SELECT TRY_CAST(year AS INTEGER) AS fy, ltrim(ticker, 'A') AS ticker,
           TRY_CAST(v AS DOUBLE) AS {col}
    FROM (UNPIVOT (SELECT * FROM read_csv('{uri}', all_varchar = true, hive_partitioning = false))
          ON COLUMNS(* EXCLUDE year) INTO NAME ticker VALUE v)
    WHERE v IS NOT NULL AND v <> ''
)""")
    cols = [c for c in ITEMS.values() if any(f"_{c} AS (" in p for p in parts)]
    joins = "\n".join(
        f"FULL JOIN _{c} USING (fy, ticker)" if i else f"FROM _{c}"
        for i, c in enumerate(cols))
    sel = ", ".join(cols)
    return f"""COPY (
WITH {',\n'.join(parts)}
SELECT fy AS fiscal_year, ticker,
       make_date(fy + 1, {REPORT_LAG_MONTH}, 1) AS available_from,
       {sel}
{joins}
WHERE fy BETWEEN 2000 AND 2100
) TO '{out.as_posix()}' (FORMAT PARQUET)"""


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    from ..core.duck import CausalLake
    out = Path(sys.argv[1]) / "fin_annual.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    keys = _keys(list(ITEMS))
    missing = [ITEMS[c] for c in ITEMS if c not in keys]
    if missing:
        print(f"경고 - 원천 없는 항목: {missing}", file=sys.stderr)
    lake = CausalLake()
    lake.con.execute(build_sql(keys, out))
    q = f"read_parquet('{out.as_posix()}')"
    n, y0, y1, tk = lake.con.execute(
        f"SELECT count(*), min(fiscal_year), max(fiscal_year), count(DISTINCT ticker) FROM {q}"
    ).fetchone()
    print(f"fin_annual.parquet  {n:,}행 · FY{y0}~{y1} · {tk:,}종목")
    for c in ITEMS.values():
        if c in (r[0] for r in lake.con.execute(f"DESCRIBE SELECT * FROM {q}").fetchall()):
            got = lake.con.execute(f"SELECT count({c}) FROM {q}").fetchone()[0]
            print(f"  {c:<12} {got:>8,} ({got / n:.0%})")


if __name__ == "__main__":
    main()
