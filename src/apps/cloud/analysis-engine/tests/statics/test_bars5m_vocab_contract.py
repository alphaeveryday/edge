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
OLDER_PRICE_DAY = "2026-08-18"  # 더 오래된 가격일 — '단일 파티션' 계약의 대조군
PRICE_DAY = "2026-08-19"    # 가격 롤업이 착지한 마지막 날
SECTOR_ONLY_DAY = "2026-08-20"  # 지수 롤업만 돈 날 — 오염된 '최신일'


def _bars_lake():
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE bars_5m (symbol VARCHAR, ts TIMESTAMP, close DOUBLE,"
        " trade_date DATE, source_vendor VARCHAR)"
    )
    for day in (OLDER_PRICE_DAY, PRICE_DAY):
        for i in range(3):
            for sym in (f"{MARKET_CODE}.KS", "069660.KS"):
                con.execute(
                    "INSERT INTO bars_5m VALUES (?, ?, 100.0, ?, '1m_rollup')",
                    [sym, f"{day} 09:{5 * i:02d}:00", day],
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
            self.seen: list[tuple[str, list]] = []  # (질의, 반환 행) — 실소비 검증용

        def sql(self, q: str):
            rows = con.execute(q).fetchall()
            self.seen.append((q, rows))
            return rows

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
    prev_queries = [q for q, _ in lake.seen if "trade_date <" in q]
    assert prev_queries and all(prev_price_day_subquery(DAY) in q for q in prev_queries)


def test_market_beta_consumes_price_day_rows_not_sector_rows():
    """`_market_beta` 가 **실제로 받아 간 행**이 가격 날의 봉이다.

    검증 대상은 내가 다시 실행한 SQL 이 아니라 함수가 소비한 결과 그 자체다 —
    바깥 날짜 조건이 틀어져도(예: `=` → `<>`) 여기서 깨져야 한다. 지수-only
    날이 뽑히면 재료가 0행이 되고, 그게 β=1 무성 폴백의 뿌리였다.
    """
    lake = _bars_lake()
    _market_beta(lake, "069660", DAY, {"069660": (0.01,), MARKET_CODE: (0.01,)})
    consumed = [rows for q, rows in lake.seen if "trade_date <" in q]
    assert consumed and consumed[0], "전일 질의가 행을 소비하지 못했다"
    # SELECT 는 (sym, ts, close) — ts 의 날짜로 어느 파티션의 봉인지 판정한다.
    # **직전 하루 단일 파티션**이 계약이다: 더 오래된 가격일(08-18)이 섞이면
    # (`=` → `<=` 류 완화) β 를 전 이력으로 적합하는 다른 함수가 된다.
    assert {ts.date() for _, ts, _ in consumed[0]} == {date.fromisoformat(PRICE_DAY)}
    assert {sym for sym, _, _ in consumed[0]} == {"069660", MARKET_CODE}
