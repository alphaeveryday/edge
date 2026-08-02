"""KODEX 200 구성종목의 장기 수정주가를 로컬 parquet 으로 내린다 (1회)."""
import duckdb

L = "s3://edge-dev-pipeline-lake/"
PX = f"{L}draft/curated/source=dataguide/dataset=price_daily/market=KR/as_of_date=2026-08-02/item=S410000700/*.csv.gz"

c = duckdb.connect()
c.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='ap-northeast-2';")
c.execute("CREATE SECRET (TYPE s3, PROVIDER credential_chain, CHAIN 'sso;config', PROFILE 'work')")

hold = c.execute(f"""
    SELECT DISTINCT constituent_ticker AS tk
    FROM read_parquet('{L}canonical/holdings/etf_holdings/market=KR/**/*.parquet', hive_partitioning=1)
    WHERE etf_id = '069500'""").fetchall()
tks = sorted(t[0] for t in hold)
print(f"구성종목 {len(tks)}")

cols = [d[0] for d in c.execute(f"SELECT * FROM read_csv_auto('{PX}', all_varchar=1) LIMIT 0").description]
have = [f"A{t}" for t in tks if f"A{t}" in cols]
print(f"가격 컬럼 매칭 {len(have)}/{len(tks)}")

sel = ", ".join(f'TRY_CAST("{h}" AS DOUBLE) AS "{h}"' for h in have)
c.execute(f"""COPY (
    SELECT CAST(date AS DATE) AS d, {sel}
    FROM read_csv_auto('{PX}', all_varchar=1)
    WHERE CAST(date AS DATE) >= DATE '2024-06-01'
) TO '.tmp/kodex_px.parquet' (FORMAT parquet)""")
print(c.execute("SELECT count(*), min(d), max(d) FROM read_parquet('.tmp/kodex_px.parquet')").fetchall())

c.execute(f"""COPY (
    SELECT constituent_ticker AS tk, weight_pct/100.0 AS w, as_of_date AS d
    FROM read_parquet('{L}canonical/holdings/etf_holdings/market=KR/**/*.parquet', hive_partitioning=1)
    WHERE etf_id = '069500'
) TO '.tmp/kodex_w.parquet' (FORMAT parquet)""")
print(c.execute("SELECT count(*), count(DISTINCT d) FROM read_parquet('.tmp/kodex_w.parquet')").fetchall())
