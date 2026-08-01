"""P7 술어 게이트 테스트.

`exposure` 는 SQL 의 WHERE 로 그대로 들어간다. 모델이 그 자리에 산문을 쓰면 실행이 죽고
(2026-07-30 실측 `SyntaxError: syntax error at or near "실적"`), 문법이 우연히 맞으면 더
나쁘다 - 엉뚱한 코호트가 조용히 잡히고 "검사했다"로 기록된다. 그래서 산문은 술어로
승격되지 않고 접지 사건 폴백으로 내려가야 한다.
"""

from edge_analysis.causal.contracts import Hypothesis, WorldGraph
from edge_analysis.causal.p7_negative import _predicates, _sql_predicate


def _graph(exposure: str, *, events: tuple[str, ...] = ()) -> WorldGraph:
    h = Hypothesis(hid="h1", says="s", treatment="T@t0", outcome="Y@t0",
                   assignment="natural", nodes={"T@t0": {}, "Y@t0": {}},
                   edges=[{"from": "T@t0", "to": "Y@t0"}], events=list(events))
    return WorldGraph(nodes={"T@t0": {}, "Y@t0": {}},
                      edges=[{"from": "T@t0", "to": "Y@t0", "exposure": exposure}],
                      hypotheses=[h])


def test_a_column_comparison_is_accepted_as_a_predicate():
    assert _sql_predicate("event_type_code = 'COMPANY.EARNINGS.RELEASE'")
    assert _sql_predicate("industry_name IN ('반도체')")


def test_prose_is_not_a_predicate():
    # 실측된 폭발 문자열. 통과시키면 음성대조 전량이 죽는다.
    assert _sql_predicate("삼성전자 실적 발표를 접한 투자자") == ""
    assert _sql_predicate("") == ""


def test_prose_exposure_falls_back_to_the_grounded_events():
    # 산문을 버리고 끝내면 처치가 사라진다 - 접지된 사건이 있으면 그것으로 정의한다.
    exposure, _ = _predicates(_graph("삼성전자 실적 발표를 접한 투자자", events=("evt_1",)))

    assert "source_event_id" in exposure
    assert "evt_1" in exposure


def test_a_real_predicate_survives_the_gate():
    exposure, _ = _predicates(_graph("event_type_code = 'DIV'", events=("evt_1",)))

    assert exposure == "event_type_code = 'DIV'"


def test_a_grounded_treatment_widens_the_exposure_to_the_whole_type():
    """`source_event_id IN (...)` 은 이 셀의 사건 하나라 **n=1 로 게이트에서 죽는다.**

    실측(2026-08-01 tools-20260801-01): 검정 4/4 가 "이벤트 코드를 알 수 없어 코호트가
    비었다"로 불가였고, 게이트는 `G2 n=1 < 30 (scope=type)` 을 남겼다. 같은 타입 전체가
    처치군이어야 표본이 쌓인다.
    """
    h = Hypothesis(hid="h1", says="s", treatment="T@t0", outcome="Y@t0",
                   assignment="natural", events=["evt_1"],
                   nodes={"T@t0": {"event_type_code": "COMPANY.EARNINGS.RELEASE"}})
    g = WorldGraph(nodes={"T@t0": {"event_type_code": "COMPANY.EARNINGS.RELEASE"},
                          "Y@t0": {}},
                   edges=[{"from": "T@t0", "to": "Y@t0"}], hypotheses=[h])

    exposure, _ = _predicates(g)

    assert "event_type_code" in exposure and "COMPANY.EARNINGS.RELEASE" in exposure
    assert "source_event_id" not in exposure, "타입이 있는데 사건 하나로 좁혔다"


def test_without_a_type_the_exposure_still_falls_back_to_the_event_id():
    # 좁은 정의라도 있는 것이 아무것도 없는 것보다 낫다 - 그 좁음은 G2 가 사유로 남긴다.
    h = Hypothesis(hid="h1", says="s", treatment="T@t0", outcome="Y@t0",
                   assignment="natural", events=["evt_1"], nodes={"T@t0": {}})
    g = WorldGraph(nodes={"T@t0": {}, "Y@t0": {}},
                   edges=[{"from": "T@t0", "to": "Y@t0"}], hypotheses=[h])

    exposure, _ = _predicates(g)

    assert "source_event_id" in exposure and "evt_1" in exposure
