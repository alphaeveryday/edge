"""Unit tests for the '오늘의 한 줄' card generator (stdlib only; no DB/model)."""
from __future__ import annotations

import pytest

from edge_event_model import config, oneliner


def _analysis(**over):
    """The per-ticker analysis dict shape that analyze_daily builds."""
    a = {
        "ticker": "NVDA", "company": "NVIDIA", "trade_date": "2026-06-19",
        "normal_return": 0.004, "predicted_return": 0.021, "abnormal_return": 0.017,
        "predicted_direction": 1, "is_event": False, "news_count": 3,
        "top_headlines": ["엔비디아, 차세대 GPU 양산 일정 공개", "데이터센터 수요 강세"],
    }
    a.update(over)
    return a


def test_messages_contain_facts_and_json_contract():
    sys_p, user_p = (m["content"] for m in oneliner._messages(_analysis()))
    assert "오늘의 한 줄" in sys_p and '"direction"' in sys_p   # output contract present
    assert "NVIDIA(NVDA)" in user_p and "차세대 GPU" in user_p   # facts injected, no fabrication
    assert "+2.1%" in user_p                                    # predicted_return formatted from input


def test_parse_valid_json():
    txt = ('{"claim1":"양산 6개월 앞당겨,","claim2":"내년 이익 눈높이 상향",'
           '"claim":"양산 일정을 6개월 앞당기며 내년 이익 추정치 상향 흐름",'
           '"direction":"긍정","strength":4,"horizon":"단기"}')
    d = oneliner.parse(txt)
    assert d["direction"] == "긍정" and d["strength"] == 4 and d["horizon"] == "단기"
    assert d["claim1"].endswith(",") and d["source"] == "llm"
    assert set(oneliner.KEYS) <= d.keys()


def test_parse_clamps_strength_and_fixes_horizon_and_tolerates_fence():
    d = oneliner.parse('```json\n{"claim1":"a,","claim2":"b","direction":"부정","strength":9,"horizon":"x"}\n```')
    assert d["strength"] == 5 and d["horizon"] == "단기" and d["direction"] == "부정"
    assert d["claim"] == "a b"  # synthesized when claim omitted


@pytest.mark.parametrize("txt", [
    "no json here",
    '{"claim1":"a"}',                                                       # missing claim2
    '{"claim1":"a,","claim2":"b","direction":"몰라","strength":3,"horizon":"단기"}',  # bad direction
])
def test_parse_rejects_malformed(txt):
    assert oneliner.parse(txt) is None


def test_template_direction_strength_and_shape():
    up = oneliner.template(_analysis(predicted_return=0.03, abnormal_return=0.03, is_event=True))
    assert up["direction"] == "긍정" and up["strength"] == 5
    assert oneliner.template(_analysis(predicted_return=-0.02, abnormal_return=-0.02))["direction"] == "부정"
    assert oneliner.template(_analysis(predicted_return=0.001, abnormal_return=0.0))["direction"] == "중립"
    assert up["claim1"].endswith(",") and set(oneliner.KEYS) <= up.keys()
    no_news = oneliner.template(_analysis(top_headlines=[]))   # card still complete with no news
    assert no_news["claim1"] and no_news["claim2"]


def test_generate_falls_back_without_llm(monkeypatch):
    monkeypatch.setattr(oneliner.llm, "chat", lambda *a, **k: (None, "no-key"))
    d = oneliner.generate_oneliner(_analysis())
    assert d["source"] == "template" and set(oneliner.KEYS) <= d.keys()


def test_generate_uses_llm_when_available(monkeypatch):
    monkeypatch.setattr(oneliner.llm, "chat", lambda *a, **k: (
        '{"claim1":"호재,","claim2":"이익 상향","claim":"호재로 이익 상향","direction":"긍정","strength":3,"horizon":"중기"}', "stub"))
    d = oneliner.generate_oneliner(_analysis())
    assert d["source"] == "llm" and d["horizon"] == "중기" and d["claim1"] == "호재,"


def test_all_nine_us_tickers_get_a_complete_card(monkeypatch):
    """E2E must emit a valid '오늘의 한 줄' for every US universe ticker (9)."""
    monkeypatch.setattr(oneliner.llm, "chat", lambda *a, **k: (None, "no-key"))  # deterministic fallback
    assert len(config.UNIVERSE) == 9
    for asset in config.UNIVERSE:
        d = oneliner.generate_oneliner(_analysis(ticker=asset.ticker, company=asset.company))
        assert set(oneliner.KEYS) <= d.keys(), asset.ticker
        assert d["direction"] in oneliner.DIRECTIONS
        assert 1 <= d["strength"] <= 5
        assert d["horizon"] in oneliner.HORIZONS
        assert d["claim1"] and d["claim2"] and d["claim"]
