"""인과 흐름 - 정적 분해 → 경쟁가설 DAG → 검정 → 갱신(≤2회) → 구조방정식 → 직관 설명.

워크플로우 (사용자 의도가 각 단계에 명시돼 있다):

  1. 정적분석기: 하루를 시장·섹터·고유로 가른다 (결정론 산술).
  2. 유효한 층에서 |기여| 순으로 **대상 ≤3** 을 뽑는다.
  3. 가설 에이전트: 대상마다 **경쟁가설 튜플 ~3** - 각 튜플에 검정 의도를 적는다.
  4. 병합: 대상 하나 = DAG 하나. 공통요인(통제됨·공유 노드)을 드러내고 한 번 더 검증.
  5. 검정 에이전트 × 간선: 같은 도구 상태기계로 관측하고 결론을 진다.
     성립 → 구조방정식 재료(형·이름·값·의미)를 남긴다. 기각 → 간선 절단 + 사유 보고.
  6. 가설 에이전트: 끊긴 곳을 보고 새 가설구조로 그래프를 갱신, 재검정. **최대 2라운드.**
  7. 구조방정식 에이전트: 연결된 DAG + 재료로 방정식을 세우고 설명분과
     **직관적 설명**을 도출한다 - 일반 투자자가 떠올리기 힘든 연결을 드러내는 것이 목표.

실행: python -m edge_analysis.statics.causeflow <ticker> <instrument_id> <YYYY-MM-DD>
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass

import numpy as np

from .dag import TargetDAG
from .duck import CausalLake
from .hypothesize import propose
from .judge import judge_edge
from .layers import BETA_WINDOW, MARKET_CODE, MIN_BETA_N, _ols, _on, _orth, _series, overnight

MAX_TARGETS = 3
MAX_ROUNDS = 2


# ── 1. 정적분석기: 종목 하루를 시장·섹터·고유로 ──────────────────────────
@dataclass(frozen=True, slots=True)
class LayerFact:
    kind: str      # 시장 | 섹터 | 고유
    label: str     # 사람용 한 줄 (수익률 × β)
    pct: float     # 오늘 기여 (로그수익, 소수)


def stock_layers(lake, tk6: str, day: str) -> tuple[float | None, list[LayerFact]]:
    """종목 하루 = β_m·시장 + β_s·섹터(시장직교) + 고유. **종목이 분해 대상**이다 -
    ETF 를 ETF 로 회귀하던 동어반복은 여기 없다(종목 ⊂ 섹터는 정상 포함관계).
    섹터 후보는 **이 종목을 실제로 담은 ETF** 뿐이다 - 겹침 게이트가 필요 없다."""
    ser = _series(lake, day, ("stock",))
    if tk6 not in ser:
        return None, []
    d0 = dt.date.fromisoformat(day)
    _nm, m, _halt = ser[tk6]
    hist = sorted(d for d in m if d < d0)[-BETA_WINDOW:]
    if len(hist) < MIN_BETA_N or d0 not in m:
        return None, []
    y = np.array([m[d] for d in hist])
    y_now = float(m[d0])

    lay = _series(lake, day, ("market", "sector"))
    if MARKET_CODE not in lay:
        return y_now, []
    _mn, mm, _h = lay[MARKET_CODE]
    xm = _on(mm, hist)
    if xm is None or d0 not in mm:
        return y_now, []
    m_now = float(mm[d0])
    bm, _se = _ols(y, xm.reshape(-1, 1))
    out = [LayerFact("시장", f"KODEX200 {m_now * 100:+.2f}% × β{bm[0]:.2f}",
                     float(bm[0]) * m_now)]

    holders = {r[0] for r in lake.sql(
        f"SELECT DISTINCT etf_id FROM s3_etf_holdings "
        f"WHERE market = 'KR' AND constituent_ticker = '{tk6}'")}
    try:
        holders |= {r[0] for r in lake.sql(
            f"SELECT DISTINCT etf_id FROM etf_holdings_fmp "
            f"WHERE constituent_ticker = '{tk6}'")}
    except Exception:                                  # noqa: BLE001 - FMP 뷰 없음
        pass
    resid = y - xm * float(bm[0])
    best = None
    for sid in holders & set(lay):
        if sid == MARKET_CODE:
            continue
        snm, sm, _sh = lay[sid]
        xs = _on(sm, hist)
        if xs is None or d0 not in sm:
            continue
        xo, xo_now = _orth(xs, xm.reshape(-1, 1), float(sm[d0]), np.array([m_now]))
        if float(xo @ xo) < 1e-12:
            continue
        bs, _ses = _ols(resid, xo.reshape(-1, 1))
        c = float(bs[0]) * xo_now
        if best is None or abs(c) > abs(best[2]):
            best = (sid, snm, c, float(bs[0]), xo_now)
    if best is not None:
        out.append(LayerFact("섹터", f"{best[1]}(시장직교) {best[4] * 100:+.2f}% × β{best[3]:.2f}",
                             best[2]))
    out.append(LayerFact("고유", "층을 뺀 잔여", y_now - sum(f.pct for f in out)))
    return y_now, out


def pick_targets(facts: list[LayerFact], k: int = MAX_TARGETS) -> list[LayerFact]:
    """|기여| 순 상위 ≤k. 0 에 가까운 층은 설명할 것이 없다 - 대상이 아니다."""
    live = [f for f in facts if abs(f.pct) >= 0.0005]      # 5bp 미만은 무대상
    return sorted(live, key=lambda f: -abs(f.pct))[:k]


# ── 2. 셀의 사실 (결정론 - 모든 에이전트가 공유) ─────────────────────────
def _fmt_flows(rows, unit: float = 1e8, tag: str = "억") -> str:
    return " · ".join(f"{r[0]} {r[1] / unit:+,.0f}{tag}" for r in rows) or "없음"


def cell_brief(lake, tk6: str, day: str, total: float, layers: list[LayerFact]) -> str:
    """층 표 + 미국장 + 수급(사후 회계 명시). 결정론이라 물어볼 값이 없는 것들."""
    L = [f"셀: {tk6} {day} · 하루 {total * 100:+.2f}%",
         "층 분해(결정론): " + " · ".join(
             f"{f.kind} {f.pct * 100:+.2f}%p ({f.label})" for f in layers)]
    on = overnight(lake, day)
    if on:
        L.append("밤사이 미국장(개장 전 확정): "
                 + " · ".join(f"{n} {v * 100:+.2f}%" for n, v in
                              sorted(on, key=lambda t: -abs(t[1]))[:4]))
    tf = lake.sql(f"SELECT investor_type, net_value FROM s3_investor_value "
                  f"WHERE ticker = '{tk6}' AND trade_date = DATE '{day}' "
                  f"ORDER BY abs(net_value) DESC LIMIT 4")
    mf = lake.sql(f"SELECT investor_type, sum(net_value) FROM s3_investor_value "
                  f"WHERE market = 'KR' AND trade_date = DATE '{day}' "
                  f"  AND investor_type IN ('foreign','institution_total','individual') "
                  f"GROUP BY 1 ORDER BY 1")
    if tf:
        L.append(f"이 종목 당일 수급(마감 후 집계 - **원인이 아니라 회계**): {_fmt_flows(tf)}")
    if mf:
        L.append(f"시장 전체 당일 수급(같은 주의): {_fmt_flows(mf, 1e12, '조')}")
    return "\n".join(L)


# ── 3~6. 가설 → DAG → 검정 → 갱신 ────────────────────────────────────────
def run(lake, ask, ticker: str, instrument_id: str, day: str,
        *, rounds: int = MAX_ROUNDS) -> str:
    from .attribute import load_cell
    from .paneltest import FEATURES, Z_ANOM, series_z

    tk6 = ticker.split(".")[0]
    total, layers = stock_layers(lake, tk6, day)
    if total is None or not layers:
        return f"{ticker} {day}: 층 분해 불가 - 일봉 창(60일)이 안 찬다"
    targets = pick_targets(layers)

    # 접지 재료: 사건 타입(고유 대상), 오늘 발화 계열(전 대상)
    shares, labels, _ac = load_cell(lake, ticker, instrument_id, day)
    types = sorted({labels[e] for s in shares for e in s.window.event_ids})
    zs = series_z(lake, instrument_id, day)
    fired = sorted(k for k, v in zs.items() if v is not None and abs(v) >= Z_ANOM)
    base = cell_brief(lake, tk6, day, total, layers)
    base += "\n시간 분해(항등식): " + " · ".join(
        f"{s.window.name} {(np.exp(s.log_ret) - 1) * 100:+.2f}%p" for s in shares)
    if fired:
        base += f"\n오늘 발화 계열(|z|≥{Z_ANOM}): " + " · ".join(
            f"{k} z={zs[k]:+.1f}" for k in fired)

    dags = [TargetDAG(t.kind, f"{t.kind} {t.pct * 100:+.2f}%p ({t.label})", t.pct)
            for t in targets]
    audit: list[str] = []

    for rnd in range(1, rounds + 1):
        for dag, tgt in zip(dags, targets):
            if rnd > 1 and dag.connected():
                continue                      # 성립이 있으면 그래프 갱신이 불필요하다
            # 시장·섹터 대상은 사건이 아니라 거시·수급·지수 계열이 접지다 -
            # 표에 근거가 실려 있다(미국장·시장 수급). 고유 대상만 사건 접지.
            et = types if tgt.kind == "고유" else []
            sf = fired if tgt.kind == "고유" else sorted(set(fired) | {"거시", "수급", "지수잔차"})
            head = (f"\n\n[네 대상] {dag.target_label} - 이 몫의 인과를 경쟁가설 3개로 내라."
                    f" 각 가설의 intent 에 '무엇이 사실이면 성립인가'를 적어라.")
            extra = ""
            if rnd > 1:
                extra = ("\n\n[1라운드 결과 - 끊긴 간선과 사유]\n" + dag.render()
                         + "\n끊긴 채널·방아쇠를 반복하지 말고 **새 가설구조**를 내라.")
            tuples, rejected = propose(ask, facts=base + head + extra, event_types=et,
                                       measurable=list(FEATURES), series_families=sf)
            audit += [f"[{dag.target_kind} r{rnd}] 반려: {r}" for r in rejected]
            audit += [f"[{dag.target_kind} r{rnd}] 병합 반려: {r}"
                      for r in dag.add(tuples, round=rnd)]
        for dag in dags:
            for p in dag.validate():
                audit.append(f"[검증] {p}")
            ctx = dag.render(verbose=False)
            for e in dag.pending():
                e.finding = judge_edge(lake, ask, ticker=ticker,
                                       instrument_id=instrument_id, day=day,
                                       edge=e, dag_txt=ctx, facts=base,
                                       types=tuple(types))
        if all(dag.connected() for dag in dags):
            break

    # ── 7. 구조방정식 에이전트 ────────────────────────────────────────────
    material = base + "\n\n" + "\n\n".join(d.render() for d in dags)
    sem = ask(_SEM_SYS, material + "\n\n방정식과 설명을 JSON 으로.")
    eqs = sem.get("equations") or []
    expl = str(sem.get("explanation", ""))

    out = [f"■ {ticker} {day}  하루 {total * 100:+.2f}%", "", base, ""]
    out += [d.render() for d in dags]
    out.append("\n── 구조방정식 (연결된 간선만 항이 된다) " + "─" * 30)
    for q in eqs:
        out.append(f"  {q.get('target', '')}: {q.get('formula', '')}")
        for t in q.get("terms") or []:
            out.append(f"     {t.get('name', '')} = {t.get('value', '')}  · {t.get('meaning', '')}")
    out.append("\n── 직관 설명 " + "─" * 50)
    out.append(expl)
    if audit:
        out.append("\n── 감사 (반려·검증) " + "─" * 40)
        out += [f"  {a}" for a in audit[:12]]
    return "\n".join(out)


_SEM_SYS = """너는 구조방정식 에이전트다. 검정이 끝난 DAG 를 받는다.

할 일:
1. **성립(✓) 간선만** 항으로 하여 대상별 구조방정식을 세워라. 각 항은 검정
   에이전트가 남긴 재료(형·이름·오늘 값·의미)를 쓴다. 잔여(ε)를 숨기지 마라.
2. 설명분: 각 항이 대상 몫의 얼마를 설명하는지 - 재료의 수치로만. 지어내지 마라.
3. 직관 설명: 4~8문장. 선두는 하루의 본체(가장 큰 몫). **일반 투자자가 떠올리기
   힘든 비자명한 연결**(예: 거시→섹터→종목의 사슬, 수급 주체 교대)이 성립 간선에
   있으면 그것을 명시적으로 한 문장 강조하라. 기각된 통념(끊긴 간선)이 있으면
   "~가 아니다"로 먼저 치워라. 숫자는 준 재료에 있는 것만 인용하라.

JSON:
{"equations": [{"target": "시장|섹터|고유",
                "formula": "대상(-x.xx%p) = 항1 + 항2 + ε(잔여)",
                "terms": [{"name": "변수명", "value": "오늘 값", "meaning": "이 항의 뜻"}]}],
 "explanation": "직관 설명"}"""


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    import os

    from ..adapters.llm import DeepSeekClient, TracingClient
    client = TracingClient(DeepSeekClient(
        os.environ["DEEPSEEK_API_KEY"],
        os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")))
    print(run(CausalLake(), client.complete_json, *sys.argv[1:]))


if __name__ == "__main__":
    main()
