"""홀딩스 파티션 선택 — 비중 결손 스냅샷 배제 (ALPHA-951).

여기서 고정하는 건 **"비중 없는 스냅샷은 분해의 근거가 아니다"** 이다. 장전 수집분은
구성종목·수량만 오고 비중이 없는 것이 정상인데, NULL 을 0 으로 접으면 그 행이 살아남아
파티션이 '최신'으로 뽑히고 장중 설명의 구성종목 귀속이 통째로 죽는다(2026-08-11 실측,
882/916 행 = 96% NULL). 엔진은 오류 없이 정상 동작하고 결론만 틀리는 실패라 테스트로
고정한다.

판정을 **비중 합으로 하면 안 되는 이유**도 여기서 고정한다 — 그 사고 파티션은 ETF 당
`원화현금 100.0` 한 행만 실값이라 합이 정확히 100 이었다.
"""
from __future__ import annotations

from datetime import date

from edge_analysis.adapters.lake import LakeReader

ETF = "069500"


def _rows(priced: int, missing: int) -> list[dict]:
    """`priced` 건은 비중 실값, `missing` 건은 비중 NULL 인 한 파티션의 행."""
    rows = [
        {"etf_id": ETF, "constituent_ticker": f"P{i:04d}",
         "constituent_name": f"실값{i}", "weight_pct": 100.0 / max(priced, 1)}
        for i in range(priced)
    ]
    rows += [
        {"etf_id": ETF, "constituent_ticker": f"N{i:04d}",
         "constituent_name": f"결손{i}", "weight_pct": None}
        for i in range(missing)
    ]
    return rows


class _StubLake(LakeReader):
    """파티션 목록과 행을 그대로 돌려주는 리더 — 선택 규칙만 검사한다."""

    def __init__(self, partitions: dict[str, list[dict]]) -> None:
        super().__init__(None, "bucket")
        self.partitions = partitions
        self.read = []

    def _partition_values(self, base: str, key: str) -> list[str]:
        return sorted(self.partitions)

    def _read_parquet_prefix(self, prefix: str, columns: list[str]) -> list[dict]:
        as_of = prefix.rstrip("/").rsplit("=", 1)[1]
        self.read.append(as_of)
        return self.partitions[as_of]


def test_weightless_partition_falls_back_to_previous():
    """비중 96% 결손 파티션이 최신이어도 **이전 정상 파티션**이 쓰인다.

    비중 합으로는 못 잡는 형상이다 — 사고 파티션의 유일한 실값이 100.0 이라 합이
    정확히 100 이다. 그런데도 넘어가야 한다.
    """
    lake = _StubLake({
        "2026-08-10": _rows(priced=30, missing=1),
        "2026-08-11": _rows(priced=1, missing=29),  # 실값 1건 = 100.0 → 합 100
    })

    holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-10"
    assert len(holdings) == 30
    assert lake.read == ["2026-08-11", "2026-08-10"]  # 최신부터 훑고 넘어갔다


def test_missing_weight_row_is_dropped_not_zeroed():
    """정상 파티션의 NULL 한 행은 **제외**된다 — 0 으로 접으면 coverage 가 거짓말한다."""
    lake = _StubLake({"2026-08-11": _rows(priced=30, missing=1)})

    holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-11"
    assert [h.ticker for h in holdings] == [f"P{i:04d}" for i in range(30)]
    assert all(h.weight > 0 for h in holdings)


def test_all_partitions_weightless_returns_empty():
    """전 파티션이 비중 결손이면 빈 결과 — 호출부가 fail-loud 할 수 있게 한다.

    여기서 0 비중 목록을 돌려주면 분해가 '전부 0 기여'라는 값을 정상값으로 낸다.
    """
    lake = _StubLake({
        "2026-08-10": _rows(priced=0, missing=20),
        "2026-08-11": _rows(priced=1, missing=29),
    })

    assert lake.load_holdings(ETF, "KR", date(2026, 8, 11)) == ([], None)
