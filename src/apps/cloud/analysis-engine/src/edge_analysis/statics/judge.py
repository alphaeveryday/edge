"""검정 에이전트 - 간선 하나(인과 주장 하나)를 맡아 도구로 검증하고 결론을 낸다.

가설 에이전트와 **같은 도구 상태기계**를 쓴다(fsm.Machine). 다른 것은 둘뿐:
  · SCREEN 메뉴에 `panel` 이 추가된다 - 맡은 튜플의 타입 수준 패널 수치.
    인자가 없다 - 검정자는 표본을 고르지 못한다(§17). 특징 선택(어떤 노출·취약성·
    교란 뷰를 볼지)은 튜플과 DAG 가 주고, 어떤 도구를 볼지는 검정자의 권한이다.
  · EMIT 에서 튜플이 아니라 **판정**을 낸다.

게이트는 결론을 내리지 않는다: 패널 수치(n·분리·p·오늘 노출)는 참고 재료로만
제시되고 결론은 검정자가 진다. 판정불가는 **희소해야 한다** - 대부분의 셀에는
명료한 재료(층·수급·미국장·패널·창)가 있다. 사람도 아리까리한 것만 판정불가로,
사유와 함께.

성립이면 구조방정식 재료를 반드시 남긴다: 변수의 형(0/1·시계열·수준)과 이름,
오늘 값, 그리고 **그 데이터의 의미**(방정식에서 이 항이 뜻하는 것).
기각이면 간선을 끊고 사유를 가설 에이전트에게 보고한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .dag import CEdge, Finding
from .fsm import EMIT, GROUND, MENUS, SCREEN, Machine
from .hypothesize import explore
from .tools import Catalog

JUDGE_MENUS = {
    GROUND: MENUS[GROUND],
    SCREEN: MENUS[SCREEN] + (
        ("panel", "panel()  맡은 튜플의 타입 수준 패널 수치 - 판정은 네가 한다"),),
    EMIT: (),
}

_SYSTEM = """너는 검정 에이전트다. **인과 주장 하나**를 맡는다 - 아래 [맡은 간선]의 튜플과 의도.

규율:
- 표본을 고르지 마라. 셀·패널은 고정이고 도구에 셀 인자가 없다.
- 특징 선택은 네 권한이다: 어떤 도구로 어떤 축(수급·계열·패널·피어·창)을 볼지 골라라.
- DAG 의 공통요인·경쟁가설을 교란으로 고려하라 - 네 간선이 성립해도 경쟁 간선이
  같은 움직임을 더 잘 설명하면 그 사실을 결론에 적어라.
- 수치는 도구가 준 것만 인용하라. 지어내지 마라.

판정 기준 (게이트가 아니라 네 판단):
- 성립: 오늘의 사실(창·발화·수급)과 역사(패널 방향)가 주장을 지지한다.
- 기각: 역사가 방향을 지지하지 않거나, 오늘의 사실이 주장과 어긋난다(시간 알리바이 포함).
- 판정불가: **희소하게**. 재료가 정말로 상충하거나 없을 때만 - 사유 필수.
  n 이 작다는 것만으로 판정불가로 도망가지 마라. 오늘의 창·수급·계열 사실로 기울여라.

마지막 턴에 JSON 하나만:
{"causal": true|false|null,
 "confidence": "높음|중간|낮음",
 "conclusion": "결론 한 단락 - 근거 수치를 인용",
 "se": {"kind": "0/1|시계열|수준", "name": "변수명", "value": "오늘 값",
        "meaning": "구조방정식에서 이 항이 뜻하는 것"},
 "cut_reason": "기각일 때 - 가설 에이전트에게 보고할 사유"}"""

_VERDICT_ASK = """[맡은 간선]
{head}
의도: {intent}

[DAG 맥락 - 경쟁가설과 공통요인]
{dag}

[셀의 사실 - 결정론 산출]
{facts}

[도구 관측 기록]
{seen}

이제 판정 JSON 을 내라. 판정불가는 희소해야 한다 - 재료가 이만큼 있다."""


@dataclass
class JudgeCatalog(Catalog):
    """가설 카탈로그 + panel(맡은 튜플 고정). 도구 인자로 튜플을 못 바꾼다."""

    tup: object = None

    def panel(self) -> str:
        """타입 수준 패널의 수치. 판정 문자열은 '코드 참고'로만 - 결론은 검정자가."""
        from .paneltest import edge_test
        r = edge_test(self.lake, self.tup, self.day,
                      cell_instrument_id=self.instrument_id)
        rows = [f"패널 수치 (판정은 네가 한다 - 아래 verdict 는 코드 참고일 뿐):",
                f"  n={r.n} · p={r.p if r.p is not None else '미계산'} · "
                f"상위 {r.effect_high * 100:+.2f}% vs 하위 {r.effect_low * 100:+.2f}%"
                if r.effect_high is not None else f"  n={r.n} · 효과 미계산",
                f"  오늘 노출 백분위 {r.today_exposure_pct * 100:.0f}%"
                if r.today_exposure_pct is not None else "  오늘 노출 미계산",
                f"  취약성 오늘: {r.vuln_today or '-'} (충족 {r.vuln_satisfied})",
                f"  환원 검사: {r.reduction or '-'}",
                f"  반사실: {r.counterfactual or '-'}",
                f"  코드 참고 의견: {r.verdict}" + (f" - {r.reason}" if getattr(r, 'reason', '') else "")]
        if getattr(r, "trigger_fired", None) is not None:
            rows.append(f"  오늘 방아쇠 발화: {r.trigger_fired}")
        return "\n".join(rows)


def judge_edge(lake, ask, *, ticker: str, instrument_id: str, day: str,
               edge: CEdge, dag_txt: str, facts: str,
               types: tuple[str, ...] = ()) -> Finding:
    """간선 하나를 검정한다. 같은 FSM 으로 관측 → 판정 JSON → Finding."""
    cat = JudgeCatalog(lake=lake, ticker=ticker, instrument_id=instrument_id,
                       day=day, types=types, tup=edge.tup)
    m = Machine(catalog=cat, menus=JUDGE_MENUS,
                screen_tools=("screen", "series", "panel"))
    brief = f"{edge.head()}\n의도: {edge.intent}\n\n{facts}"
    seen = explore(ask, m, facts=brief)          # explore 는 자체 관측자 프롬프트를 쓴다
    out = ask(_SYSTEM, _VERDICT_ASK.format(
        head=edge.head(), intent=edge.intent, dag=dag_txt, facts=facts,
        seen=seen or "(관측 없음)"))
    return to_finding(out)


def to_finding(out: dict) -> Finding:
    """판정 JSON → Finding. 파싱은 순수 함수 - 테스트가 여기를 잡는다."""
    se = out.get("se") or {}
    causal = out.get("causal", None)
    return Finding(
        causal=bool(causal) if causal is not None else None,
        conclusion=str(out.get("conclusion", ""))[:600],
        confidence=str(out.get("confidence", ""))[:8],
        se_kind=str(se.get("kind", ""))[:12], se_name=str(se.get("name", ""))[:60],
        se_value=str(se.get("value", ""))[:60],
        se_meaning=str(se.get("meaning", ""))[:240],
        cut_reason=str(out.get("cut_reason", ""))[:300])


__all__ = ["JUDGE_MENUS", "JudgeCatalog", "judge_edge", "to_finding"]
