import datetime as dt
import re
from types import SimpleNamespace

from edge_analysis.statics.interval import (
    BLOCK_ORDER,
    MIN_N,
    ContributionFact,
    EventDistributionFact,
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

    monkeypatch.setattr("edge_analysis.statics.interval.decompose", fake_decompose)
    def fake_premium(lake, ticker, day, **window):
        premium_calls.append(window)
        return SimpleNamespace(premium_move=0.011), "ok"

    monkeypatch.setattr("edge_analysis.statics.interval.premium_5m", fake_premium)
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


def test_injected_rollup_is_used_instead_of_requerying(monkeypatch):
    """호출자가 준 층 분해가 있으면 **다시 질의하지 않는다**.

    `pipeline` 은 이 분해로 route_code 를 정해 원장에 넣고, 같은 창의 산문을 여기서
    만든다. 여기서 재질의하면 그 사이 분봉 canonical 이 정정될 때 한 explanation 안의
    라우팅 근거와 산문 근거가 갈린다 - 재질의하지 않는 것이 계약이다.
    """
    calls = []

    def fake_decompose(lake, ticker, day, *, clock=None):   # pragma: no cover - 불려선 안 된다
        calls.append(clock)
        return SimpleNamespace(etf_name="재질의", layers=(), names=())

    monkeypatch.setattr("edge_analysis.statics.interval.decompose", fake_decompose)
    monkeypatch.setattr(
        "edge_analysis.statics.interval.premium_5m",
        lambda *a, **k: (SimpleNamespace(premium_move=0.0), "ok"))

    injected = SimpleNamespace(
        etf_name="주입분",
        layers=(SimpleNamespace(kind="섹터", ret=-0.008, name="KRX 반도체"),),
        names=(SimpleNamespace(label="삼성전자", contribution=-0.02, ret=-0.03),),
    )
    facts = window_facts(_Lake(), "091160", "iid", "2026-08-05", "10:40", "13:20",
                         roll=injected)

    assert calls == [], "주입분이 있는데 재질의했다"
    # 주입분이 실제로 쓰였다 - 호출만 안 한 게 아니라 그 값이 사실로 나온다
    assert facts.sector_name == "KRX 반도체"
    assert [c.name for c in facts.contributions] == ["삼성전자"]


def test_injected_none_rollup_is_not_retried_here(monkeypatch):
    """`None` 도 전달된 값이다 — **라우팅이 못 얻었다**는 사실이라 재시도하지 않는다.

    재시도하면 라우팅 시점엔 없던 재료가 그새 착지했을 때 원장은 미상(`PRICE_ONLY`)인데
    산문에는 층 근거가 실린다. 그게 이 주입이 막으려는 갈림 그대로다 — `None` 을 "안
    넘겼다"로 읽으면 막으려던 구멍이 그 경로로 되살아난다.
    """
    calls = []

    def fake_decompose(lake, ticker, day, *, clock=None):   # pragma: no cover - 불려선 안 된다
        calls.append(clock)
        return SimpleNamespace(etf_name="재질의", layers=(), names=())

    monkeypatch.setattr("edge_analysis.statics.interval.decompose", fake_decompose)
    monkeypatch.setattr(
        "edge_analysis.statics.interval.premium_5m",
        lambda *a, **k: (SimpleNamespace(premium_move=0.0), "ok"))

    facts = window_facts(_Lake(), "091160", "iid", "2026-08-05", "10:40", "13:20",
                         roll=None)

    assert calls == [], "None 을 미전달로 읽어 재질의했다"
    assert facts.sector_name is None
    assert facts.contributions == ()


def test_window_end_event_is_available_for_the_requested_window(monkeypatch):
    """PIT의 available_at <= window_end는 끝 시각 사건을 포함한다."""
    monkeypatch.setattr(
        "edge_analysis.statics.interval.decompose",
        lambda *args, **kwargs: SimpleNamespace(etf_name="T", layers=(), names=()))
    monkeypatch.setattr(
        "edge_analysis.statics.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))

    facts = window_facts(
        _Lake(), "091160", "iid", "2026-08-05", "10:40", "13:20")

    assert "요청창 사건 after" in facts.disclosures


def test_future_and_unknown_tau_events_never_surface(monkeypatch):
    """PIT 클램프의 반례: 요청 끝(as_of) 뒤에 관측된 사건·시각 미상(자정 폴백)
    사건이 표면에 오르면 이 테스트가 깨져야 한다 - 미래 관측이 산문에 들어오는
    순간 PIT 위반이다."""
    monkeypatch.setattr(
        "edge_analysis.statics.interval.decompose",
        lambda *args, **kwargs: SimpleNamespace(etf_name="T", layers=(), names=()))
    monkeypatch.setattr(
        "edge_analysis.statics.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))

    class FutureLake(_Lake):
        def taus(self, instrument_id, day):
            return [
                (dt.datetime(2026, 8, 5, 14, 0), "future"),     # as_of 뒤 관측
                (dt.datetime(2026, 8, 5, 0, 0), "unknown_tau"),  # 자정 = 시각 미상
            ]

    facts = window_facts(
        FutureLake(), "091160", "iid", "2026-08-05", "10:40", "13:20")

    assert facts.event_ids == ()
    assert facts.disclosures == ()
    text = render_block_plan(build_block_plan(facts))
    assert "future" not in text and "unknown_tau" not in text
    assert "해당 구간에 확인된 공시·보도는 없습니다." in text


def test_disclosure_lines_carry_time_and_gist(monkeypatch):
    """창 안 사건은 id 가 아니라 **시각 + 요지**로 disclosure 블록에 오른다."""
    monkeypatch.setattr(
        "edge_analysis.statics.interval.decompose",
        lambda *args, **kwargs: SimpleNamespace(etf_name="T", layers=(), names=()))
    monkeypatch.setattr(
        "edge_analysis.statics.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))

    class TitledLake(_Lake):
        def sql(self, query):
            if "SELECT source_event_id, title" in query and "LIMIT 1" not in query:
                return [("inside", "유상증자 결정 공시."), ("after", None)]
            return super().sql(query)

    facts = window_facts(
        TitledLake(), "091160", "iid", "2026-08-05", "10:40", "13:20")

    # 제목이 있으면 시각+요지, 없으면 id 폴백 - 조회 실패가 사건을 지우지 않는다.
    assert facts.disclosures == ("10:45, 유상증자 결정 공시", "요청창 사건 after")


def test_clamp_rejects_a_window_empty_after_session_cut():
    """장과 겹치지 않는 요청을 하루 전체로 바꾸지 않는다."""
    import pytest
    from edge_analysis.statics.interval import IntervalError, clamp

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


def test_statistical_output_declares_a_structural_evidence_requirement():
    payload = final_explanation_payload(_facts(statistics=(StatisticFact(
        claim="계약 체결 뒤 평균 수익률", n=MIN_N,
        effect=0.013, p=0.004, evidence_ids=("s1",),
    ),)))

    statistical = next(block for block in payload["blocks"]
                       if block["block_code"] == "4")
    assert statistical["evidence_requirement"] == "CAUSAL_STAT_TEST"
    assert statistical["source_systems"] == ["ANALYSIS.stat_tests"]



def test_final_payload_uses_named_blocks_and_traceable_references():
    """최종 JSONB는 H→4 순서와 근거 조회키를 동시에 보존한다."""
    facts = _facts(
        contributions=(ContributionFact(
            "삼성전자", 0.012, evidence_ids=("bars_5m:005930",)),),
        disclosures=("요청창 사건 evt_1",),
        final_lines=("14:20, 공급계약 공시가 있었습니다.",),
        event_ids=("evt_1",),
    )

    payload = final_explanation_payload(facts)

    assert [b["block_code"] for b in payload["blocks"]] == ["H", "1", "2", "3", "4"]
    assert payload["rendered_text"].startswith("[H] KODEX 반도체")
    assert "\n\n[4] 14:20, 공급계약 공시가 있었습니다." in payload["rendered_text"]
    event = payload["blocks"][-1]
    assert event["source_systems"] == ["RDB.source_event"]
    assert event["evidence_refs"] == ["source_event:evt_1"]


def test_final_payload_emits_absence_only_when_optional_blocks_are_empty():
    """이벤트·통계 블록이 전부 비면 4를 꾸미지 않고 N을 남긴다."""
    payload = final_explanation_payload(_facts())

    assert [b["block_code"] for b in payload["blocks"]] == ["H", "1", "2", "3", "N"]
    assert payload["blocks"][-1]["block_title"] == "부재 고지"
    assert "확인된 공시·보도는 없습니다" in payload["blocks"][-1]["text"]


def test_ready_event_distribution_renders_one_grounded_customer_paragraph():
    payload = final_explanation_payload(_facts(
        news=("기존 뉴스 목록은 고객 문장으로 쓰지 않는다.",),
        event_ids=("evt_selected", "evt_other"),
        event_distributions=(EventDistributionFact(
            source_event_id="evt_selected", title="포스코퓨처엠의 LFP 장기공급 합의",
            available_at="2026-08-05T09:49:00", evidence_id="ev_title",
            n=41, mean=-0.031, today=-0.036, percentile=0.42,
            event_type_code="COMPANY.CONTRACT.SIGNING",
        ),),
    ))
    event = payload["blocks"][-1]

    assert event["block_code"] == "4"
    # 어휘 계약(ALPHA-943): 유형은 한국어 라벨("계약 체결")로 명시, 표본은 여러
    # 종목의 횡단면이라 "해당 종목들"·"건" 단위, percentile 은 부호 포함 ECDF 라
    # "높게"(0.42 → 58%가 오늘보다 높았다). "하위 42%"(ALPHA-937 이전)나
    # "크게"(절대폭 오서술)로 되돌리는 회귀는 이 단언이 깨뜨린다.
    assert event["text"] == (
        "09:49, 포스코퓨처엠의 LFP 장기공급 합의 소식이 있었습니다. "
        "과거에 계약 체결 소식이 있었던 41건의 사례에서, 해당 종목들은 소식 당일 "
        "시장 대비 평균 -3.10% 움직였습니다. 오늘 이 종목은 시장 대비 -3.60%로, "
        "과거 41건 중 약 59%는 오늘보다 높게 움직였습니다."
    )
    assert event["evidence_refs"] == ["source_event:evt_selected"]


def test_event_distribution_near_zero_mean_reads_as_about_zero():
    """mean 이 반올림으로 ±0.00% 가 되는 언더플로는 "0% 부근"으로 말한다 — "평균
    +0.00% 움직였습니다"는 정보가 없는 문장이다(2026-08-11 첫 RENDERED 실측
    mean=7.9e-06). 일반 수익률(today)의 +.2f 표기는 불변이어야 한다."""
    payload = final_explanation_payload(_facts(
        event_ids=("evt_zero",),
        event_distributions=(EventDistributionFact(
            source_event_id="evt_zero", title="한미반도체 미국 법인 설립",
            available_at="2026-08-10T15:49:00", evidence_id="ev_title",
            n=871, mean=7.9e-06, today=0.0127, percentile=0.69,
            event_type_code="COMPANY.COMMERCIAL.MARKET_ENTRY",
        ),),
    ))
    event = payload["blocks"][-1]

    assert "평균 0% 부근에서 움직였습니다" in event["text"]
    assert "+0.00%" not in event["text"]
    assert "과거에 시장 진출·철수 소식이 있었던 871건" in event["text"]
    assert "오늘 이 종목은 시장 대비 +1.27%로" in event["text"]
    assert "약 31%는 오늘보다 높게 움직였습니다" in event["text"]


_PROSE_BANNED = (
    # 산문 금지어 게이트(ALPHA-943) - 고객 산문(rendered_text)에 통계·내부 어휘가
    # 다시 스며드는 것을 구조적으로 막는다(plain.BANNED·JARGON 선례의 interval 판).
    # ⚠️ 현재 픽스처는 **사건 분포 경로**를 덮는다. 통계 검정 병치 경로([4] 의
    # "표본이 부족해"·"p=0.0040" — interval 이 ALPHA-876 §0 과 충돌 중인 기존 부채)와
    # [3] 요인 어휘("요인"·"%p")는 후속 어휘 개편 티켓에서 픽스처·목록에 함께 올린다.
    "시장초과수익률", "초과수익률", "분포", "백분위", "표본", "ECDF", "유의", "p값",
)
_PROSE_BANNED_PATTERNS = (
    r"[A-Z]{2,}\.[A-Z_.]{2,}",   # 사건 유형 코드 원문
    r"(상위|하위)\s?\d+%",        # 방향 백분위 표현(ALPHA-943 에서 문장형으로 대체)
    r"\bp\s?=\s?\d",             # p값 직출 표기(통계 병치 경로의 실제 형태)
)


def test_customer_prose_never_contains_banned_vocabulary():
    """게이트: 사건 분포 경로의 rendered_text 에 금지 어휘가 없다 — 새 문장이
    추가될 때 이 테스트가 어휘 계약을 지키게 한다(경로 확장은 목록 주석의 TODO)."""
    payload = final_explanation_payload(_facts(
        news=("보도 한 줄.",),
        event_ids=("evt_a",),
        event_distributions=(EventDistributionFact(
            source_event_id="evt_a", title="포스코퓨처엠의 LFP 장기공급 합의",
            available_at="2026-08-05T09:49:00", evidence_id="ev_title",
            n=41, mean=-0.031, today=-0.036, percentile=0.42,
            event_type_code="COMPANY.CONTRACT.SIGNING",
        ),),
    ))
    text = payload["rendered_text"]

    for word in _PROSE_BANNED:
        assert word not in text, (word, text)
    for pattern in _PROSE_BANNED_PATTERNS:
        assert not re.search(pattern, text), (pattern, text)


def test_higher_share_clause_states_facts_even_at_extremes():
    """비율 절은 사실 그대로다 — 순위 표시용 클램프를 재사용하면 극단에서 왜곡된다
    (p=1.0·n=30 이면 높은 사례 0건인데 "약 4%"). 극단은 건수로 말한다."""
    from edge_analysis.statics.interval import _higher_share_clause

    assert _higher_share_clause(0.26, 213) == (
        "과거 213건 중 약 74%는 오늘보다 높게 움직였습니다")
    assert _higher_share_clause(1.0, 30) == "오늘보다 높게 움직인 사례는 없었습니다"
    assert _higher_share_clause(0.0, 30) == "과거 30건 모두 오늘보다 높게 움직였습니다"
    # 반올림이 0%·100% 로 접히는 자리는 백분율 대신 건수 — "약 0%"는 거짓이다.
    assert _higher_share_clause(1 - 1 / 871, 871) == (
        "과거 871건 중 1건이 오늘보다 높게 움직였습니다")
    assert _higher_share_clause(1 / 871, 871) == (
        "과거 871건 중 870건이 오늘보다 높게 움직였습니다")

def test_final_explanation_never_reads_a_prior_analysis_output():
    """A previous run's output must never become evidence for the current run."""
    from edge_analysis.statics.interval import _final_lines

    class Lake:
        def __init__(self):
            self.queries = []

        def sql(self, query):
            self.queries.append(query)
            if "source_event" in query:
                return [("e1", "SK하이닉스 공급계약 해지 공시")]
            if "s3_supply_fact" in query:
                return [(320_000_000_000, 0.9)]
            if "analysis_evidence_bundle" in query:
                raise AssertionError("prior output relation was read")
            raise AssertionError(query)

    lake = Lake()
    lines = _final_lines(
        lake, "000660", "2026-08-05", ("e1",),
        {"e1": dt.datetime(2026, 8, 5, 10, 31)},
    )

    assert lines == (
        "10:31, SK하이닉스 공급계약 해지 공시가 있었습니다.",
        "계약금액 3,200억원, 최근 연매출 대비 0.9% 규모입니다.",
    )
    assert " ".join(lines).count("10:31") == 1
    assert not any("analysis_evidence_bundle" in query for query in lake.queries)


def test_missing_requested_window_return_fails_loud(monkeypatch):
    """봉 부재는 보합 0%가 아니다."""
    import pytest

    monkeypatch.setattr(
        "edge_analysis.statics.interval.decompose",
        lambda *args, **kwargs: SimpleNamespace(etf_name="T", layers=(), names=()))
    monkeypatch.setattr(
        "edge_analysis.statics.interval.premium_5m",
        lambda *args, **kwargs: (None, "없음"))

    with pytest.raises(ValueError, match="5분 수익률을 계산하지 못했습니다"):
        window_facts(_Lake(), "091160", "iid", "2026-08-05", "11:00", "11:05")

# ── 기여회계 산문 (ALPHA-871) ─────────────────────────────────────────────
def test_factor_block_states_the_layer_accounting_without_proxy_names():
    """[3] 은 층 기여회계다 - "시장 요인 X%p · 섹터 요인 Y%p · 고유 요인 Z%p".

    상대비교("X 대비")는 층 회계와 다른 프레임이고 프록시 상품명을 노출했다
    (이름 오염 41/80 이 사용자에게 보이던 경로). 섹터 소스가 업종지수 1분봉으로
    바뀌어도 이 형식은 불변이어야 한다 - 상품명이 다시 나타나면 이 테스트가 깨진다.
    """
    text = render_block_plan(build_block_plan(_facts(
        market_contribution=-0.002, sector_contribution=-0.006,
        idio_contribution=-0.033)))
    assert "시장 요인 -0.20%p · 섹터 요인 -0.60%p · 고유 요인 -3.30%p" in text
    assert "KRX 반도체" not in text, "프록시·지수명이 산문에 노출됐다"
    assert "시장 대비" not in text and "대비 " not in text


def test_factor_block_omits_the_sector_term_when_no_sector_layer_stood():
    """섹터 층이 없으면 항을 생략한다 - 0 으로 지어내지 않는다."""
    text = render_block_plan(build_block_plan(_facts(
        market_contribution=-0.002, sector_contribution=None,
        idio_contribution=-0.039)))
    assert "시장 요인 -0.20%p · 고유 요인 -3.90%p" in text
    assert "섹터 요인" not in text


def test_factor_block_admits_when_no_layer_stood():
    """층이 아예 없으면 미계측을 말한다 - 빈 회계를 0 요인으로 위장하지 않는다."""
    text = render_block_plan(build_block_plan(_facts()))
    assert "층 미계측" in text
    assert "시장 요인" not in text


# ── 가설 제안 사건 문맥 (ALPHA-885) ──────────────────────────────────────
class _ContextLake:
    """직전 거래일~창 끝 스레드 문맥 조회의 가짜 표면.

    질의는 내용 표식으로 분기한다 - thread_context 의 각 조회(캘린더·τ·상세·인자·
    수치·리드·직전 스레드)가 무엇을 묻는지가 이 분기 목록이다.
    """

    exists = {"rdb": True}

    def __init__(self, *, fail_detail: bool = False):
        self.fail_detail = fail_detail
        self.queries: list[str] = []

    def taus(self, iid, day):
        if day == "2026-08-05":
            return [
                (dt.datetime(2026, 8, 5, 10, 31), "e1"),
                # τ > as_of(13:20) - PIT 위반. 문맥에 실리면 깨져야 한다.
                (dt.datetime(2026, 8, 5, 14, 50), "e_future"),
            ]
        return [(dt.datetime(2026, 8, 4, 16, 0), "e0")]

    def sql(self, q):
        self.queries.append(q)
        if "max(CAST(ts AS DATE))" in q:
            return [("2026-08-04",)]
        if self.fail_detail:
            if q.lstrip().startswith("WITH") or "event_argument" in q \
                    or "event_measure" in q:
                raise RuntimeError("표면 죽음")
            if "FROM rdb.public.source_event WHERE source_event_id IN" in q:
                return [("e1", "CONTRACT.SIGNING"), ("e0", "CONTRACT.SIGNING")]
            raise RuntimeError("표면 죽음")
        if q.lstrip().startswith("WITH"):
            # base(views_sql)가 기반 테이블명을 전부 품으므로 본문 SELECT 로만 가른다.
            if "any_value(n.lead_text)" in q:
                return [("e1", "SK하이닉스가 공급계약 해지를 공시했다.")]
            if "SELECT DISTINCT e.thread_id" in q:
                return [("th1", "2026-07-30 09:00:00", "CONTRACT.SIGNING",
                         "공급계약 루머 보도")]
            assert "SELECT DISTINCT e.source_event_id" in q, f"예상 밖 질의: {q[-160:]}"
            assert "e_future" not in q, "PIT 위반 사건이 상세 조회에 들어갔다"
            return [
                ("e1", "CONTRACT.SIGNING", "SK하이닉스 공급계약 해지", "th1",
                 "FOLLOW_UP_STAGE", "CONFIRMED"),
                ("e0", "CONTRACT.SIGNING", "공급계약 협상 착수", "th1",
                 "FIRST_IN_THREAD", "RUMORED"),
            ]
        if "FROM rdb.public.event_argument ea" in q:
            return [("e1", "ISSUER", "SK하이닉스")]
        if "FROM rdb.public.event_measure" in q:
            return [("e1", "CONTRACT_AMOUNT", 3200.0, "억원")]
        raise AssertionError(f"예상 밖 질의: {q[:120]}")


def test_thread_context_carries_titles_and_prev_day_events():
    """창 안 사건(제목·τ)과 **직전 거래일 사건**이 구분 표기로 실린다.

    수집 구간이 창 안뿐이면 밤사이·전일 장중 재료가 통째로 빠진다(ALPHA-885 정정).
    제목·τ 가 안 실리면 제안이 타입 코드만 보고 가설을 세운다 - 그게 고칠 대상이다.
    """
    from edge_analysis.statics.interval import thread_context

    blocks, n, fails = thread_context(
        _ContextLake(), "iid", "2026-08-05", "13:20", ("e1",))

    text = "\n\n".join(blocks)
    assert n == 2 and fails == 0
    assert "[설명창 안] 08-05 10:31 CONTRACT.SIGNING — SK하이닉스 공급계약 해지" in text
    assert "[직전 거래일~창 시작] 08-04 16:00" in text and "공급계약 협상 착수" in text
    assert "리드: SK하이닉스가 공급계약 해지를 공시했다." in text
    assert "인자: ISSUER=SK하이닉스" in text
    assert "수치: CONTRACT_AMOUNT=3200.0 억원" in text
    assert "스레드(CONFIRMED·FOLLOW_UP_STAGE): 직전 2026-07-30 09:00" in text


def test_thread_context_excludes_pit_violating_events():
    """τ > as_of 사건은 문맥에 못 들어온다 - 실리면 PIT 위반이다."""
    from edge_analysis.statics.interval import thread_context

    blocks, n, _fails = thread_context(
        _ContextLake(), "iid", "2026-08-05", "13:20", ("e1",))

    text = "\n".join(blocks)
    assert "e_future" not in text and "14:50" not in text
    assert n == 2


def test_thread_context_falls_back_to_type_codes_on_lookup_failure():
    """표면 조회가 죽어도 사건은 타입 코드로 남고 실패 수가 보고된다.

    조회 실패가 사건 존재를 지우면, 침묵 폴백이 실행마다 제안 입력을 흔든다 -
    그 관측 라인이 이 반환값(fails)이다.
    """
    from edge_analysis.statics.interval import thread_context

    blocks, n, fails = thread_context(
        _ContextLake(fail_detail=True), "iid", "2026-08-05", "13:20", ("e1",))

    text = "\n".join(blocks)
    assert n == 2 and fails >= 1
    assert "CONTRACT.SIGNING" in text, "타입 코드 폴백이 사라졌다"
    assert "[설명창 안]" in text and "[직전 거래일~창 시작]" in text


def test_thread_context_caps_events_and_says_the_overflow():
    """상한 초과분은 "외 N건" 으로 말한다 - 조용한 절단 금지."""
    from edge_analysis.statics.interval import thread_context

    class _Many(_ContextLake):
        def taus(self, iid, day):
            if day != "2026-08-05":
                return []
            return [(dt.datetime(2026, 8, 5, 9, i + 1), f"e{i}") for i in range(5)]

        def sql(self, q):
            if "max(CAST(ts AS DATE))" in q:
                return [("2026-08-04",)]
            return []

    blocks, n, _fails = thread_context(
        _Many(), "iid", "2026-08-05", "13:20", (), max_events=3)

    assert n == 3
    assert blocks[-1] == "외 2건 - 상한 3건 초과 (수집 구간 사건 5건)"
