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

import logging
from datetime import date

import pytest

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


def test_weightless_partition_falls_back_to_previous(caplog):
    """비중 96% 결손 파티션이 최신이어도 **이전 정상 파티션**이 쓰인다.

    비중 합으로는 못 잡는 형상이다 — 사고 파티션의 유일한 실값이 100.0 이라 합이
    정확히 100 이다. 그런데도 넘어가야 한다.

    건너뛴 사실과 결손율이 **경고로 남는지도 함께 고정한다**. 조용히 넘어가면 어느
    파티션이 왜 거부됐는지 호출부가 알 길이 없다(Rule 12 — 조용한 skip 금지).
    """
    lake = _StubLake({
        "2026-08-10": _rows(priced=30, missing=1),
        "2026-08-11": _rows(priced=1, missing=29),  # 실값 1건 = 100.0 → 합 100
    })

    with caplog.at_level(logging.WARNING):
        holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-10"
    assert len(holdings) == 30
    assert lake.read == ["2026-08-11", "2026-08-10"]  # 최신부터 훑고 넘어갔다
    assert "2026-08-11" in caplog.text and "1/30" in caplog.text


def test_missing_weight_row_is_dropped_not_zeroed():
    """정상 파티션의 NULL 한 행은 **제외**된다 — 0 으로 접으면 coverage 가 거짓말한다."""
    lake = _StubLake({"2026-08-11": _rows(priced=30, missing=1)})

    holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-11"
    assert [h.ticker for h in holdings] == [f"P{i:04d}" for i in range(30)]
    assert all(h.weight > 0 for h in holdings)


def test_exactly_half_missing_is_still_usable():
    """정확히 절반 결손은 **쓴다** — 판정은 '과반 결손'이고 절반은 과반이 아니다.

    경계를 고정하지 않으면 `>=` 를 `>` 로 바꿔도(= 절반 결손 파티션을 거부해도)
    아무 테스트도 안 깨진다. 그 한 글자가 정상 파티션을 통째로 버리게 만든다.
    """
    lake = _StubLake({"2026-08-11": _rows(priced=15, missing=15)})

    holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-11"
    assert len(holdings) == 15


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), -3.0, "12.3", True],
    ids=["nan", "inf", "-inf", "negative", "str", "bool"],
)
def test_bad_weight_does_not_certify_partition(bad):
    """실값이 아닌 값은 **한 종류만으로도** 파티션을 인증하지 못한다.

    NaN 은 비교가 전부 False 라 수치 검사를 조용히 통과한다. 그 행이 실값으로 세어지면
    결손 과반 파티션이 유효로 인증되고, 분해에는 NaN 커버리지가 심긴다 — 오류 없이
    결론만 오염되는 경로다. 문자열·bool 은 트리거 writer 의 `_num` 이 버리는 형이라,
    여기서 받으면 트리거와 설명이 다른 구성종목으로 선다.

    ⚠️ **오염 유형을 한 픽스처에 섞으면 안 된다.** 섞으면 어느 한 규칙을 되돌려도 남은
    규칙이 여전히 과반 게이트에서 걸러, 되돌린 규칙의 회귀가 안 잡힌다. 그래서 유형마다
    **혼자서 판정을 뒤집는** 형상으로 세운다: 불량 16 · NULL 14 = 30. 불량을 실값으로
    세면 16/30 이라 게이트를 통과해 버린다.
    """
    broken = _rows(priced=0, missing=14) + [
        {"etf_id": ETF, "constituent_ticker": f"X{i:04d}", "constituent_name": "불량",
         "weight_pct": bad}
        for i in range(16)
    ]
    lake = _StubLake({"2026-08-10": _rows(priced=30, missing=0), "2026-08-11": broken})

    holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-10"
    assert not any(h.ticker.startswith("X") for h in holdings)


@pytest.mark.parametrize("blank", [None, "", "   ", "\t"], ids=["none", "empty", "spaces", "tab"])
def test_tickerless_rows_are_counted_as_missing_and_warned(caplog, blank):
    """티커 결손 행도 결손으로 세고 경고한다 — 분모에서 미리 빼면 skip 이 안 보인다.

    그 행들을 조용히 버리면 파티션이 '대상 ETF 행 0건'으로 보여 **정상 폴백과 구분되지
    않는다**. 손상된 파티션을 지나쳤다는 사실이 어디에도 안 남는다(Rule 12).

    공백·탭만 있는 티커도 같이 고정한다 — truthy 라 그냥 두면 '유효'로 세어져 과반
    게이트를 통과하는데, 그 파티션으로 분해하면 가격에 매칭되는 종목이 0종이다.
    """
    broken = [
        {"etf_id": ETF, "constituent_ticker": blank, "constituent_name": "무티커",
         "weight_pct": 5.0}
        for _ in range(20)
    ]
    lake = _StubLake({"2026-08-10": _rows(priced=30, missing=0), "2026-08-11": broken})

    with caplog.at_level(logging.WARNING):
        _, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-10"
    assert "2026-08-11" in caplog.text and "0/20" in caplog.text


def test_zero_weight_is_a_real_value():
    """비중 0 은 결손이 아니라 실값 — writer 도 0 을 남긴다(행 규칙 일치).

    0 까지 결손으로 세면 정상 파티션이 과반 게이트에 걸려 버려진다.
    """
    rows = _rows(priced=0, missing=0) + [
        {"etf_id": ETF, "constituent_ticker": "Z0001", "constituent_name": "영",
         "weight_pct": 0.0},
    ]
    lake = _StubLake({"2026-08-11": rows})

    holdings, chosen = lake.load_holdings(ETF, "KR", date(2026, 8, 11))

    assert chosen == "2026-08-11"
    assert [h.weight for h in holdings] == [0.0]


def test_all_partitions_weightless_returns_empty():
    """전 파티션이 비중 결손이면 빈 결과 — 호출부가 fail-loud 할 수 있게 한다.

    여기서 0 비중 목록을 돌려주면 분해가 '전부 0 기여'라는 값을 정상값으로 낸다.
    """
    lake = _StubLake({
        "2026-08-10": _rows(priced=0, missing=20),
        "2026-08-11": _rows(priced=1, missing=29),
    })

    assert lake.load_holdings(ETF, "KR", date(2026, 8, 11)) == ([], None)
