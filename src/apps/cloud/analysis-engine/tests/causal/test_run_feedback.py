"""0건 술어 되먹임 — 이유를 구별해서 알려주는가.

0건의 원인이 "그런 사건이 없다"인지 "컬럼을 잘못 골랐다"인지 구별하지 않으면, LLM 이 설계를
고치는 대신 전략을 통째로 갈아탄다. 실제로 클라우드 실행에서 그랬고 2회차가 소진됐다.
"""
from __future__ import annotations

from edge_analysis.causal.run import _surrogate_hint


def test_ticker_in_instrument_id_is_named():
    hint = _surrogate_hint("instrument_id = '000660'")
    assert "ticker" in hint and "inst_" in hint


def test_new_style_krx_code_is_also_caught():
    """0007C0 같은 신형 코드도 티커다 - 숫자 6자리만 보면 놓친다."""
    assert _surrogate_hint("instrument_id = '0007C0'")


def test_correct_predicate_gets_no_hint():
    """`ticker` 를 쓴 술어에 정정을 붙이면 되먹임이 거짓이 된다."""
    assert _surrogate_hint("ticker = '000660'") == ""


def test_opaque_id_gets_no_hint():
    """진짜 서로게이트를 쓴 술어는 컬럼 선택이 옳다 - 0건은 다른 이유다."""
    assert _surrogate_hint("instrument_id = 'inst_01KXJB6W2EFQRP1D5TBRF0EBEK'") == ""


def test_brief_exposes_the_real_industry_vocabulary():
    """어휘를 안 보여주면 모델이 값을 추측한다.

    원장의 industry_name 은 원천 원문(영어)이다. 실제로 모델이 `sector_name = '반도체'` 를
    써서 대조군이 0건이 됐다 - 값이 없어서가 아니라 이름이 달라서다.
    """
    from edge_analysis.causal.agents import brief

    text = brief(etf_name="X", trade_date="2026-07-29", observed=-0.08, residual=-0.03,
                 route_code="COMMON_FACTOR", contributors=[], candidates=[],
                 industry={"i1": "Semiconductors", "i2": "Technology", "i3": "Semiconductors"})

    assert "Semiconductors" in text and "Technology" in text
    assert "2종" in text, "중복을 접어 종수를 세야 한다"


def test_brief_without_the_map_omits_the_section():
    """맵이 없으면 빈 목록을 보여주지 않는다 - 빈 어휘는 값이 없다는 거짓 신호다."""
    from edge_analysis.causal.agents import brief

    text = brief(etf_name="X", trade_date="2026-07-29", observed=-0.08, residual=-0.03,
                 route_code="COMMON_FACTOR", contributors=[], candidates=[])

    assert "industry_name 값" not in text
