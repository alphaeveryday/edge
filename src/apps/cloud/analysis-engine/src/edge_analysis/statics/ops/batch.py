"""셀 배치 — 표현력 환원율의 **분포**를 낸다.

20R 까지 모든 라이브가 셀 하나짜리였다. "환원율 0% · 방아쇠 5/5 막힘"은 삼성전자
7월 30일 **하루**에서 나온 값이고, 그날은 시장이 -1.09% 빠진 날이라 뉴스 방아쇠가
안 잡히는 게 당연했을 수도 있다. 일화(anecdote)지 측정(measurement)이 아니었다.

배치가 답하는 것:
  · 환원율이 셀마다 얼마나 흔들리나 (평균 하나로 말할 수 있는 값인가)
  · 막힘 슬롯 **순위**가 안정적인가 (방아쇠가 진짜 1위인가, 그날만 그랬나)
  · 필요했던 개념이 **몇 %의 셀에서 반복**되나 → 어휘 확장 우선순위
  · 측정기 건강 지표(허위사상·쏠림)가 셀을 넘어 일정한가

셀 선정은 **결정론**이다 - 모델이 셀을 고르면 그게 표본 선택이다(§17). ETF 구성
종목 × 정렬된 날짜 전수를 돌리고, 못 도는 셀은 사유와 함께 건너뛴다.

사용:  python -m edge_analysis.statics.ops.batch <etf_id> <d0> <d1> [종목수] [가설수]
       예: python -m edge_analysis.statics.ops.batch 091160 2026-07-27 2026-07-31 8 3
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from .expressive import SLOTS, Survey, append_ledger, survey_cell
from ..core.registry import roadmap


def cells(lake, etf_id: str, d0: str, d1: str, top: int = 8) -> list[tuple[str, str, str]]:
    """(ticker, instrument_id, day) 전수. **비중 상위 순** - 셀을 모델이 못 고른다.

    봉이 없는 셀은 애초에 제외한다(시간 분해가 안 되면 셀이 성립하지 않는다).
    사건 유무로는 거르지 않는다 - **사건 없는 셀이야말로** 분석가가 시장·수급으로
    가설을 내는 곳이고, 어휘 구멍이 거기서 드러난다.
    """
    from ..core.paneltest import _base
    # `_base` 가 이미 WITH 체인을 연다 - 두 번째 WITH 를 붙이면 파서가 죽는다.
    rows = lake.sql(_base(d1) + f"""
        , h AS (
            SELECT constituent_ticker AS tk, weight_pct
            FROM s3_etf_holdings
            WHERE etf_id = '{etf_id}' AND market = 'KR'
              AND as_of_date = (SELECT max(as_of_date) FROM s3_etf_holdings
                                WHERE etf_id = '{etf_id}')
              AND constituent_ticker ~ '^[0-9]{{6}}$'
            ORDER BY weight_pct DESC LIMIT {top}
        )
        SELECT h.tk, i.instrument_id, b.trade_date, h.weight_pct, b.symbol
        FROM h
        JOIN v_instrument i ON i.ticker = h.tk
        JOIN (SELECT DISTINCT ticker, symbol, trade_date FROM bars_5m
              WHERE trade_date BETWEEN DATE '{d0}' AND DATE '{d1}') b
          ON b.ticker = h.tk
        ORDER BY h.weight_pct DESC, b.trade_date""")
    # 봉 조회 심볼은 시장별 접미사가 붙는다 - 어느 쪽인지 봉에서 직접 읽는다
    # (KOSPI .KS · KOSDAQ .KQ. 상수로 박으면 코스닥 종목이 통째로 빠진다 - 실측).
    sym = {r[0]: r[4] for r in rows}
    return [(sym[r[0]], r[1], str(r[2])) for r in rows]


def run(lake, ask, etf_id: str, d0: str, d1: str, top: int = 8, n: int = 3,
        root: str = ".tmp/causal-backfill") -> list[Survey]:
    """전 셀 조사. 한 셀이 죽어도 나머지는 돈다 - 배치의 요점은 분포다."""
    out: list[Survey] = []
    todo = cells(lake, etf_id, d0, d1, top)
    print(f"셀 {len(todo)}개 — {etf_id} 상위 {top}종목 × {d0}~{d1}", file=sys.stderr)
    for i, (tk, iid, day) in enumerate(todo, 1):
        t0 = time.monotonic()
        try:
            sv = survey_cell(lake, ask, tk, iid, day, n=n)   # tk 에 접미사가 이미 붙어 있다
        except Exception as e:                      # noqa: BLE001 - 셀 실패는 기록하고 계속
            print(f"  [{i}/{len(todo)}] {tk} {day} 실패: {type(e).__name__}: "
                  f"{str(e)[:80]}", file=sys.stderr)
            continue
        append_ledger(root, sv)
        out.append(sv)
        g = Counter(r.grade for r in sv.items)
        print(f"  [{i}/{len(todo)}] {tk} {day} 가설{len(sv.items)} "
              f"{dict(g)} {time.monotonic() - t0:.0f}s", file=sys.stderr)
    return out


def report(surveys: list[Survey], root: str = "") -> str:
    """분포 보고. **평균 하나로 접지 않는다** - 흔들리면 흔들린다고 말해야 한다."""
    items = [r for s in surveys for r in s.items]
    if not items:
        return "가설 0개 - 배치 실패"
    n, nc = len(items), len(surveys)
    gr = Counter(r.grade for r in items)
    ok = gr["완전"] + gr["대리"]
    per = [100 * sum(1 for r in s.items if r.grade in ("완전", "대리")) / len(s.items)
           for s in surveys if s.items]
    per.sort()
    blocked = Counter(s for r in items for s in r.blocked)
    cells_blocked = Counter(s for sv in surveys
                            for s in {x for r in sv.items for x in r.blocked})
    out = [f"셀 {nc} · 가설 {n} — 환원율 분포",
           "  등급: " + " · ".join(f"{k} {gr.get(k, 0)}"
                                 for k in ("완전", "대리", "부분", "불가")),
           f"  전체 환원율 {100 * ok / n:.0f}%  "
           f"셀별 중앙값 {per[len(per) // 2]:.0f}% · 최소 {per[0]:.0f}% · 최대 {per[-1]:.0f}%",
           f"  셀별 전량 실패 {sum(1 for p in per if p == 0)}/{nc} · "
           f"전량 성공 {sum(1 for p in per if p == 100)}/{nc}"]
    if blocked:
        out.append("  막힘 슬롯 (가설 기준 / **셀 기준** - 셀 기준이 재현성이다):")
        out += [f"    {s:<5} 가설 {c:>3}/{n}  ·  셀 {cells_blocked[s]:>2}/{nc}"
                for s, c in blocked.most_common()]
    bogus = sum(len(r.bogus) for r in items)
    if bogus:
        out.append(f"  ⚠ 채점자 허위사상 {bogus}건 / 슬롯 {n * len(SLOTS)} "
                   f"= {100 * bogus / (n * len(SLOTS)):.1f}% (측정기 건강)")
    want = Counter(v["want"][:60] for r in items for v in r.slots.values()
                   if v.get("grade") == "불가" and v.get("want")
                   and "허위사상" not in v.get("want", ""))
    if want:
        out.append("  반복된 '필요했던 개념' (셀을 넘어 반복될수록 어휘 확장 1순위):")
        out += [f"    ×{c}  {w}" for w, c in want.most_common(10)]
    # **판정불가 사유 = 데이터 수집 우선순위**(21R). 표현력 조사의 `blocked` 는 슬롯이
    # 비었는지를 세고, 이건 검정 본선이 실제로 무엇에 막혔는지를 센다 - 후자가 로드맵이다.
    if root:
        rm = roadmap(root)
        if rm:
            out.append("  데이터 요청 큐 (채우면 열리는 가설 수 / 걸린 셀 수):")
            out += [f"    +{r['unlocks']:>3} 가설 · 셀 {r['cells']:>2}  {r['need']}"
                    for r in rm]
        else:
            out.append("  데이터 요청 큐: 비어 있음 (막힌 판정이 기록되지 않았다)")
    return "\n".join(out)



if __name__ == "__main__":       # pragma: no cover
    import os

    from ...adapters.llm import DeepSeekClient
    from ..core.duck import CausalLake

    if len(sys.argv) < 4:
        sys.exit(__doc__)
    client = DeepSeekClient(os.environ["DEEPSEEK_API_KEY"],
                            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    root = os.environ.get("CAUSAL_BACKFILL_DIR", ".tmp/causal-backfill")
    svs = run(CausalLake(), client.complete_json, sys.argv[1], sys.argv[2], sys.argv[3],
              top=int(sys.argv[4]) if len(sys.argv) > 4 else 8,
              n=int(sys.argv[5]) if len(sys.argv) > 5 else 3, root=root)
    print(report(svs, root=root))
