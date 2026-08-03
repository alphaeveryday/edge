"""RCT 근사 시행(Trial) — **닫힌 슬롯 조합이 통계 검정으로 컴파일된다.**

## 왜 이 실험을 하는가

지금 가설의 의미가 너무 넓다. `edge_test` 가 재는 것은
    "그 사건이 난 날들 중, 노출 상위 20% 가 다르게 반응했나"
이고, 이건 **처치가 무작위 배정된 비교가 아니다** - 관측 특성으로 나눈 군이다.
위약군(같은 사건일의 노출 하위)도 노출이 내생이면 같이 오염된다.

RCT 근사는 처치를 **사건 발생 자체**로 두고, 대조군을 같은 날·같은 업종·유사
시총·유사 β 인데 **그 사건이 안 난** 종목으로 매칭한다.

## 두 설계 중 어느 쪽인가 (이 모듈이 실험의 B안이다)

  A안 샌드박스   에이전트가 SQL/파이썬을 써서 실행
  B안 연산 대수  닫힌 슬롯 조합 -> 코드가 검정으로 컴파일   <- 이 모듈

B안의 주장: RCT 근사에 필요한 자유도는 **6개 슬롯**으로 닫힌다.

    처치   사건 타입 + 역할            (닫힌 어휘: 사건타입 53 · 역할)
    대조   매칭 축 + 캘리퍼            (닫힌 어휘: 업종·시총·β)
    시점   PIT 클램프                  (전역 규약 - 선택 여지 없음)
    결과   층 y                        (닫힌 어휘: 시장·섹터·고유·되돌림)
    조절   조건 술어                   (닫힌 어휘: 계열족 × 변환 × 비교자)
    추정   ATT | CATE                  (닫힌 어휘)

이 여섯이 닫히면 샌드박스가 필요 없다. 필요해지는 순간은 **새 추정량**(IV·RDD·
합성통제)이고, 그건 프로젝트 규약상 사람의 스키마 변경이다 - 에이전트가 즉석에서
만들 것이 아니다.
"""
from __future__ import annotations

import numpy as np

from .paneltest import (ALPHA, MIN_N, PERMS, SEED, _base, _cate_interaction,
                        _panel_rows, _pctile, _two_sided)
from .paneltest import FEATURES, LAYER_Y

# 매칭 캘리퍼 — 전역 상수. 셀별 조정 금지 (조정하면 대조군을 고르는 셈이다).
CAL_LOGCAP = 0.75      # |Δln(시총)| 상한 ≈ 2.1배
CAL_BETA = 0.40        # |Δβ_m| 상한
MATCH_K = 3            # 처치 1건당 대조 최대 수
MIN_PAIRS = 20         # 짝이 이보다 적으면 판정불가
SMD_MAX = 0.10         # 매칭 후 표준화 평균차 상한 (RCT 관례). 넘으면 균형 실패
PLACEBO_LAYER = "시장"  # 위약 결과 - 종목 사건이 만들 수 없는 층

_TRIAL_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e
    WHERE e.event_type_code = '{etype}' {role}
      AND e.trade_date < DATE '{day}'
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm{extra}
    FROM g
    WHERE g.sector_code IS NOT NULL AND g.mcap_pit > 0 AND g.beta_m IS NOT NULL
      AND g.{y} IS NOT NULL AND g.trade_date < DATE '{day}'
)
, t AS (SELECT b.* FROM b JOIN ev ON ev.iid = b.iid AND ev.d = b.d)
, c AS (SELECT b.* FROM b LEFT JOIN ev ON ev.iid = b.iid AND ev.d = b.d
        WHERE ev.iid IS NULL)
, m AS (
    SELECT t.iid AS tid, t.d AS d, c.iid AS cid,
           row_number() OVER (PARTITION BY t.iid, t.d
               ORDER BY abs(ln(t.mcap) - ln(c.mcap)) / {cal_m}
                      + abs(t.bm - c.bm) / {cal_b}) AS rn
    FROM t JOIN c ON c.d = t.d AND c.sec = t.sec
    WHERE abs(ln(t.mcap) - ln(c.mcap)) <= {cal_m} AND abs(t.bm - c.bm) <= {cal_b}
)
SELECT m.tid, CAST(m.d AS VARCHAR) AS d, m.cid,
       t.y AS y_t, c.y AS y_c,
       ln(t.mcap) AS lc_t, ln(c.mcap) AS lc_c, t.bm AS bm_t, c.bm AS bm_c{sel}
FROM m JOIN t ON t.iid = m.tid AND t.d = m.d
       JOIN c ON c.iid = m.cid AND c.d = m.d
WHERE m.rn <= {k}
"""


def run_trial(lake, day: str, *, etype: str, layer: str = "고유",
              role: str = "", cond_key: str | None = None,
              cond_pct: float = 0.8, cond_cmp: str = ">=",
              k: int = MATCH_K) -> dict:
    """사건 = 처치, 매칭 대조군 = 위약. ATT 와 (조건이 있으면) CATE 교호항.

    반환 슬롯은 판정을 담지 않는다 - 게이트는 호출자가 `edge_gate` 로 세운다.
    (판정 자리를 여기 두면 '수치는 코드가, 판정은 코드가' 가 두 곳이 된다.)
    """
    y = LAYER_Y.get(layer)
    if y is None:
        return {"verdict": "판정불가", "reason": f"층은 {sorted(LAYER_Y)} 중 하나다"}
    col = None
    if cond_key is not None:
        col = FEATURES.get(tuple(cond_key.split("/")))
        if col is None:
            return {"verdict": "판정불가",
                    "reason": f"조건 {cond_key!r} 은 못 잰다 - 재는 것: "
                              f"{sorted('/'.join(k) for k in FEATURES)}"}
    sql = (_base(day) + _TRIAL_SQL).format(
        etype=etype, day=day, y=y, k=k,
        role=f"AND e.role_code = '{role}'" if role else "",
        cal_m=CAL_LOGCAP, cal_b=CAL_BETA,
        extra=f", g.{col} AS cval" if col else "",
        sel=", t.cval AS c_t" if col else "")
    try:
        rows = _panel_rows(lake, sql, strict=False)
    except Exception as e:                          # noqa: BLE001 - 부재는 사유와 함께
        return {"verdict": "판정불가", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = [r for r in rows if r[3] is not None and r[4] is not None]
    if len(rows) < MIN_PAIRS:
        return {"verdict": "판정불가",
                "reason": f"매칭 짝 {len(rows)} < {MIN_PAIRS} - 캘리퍼 안에 대조군이 없다"
                          f" (업종 동일 · |Δln시총|≤{CAL_LOGCAP} · |Δβ|≤{CAL_BETA})"}
    dates = np.array([str(r[1]) for r in rows])
    yt = np.array([float(r[3]) for r in rows])
    yc = np.array([float(r[4]) for r in rows])
    diff = yt - yc
    att = float(diff.mean())
    # 귀무: 짝 안에서 처치/대조 라벨을 뒤집는다 (부호 순열). 날짜 층화는 자동 -
    # 짝이 같은 날 안에서만 만들어졌기 때문이다.
    rng = np.random.default_rng(SEED)
    null = np.empty(PERMS)
    for i in range(PERMS):
        null[i] = float((diff * rng.choice([-1.0, 1.0], size=len(diff))).mean())
    p = _two_sided(float((null >= att).mean()))
    n_t = len({(r[0], r[1]) for r in rows})
    # **균형 검정 (RCT 의 필수 절차).** 매칭했다고 균형이 잡힌 게 아니다 - 캘리퍼
    # 안이면 통과시키므로 잔차가 남는다. 실측(실적·07-29): β 캘리퍼 0.40 이 느슨해서
    # **시장층 ATT +0.410%p (p=0.016)** 이 나왔다 - 실적 발표는 시장 요인 수익을
    # 만들 수 없으므로 그건 처치효과가 아니라 β 매칭 잔차다. 짝을 같은 날 안에서
    # 만들었으니 시장 수익은 동일하고, 차이는 전부 Δβ × 시장수익에서 온다.
    lc_t = np.array([float(r[5]) for r in rows])
    lc_c = np.array([float(r[6]) for r in rows])
    bm_t = np.array([float(r[7]) for r in rows])
    bm_c = np.array([float(r[8]) for r in rows])
    smd = {}
    for nm, a, b in (("ln시총", lc_t, lc_c), ("β_m", bm_t, bm_c)):
        sd = float(np.sqrt((np.var(a, ddof=1) + np.var(b, ddof=1)) / 2))
        smd[nm] = float((a.mean() - b.mean()) / sd) if sd > 0 else 0.0
    balanced = all(abs(v) <= SMD_MAX for v in smd.values())
    out = {"verdict": "계산됨", "att": att, "p": p, "pairs": len(rows),
           "treated": n_t, "dates": len(set(dates)),
           "y_t": float(yt.mean()), "y_c": float(yc.mean()),
           "caliper": (CAL_LOGCAP, CAL_BETA), "k": k, "layer": layer,
           "smd": smd, "balanced": balanced}
    # 위약 결과: 같은 처치·같은 짝으로 **시장층**을 재본다. 종목 사건은 시장 요인
    # 수익을 만들 수 없으니 0 이어야 한다. 유의하면 매칭이 실패한 것이다.
    if layer != PLACEBO_LAYER:
        pl = run_trial(lake, day, etype=etype, layer=PLACEBO_LAYER, role=role, k=k)
        if pl.get("verdict") == "계산됨":
            out["placebo"] = (pl["att"], pl["p"])
    if col:
        cv = np.array([float(r[9]) for r in rows])
        hi = _pctile(cv) >= cond_pct if cond_cmp == ">=" else _pctile(cv) <= cond_pct
        # 처치효과의 조절: 짝 차이를 조건 클래스에 회귀 (표본 분할 없음)
        d_obs, d_p = _cate_interaction(diff, np.ones(len(diff), dtype=bool), hi, dates)
        sub = diff[hi]
        out.update(cond=cond_key, cond_n=int(hi.sum()),
                   att_cond=float(sub.mean()) if len(sub) >= 3 else None,
                   inter=d_obs, inter_p=d_p)
    return out


def say(r: dict) -> str:
    """시행 결과를 한 문단으로. 판정은 호출자가 세운다 - 여기서 의견을 내지 않는다."""
    if r.get("verdict") != "계산됨":
        return f"RCT 근사 판정불가 — {r.get('reason', '?')}"
    s = (f"RCT 근사(매칭 위약): 처치 {r['treated']}건 · 짝 {r['pairs']}개 · "
         f"{r['dates']}일 · ATT {r['att'] * 100:+.3f}%p (양측 p={r['p']:.3f}) · "
         f"처치 {r['y_t'] * 100:+.2f}% vs 대조 {r['y_c'] * 100:+.2f}% "
         f"[{r['layer']}층 · 업종 동일 · |Δln시총|≤{r['caliper'][0]} · "
         f"|Δβ|≤{r['caliper'][1]} · k≤{r['k']}]")
    sm = " · ".join(f"{k} SMD {v:+.3f}" for k, v in r["smd"].items())
    s += (f"\n  균형: {sm} (상한 {SMD_MAX}) — "
          + ("통과" if r["balanced"] else "**실패: 이 ATT 는 처치효과가 아니라 매칭 잔차다**"))
    if r.get("placebo") is not None:
        pa, pp = r["placebo"]
        s += (f"\n  위약({PLACEBO_LAYER}층): ATT {pa * 100:+.3f}%p (p={pp:.3f}) — "
              + ("0 과 구분 안 됨 = 통과" if pp >= ALPHA else
                 "**유의: 종목 사건이 시장 요인을 만들 수 없으므로 매칭 실패다**"))
    if r.get("cond"):
        s += (f"\n  조절({r['cond']}): 충족 n={r['cond_n']} ATT "
              + (f"{r['att_cond'] * 100:+.3f}%p" if r.get("att_cond") is not None else "미계산")
              + (f" · 교호항 {r['inter'] * 100:+.3f}%p (p={r['inter_p']:.3f})"
                 if r.get("inter") is not None else " · 교호항 추정 불가"))
    return s


def _selfcheck() -> None:
    """대수가 표현할 수 있는 것과 없는 것을 명시적으로 남긴다."""
    assert CAL_LOGCAP > 0 and CAL_BETA > 0 and MATCH_K >= 1
    assert MIN_PAIRS >= MIN_N // 2
    # 못 표현하는 것 (샌드박스가 필요해지는 경계):
    unsupported = ("IV(도구변수)", "RDD(회귀단절)", "합성통제", "리드-래그 이벤트스터디",
                   "성향점수(로짓) 매칭", "다중 처치 동시 투입")
    assert len(unsupported) == 6
    print("ok · 미지원:", " · ".join(unsupported))


if __name__ == "__main__":
    _selfcheck()
