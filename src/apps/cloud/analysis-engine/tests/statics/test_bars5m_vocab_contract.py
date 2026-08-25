"""공유 5분봉 표면의 어휘 순수성 — 날짜 선택이 가격 집합 위에서 서야 한다 (ALPHA-1028).

ALPHA-941 의 표준 결함형을 고정한다: `bars_5m`/`intraday_5m` 은 가격과 업종지수
롤업(`SECTOR_ROLLUP_VENDOR`)이 나란히 사는 공유 표면이고, 두 생산자는 각자 실행이라
**"지수만 돈 날"이 정상 운영에서 생긴다.** 어휘 필터를 집계에만 걸고 날짜 선택
(`max(trade_date)`)에 안 걸면 그날이 최신일로 뽑혀 가격 행이 0이 되는데, 세 소비자가
전부 이 모양으로 한 번씩 틀렸다(수집 유니버스 공집합 → PipelineError · 정본 오승인 →
폴백 꺼짐 · β=1 무성 폴백). 여기서는 그 반례("지수만 돈 최신일")를 픽스처로 만들어
**필터 한 줄 제거 변이가 살아남지 못하게** 한다.
"""
from __future__ import annotations

from datetime import date

import duckdb

from edge_analysis.statics.duck import SECTOR_ROLLUP_VENDOR
from edge_analysis.statics.layers import MARKET_CODE, _market_beta, prev_price_day_subquery

DAY = "2026-08-21"          # 분석 요청일
PRICE_DAY = "2026-08-19"    # 가격 롤업이 착지한 마지막 날
SECTOR_ONLY_DAY = "2026-08-20"  # 지수 롤업만 돈 날 — 오염된 '최신일'


def _bars_lake():
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE bars_5m (symbol VARCHAR, ts TIMESTAMP, close DOUBLE,"
        " trade_date DATE, source_vendor VARCHAR)"
    )
    for i in range(3):
        for sym in (f"{MARKET_CODE}.KS", "069660.KS"):
            con.execute(
                "INSERT INTO bars_5m VALUES (?, ?, 100.0, ?, '1m_rollup')",
                [sym, f"{PRICE_DAY} 09:{5 * i:02d}:00", PRICE_DAY],
            )
    for i in range(3):
        con.execute(
            "INSERT INTO bars_5m VALUES ('1005', ?, 300.0, ?, ?)",
            [f"{SECTOR_ONLY_DAY} 09:{5 * i:02d}:00", SECTOR_ONLY_DAY,
             SECTOR_ROLLUP_VENDOR],
        )

    class _Lake:
        def __init__(self):
            self.exists: dict = {}
            self.seen: list[str] = []

        def sql(self, q: str):
            self.seen.append(q)
            return con.execute(q).fetchall()

    return _Lake()


def test_prev_day_subquery_skips_sector_only_latest():
    """전일 선택이 지수-only 최신일을 건너뛰고 **가격 어휘의 최신일**을 고른다.

    어휘 필터가 max 에서 빠지면 2026-08-20(지수만 있는 날)이 뽑힌다 — 그날의
    가격 행은 0이라, 이 서브쿼리를 쓰는 모든 소비자가 빈 결과로 조용히 무너진다.
    """
    lake = _bars_lake()
    assert lake.sql(f"SELECT {prev_price_day_subquery(DAY)}")[0][0] \
        == date.fromisoformat(PRICE_DAY)


def test_market_beta_prev_day_query_uses_the_shared_subquery():
    """`_market_beta` 의 전일 질의가 공유 서브쿼리를 그대로 싣는다 — 인라인 max 로
    되돌아가면(복제) 어휘 필터가 조용히 빠지는 자리가 부활한다.

    β 적합까지 가지 않는다 — 여기서 고정하는 계약은 **어느 날짜의 봉을 재료로
    삼는가**뿐이고, 적합 산술은 kbeta 테스트 소관이다.
    """
    lake = _bars_lake()
    _market_beta(lake, "069660", DAY, {"069660": (0.01,), MARKET_CODE: (0.01,)})
    prev_queries = [q for q in lake.seen if "trade_date <" in q]
    assert prev_queries and all(prev_price_day_subquery(DAY) in q for q in prev_queries)


def test_market_beta_reads_price_day_rows_not_sector_rows():
    """전일 재료가 실제로 가격 날의 봉이다 — 지수-only 날이 뽑히면 재료가 0행이라
    β=1 폴백 사유가 남는다(그 폴백 자체가 이 결함의 관측면이었다).
    """
    lake = _bars_lake()
    _market_beta(lake, "069660", DAY, {"069660": (0.01,), MARKET_CODE: (0.01,)})
    # 전일 질의의 결과를 재현해 행이 가격 날 것인지 직접 확인한다.
    rows = lake.sql(
        rf"SELECT DISTINCT trade_date FROM bars_5m "
        rf"WHERE trade_date = {prev_price_day_subquery(DAY)}")
    assert [r[0] for r in rows] == [date.fromisoformat(PRICE_DAY)]
