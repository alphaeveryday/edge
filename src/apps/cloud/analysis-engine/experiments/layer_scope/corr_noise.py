"""상관행렬 추정 노이즈 — 일봉 252일로 200종목 상관을 잴 수 있나.

p/n = 200/252 = 0.79. Marchenko-Pastur 상한 밖 고유값만 신호이고 나머지는 잡음이다.
잡음이 지배하면 클러스터가 매일 흔들린다 → 캐시가 무의미해진다.
처방 후보: (a) Ledoit-Wolf 축소, (b) 더 짧은 봉으로 표본 늘리기(Epps 편의 대가).
"""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.covariance import LedoitWolf
from sklearn.metrics import adjusted_rand_score

LOOKBACK, MIN_CL, K = 252, 5, 25

c = duckdb.connect()
px = c.execute("SELECT * FROM read_parquet('.tmp/kodex_px.parquet') ORDER BY d").df()
tks = [x for x in px.columns if x != "d"]
R = np.diff(np.log(px[tks].to_numpy(dtype=float)), axis=0)
n_ok = (~np.isnan(R)).sum(axis=1, keepdims=True)
U = R - (np.nansum(R, axis=1, keepdims=True) - np.nan_to_num(R)) / np.maximum(n_ok - 1, 1)

ti = len(U) - 1
win = U[ti - LOOKBACK:ti]
ok = ~np.isnan(win).any(axis=0)
X = win[:, ok]
n, p = X.shape
print(f"표본 n={n} · 종목 p={p} · p/n = {p/n:.2f}")

C = np.corrcoef(X.T)
ev = np.sort(np.linalg.eigvalsh(C))[::-1]
q = p / n
mp_hi = (1 + np.sqrt(q)) ** 2                      # Marchenko-Pastur 상한 (단위분산)
sig = int((ev > mp_hi).sum())
print(f"MP 상한 λ+ = {mp_hi:.2f} · 상한 밖 고유값 {sig}개 / {p}")
print(f"  상위 5 고유값: {np.round(ev[:5], 1)}")
print(f"  신호 분산 비중 {ev[:sig].sum()/ev.sum():.1%} · 잡음 {1-ev[:sig].sum()/ev.sum():.1%}")


def clus(corr):
    return fcluster(linkage(squareform(np.clip(1 - corr, 0, 2), checks=False),
                            method="ward"), K, criterion="maxclust")


def shrunk(Y):
    S = LedoitWolf().fit(Y).covariance_
    d = np.sqrt(np.diag(S))
    return S / np.outer(d, d)


print("\n== 축소 전후 클러스터 안정성 (ARI, 오늘 대비)")
print(f"{'경과일':>7}{'원본':>8}{'LW축소':>8}")
base_raw, base_lw = clus(C), clus(shrunk(X))
for k in (1, 5, 10, 21, 63):
    w2 = U[ti - k - LOOKBACK:ti - k]
    ok2 = ~np.isnan(w2).any(axis=0)
    m = ok & ok2
    Y = w2[:, ok2]
    a_raw, a_lw = clus(np.corrcoef(Y.T)), clus(shrunk(Y))
    sel, sel2 = m[ok], m[ok2]
    print(f"{k:>7}{adjusted_rand_score(a_raw[sel2], base_raw[sel]):>8.2f}"
          f"{adjusted_rand_score(a_lw[sel2], base_lw[sel]):>8.2f}")

print("\n== 클러스터 모양")
for nm, lab in (("원본", base_raw), ("LW축소", base_lw)):
    _, cnt = np.unique(lab, return_counts=True)
    big = sorted((int(x) for x in cnt if x >= MIN_CL), reverse=True)
    print(f"  {nm:<7} 유효군집 {len(big):>2} · 최대 {max(big):>3} · 커버 {sum(big):>3}/{p}")

print("\n== 룩백을 늘리면 (표본 확보의 다른 길)")
for lb in (252, 378, 504):
    if ti - lb < 0:
        print(f"  {lb:>4}일: 데이터 부족 (보유 {ti}일)")
        continue
    w = U[ti - lb:ti]
    o = ~np.isnan(w).any(axis=0)
    Y = w[:, o]
    q2 = Y.shape[1] / Y.shape[0]
    e = np.sort(np.linalg.eigvalsh(np.corrcoef(Y.T)))[::-1]
    s = int((e > (1 + np.sqrt(q2)) ** 2).sum())
    print(f"  {lb:>4}일: p/n={q2:.2f} · 신호 고유값 {s} · 신호 분산 {e[:s].sum()/e.sum():.1%}")
