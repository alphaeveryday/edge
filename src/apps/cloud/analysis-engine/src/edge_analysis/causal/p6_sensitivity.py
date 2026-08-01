"""P6 민감도 — **식별이 안 될 때 주장의 강도를 재는 유일한 값싼 축이다.**

P4 가 `not_identified` 를 내면 그 간선은 죽은 것이 아니라 *조건부* 다. 그런데 지금까지의
산출물은 "확인됐다/못 했다" 두 값뿐이어서, 뒷문이 안 닫힌 간선과 아무 근거도 없는 간선이
같은 칸에 들어갔다. 둘은 전혀 다르다: 전자는 "이 연관을 지우려면 관측된 어떤 교란보다도
강한 미관측 교란이 필요하다"까지 말할 수 있고, 후자는 그것도 못 한다. 그 차이를 수치로
내는 것이 E-value 다 (VanderWeele-Ding 2017). 새 데이터도, 식별도 필요 없다 — 이미 나온
효과 추정 하나로 계산된다. 그래서 값싸다.

**근사의 한계 — 여기에 적힌 것 이상을 이 수치에 얹지 마라.**

하나. E-value 는 원래 이분 결과의 위험비(RR)에 대한 것이다. 우리 결과는 연속 초과수익이다.
그래서 표준화 평균차 `d = 효과 / 결과 표준편차` 를 `RR ≈ exp(0.91 * d)` 로 옮긴다
(VanderWeele 2017, "Sensitivity Analysis Without Assumptions" 후속의 연속 결과 근사).
이 변환은 결과를 이분화했을 때의 위험비를 흉내내는 것이므로, 나오는 E-value 의 **크기는
지시적**이다. "3.41 vs 3.28" 같은 비교를 하지 마라. "1 에 가까운가, 관측된 최강 교란보다
큰가"만 읽어라.

둘. 분모는 **결과 자체의 산포**여야 한다. `EdgeProof.null_sd` 는 순열 귀무의 산포이고,
설계·표본크기에 따라 결과 sd 와 자릿수가 다르다. 그것으로 나누면 d 가 부풀어 E-value 가
근거 없이 커진다. 그래서 결과 sd 는 `outcome_sd` 로 **바깥에서 받는다**. 없으면 E-value 를
내지 않는다 — 0 이나 임의값으로 채우는 것이 이 축에서 가장 위험한 실패 모드다.
`EdgeProof` 에 결과 sd 필드가 없어서 이렇게 했다(계약 변경 금지).

셋. Rosenbaum Γ 는 **짝지은** 관측연구의 순위검정용이다. 우리 설계는 짝짓기가 아니라
층화 순열이므로 Γ 를 계산하면 정의되지 않은 양에 숫자를 붙이는 것이다. `gamma=None` 으로
두고 `says` 에 왜 해당 없는지 적는다. 매칭 설계가 들어오면 그 자리다.

미산출을 표현하는 방법: `Sensitivity.e_value` 는 계약상 `float` 이라 None 을 못 넣는다.
그래서 하한 1.0(= "교란 없이도 설명된다" = 강도 주장 없음)을 놓고 `says` 에 무엇이 없어서
미산출인지 적는다. nan 은 P9 가 JSON 으로 직렬화하므로 쓰지 않는다. 1.0 은 소비자가 잘못
읽어도 **주장이 약해지는 방향**이라 안전한 실패값이다.
"""
from __future__ import annotations

import math
from typing import Any

try:
    from ..config import PipelineError
    from ..observability import log
    from .contracts import Sensitivity
except ImportError:                                   # `python p6_sensitivity.py` 자체 검사
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config import PipelineError                  # type: ignore[no-redef]
    from contracts import Sensitivity                 # type: ignore[no-redef]

    def log(event: str, **fields: object) -> None:    # type: ignore[misc]
        pass

# d -> RR 변환 계수. VanderWeele 2017 이 연속 결과에 제시한 근사의 상수다. 조정 금지 -
# 이 값을 만지면 문헌과 비교 불가능한 수가 나온다.
_D_TO_RR = 0.91
_Z95 = 1.96          # null_sd 를 SE 로 쓴 정규 근사. 아래 e_value_lower 주석 참조
_GAMMA_SAY = "Rosenbaum Γ 해당 없음 - 짝지은 설계가 아니라 층화 순열이다."


def e_value(risk_ratio: float) -> float:
    """위험비를 완전히 설명해 없애는 데 필요한 미관측 교란의 최소 연관 강도.

    E = RR + sqrt(RR * (RR - 1)), RR < 1 이면 1/RR 로 뒤집는다 (VanderWeele-Ding 2017).
    보호 효과와 위험 효과는 같은 크기의 교란을 요구하므로 뒤집는 것이 옳다.

    RR=1 에서 정확히 1.0 이 나온다: 연관이 없으면 교란도 필요 없다. 0 이나 음수는 위험비가
    아니므로 계산하지 않고 죽는다 - 여기서 조용히 1.0 을 돌려주면 "설명 불가능한 강한
    연관" 과 "입력이 망가짐" 이 같은 값으로 붙는다.
    """
    if not math.isfinite(risk_ratio) or risk_ratio <= 0.0:
        raise PipelineError(f"위험비가 양의 유한수가 아니다: {risk_ratio!r}")
    rr = risk_ratio if risk_ratio >= 1.0 else 1.0 / risk_ratio
    return rr + math.sqrt(rr * (rr - 1.0))


def _rr_from_d(d: float) -> float:
    """표준화 평균차 -> 근사 위험비. 부호는 무관하다(E-value 가 역수에 대칭)."""
    return math.exp(_D_TO_RR * abs(d))


def _pos(x: Any) -> float | None:
    """양의 유한수만 통과. 0·nan·음수는 산포가 아니다."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0.0 else None


def _lookup_sd(outcome_sd: dict[str, float] | None, edge: str, dst: str) -> float | None:
    """결과 sd 조회. 간선 키가 먼저, 없으면 결과 노드 키.

    sd 는 결과 노드의 성질이므로 노드 키가 자연스럽지만, 같은 노드를 다른 표본으로 잰
    간선이 둘 이상일 수 있어 간선 키가 우선한다.
    """
    if not outcome_sd:
        return None
    return _pos(outcome_sd.get(edge)) or _pos(outcome_sd.get(dst))


def evaluate(proofs: list, *, observed_strongest: float | None = None,
             outcome_sd: dict[str, float] | None = None) -> list[Sensitivity]:
    """간선마다 민감도 하나. **검토한 간선에 침묵이 없다** - 미산출도 한 줄로 남는다.

    `proofs` 는 `verify.EdgeProof` 리스트다. 효과 추정이 없는 간선(게이트 실패·불가)은
    E-value 가 정의되지 않지만 그래도 항목을 낸다 - P8 이 "민감도 항목 없음" 과 "민감도
    미산출" 을 구별해야 하고, 전자는 P6 를 안 돌린 것이지 산출이 아니다.

    `observed_strongest` 는 관측된 최강 교란의 연관 강도(위험비 척도, 있으면). 실제로
    읽히는 산출은 E-value 숫자가 아니라 이것과의 비교 문장이다: 필요한 강도가 이미 재본
    가장 강한 교란보다 작으면, 그 간선은 알려진 교란으로도 설명될 수 있다.
    """
    out: list[Sensitivity] = []
    strongest = _pos(observed_strongest)
    for pf in proofs:
        d = getattr(pf, "design", None)
        src = getattr(d, "src", "?")
        dst = getattr(d, "dst", "?")
        edge = f"{src}->{dst}"
        eff = getattr(pf, "effect", None)
        eff = float(eff) if isinstance(eff, (int, float)) and math.isfinite(eff) else None

        if eff is None:
            out.append(Sensitivity(
                edge=edge, effect=0.0, e_value=1.0, observed_strongest=strongest,
                says=(f"E-value 미산출: 효과 추정이 없다(status={getattr(pf, 'status', '?')}"
                      f", n={getattr(pf, 'n', 0)}). 효과가 없으면 지울 연관도 없다. "
                      f"{_GAMMA_SAY}")))
            continue

        sd = _lookup_sd(outcome_sd, edge, dst)
        if sd is None:
            out.append(Sensitivity(
                edge=edge, effect=eff, e_value=1.0, observed_strongest=strongest,
                says=(f"E-value 미산출: 결과 표준편차가 없다(outcome_sd['{edge}'] 또는 "
                      f"['{dst}'] 필요). 표준화 없이 초과수익 {eff:+.4f} 를 위험비로 옮길 수 "
                      "없다. 귀무 산포(null_sd)는 결과 산포가 아니므로 대신 쓰지 않았다. "
                      f"e_value 는 하한 1.0 = 강도 주장 없음. {_GAMMA_SAY}")))
            continue

        dd = eff / sd
        rr = _rr_from_d(dd)
        ev = e_value(rr)

        # 신뢰한계 E-value: 점추정만 보면 정밀도가 무시된다(E-value 에 대한 표준 비판).
        # SE 는 순열 귀무의 산포로 근사한다 - 귀무 하에서는 옳고, 효과가 크면 효과 이질성만큼
        # 어긋난다. 구간이 귀무를 포함하면 관례대로 1.0 이다.
        ev_lo: float | None = None
        se = _pos(getattr(pf, "null_sd", None))
        if se is not None:
            half = _Z95 * se
            ev_lo = 1.0 if abs(eff) <= half else e_value(_rr_from_d((abs(eff) - half) / sd))

        L = [f"E={ev:.2f} — 이 연관을 지우려면 미관측 교란이 처치·결과 양쪽과 위험비 "
             f"{ev:.2f} 이상으로 연관돼야 한다."]
        if ev_lo is not None:
            L.append(f"신뢰한계 기준 E={ev_lo:.2f}"
                     + ("(구간이 귀무를 포함 - 정밀도만으로는 아무 교란도 필요 없다)."
                        if ev_lo <= 1.0 else "(null_sd 를 SE 로 쓴 정규 근사)."))
        L.append(f"위험비 환산 RR={rr:.2f} 은 표준화 평균차 d={dd:+.2f}"
                 f"(효과 {eff:+.4f} / 결과 sd {sd:.4f})의 **근사**다"
                 "(VanderWeele 2017, RR≈exp(0.91·d)) - 이분 결과의 실제 위험비가 아니므로"
                 " 크기는 지시적이다.")
        if strongest is not None:
            L.append(f"관측된 최강 교란 {strongest:.2f} "
                     + ("보다 크다: 이미 재본 교란으로는 설명되지 않는다."
                        if ev > strongest else
                        "보다 작다: 알려진 교란 수준의 미관측 교란만으로도 설명될 수 있다."))
        else:
            L.append("비교 기준 없음: 관측된 최강 교란의 연관 강도가 안 넘어왔다 - "
                     "E-value 만으로는 크다/작다를 말할 수 없다.")
        L.append(_GAMMA_SAY)

        out.append(Sensitivity(edge=edge, effect=eff, e_value=ev, e_value_lower=ev_lo,
                               observed_strongest=strongest, says=" ".join(L)))

    log("causal.p6.done", n=len(out),
        computed=sum(1 for s in out if s.e_value > 1.0))
    return out


if __name__ == "__main__":
    from types import SimpleNamespace as NS

    # 문헌 값. RR=3.9 -> 7.26 은 VanderWeele-Ding 2017 의 예시(흡연-폐암)다.
    assert e_value(1.0) == 1.0
    assert abs(e_value(2.0) - 3.4142) < 1e-3
    assert abs(e_value(3.9) - 7.26) < 1e-2
    assert abs(e_value(0.5) - e_value(2.0)) < 1e-12, "역수는 같은 강도를 요구한다"
    for bad in (0.0, -1.0, float("nan")):
        try:
            e_value(bad)
        except PipelineError:
            pass
        else:
            raise AssertionError(f"위험비가 아닌 입력을 통과시켰다: {bad}")

    pf_ok = NS(design=NS(src="EVENT", dst="AR"), status="통과", n=120,
               effect=0.012, p=0.01, null_sd=0.004)
    pf_bad = NS(design=NS(src="FLOW", dst="AR"), status="게이트실패", n=0,
                effect=None, p=None, null_sd=None)

    # 결과 sd 가 없으면 E-value 를 내지 않는다 - 조용히 0 으로 나누지도, 채우지도 않는다.
    s = evaluate([pf_ok])[0]
    assert s.e_value == 1.0 and "미산출" in s.says and s.e_value_lower is None
    assert "null_sd" in s.says, "무엇을 대신 쓰지 않았는지 말해야 한다"

    # d = 0.012/0.03 = 0.4 -> RR=exp(0.364)=1.439 -> E=2.23
    s = evaluate([pf_ok], outcome_sd={"AR": 0.03}, observed_strongest=1.5)[0]
    assert 2.2 < s.e_value < 2.3, s.e_value
    assert s.e_value_lower is not None and 1.0 < s.e_value_lower < s.e_value
    assert s.gamma is None and "짝지은 설계가 아니" in s.says
    assert "보다 크다" in s.says and "근사" in s.says

    # 간선 키가 노드 키를 이긴다
    s2 = evaluate([pf_ok], outcome_sd={"AR": 0.03, "EVENT->AR": 0.006})[0]
    assert s2.e_value > s.e_value, "sd 가 작으면 d 가 커지고 E 도 커진다"

    # 효과가 없는 간선도 항목을 낸다 (침묵 금지)
    out = evaluate([pf_ok, pf_bad], outcome_sd={"AR": 0.03})
    assert len(out) == 2 and out[1].e_value == 1.0 and "효과 추정이 없다" in out[1].says

    # 구간이 귀무를 포함하면 신뢰한계 E-value 는 1.0
    pf_wide = NS(design=NS(src="EVENT", dst="AR"), status="통과", n=20,
                 effect=0.012, p=0.4, null_sd=0.02)
    assert evaluate([pf_wide], outcome_sd={"AR": 0.03})[0].e_value_lower == 1.0

    print("p6_sensitivity 자체 검사 통과")
