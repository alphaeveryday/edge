"""검정 에이전트 — 튜플의 결정론 전개와 타입 수준 패널 게이트. LLM 없음.

설계 §17: 검정자는 고르지 않는다. 창·컷·표본·유의수준은 전역 상수(vocab)이고,
대상군·위약군은 튜플의 노출원에서 **유도**된다.

이 판이 구현하는 명시 항목 전부:
  점 방아쇠 (§6)      과거 그 타입 사건일 패널
  계열 방아쇠 (§6)    그 계열족 혁신의 |z|≥2 이상일 패널 (z 창 60d, 전역 상수)
  취약성 = INUS (§6)  술어로 패널을 **조건화**한다 - 취약성 미충족 표본에서의
                      용량-반응은 이 가설의 검정이 아니다. 오늘 셀의 충족 여부가
                      적용(applies_today)을 정한다: 성립해도 오늘 미충족이면 부적용
  반사실 쌍 (§14)     취약성 미충족 부류의 효과를 함께 낸다. 반대 사례 < 5 면
                      침묵(positivity) - 외삽 금지
  환원 검사 (§8)      오늘 같은 타입 사건의 횡단면이 패널과 같은 방향인가
  용량-반응 (§4)      노출 상위(≥컷) vs 하위, 층=사건일 순열 귀무

감사에서 배운 계약: PIT(과거 패널은 day 미만 - 오늘이 자기 패널에 못 들어간다) ·
결정론(SEED 고정 = 재실행 동일 판정) · 부재 선언(못 재는 조합 = 판정불가+사유) ·
선언=배선(이 도크스트링의 모든 항목이 아래 코드에 있다).

지금 잴 수 있는 피처는 price_daily(3.7년) 파생 4종이다. 관계 노출(전이 패널,
§16)은 다음 판이다 - 산업쌍 위약(같은 속성 ∧ 관계 없음) 설계가 필요하다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gates import EdgeVerdict, edge_gate
from .vocab import EXPOSURE_CUT, HypothesisTuple, MIN_N, Vulnerability

PERMS = 1000        # 전역 상수 - 가설별 지정 금지 (§13)
SEED = 0
Z_ANOM = 2.0        # 계열 방아쇠의 이상 임계 (혁신 z, 60d 창)
MIN_OPPOSITE = 5    # positivity: 반대(미충족) 사례가 이보다 적으면 반사실 침묵
MIN_TODAY = 5       # 환원 검사의 오늘 횡단면 최소 표본

# (계열족, 변환) → 피처 컬럼. **여기 없는 조합은 아직 못 잰다** (부재 선언).
FEATURES = {
    ("가격잔차", "누적"): "cum20",
    ("가격잔차", "변동성"): "vol20",
    ("거래량", "수준"): "tv20",
    ("거래량", "변화"): "tv_chg",
}
# 계열 방아쇠의 혁신값 (z 를 재는 대상)
_INNOVATION = {"가격잔차": "ar", "거래량": "tv_chg"}

# 공통 피처 CTE. 창은 전부 [t-20, t-1]·[t-60, t-1] - **당일 제외 = PIT**.
_BASE = """
WITH r AS (
    SELECT instrument_id, trade_date, log_return, volume,
           -- turnover_value 는 전량 NULL(실측 0/176k) - volume(82%)이 거래량 축이다.
           log_return - avg(log_return) OVER (PARTITION BY trade_date) AS ar
    FROM rdb.public.price_daily
    WHERE log_return IS NOT NULL
),
f AS (
    SELECT instrument_id, trade_date, ar,
           sum(ar)                 OVER w20 AS cum20,
           stddev_samp(log_return) OVER w20 AS vol20,
           avg(volume)             OVER w20 AS tv20,
           volume / NULLIF(avg(volume) OVER w20, 0) - 1 AS tv_chg
    FROM r
    WINDOW w20 AS (PARTITION BY instrument_id ORDER BY trade_date
                   ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
),
g AS (
    SELECT *,
           (ar - avg(ar) OVER w60) / NULLIF(stddev_samp(ar) OVER w60, 0) AS z_ar,
           (tv_chg - avg(tv_chg) OVER w60) / NULLIF(stddev_samp(tv_chg) OVER w60, 0) AS z_tv_chg
    FROM f
    WINDOW w60 AS (PARTITION BY instrument_id ORDER BY trade_date
                   ROWS BETWEEN 60 PRECEDING AND 1 PRECEDING)
)
"""

_POINT_PANEL = _BASE + """
, ev AS (
    SELECT DISTINCT ea.entity_id AS iid, se.event_date AS d
    FROM rdb.public.source_event se
    JOIN rdb.public.event_argument ea ON ea.source_event_id = se.source_event_id
    JOIN rdb.public.instrument i ON i.instrument_id = ea.entity_id
    WHERE se.event_type_code = '{etype}' AND se.event_status = 'ACTIVE'
      AND se.event_date {cmp} DATE '{day}'
      AND se.available_at <= TIMESTAMP '{day} {clock}'
)
SELECT g.instrument_id, g.trade_date, g.ar, {cols}
FROM ev JOIN g ON g.instrument_id = ev.iid AND g.trade_date = ev.d
"""

_SERIES_PANEL = _BASE + """
SELECT instrument_id, trade_date, ar, {cols}
FROM g WHERE abs(z_{innov}) >= {z} AND trade_date < DATE '{day}'
"""

_TODAY_ROW = _BASE + """
SELECT {cols} FROM g WHERE instrument_id = '{iid}' AND trade_date = DATE '{day}'
"""

# 전이 패널 (§16 관계 노출): 사건 종목의 동일산업 피어(관계=1) vs 비피어(위약=0).
# 위약이 '같은 날 시장 전체'인 이유: 날짜 층화 순열이 공통충격을 소거하므로,
# 남는 대비가 정확히 '관계가 있느냐'다. 산업 분류는 셀 시점 이전 최신(PIT).
_RELATION_PANEL = _BASE + """
, cls AS (
    SELECT instrument_id, industry_name FROM (
        SELECT instrument_id, industry_name,
               row_number() OVER (PARTITION BY instrument_id ORDER BY as_of_date DESC) rn
        FROM rdb.public.instrument_classification
        WHERE as_of_date <= DATE '{day}' AND industry_name IS NOT NULL) WHERE rn = 1
),
ev AS (
    SELECT DISTINCT ea.entity_id AS iid, se.event_date AS d
    FROM rdb.public.source_event se
    JOIN rdb.public.event_argument ea ON ea.source_event_id = se.source_event_id
    JOIN rdb.public.instrument i ON i.instrument_id = ea.entity_id
    WHERE se.event_type_code = '{etype}' AND se.event_status = 'ACTIVE'
      AND se.event_date < DATE '{day}'
      AND se.available_at <= TIMESTAMP '{day} 00:00:00'
)
SELECT g.instrument_id, g.trade_date, g.ar, {cols},
       CASE WHEN cp.industry_name = ce.industry_name THEN 1 ELSE 0 END AS rel
FROM ev
JOIN g   ON g.trade_date = ev.d AND g.instrument_id <> ev.iid
JOIN cls cp ON cp.instrument_id = g.instrument_id
JOIN cls ce ON ce.instrument_id = ev.iid
"""


@dataclass(frozen=True, slots=True)
class EdgeReport:
    """엣지 하나의 패널 판정 + 오늘 적용 판정. 수치는 전부 이 모듈이 계산했다."""
    verdict: EdgeVerdict
    n: int
    p: float | None
    effect_high: float | None        # 취약성 조건화된 패널에서 노출 상위 평균 ar
    effect_low: float | None
    today_exposure_pct: float | None
    vuln_today: str = ""             # 오늘 셀의 취약성 술어별 충족 (예: "수급/누적 p98 충족")
    vuln_satisfied: bool | None = None   # None = 취약성 없음 또는 못 잼
    counterfactual: str = ""         # 반사실 쌍 (positivity 통과 시에만 채워진다)
    reduction: str = "—"             # 환원 검사: 일치 · 불일치 · 표본부족 · —(미실행)
    assignable: bool = True          # False = 엣지 검정만 유효, 몫 배정 불가 (전이 등)
    reason: str = ""

    @property
    def applies_today(self) -> bool:
        """오늘 셀에 몫을 배정할 자격. 성립 + 배정가능 + 취약성 미위반 + 환원 미불일치."""
        return (self.verdict == "성립" and self.assignable
                and self.vuln_satisfied is not False
                and not self.reduction.startswith("불일치"))

    @property
    def line(self) -> str:
        if self.verdict == "판정불가":
            return f"판정불가 (n={self.n}) — {self.reason}"
        hi = f"{self.effect_high * 100:+.2f}%" if self.effect_high is not None else "?"
        lo = f"{self.effect_low * 100:+.2f}%" if self.effect_low is not None else "?"
        te = (f" · 오늘 노출 p{self.today_exposure_pct * 100:.0f}"
              if self.today_exposure_pct is not None else "")
        return f"{self.verdict} (n={self.n}, p={self.p:.3f}, 상위 {hi} vs 하위 {lo}{te})"


def _unmeasurable(reason: str) -> EdgeReport:
    return EdgeReport("판정불가", 0, None, None, None, None, reason=reason)


def _pctile(v: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(v)) / max(len(v) - 1, 1)


def _stratified_p(ar: np.ndarray, hi: np.ndarray, dates: np.ndarray,
                  sign: float) -> float:
    """사건일 층화 순열 귀무의 단측 p. SEED 고정 - 재실행 결정론."""
    obs = (ar[hi].mean() - ar[~hi].mean()) * sign
    rng = np.random.default_rng(SEED)
    null = np.empty(PERMS)
    for k in range(PERMS):
        perm = hi.copy()
        for d in np.unique(dates):
            m = dates == d
            perm[m] = rng.permutation(perm[m])
        null[k] = (ar[perm].mean() - ar[~perm].mean()) * sign
    return float((null >= obs).mean())


def _cols(t: HypothesisTuple) -> tuple[list[tuple[str, str]], str] | None:
    """튜플이 요구하는 피처 컬럼 목록 (노출 + 취약성들). 노출을 못 재면 None."""
    need = [("__x__", FEATURES.get((t.exposure.ident, t.exposure.transform)))]
    for v in t.vulnerabilities:
        need.append((f"{v.family}/{v.transform}", FEATURES.get((v.family, v.transform))))
    if need[0][1] is None:
        return None
    return ([(k, c) for k, c in need if c is not None],
            ", ".join(f"g.{c}" for _, c in need if c is not None))


def edge_test(lake, t: HypothesisTuple, day: str,
              cell_instrument_id: str = "") -> EdgeReport:
    """튜플 → 패널 검정 → 오늘 적용 판정. 표본이 얇으면 판정불가 —
    **다른 표본을 찾으러 가지 않는다.**"""
    if t.exposure.kind == "관계":
        return _relation_test(lake, t, day)
    got = _cols(t)
    if got is None:
        return _unmeasurable(
            f"노출 ({t.exposure.ident},{t.exposure.transform}) 는 아직 못 잰다 - "
            f"재는 것: {sorted(FEATURES)}")
    cols, col_sql = got
    unmeasured_vulns = [f"{v.family}/{v.transform}" for v in t.vulnerabilities
                        if (v.family, v.transform) not in FEATURES]

    if t.trigger.kind == "점":
        sql = _POINT_PANEL.format(etype=t.trigger.ident, cmp="<", day=day,
                                  clock="00:00:00", cols=col_sql)
    else:
        innov = _INNOVATION.get(t.trigger.ident)
        if innov is None:
            return _unmeasurable(f"계열 방아쇠 {t.trigger.ident!r} 의 혁신값은 아직 못 잰다 - "
                                 f"재는 것: {sorted(_INNOVATION)}")
        sql = _SERIES_PANEL.format(innov=innov, z=Z_ANOM, day=day, cols=col_sql)

    raw = [row for row in lake.sql(sql) if all(v is not None for v in row[2:])]
    if len(raw) < MIN_N:
        return EdgeReport("판정불가", len(raw), None, None, None, None,
                          reason=f"패널 n={len(raw)} < {MIN_N}")

    ar = np.array([float(r[2]) for r in raw])
    dates = np.array([str(r[1]) for r in raw])
    feats = {cols[j][0]: np.array([float(r[3 + j]) for r in raw])
             for j in range(len(cols))}
    x = feats["__x__"]

    pctile = _pctile

    # ── 취약성 = INUS 조건화. 미충족 표본의 용량-반응은 이 가설의 검정이 아니다 ──
    mask = np.ones(len(ar), dtype=bool)
    for v in t.vulnerabilities:
        key = f"{v.family}/{v.transform}"
        if key not in feats:
            continue                                   # 못 재는 술어는 부재 선언으로만
        pv = pctile(feats[key])
        mask &= (pv >= v.percentile) if v.comparator == ">=" else (pv <= v.percentile)
    opposite = int((~mask).sum())
    if mask.sum() < MIN_N:
        return EdgeReport("판정불가", int(mask.sum()), None, None, None, None,
                          reason=f"취약성 조건화 후 n={int(mask.sum())} < {MIN_N} - "
                                 "조건이 표본을 죽인다 (임계를 완화하거나 백필)")

    def dose(sub: np.ndarray) -> tuple[float | None, float | None, np.ndarray | None]:
        xs, ars = x[sub], ar[sub]
        hi = pctile(xs) >= EXPOSURE_CUT
        if hi.sum() < 3 or (~hi).sum() < 3:
            return None, None, None
        return float(ars[hi].mean()), float(ars[~hi].mean()), hi

    eff_hi, eff_lo, hi = dose(mask)
    if hi is None:
        return EdgeReport("판정불가", int(mask.sum()), None, None, None, None,
                          reason="노출 분산 부족 - 상·하위가 갈리지 않는다 (게이트 A)")

    sign = float(t.sign)
    p = _stratified_p(ar[mask], hi, dates[mask], sign)
    verdict = edge_gate(int(mask.sum()), p)

    # ── 반사실 쌍 (§14): 취약성 미충족 부류의 효과. positivity 없으면 침묵 ──
    counterfactual = ""
    if t.vulnerabilities and opposite >= MIN_OPPOSITE:
        c_hi, c_lo, c_mask = dose(~mask)
        if c_hi is not None:
            counterfactual = (f"취약성 미충족 부류(n={opposite})에서는 상위 {c_hi * 100:+.2f}% "
                              f"vs 하위 {c_lo * 100:+.2f}% - 충족 부류와 대조하라")
    elif t.vulnerabilities:
        counterfactual = f"반대(미충족) 사례 {opposite}건 < {MIN_OPPOSITE} - 반사실 침묵 (positivity)"

    # ── 오늘 셀: 노출 백분위 + 취약성 충족 (INUS 의 적용 판정) ──────────
    today_pct = None
    vuln_bits: list[str] = []
    vuln_sat: bool | None = None
    if cell_instrument_id:
        row = lake.sql(_TODAY_ROW.format(iid=cell_instrument_id, day=day, cols=col_sql))
        if row and row[0][0] is not None:
            today_pct = float((x <= float(row[0][0])).mean())
            sat = True
            for v in t.vulnerabilities:
                key = f"{v.family}/{v.transform}"
                if key not in feats:
                    vuln_bits.append(f"{key} 못잼")
                    continue
                # cols 의 이름 순서 = _TODAY_ROW 반환 컬럼 순서. 이름으로 찾는다.
                idx = [k for k, _ in cols].index(key)
                tv = row[0][idx]
                if tv is None:
                    vuln_bits.append(f"{key} 오늘 결측")
                    continue
                pv = float((feats[key] <= float(tv)).mean())
                ok = (pv >= v.percentile) if v.comparator == ">=" else (pv <= v.percentile)
                sat &= bool(ok)
                vuln_bits.append(f"{key} p{pv * 100:.0f} {'충족' if ok else '미충족'}")
            vuln_sat = sat if t.vulnerabilities else None

    # ── 환원 검사 (§8): 오늘 같은 타입의 횡단면이 패널과 같은 방향인가 ──
    reduction = "—"
    if t.trigger.kind == "점":
        tsql = _POINT_PANEL.format(etype=t.trigger.ident, cmp="=", day=day,
                                   clock="23:59:59", cols=col_sql)
        trows = [r for r in lake.sql(tsql) if all(v is not None for v in r[2:])]
        if len(trows) < MIN_TODAY:
            reduction = f"표본부족 (오늘 n={len(trows)})"
        else:
            t_ar = np.array([float(r[2]) for r in trows])
            t_x = np.array([float(r[3]) for r in trows])
            t_hi = pctile(t_x) >= EXPOSURE_CUT
            if t_hi.sum() < 2 or (~t_hi).sum() < 2:
                reduction = f"표본부족 (오늘 노출 분산 없음, n={len(trows)})"
            else:
                t_obs = (t_ar[t_hi].mean() - t_ar[~t_hi].mean()) * sign
                reduction = "일치" if t_obs > 0 else "불일치"
                reduction += f" (오늘 n={len(trows)}, 방향 {'+' if t_obs > 0 else '-'})"

    if unmeasured_vulns:
        vuln_bits.append("패널 미조건화: " + "·".join(unmeasured_vulns))

    return EdgeReport(verdict, int(mask.sum()), p, eff_hi, eff_lo, today_pct,
                      vuln_today=" · ".join(vuln_bits), vuln_satisfied=vuln_sat,
                      counterfactual=counterfactual, reduction=reduction)


def _relation_test(lake, t: HypothesisTuple, day: str) -> EdgeReport:
    """전이 패널 (§16): 사건 종목의 동일산업 피어가 비피어보다 부호 방향으로
    더 반응했는가. 위약 = 같은 날 비관계 종목 (날짜 층화가 공통충격을 소거하므로
    남는 대비가 정확히 '관계'다). **몫 배정은 비지원**(assignable=False) -
    오늘 셀에 배정하려면 소스-타깃 창 정렬(누가 먼저 움직였나, 5분봉)이 필요하고
    그 판은 다음이다. 엣지의 존재 검정까지가 이 함수의 정직한 범위다."""
    if t.exposure.ident != "SAME_INDUSTRY":
        return _unmeasurable(f"관계 노출 {t.exposure.ident!r} 는 아직 못 잰다 - "
                             "재는 것: ['SAME_INDUSTRY']")
    if t.trigger.kind != "점":
        return _unmeasurable("계열 방아쇠 × 관계 노출 조합 판은 아직 없다")

    vcols = [(f"{v.family}/{v.transform}", FEATURES[(v.family, v.transform)])
             for v in t.vulnerabilities if (v.family, v.transform) in FEATURES]
    col_sql = ", ".join(f"g.{c}" for _, c in vcols) or "1 AS _one"
    sql = _RELATION_PANEL.format(etype=t.trigger.ident, day=day, cols=col_sql)
    raw = [r for r in lake.sql(sql) if all(v is not None for v in r[2:])]
    if len(raw) < MIN_N:
        return EdgeReport("판정불가", len(raw), None, None, None, None,
                          reason=f"전이 패널 n={len(raw)} < {MIN_N}")

    ar = np.array([float(r[2]) for r in raw])
    dates = np.array([str(r[1]) for r in raw])
    rel = np.array([int(r[-1]) for r in raw]) == 1
    feats = {vcols[j][0]: np.array([float(r[3 + j]) for r in raw])
             for j in range(len(vcols))}

    mask = np.ones(len(ar), dtype=bool)               # INUS: 피어 측 취약성 조건화
    for v in t.vulnerabilities:
        key = f"{v.family}/{v.transform}"
        if key not in feats:
            continue
        pv = _pctile(feats[key])
        mask &= (pv >= v.percentile) if v.comparator == ">=" else (pv <= v.percentile)
    if mask.sum() < MIN_N:
        return EdgeReport("판정불가", int(mask.sum()), None, None, None, None,
                          reason=f"취약성 조건화 후 n={int(mask.sum())} < {MIN_N}")
    hi = rel[mask]
    if hi.sum() < 3 or (~hi).sum() < 3:
        return EdgeReport("판정불가", int(mask.sum()), None, None, None, None,
                          reason="관계·비관계가 갈리지 않는다 (게이트 A)")

    sign = float(t.sign)
    sub = ar[mask]
    p = _stratified_p(sub, hi, dates[mask], sign)
    return EdgeReport(edge_gate(int(mask.sum()), p), int(mask.sum()), p,
                      float(sub[hi].mean()), float(sub[~hi].mean()), None,
                      vuln_today="전이: 취약성은 피어 측 - 오늘 셀 평가 없음",
                      assignable=False,
                      reason="몫 배정 비지원 - 소스-타깃 창 정렬(5분봉)이 다음 판이다")


def grid_screen(lake, day: str, types: list[str],
                max_rows: int = 6) -> list[dict]:
    """결정론 격자 스크린 — 그날 타입 × 측정가능 노출 전수. LLM 무관.

    가설 커버리지의 세션 분산(라이브 2차는 EXECUTIVE_CHANGE 성립을 찾았는데
    3차는 그 튜플을 안 골랐다)을 메우는 이중화다. 닫힌 어휘라서 격자가 유한하고,
    유한해서 전수가 가능하다. **탐색이지 확증이 아니다**: 방향을 사후에 고르므로
    p 는 양측(x2)이고, 결과는 '스크린 발견'으로만 표기한다 - 확증은 취약성·환원
    검사를 거치는 튜플 게이트의 몫이다.
    """
    feat_names = list(dict.fromkeys(FEATURES.values()))
    all_cols = ", ".join(f"g.{c}" for c in feat_names)
    out: list[dict] = []
    label_of = {c: k for k, c in FEATURES.items()}
    for etype in types:
        sql = _POINT_PANEL.format(etype=etype, cmp="<", day=day,
                                  clock="00:00:00", cols=all_cols)
        raw = [r for r in lake.sql(sql) if r[2] is not None]
        if len(raw) < MIN_N:
            out.append({"type": etype, "status": f"표본부족 n={len(raw)}"})
            continue
        ar = np.array([float(r[2]) for r in raw])
        dates = np.array([str(r[1]) for r in raw])
        for j, colname in enumerate(feat_names):
            xv = np.array([float(r[3 + j]) if r[3 + j] is not None else np.nan
                           for r in raw])
            ok = ~np.isnan(xv)
            if ok.sum() < MIN_N:
                continue
            hi = _pctile(xv[ok]) >= EXPOSURE_CUT
            if hi.sum() < 3 or (~hi).sum() < 3:
                continue
            p_pos = _stratified_p(ar[ok], hi, dates[ok], +1.0)
            p2 = min(min(p_pos, 1.0 - p_pos) * 2, 1.0)
            fam, tr = label_of[colname]
            out.append({"type": etype, "exposure": f"{fam}/{tr}",
                        "n": int(ok.sum()), "p2": round(p2, 3),
                        "direction": "+" if p_pos < 0.5 else "-",
                        "hi": float(ar[ok][hi].mean()), "lo": float(ar[ok][~hi].mean())})
    hits = sorted((o for o in out if "p2" in o), key=lambda o: o["p2"])
    misses = [o for o in out if "p2" not in o]
    return hits[:max_rows] + misses


__all__ = ["EdgeReport", "FEATURES", "MIN_OPPOSITE", "PERMS", "SEED", "Z_ANOM",
           "edge_test", "grid_screen"]
