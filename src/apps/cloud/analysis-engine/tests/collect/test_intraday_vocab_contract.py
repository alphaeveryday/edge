"""수집 유니버스의 어휘 순수성 — 지수-only 최신일이 유니버스를 비우면 안 된다 (ALPHA-1028).

`load_symbols` 는 canonical intraday_5m 의 최신 파티션에서 유니버스를 이어받는데,
그 파티션에는 업종지수 롤업이 나란히 산다. `canonical_sql` 이 FROM 절에서 어휘를
거르므로 `max(trade_date)` 도 걸러진 집합 위에서 서야 한다 — 필터가 빠지면
"지수만 돈 날"이 최신일로 뽑혀 가격 심볼이 0이 되고, 멀쩡한 직전 거래일을 두고
`PipelineError` 로 수집이 시작조차 못 한다(ALPHA-941 1번 자리).
"""
from __future__ import annotations

import duckdb
import pytest

from edge_analysis.collect.intraday import load_symbols
from edge_analysis.statics.duck import SECTOR_ROLLUP_VENDOR

PRICE_DAY = "2026-08-19"
SECTOR_ONLY_DAY = "2026-08-20"


@pytest.fixture()
def canonical_base(tmp_path):
    """가격 파티션(08-19)과 지수-only 파티션(08-20)이 나란한 canonical 루트."""
    con = duckdb.connect(":memory:")
    for day, rows in (
        (PRICE_DAY, [("005930", "1m_rollup"), ("000660", "1m_rollup")]),
        (SECTOR_ONLY_DAY, [("1005", SECTOR_ROLLUP_VENDOR)]),
    ):
        part = tmp_path / "market=KR" / f"trade_date={day}"
        part.mkdir(parents=True)
        con.execute("CREATE OR REPLACE TABLE t (source_symbol VARCHAR, source_vendor VARCHAR)")
        for sym, vendor in rows:
            con.execute("INSERT INTO t VALUES (?, ?)", [sym, vendor])
        con.execute(f"COPY t TO '{part / 'part-0.parquet'}' (FORMAT parquet)")
    return str(tmp_path)


def test_universe_survives_sector_only_latest_day(canonical_base):
    """최신일이 지수-only 여도 유니버스는 **가격 어휘의 최신일**에서 나온다.

    어휘 필터가 날짜 선택에서 빠지면 08-20 이 뽑혀 심볼 0건 → PipelineError —
    직전 거래일(08-19)에 멀쩡한 가격 심볼이 있는데도 수집이 통째로 멎는 회귀다.
    업종코드('1005')가 유니버스로 새는 것도 여기서 함께 거부된다(그 코드는 FMP
    종목이 아니라 빈 응답·오해소 경로다).
    """
    got = load_symbols(duckdb.connect(":memory:"), "KR", base=canonical_base)
    assert got == ["000660", "005930"]
    assert "1005" not in got
