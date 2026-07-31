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


def test_brief_shows_the_predicate_code_when_present():
    """술어에 쓸 수 있는 값을 안 보여주면 모델이 발명한다.

    실제로 `predicate_code = 'EARNINGS_MISS'` 를 냈고 원장에 없는 값이라 0건이 됐다.
    """
    from edge_analysis.causal.agents import brief

    text = brief(etf_name="X", trade_date="2026-07-29", observed=-0.08, residual=-0.03,
                 route_code="COMMON_FACTOR", contributors=[],
                 candidates=[{"event_type_code": "T", "predicate_code": "RESULT_BEAT"}])

    assert "predicate_code=RESULT_BEAT" in text


def test_brief_omits_the_predicate_code_when_absent():
    """없는 값을 빈 문자열로 보여주면 그것이 유효한 값처럼 읽힌다."""
    from edge_analysis.causal.agents import brief

    text = brief(etf_name="X", trade_date="2026-07-29", observed=-0.08, residual=-0.03,
                 route_code="COMMON_FACTOR", contributors=[],
                 candidates=[{"event_type_code": "T", "predicate_code": None}])

    assert "predicate_code" not in text


def test_worked_example_in_the_prompt_actually_passes_the_guards():
    """프롬프트 예시가 규칙을 통과해야 한다.

    통과하지 않는 예시는 최악이다 - 모델이 그대로 따라 했는데 기각되면 되먹임이
    자기모순이 되고, 무엇을 믿어야 할지 알 수 없어진다. 선언적 규칙 5회가 실패한 뒤
    예시로 전환했으므로, 예시 자체를 검사에 걸어 둔다.
    """
    import json
    import re

    from edge_analysis.adapters.causal_data import COHORT_COLUMNS, UNIVERSE_COLUMNS, _guard
    from edge_analysis.causal import graph as G
    from edge_analysis.causal.agents import SYSTEM, parse

    block = re.search(r"```json\n(.*?)\n```", SYSTEM, re.DOTALL)
    assert block, "프롬프트에 json 예시 블록이 없다"
    out = json.loads(block.group(1))

    nodes, designs, _ = parse(out)
    assert len(designs) == 1

    # run.py:146 과 같은 형태로 만든다 - validate 는 timing 을 간선에서 읽는다.
    edges = [{"from": d.src, "to": d.dst, "timing": d.timing} for d in designs]
    assert G.validate({"nodes": nodes, "structures": [{"id": "A", "edges": edges}]},
                      grounded={"evt_abc123"}, require_competing=False) == []

    _guard(designs[0].treated, COHORT_COLUMNS)
    _guard(designs[0].control, UNIVERSE_COLUMNS)
