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
