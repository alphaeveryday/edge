import datetime as dt
from types import SimpleNamespace

from edge_analysis.statics.window.interval import (
    BLOCK_ORDER,
    MIN_N,
    ContributionFact,
    StatisticFact,
    WindowFacts,
    build_block_plan,
    final_explanation_payload,
    explain,
    render_block_plan,
    window_facts,
)


def _facts(**changes):
    base = dict(
        ticker="091160",
        name="KODEX 반도체",
        day="2026-08-05",
        window_start="10:40",
        window_end="13:20",
        header_return=-0.062,
        window_return=-0.041,
        advancers=12,
        decliners=18,
        market_return=-0.002,
        sector_name="KRX 반도체",
        sector_return=-0.008,
        path="10:40부터 13:20까지 하락 폭이 커졌습니다.",
        disclosures=(),
        news=(),
    )
    base.update(changes)
    return WindowFacts(**base)


def test_render_describes_only_the_requested_window():
    text = render_block_plan(build_block_plan(_facts()))

    assert "13:20 기준 · 전일 종가 대비" in text
    assert "10:40부터 13:20까지" in text
    assert "해당 구간에 확인된 공시·보도는 없습니다." in text
    assert "09:00" not in text
    assert "갱신됨" not in text
    assert "supersedes" not in text


def test_block_order_is_fixed_and_thin_statistics_hide_all_estimates():
    facts = _facts(statistics=(StatisticFact(
        claim="계약 체결 뒤 평균 수익률이 높았습니다.", n=MIN_N - 1,
        effect=0.013, p=0.004, evidence_ids=("s1",),
    ),))

    plan = build_block_plan(facts)
    keys = [block.key for block in plan]
    assert keys == [key for key in BLOCK_ORDER if key in keys]

    text = render_block_plan(plan)
    assert "표본이 부족해 판단하지 않았습니다" in text
    assert "0.013" not in text
    assert "0.004" not in text
    assert "계약 체결 뒤" not in text


def test_contributions_keep_three_per_direction_and_report_breadth():
    contributions = tuple(
        ContributionFact(f"상승{i}", i / 1000) for i in range(1, 5)
    ) + tuple(
        ContributionFact(f"하락{i}", -i / 1000) for i in range(1, 5)
    )

    text = render_block_plan(build_block_plan(_facts(contributions=contributions)))

    assert "상승4" in text and "상승3" in text and "상승2" in text
    assert "상승1" not in text
    assert "하락4" in text and "하락3" in text and "하락2" in text
    assert "하락1" not in text
    assert "12종목 상승 · 18종목 하락" in text


def test_summary_mode_changes_only_display_not_plan_or_facts():
    facts = _facts()
    plan = build_block_plan(facts)

    numeric = render_block_plan(plan)
    summary = render_block_plan(plan, summary=True)

    assert [b.key for b in plan] == [b.key for b in build_block_plan(facts)]
    assert facts.header_return == -0.062 and facts.window_return == -0.041
    assert "-6.20%" in numeric
    assert "-6.20%" not in summary
    assert "큰 폭 하락" in summary


class _Lake:
    exists = {"rdb": True}

    def sql(self, query):
        if "SELECT ts, open, close" in query:
            return [
                (dt.datetime(2026, 8, 5, 9, 0), 100, 101),
                (dt.datetime(2026, 8, 5, 10, 40), 101, 102),
                (dt.datetime(2026, 8, 5, 10, 45), 102, 103),
                (dt.datetime(2026, 8, 5, 13, 20), 103, 120),
            ]
        if "GROUP BY 1 ORDER BY 1 DESC LIMIT 2" in query:
            return [("2026-08-05", 100, 103), ("2026-08-04", 98, 99)]
        return []

    def taus(self, instrument_id, day):
        return [
            (dt.datetime(2026, 8, 5, 9, 10), "before"),
            (dt.datetime(2026, 8, 5, 10, 45), "inside"),
            (dt.datetime(2026, 8, 5, 13, 20), "after"),
        ]


def test_window_facts_use_requested_clock_and_pit_cut(monkeypatch):
    calls = []
    premium_calls = []
    roll = SimpleNamespace(
        etf_name="KODEX 반도체",
        layers=(
            SimpleNamespace(kind="시장", ret=-0.002),
            SimpleNamespace(kind="섹터", ret=-0.008, name="KRX 반도체"),
        ),
        names=(
            SimpleNamespace(label="삼성전자", contribution=-0.02, ret=-0.03),
            SimpleNamespace(label="SK하이닉스", contribution=0.01, ret=0.02),
        ),
    )

    def fake_decompose(lake, ticker, day, *, clock=None):
        calls.append(clock)
        return roll

    monkeypatch.setattr("edge_analysis.statics.window.interval.decompose", fake_decompose)
    def fake_premium(lake, ticker, day, **window):
        premium_calls.append(window)
        return SimpleNamespace(premium_move=0.011), "ok"

    monkeypatch.setattr("edge_analysis.statics.window.interval.premium_5m", fake_premium)
    facts = window_facts(_Lake(), "091160", "iid", "2026-08-05", "10:40", "13:20")

    assert calls == [("10:40:00", "13:20:00")]
    assert facts.window_start == "10:40" and facts.window_end == "13:20"
    assert facts.disclosures == (
        "요청창 사건 inside", "요청창 사건 after")
    assert "before" not in repr(facts)
    assert facts.advancers == 1 and facts.decliners == 1
    assert facts.nav_gap == 0.011
    assert premium_calls == [{"window_start": "10:40:00", "window_end": "13:20:00"}]

    text = explain(_Lake(), "091160", "iid", "2026-08-05", "10:40", "13:20",
                   tools=False)
    assert "09:10" not in text and "13:20 기준" in text


def test_window_end_event_is_available_for_the_requested_window(monkeypatch):
    """PIT의 available_at <= window_end는 끝 시각 사건을 포함한다."""
    monkeypatch.setattr(
        "edge_analysis.statics.window.interval.decompose",
        lambda *args, **kwargs: SimpleNamespace(etf_name="T", layers=(), names=()))
    monkeypatch.setattr(
        "edge_analysis.statics.window.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))

    facts = window_facts(
        _Lake(), "091160", "iid", "2026-08-05", "10:40", "13:20")

    assert "요청창 사건 after" in facts.disclosures



def test_window_facts_include_top_constituent_events(monkeypatch):
    """ETF 자체가 아니라 가격 기여 상위 구성종목의 사건도 [4] 후보여야 한다."""
    roll = SimpleNamespace(
        etf_name="KODEX 반도체",
        layers=(),
        names=(SimpleNamespace(
            ticker="005930", label="삼성전자",
            contribution=0.02, ret=0.03),),
    )
    monkeypatch.setattr(
        "edge_analysis.statics.window.interval.decompose", lambda *args, **kwargs: roll)
    monkeypatch.setattr(
        "edge_analysis.statics.window.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))
    monkeypatch.setattr(
        "edge_analysis.statics.window.etfday.instrument_ids",
        lambda *_: {"005930": "iid-samsung"})

    class Lake(_Lake):
        def taus_many(self, instrument_ids, day):
            assert instrument_ids == ("iid-etf", "iid-samsung")
            return [(
                "iid-samsung", dt.datetime(2026, 8, 5, 11, 20),
                "samsung-event")]

        def taus(self, instrument_id, day):
            raise AssertionError("구성종목별 N+1 조회를 하면 안 된다")

        def sql(self, query):
            if "source_event_id IN ('samsung-event')" in query:
                return [("samsung-event", "삼성전자 공급계약 공시")]
            return super().sql(query)

    facts = window_facts(
        Lake(), "091160", "iid-etf", "2026-08-05", "10:40", "13:20")

    assert facts.event_ids == ("samsung-event",)
    assert facts.final_lines == (
        "11:20, 삼성전자 공급계약 공시가 있었습니다.",)

def test_clamp_rejects_a_window_empty_after_session_cut():
    """장과 겹치지 않는 요청을 하루 전체로 바꾸지 않는다."""
    import pytest
    from edge_analysis.statics.window.interval import IntervalError, clamp

    with pytest.raises(IntervalError, match="자른 뒤 구간이 비었다"):
        clamp("00:00", "00:30")


def test_statistics_begin_at_minimum_sample_boundary():
    """MIN_N은 결과를 숨기는 최대값이 아니라 공개 가능한 최소값이다."""
    facts = _facts(statistics=(StatisticFact(
        claim="계약 체결 뒤 평균 수익률", n=MIN_N,
        effect=0.013, p=0.004, evidence_ids=("s1",),
    ),))

    text = render_block_plan(build_block_plan(facts))

    assert "계약 체결 뒤 평균 수익률" in text
    assert "효과 +1.30%p" in text
    assert "p=0.0040" in text



def test_final_payload_uses_named_blocks_and_traceable_references():
    """최종 JSONB는 H→4 순서와 근거 조회키를 동시에 보존한다."""
    facts = _facts(
        contributions=(ContributionFact(
            "삼성전자", 0.012, evidence_ids=("bars_5m:005930",)),),
        disclosures=("요청창 사건 evt_1",),
        final_lines=("14:20, 공급계약 공시가 있었습니다.",),
        event_ids=("evt_1",),
        final_bundle_ids=("ev_0123456789abcdef",),
    )

    payload = final_explanation_payload(facts)

    assert [b["block_code"] for b in payload["blocks"]] == ["H", "1", "2", "3", "4"]
    assert payload["rendered_text"].startswith("[H] KODEX 반도체")
    assert "\n\n[4] 14:20, 공급계약 공시가 있었습니다." in payload["rendered_text"]
    event = payload["blocks"][-1]
    assert event["source_systems"] == ["RDB.source_event", "RDB.analysis_evidence_bundle"]
    assert event["evidence_refs"] == [
        "source_event:evt_1", "analysis_evidence_bundle:ev_0123456789abcdef"]


def test_final_payload_emits_absence_only_when_optional_blocks_are_empty():
    """이벤트·통계 블록이 전부 비면 4를 꾸미지 않고 N을 남긴다."""
    payload = final_explanation_payload(_facts())

    assert [b["block_code"] for b in payload["blocks"]] == ["H", "1", "2", "3", "N"]
    assert payload["blocks"][-1]["block_title"] == "부재 고지"
    assert "확인된 공시·보도는 없습니다" in payload["blocks"][-1]["text"]

def test_final_explanation_binds_event_financial_and_statistical_db_facts():
    """최종 네 문장은 DB 사실을 그대로 쓰고 시각을 한 번만 표시한다."""
    from edge_analysis.statics.window.interval import _final_lines

    class Lake:
        def sql(self, query):
            if "source_event" in query:
                return [("e1", "SK하이닉스 공급계약 해지 공시")]
            if "s3_supply_fact" in query:
                return [(320_000_000_000, 0.9)]
            if "analysis_evidence_bundle" in query:
                return [("ev_0123456789abcdef",
                         {"n": 41, "mean_excess": -0.031,
                          "position": "중앙값 부근"})]
            raise AssertionError(query)

    lines = _final_lines(
        Lake(), "000660", "2026-08-05", ("e1",),
        {"e1": dt.datetime(2026, 8, 5, 10, 31)},
        window_return=-0.038, market_return=-0.002,
    )

    assert lines == (
        "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다.",
        "계약금액 3,200억원, 최근 연매출 대비 0.9% 규모입니다.",
        "시장 요인을 제거한 기준으로, 조건이 비슷한 과거 41건의 공시 당일 "
        "초과수익률은 평균 -3.1%였습니다.",
        "오늘 이 종목의 초과수익률은 -3.6%로, 과거 분포의 중앙값 부근입니다.",
    )
    assert " ".join(lines).count("10:31") == 1


def test_missing_requested_window_return_fails_loud(monkeypatch):
    """봉 부재는 보합 0%가 아니다."""
    import pytest

    monkeypatch.setattr(
        "edge_analysis.statics.window.interval.decompose",
        lambda *args, **kwargs: SimpleNamespace(etf_name="T", layers=(), names=()))
    monkeypatch.setattr(
        "edge_analysis.statics.window.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))

    with pytest.raises(ValueError, match="5분 수익률을 계산하지 못했습니다"):
        window_facts(_Lake(), "091160", "iid", "2026-08-05", "11:00", "11:05")