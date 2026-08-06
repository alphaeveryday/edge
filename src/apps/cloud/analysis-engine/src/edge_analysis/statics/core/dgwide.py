"""DataGuide curated **전량 바인딩** — 검정 재료와 가설 어휘를 분리한다.

## 왜 두 층인가

`fin.py` 는 692 항목 중 12개만 실었고 그 근거를 이렇게 적었다:
    "다 실으면 어휘가 아니라 열 목록이 된다"

그 말은 **가설 층**에서는 맞다. 가설 에이전트가 692 개 열을 보면 슬롯 채우기가
아니라 열 고르기가 되고, 그게 곧 채굴이다.

그런데 **검정 층**에서는 틀리다. 검정 에이전트의 일은 거친 가설을 받아 **구체화**
하는 것이다 - "실적 발표가 원인" 을 "영업이익률 하락 국면에서 차입금의존도 높은
종목에서만" 으로 좁히는 것. 그 좁힘의 재료가 12개면 좁힐 수 없다. 실측이 그
증상을 보여줬다: 6+6 가설이 전부 죽고 성립-적용 엣지가 0개.

그래서 층을 나눈다:

    가설 어휘   닫힌 계열족 16 × 변환 6          (vocab.py - 안 늘린다)
    검정 재료   curated 전량 (fin_wide·mkt_wide)  (이 모듈 - 다 싣는다)

검정 에이전트는 전량에서 후보를 찾고, **찾은 것을 계열족 어휘로 환원해서** 보고한다.
환원이 안 되면 그건 어휘 확장 요청이고 사람이 판단한다.

## PIT 규율은 같다

재무는 `available_from = FY+1년 4월 1일` (결산 후 법정 90일, 보수적·결정론적).
시장 일간은 파티션 `as_of_date` 가 곧 클램프다 (그날 관측 가능했던 값만 그 행에).

사용:  python -m edge_analysis.statics.core.dgwide fin  <out_dir>
       python -m edge_analysis.statics.core.dgwide mkt  <out_dir>
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BUCKET = "edge-dev-pipeline-lake"
FIN = "draft/curated/source=dataguide/dataset=financial_statements/"
MKT = "draft/curated/source=dataguide/dataset=market_daily/"
REPORT_LAG_MONTH = 4


def _keys(prefix: str) -> list[tuple[str, str, str]]:
    """(item_code, 사람이 읽는 이름, s3 uri). 최신 as_of 파티션만."""
    out = subprocess.run(
        ["aws", "--profile", "work", "s3api", "list-objects-v2", "--bucket", BUCKET,
         "--prefix", prefix, "--query", "Contents[].Key", "--output", "text"],
        capture_output=True, text=True, check=True)
    keys = [k for k in out.stdout.split() if k.endswith(".csv.gz")]
    if not keys:
        return []
    latest = max(re.search(r"as_of_date=([\d-]+)", k).group(1) for k in keys
                 if "as_of_date=" in k)
    picked = []
    for k in keys:
        if f"as_of_date={latest}" not in k:
            continue
        fn = k.rsplit("/", 1)[-1]
        code = fn.split("_", 1)[0]
        nm = fn.split("_", 1)[1].removesuffix(".csv.gz").strip("_") if "_" in fn else code
        picked.append((code, nm, f"s3://{BUCKET}/{k}"))
    return sorted(set(picked))


def _safe(code: str, nm: str) -> str:
    """열 이름: 코드 + 슬러그. 이름만 쓰면 중복·특수문자로 SQL 이 깨진다."""
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "_", nm).strip("_")[:28]
    return f"{code}_{s}" if s else code


def build_fin(out_dir: str | Path) -> Path:
    """재무 692 항목 전량 → (ticker, fiscal_year, available_from, 692열) parquet.

    각 파일이 `year × ticker` 넓은 표라 UNPIVOT 으로 녹인 뒤 full join 으로 붙인다.
    항목이 많아 join 을 SQL 한 문장에 넣으면 플래너가 죽는다 - **누적 병합**한다.
    """
    import duckdb
    ks = _keys(FIN)
    if not ks:
        raise SystemExit("재무 curated 키를 못 찾았다")
    out = Path(out_dir) / "fin_wide.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, "
                "CHAIN 'sso;config;env', PROFILE 'work', REGION 'ap-northeast-2')")
    made = 0
    for i, (code, nm, uri) in enumerate(ks):
        col = _safe(code, nm)
        try:
            con.execute(f"""CREATE OR REPLACE TEMP TABLE piece AS
                SELECT TRIM(name) AS ticker, TRY_CAST(year AS INTEGER) AS fy,
                       TRY_CAST(value AS DOUBLE) AS "{col}"
                FROM (UNPIVOT (SELECT * FROM read_csv('{uri}', all_varchar = true))
                      ON COLUMNS(* EXCLUDE year) INTO NAME name VALUE value)
                WHERE TRY_CAST(year AS INTEGER) IS NOT NULL""")
        except Exception as e:                      # noqa: BLE001 - 항목 하나가 전체를 막지 않는다
            print(f"  skip {col}: {str(e)[:70]}")
            continue
        if made == 0:
            con.execute("CREATE OR REPLACE TABLE w AS SELECT * FROM piece")
        else:
            con.execute(f"""CREATE OR REPLACE TABLE w AS
                SELECT coalesce(w.ticker, p.ticker) AS ticker,
                       coalesce(w.fy, p.fy) AS fy,
                       w.* EXCLUDE (ticker, fy), p."{col}"
                FROM w FULL JOIN piece p ON p.ticker = w.ticker AND p.fy = w.fy""")
        made += 1
        if made % 100 == 0:
            print(f"  {made}/{len(ks)} 항목")
    con.execute(f"""COPY (
        SELECT ticker, fy AS fiscal_year,
               make_date(fy + 1, {REPORT_LAG_MONTH}, 1) AS available_from,
               * EXCLUDE (ticker, fy)
        FROM w WHERE ticker IS NOT NULL AND fy IS NOT NULL
    ) TO '{out.as_posix()}' (FORMAT parquet)""")
    n, c = con.execute(f"SELECT count(*), count(*) FROM read_parquet('{out.as_posix()}')"
                       ).fetchone()[0], made
    print(f"fin_wide {n:,}행 · {c} 항목 → {out}")
    return out


REF = ("draft/curated/source=dataguide/dataset=reference/market=KR/"
       "as_of_date=2026-08-02/items_resolved.csv")


def items_dict() -> list[tuple[str, str, str, str]]:
    """(item_code, name_kr, domain, category) — curated 가 주는 **항목 사전**.

    실측 947 항목: 투자자별매매-수량 329 · 대금 329 · 가격수익률 97 · 주식수시총 59
    · 베타 45 · 거래량 23 · 주가배수 20 · 신용거래 20 · 대차거래 13 · 차입공매도 12.
    **컨센서스/추정 항목은 없다** (이름·카테고리 전수 검색 0건) - 서프라이즈는 다른
    소스가 필요하다.
    """
    import csv
    import io
    out = subprocess.run(
        ["aws", "--profile", "work", "s3", "cp", f"s3://{BUCKET}/{REF}", "-"],
        capture_output=True, check=True)
    rd = csv.DictReader(io.StringIO(out.stdout.decode("utf-8-sig")))
    return [(r["item_code"], r["name_kr"], r["domain"], r["category"]) for r in rd]


def build_mkt(out_dir: str | Path, *, domains: tuple[str, ...] = ("price", "flow")) -> Path:
    """시장 일간 **전량** → (ticker, trade_date, N열) parquet. 파티션이 곧 PIT 클램프.

    curated 는 long 형식 `(trade_date, ticker, item_code, value)` 이고 하루 75만 행이다.
    `pit.py` 는 그중 20 항목만 피벗한다 - 검정 층이 좁힐 재료가 20개면 좁힐 수 없다.
    여기서는 사전에 있는 전 항목을 피벗한다.
    """
    import duckdb
    dic = [d for d in items_dict() if d[2] in domains]
    if not dic:
        raise SystemExit("항목 사전이 비었다")
    out = Path(out_dir) / "mkt_wide.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, "
                "CHAIN 'sso;config;env', PROFILE 'work', REGION 'ap-northeast-2')")
    cols = ",\n           ".join(
        f"max(CASE WHEN item_code = '{c}' THEN v END) AS \"{_safe(c, nm)}\""
        for c, nm, _d, _k in dic)
    uri = f"s3://{BUCKET}/{MKT}**/*.csv.gz"
    con.execute(f"""COPY (
        WITH r AS (
            SELECT TRIM(ticker) AS ticker, TRY_CAST(trade_date AS DATE) AS trade_date,
                   item_code, TRY_CAST(value AS DOUBLE) AS v
            FROM read_csv('{uri}', all_varchar = true, hive_partitioning = true,
                          union_by_name = true)
            WHERE item_code IN ({", ".join(f"'{c}'" for c, *_ in dic)})
        )
        SELECT ticker, trade_date,
           {cols}
        FROM r WHERE ticker IS NOT NULL AND trade_date IS NOT NULL
        GROUP BY 1, 2
    ) TO '{out.as_posix()}' (FORMAT parquet)""")
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
    print(f"mkt_wide {n:,}행 · {len(dic)} 항목 → {out}")
    return out


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("fin", "mkt", "keys"):
        raise SystemExit(__doc__)
    if sys.argv[1] == "keys":
        if sys.argv[2] == "fin":
            for code, nm, _u in _keys(FIN):
                print(f"{code}  {nm}")
        else:
            for code, nm, dom, cat in items_dict():
                print(f"{code}  {dom:<6} {cat:<18} {nm}")
        return
    (build_fin if sys.argv[1] == "fin" else build_mkt)(sys.argv[2])


if __name__ == "__main__":
    main()
