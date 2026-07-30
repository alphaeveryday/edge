"""Yahoo 가격 어댑터 테스트 — 수집 계획(plan) 계약.

**왜 이 어댑터가 있는가:** KR 개별주·ETF 자체 종가는 KIS 가 이미 준다(ALPHA-419 —
holdings 유니버스에 ETF 자신 포함). 빠진 건 **벤치마크 지수**뿐이고, 지수가 없어
L0 상대 게이트가 미적용이다. 그래서 이 어댑터가 고정해야 할 계약은 둘이다:

  1. 지수는 targets/holdings 와 **무관하게 항상** 수집된다 — 지수는 우리 유니버스의
     종목이 아니라 대조축이라 symbols 로 들어올 길이 없다. 여기서 빠지면 존재 이유가 없다.
  2. US 심볼을 KR 로 주워담지 않는다 — `.KS` 를 붙여 질의하면 없는 종목이거나 최악의
     경우 **동명 KR 종목의 시세를 US 티커에 붙인다**(통화·거래시간 오염).

`fetch()` 는 네트워크·yfinance 의존이라 여기서 다루지 않는다.
"""

from data_pipeline.config import YahooPriceSourceConfig
from data_pipeline.sources import YahooPriceSource

_INDEX_MAP = {"KS11": "^KS11", "KQ11": "^KQ11"}


def _source(**over) -> YahooPriceSource:
    config = YahooPriceSourceConfig(index_map=_INDEX_MAP, **over)
    return YahooPriceSource(config)


def test_indices_are_planned_even_with_no_symbols():
    """지수는 symbols 가 비어도 계획에 든다 — 이 어댑터의 존재 이유다."""
    planned = dict(_source().plan([]))

    assert planned == {"KS11": "^KS11", "KQ11": "^KQ11"}


def test_kr_tickers_get_the_exchange_suffix():
    """KR 단축코드는 접미사 규칙으로 Yahoo 심볼이 된다(신형 문자혼합 코드 포함)."""
    planned = dict(_source().plan(["091160", "0167A0"]))

    assert planned["091160"] == "091160.KS"
    assert planned["0167A0"] == "0167A0.KS"  # 신형 단축코드도 6자리라 규칙이 같다


def test_us_symbols_are_not_swept_into_kr():
    """US 심볼에 .KS 를 붙이면 없는 종목이거나 무관한 KR 종목 시세가 붙는다 — 제외한다."""
    planned = dict(_source().plan(["NVDA", "AAPL", "BRK.B", "005930"]))

    assert set(planned) == {"KS11", "KQ11", "005930"}


def test_symbol_map_overrides_the_suffix_rule():
    """KOSDAQ 등 접미사 규칙에서 벗어나는 종목은 명시 맵이 이긴다 — 추정하면 조용히
    다른 시장 종목을 붙인다."""
    planned = dict(_source(symbol_map={"263750": "263750.KQ"}).plan(["263750", "091160"]))

    assert planned["263750"] == "263750.KQ"
    assert planned["091160"] == "091160.KS"


def test_plan_is_deduplicated_and_counted():
    """같은 티커가 두 번 와도 한 번만 질의한다 — planned_symbols 가 실제 호출 수다."""
    source = _source()
    planned = source.plan(["091160", "091160", "KS11"])

    assert [t for t, _ in planned].count("091160") == 1
    assert source.planned_symbols == len(planned) == 3  # 지수 2 + 종목 1


def test_disabled_source_reports_disabled():
    """인증이 없어 enabled 는 설정 플래그가 유일한 스위치다."""
    assert _source(enabled=False).enabled is False
    assert _source().enabled is True
