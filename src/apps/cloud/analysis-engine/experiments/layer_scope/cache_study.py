"""군집 캐시 실증 — 클러스터가 며칠이나 같은가, 그리고 캐시 이득은 몇 배인가."""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sklearn.metrics import adjusted_rand_score

LOOKBACK, MIN_CL, K = 252, 5, 25

c = duckdb.connect()
px = c.execute("SELECT * FROM read_parquet('.tmp/kodex_px.parquet') ORDER BY d").df()
tks = [x for x in px.columns if x != "d"]
R = np.diff(np.log(px[tks].to_numpy(dtype=float)), axis=0)
rdates = px["d"].values[1:]


def cluster_at(ti):
    win = R[max(0, ti - LOOKBACK):ti]
    ok = ~np.isnan(win).any(axis=0)
    C = np.nan_to_num(np.corrcoef(win[:, ok].T), nan=0.0)
    np.fill_diagonal(C, 1.0)
    lab_ok = fcluster(linkage(squareform(np.clip(1 - C, 0, 2), checks=False),
                              method="average"), K, criterion="maxclust")
    lab = np.full(len(tks), -1)
    lab[ok] = lab_ok
    return lab


base = len(rdates) - 1
idxs = [base - k for k in (0, 1, 2, 5, 10, 21, 42, 63, 126, 252)]
labs = {k: cluster_at(base - k) for k in (0, 1, 2, 5, 10, 21, 42, 63, 126, 252)}

print("== 클러스터 안정성 (오늘 대비 ARI, 1.0 = 동일)")
print(f"{'경과일':>7}{'ARI':>8}{'라벨동일%':>10}")
b = labs[0]
for k in (1, 2, 5, 10, 21, 42, 63, 126, 252):
    a = labs[k]
    m = (a >= 0) & (b >= 0)
    print(f"{k:>7}{adjusted_rand_score(a[m], b[m]):>8.3f}"
          f"{(a[m] == b[m]).mean() * 100:>9.0f}%")

print("\n== 클러스터 크기 분포 (오늘)")
u, n = np.unique(b[b >= 0], return_counts=True)
n = sorted(n, reverse=True)
print(f"  클러스터 {len(u)}개 · 크기 {n}")
print(f"  최대 {max(n)} · 중앙값 {int(np.median(n))} · MIN_CL={MIN_CL} 미만 "
      f"{sum(1 for x in n if x < MIN_CL)}개")

print("\n== 캐시 이득 (한 셀 = ETF 1일 분석)")
big = [x for x in n if x >= MIN_CL]
print(f"  군집 스코프 1개가 덮는 종목: 중앙값 {int(np.median(big))} · 최대 {max(big)}")
print(f"  전 구성종목을 고유 스코프로 다루면: {len(tks)} 워크플로우")
print(f"  군집 층으로 접으면:              {len(big)} 워크플로우  "
      f"({len(tks)/len(big):.0f}배 축소)")
