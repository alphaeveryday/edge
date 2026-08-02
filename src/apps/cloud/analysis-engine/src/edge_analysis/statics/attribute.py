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

from ..observability import record as trace   # registry.record 와 이름 충돌 회피
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


def _clip(lo: float, hi: float, cap: float) -> tuple[float, float] | None:
    """부분식별의 공용 교차: [lo,hi] ∩ (0 방향 cap]. None = 모순 (방향이 어긋난다)."""
    a = max(lo, 0.0) if cap >= 0 else max(lo, cap)
    b = min(hi, cap) if cap >= 0 else min(hi, 0.0)
    return (a, b) if a <= b else None


def _iset(r: EdgeReport, day_total: float) -> tuple[float, float] | None:
    """일 단위 식별집합 = CI(τ̂·Δx) ∩ (0, 하루 총합]. None = CI 없음 또는 모순(§10).

    블록과 산문이 이 한 곳에서 같은 값을 얻는다 - 표·산문 동일 객체 계약의 채널판.
    """
    if r.ci_lo is None or r.ci_hi is None:
        return None
    return _clip(r.ci_lo, r.ci_hi, day_total)


MIN_BETA_N = 40     # 갭 β 추정의 최소 표본 (60d 창 기준 - 이보다 얇으면 부재 선언)


def _beta_ci(xs, ys) -> tuple[float, float] | None:
    """OLS 기울기의 95% CI. 분산 없으면 None. 순수 함수 - 단위검정 대상."""
    import numpy as np
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    n = len(x)
    sxx = float(((x - x.mean()) ** 2).sum())
    if n < 3 or sxx <= 0.0:
        return None
    beta = float(((x - x.mean()) * (y - y.mean())).sum() / sxx)
    resid = y - y.mean() - beta * (x - x.mean())
    se = float((resid @ resid / (n - 2) / sxx) ** 0.5)
    return beta - 1.96 * se, beta + 1.96 * se


def gap_covariate(lake, ticker: str, day: str, gap_share: float):
    """§9: 갭은 더 잘리지 않으므로 공변량으로만 좁힌다 - 그리고 그 좁힘도
    **부분식별**이다. β 의 CI × 직전 미국 세션 수익률 → 갭 몫의 설명 구간.
    점 β 로 곱하면 크기 층이 다시 점 주장으로 오염된다.

    재료가 없으면 GapCovariate(reason=...) 부재 선언 - 침묵 금지. 부재의 사유가
    곧 백필 요청이다 (docs/analysis-engine/data-backfill-requests.md).
    """
    from .narrate import GapCovariate
    try:
        rows = lake.sql(f"""
            WITH d AS (
              SELECT CAST(ts AS DATE) AS dt,
                     first(open ORDER BY ts) AS o, last(close ORDER BY ts) AS c
              FROM bars_5m WHERE symbol = '{ticker}' GROUP BY 1
            ),
            gap AS (
              SELECT dt, ln(o / NULLIF(lag(c) OVER (ORDER BY dt), 0)) AS g FROM d
            ),
            us AS (SELECT CAST(date AS DATE) AS ud, change_pct / 100.0 AS r FROM us_market)
            SELECT g.dt, g.g,
                   (SELECT u.r FROM us u WHERE u.ud < g.dt ORDER BY u.ud DESC LIMIT 1)
            FROM gap g WHERE g.dt <= DATE '{day}' AND g.g IS NOT NULL
            ORDER BY g.dt DESC LIMIT 121
        """)
    except Exception:                       # us_market 뷰 부재 등
        rows = []
    today = next((r for r in rows if str(r[0]) == day), None)
    hist = [(float(r[2]), float(r[1])) for r in rows
            if str(r[0]) != day and r[2] is not None]
    if today is None or today[2] is None:
        return GapCovariate(reason="직전 미국 세션 수익률 없음 (us_market 커버리지 밖 - 백필 필요)")
    if len(hist) < MIN_BETA_N:
        return GapCovariate(reason=f"β 표본 {len(hist)} < {MIN_BETA_N} (us_market {len(hist)}일 - 백필 필요)")
    ci = _beta_ci([h[0] for h in hist], [h[1] for h in hist])
    if ci is None:
        return GapCovariate(reason="β 추정 불가 (공변량 분산 없음)")
    us_t = float(today[2])
    lo, hi = sorted((ci[0] * us_t, ci[1] * us_t))
    clipped = _clip(lo, hi, gap_share)
    return GapCovariate(factor_ret=us_t, n=len(hist), beta_lo=ci[0], beta_hi=ci[1],
                        explained=clipped, contradiction=clipped is None)


def _assign_rows(shares, labels: dict[str, str], passing: dict, refuted: set[str]) -> list[Row]:
    """창 행의 3값 배정. 산문의 자기모순을 여기서 막는다 (10차 정정).

    - 성립·적용 튜플의 타입을 담은 창 → 성립 (처치 표기)
    - 창의 접지 타입 **전부**가 패널 기각(불성립)된 창 → 불성립 - '원인이 아니다'
      를 창 수준에서 말할 자격은 모든 후보가 기각됐을 때뿐이다
    - 나머지 사건 창 → 판정불가. 사유는 창이 모른다 - 채널판이 말한다
      (기존엔 불성립 타입의 창까지 '표본이 없어 판정불가'로 뭉개 [아닌 것 먼저]
       의 엣지 문장과 산문 안에서 모순됐다)
    """
    rows: list[Row] = []
    for s in shares:
        wtypes = {labels[e] for e in s.window.event_ids}
        hit = next((passing[w] for w in wtypes if w in passing), None)
        if hit is not None:
            t, _ = hit
            rows.append(Row(s, treatment=f"{t.trigger.ident[:14]}→{t.channel}",
                            verdict="성립"))
        elif wtypes and wtypes <= refuted:
            rows.append(Row(s, treatment=" · ".join(sorted(wtypes))[:44],
                            verdict="불성립"))
        elif s.window.kind == "event" or (s.window.kind == "gap" and s.window.event_ids):
            rows.append(Row(s, treatment=",".join(s.window.event_ids)[:20],
                            verdict="판정불가"))
        else:
            rows.append(Row(s))
    return rows


def _route_gate(lake, instrument_id: str, day: str):
    """귀속 게이트 D (요인 오염) 1단 배선. 광역 ETF(구성 ≥100종목) 내 비중이
    상한을 넘으면 이 셀의 점귀속은 **거절** - 자기 사건이 요인을 움직이는 대형주는
    요인을 빼면 효과가 같이 빠진다 (gates.route 의 저주 그대로).

    비중 미계측(PIT 상 스냅샷 부재)이나 상한 미만은 None - 나머지 게이트(A·B·C·E)
    가 미배선이라 '점추정'을 선언할 자격이 아직 없다. 부분 배선의 정직한 어법:
    거절은 말할 수 있고, 통과는 아직 말할 수 없다.
    """
    # 상한 0.05 = gates.route 의 weight_cap 과 동일 계약 (한 곳이 바뀌면 둘 다).
    try:
        w = lake.sql(f"""
            WITH broad AS (
              SELECT etf_instrument_id, max(trade_date) AS d
              FROM rdb.public.etf_holding_snapshot
              WHERE trade_date <= DATE '{day}'
              GROUP BY 1 HAVING count(DISTINCT constituent_instrument_id) >= 100
            )
            SELECT max(h.weight_ratio)
            FROM rdb.public.etf_holding_snapshot h
            JOIN broad b ON b.etf_instrument_id = h.etf_instrument_id AND b.d = h.trade_date
            WHERE h.constituent_instrument_id = '{instrument_id}'
        """)
    except Exception:
        return None
    weight = w[0][0] if w and w[0] else None
    return "거절" if weight is not None and float(weight) >= 0.05 else None


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
    from .paneltest import FEATURES, Z_ANOM, grid_screen, series_z
    # 계열 방아쇠의 접지 = 오늘 발화(|z|≥2, 60d). 점 사건이 없어도 계열 이상이
    # 있으면 가설 단계는 돈다 - 무사건 폭락일이 계열 방아쇠의 존재 이유다
    # (14차 정정: 기계는 e1113ce 부터 있었는데 이 게이트가 점 사건에만 걸려
    # 정확히 그 날들에 절대 발화할 수 없었다 - 선언≠배선).
    zs = series_z(lake, instrument_id, day)
    anomalous = sorted(f for f, z in zs.items() if abs(z) >= Z_ANOM)
    if types or anomalous:
        # 회상이 기록보다 먼저다 (P9 교훈). 과거 셀들의 스크린·게이트 이력은
        # PIT 안전한 사실이고, 가설 에이전트의 어포던스로 들어간다.
        memory = recall(root, day=day, types=types)
        if memory:
            facts += "\n과거 셀 이력 (어포던스 - 확증 아님):\n" + "\n".join(
                f"  - {m}" for m in memory)
        if anomalous:
            facts += ("\n오늘 계열 이상 (계열 방아쇠는 이 계열족에서만): "
                      + " · ".join(f"{f} z={zs[f]:+.1f}" for f in anomalous))
        tuples, rejected = propose(ask, facts=facts, event_types=types,
                                   measurable=list(FEATURES),
                                   series_families=anomalous)
        reports = [(t, edge_test(lake, t, day, cell_instrument_id=instrument_id,
                                 m_tests=len(tuples)))
                   for t in tuples]
        screens = grid_screen(lake, day, types) if types else []
    else:
        screens = []

    # 몫 배정: 성립 + 오늘 취약성 충족 + 환원 미불일치 (INUS 의 적용 판정).
    # 크기는 창 행에 싣지 않는다 - SEM 기여는 **일 단위** 추정량이라(패널이 일간 ar)
    # 15분 창의 몫으로 클립하는 것은 범주 오류다 (8차 정정). 창 행은 존재 판정만,
    # 크기의 식별집합은 튜플 블록에서 일 단위 상한(하루 총합)과 교차한다.
    passing = {t.trigger.ident: (t, r) for t, r in reports
               if t.trigger.kind == "점" and r.applies_today}
    refuted = {t.trigger.ident for t, r in reports
               if t.trigger.kind == "점" and r.verdict == "불성립"}
    rows = _assign_rows(shares, labels, passing, refuted)
    record(root, day=day, cell=f"{ticker}/{day}", reports=reports, screens=screens)

    # 채널판을 산문에 배선한다 - 표·블록·산문이 같은 값에서 나와야 한다는 계약의
    # 채널 확장. 성립-미적용의 사유는 applies_today 의 부정을 그대로 옮긴다.
    day_total = sum(s.log_ret for s in shares)
    cell_route = _route_gate(lake, instrument_id, day)
    edges = []
    for t, r in reports:
        why = ("" if r.applies_today else
               "취약성 미충족 (INUS)" if r.vuln_satisfied is False else
               "횡단면 방향 반대 (환원 불일치)" if r.reduction.startswith("불일치") else
               "방아쇠 미발화 (오늘 |z| < 임계)" if r.trigger_fired is False else
               "전이 엣지 - 몫 배정 불가" if not r.assignable else "")
        iset = _iset(r, day_total)
        edges.append(Edge(channel=t.channel, event_type=t.trigger.ident,
                          verdict=r.verdict, applied=r.applies_today, why_not=why,
                          iset_lo=iset[0] if iset else None,
                          iset_hi=iset[1] if iset else None,
                          contradiction=r.ci_lo is not None and iset is None))

    gwin = next((s for s in shares if s.window.kind == "gap"), None)
    gcov = gap_covariate(lake, ticker, day, gwin.log_ret) if gwin is not None else None
    # 셀 판정 전량을 trace 에 남긴다 (18R). collect_trace 밖이면 record 는 no-op -
    # 라이브러리 경로·테스트는 영향 없고, __main__ 만 파일로 떨군다.
    trace("cell.inputs", ticker=ticker, day=day, instrument_id=instrument_id,
           day_total=day_total, types=types, series_z=zs, anomalous=anomalous,
           route=cell_route, after_close=len(after_close), windows=len(shares))
    for t, r in reports:
        trace("edge.verdict", channel=t.channel,
               trigger=f"{t.trigger.kind}:{t.trigger.ident}",
               exposure=f"{t.exposure.ident}/{t.exposure.transform}",
               verdict=r.verdict, n=r.n, p2=r.p, applied=r.applies_today,
               mode=r.mode, moderation=r.moderation, reduction=r.reduction,
               vuln_today=r.vuln_today, trigger_note=r.trigger_note,
               contribution=r.contribution, ci=[r.ci_lo, r.ci_hi],
               iset=_iset(r, day_total), reason=r.reason)
    for s in screens:
        trace("grid.screen", **s)
    if gcov is not None:
        trace("gap.covariate", factor_ret=gcov.factor_ret, n=gcov.n,
               beta=[gcov.beta_lo, gcov.beta_hi], explained=gcov.explained,
               contradiction=gcov.contradiction, reason=gcov.reason)

    story = narrate(ticker=ticker, name=instrument_id[:20], day=day, route=cell_route,
                    rows=rows, grounded=labels, after_close=tuple(after_close),
                    edges=tuple(edges), gap_cov=gcov)

    block = ["", "── 튜플 · 패널 게이트 " + "─" * 40]
    if not types and not anomalous:
        # 부재≠판정: z 를 못 잰 것(가격계열 결손)과 조용한 것(|z|<2 관측)은 다르다.
        quiet = ("계열 z 미계측 (가격계열 결손 - 발화 판정 불가)" if not zs
                 else "계열 이상 없음 (" + " · ".join(f"{f} z={z:+.1f}" for f, z in sorted(zs.items())) + ")")
        block.append(f"장중 접지 사건이 없고 {quiet} - 가설 단계를 건너뛴다.")
    if reports:
        block.append(f"검정 규약: 산업층 이중차감 ar · 양측 p₂ · 셀 Bonferroni "
                     f"α=0.05/{len(reports)} (학술 수리 ①②③)")
    for t, r in reports:
        vuln = " ∧ ".join(f"{v.family}/{v.transform}{v.comparator}p{v.percentile:.0%}"[:28]
                          for v in t.vulnerabilities) or "—"
        apply_say = ("오늘 적용" if r.applies_today else
                     "오늘 부적용 - " + ("취약성 미충족" if r.vuln_satisfied is False else
                                        "환원 불일치" if r.reduction.startswith("불일치") else
                                        "방아쇠 미발화" if r.trigger_fired is False else
                                        "패널 미성립"))
        block += [f"[{t.channel}] {t.trigger.kind}:{t.trigger.ident[:44]} 부호{t.sign:+d}",
                  f"    취약성 {vuln} · 노출 {t.exposure.ident}/{t.exposure.transform}",
                  f"    환원(가설): {t.reduction_note[:90]}",
                  f"    패널: {r.line}",
                  f"    오늘: {r.vuln_today or ('미평가 - 패널이 먼저 서야 한다' if t.vulnerabilities else '취약성 없음')} → **{apply_say}**",
                  f"    환원 검사: {r.reduction}"]
        if r.trigger_note:
            block.append(f"    {r.trigger_note}")
        if r.mode == "조절자":
            block.append(f"    §14 조절자 모드 (충족 클래스가 얇어 전체 패널로 검정): {r.moderation}")
        if r.contribution is not None:
            # 식별집합 = SEM 구간 ∩ (0, 하루 총합] - 일 단위끼리의 교차 (§10).
            iset = _iset(r, day_total)
            say = ("셀 점귀속 거절(요인 오염) - 인용 금지, 요인 재구성 후 재계산"
                   if cell_route == "거절" else
                   f"식별집합 [{iset[0] * 100:+.2f}, {iset[1] * 100:+.2f}]%p" if iset else
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
    import json
    import os
    from datetime import datetime as _dt
    from pathlib import Path
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    from ..adapters.llm import DeepSeekClient, TracingClient
    from ..observability import collect_trace
    client = TracingClient(DeepSeekClient(
        os.environ["DEEPSEEK_API_KEY"],
        os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")))
    with collect_trace() as tr:
        out = run_cell(CausalLake(), client.complete_json, *sys.argv[1:])
    print(out)
    # 프롬프트·응답 원문은 stdout 금지(observability 계약) - 파일로만 흐른다.
    d = Path(os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill")) / "traces"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sys.argv[1]}_{sys.argv[3]}_{_dt.now():%H%M%S}.jsonl"
    f.write_text("\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in tr),
                 encoding="utf-8")
    print(f"\n[trace] {len(tr)}건 → {f}")
