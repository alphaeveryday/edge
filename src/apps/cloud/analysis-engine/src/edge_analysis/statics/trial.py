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
LEADS = (1, 2)         # 사전추세 위약: 처치 t−1·t−2 의 ATT 는 0 이어야 한다

_TRIAL_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e
    WHERE e.event_type_code = '{etype}' {role}
      AND e.trade_date < DATE '{day}'
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm,
           LAG(g.{y}, 1) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l1,
           LAG(g.{y}, 2) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l2{extra}
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
       ln(t.mcap) AS lc_t, ln(c.mcap) AS lc_c, t.bm AS bm_t, c.bm AS bm_c,
       t.y_l1 AS l1_t, c.y_l1 AS l1_c, t.y_l2 AS l2_t, c.y_l2 AS l2_c{sel}
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
    out = _summarize(rows, layer=layer, k=k, tag=f"직접 {etype.split('.')[-1]}")
    if col:
        # 조절: 짝 차이를 조건 클래스에 회귀. 표본을 쪼개지 않는다 (§14).
        diff = np.array([float(r[3]) - float(r[4]) for r in rows])
        dates = np.array([str(r[1]) for r in rows])
        cv = np.array([float(r[13]) for r in rows])
        hi = _pctile(cv) >= cond_pct if cond_cmp == ">=" else _pctile(cv) <= cond_pct
        d_obs, d_p = _moderate_diff(diff, hi, dates)
        sub = diff[hi]
        out.update(cond=cond_key, cond_n=int(hi.sum()),
                   att_cond=float(sub.mean()) if len(sub) >= 3 else None,
                   inter=d_obs, inter_p=d_p)
    return out


def _moderate_diff(diff: np.ndarray, c: np.ndarray,
                   dates: np.ndarray) -> tuple[float | None, float]:
    """짝 차이에서의 조절: diff = a + d·C.  CATE(C=1)=a+d, CATE(C=0)=a.

    `_cate_interaction` 을 그대로 쓰면 안 된다 - 짝 차이 프레임에서 D 는 **전부 1**
    이라 D×C 열이 D 와 같아져 식별이 퇴화한다(실측: '교호항 추정 불가' 가 항상 나왔다).
    처치 대비는 이미 차분에 들어 있으므로 조절항은 C 계수 하나다.

    귀무는 조건 라벨을 **날짜 층 안에서 순열**한다 - 날짜 효과를 고정하고 '누가
    조건을 충족했나' 만 무작위화한다.
    """
    cf = c.astype(float)
    if np.ptp(cf) == 0:
        return None, 1.0
    X = np.column_stack([np.ones(len(diff)), cf])

    def fit(cv: np.ndarray) -> float:
        beta, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(diff)), cv]),
                                   diff, rcond=None)
        return float(beta[1])

    obs = fit(cf)
    rng = np.random.default_rng(SEED)
    null = np.empty(PERMS)
    for k in range(PERMS):
        perm = cf.copy()
        for dd in np.unique(dates):
            m = dates == dd
            perm[m] = rng.permutation(perm[m])
        null[k] = fit(perm)
    _ = X
    return obs, _two_sided(float((null >= obs).mean()))


def say(r: dict) -> str:
    """시행 결과를 한 문단으로. 판정은 호출자가 세운다 - 여기서 의견을 내지 않는다."""
    if r.get("verdict") != "계산됨":
        return f"RCT 근사 판정불가 — {r.get('reason', '?')}"
    s = (f"RCT 근사(매칭 위약): 처치 {r['treated']}건 · 짝 {r['pairs']}개 · "
         f"{r['dates']}일 · ATT {r['att'] * 100:+.3f}%p (양측 p={r['p']:.3f}) · "
         f"처치 {r['y_t'] * 100:+.2f}% vs 대조 {r['y_c'] * 100:+.2f}% "
         f"[{r['layer']}층 · 업종 동일 · |Δln시총|≤{r['caliper'][0]} · "
         f"|Δβ|≤{r['caliper'][1]} · k≤{r['k']}]")
    if r.get("att_adj") is not None:
        s += (f"\n  편향보정 ATT {r['att_adj'] * 100:+.3f}%p (p={r['p_adj']:.3f}) "
              "— Δ공변량 0 에서의 절편 (캘리퍼 잔차 제거)")
    sm = " · ".join(f"{k} SMD {v:+.3f}" for k, v in r["smd"].items())
    s += (f"\n  균형: {sm} (상한 {SMD_MAX}) — "
          + ("통과" if r["balanced"] else "**실패: 이 ATT 는 처치효과가 아니라 매칭 잔차다**"))
    if r.get("lead"):
        bits = []
        for j, v in sorted(r["lead"].items()):
            bits.append(f"t−{j} 표본부족" if v is None else
                        f"t−{j} ATT {v[0] * 100:+.3f}%p (p={v[1]:.3f}, n={v[2]})")
        s += ("\n  사전추세 위약: " + " · ".join(bits) + " — "
              + ("0 과 구분 안 됨 = 통과" if r.get("pretrend_ok") else
                 "**유의: 처치 전에 이미 갈렸다 = 매칭 실패 또는 선견**"))
    if r.get("cond"):
        s += (f"\n  조절({r['cond']}): 충족 n={r['cond_n']} ATT "
              + (f"{r['att_cond'] * 100:+.3f}%p" if r.get("att_cond") is not None else "미계산")
              + (f" · 교호항 {r['inter'] * 100:+.3f}%p (p={r['inter_p']:.3f})"
                 if r.get("inter") is not None else " · 교호항 추정 불가"))
    return s


def _selfcheck() -> None:
    """대수가 표현할 수 있는 것과 없는 것을 명시적으로 남긴다.

    이 목록이 곧 '샌드박스가 필요해지는 경계' 다. 넷 다 프로젝트 규약상 **사람의
    스키마 변경** 사안이지 에이전트가 즉석에서 만들 것이 아니다.
    """
    assert CAL_LOGCAP > 0 and CAL_BETA > 0 and MATCH_K >= 1
    assert MIN_PAIRS >= MIN_N // 2
    assert LEADS == (1, 2)
    unsupported = ("IV(도구변수)", "RDD(회귀단절)", "합성통제", "성향점수(로짓) 매칭")
    # 구현한 것: 사전추세 위약(리드) · 다중 처치 동시 투입 · 균형(SMD) · CATE 교호항
    assert len(unsupported) == 4
    print("ok · 미지원(샌드박스 경계):", " · ".join(unsupported))

_MULTI_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d, e.event_type_code AS et
    FROM v_event e
    WHERE e.event_type_code IN ({types}) AND e.trade_date < DATE '{day}'
)
, ind AS (
    SELECT iid, d{flags} FROM ev GROUP BY iid, d
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm
    FROM g
    WHERE g.sector_code IS NOT NULL AND g.mcap_pit > 0 AND g.beta_m IS NOT NULL
      AND g.{y} IS NOT NULL AND g.trade_date < DATE '{day}'
)
, t AS (SELECT b.*{icols} FROM b JOIN ind ON ind.iid = b.iid AND ind.d = b.d)
, c AS (SELECT b.* FROM b LEFT JOIN ind ON ind.iid = b.iid AND ind.d = b.d
        WHERE ind.iid IS NULL)
, m AS (
    SELECT t.iid AS tid, t.d AS d, c.iid AS cid,
           row_number() OVER (PARTITION BY t.iid, t.d
               ORDER BY abs(ln(t.mcap) - ln(c.mcap)) / {cal_m}
                      + abs(t.bm - c.bm) / {cal_b}) AS rn
    FROM t JOIN c ON c.d = t.d AND c.sec = t.sec
    WHERE abs(ln(t.mcap) - ln(c.mcap)) <= {cal_m} AND abs(t.bm - c.bm) <= {cal_b}
)
SELECT m.tid, CAST(m.d AS VARCHAR) AS d, m.cid, t.y - c.y AS diff{tcols}
FROM m JOIN t ON t.iid = m.tid AND t.d = m.d
       JOIN c ON c.iid = m.cid AND c.d = m.d
WHERE m.rn <= {k}
"""


def run_multi(lake, day: str, etypes: list[str], *, layer: str = "고유",
              k: int = MATCH_K) -> dict:
    """**다중 처치 동시 투입.** 같은 날 여러 사건이 있으면 하나씩 재면 서로 흡수한다.

        diff_i = Σ_k b_k · D_ik + ε          (짝 차이에 처치 지시자들을 회귀)

    실측 000660 07-29 에 실적·목표주가·서킷브레이커가 다 있었다. 하나씩 재면 각
    ATT 가 나머지 둘의 효과를 흡수한다 - 그러면 '왜 **이** 이벤트' 에 답할 수 없다.
    대조군은 **어느 처치도 안 받은** 종목이다.

    귀무는 짝 부호 순열 (날짜 층화 자동 - 짝이 같은 날 안에서만 만들어졌다).
    """
    y = LAYER_Y.get(layer)
    if y is None:
        return {"verdict": "판정불가", "reason": f"층은 {sorted(LAYER_Y)} 중 하나다"}
    if not 2 <= len(etypes) <= 6:
        return {"verdict": "판정불가", "reason": "다중 처치는 2~6개다"}
    flags = "".join(
        f", max(CASE WHEN et = '{e}' THEN 1 ELSE 0 END) AS d{i}"
        for i, e in enumerate(etypes))
    sql = (_base(day) + _MULTI_SQL).format(
        types=", ".join(f"'{e}'" for e in etypes), day=day, y=y, k=k,
        cal_m=CAL_LOGCAP, cal_b=CAL_BETA, flags=flags,
        icols="".join(f", ind.d{i}" for i in range(len(etypes))),
        tcols="".join(f", t.d{i}" for i in range(len(etypes))))
    try:
        rows = _panel_rows(lake, sql, strict=False)
    except Exception as e:                          # noqa: BLE001
        return {"verdict": "판정불가", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = [r for r in rows if r[3] is not None]
    if len(rows) < MIN_PAIRS:
        return {"verdict": "판정불가", "reason": f"매칭 짝 {len(rows)} < {MIN_PAIRS}"}
    diff = np.array([float(r[3]) for r in rows])
    D = np.array([[float(r[4 + i]) for i in range(len(etypes))] for r in rows])
    if (D.sum(axis=0) < 3).any():
        thin = [etypes[i] for i in range(len(etypes)) if D[:, i].sum() < 3]
        return {"verdict": "판정불가", "reason": f"처치 표본 3건 미만: {thin}"}

    def fit(v: np.ndarray) -> np.ndarray:
        beta, *_ = np.linalg.lstsq(D, v, rcond=None)
        return beta

    obs = fit(diff)
    rng = np.random.default_rng(SEED)
    null = np.empty((PERMS, len(etypes)))
    for i in range(PERMS):
        null[i] = fit(diff * rng.choice([-1.0, 1.0], size=len(diff)))
    ps = [_two_sided(float((null[:, i] >= obs[i]).mean())) for i in range(len(etypes))]
    # 단독 투입과 비교하면 흡수량이 보인다.
    solo = {}
    for e in etypes:
        r1 = run_trial(lake, day, etype=e, layer=layer, k=k)
        solo[e] = (r1["att"], r1["p"]) if r1.get("verdict") == "계산됨" else None
    return {"verdict": "계산됨", "layer": layer, "pairs": len(rows),
            "etypes": etypes, "att": [float(v) for v in obs], "p": ps,
            "n_treat": [int(v) for v in D.sum(axis=0)], "solo": solo}


def say_multi(r: dict) -> str:
    if r.get("verdict") != "계산됨":
        return f"다중 처치 판정불가 — {r.get('reason', '?')}"
    out = [f"다중 처치 동시 투입 ({r['layer']}층 · 짝 {r['pairs']}개) — 대조군은 "
           "어느 처치도 안 받은 종목"]
    for e, a, p, n in zip(r["etypes"], r["att"], r["p"], r["n_treat"]):
        s = r["solo"].get(e)
        solo = (f" · 단독 {s[0] * 100:+.3f}%p (p={s[1]:.3f})" if s else " · 단독 판정불가")
        absorb = ("" if not s else
                  f" · 흡수 {abs(s[0] - a) * 100:+.3f}%p" if abs(s[0] - a) > 1e-9 else "")
        out.append(f"  {e.split('.')[-1]:<22} n={n:<5} ATT {a * 100:+.3f}%p "
                   f"(p={p:.3f}){solo}{absorb}")
    return "\n".join(out)


if __name__ == "__main__":
    _selfcheck()


_SPILL_SQL = """
, ev AS (
    SELECT DISTINCT e.instrument_id AS iid, e.trade_date AS d
    FROM v_event e
    WHERE e.event_type_code = '{etype}' AND e.trade_date < DATE '{day}'
)
, sp AS (
    -- **관계 처치**: 상대에게 사건이 났고 나에게는 안 났다. 그게 전이의 정의다.
    SELECT DISTINCT l.dst AS iid, ev.d AS d
    FROM v_link l JOIN ev ON ev.iid = l.src AND l.link_date <= ev.d
    WHERE l.link_type = '{rel}'
    UNION
    SELECT DISTINCT l.src AS iid, ev.d AS d
    FROM v_link l JOIN ev ON ev.iid = l.dst AND l.link_date <= ev.d
    WHERE l.link_type = '{rel}'
)
, b AS (
    SELECT g.instrument_id AS iid, g.trade_date AS d, g.{y} AS y,
           g.sector_code AS sec, g.mcap_pit AS mcap, g.beta_m AS bm,
           LAG(g.{y}, 1) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l1,
           LAG(g.{y}, 2) OVER (PARTITION BY g.instrument_id ORDER BY g.trade_date) AS y_l2
    FROM g
    WHERE g.sector_code IS NOT NULL AND g.mcap_pit > 0 AND g.beta_m IS NOT NULL
      AND g.{y} IS NOT NULL AND g.trade_date < DATE '{day}'
)
, t AS (
    SELECT b.* FROM b JOIN sp ON sp.iid = b.iid AND sp.d = b.d
    LEFT JOIN ev ON ev.iid = b.iid AND ev.d = b.d
    WHERE ev.iid IS NULL          -- 자기에게 사건이 났으면 전이가 아니라 직접이다
)
, c AS (
    SELECT b.* FROM b
    LEFT JOIN sp ON sp.iid = b.iid AND sp.d = b.d
    LEFT JOIN ev ON ev.iid = b.iid AND ev.d = b.d
    WHERE sp.iid IS NULL AND ev.iid IS NULL
)
, m AS (
    SELECT t.iid AS tid, t.d AS d, c.iid AS cid,
           row_number() OVER (PARTITION BY t.iid, t.d
               ORDER BY abs(ln(t.mcap) - ln(c.mcap)) / {cal_m}
                      + abs(t.bm - c.bm) / {cal_b}) AS rn
    FROM t JOIN c ON c.d = t.d AND c.sec = t.sec
    WHERE abs(ln(t.mcap) - ln(c.mcap)) <= {cal_m} AND abs(t.bm - c.bm) <= {cal_b}
)
SELECT m.tid, CAST(m.d AS VARCHAR) AS d, m.cid, t.y AS y_t, c.y AS y_c,
       ln(t.mcap) AS lc_t, ln(c.mcap) AS lc_c, t.bm AS bm_t, c.bm AS bm_c,
       t.y_l1 AS l1_t, c.y_l1 AS l1_c, t.y_l2 AS l2_t, c.y_l2 AS l2_c
FROM m JOIN t ON t.iid = m.tid AND t.d = m.d
       JOIN c ON c.iid = m.cid AND c.d = m.d
WHERE m.rn <= {k}
"""


def run_spillover(lake, day: str, *, etype: str, rel: str,
                  layer: str = "고유", k: int = MATCH_K) -> dict:
    """**관계 처치(전이)**: 상대에게 사건이 났고 나에겐 안 났다.

    '왜 **이** 종목인가' 에 답하는 유일한 경로다. 층 분해도 매칭도 종목 개별성을
    지우는 것이 목적이라 그 질문에 답할 수 없다 - 개별성은 (a) 조절자, (b) 관계
    두 곳에서만 온다.

    이전 `_relation_test` 는 `assignable=False` 였다 (소스-타깃 5분봉 창 정렬 부재).
    시행 대수는 **일 단위 ATT** 라 창 정렬이 필요 없다 - 하루의 몫을 쪼개는 것이
    아니라 '전이가 있었나' 를 묻기 때문이다. 그래서 배정 제약이 풀린다.

    처치군에서 **자기 사건은 제외**한다. 자기에게 났으면 전이가 아니라 직접이다.
    """
    from .vocab import RELATIONS
    if rel not in RELATIONS:
        return {"verdict": "판정불가", "reason": f"관계는 {sorted(RELATIONS)} 중 하나다"}
    y = LAYER_Y.get(layer)
    if y is None:
        return {"verdict": "판정불가", "reason": f"층은 {sorted(LAYER_Y)} 중 하나다"}
    sql = (_base(day) + _SPILL_SQL).format(
        etype=etype, rel=rel, day=day, y=y, k=k, cal_m=CAL_LOGCAP, cal_b=CAL_BETA)
    try:
        rows = _panel_rows(lake, sql, strict=False)
    except Exception as e:                          # noqa: BLE001
        return {"verdict": "판정불가", "reason": f"{type(e).__name__}: {str(e)[:120]}"}
    rows = [r for r in rows if r[3] is not None and r[4] is not None]
    if len(rows) < MIN_PAIRS:
        return {"verdict": "판정불가",
                "reason": f"전이 짝 {len(rows)} < {MIN_PAIRS} — {rel} 링크가 얇다"}
    return _summarize(rows, layer=layer, k=k, tag=f"전이 {rel}")


def _summarize(rows: list, *, layer: str, k: int, tag: str = "") -> dict:
    """짝 목록 → ATT · 균형(SMD) · 사전추세 위약. `run_trial`/`run_spillover` 공용.

    컬럼 규약: (tid, d, cid, y_t, y_c, lc_t, lc_c, bm_t, bm_c, l1_t, l1_c, l2_t, l2_c[, cval])
    한 곳에서만 조립한다 - 두 곳이면 진단이 갈린다.
    """
    dates = np.array([str(r[1]) for r in rows])
    yt = np.array([float(r[3]) for r in rows])
    yc = np.array([float(r[4]) for r in rows])
    diff = yt - yc
    att = float(diff.mean())
    rng = np.random.default_rng(SEED)
    null = np.array([float((diff * rng.choice([-1.0, 1.0], size=len(diff))).mean())
                     for _ in range(PERMS)])
    p = _two_sided(float((null >= att).mean()))
    smd = {}
    for nm, a, b in (("ln시총", 5, 6), ("β_m", 7, 8)):
        av = np.array([float(r[a]) for r in rows])
        bv = np.array([float(r[b]) for r in rows])
        sd = float(np.sqrt((np.var(av, ddof=1) + np.var(bv, ddof=1)) / 2))
        smd[nm] = float((av.mean() - bv.mean()) / sd) if sd > 0 else 0.0
    # **편향 보정 (Abadie-Imbens).** 캘리퍼 안이면 통과시키므로 공변량 잔차가 남고,
    # 균형 실패(SMD>상한)가 실측에서 반복됐다(전이 셋 다 β_m SMD 0.125~0.151).
    # 캘리퍼를 셀별로 좁히는 것은 **대조군을 고르는 것**이라 금지 - 대신 짝 차이를
    # 공변량 차이에 회귀해 잔차 편향을 뺀다. ATT_adj = Δ공변량 0 에서의 절편.
    dlc = np.array([float(r[5]) - float(r[6]) for r in rows])
    dbm = np.array([float(r[7]) - float(r[8]) for r in rows])
    Xa = np.column_stack([np.ones(len(rows)), dlc, dbm])

    def _adj(v: np.ndarray) -> float:
        beta, *_ = np.linalg.lstsq(Xa, v, rcond=None)
        return float(beta[0])

    att_adj = _adj(diff)
    rg2 = np.random.default_rng(SEED)
    nadj = np.array([_adj(diff * rg2.choice([-1.0, 1.0], size=len(diff)))
                     for _ in range(PERMS)])
    p_adj = _two_sided(float((nadj >= att_adj).mean()))
    lead = {}
    for j, (a, b) in ((1, (9, 10)), (2, (11, 12))):
        pr = [(float(r[a]), float(r[b])) for r in rows
              if r[a] is not None and r[b] is not None]
        if len(pr) < MIN_PAIRS:
            lead[j] = None
            continue
        dl = np.array([x[0] - x[1] for x in pr])
        rg = np.random.default_rng(SEED)
        nl = np.array([float((dl * rg.choice([-1.0, 1.0], size=len(dl))).mean())
                       for _ in range(PERMS)])
        lead[j] = (float(dl.mean()), _two_sided(float((nl >= dl.mean()).mean())), len(dl))
    return {"verdict": "계산됨", "att": att, "p": p, "pairs": len(rows),
            "treated": len({(r[0], r[1]) for r in rows}), "dates": len(set(dates)),
            "y_t": float(yt.mean()), "y_c": float(yc.mean()),
            "caliper": (CAL_LOGCAP, CAL_BETA), "k": k, "layer": layer, "tag": tag,
            "smd": smd, "balanced": all(abs(v) <= SMD_MAX for v in smd.values()),
            "att_adj": att_adj, "p_adj": p_adj, "lead": lead,
            "pretrend_ok": all(v is None or v[1] >= ALPHA for v in lead.values())}
