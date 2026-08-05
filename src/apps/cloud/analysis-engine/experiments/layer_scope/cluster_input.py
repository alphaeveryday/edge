"""클러스터링 입력 비교 — raw 수익률 상관 vs 시장차감 잔차 상관.

순환은 없다: 시장 성분 m^(-i) 는 클러스터를 몰라도 계산되므로, 잔차 u = r − m 으로
클러스터링한 뒤 그 클러스터로 군집증분을 만들면 된다. 문제는 raw 상관을 쓰면
시장 공통분이 상관을 지배해 average linkage 가 chaining 을 일으킨다는 것.
"""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.metrics import adjusted_rand_score

LOOKBACK, MIN_CL = 252, 5

c = duckdb.connect()
px = c.execute("SELECT * FROM read_parquet('.tmp/kodex_px.parquet') ORDER BY d").df()
tks = [x for x in px.columns if x != "d"]
R = np.diff(np.log(px[tks].to_numpy(dtype=float)), axis=0)

# 시장 차감 잔차 (LOO). 클러스터와 무관하게 계산된다 - 순환 없음.
n_ok = (~np.isnan(R)).sum(axis=1, keepdims=True)
S = np.nansum(R, axis=1, keepdims=True)
M = (S - np.nan_to_num(R)) / np.maximum(n_ok - 1, 1)
U = R - M

print(f"평균 |상관|  raw {np.abs(np.corrcoef(np.nan_to_num(R[-252:]).T)).mean():.3f}"
      f"  ·  잔차 {np.abs(np.corrcoef(np.nan_to_num(U[-252:]).T)).mean():.3f}")


def clusters(X, ti, K, method):
    win = X[max(0, ti - LOOKBACK):ti]
    ok = ~np.isnan(win).any(axis=0)
    C = np.nan_to_num(np.corrcoef(win[:, ok].T), nan=0.0)
    np.fill_diagonal(C, 1.0)
    lab_ok = fcluster(linkage(squareform(np.clip(1 - C, 0, 2), checks=False),
                              method=method), K, criterion="maxclust")
    lab = np.full(len(tks), -1)
    lab[ok] = lab_ok
    return lab


def profile(lab):
    u, n = np.unique(lab[lab >= 0], return_counts=True)
    big = sorted((int(x) for x in n if x >= MIN_CL), reverse=True)
    orphan = int(sum(int(x) for x in n if x < MIN_CL))
    return big, orphan


ti = len(R) - 1
print(f"\n{'입력':<8}{'linkage':<10}{'K':>4}{'유효군집':>9}{'최대':>6}{'중앙':>6}"
      f"{'커버종목':>9}{'고아':>6}")
best = {}
for name, X in (("raw", R), ("잔차", U)):
    for method in ("average", "ward"):
        for K in (25, 40):
            lab = clusters(X, ti, K, method)
            big, orphan = profile(lab)
            cov = sum(big)
            print(f"{name:<8}{method:<10}{K:>4}{len(big):>9}{max(big):>6}"
                  f"{int(np.median(big)):>6}{cov:>9}{orphan:>6}")
            best[(name, method, K)] = lab

print("\n== 안정성 (경과 영업일별 ARI)")
print(f"{'입력':<8}{'linkage':<10}{'K':>4}" + "".join(f"{k:>7}" for k in (1, 5, 10, 21, 63)))
for (name, method, K), lab in best.items():
    X = R if name == "raw" else U
    row = []
    for k in (1, 5, 10, 21, 63):
        a = clusters(X, ti - k, K, method)
        m = (a >= 0) & (lab >= 0)
        row.append(adjusted_rand_score(a[m], lab[m]))
    print(f"{name:<8}{method:<10}{K:>4}" + "".join(f"{v:>7.2f}" for v in row))
