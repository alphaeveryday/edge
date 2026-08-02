"""ETF 하루를 설명한다 - 층은 산술로, 종목은 서사로.

투자자가 실제로 묻는 것은 "오늘 반도체 왜 빠졌어?" 지 "SK하이닉스 셀의 창별 몫" 이
아니다. 그래서 설명 대상을 ETF 하루로 올리고 위에서 아래로 내려간다:

    밤사이 미국장 (개장 전 확정)  →  시장  →  섹터 ≤2  →  종목 ≤5

앞 세 층은 **결정론적 산술**이다 - 모델이 끼지 않는다. 모델은 마지막 종목 고유분에만
들어가 5분봉 시간 분해와 튜플 패널 검정을 돈다. 그래서 하루가 최대 8개 서사로 닫히고,
그 중 모델이 만든 건 5개뿐이며 나머지는 검산 가능하다.

실행: python -m edge_analysis.statics.etfday <etf_id> <YYYY-MM-DD> [종목수]
"""
from __future__ import annotations

import sys

from .duck import CausalLake
from .layers import Rollup, decompose, market_source, overnight


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}"


def headline(lake, r: Rollup) -> str:
    """층 산술을 투자자 어법으로. **검산 가능해야 한다** - 합이 안 맞으면 아무도 안 믿는다."""
    L = [f"■ {r.etf_name}({r.etf})  {r.day}  하루 {_pct(r.total)}%", ""]

    on = overnight(lake, r.day)
    if on:
        big = sorted(on, key=lambda t: -abs(t[1]))[:3]
        L.append("  밤사이 미국장 (개장 전 확정 - 국내 장중 사건보다 앞선다)")
        L.append("     " + " · ".join(f"{n} {_pct(v)}%" for n, v in big))
        L.append("")

    if not r.layers:
        L.append("  층 없음 - 시장 자료가 안 서서 분해하지 못했다 (판정불가)")
        return "\n".join(L)

    L.append("  덮은 층")
    src = market_source(lake, r.day)
    for x in r.layers:
        tag = "코스피" if x.kind == "시장" else "섹터"
        ov = "" if x.kind == "시장" else f" (구성 {x.overlap * 100:.0f}% 겹침)"
        L.append(f"     {tag} {x.name}{ov} {_pct(x.ret)}% × 민감도 β{x.beta:.2f}"
                 f"[{x.lo:.2f},{x.hi:.2f}] = {_pct(x.contribution)}%p")
        if x.kind == "시장" and src is not None:
            lo, hi = src
            # 코스피가 왜 움직였나 - 밤사이 미국으로 설명되는 몫. 점이 아니라 구간이 정직하다.
            same = (lo <= x.ret <= hi) or (min(abs(lo), abs(hi)) <= abs(x.ret) <= max(abs(lo), abs(hi)))
            verdict = ("← 코스피 움직임이 미국으로 설명된다" if same else
                       "← 미국으로 설명 안 된다. **국내 요인이다**")
            L.append(f"        └ 그 코스피 {_pct(x.ret)}% 중 밤사이 미국이 설명하는 몫 "
                     f"[{_pct(lo)}, {_pct(hi)}]%p  {verdict}")
    L.append(f"     {'─' * 58}")
    L.append(f"     층 합계 {_pct(sum(x.contribution for x in r.layers))}%p"
             f"  ·  남은 몫 {_pct(r.idio)}%p  ·  **설명 {r.coverage * 100:.0f}%**")
    if r.coverage < 0:
        L.append("     ← 층이 오히려 반대로 밀었다. 시장·섹터로는 설명되지 않는 날이다")
    elif r.coverage < 0.5:
        L.append("     ← 절반도 못 덮었다. 남은 몫이 이 하루의 본체다")
    L.append("")

    if r.twins or r.alien:
        L.append(f"  후보에서 뺀 ETF: 구성이 겹쳐 동어반복 {len(r.twins)}종"
                 f" · 구성이 안 겹쳐 근거 없음 {len(r.alien)}종"
                 " — 적합도가 아니라 구성 겹침이 후보 자격을 정한다")
    if r.rho is not None and abs(r.rho) >= 0.15:
        L.append(f"  ⚠ 종목 잔차 공통상관 ρ={r.rho:+.3f} - 이름 없는 공통요인이 남아 있다."
                 " 아래 '고유'는 아직 고유가 아니다")
    if r.halted:
        L.append(f"  ⚠ 거래정지 {r.halted}종목 제외 - 그날 수익률 0 은 참이 아니다")
    if r.rollup_gap is not None and abs(r.rollup_gap) > 0.005:
        L.append(f"  ⚠ 구성종목 가중합이 ETF 와 {_pct(r.rollup_gap)}%p 어긋난다"
                 f" (커버 비중 {r.weight_covered * 100:.0f}%·추적오차)")
    L.append("")

    L.append(f"  남은 몫 {_pct(r.idio)}%p 을 청구하는 종목 (비중 × 고유)")
    for n in r.names:
        L.append(f"     {n.label}({n.ticker}) 비중 {n.weight * 100:.1f}% · "
                 f"수익 {_pct(n.ret)}% 중 고유 {_pct(n.idio)}% → {_pct(n.contribution)}%p")
    # 순합만 쓰면 부호 상쇄를 숨긴다: 상위 5 가 ±0.6%p 씩 밀고 당겨 순합 +0.01%p 여도
    # "아무 일 없었다"가 아니다. 총량과 순합을 같이 낸다.
    claimed = sum(n.contribution for n in r.names)
    gross = sum(abs(n.contribution) for n in r.names)
    L.append(f"     상위 {len(r.names)}종목: 순합 {_pct(claimed)}%p "
             f"(밀고 당긴 총량 {gross * 100:.2f}%p - 서로 상쇄된다) / "
             f"나머지 {max(r.n_names - len(r.names), 0)}종목 {_pct(r.idio - claimed)}%p")
    return "\n".join(L)


def instrument_ids(lake, tickers: list[str], day: str) -> dict[str, str]:
    """ticker → instrument_id. 없는 종목은 빠진다 - 셀을 못 돌린다는 뜻이고 그렇게 보고한다.

    `v_instrument` 는 상시 뷰가 아니라 `_base()` 가 앞에 붙이는 PIT 클램프 CTE 다.
    """
    from .paneltest import _base
    if not tickers:
        return {}
    lst = ", ".join(f"'{t}'" for t in tickers)
    return {t: i for t, i in lake.sql(
        _base(day) + f"SELECT ticker, instrument_id FROM v_instrument "
                     f"WHERE ticker IN ({lst})")}


def run_etf_day(lake: CausalLake, ask, etf: str, day: str, *, names: int = 5) -> str:
    """ETF 하루 = 층 산술 + 상위 종목의 셀 서사. 모델은 마지막 단계에만 들어간다."""
    from .attribute import run_cell

    r = decompose(lake, etf, day, top=names)
    if r is None:
        return f"{etf} {day}: 분해 불가 - 일봉 창(60일)이 안 찬다"
    out = [headline(lake, r)]

    ids = instrument_ids(lake, [n.ticker for n in r.names], day)
    for n in r.names:
        out.append("\n" + "═" * 78)
        out.append(f"▸ {n.label}({n.ticker}) - 고유 {_pct(n.idio)}% · "
                   f"ETF 기여 {_pct(n.contribution)}%p")
        out.append("═" * 78)
        iid = ids.get(n.ticker)
        if not iid:
            out.append(f"  instrument_id 없음 - 셀을 못 돈다 (판정불가, 자료 일감)")
            continue
        try:
            out.append(run_cell(lake, ask, f"{n.ticker}.KS", iid, day))
        except Exception as e:                                  # noqa: BLE001
            out.append(f"  셀 실패: {type(e).__name__}: {str(e)[:200]}")
    return "\n".join(out)


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: etfday <etf_id> <YYYY-MM-DD> [종목수]")
    etf, day = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    lake = CausalLake()
    if n == 0:                                       # 층만 - 모델 호출 없음
        r = decompose(lake, etf, day)
        print(headline(lake, r) if r else f"{etf} {day}: 분해 불가")
        return
    import os
    from ..adapters.llm import DeepSeekClient, TracingClient
    client = TracingClient(DeepSeekClient(
        os.environ["DEEPSEEK_API_KEY"],
        os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")))
    print(run_etf_day(lake, client.complete_json, etf, day, names=n))


if __name__ == "__main__":
    main()
