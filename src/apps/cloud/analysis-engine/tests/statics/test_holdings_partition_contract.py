"""holdings 파티션 선택 계약 — 리더 3곳의 과반-NULL 가드 패리티 (ALPHA-1027).

여기서 고정하는 건 **"같은 레이크를 읽는 리더들은 같은 파티션을 고른다"** 다.
2026-08-11 사고(비중 96% NULL 프리마켓 파티션)의 가드가 `adapters/lake.load_holdings`
한 곳에만 붙은 채 프리마켓 레인이 상시화되자, `statics/layers.holdings` 가 매 거래일
오염 파티션을 집어 장중 기여 분해가 08-13 부터 전건 죽었다 — 산술 오류가 아니라
**리더 간 계약 불일치**가 결함이었다. 그래서 검사 대상은 개별 리더의 정답이 아니라
**리더끼리의 합의**다: SQL 리더(layers·batch, `weighted_asof_subquery` 공유)와
파케이 리더(adapters)가 같은 픽스처에서 같은 as_of 를 골라야 한다.

픽스처는 사고의 형상 그대로다 — 오염 파티션의 유일한 실값이 `원화현금 100.0` 이라
**비중 합으로는 못 가른다**(합이 정확히 100). 가드가 합 기반으로 퇴행하면 여기서 깨진다.
"""
from __future__ import annotations

from datetime import date

import duckdb
import pytest

from edge_analysis.adapters.lake import LakeReader
from edge_analysis.statics import layers
from edge_analysis.statics.layers import weighted_asof_subquery

ETF = "069500"
GOOD_DAY = "2026-08-20"     # EOD 수집 — 비중 실값
POISON_DAY = "2026-08-21"   # 프리마켓 수집 — 과반 NULL, 실값은 원화현금 100.0 뿐
CASH = "KRD010010001"
GOOD_TICKERS = [f"{100000 + i}" for i in range(10)]  # 6자리 숫자 티커 10종


def _duck_lake():
    """`s3_etf_holdings` 만 있는 in-memory 레이크 — SQL 리더(layers·batch)용."""
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE s3_etf_holdings (market VARCHAR, etf_id VARCHAR,"
        " constituent_ticker VARCHAR, constituent_name VARCHAR,"
        " weight_pct DOUBLE, as_of_date DATE)"
    )
    for tk in GOOD_TICKERS:
        con.execute(
            "INSERT INTO s3_etf_holdings VALUES ('KR', ?, ?, ?, 10.0, ?)",
            [ETF, tk, f"종목{tk}", GOOD_DAY],
        )
    con.execute(
        "INSERT INTO s3_etf_holdings VALUES ('KR', ?, ?, '원화현금', 100.0, ?)",
        [ETF, CASH, POISON_DAY],
    )
    for tk in GOOD_TICKERS[:9]:
        con.execute(
            "INSERT INTO s3_etf_holdings VALUES ('KR', ?, ?, ?, NULL, ?)",
            [ETF, tk, f"종목{tk}", POISON_DAY],
        )

    class _DuckLake:
        def sql(self, q: str):
            return con.execute(q).fetchall()

    return _DuckLake()


class _StubLake(LakeReader):
    """파티션 dict 를 그대로 돌려주는 파케이 리더 — `test_lake_holdings` 와 같은 형."""

    def __init__(self, partitions: dict[str, list[dict]]) -> None:
        super().__init__(None, "bucket")
        self.partitions = partitions

    def _partition_values(self, base: str, key: str) -> list[str]:
        return sorted(self.partitions)

    def _read_parquet_prefix(self, prefix: str, columns: list[str]) -> list[dict]:
        as_of = prefix.rstrip("/").rsplit("=", 1)[1]
        return self.partitions[as_of]


def _same_partitions_as_dict() -> dict[str, list[dict]]:
    """duck 픽스처와 **논리적으로 동일한** 파티션 — 파케이 리더용."""
    good = [
        {"etf_id": ETF, "constituent_ticker": tk,
         "constituent_name": f"종목{tk}", "weight_pct": 10.0}
        for tk in GOOD_TICKERS
    ]
    poison = [{"etf_id": ETF, "constituent_ticker": CASH,
               "constituent_name": "원화현금", "weight_pct": 100.0}]
    poison += [
        {"etf_id": ETF, "constituent_ticker": tk,
         "constituent_name": f"종목{tk}", "weight_pct": None}
        for tk in GOOD_TICKERS[:9]
    ]
    return {GOOD_DAY: good, POISON_DAY: poison}


@pytest.fixture(autouse=True)
def _fresh_cache():
    # holdings 는 (lake, etf, day) lru_cache 다 — 테스트 간 오염을 끊는다.
    layers.holdings.cache_clear()


def test_sql_reader_skips_poisoned_latest_partition():
    """layers.holdings 는 과반-NULL 최신 파티션을 건너뛰고 직전 정상 파티션을 쓴다.

    가드가 없으면 원화현금 1행이 나온다 — 그 1행은 분봉에 없어 기여 분해가
    "0종목 중 0종목"으로 죽는 바로 그 산출이다. 비중 합(100.0)으로는 못 가른다.
    """
    got = layers.holdings(_duck_lake(), ETF, POISON_DAY)
    assert sorted(t for t, _, _ in got) == sorted(GOOD_TICKERS)
    assert all(abs(w - 0.10) < 1e-9 for _, _, w in got)
    assert CASH not in {t for t, _, _ in got}


def test_readers_agree_on_partition_choice():
    """**패리티가 계약이다** — SQL 리더와 파케이 리더가 같은 픽스처에서 같은 as_of.

    한쪽에만 가드가 붙는 순간(08-13 사고의 형태) 이 테스트가 깨진다. 리더를
    새로 만들면 여기 픽스처에 물려라.
    """
    sql_got = layers.holdings(_duck_lake(), ETF, POISON_DAY)
    sql_chosen = GOOD_DAY if sorted(t for t, _, _ in sql_got) == sorted(GOOD_TICKERS) \
        else POISON_DAY
    _, parquet_chosen = _StubLake(_same_partitions_as_dict()).load_holdings(
        ETF, "KR", date.fromisoformat(POISON_DAY))
    assert sql_chosen == parquet_chosen == GOOD_DAY


def test_batch_query_carries_the_same_gate():
    """batch.cells 의 holdings CTE 가 공유 서브쿼리를 그대로 싣는다.

    batch 가 자기 max() 를 다시 쓰면(복제) 다음 드리프트가 거기서 난다 — 질의
    텍스트에 공유 게이트가 실려 나가는지를 본다(레이크는 스텁, 0행 반환).
    """
    from edge_analysis.statics.batch import cells

    seen: list[str] = []

    class _CaptureLake:
        def sql(self, q: str):
            seen.append(q)
            return []

    assert cells(_CaptureLake(), ETF, "2026-08-20", "2026-08-21") == []
    assert len(seen) == 1
    assert weighted_asof_subquery(ETF) in seen[0]
    assert "max(as_of_date)" not in seen[0]


def test_gate_subquery_prefers_valid_partition_and_empties_when_all_poisoned():
    """서브쿼리 단독 계약 — 정상 파티션 선택, 전부 오염이면 NULL(= 행 0건 경로).

    전부 오염일 때 layers 는 KRX 0행 → FMP 폴백 → (없으면) 빈 목록으로 식는다 —
    원화현금 1행짜리 '가짜 분해'보다 빈 결과가 낫다는 것이 이 계약의 방향이다.
    """
    lake = _duck_lake()
    assert lake.sql(f"SELECT {weighted_asof_subquery(ETF, POISON_DAY)}")[0][0] \
        == date.fromisoformat(GOOD_DAY)

    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE s3_etf_holdings (market VARCHAR, etf_id VARCHAR,"
        " constituent_ticker VARCHAR, constituent_name VARCHAR,"
        " weight_pct DOUBLE, as_of_date DATE)"
    )
    con.execute(
        "INSERT INTO s3_etf_holdings VALUES ('KR', ?, ?, '원화현금', 100.0, ?)",
        [ETF, CASH, POISON_DAY],
    )
    for i in range(9):
        con.execute(
            "INSERT INTO s3_etf_holdings VALUES ('KR', ?, ?, ?, NULL, ?)",
            [ETF, f"{100000 + i}", f"종목{i}", POISON_DAY],
        )
    assert con.execute(f"SELECT {weighted_asof_subquery(ETF, POISON_DAY)}").fetchall()[0][0] is None

    class _PoisonOnlyLake:
        def sql(self, q: str):
            return con.execute(q).fetchall()

    assert layers.holdings(_PoisonOnlyLake(), ETF, POISON_DAY) == []
