from edge_analysis.statics.window.interval import WindowFacts


def _facts():
    return WindowFacts(
        ticker="091160", name="KODEX 반도체", day="2026-08-05",
        window_start="10:40", window_end="13:20",
        header_return=-0.062, window_return=-0.041,
        advancers=12, decliners=18, market_return=-0.002,
        sector_name="KRX 반도체", sector_return=-0.008,
        path="10:40부터 13:20까지 하락했습니다.",
        lineage=({"view": "bars_5m"},),
        final_lines=(
            "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다.",
            "계약금액 3,200억원, 최근 연매출 대비 0.9% 규모입니다.",
            "시장 요인을 제거한 기준으로, 조건이 비슷한 과거 41건의 공시 당일 "
            "초과수익률은 평균 -3.1%였습니다.",
            "오늘 이 종목의 초과수익률은 -3.6%로, 과거 분포의 중앙값 부근입니다.",
        ),
    )


def test_minute_run_keeps_core_blocks_before_final_explanation(monkeypatch):
    from edge_analysis.statics.window import etfcell

    calls = []
    monkeypatch.setattr(etfcell, "window_facts", lambda *args: _facts())

    def ask(system, user):
        calls.append((system, user))
        return {
            "template": "{window_start}부터 {window_end}까지 {direction}."
        }

    meta = {}
    text = etfcell.run(
        object(), "091160", "2026-08-05", ask,
        instrument_id="iid", window_start="10:40", window_end="13:20",
        window_meta=meta,
    )

    assert not calls
    assert text.startswith(
        "[H] KODEX 반도체 -6.20%\n13:20 기준 · 전일 종가 대비\n\n"
        "[1] 구성종목 기여를 계산하지 못했습니다.\n"
        "구성종목 30종목 중 12종목 상승 · 18종목 하락\n\n"
        "[2] 10:40부터 13:20까지 하락했습니다.\n\n"
        "[3] 시장 대비 -3.90%p\n"
        "KRX 반도체 대비 -3.30%p\n\n"
        "[4] "
    )
    assert text.split("[4] ", 1)[1].splitlines() == [
        "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다.",
        "계약금액 3,200억원, 최근 연매출 대비 0.9% 규모입니다.",
        "시장 요인을 제거한 기준으로, 조건이 비슷한 과거 41건의 공시 당일 "
        "초과수익률은 평균 -3.1%였습니다.",
        "오늘 이 종목의 초과수익률은 -3.6%로, 과거 분포의 중앙값 부근입니다.",
    ]
    assert "쉬운 설명" not in text and "요청창" not in text
    assert text.count("10:31") == 1
    assert meta["window_start"] == "10:40" and meta["as_of"] == "13:20"
    assert [block["kind"] for block in meta["blocks"]] == [
        "header", "contribution", "breadth", "path", "relative", "absence", "evidence",
    ]
    assert meta["lineage"][0]["view"] == "bars_5m"
    assert meta["final_explanation"]["rendered_text"] == text
    assert [block["block_code"] for block in meta["final_explanation"]["blocks"]] == [
        "H", "1", "2", "3", "4",
    ]


def test_model_cannot_replace_the_final_explanation(monkeypatch):
    from edge_analysis.statics.window import etfcell

    monkeypatch.setattr(etfcell, "window_facts", lambda *args: _facts())
    text = etfcell.run(
        object(), "091160", "2026-08-05",
        lambda *_: {"template": "삼성전자 -2.1% 때문입니다."},
        instrument_id="iid", window_start="10:40", window_end="13:20",
    )

    assert "SK하이닉스 공급계약 해지 공시" in text
    assert "삼성전자" not in text
