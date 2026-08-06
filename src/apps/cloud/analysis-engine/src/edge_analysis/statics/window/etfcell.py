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

사용:  python -m edge_analysis.statics.window.etfcell <etf_ticker> <YYYY-MM-DD>
"""
from __future__ import annotations

import os
import sys

from .route import route_etf, say_route
from .interval import (
    build_block_plan,
    final_explanation_payload,
    window_facts,
)


def run(lake, etf: str, day: str, ask=None, *, instrument_id: str | None = None,
        window_start: str | None = None, window_end: str | None = None,
        summary: bool = False, window_meta: dict | None = None) -> str:
    """ETF 하루 또는 요청창 하나를 설명한다."""
    if window_start is not None or window_end is not None:
        if not instrument_id or window_start is None or window_end is None:
            raise ValueError("요청창 설명에는 instrument_id·window_start·window_end가 필요하다")
        facts = window_facts(
            lake, etf, instrument_id, day, window_start, window_end)
        final_payload = final_explanation_payload(facts)
        final = final_payload["rendered_text"]
        plan = build_block_plan(facts)
        if window_meta is not None:
            window_meta.update({
                "window_start": facts.window_start,
                "window_end": facts.window_end,
                "as_of": facts.window_end,
                "blocks": [
                    {
                        "kind": block.key,
                        "evidence_ids": list(block.evidence_ids),
                        "final": list(block.lines),
                    }
                    for block in plan
                ],
                "lineage": list(facts.lineage),
                "final_explanation": final_payload,
            })
        return final
    from ..core.layers import decompose
    from .premium import screen
    from .premium5 import premium_5m
    out: list[str] = []

    roll = decompose(lake, etf, day)
    if roll is None:
        return f"[ETF {etf} {day}] 층 분해 불가 — 구성종목 이력 또는 ETF 봉이 없다"
    # 괴리 판정은 `premium.screen` 이 셀 목록으로 준다 (NAV vs 가격). 그날 이 ETF 의
    # 셀이 없으면 판정 없이 진행한다 - 없는 판정을 만들지 않는다.
    pv = None
    try:
        target_id = instrument_id
        if target_id is None:
            found = lake.sql(
                "SELECT instrument_id FROM rdb.public.instrument "
                f"WHERE ticker = '{etf.split('.')[0]}' LIMIT 1")
            target_id = str(found[0][0]) if found else None
        pv = next((c.verdict for c in screen(lake, day, day)
                   if c.etf_id == target_id), None)
        if pv is None:
            # **사유를 틀리게 쓰지 않는다.** `screen` 은 트리거가 울린 셀만 돌려주므로
            # 부재의 원인은 NAV 가 아니라 **트리거 미발화**일 수 있다. 실측(091160
            # 07-27): 여기서 'NAV 커버리지 밖' 이라 말하는 같은 산출물의 다음 줄이
            # NAV 로 5분 괴리를 분해했다 - 진단이 자기모순이면 백필 우선순위가 틀어진다.
            out.append("[괴리] 이 ETF·이 날의 괴리 셀 없음 — 트리거 미발화 또는 NAV·종가 "
                       "부재 (둘 중 무엇인지는 아래 5분 분해 줄이 가른다). "
                       "바스켓/수급 분기 없이 층 라우팅만 한다")
    except Exception as e:                          # noqa: BLE001 - 부재는 사유와 함께
        out.append(f"[괴리] 판정 불가 — {type(e).__name__}: {str(e)[:70]}")
    # 5분 괴리 분해는 **선제적**이다 - NAV 가 있으면 하루를 바스켓 몫과 괴리변화 몫으로
    # 쪼갠다(로그 항등식). 재료가 없으면 사유 한 줄만 남기고 넘어간다 - 이것 때문에
    # 셀 설명이 멈추면 안 된다. 실측(2026-08): NAV 33종목 중 5분봉이 겹치는 것 1종목.
    split, why5 = premium_5m(lake, etf, day)
    out.append(f"[괴리·5분] {why5}")
    if split is not None:
        # 괴리 몫이 **어느 창에서** 났는지 짚는다 - 하루 합만 주면 '방금 왜' 에 답이 없다
        for w in sorted(split.wins, key=lambda x: -abs(x.d_prem))[:3]:
            out.append(f"  {str(w.ts)[11:16]} 괴리 {w.premium * 100:+.2f}% · "
                       f"ETF {w.r_etf * 100:+.2f}%p = 바스켓 {w.r_bk * 100:+.2f}%p "
                       f"+ 괴리 {w.d_prem * 100:+.2f}%p")

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
    imps: list = []
    out.extend(_workflow(lake, roll, r, day, imps))
    honest = "\n".join(out)
    if ask is None:
        return honest
    return _dual(lake, roll, r, day, honest, ask, split, imps)


def _dual(lake, roll, r, day: str, honest: str, ask, split=None,
          imps: list | None = None) -> str:
    """정직한 설명 + 토스식. ETF 는 5분봉이 없어 창이 없다 - **그 사실을 정직하게**
    다루고, '최근 시점' 대신 시장 사건의 시각(있으면)이나 밤사이를 쓴다."""
    from .mkttrial import event_days
    from ..core.evidence import say_save
    from ..core.plain import PlainError, context, dual, narrate_plain
    from ..core.trial import reduce_market
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
    from ..core.evidence import _plain_num
    from ..core.gates import ALPHA
    stats: list[dict] = []
    # 밤사이 해외 지수 움직임은 **관측치**다 - 추론이 아니므로 구간도 p 도 없다.
    # 그런데 ref 를 안 주면 '밤사이 불안이 줄었어요' 라는 사실 서술이 '근거 없이
    # 단언' 으로 즉사한다(실측 2회). 관측도 근거다 - ref 를 준다.
    for nm in over:
        stats.append({"ref": f"s{len(stats) + 1}", "kind": "밤사이 해외 실측",
                      "무엇": nm, "note": ""})
    if mr and mr.get("factor_name"):
        stats.append(_plain_num({
            "ref": f"s{len(stats) + 1}", "kind": "밤사이 환원", "factor": mr["factor_name"],
            "factor_ret": mr.get("gap_factor_ret"),
            "beta_ci": list(mr.get("gap_beta") or ()),
            "explained": mr.get("mkt_explained"),
            "note": mr.get("gap_reason", "")[:60]}))
    # 5분 괴리 분해가 있으면 **하루의 두 몫**을 재료로 준다. 이것이 '바스켓이 올라서'
    # 와 'ETF 값만 따로 올라서(수급)' 를 낱말이 아니라 수치로 갈라 준다.
    if split is not None:
        top = max(split.wins, key=lambda w: abs(w.d_prem))
        stats.append(_plain_num({
            "ref": f"s{len(stats) + 1}", "kind": "5분 괴리 분해",
            "하루": round(split.total, 5), "바스켓몫": round(split.basket, 5),
            "괴리변화몫": round(split.premium_move, 5),
            "괴리_시작": round(split.prem_open, 5), "괴리_끝": round(split.prem_last, 5),
            "괴리_최대창": str(top.ts)[11:16], "그_창_괴리몫": round(top.d_prem, 5),
            "창수": len(split.wins),
            "판정": ("바스켓이 끌었다" if abs(split.basket) >= abs(split.premium_move)
                   else "ETF 값만 따로 움직였다(수급)")}))
    # 성립한 함의의 **조절 조건**을 재료로 준다. 이것이 '이 사건이 이런 종목에서 더
    # 크게 나타났어요' 를 가능하게 한다 - 없으면 쉬운 설명은 평균 효과만 말하고,
    # 정직한 설명에만 조절이 찍혀 두 산출물이 다른 것을 말한다.
    for x in (imps or []):
        for nm, coef, pi_, pj in getattr(x, "mods", ()):
            stats.append(_plain_num({
                "ref": f"s{len(stats) + 1}", "kind": "조절 조건",
                "사건": x.claim.split()[0] if x.claim else "",
                "조건": nm, "방향말": "높을수록 더 크게" if coef > 0 else "높을수록 덜",
                "조절계수": round(float(coef), 5),
                "안정성": round(float(pi_), 3), "p": round(float(pj), 4),
                "판정": "조절 성립 (원인 아님 - 조절이다)"}))

    # **판정 등급을 코드가 실어 준다.** 수치만 주면 모델이 p 를 제 맘대로 읽는다 -
    # 실측: p=0.232 · ATT -2.5%p 를 '큰 영향을 주지 않았어요' 로 단정했다.
    stats += [_plain_num({
        "ref": f"s{len(stats) + i}", "kind": "시장사건 시행", "etype": x["etype"],
        "att": round(float(x["att"]), 5), "p": round(float(x["p"]), 4),
        "n_days": x["n_days"], "unit": "거래일",
        "판정": "유의" if x["p"] < ALPHA else "못 가름(무유의 - 영향 없음이 아니다)"})
        for i, x in enumerate(
            (z for z in (scr or []) if z.get("verdict") == "계산됨"), 1)]
    try:
        # **구성종목 뉴스는 항상 재료로 준다.** `narrative_allowed` 는 '서사로 하루를
        # 설명해도 되나' 를 묻는 게이트이고, '어떤 소식이 있었나' 는 관측이다 - 둘을
        # 같은 스위치로 묶으면 통계가 서는 날에는 뉴스를 영원히 못 말한다.
        news = member_news(lake, roll, day)
        plain, bundles = narrate_plain(ask, ctx, news=news, stats=stats, cell=roll.etf,
                                      day=day, layer=r.kind)
        # 묶음은 **만든 그 자리에서** 적재한다. 꼬리표 id 만 산출물로 나가고 본문이
        # 아무 데도 없으면, 나중에 그 문장이 무엇에 근거했는지 되짚을 방법이 없다.
        if line := say_save(bundles):
            plain += f"\n{line}"
        return dual(honest, plain, bundles)
    except PlainError as e:
        return dual(honest, f"(쉬운 설명 생성 실패 - 계약 위반: {e})")


def _observed_types(lake, ticker: str, day: str) -> list[str]:
    """그날 그 종목에 **접지된** 사건 타입. 부재는 빈 목록이다 - 지어내지 않는다.

    하드코딩된 타입으로 검정을 세우면 그 타입이 없는 날에도 "처치일 부족" 만 반복하고,
    실제로 난 사건은 아무도 안 본다(실측: 종목 경로가 `RESULT_RELEASE` 만 물었다).
    """
    try:
        from ..core.paneltest import _base
        rows = lake.sql(
            _base(day) +
            f"\nSELECT DISTINCT e.event_type_code FROM v_event e "
            f"JOIN v_instrument i ON i.instrument_id = e.instrument_id "
            f"WHERE i.ticker = '{ticker.split('.')[0]}' "
            f"  AND e.trade_date = DATE '{day}' ORDER BY 1")
    except Exception:                               # noqa: BLE001 - 부재는 빈 목록
        return []
    return [str(r[0]) for r in rows if r and r[0]]


def _sector_types(lake, day: str, top: int = 3) -> list[str]:
    """그날 시장 전체에서 관측된 **광역 타입** 상위 - 섹터 공통 처치 후보.

    섹터 처치는 특정 종목의 사건이 아니라 여러 종목에 동시에 닿은 타입이다. 그래서
    '몇 종목에 닿았나' 로 고른다 - 하드코딩 2종은 그날 무엇이 났는지와 무관했다.
    """
    try:
        from ..core.paneltest import _base
        rows = lake.sql(
            _base(day) +
            f"\nSELECT e.event_type_code, count(DISTINCT e.instrument_id) n "
            f"FROM v_event e WHERE e.trade_date = DATE '{day}' "
            f"GROUP BY 1 HAVING count(DISTINCT e.instrument_id) >= 2 "
            f"ORDER BY 2 DESC, 1 LIMIT {top}")
    except Exception:                               # noqa: BLE001
        return []
    return [str(r[0]) for r in rows if r and r[0]]


def member_news(lake, roll, day: str, top: int = 4, per: int = 4) -> list[dict]:
    """**구성종목**의 뉴스. ETF 자신에는 사건이 안 붙는다 - 트리거가 없다.

    이것이 없어서 ETF 산문이 '어떤 뉴스 때문인지' 를 영원히 말할 수 없었다:
    `news_objectset(lake, <ETF id>, day)` 는 항상 `[]` 다(사건은 종목의 것이다).
    기여 절댓값 상위 종목의 뉴스를 모아 **그 종목 이름과 함께** 준다 - 어느 종목의
    소식인지 말하지 않으면 ETF 설명에서 뜻이 없다.

    **뉴스는 관측이다.** 여기 있다는 것이 원인이라는 뜻이 아니다 - 그래서 각 항목에
    `판정: 관측 - 방향 미검정` 을 달아 보낸다(`plain._sign_guard` 가 방향 주장을 막는다).
    """
    from ..core.evidence import news_objectset
    from ..core.paneltest import _base
    names = sorted((n for n in roll.names if n.contribution is not None),
                   key=lambda n: -abs(n.contribution))[:top]
    if not names:
        return []
    want = {n.ticker.split(".")[0]: n for n in names}
    try:
        # `v_instrument` 는 **독립 뷰가 아니라 `_base(day)` 가 만드는 CTE** 다.
        # 이걸 빼먹어 질의가 `CatalogException` 으로 죽고 `except: return []` 가
        # 조용히 삼켰다 - 30일 배치에서 뉴스 인용이 0/30 이었던 이유다. 부재를 빈
        # 목록으로 돌려주는 except 절이 원인을 통째로 감췄다.
        rows = lake.sql(_base(day) + "SELECT ticker, instrument_id FROM v_instrument "
                        "WHERE ticker IN (" + ",".join(repr(t) for t in want) + ")")
    except Exception as exc:                        # noqa: BLE001 - 사유를 남긴다
        return [{"ref": "!뉴스오류", "title": f"{type(exc).__name__}: {str(exc)[:80]}",
                 "종목": "", "판정": "조회 실패 - 부재가 아니다"}]
    out: list[dict] = []
    for tk, iid in rows:
        nm = want.get(str(tk))
        for o in news_objectset(lake, str(iid), day, limit=per):
            if str(o.get("ref", "")).startswith("!"):
                continue                            # 조회 오류 항목은 재료가 아니다
            out.append({**o, "ref": f"n{len(out) + 1}",
                        "종목": (nm.label or str(tk)) if nm else str(tk),
                        "판정": "관측 - 방향 미검정"})
    return out


def _workflow(lake, roll, r, day: str,
              sink: list | None = None) -> list[str]:
    """분해에서 실질적인 시장·섹터·고유층을 각각 실행한다.

    `Route.kind`는 대표 제목일 뿐 다른 층을 막지 않는다. `sink`에는 성립한 함의를
    모아 쉬운 설명 재료로 넘긴다.
    """
    from .interval import FLOOR
    from ..core.layers import MARKET_CODE
    from .route import MIN_NAME_SHARE, TOP_NAMES
    from ..core.trial import reduce_market, say_market

    out: list[str] = []
    if r.kind == "괴리단독":
        out.append("[워크플로우] ETF 고유 — 구성종목 인과를 세우지 않는다. 수급·유동성"
                   " 축만 유효하고, 그 축은 되돌림 후보다.")
        return out

    layers = tuple(getattr(roll, "layers", ()))
    total = sum(abs(x.contribution) for x in layers) + abs(roll.idio)

    def material(value: float) -> bool:
        return total > 0 and abs(value) / total >= FLOOR

    is_market_proxy = str(getattr(roll, "etf", "")).endswith(MARKET_CODE)
    market_on = is_market_proxy or any(
        x.kind == "시장" and material(x.contribution) for x in layers)
    sectors = (() if is_market_proxy else tuple(
        x for x in layers if x.kind == "섹터" and material(x.contribution)))
    idio_on = not is_market_proxy and material(roll.idio)
    name_floor = MIN_NAME_SHARE * abs(roll.idio)
    targets = (() if not idio_on else tuple(
        n.ticker for n in roll.names if abs(n.contribution) >= name_floor)[:TOP_NAMES])

    if market_on:
        out.append("[워크플로우] 시장 환원 — 종목 가설을 세우지 않는다")
        out.append(say_market(reduce_market(lake, day, symbol=f"{roll.etf}.KS")))
        from .mkttrial import say_screen, screen_market
        out.append(say_screen(screen_market(lake, day)))

    from ..core.verifier import say_implications, verify
    for sec in sectors:
        out.append(f"[워크플로우] 섹터 공통 처치 — {sec.name} "
                   f"(β {sec.beta:.2f} [{sec.lo:.2f}, {sec.hi:.2f}] · "
                   f"층 수익 {sec.ret * 100:+.2f}%p)")
        ets = _sector_types(lake, day)
        if not ets:
            out.append("  그날 2종목 이상에 닿은 타입이 없다 - 섹터 공통 처치를 "
                       "세울 수 없다 (그것도 결과다)")
        for et in ets:
            imps, lg = verify(lake, day, etype=et, layer="섹터")
            if sink is not None:
                sink += [x for x in imps if getattr(x, "credible", True)]
            out.append("  " + lg.replace("\n", "\n  "))
            out.append("  " + say_implications(imps).replace("\n", "\n  "))

    if targets:
        out.append(f"[워크플로우] 종목 개별 시행 — 상위 {len(targets)}종목")
        by_type: dict[str, list[str]] = {}
        for tk in targets:
            nm = next((n for n in roll.names if n.ticker == tk), None)
            ets = _observed_types(lake, tk, day)
            out.append(f"  {tk}" + (f" ({nm.label})" if nm and nm.label else "")
                       + (f" 기여 {nm.contribution * 100:+.2f}%p · 비중 {nm.weight:.1%}"
                          if nm else "")
                       + ("  사건: " + " · ".join(ets) if ets else "  사건 없음"))
            for et in ets:
                by_type.setdefault(et, []).append(tk)
        if not by_type:
            out.append("  상위 종목에 그날 접지 사건이 없다 - 종목 시행을 세울 수 없다 "
                       "(그것도 결과다: 사건 없이 움직인 고유)")
        for et, tks in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            out.append(f"  [검정] {et} — 이 셀에서 {' · '.join(tks)} 가 해당")
            imps, lg = verify(lake, day, etype=et, layer="고유", max_probes=4)
            if sink is not None:
                sink += [x for x in imps if getattr(x, "credible", True)]
            out.append("    " + lg.replace("\n", "\n    "))
            out.append("    " + say_implications(imps).replace("\n", "\n    "))
    elif idio_on:
        out.append("[워크플로우] 고유 몫은 크지만 |기여| 상위 종목이 임계 미달 — "
                   "귀속할 이름이 없다. 이것도 결과다 (분산된 고유)")
    return out


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    from ..core.duck import CausalLake
    # 키가 있으면 쉬운 설명까지, 없으면 정직한 설명만 - **조용히 빠지지 않는다**.
    ask = None
    if key := os.environ.get("DEEPSEEK_API_KEY"):
        from ...adapters.llm import DeepSeekClient, TracingClient
        ask = TracingClient(DeepSeekClient(
            key, os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))).complete_json
    else:
        print("[알림] DEEPSEEK_API_KEY 없음 - 쉬운 설명(토스식)은 생략한다")
    print(run(CausalLake(), sys.argv[1], sys.argv[2], ask))


if __name__ == "__main__":
    main()
