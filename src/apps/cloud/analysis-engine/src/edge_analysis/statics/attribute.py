"""셀 러너 — 취약성·채널 튜플 체계의 전 루프: 분해 → 가설 → 패널 게이트 → 서술.

한 셀에서 도는 것 (설계 §1 세 분리 그대로):
  크기   시간 항등식 (tree)                          — 오늘 · 산술
  인과   튜플 → 패널 게이트 (hypothesize→paneltest)  — 역사 · 3값
  서술   게이트 통과분만 처치로 표기, 나머지 미설명   — narrate 계약

몫 배정 규칙: 사건 창의 타입이 성립 튜플의 점 방아쇠와 일치하면 그 창의
처치가 그 채널이 된다. 계수(est)는 붙이지 않는다 - 게이트는 크기를 만들지
않는다(§11). 크기 주장은 SEM(폴드 B)이 생겨야 하고, 그 전에 숫자를 적으면
그게 바로 우리가 STORM 에서 잰 날조다.

사용:  python -m edge_analysis.statics.attribute <ticker> <instrument_id> <YYYY-MM-DD>
       env: EDGE_RDB_DSN · CAUSAL_BACKFILL_DIR · DEEPSEEK_API_KEY
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from datetime import datetime, time, timedelta, timezone

from .duck import CausalLake
from .hypothesize import propose
from .narrate import Edge, narrate
from .paneltest import EdgeReport, edge_test
from .render import Row, render
from .tree import Share, decompose
from .vocab import HypothesisTuple
from .windows import build_windows

KST = timezone(timedelta(hours=9))


def _kst(ts) -> datetime:
    return ts.astimezone(KST).replace(tzinfo=None) if ts.tzinfo else ts


def _iset(r: EdgeReport, day_total: float) -> tuple[float, float] | None:
    """일 단위 식별집합 = CI(τ̂·Δx) ∩ (0, 하루 총합]. None = CI 없음 또는 모순(§10).

    블록과 산문이 이 한 곳에서 같은 값을 얻는다 - 표·산문 동일 객체 계약의 채널판.
    """
    if r.ci_lo is None or r.ci_hi is None:
        return None
    lo = max(r.ci_lo, 0.0) if day_total >= 0 else max(r.ci_lo, day_total)
    hi = min(r.ci_hi, day_total) if day_total >= 0 else min(r.ci_hi, 0.0)
    return (lo, hi) if lo <= hi else None


def load_cell(lake: CausalLake, ticker: str, instrument_id: str, day: str):
    """셀 재료 조립. smoke 와 같은 규약 (마감 동시호가 포함 · 마감 후 = 알리바이)."""
    d = datetime.strptime(day, "%Y-%m-%d")
    o = datetime.combine(d.date(), time(9, 0))
    c = datetime.combine(d.date(), time(15, 35))
    taus, after_close, labels = [], [], {}
    if lake.exists.get("rdb") is True:
        for t, e in lake.taus(instrument_id, day):
            t = _kst(t)
            if t >= c:
                after_close.append(str(e))     # 창이 아니라 알리바이로 간다
            else:
                taus.append((t, str(e)))
        ids = [e for _, e in taus] + after_close
        for eid in ids:
            labels[eid] = eid[:16]
        if ids:
            for eid, code in lake.sql(
                    "SELECT source_event_id, event_type_code FROM rdb.public.source_event "
                    f"WHERE source_event_id IN ({','.join(repr(e) for e in ids)})"):
                labels[str(eid)] = str(code)
    bars = [(ts.replace(tzinfo=None) if ts.tzinfo else ts, float(px))
            for ts, px in lake.bars(ticker, day)]
    shares = decompose(bars, lake.prev_close(ticker, day), build_windows(o, c, taus))
    return shares, labels, after_close


def cell_facts(ticker: str, day: str, shares: list[Share],
               labels: dict[str, str], after_close: list[str]) -> tuple[str, list[str]]:
    """가설 에이전트에게 주는 사실 문단 + 접지 타입 목록. **결과의 크기는 주되
    사건의 내용은 타입 분포로만** - 결과를 본 특징 오염(§13)은 τ 이후 문서를
    안 주는 것으로 이미 막혀 있고, 여기는 타입·시각 사실만 싣는다."""
    pct = lambda lr: (math.exp(lr) - 1) * 100  # noqa: E731
    total = sum(s.log_ret for s in shares)
    gap = next(s for s in shares if s.window.kind == "gap")
    big = max(shares, key=lambda s: abs(s.log_ret))
    intraday_types = Counter(labels[e] for s in shares for e in s.window.event_ids)
    ac_types = Counter(labels[e] for e in after_close)
    types = sorted(set(intraday_types))
    L = [f"셀: {ticker} {day}. 하루 {pct(total):+.2f}%p.",
         f"시간 분해(항등식): 갭(밤) {pct(gap.log_ret):+.2f}%p · "
         f"최대 몫 {big.window.name} {pct(big.log_ret):+.2f}%p"
         + (" (사건 없는 구간이다 - 보도 사건 주도 서사는 이 사실과 싸워야 한다)"
            if big.window.kind == "residual" else ""),
         "장중 사건 타입 분포: " + (" · ".join(f"{t} ×{n}" for t, n in
                                              intraday_types.most_common()) or "없음")]
    if ac_types:
        L.append("시간 알리바이: 마감 후 보도 "
                 + " · ".join(f"{t} ×{n}" for t, n in ac_types.most_common())
                 + " - 오늘 수익률은 장중 실현이라 이것들은 오늘의 원인이 될 수 없다")
    L.append(f"가설 {3}개를 내라.")
    return "\n".join(L), types


def run_cell(lake: CausalLake, ask, ticker: str, instrument_id: str, day: str) -> str:
    import os
    from .registry import recall, record
    shares, labels, after_close = load_cell(lake, ticker, instrument_id, day)
    facts, types = cell_facts(ticker, day, shares, labels, after_close)
    root = os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill")

    tuples: list[HypothesisTuple] = []
    rejected: list[str] = []
    reports: list[tuple[HypothesisTuple, EdgeReport]] = []
    memory: list[str] = []
    if types:
        from .paneltest import FEATURES, grid_screen
        # 회상이 기록보다 먼저다 (P9 교훈). 과거 셀들의 스크린·게이트 이력은
        # PIT 안전한 사실이고, 가설 에이전트의 어포던스로 들어간다.
        memory = recall(root, day=day, types=types)
        if memory:
            facts += "\n과거 셀 이력 (어포던스 - 확증 아님):\n" + "\n".join(
                f"  - {m}" for m in memory)
        tuples, rejected = propose(ask, facts=facts, event_types=types,
                                   measurable=list(FEATURES))
        reports = [(t, edge_test(lake, t, day, cell_instrument_id=instrument_id))
                   for t in tuples]
        screens = grid_screen(lake, day, types)
    else:
        screens = []

    # 몫 배정: 성립 + 오늘 취약성 충족 + 환원 미불일치 (INUS 의 적용 판정).
    # 크기는 창 행에 싣지 않는다 - SEM 기여는 **일 단위** 추정량이라(패널이 일간 ar)
    # 15분 창의 몫으로 클립하는 것은 범주 오류다 (8차 정정). 창 행은 존재 판정만,
    # 크기의 식별집합은 튜플 블록에서 일 단위 상한(하루 총합)과 교차한다.
    passing = {t.trigger.ident: (t, r) for t, r in reports
               if t.trigger.kind == "점" and r.applies_today}
    rows = []
    for s in shares:
        wtypes = {labels[e] for e in s.window.event_ids}
        hit = next((passing[w] for w in wtypes if w in passing), None)
        if hit is not None:
            t, r = hit
            rows.append(Row(s, treatment=f"{t.trigger.ident[:14]}→{t.channel}",
                            verdict="성립"))
        elif s.window.kind == "event" or (s.window.kind == "gap" and s.window.event_ids):
            rows.append(Row(s, treatment=",".join(s.window.event_ids)[:20],
                            verdict="판정불가"))
        else:
            rows.append(Row(s))
    record(root, day=day, cell=f"{ticker}/{day}", reports=reports, screens=screens)

    # 채널판을 산문에 배선한다 - 표·블록·산문이 같은 값에서 나와야 한다는 계약의
    # 채널 확장. 성립-미적용의 사유는 applies_today 의 부정을 그대로 옮긴다.
    day_total = sum(s.log_ret for s in shares)
    edges = []
    for t, r in reports:
        why = ("" if r.applies_today else
               "취약성 미충족 (INUS)" if r.vuln_satisfied is False else
               "횡단면 방향 반대 (환원 불일치)" if r.reduction.startswith("불일치") else
               "전이 엣지 - 몫 배정 불가" if not r.assignable else "")
        iset = _iset(r, day_total)
        edges.append(Edge(channel=t.channel, event_type=t.trigger.ident,
                          verdict=r.verdict, applied=r.applies_today, why_not=why,
                          iset_lo=iset[0] if iset else None,
                          iset_hi=iset[1] if iset else None,
                          contradiction=r.ci_lo is not None and iset is None))

    story = narrate(ticker=ticker, name=instrument_id[:20], day=day, route=None,
                    rows=rows, grounded=labels, after_close=tuple(after_close),
                    edges=tuple(edges))

    block = ["", "── 튜플 · 패널 게이트 " + "─" * 40]
    if not types:
        block.append("장중 접지 사건이 없어 가설 단계를 건너뛴다 - 계열 방아쇠 판은 아직 없다.")
    for t, r in reports:
        vuln = " ∧ ".join(f"{v.family}/{v.transform}{v.comparator}p{v.percentile:.0%}"[:28]
                          for v in t.vulnerabilities) or "—"
        apply_say = ("오늘 적용" if r.applies_today else
                     "오늘 부적용 - " + ("취약성 미충족" if r.vuln_satisfied is False else
                                        "환원 불일치" if r.reduction.startswith("불일치") else
                                        "패널 미성립"))
        block += [f"[{t.channel}] {t.trigger.kind}:{t.trigger.ident[:44]} 부호{t.sign:+d}",
                  f"    취약성 {vuln} · 노출 {t.exposure.ident}/{t.exposure.transform}",
                  f"    환원(가설): {t.reduction_note[:90]}",
                  f"    패널: {r.line}",
                  f"    오늘: {r.vuln_today or ('미평가 - 패널이 먼저 서야 한다' if t.vulnerabilities else '취약성 없음')} → **{apply_say}**",
                  f"    환원 검사: {r.reduction}"]
        if r.mode == "조절자":
            block.append(f"    §14 조절자 모드 (충족 클래스가 얇어 전체 패널로 검정): {r.moderation}")
        if r.contribution is not None:
            # 식별집합 = SEM 구간 ∩ (0, 하루 총합] - 일 단위끼리의 교차 (§10).
            iset = _iset(r, day_total)
            say = (f"식별집합 [{iset[0] * 100:+.2f}, {iset[1] * 100:+.2f}]%p" if iset else
                   f"**과대식별 모순** - 구간이 하루 총합 {day_total * 100:+.2f}%p 와 안 겹친다")
            block.append(f"    SEM 기여(일 단위): {r.contribution * 100:+.2f}%p "
                         f"[{r.ci_lo * 100:+.2f}, {r.ci_hi * 100:+.2f}] · {say}")
        if r.counterfactual:
            block.append(f"    반사실: {r.counterfactual}")
    if memory:
        block.append("회상(과거 셀): " + " | ".join(memory[:3]))
    if rejected:
        block.append(f"거부된 제출 {len(rejected)}건 (검증기가 죽임): "
                     + " | ".join(x[:60] for x in rejected[:3]))
    if screens:
        block.append("")
        block.append("── 격자 스크린 (탐색 - 방향 사후·p 양측. 확증은 튜플 게이트만) " + "─" * 8)
        for s in screens:
            if "p2" in s:
                block.append(f"  {s['type'][:40]:<40} {s['exposure']:<14} n={s['n']:<5} "
                             f"p₂={s['p2']:.3f} 방향{s['direction']} "
                             f"상위 {s['hi'] * 100:+.2f}% vs 하위 {s['lo'] * 100:+.2f}%")
            else:
                block.append(f"  {s['type'][:40]:<40} {s['status']}")
    return render(rows) + "\n\n" + story + "\n" + "\n".join(block)


if __name__ == "__main__":
    import os
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    from ..adapters.llm import DeepSeekClient
    client = DeepSeekClient(os.environ["DEEPSEEK_API_KEY"],
                            os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    print(run_cell(CausalLake(), client.complete_json, *sys.argv[1:]))
