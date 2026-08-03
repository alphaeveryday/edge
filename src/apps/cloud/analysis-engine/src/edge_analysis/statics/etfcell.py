"""ETF 셀 워크플로우 — 층 분해 → **라우팅** → 층에 맞는 인과 시행 → 산문.

## 왜 라우팅이 먼저인가

지금까지는 어느 층이 끌었는지 보지 않고 종목 가설부터 세웠다. 실측(042700 07-31):
하루의 77% 가 시장인데 9간선 전부 종목 가설이었고, 전부 죽었다. **틀린 질문을 잘
검정한 것**이다.

라우팅이 질문을 정한다:

    시장 지배    밤사이 해외 팩터 · 장중 시장 사건 · 국면으로 환원. 종목 가설 금지
    섹터 지배    그 업종 공통 처치. 종목 개별성은 조절자로만
    고유 지배    |기여| 상위 3종목에 개별 RCT 시행
    혼합         어느 층도 55% 미달 - 층별로 나눠 묻고 그 사실을 말한다
    괴리단독     바스켓 미이동 - ETF 수급/유동성. 구성종목으로 안 내려간다

## 설명할 수 없는 것은 접는다. 아무것도 설명 못 하는 건 안 된다

라우팅이 그 균형을 잡는다. 시장 지배면 종목 인과는 접지만 **시장 환원은 반드시
말한다**(밤사이 팩터 · 서킷브레이커 · 국면). 직관적으로 보이는 요인을 침묵하지
않는다.

사용:  python -m edge_analysis.statics.etfcell <etf_ticker> <YYYY-MM-DD>
"""
from __future__ import annotations

import sys

from .route import route_etf, say_route


def run(lake, etf: str, day: str) -> str:
    """ETF 하루 → 층 분해 → 라우팅 → 층별 시행 → 한 편의 설명."""
    from .layers import decompose
    from .premium import screen
    out: list[str] = []

    roll = decompose(lake, etf, day)
    if roll is None:
        return f"[ETF {etf} {day}] 층 분해 불가 — 구성종목 이력 또는 ETF 봉이 없다"
    # 괴리 판정은 `premium.screen` 이 셀 목록으로 준다 (NAV vs 가격). 그날 이 ETF 의
    # 셀이 없으면 판정 없이 진행한다 - 없는 판정을 만들지 않는다.
    pv = None
    try:
        pv = next((c.verdict for c in screen(lake, day, day)
                   if c.etf_id.startswith(etf) or etf in c.etf_id), None)
        if pv is None:
            out.append("[괴리] 이 ETF·이 날의 괴리 셀 없음 (NAV 커버리지 밖) — "
                       "바스켓/수급 분기 없이 층 라우팅만 한다")
    except Exception as e:                          # noqa: BLE001 - 부재는 사유와 함께
        out.append(f"[괴리] 판정 불가 — {type(e).__name__}: {str(e)[:70]}")

    out.append(f"[ETF] {etf} {roll.etf_name} · {day} · 하루 "
               f"{roll.total * 100:+.2f}%p (로그)")
    out.append(f"  귀속 커버리지 {roll.weight_covered:.0%} · 구성종목 {roll.n_names}"
               + (f" · 추적오차 {roll.rollup_gap * 100:+.2f}%p" if roll.rollup_gap else "")
               + (f" · 잔차 공통상관 ρ={roll.rho:+.3f}" if roll.rho is not None else "")
               + (f" · 동어반복 제외 {len(roll.twins)}" if roll.twins else ""))
    if roll.rho is not None and abs(roll.rho) > 0.20:
        out.append(f"  **고유 자격 없음** (ρ={roll.rho:+.3f}) — 이름 없는 공통요인이 "
                   "남아 있다. 아래 고유 몫은 상한으로 읽어라")

    r = route_etf(roll, pv)
    out.append(say_route(r))
    out.append("")
    out.extend(_workflow(lake, roll, r, day))
    return "\n".join(out)


def _workflow(lake, roll, r, day: str) -> list[str]:
    """라우팅별 인과 워크플로우. **다른 층의 질문은 세우지 않는다.**"""
    from .trial import reduce_market, run_trial, say, say_market
    out: list[str] = []

    if r.kind == "괴리단독":
        out.append("[워크플로우] ETF 고유 — 구성종목 인과를 세우지 않는다. 수급·유동성"
                   " 축만 유효하고, 그 축은 되돌림 후보다.")
        return out

    if r.kind in ("시장", "혼합"):
        out.append("[워크플로우] 시장 환원 — 종목 가설을 세우지 않는다")
        mr = reduce_market(lake, day, symbol=f"{roll.etf}.KS")
        out.append(say_market(mr))

    if r.kind in ("섹터", "혼합"):
        sec = next((x for x in roll.layers if x.kind == "섹터"), None)
        if sec is None:
            out.append("[워크플로우] 섹터 층이 분해에 없다 — 섹터 질문을 세울 근거가 없다")
        else:
            out.append(f"[워크플로우] 섹터 공통 처치 — {sec.name} "
                       f"(β {sec.beta:.2f} [{sec.lo:.2f}, {sec.hi:.2f}] · "
                       f"층 수익 {sec.ret * 100:+.2f}%p)")
            for et in ("POLICY.REGULATION.RULE_CHANGE",
                       "COMPANY.COMMERCIAL.PRICING_ACTION"):
                t = run_trial(lake, day, etype=et, layer="섹터")
                out.append(f"  {et.split('.')[-1]}: "
                           + say(t).splitlines()[0].replace("RCT 근사(매칭 위약): ", ""))

    if r.kind in ("고유", "혼합") and r.targets:
        out.append(f"[워크플로우] 종목 개별 시행 — 상위 {len(r.targets)}종목")
        for tk in r.targets:
            nm = next((n for n in roll.names if n.ticker == tk), None)
            head = (f"  {tk}" + (f" ({nm.label})" if nm and nm.label else "")
                    + (f" 기여 {nm.pct * 100:+.2f}%p · 비중 {nm.weight:.1%}" if nm else ""))
            out.append(head)
            t = run_trial(lake, day, etype="COMPANY.EARNINGS.RESULT_RELEASE",
                          layer="고유")
            out.append("    " + say(t).splitlines()[0].replace("RCT 근사(매칭 위약): ", ""))
    elif r.kind == "고유":
        out.append("[워크플로우] 고유가 지배하는데 |기여| 상위 종목이 임계 미달 — "
                   "귀속할 이름이 없다. 이것도 결과다 (분산된 고유)")
    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    from .duck import CausalLake
    print(run(CausalLake(), sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
