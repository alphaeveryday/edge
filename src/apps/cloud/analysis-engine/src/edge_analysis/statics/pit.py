"""PIT 일간 패널 — curated DataGuide 를 피처 프레임이 쓸 수 있는 모양으로.

`draft/curated/…/dataset=market_daily` 는 (trade_date, ticker, item_code, value)
**긴 형식**이다. 하루 74만 행 × 248일 = 1.8억 행이라 셀마다 직접 읽으면 2분 12초가
든다(실측). 필요한 항목만 뽑아 **넓은 형식 파케이 한 장**으로 접는다 - 백필 규약
(`<name>.parquet` → 같은 이름의 뷰)을 그대로 따른다.

**왜 PIT 인가**: 원본이 trade_date 별 특정시점 스냅샷이라 선견이 구조적으로 없다.
`available_at` 클램프를 손으로 쓰던 자리를 파티션 키가 대신한다.

항목 선택은 **계열족당 하나**다. 여러 변종(PER 4종 · 시가총액 6종)을 다 넣으면
어휘가 아니라 열 목록이 되고, 가설 에이전트가 무엇을 골라야 하는지 모른다.

사용:  python -m edge_analysis.statics.pit <out_dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

CURATED = ("s3://edge-dev-pipeline-lake/draft/curated/source=dataguide/"
           "dataset=market_daily/**/*.csv.gz")

# item_code → 컬럼. 주석의 계열족이 vocab.SERIES_FAMILIES 와 짝이다.
ITEMS = {
    "S410001300": "foreign_ratio",   # 외국인지분율(%)         → 주주
    "S410011800": "credit_ratio",    # 신용융자잔고율(%)        → 신용
    "S410000996": "short_ratio",     # 차입공매도잔고비율(%)    → 공매도
    "S420006201": "pbr",             # PBR(IFRS-별도)          → 배수
    "S420003800": "shares",          # 상장주식수(주)           → 주식수
    "S410001250": "mktcap",          # 시가총액(백만원)         (크기 - 통제용)
}


def build_sql(out: Path) -> str:
    """긴 형식 → 넓은 형식. ticker 는 'A000010' 이라 앞 글자를 떼 6자리로 맞춘다."""
    cols = ",\n       ".join(
        f"max(CASE WHEN item_code = '{code}' THEN TRY_CAST(value AS DOUBLE) END) AS {name}"
        for code, name in ITEMS.items())
    keys = "', '".join(ITEMS)
    return f"""COPY (
SELECT CAST(trade_date AS DATE) AS trade_date,
       ltrim(ticker, 'A')       AS ticker,
       {cols}
FROM read_csv('{CURATED}', hive_partitioning = true, union_by_name = true)
WHERE item_code IN ('{keys}')
GROUP BY 1, 2
) TO '{out.as_posix()}' (FORMAT PARQUET)"""


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    from .duck import CausalLake
    out = Path(sys.argv[1]) / "pit_daily.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    lake = CausalLake()
    lake.con.execute(build_sql(out))
    n, d0, d1, tk = lake.con.execute(
        f"SELECT count(*), min(trade_date), max(trade_date), count(DISTINCT ticker) "
        f"FROM read_parquet('{out.as_posix()}')").fetchone()
    print(f"pit_daily.parquet  {n:,}행 · {d0}~{d1} · {tk:,}종목 · 항목 {len(ITEMS)}")
    for c in ITEMS.values():
        got = lake.con.execute(
            f"SELECT count({c}) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
        print(f"  {c:<14} {got:>9,} ({got / n:.0%})")


if __name__ == "__main__":
    main()
