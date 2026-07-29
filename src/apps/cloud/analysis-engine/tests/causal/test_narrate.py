"""고객 문장 테스트 — **문장에 없는 수치가 들어가지 않는다.**

실험판에서 모델이 보고한 수치는 날조였다. 서술을 LLM 에게 맡기면 그 자리가 다시
열리므로, 여기서는 계산된 값만 포맷팅한다. 그 성질을 테스트가 고정한다.
그리고 어휘는 `domain.models._VERDICT_TO_TYPE` 키와 정확히 맞아야 한다 - 다른
문자열이면 조용히 UNCERTAIN 으로 떨어진다.
"""

import re

from edge_analysis.causal.narrate import CausalReport, EdgeFinding, narrate
from edge_analysis.domain.models import Explanation


def _report(**kw) -> CausalReport:
    base = dict(etf_name="KODEX 바이오", trade_date="2026-07-29",
                observed=0.0421, residual=0.0389, route_code="THEMA")
    base.update(kw)
    return CausalReport(**base)


def test_survived_finding_produces_event_verdict_that_maps_to_enum():
    r = _report(findings=[EdgeFinding(
        cause="삼성바이오로직스 수주 공시", because="대형 위탁생산 계약이 매출 전망을 올린다",
        effect=0.061, p=0.004, n=180, share=0.22, contribution=0.0134, survived=True)])

    raw = narrate(r)

    assert Explanation(raw).explanation_type == "EVENT_SUPPORTED"
    assert Explanation(raw).confidence_level == "HIGH"
    assert Explanation(raw).is_valid


def test_no_surviving_cause_says_so_and_never_invents_confidence():
    r = _report(findings=[EdgeFinding(
        cause="애널리스트 등급 변경", because="",
        effect=-0.002, p=0.79, n=532, share=0.052,
        killed_by="그 종목들의 비중으로는 이만한 움직임을 만들 수 없었습니다.")],
        residual=0.0421)

    raw = narrate(r)
    exp = Explanation(raw)

    assert exp.explanation_type == "UNCERTAIN"
    assert exp.confidence_level == "LOW"
    assert "확인되지 않았습니다" in exp.summary
    assert "비중으로는" in exp.summary


def test_market_driven_move_is_mixed_not_uncertain():
    """잔차가 관측의 절반도 안 되면 움직임의 대부분이 시장·업종에서 왔다."""
    raw = narrate(_report(observed=0.0500, residual=0.0100))

    assert Explanation(raw).explanation_type == "MIXED"


def test_every_number_in_the_body_comes_from_the_report():
    """본문의 모든 퍼센트가 리포트가 준 값에서 나와야 한다 - 새 수치 생성 금지."""
    r = _report(observed=0.0421, residual=0.0389,
                top_contributors=[("셀트리온", 0.0181)],
                findings=[EdgeFinding(cause="공시", because="", effect=0.061,
                                      p=0.01, n=100, share=0.22,
                                      contribution=0.0134, survived=True)])

    body = narrate(r)["explain"]
    allowed = {"+4.21%", "+3.89%", "+1.81%", "+1.34%", "22.00%", "+6.10%"}

    assert set(re.findall(r"[+-]?\d+\.\d\d%", body)) <= allowed


def test_missing_inputs_are_reported_rather_than_papered_over():
    raw = narrate(_report(missing=["투자자별 순매수 일별", "지수 편입 예정일"]))

    assert "확보하지 못한 자료" in raw["explain"]


def test_audit_block_is_preserved_for_the_archive():
    """DB 매핑이 버리는 것을 raw 가 남긴다 - 감사 경로가 끊기면 재현이 안 된다."""
    r = _report(global_fit={"C": 12.3, "df": 10, "p": 0.26},
                local_violations=["LOGCAP ⊥ RET | ..."],
                findings=[EdgeFinding(cause="공시", because="", effect=0.06,
                                      p=0.01, n=100, survived=True)])

    audit = narrate(r)["causal"]

    assert audit["global_fit"]["p"] == 0.26
    assert audit["local_violations"]
    assert audit["survived"][0]["n"] == 100
    assert audit["residual"] == r.residual
