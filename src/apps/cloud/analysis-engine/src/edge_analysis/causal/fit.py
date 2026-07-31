"""적합도 - **국소가 본체, 전역은 마지막에 한 번.**

왜 SEM 의 ML 적합(카이제곱 on Σ(θ))을 안 쓰나:
  · Σ(θ) 를 세우려면 선형·정규를 다 받아야 한다. 우리 데이터는 둘 다 아니다.
  · 그리고 우리가 필요한 건 "어디가 틀렸나"다. 전역 카이제곱은 그걸 안 알려준다
    (Bollen-Pearl 자신들의 비판: *"SEM researchers tend to focus too heavily on
    global tests ... and often ignore local tests ... which are indispensable
    for model diagnosis"*).

대신 **Shipley (2000) d-분리 검정**을 쓴다. 국소에서 전역이 조립된다:

    국소   함의 조건부독립 하나마다 부분상관 검정 -> p_i
    전역   C = -2 Σ ln p_i  ~  chi2(2k)          <- 같은 p_i 를 합성

`MI`(수정지수) 대응물도 따로 계산하지 않는다. **깨진 CI 가 곧 빠진 간선이다** -
p 오름차순 목록이 "무슨 화살표를 추가해야 하나"를 그대로 준다. SEM 의 MI 보다 직접적이다.

쓰지 않는 지표: CFI·TLI. 둘은 독립모형 기준선과 Σ(θ) 의 ML 적합을 요구하는데
d-분리 검정에는 Σ(θ) 가 없다 - 정의되지 않는다. 억지로 계산하면 숫자만 나온다.
"""
from __future__ import annotations

import math

import numpy as np

from . import graph as G


# ── chi2 상단 꼬리 (짝수 자유도) ────────────────────────────────────────
def chi2_sf(c: float, df: int) -> float:
    """P(chi2_df > c). df 는 항상 짝수(=2k)라 닫힌형이 있다 - scipy 불필요."""
    if df <= 0:
        return 1.0
    if c <= 0:
        return 1.0
    k, h = df // 2, c / 2.0
    term, s = 1.0, 1.0
    for j in range(1, k):
        term *= h / j
        s += term
    return float(min(1.0, math.exp(-h) * s))


def _norm_sf(z: float) -> float:
    return 0.5 * math.erfc(abs(z) / math.sqrt(2.0))


# ── 국소: 부분상관 조건부독립 검정 ──────────────────────────────────────
def ci_test(cols: dict, X: str, Y: str, Z: tuple) -> dict:
    """X ⊥ Y | Z 를 부분상관으로 검정. Fisher z.

    반환 `testable=False` 는 실패가 아니라 **잠재라서 못 잰다**는 뜻이다 -
    그게 잠재변수 모형에서 CI 만으로 완비가 안 되는 이유이고, tetrad 가 필요한 자리다.
    """
    need = [X, Y, *Z]
    miss = [n for n in need if n not in cols]
    if miss:
        return {"X": X, "Y": Y, "Z": tuple(Z), "testable": False,
                "reason": f"열 없음(잠재): {miss}"}
    M = np.column_stack([np.asarray(cols[n], dtype=float).ravel() for n in need])
    M = M[np.isfinite(M).all(axis=1)]
    n = len(M)
    if n < len(Z) + 10:
        return {"X": X, "Y": Y, "Z": tuple(Z), "testable": False,
                "reason": f"표본 {n} - |Z|+10 미만", "n": n}
    x, y = M[:, 0], M[:, 1]
    if Z:
        W = np.column_stack([np.ones(n), M[:, 2:]])
        x = x - W @ np.linalg.lstsq(W, x, rcond=None)[0]
        y = y - W @ np.linalg.lstsq(W, y, rcond=None)[0]
    sx, sy = x.std(), y.std()
    if sx == 0 or sy == 0:
        return {"X": X, "Y": Y, "Z": tuple(Z), "testable": False,
                "reason": "잔차 분산 0", "n": n}
    r = float(np.clip(np.corrcoef(x, y)[0, 1], -0.999999, 0.999999))
    dof = n - len(Z) - 3
    if dof <= 0:
        return {"X": X, "Y": Y, "Z": tuple(Z), "testable": False,
                "reason": f"자유도 {dof}", "n": n}
    z = 0.5 * math.log((1 + r) / (1 - r)) * math.sqrt(dof)
    return {"X": X, "Y": Y, "Z": tuple(Z), "testable": True,
            "n": n, "r": r, "p": float(2 * _norm_sf(z))}


def implied(nodes: dict, edges: list) -> list:
    """함의 조건부독립 기저. 양방향을 인식한다 (잠재 확장 후 관측쌍만)."""
    d, b = G.split(edges)
    full = G.expand(d, b)
    obs = [n for n in nodes if not n.startswith(G._LAT)]
    out = []
    for i, X in enumerate(sorted(obs)):
        for Y in sorted(obs)[i + 1:]:
            if any({a, c} == {X, Y} for a, c in full) or any({a, c} == {X, Y} for a, c in b):
                continue
            Z = tuple(sorted((G.parents(full, X) | G.parents(full, Y)) - {X, Y}
                             - {n for n in nodes if False}))
            Zo = tuple(z for z in Z if not z.startswith(G._LAT))
            if G.msep(d, b, X, Y, set(Zo)):
                out.append((X, Y, Zo))
    return out


def local_fit(nodes: dict, edges: list, cols: dict) -> list:
    """함의 전부를 데이터로 검정. **p 오름차순이 곧 수정지수다.**"""
    rs = [ci_test(cols, X, Y, Z) for X, Y, Z in implied(nodes, edges)]
    return sorted(rs, key=lambda r: (not r["testable"], r.get("p", 9)))


# ── 전역: Shipley's C ───────────────────────────────────────────────────
def global_fit(local: list) -> dict:
    """C = -2 Σ ln p_i ~ chi2(2k). 국소에서 조립된다 - 별도 적합 없음.

    **일일 게시 경로는 이걸 쓰지 않는다**(`run.explain` 은 `chain.budget` 을 쓴다).
    이 통계량이 답하는 것은 "이 DAG 가 모집단 공분산과 정합하나"이고, 귀속이 묻는 것은
    "오늘 이 움직임을 어디까지 설명했나"다. 남겨둔 이유는 발견 루프(실험판)에서 구조
    후보를 비교할 때 여전히 쓰이기 때문이다.
    """
    ps = [max(r["p"], 1e-300) for r in local if r["testable"]]
    k = len(ps)
    if k == 0:
        return {"testable": False, "reason": "검정 가능한 함의가 없다 (전부 잠재/표본부족)"}
    C = float(-2 * sum(math.log(p) for p in ps))
    df = 2 * k
    ns = [r["n"] for r in local if r.get("n")]
    N = int(np.median(ns)) if ns else 0
    out = {"testable": True, "C": C, "df": df, "k": k, "p": chi2_sf(C, df),
           "C_over_df": C / df, "N": N,
           "n_untestable": sum(1 for r in local if not r["testable"])}
    if N > 1:
        out["RMSEA"] = float(math.sqrt(max(C - df, 0.0) / (df * (N - 1))))
    return out


def report(nodes: dict, edges: list, cols: dict, *, top: int = 8) -> str:
    L = local_fit(nodes, edges, cols)
    g = global_fit(L)
    L2 = ["[국소 적합] 함의 조건부독립. **p 낮은 것이 빠진 간선이다** (= 수정지수)",
         f"  {'X':<22} {'Y':<22} {'|Z|':>3} {'n':>5} {'r':>7} {'p':>8}"]
    for r in L[:top]:
        if r["testable"]:
            flag = "  <- 위반" if r["p"] < 0.05 else ""
            L2.append(f"  {r['X'][:22]:<22} {r['Y'][:22]:<22} {len(r['Z']):>3} "
                     f"{r['n']:>5} {r['r']:>+7.3f} {r['p']:>8.4f}{flag}")
        else:
            L2.append(f"  {r['X'][:22]:<22} {r['Y'][:22]:<22} {len(r['Z']):>3} "
                     f"{'':>5} {'':>7} {'  미검정':>8}  {r['reason'][:40]}")
    if len(L) > top:
        L2.append(f"  ... 총 {len(L)}건")
    viol = [r for r in L if r["testable"] and r["p"] < 0.05]
    L2.append(f"  위반 {len(viol)}건 / 검정 {sum(1 for r in L if r['testable'])}건"
             f" / 미검정 {sum(1 for r in L if not r['testable'])}건")
    L2 += ["", "[전역 적합] Shipley d-분리 검정 - 국소 p 를 합성한 것. 한 번만 본다"]
    if not g["testable"]:
        L2.append(f"  불가: {g['reason']}")
    else:
        L2.append(f"  C = -2 Σ ln p = {g['C']:.2f}   df = 2k = {g['df']}   "
                 f"p = {g['p']:.4f}   C/df = {g['C_over_df']:.2f}")
        if "RMSEA" in g:
            L2.append(f"  RMSEA = {g['RMSEA']:.3f}   (N={g['N']})")
        L2.append("  p > 0.05 면 그래프가 데이터와 불일치한다는 증거가 없다"
                 " (적합 증명이 아니다)." if g["p"] > 0.05 else
                 "  **p <= 0.05 - 그래프가 데이터와 불일치한다.** 위 위반 목록이 어디인지 말한다.")
        if g["n_untestable"]:
            L2.append(f"  주의: 미검정 {g['n_untestable']}건은 C 에 안 들어갔다 - "
                     "잠재가 있으면 CI 만으로 완비가 아니다(tetrad 필요).")
    return "\n".join(L2)


