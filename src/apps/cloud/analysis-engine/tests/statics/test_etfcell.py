import pytest
import duckdb

from edge_analysis.config import PipelineError
from edge_analysis.statics.interval import WindowFacts


def _object_lake():
    con = duckdb.connect()
    con.execute("CREATE VIEW v_instrument AS SELECT 'iid' AS instrument_id")
    return type("ObjectLake", (), {"con": con, "bound": {"instrument": None}})()


def _facts():
    return WindowFacts(
        ticker="091160", name="KODEX 반도체", day="2026-08-05",
        window_start="10:40", window_end="13:20",
        header_return=-0.062, window_return=-0.041,
        advancers=12, decliners=18, market_return=-0.002,
        sector_name="KRX 반도체", sector_return=-0.008,
        market_contribution=-0.002, sector_contribution=-0.006,
        idio_contribution=-0.033,
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


def test_run_hands_the_injected_rollup_to_window_facts(monkeypatch):
    """`run` 은 받은 층 분해를 **그대로 전달만** 한다 — 중간에서 흘리면 안 된다.

    이 전달이 끊기면 설명 경로가 조용히 재질의로 돌아가는데, 파이프라인 테스트는
    `etfcell.run` 자체를 가짜로 바꾸고 interval 테스트는 `window_facts` 를 직접 부르므로
    **어느 쪽도 그 회귀를 못 잡는다**. 이 지점만 이 테스트가 본다.
    """
    from edge_analysis.statics import etfcell

    seen = {}

    def fake_window_facts(*args, **kwargs):
        seen.update(kwargs)
        return _facts()

    monkeypatch.setattr(etfcell, "window_facts", fake_window_facts)
    sentinel = object()
    etfcell.run(object(), "091160", "2026-08-05", instrument_id="iid",
                window_start="09:00", window_end="10:35", roll=sentinel)

    assert seen.get("roll") is sentinel, "run 이 주입분을 흘렸다"


def test_daily_run_respects_an_injected_rollup(monkeypatch):
    """하루 모드도 주입분을 쓴다 — 무조건 재대입하면 **파라미터를 조용히 무시**한다.

    호출자는 넘겼다고 믿는데 실제로는 다른 분해로 산문이 만들어진다. 침묵이라 로그로도
    안 드러난다 — 그 갈래를 여기서 고정한다.
    """
    from edge_analysis.statics import etfcell

    calls = []

    def fake_decompose(*a, **k):    # pragma: no cover - 불려선 안 된다
        calls.append(a)
        return None

    monkeypatch.setattr("edge_analysis.statics.layers.decompose", fake_decompose)
    # 주입분이 `None` = "호출자도 못 얻었다" — 재질의 없이 그 사실을 그대로 말한다.
    # 분해 없는 하루 설명은 fail-loud 다(ALPHA-793 이후 문자열 반환이 아니라 raise).
    with pytest.raises(PipelineError, match="층 분해 불가"):
        etfcell.run(object(), "091160", "2026-08-05", roll=None)

    assert calls == [], "주입분이 있는데 하루 모드가 재분해했다"


def test_minute_run_keeps_core_blocks_before_final_explanation(monkeypatch):
    from edge_analysis.statics import etfcell

    calls = []
    monkeypatch.setattr(etfcell, "window_facts", lambda *args, **kwargs: _facts())

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
        "[3] 시장 요인 -0.20%p · 섹터 요인 -0.60%p · 고유 요인 -3.30%p\n\n"
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
    from edge_analysis.statics import etfcell

    monkeypatch.setattr(etfcell, "window_facts", lambda *args, **kwargs: _facts())
    text = etfcell.run(
        object(), "091160", "2026-08-05",
        lambda *_: {"template": "삼성전자 -2.1% 때문입니다."},
        instrument_id="iid", window_start="10:40", window_end="13:20",
    )

    assert "SK하이닉스 공급계약 해지 공시" in text
    assert "삼성전자" not in text


def _tuple():
    from edge_analysis.statics.vocab import (ExposureSource, HypothesisTuple,
                                             Trigger)
    return HypothesisTuple(
        conditions=(), trigger=Trigger("점", "CONTRACT.SIGNING"),
        channel="Q수량", exposure=ExposureSource("속성", "거래량", "수준"),
        outcome="수익률", layer="고유")


def _wire(monkeypatch, reports, rejected=()):
    """요청창 갈래에 가설→검정 사슬을 스텁으로 배선한다. 판정(EdgeReport)만 주입 -
    승격·명시 규율은 실제 코드(`_window_paneltest`)가 돈다."""
    import dataclasses

    from edge_analysis.statics import etfcell

    facts = dataclasses.replace(
        _facts(), final_lines=(), event_ids=("e1",),
        disclosures=("10:31, 공급계약 공시",))
    monkeypatch.setattr(etfcell, "window_facts", lambda *a, **k: facts)
    monkeypatch.setattr("edge_analysis.statics.interval._etypes",
                        lambda lake, eids: ["CONTRACT.SIGNING"])
    monkeypatch.setattr("edge_analysis.statics.paneltest.series_z",
                        lambda lake, iid, day: {})
    tup = _tuple()
    monkeypatch.setattr("edge_analysis.statics.hypothesize.propose",
                        lambda ask, **kw: ([tup] * len(reports), list(rejected)))
    monkeypatch.setattr("edge_analysis.statics.paneltest.edge_tests",
                        lambda lake, tuples, day, cell_instrument_id="":
                        [(tup, r) for r in reports])

    meta = {}
    text = etfcell.run(
        object(), "091160", "2026-08-05", lambda *_: {},
        instrument_id="iid", window_start="10:40", window_end="13:20",
        window_meta=meta)
    return text, meta


def _block_kinds(meta):
    return [block["kind"] for block in meta["blocks"]]


JARGON = ("방아쇠", "노출", "성립", "판정불가", "유의", "p=", "패널", "고유층")


def _clean(text: str) -> bool:
    """고객 산문에 통계 어휘가 없다 - 근거 명세 v3 §0(카드에 통계 어휘 금지)."""
    return not any(w in text for w in JARGON)


def test_significant_verdict_lands_in_the_buffer_not_the_prose(monkeypatch):
    """성립+오늘 적용 검정도 **고객 산문에는 안 실린다**(ALPHA-876) - 레코드는
    stage_results 버퍼(stat_tests)에 남아 근거 포맷(통계검정 레코드 §3)의 입력이
    된다. 검정 원문이 산문에 다시 나타나면 이 테스트가 깨져야 한다(Rule 9)."""
    from edge_analysis.statics.paneltest import EdgeReport

    text, meta = _wire(monkeypatch, [
        EdgeReport("성립", 120, 0.001, 0.012, -0.003, 0.9)])

    assert _clean(text), f"고객 산문에 통계 어휘가 노출됐다: {text}"
    [rec] = meta["stat_tests"]
    assert rec["verdict"] == "성립" and rec["applies_today"] is True
    assert rec["n"] == 120 and rec["p"] == 0.001
    assert rec["trigger"] == "CONTRACT.SIGNING" and rec["layer"] == "고유"


def test_insignificant_verdict_is_recorded_not_rendered(monkeypatch):
    """비유의도 버퍼에 사실로 남되(숨기지 않음 - Rule 12) 산문에는 없다."""
    from edge_analysis.statics.paneltest import EdgeReport

    text, meta = _wire(monkeypatch, [
        EdgeReport("불성립", 120, 0.4, 0.01, -0.002, 0.5)])

    assert _clean(text)
    [rec] = meta["stat_tests"]
    assert rec["verdict"] == "불성립" and rec["applies_today"] is False


def test_undecidable_keeps_its_reason_in_the_buffer(monkeypatch):
    """판정불가는 사유와 함께 버퍼에 남는다 - 유의로 위장하면 깨진다."""
    from edge_analysis.statics.paneltest import EdgeReport

    text, meta = _wire(monkeypatch, [
        EdgeReport("판정불가", 10, None, None, None, None,
                   reason="패널 표본 n=10 < 30 - 백필이 필요하다")])

    assert _clean(text)
    [rec] = meta["stat_tests"]
    assert rec["verdict"] == "판정불가"
    assert "패널 표본" in rec["reason"]


def test_established_but_inapplicable_is_flagged_in_the_buffer(monkeypatch):
    """패널 성립이어도 오늘 조건 미충족이면 applies_today=False 로 남는다."""
    from edge_analysis.statics.paneltest import EdgeReport

    text, meta = _wire(monkeypatch, [
        EdgeReport("성립", 120, 0.001, 0.012, -0.003, 0.9,
                   cond_satisfied=False)])

    assert _clean(text)
    [rec] = meta["stat_tests"]
    assert rec["verdict"] == "성립" and rec["applies_today"] is False
    assert rec["reason"], "적용불가 사유가 비었다"


def test_rejected_proposals_become_ledger_rows(monkeypatch):
    """기각 제안은 REJECTED+사유로 원장 행이 된다 — 기각이 침묵되면 깨진다(ALPHA-881).

    이전에는 유효 튜플이 하나라도 있으면 rejected 목록이 통째로 사라졌다: "무엇이
    제안됐고 왜 죽었나"가 로그 보존 기간과 함께 증발한다."""
    from edge_analysis.statics.paneltest import EdgeReport

    why = "[1] 접지 밖 사건타입 날조: 'EVT_FAKE'"
    _text, meta = _wire(monkeypatch, [
        EdgeReport("성립", 120, 0.001, 0.012, -0.003, 0.9)], rejected=[why])

    trials = meta["hypothesis_trials"]
    rejected = [t for t in trials if t["stage"] == "REJECTED"]
    assert rejected == [{"stage": "REJECTED", "verdict": "REJECTED", "reason": why}]
    # 검정 행도 같은 원장에 있다 — 기각만 남고 검정이 사라지면 그것도 침묵이다.
    assert [t["stage"] for t in trials] == ["REJECTED", "TESTED"]


def test_tested_verdicts_are_stored_as_english_codes(monkeypatch):
    """원장 저장은 영문 코드 원칙 — 한글 판정이 그대로 새면 DB CHECK 가 거부한다.

    stage_results 버퍼(stat_tests)는 한글 원값을 유지한다 — 두 소비자의 계약이 다르다."""
    from edge_analysis.statics.paneltest import EdgeReport

    _text, meta = _wire(monkeypatch, [
        EdgeReport("성립", 120, 0.001, 0.012, -0.003, 0.9),
        EdgeReport("불성립", 120, 0.4, 0.01, -0.002, 0.5),
        EdgeReport("판정불가", 10, None, None, None, None, reason="표본부족"),
    ])

    tested = [t for t in meta["hypothesis_trials"] if t["stage"] == "TESTED"]
    assert [t["verdict"] for t in tested] == [
        "ESTABLISHED", "NOT_ESTABLISHED", "UNDECIDABLE"]
    assert all(t["verdict"] not in ("성립", "불성립", "판정불가") for t in tested)
    # 슬롯 원값(닫힌 어휘)이 행에 실린다 — 원장만 보고 튜플을 복원할 수 있어야 한다.
    assert tested[0]["trigger_slot"] == "점:CONTRACT.SIGNING"
    assert tested[0]["exposure"] == "거래량/수준" and tested[0]["layer"] == "고유"


def test_all_rejected_proposals_still_reach_the_ledger(monkeypatch):
    """전부 기각돼 검정이 0건이어도 기각 행은 남는다 — 빈손 런이 무기록 런으로
    위장되면 안 된다(Rule 12)."""
    _text, meta = _wire(monkeypatch, [], rejected=["[1] 채널 중복: Q수량"])

    assert [t["stage"] for t in meta["hypothesis_trials"]] == ["REJECTED"]
    assert meta["hypothesis_trials"][0]["reason"] == "[1] 채널 중복: Q수량"
    # stat_tests 버퍼의 '가설없음' 계약은 그대로다.
    [rec] = meta["stat_tests"]
    assert rec["verdict"] == "가설없음"


def test_propose_prompts_land_in_the_agent_trace(monkeypatch):
    """가설 제안 호출의 프롬프트·응답 원문이 trace 버퍼에 남는다(ALPHA-881).

    `TracingClient` 가 propose 의 ask 지점을 감싸므로, collect_trace 안에서 요청창
    갈래를 돌리면 llm.request/response 가 쌓여야 한다 — 이 사슬이 끊기면 가설이
    왜 그렇게 나왔는지 되짚을 원문이 없다."""
    import dataclasses

    from edge_analysis.adapters.llm import TracingClient
    from edge_analysis.observability import collect_trace
    from edge_analysis.statics import etfcell

    facts = dataclasses.replace(_facts(), event_ids=("e1",))
    monkeypatch.setattr("edge_analysis.statics.interval._etypes",
                        lambda lake, eids: ["CONTRACT.SIGNING"])
    monkeypatch.setattr("edge_analysis.statics.paneltest.series_z",
                        lambda lake, iid, day: {})

    class _Client:
        def complete_json(self, system, user):
            return {"hypotheses": []}   # 빈 제안 — 검정 없이 propose 만 돈다

    ask = TracingClient(_Client()).complete_json
    with collect_trace() as trace:
        etfcell._window_paneltest(object(), "iid", "2026-08-05", ask, facts)

    requests = [e for e in trace if e.get("event") == "llm.request"]
    assert requests, "propose 프롬프트가 trace 에 없다"
    assert "인과 가설 에이전트" in str(requests[0]["system"])
    assert any(e.get("event") == "llm.response" for e in trace)


def test_window_paneltest_wires_objectset_only_when_the_lake_can(monkeypatch):
    """Bound lakes receive structured ObjectSet tools; a stub gets no query tool."""
    import dataclasses
    from edge_analysis.statics import etfcell

    facts = dataclasses.replace(_facts(), event_ids=("e1",))
    monkeypatch.setattr("edge_analysis.statics.interval._etypes",
                        lambda lake, eids: ["CONTRACT.SIGNING"])
    monkeypatch.setattr("edge_analysis.statics.paneltest.series_z",
                        lambda lake, iid, day: {})
    seen: dict = {}
    monkeypatch.setattr("edge_analysis.statics.hypothesize.propose",
                        lambda ask, **kw: (seen.update(kw), ([], []))[1])

    lake = _object_lake()
    etfcell._window_paneltest(lake, "iid", "2026-08-05", lambda *_: {}, facts)
    assert seen["object_tools"] is not None
    assert seen["object_tools"]["specs"][0]["name"] == "objectset.create"
    assert "sql_tool" not in seen

    seen.clear()
    etfcell._window_paneltest(object(), "iid", "2026-08-05", lambda *_: {}, facts)
    assert seen["object_tools"] is None


def test_missing_layers_raise_instead_of_returning_prose():
    """**부재는 예외로 말한다.**

    산문으로 돌려주면 호출자가 정상 설명과 못 가르고 게시본 자리를 내준다 — 판정불가가
    발화의 게시본을 선점하면 나중의 제대로 된 설명이 DRAFT 로 밀린다(ALPHA-795).
    """
    import pytest

    from edge_analysis.config import PipelineError
    from edge_analysis.statics import etfcell

    class _NoLayers:
        exists: dict = {}

        def sql(self, q):
            return []

    with pytest.raises(PipelineError, match="층 분해 불가"):
        etfcell.run(_NoLayers(), "091160", "2026-08-06", lambda *_a, **_k: {},
                    roll=None)


# ── 제안 접지: 스레드 문맥 (ALPHA-885) ────────────────────────────────────
def _capture_propose(monkeypatch, facts, context=None):
    """propose 에 실리는 facts 문자열을 붙잡는다. context 는 thread_context 스텁."""
    from edge_analysis.statics import etfcell

    monkeypatch.setattr("edge_analysis.statics.interval._etypes",
                        lambda lake, eids: ["CONTRACT.SIGNING"] if eids else [])
    monkeypatch.setattr("edge_analysis.statics.paneltest.series_z",
                        lambda lake, iid, day: {"거래량": 3.0})
    if context is not None:
        monkeypatch.setattr("edge_analysis.statics.interval.thread_context",
                            lambda *a, **k: context)
    seen = {}

    def fake_propose(ask, **kw):
        seen.update(kw)
        return [], []

    monkeypatch.setattr("edge_analysis.statics.hypothesize.propose", fake_propose)
    lake = _object_lake()
    etfcell._window_paneltest(lake, "iid", "2026-08-05", lambda *_: {}, facts)
    return seen


def test_thread_context_reaches_the_proposal_prompt(monkeypatch):
    """창 안 사건이 있으면 제안 프롬프트에 스레드 문맥(제목·τ)이 실린다.

    안 실리면 제안이 타입 코드 목록만 보고 가설을 세운다 - 그게 ALPHA-885 가
    고치는 결함이므로, 문맥이 빠지면 이 테스트가 깨져야 한다(Rule 9).
    """
    import dataclasses

    block = ("[설명창 안] 08-05 10:31 CONTRACT.SIGNING — SK하이닉스 공급계약 해지\n"
             "  스레드(CONFIRMED·FOLLOW_UP_STAGE): 직전 07-30 09:00 루머 보도")
    facts = dataclasses.replace(_facts(), event_ids=("e1",))
    seen = _capture_propose(monkeypatch, facts, context=((block,), 1, 0))

    assert "[사건 문맥" in seen["facts"], "스레드 문맥 섹션이 프롬프트에 없다"
    assert "10:31" in seen["facts"] and "SK하이닉스 공급계약 해지" in seen["facts"]
    # 검정의 점 방아쇠 접지는 그대로 창 안 축이다 - 제안 문맥 확장이 검정 계약을
    # 흔들면 안 된다.
    assert seen["event_types"] == ["CONTRACT.SIGNING"]


def test_no_events_keeps_the_current_brief(monkeypatch):
    """사건 0·계열만 발화면 brief 는 현행 그대로다 - 스레드가 없는데 문맥 섹션을
    지어내면 안 된다."""
    import dataclasses

    facts = dataclasses.replace(_facts(), event_ids=())
    seen = _capture_propose(monkeypatch, facts, context=((), 0, 0))

    assert "[사건 문맥" not in seen["facts"]
    assert "창 안 사건 타입: 없음" in seen["facts"]
    assert "오늘 발화 계열족: ['거래량']" in seen["facts"]


def test_hypothesis_path_injects_objectset_tools_instead_of_the_sql_tool(monkeypatch):
    """The live P2 path must not hand executable query text to model output."""
    import dataclasses

    facts = dataclasses.replace(_facts(), event_ids=())
    seen = _capture_propose(monkeypatch, facts, context=((), 0, 0))

    assert "object_tools" in seen
    assert [spec["name"] for spec in seen["object_tools"]["specs"]][:2] == [
        "objectset.create", "objectset.filter"]
    assert "sql_tool" not in seen
    assert seen["object_tools"]["call"].__self__.as_of == "2026-08-05T13:20:59.999999"


def test_context_counts_and_failures_are_logged(monkeypatch):
    """제안 입력에 실린 사건 수·조회 실패 수가 log 이벤트로 남는다.

    방아쇠 판정이 침묵 폴백이라 실행마다 결과가 흔들려도 원인을 못 짚던 문제의
    관측 라인이다 - 이 이벤트가 사라지면 깨져야 한다.
    """
    import dataclasses

    from edge_analysis.observability import collect_trace

    facts = dataclasses.replace(_facts(), event_ids=("e1",))
    with collect_trace() as trace:
        _capture_propose(monkeypatch, facts, context=(("블록",), 3, 2))

    [ev] = [e for e in trace if e.get("event") == "hypothesis.context"]
    assert ev["events"] == 3 and ev["lookup_failures"] == 2
    assert ev["in_window"] == 1


def test_swallowed_trigger_exceptions_leave_a_log_line(monkeypatch):
    """_etypes·series_z 예외 삼킴에 로그 한 줄이 남는다 - 완전 침묵 폴백 금지."""
    import dataclasses

    from edge_analysis.observability import collect_trace
    from edge_analysis.statics import etfcell

    def boom(*a, **k):
        raise RuntimeError("RDB 부재")

    monkeypatch.setattr("edge_analysis.statics.interval._etypes", boom)
    monkeypatch.setattr("edge_analysis.statics.paneltest.series_z", boom)

    facts = dataclasses.replace(_facts(), event_ids=("e1",))
    with collect_trace() as trace:
        out = etfcell._window_paneltest(
            object(), "iid", "2026-08-05", lambda *_: {}, facts)

    assert out == ((), ()), "재료 없음 - LLM 미호출 계약이 흔들렸다"
    events = {e.get("event") for e in trace}
    assert "hypothesis.etypes_failed" in events
    assert "hypothesis.series_z_failed" in events
