"""라쏘 커널 — 조절자 **선택을 검정 안으로** 넣기 위한 최소 도구. 의존성 numpy 뿐.

왜 자체 구현인가: sklearn·scipy 가 이 컨테이너에 없다. 넣으면 이미지가 커지고, 이
저장소는 "없는 패키지를 import 하면 컨테이너에서 즉사한다" 로 이미 데인 적이 있다.
좌표하강은 40줄이고 결정론이다 - 같은 입력이면 같은 출력.

**왜 라쏘인가.** 지금까지 조절자는 술어 하나(논리곱)로 표본을 쪼갰고, 조건을 붙일수록
표본이 죽어 판정불가만 남았다(실측 n=4·5·6·23). 매개변수화하면 전체 표본을 쓰면서
여러 조절자를 동시에 볼 수 있다:

    δₚ = a + Σⱼ dⱼ·c_{p,j} + uₚ      CATE(c) = a + Σⱼ dⱼ·cⱼ

`a`(=ATT)는 **벌점 없음**이다. 수축되면 게이트가 무너진다.

**왜 이것만으로는 통계가 아닌가.** 순진하게 적합 -> 비영계수 -> p 를 내면 그건 선택 후
추론이고 위양성이 명목의 몇 배로 뜬다. 이 파일은 커널만 제공하고, 유효한 p 는
`moderate()` 가 **선택을 순열 루프 안에 넣어서** 만든다(Westfall-Young maxT).
"""

from __future__ import annotations

import numpy as np

# 안정성 선택 (Meinshausen-Bühlmann). 전역 상수 - 셀별 조정 금지.
PI_MIN = 0.70        # 이 빈도 미만으로 뽑히는 조절자는 설명에 싣지 않는다
B_SUB = 200          # 부표본 수
SUB_FRAC = 0.5       # 부표본 비율 (짝의 절반)
# λ 격자. 전량을 보고한다 - 하나만 쓰면 스펙 쇼핑이 보이지 않는다.
LAM_GRID = (0.0005, 0.001, 0.002, 0.005, 0.01)
MISS_MAX = 0.20      # 결측률이 이보다 높은 후보는 제외(대체 금지 - 부재는 부재다)
COLLIN_CUT = 0.95    # |상관| 이 이보다 크면 준중복 - 적합 전에 하나만 남긴다


def lasso(y: np.ndarray, X: np.ndarray, lam: float, *, max_iter: int = 200,
          tol: float = 1e-8, warm: np.ndarray | None = None) -> tuple[float, np.ndarray]:
    """절편 비벌점 좌표하강. `(a, d)` 반환.

    `X` 는 [0,1] 백분위이므로 표준화하지 않는다 - 스케일이 이미 공평하다. 표준화를
    끼우면 계수의 단위가 '표준편차당' 으로 바뀌어 산문이 %p 로 못 읽는다.

    `warm` 은 순열 루프의 비용을 결정한다(1000회 x 22열). 직전 해에서 출발하면
    반복이 몇 번에 끝난다.
    """
    n, p = X.shape
    d = np.zeros(p) if warm is None else np.asarray(warm, dtype=float).copy()
    xx = (X * X).sum(axis=0)                      # 열별 제곱합 - 루프 밖에서 한 번
    a = float((y - X @ d).mean())
    for _ in range(max_iter):
        d_old = d.copy()
        r = y - a - X @ d
        for j in range(p):
            if xx[j] <= 0.0:                      # 상수열: 기울기를 정의할 수 없다
                continue
            rho = float(X[:, j] @ (r + X[:, j] * d[j]))
            new = np.sign(rho) * max(abs(rho) - lam * n, 0.0) / xx[j]
            r += X[:, j] * (d[j] - new)
            d[j] = new
        a = float((y - X @ d).mean())              # 절편은 연화하지 않는다
        if np.abs(d - d_old).max() < tol:
            break
    return a, d


def post_ols(y: np.ndarray, X: np.ndarray,
             keep: np.ndarray) -> tuple[float, np.ndarray]:
    """post-LASSO 재적합. **크기는 여기서 나온다.**

    라쏘 계수는 벌점 때문에 0 쪽으로 편의돼 있다 - 산문에 그 값을 쓰면 효과를 체계적
    으로 과소보고한다. 선택 집합을 확정한 뒤 벌점 없는 OLS 로 다시 잰다.

    보고 규약: **유의성은 순열 p, 크기는 이 값.** 두 출처를 섞지 않는다.
    """
    d = np.zeros(X.shape[1])
    if not keep.any():
        return float(y.mean()), d
    A = np.column_stack([np.ones(len(y)), X[:, keep]])
    beta = np.linalg.lstsq(A, y, rcond=None)[0]
    d[keep] = beta[1:]
    return float(beta[0]), d


def _perm_labels(C: np.ndarray, dates: np.ndarray, rng) -> np.ndarray:
    """날짜 층 안에서 조건 라벨을 섞는다. 귀무는 '조건이 효과를 안 바꾼다'.

    날짜를 섞으면 '이 날이 특별한가' 가 되고 그건 순환이다(셀이 큰 이상수익으로 선정
    됐으므로). 층화가 공통충격을 통제한다.
    """
    out = C.copy()
    for d in np.unique(dates):
        m = dates == d
        idx = rng.permutation(np.flatnonzero(m))
        out[m] = C[idx]
    return out


def prune_collinear(X: np.ndarray, names: list[str],
                    cut: float = COLLIN_CUT) -> tuple[np.ndarray, dict[str, str]]:
    """준중복 열을 **적합 전에** 걷는다. `(살릴 열 마스크, {버린 이름: 남긴 이름})`.

    안정성 선택이 이걸 처리해 줄 것이라 기대했는데 **실측이 반증했다**: 두 열이 거의
    같으면(|corr|≈1) 좌표하강은 하나를 고르는 게 아니라 **계수를 쪼개 나눈다** - 둘 다
    Π=1.0 으로 뽑히고 크기는 절반씩 실린다. 그러면 산문이 같은 조건을 두 이름으로
    말하면서 각각을 과소보고한다. post-OLS 도 조건수가 나빠진다.

    실측 후보에 `fr_lvl`↔`fr_chg`, `lev_lvl`↔`lev_chg` 같은 쌍이 여럿이라 실제로 걸린다.
    남기는 쪽은 **먼저 온 열**이다 - 사후에 고르면 그게 선택 편향이다.
    """
    p = X.shape[1]
    keep = np.ones(p, dtype=bool)
    dropped: dict[str, str] = {}
    sd = X.std(axis=0)
    for i in range(p):
        if not keep[i] or sd[i] <= 0:
            continue
        for j in range(i + 1, p):
            if not keep[j] or sd[j] <= 0:
                continue
            c = float(np.corrcoef(X[:, i], X[:, j])[0, 1])
            if abs(c) >= cut:
                keep[j] = False
                dropped[names[j]] = f"{names[i]} (|corr|={abs(c):.3f})"
    return keep, dropped


def stability(y: np.ndarray, X: np.ndarray, dates: np.ndarray, lam: float,
              *, seed: int = 0, b: int = B_SUB,
              frac: float = SUB_FRAC) -> np.ndarray:
    """열별 선택 빈도 Π (Meinshausen-Bühlmann). 부표본마다 라쏘를 다시 적합한다.

    잡는 것: **적당히** 상관된 후보들 사이의 흔들리는 선택. 표본을 조금 바꿨을 때
    뽑히다 말다 하는 열은 Π 가 임계 밑으로 떨어진다 - 안정적이지 않은 선택을 설명에
    싣지 않는 것이 정직하다.

    못 잡는 것: **준중복**(|corr|≈1). 그때 라쏘는 고르는 대신 쪼개 나눠서 둘 다 Π=1 이
    된다 - 그건 `prune_collinear` 가 적합 전에 처리한다(실측으로 확인했다).
    """
    rng = np.random.default_rng(seed)
    hit = np.zeros(X.shape[1])
    n = len(y)
    take = max(int(round(n * frac)), 4)
    warm = None
    for _ in range(b):
        # 날짜 층화 부표본: 층을 무시하면 한 날이 통째로 빠져 선택이 날짜에 좌우된다
        idx: list[int] = []
        for d in np.unique(dates):
            m = np.flatnonzero(dates == d)
            k = max(int(round(len(m) * frac)), 1)
            idx += list(rng.choice(m, size=k, replace=False))
        if len(idx) < 4:
            continue
        sub = np.asarray(sorted(idx))
        _, dd = lasso(y[sub], X[sub], lam, warm=warm)
        warm = dd
        hit += (dd != 0.0)
    return hit / max(b, 1)


def moderate(y: np.ndarray, X: np.ndarray, dates: np.ndarray, names: list[str],
             *, lam_grid: tuple[float, ...] = LAM_GRID, perms: int = 1000,
             seed: int = 0, alpha: float = 0.05) -> dict:
    """조절자 검정 전 과정. **선택이 순열 안에 있다** - 그것이 p 를 유효하게 만든다.

    관측 통계량은 `T = max_j |d̂ⱼ|` 이고, 귀무 분포는 매 순열마다 **라쏘를 다시 적합**해
    만든다. max 통계량이 다중비교를 공짜로 처리한다(Westfall-Young maxT) - J 개를 훑어
    최대를 골랐다는 사실이 귀무 분포에 그대로 반영되므로 Bonferroni 도 FDR 도 필요 없다.

    개별 조절자는 **단계적 하강**으로 판정한다: 최대가 유의하면 그 열을 빼고 다시 최대를
    잰다. 이것이 강한 FWER 통제를 준다.

    λ 는 격자 전량을 보고한다 - 격자마다 선택 집합이 갈리면 그 사실이 곧 스펙 민감이다.
    """
    n, p = X.shape
    if n < 4 or p == 0:
        return {"verdict": "판정불가", "reason": f"조절자 검정 표본 n={n} · 후보 {p}"}

    # 준중복 후보를 먼저 걷는다 - 남기면 계수가 쪼개져 둘 다 과소보고된다
    live0, dropped = prune_collinear(X, names)
    X, names = X[:, live0], [names[i] for i in range(p) if live0[i]]
    p = X.shape[1]

    lam = lam_grid[len(lam_grid) // 2]            # 중앙값을 본 λ 로 삼는다
    a_hat, d_hat = lasso(y, X, lam)
    pi = stability(y, X, dates, lam, seed=seed)

    # ── maxT 순열: 선택을 루프 안에 둔다 ──────────────────────────────────
    rng = np.random.default_rng(seed)
    null = np.empty(perms)
    warm = None
    for k in range(perms):
        _, dk = lasso(y, _perm_labels(X, dates, rng), lam, warm=warm)
        warm = dk
        null[k] = np.abs(dk).max()
    t_obs = float(np.abs(d_hat).max())
    p_max = float((1 + (null >= t_obs).sum()) / (1 + perms))

    # ── 단계적 하강: 유의한 최대를 빼고 다시 잰다 ─────────────────────────
    p_step: dict[str, float] = {}
    live = np.ones(p, dtype=bool)
    while live.any():
        sub = np.flatnonzero(live)
        _, ds = lasso(y, X[:, sub], lam)
        j = int(np.argmax(np.abs(ds)))
        t = float(abs(ds[j]))
        rng2 = np.random.default_rng(seed)
        nl = np.empty(perms)
        w = None
        for k in range(perms):
            _, dk = lasso(y, _perm_labels(X[:, sub], dates, rng2), lam, warm=w)
            w = dk
            nl[k] = np.abs(dk).max()
        pj = float((1 + (nl >= t).sum()) / (1 + perms))
        p_step[names[sub[j]]] = pj
        if pj >= alpha:
            break                                  # 여기서 멈춘다 - 아래는 판정 안 한다
        live[sub[j]] = False

    keep = np.array([pi[i] >= PI_MIN
                     and p_step.get(names[i], 1.0) < alpha for i in range(p)])
    a_ols, d_ols = post_ols(y, X, keep)

    # λ 민감도: 격자마다 어떤 열이 살아남나
    lam_sens = {f"{lm:g}": sorted(names[i] for i in range(p)
                                  if lasso(y, X, lm)[1][i] != 0.0)
                for lm in lam_grid}
    return {"verdict": "계산됨", "null_kind": "label",
            "att_base": a_ols, "p_max": p_max, "p_step": p_step,
            "pi": {names[i]: round(float(pi[i]), 3) for i in range(p)},
            "selected": {names[i]: float(d_ols[i]) for i in range(p) if keep[i]},
            "lam": lam, "lam_sensitivity": lam_sens, "n": n, "j": p,
            "dropped_collinear": dropped}


def _selfcheck() -> None:
    rng = np.random.default_rng(0)
    n = 300
    X = rng.random((n, 4))
    dates = np.array([f"2026-01-{1 + i % 10:02d}" for i in range(n)])
    # 진짜 조절자는 0번 하나. 나머지는 잡음.
    y = 0.01 + 0.05 * X[:, 0] + rng.normal(0, 0.002, n)

    a, d = lasso(y, X, 0.001)
    assert abs(a - 0.01) < 0.01, a
    assert abs(d[0]) > abs(d[1:]).max(), d           # 진짜가 가장 크다
    # 절편은 벌점이 없다 - λ 를 키워도 0 으로 안 간다
    a_big, d_big = lasso(y, X, 1.0)
    assert (d_big == 0).all() and abs(a_big - y.mean()) < 1e-9

    pi = stability(y, X, dates, 0.001, b=40)
    assert pi[0] >= PI_MIN > pi[1:].max(), pi

    r = moderate(y, X, dates, ["진짜", "잡음1", "잡음2", "잡음3"], perms=60)
    assert r["verdict"] == "계산됨" and r["p_max"] < 0.05, r["p_max"]
    assert "진짜" in r["selected"], r["selected"]
    assert not {k for k in r["selected"] if k.startswith("잡음")}, r["selected"]
    # post-LASSO 는 수축을 되돌린다 - 라쏘 계수보다 크다
    assert abs(r["selected"]["진짜"]) >= abs(d[0]) - 1e-12

    # 조절자가 없으면 maxT 가 유의하지 않다 (위양성 검사)
    y0 = 0.01 + rng.normal(0, 0.002, n)
    r0 = moderate(y0, X, dates, ["a", "b", "c", "d"], perms=60)
    assert not r0["selected"], r0["selected"]


if __name__ == "__main__":                          # pragma: no cover
    _selfcheck()
    print("ok")
