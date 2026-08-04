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

import os
import sys

from .route import route_etf, say_route


def run(lake, etf: str, day: str, ask=None) -> str:
    """ETF 하루 → 층 분해 → 라우팅 → 층별 시행 → 한 편의 설명.

    `ask` 를 주면 **쉬운 설명(토스식)** 을 덧붙인다 - 정직한 설명이 먼저다.
    """
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
    honest = "\n".join(out)
    if ask is None:
        return honest
    return _dual(lake, roll, r, day, honest, ask)


def _dual(lake, roll, r, day: str, honest: str, ask) -> str:
    """정직한 설명 + 토스식. ETF 는 5분봉이 없어 창이 없다 - **그 사실을 정직하게**
    다루고, '최근 시점' 대신 시장 사건의 시각(있으면)이나 밤사이를 쓴다."""
    from .mkttrial import event_days
    from .evidence import save
    from .plain import PlainError, context, dual, narrate_plain
    from .trial import reduce_market
    # 이름만 넘기면 모델이 방향을 지어낸다 - 실측에서 VIX 가 -17% 인데 'VIX 강세'
    # 라고 썼다. **부호를 지우면 서사가 거짓이 된다.** 수치는 못 주므로 방향을 말로
    # 준다. VIX 는 오르면 불안이라 강세/약세로 부르면 뜻이 뒤집힌다 - 따로 옮긴다.
    over: list[str] = []
    mr: dict = {}
    try:
        mr = reduce_market(lake, day, symbol=f"{roll.etf}.KS")
        for nm, v in (mr.get("overnight") or ()):
            f = float(v)
            if abs(f) <= 0.01:
                continue
            name = str(nm)
            if "VIX" in name.upper() or "변동성" in name:
                over.append("미국 시장의 불안이 " + ("커졌어요" if f > 0 else "줄었어요"))
            else:
                over.append(f"{name}가 " + ("올랐어요" if f > 0 else "내렸어요"))
            if len(over) >= 3:
                break
    except Exception:                       # noqa: BLE001 - 부재는 빈 목록
        over = []
    # 시장 사건 검정의 **무유의도 사실이다**: '특별한 뉴스 때문이 아니다' 는
    # 사람이 가장 궁금해하는 답 중 하나다(급변인데 뉴스가 없을 때).
    from .mkttrial import say_screen, screen_market
    facts: list[str] = []
    scr: list = []
    try:
        scr = screen_market(lake, day)
        if scr and "유의한 시장 사건 없음" in say_screen(scr):
            facts.append("특별한 국내 소식 때문은 아님")
        elif any(d == day for d in event_days(lake, day)):
            facts.append("시장 전체에 영향을 준 소식")
    except Exception:                       # noqa: BLE001 - 부재는 빈 목록
        pass
    # **밤사이 환원이 곧 이유다.** 이것을 `established` 에 안 넣으면 산문이
    # '밤사이 올랐어요' 라 말한 뒤 '이유는 안 보여요' 라고 자기모순을 낸다(실측).
    if over:
        facts.insert(0, "밤사이 해외에서 " + over[0])
    ctx = context(
        ticker_name=roll.etf_name, day_log=roll.total,
        idio_log=roll.idio, route_kind=r.kind,
        market_name=r.layer_name or "코스피 대형주",
        # ETF 는 창이 없다(5분봉 부재) - 밤사이가 하루의 시작이므로 그것을 시점으로.
        recent={"when": "밤사이", "events": facts[:1]},
        established=facts, overnight=over,
        unexplained_top=False, idio_qualified=roll.rho is None or abs(roll.rho) < 0.20)
    # 통계 재료. **수치는 재료에만** 있고 산문에는 못 들어간다(코드가 막는다).
    # 밤사이 환원도 통계다(β 구간 × 팩터 수익) - 이것을 참조 가능한 근거로 안 주면
    # '밤사이 미국 반도체가 올라서' 라는 주장이 접지 실패로 즉사한다(실측 2회).
    stats: list[dict] = []
    if mr and mr.get("factor_name"):
        stats.append({"ref": "s1", "kind": "밤사이 환원", "factor": mr["factor_name"],
                      "factor_ret": mr.get("gap_factor_ret"),
                      "beta_ci": list(mr.get("gap_beta") or ()),
                      "explained": mr.get("mkt_explained"),
                      "note": mr.get("gap_reason", "")[:60]})
    stats += [{"ref": f"s{len(stats) + i}", "kind": "시장사건 시행", "etype": x["etype"],
               "att": round(x["att"], 5), "p": round(x["p"], 4),
               "n_days": x["n_days"], "unit": "거래일"}
              for i, x in enumerate(
                  (z for z in (scr or []) if z.get("verdict") == "계산됨"), 1)]
    try:
        plain, bundles = narrate_plain(ask, ctx, stats=stats, cell=roll.etf,
                                      day=day, layer=r.kind)
        n, why = save(bundles)
        if why:
            plain += f"\n(근거 묶음 저장 실패: {why})"
        return dual(honest, plain, bundles)
    except PlainError as e:
        return dual(honest, f"(쉬운 설명 생성 실패 - 계약 위반: {e})")


def _workflow(lake, roll, r, day: str) -> list[str]:
    """라우팅별 인과 워크플로우. **다른 층의 질문은 세우지 않는다.**"""
    from .trial import reduce_market, say_market
    out: list[str] = []

    if r.kind == "괴리단독":
        out.append("[워크플로우] ETF 고유 — 구성종목 인과를 세우지 않는다. 수급·유동성"
                   " 축만 유효하고, 그 축은 되돌림 후보다.")
        return out

    if r.kind in ("시장", "혼합"):
        out.append("[워크플로우] 시장 환원 — 종목 가설을 세우지 않는다")
        mr = reduce_market(lake, day, symbol=f"{roll.etf}.KS")
        out.append(say_market(mr))
        # ② 는 선언만 되고 배선이 없었다: `say_market` 의 시장 사건 줄은 `iid` 가
        # 있을 때만 나오고 ETF 경로는 그것을 안 넘긴다(ETF 에 instrument_id 가 없다).
        # 그래서 라우팅은 '밤사이 해외 · **장중 시장 사건** · 국면' 셋을 약속하는데
        # 산문에는 ①③ 만 있었다. 그리고 τ 마크는 **표시**일 뿐 검정이 아니다.
        # 시장 사건은 그 날 전 종목이 처치라 횡단면 대조군이 없다(SUTVA) - 단위를
        # 거래일로 바꿔야 검정된다. `mkttrial` 이 그것이다.
        from .mkttrial import say_screen, screen_market
        out.append(say_screen(screen_market(lake, day)))

    if r.kind in ("섹터", "혼합"):
        sec = next((x for x in roll.layers if x.kind == "섹터"), None)
        if sec is None:
            out.append("[워크플로우] 섹터 층이 분해에 없다 — 섹터 질문을 세울 근거가 없다")
        else:
            out.append(f"[워크플로우] 섹터 공통 처치 — {sec.name} "
                       f"(β {sec.beta:.2f} [{sec.lo:.2f}, {sec.hi:.2f}] · "
                       f"층 수익 {sec.ret * 100:+.2f}%p)")
            # 검정 층에 넘긴다 - 위약 먼저, 구체화, 유의한 것에만 CATE.
            from .verifier import say_implications, verify
            for et in ("POLICY.REGULATION.RULE_CHANGE",
                       "COMPANY.COMMERCIAL.PRICING_ACTION"):
                imps, lg = verify(lake, day, etype=et, layer="섹터")
                out.append("  " + lg.replace("\n", "\n  "))
                out.append("  " + say_implications(imps).replace("\n", "\n  "))

    if r.kind in ("고유", "혼합") and r.targets:
        out.append(f"[워크플로우] 종목 개별 시행 — 상위 {len(r.targets)}종목")
        for tk in r.targets:
            nm = next((n for n in roll.names if n.ticker == tk), None)
            head = (f"  {tk}" + (f" ({nm.label})" if nm and nm.label else "")
                    + (f" 기여 {nm.pct * 100:+.2f}%p · 비중 {nm.weight:.1%}" if nm else ""))
            out.append(head)
            from .verifier import say_implications, verify
            imps, lg = verify(lake, day, etype="COMPANY.EARNINGS.RESULT_RELEASE",
                              layer="고유", max_probes=4)
            out.append("    " + lg.replace("\n", "\n    "))
            out.append("    " + say_implications(imps).replace("\n", "\n    "))
    elif r.kind == "고유":
        out.append("[워크플로우] 고유가 지배하는데 |기여| 상위 종목이 임계 미달 — "
                   "귀속할 이름이 없다. 이것도 결과다 (분산된 고유)")
    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    from .duck import CausalLake
    # 키가 있으면 쉬운 설명까지, 없으면 정직한 설명만 - **조용히 빠지지 않는다**.
    ask = None
    if key := os.environ.get("DEEPSEEK_API_KEY"):
        from ..adapters.llm import DeepSeekClient, TracingClient
        ask = TracingClient(DeepSeekClient(
            key, os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))).complete_json
    else:
        print("[알림] DEEPSEEK_API_KEY 없음 - 쉬운 설명(토스식)은 생략한다")
    print(run(CausalLake(), sys.argv[1], sys.argv[2], ask))


if __name__ == "__main__":
    main()
