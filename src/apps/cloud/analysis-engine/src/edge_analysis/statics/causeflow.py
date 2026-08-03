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
import math
import sys
import time
from dataclasses import dataclass


def _log(msg: str) -> None:
    """진행을 stderr 로 즉시 흘린다 - 백그라운드 실행을 밖에서 볼 수 있어야 한다."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)

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
    """종목 하루 = β_m·시장 + β_s·섹터(시장직교) + 고유. **종목이 분해 대상**이다.

    섹터는 **KRX 업종지수**다 - 우리가 고르지 않는다(statics.krxsector, 분기 PIT).
    그전에는 '이 종목을 담은 ETF 중 설명력 최대' 로 골랐고, 그 결과 삼성전자를
    섹터라고 부르는 일이 났다(실측: 042700 07-31). 설명력으로 고르면 섹터가 아니라
    '가장 잘 맞는 무엇' 이 되고, 그건 섹터의 정의가 아니다."""
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

    # 섹터 = 이 종목의 KRX 업종지수. 분기 스냅샷 중 as-of 최신 것을 쓴다.
    resid = y - xm * float(bm[0])
    rows = lake.sql(
        f"SELECT si.trade_date, si.close, sm.code FROM sector_member sm "
        f"JOIN sector_index si ON si.code = sm.code "
        f"WHERE sm.ticker = '{tk6}' AND sm.as_of = ("
        f"  SELECT max(as_of) FROM sector_member "
        f"  WHERE ticker = '{tk6}' AND as_of <= DATE '{day}') "
        f"ORDER BY si.trade_date")
    if rows:
        from .krxsector import sector_name
        code = str(rows[0][2])
        lv = {r[0] if isinstance(r[0], dt.date) else dt.date.fromisoformat(str(r[0])):
              float(r[1]) for r in rows if r[1]}
        ds = sorted(lv)
        sm = {ds[i]: math.log(lv[ds[i]] / lv[ds[i - 1]])
              for i in range(1, len(ds)) if lv[ds[i - 1]] > 0}
        xs = _on(sm, hist)
        if xs is not None and d0 in sm:
            xo, xo_now = _orth(xs, xm.reshape(-1, 1), float(sm[d0]), np.array([m_now]))
            if float(xo @ xo) >= 1e-12:
                bs, _ses = _ols(resid, xo.reshape(-1, 1))
                out.append(LayerFact(
                    "섹터",
                    f"{sector_name(code)}(시장직교) {xo_now * 100:+.2f}% × β{bs[0]:.2f}",
                    float(bs[0]) * xo_now))
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
            _log(f"r{rnd} {dag.target_kind}: 경쟁가설 요청")
            tuples, rejected = propose(ask, facts=base + head + extra, event_types=et,
                                       measurable=list(FEATURES), series_families=sf)
            _log(f"r{rnd} {dag.target_kind}: 튜플 {len(tuples)} · 반려 {len(rejected)}")
            audit += [f"[{dag.target_kind} r{rnd}] 반려: {r}" for r in rejected]
            audit += [f"[{dag.target_kind} r{rnd}] 병합 반려: {r}"
                      for r in dag.add(tuples, round=rnd)]
        for dag in dags:
            for p in dag.validate():
                audit.append(f"[검증] {p}")
            ctx = dag.render(verbose=False)
            for e in dag.pending():
                _log(f"검정 {e.eid} {e.tup.channel}·{e.tup.trigger.ident}")
                e.finding = judge_edge(lake, ask, ticker=ticker,
                                       instrument_id=instrument_id, day=day,
                                       edge=e, dag_txt=ctx, facts=base,
                                       types=tuple(types))
        if all(dag.connected() for dag in dags):
            break

    # ── 7. 구조방정식 에이전트 ────────────────────────────────────────────
    _log("구조방정식 에이전트")
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


_GROUND_SQL = """
SELECT e.event_type_code, e.role_code, e.title, e.available_at,
       th.novelty_status, th.thread_key, th.current_stage
FROM v_event e
LEFT JOIN v_thread th ON th.source_event_id = e.source_event_id
WHERE e.instrument_id = '{iid}' AND e.trade_date = DATE '{day}'
ORDER BY e.available_at
"""

_NEWS_SQL = """
SELECT n.title, n.available_at, n.source_code
FROM v_event e
JOIN v_event_news en ON en.source_event_id = e.source_event_id
JOIN v_news n ON n.document_id = en.document_id
WHERE e.instrument_id = '{iid}' AND e.trade_date = DATE '{day}'
ORDER BY n.available_at
LIMIT 12
"""


def _grounding(lake, instrument_id: str, day: str) -> str:
    """접지의 **본문**. 사건 타입 목록만으로는 '왜 오늘' 을 세울 수 없다 - 제목과
    스레드 신규성(신규 보도인가 후속인가)이 가설을 가른다. 8셀 내내 이걸 안 읽고
    타입 이름만 보고 가설을 썼다.

    부재도 문장으로 말한다: '접지 사건 없음' 은 '조회 안 함' 과 다르다."""
    out: list[str] = []
    try:
        rows = lake.sql((_base_views(day) + _GROUND_SQL).format(iid=instrument_id, day=day))
    except Exception as e:                          # noqa: BLE001 - 부재는 사유와 함께
        return f"=== 접지 본문 ===\n조회 실패: {type(e).__name__}: {str(e)[:100]}"
    if not rows:
        return "=== 접지 본문 ===\n오늘 이 종목에 접지된 사건 없음 (조회했고 0건이다)"
    out.append("=== 접지 본문 (사건 · 역할 · 도달시각 · 스레드 신규성) ===")
    for et, role, title, av, nov, tkey, stage in rows[:12]:
        t = str(title or "")[:80]
        av_s = str(av)[11:16] if av else "?"
        nov_s = f" · {nov}" if nov else ""
        st_s = f" · {stage}" if stage else ""
        out.append(f"  [{av_s}] {et} ({role}){nov_s}{st_s}\n      {t}")
    try:
        news = lake.sql((_base_views(day) + _NEWS_SQL).format(iid=instrument_id, day=day))
    except Exception:                               # noqa: BLE001
        news = []
    seen: set[str] = set()
    heads = [n for n in news if not (str(n[0]) in seen or seen.add(str(n[0])))]
    if heads:
        out.append("=== 근거 문서 제목 ===")
        out += [f"  [{str(a)[11:16]}] {str(t)[:90]} ({s})" for t, a, s in heads[:8]]
    return "\n".join(out)


def _base_views(day: str) -> str:
    """뷰 표면만 (피처 CTE 없이). 접지 조회는 파생 피처가 필요 없다."""
    from ..adapters.sql_surface import views_sql
    return "WITH " + views_sql(f"TIMESTAMP '{day} 23:59:59'", f"DATE '{day}'", "rdb.public.")


def gather(lake, ticker: str, instrument_id: str, day: str) -> dict:
    """결정론 재료 전부. 하네스 오케스트레이터(에이전트)가 가설·판정·SEM 을 맡고
    코드는 사실만 낸다 - 원격 모델 직렬 왕복이 사라진다."""
    from .attribute import load_cell
    from .paneltest import Z_ANOM, series_z

    tk6 = ticker.split(".")[0]
    total, layers = stock_layers(lake, tk6, day)
    if total is None or not layers:
        raise SystemExit(f"{ticker} {day}: 층 분해 불가")
    targets = pick_targets(layers)
    shares, labels, _ac = load_cell(lake, ticker, instrument_id, day)
    types = sorted({labels[e] for sh in shares for e in sh.window.event_ids})
    zs = series_z(lake, instrument_id, day)
    fired = sorted(k for k, v in zs.items() if v is not None and abs(v) >= Z_ANOM)
    base = cell_brief(lake, tk6, day, total, layers)
    base += "\n시간 분해(항등식): " + " · ".join(
        f"{sh.window.name} {(np.exp(sh.log_ret) - 1) * 100:+.2f}%p" for sh in shares)
    if fired:
        base += f"\n오늘 발화 계열(|z|≥{Z_ANOM}): " + " · ".join(
            f"{k} z={zs[k]:+.1f}" for k in fired)
    base += "\n계열 z 전체(이 종목): " + " · ".join(
        f"{k} {v:+.1f}" for k, v in sorted(zs.items()) if v is not None)
    # 시장 대상 판정에 결정적인 사실 - 코스피 중 밤사이 미국이 설명하는 몫 (구간).
    from .layers import market_source
    ms = market_source(lake, day)
    if ms is not None:
        base += (f"\n코스피 수익 중 밤사이 미국(S&P500)이 설명하는 몫: "
                 f"[{ms[0] * 100:+.2f}, {ms[1] * 100:+.2f}]%p → 나머지는 국내 요인")
    base += "\n" + _grounding(lake, instrument_id, day)
    return {"base": base, "total": total,
            "targets": [(t.kind, t.label, t.pct) for t in targets],
            "event_types": types, "fired": fired}


_W = {}


def _prep_init(ticker: str, iid: str, day: str) -> None:
    """워커 초기화 - 프로세스당 레이크 1개 (duckdb 연결은 프로세스 경계를 못 넘는다)."""
    _W.update(lake=CausalLake(), ticker=ticker, iid=iid, day=day)


def _prep_one(item: tuple[str, str, str, int]) -> tuple[str, str]:
    """(eid, env_json, out_dir, m) → 심사 + 패널 파일. 반려는 사유 문자열로 돌려준다.

    m = 이 셀에서 동시에 검정하는 간선 수. Bonferroni 임계가 패널 텍스트에 실린다.
    """
    import json as _json
    from pathlib import Path

    from .hypothesize import screen_tuples
    from .judge import panel_text
    from .paneltest import FEATURES
    eid, env_s, out_dir, m = item
    env = _json.loads(env_s)
    valid, rej = screen_tuples(env.get("hypotheses") or [],
                               event_types=env.get("event_types") or [],
                               series_families=env.get("series_families") or [],
                               measurable=list(FEATURES),
                               layer=env.get("layer") or "고유")
    if not valid:
        return eid, "REJ " + " | ".join(rej)
    (Path(out_dir) / f"env_{eid}.json").write_text(env_s, encoding="utf-8")
    txt = panel_text(_W["lake"], valid[0], _W["iid"], _W["day"],
                     layer=env.get("layer") or "고유", m_tests=m)
    (Path(out_dir) / f"panel_{eid}.txt").write_text(txt, encoding="utf-8")
    return eid, "ok"


def _cli() -> None:
    """하네스용 서브커맨드 - 결정론 조각을 낱개로 판다.

      facts    <ticker> <iid> <day>            셀 사실 + 대상 + 접지
      validate <envelope.json>                 {"event_types":[],"series_families":[],
                                                "hypotheses":[...]} → 심사 결과
      panel    <ticker> <iid> <day> <env.json> 튜플 1개의 패널 수치 (판정 없음)
      prep     <ticker> <iid> <day> <dir> <edges.json>  한 방: {"<ID>": envelope} 전체를
               심사 + env_/panel_ 파일로. 패널은 프로세스 풀(≤4)로 **내장 병렬**
      panels   <ticker> <iid> <day> <dir> [패턴]  env 글롭 → panel_*.txt (웜 레이크 일괄)
    """
    import json
    import pathlib

    from .hypothesize import screen_tuples
    from .judge import panel_text
    from .paneltest import FEATURES
    cmd = sys.argv[1]
    if cmd == "facts":
        g = gather(CausalLake(), *sys.argv[2:5])
        print(g["base"])
        print("\n=== 대상 (|기여| 순 ≤3) ===")
        for k, lb, pct in g["targets"]:
            print(f"  {k}\t{pct * 100:+.3f}%p\t{lb}")
        print("\n=== 접지 ===")
        print("event_types:", " · ".join(g["event_types"]) or "없음")
        print("fired:", " · ".join(g["fired"]) or "없음")
        return
    if cmd == "validate":
        from .hypothesize import screen_tuples
        env = json.loads(pathlib_read(sys.argv[2]))
        valid, rejected = screen_tuples(env.get("hypotheses") or [],
                                        event_types=env.get("event_types") or [],
                                        series_families=env.get("series_families") or [],
                                        measurable=list(FEATURES), layer=env.get("layer") or "고유")
        for t in valid:
            print(f"[OK] {t.channel} · {t.trigger.kind}:{t.trigger.ident} · "
                  f"노출 {t.exposure.ident}/{t.exposure.transform} · 부호{t.sign:+d} · "
                  f"의도: {t.intent}")
        for r in rejected:
            print(f"[REJ] {r}")
        return
    if cmd == "serve":
        # 웜 레이크 상주 서버 - 레이크 부팅(~1분)을 세션당 1회로 접는다.
        # stdin 한 줄 = JSON 명령. hub 프로세스로 띄워 hub send 로 부린다.
        #   {"op":"facts","ticker":..,"iid":..,"day":..,"dir":..}   → <dir>/facts.txt
        #   {"op":"prep","ticker":..,"iid":..,"day":..,"dir":..}    → <dir>/edges.json 읽어
        #                                                              env_/panel_ 생성
        import time as _t
        lake = CausalLake()
        print("READY", flush=True)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if line in ("quit", "exit"):
                break
            t0 = _t.time()
            try:
                q = json.loads(line)
                d = pathlib.Path(q["dir"]); d.mkdir(parents=True, exist_ok=True)
                if q["op"] == "facts":
                    g = gather(lake, q["ticker"], q["iid"], q["day"])
                    txt = [g["base"], "", "=== 대상 (|기여| 순 ≤3) ==="]
                    txt += [f"  {k}\t{pct * 100:+.3f}%p\t{lb}" for k, lb, pct in g["targets"]]
                    txt += ["", "=== 접지 ===",
                            "event_types: " + (" · ".join(g["event_types"]) or "없음"),
                            "fired: " + (" · ".join(g["fired"]) or "없음")]
                    (d / "facts.txt").write_text("\n".join(txt), encoding="utf-8")
                elif q["op"] == "prep":
                    edges = json.loads((d / "edges.json").read_text(encoding="utf-8"))
                    for eid, env in edges.items():
                        valid, rej = screen_tuples(
                            env.get("hypotheses") or [],
                            event_types=env.get("event_types") or [],
                            series_families=env.get("series_families") or [],
                            measurable=list(FEATURES), layer=env.get("layer") or "고유")
                        if not valid:
                            print(f"REJ {eid}: {' | '.join(rej)}", flush=True)
                            continue
                        (d / f"env_{eid}.json").write_text(
                            json.dumps(env, ensure_ascii=False), encoding="utf-8")
                        (d / f"panel_{eid}.txt").write_text(
                            panel_text(lake, valid[0], q["iid"], q["day"],
                                       layer=env.get("layer") or "고유",
                                       m_tests=len(edges)),
                            encoding="utf-8")
                print(f"DONE {q['op']} {_t.time() - t0:.0f}s", flush=True)
            except Exception as e:                          # noqa: BLE001 - 서버는 안 죽는다
                print(f"ERR {type(e).__name__}: {str(e)[:200]}", flush=True)
        return
    if cmd == "prep":
        # 한 방: edges.json({"<ID>": envelope}) → 심사 + env 분할 + 패널 병렬.
        # 셀마다 손으로 하던 validate 루프·env 쪼개기·bash 샤딩이 이 안에 접힌다.
        from concurrent.futures import ProcessPoolExecutor
        tkr, iid, day, d = sys.argv[2], sys.argv[3], sys.argv[4], pathlib.Path(sys.argv[5])
        edges = json.loads(pathlib_read(sys.argv[6]))
        d.mkdir(parents=True, exist_ok=True)
        items = [(eid, json.dumps(env, ensure_ascii=False), str(d), len(edges))
                 for eid, env in edges.items()]
        bad = 0
        with ProcessPoolExecutor(max_workers=min(4, len(items)),
                                 initializer=_prep_init,
                                 initargs=(tkr, iid, day)) as ex:
            for eid, msg in ex.map(_prep_one, items):
                print(f"{eid}: {msg}")
                bad += msg.startswith("REJ")
        raise SystemExit(1 if bad else 0)
    if cmd == "panels":
        # 웜 레이크 하나로 env_*.json 전부의 패널을 일괄 선계산한다.
        # 판정자마다 콜드 레이크 + edge_test 를 돌면 간선당 1~7분이 든다(실측) -
        # 첫 attach·프루닝이 지배 비용이라 한 프로세스로 접으면 간선당 수십 초가 된다.
        from .hypothesize import screen_tuples
        lake = CausalLake()
        tkr, iid, day, d = sys.argv[2], sys.argv[3], sys.argv[4], pathlib.Path(sys.argv[5])
        pat = sys.argv[6] if len(sys.argv) > 6 else "env_*.json"
        for f in sorted(d.glob(pat)):
            env = json.loads(f.read_text(encoding="utf-8"))
            valid, rej = screen_tuples(env.get("hypotheses") or [],
                                       event_types=env.get("event_types") or [],
                                       series_families=env.get("series_families") or [],
                                       measurable=list(FEATURES), layer=env.get("layer") or "고유")
            eid = f.stem.removeprefix("env_")
            if not valid:
                (d / f"panel_{eid}.txt").write_text("유효 튜플 없음:\n" + "\n".join(rej),
                                                    encoding="utf-8")
                continue
            (d / f"panel_{eid}.txt").write_text(
                panel_text(lake, valid[0], iid, day, layer=env.get("layer") or "고유"),
                encoding="utf-8")
            print(f"{eid}: ok")
        return
    if cmd == "panel":
        from .hypothesize import screen_tuples
        from .judge import panel_text
        env = json.loads(pathlib_read(sys.argv[5]))
        valid, rejected = screen_tuples(env.get("hypotheses") or [],
                                        event_types=env.get("event_types") or [],
                                        series_families=env.get("series_families") or [],
                                        measurable=list(FEATURES), layer=env.get("layer") or "고유")
        if not valid:
            raise SystemExit("유효 튜플 없음:\n" + "\n".join(rejected))
        print(panel_text(CausalLake(), valid[0], sys.argv[3], sys.argv[4],
                         layer=env.get("layer") or "고유"))
        return
    raise SystemExit(__doc__)


def pathlib_read(p: str) -> str:
    from pathlib import Path
    return Path(p).read_text(encoding="utf-8")


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("facts", "validate", "panel", "panels", "prep", "serve"):
        _cli()
        return
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
