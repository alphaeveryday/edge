"""산업제외 시장 검증 — β=1 가정이 버티나, 제외 후 잔여 연동은 얼마나 남나.

KSIC 가 아직 2건뿐이라 최대 상관 클러스터를 산업 대리로 쓴다.
묻는 것 셋:
  ① 클러스터 구성원의 β — 전체 시장 대비 vs 클러스터제외 시장 대비
  ② 제외 후 잔여 연동 corr(m^(-ind), r̄_c)  — 완전 외생인가
  ③ 전파 층 크기  m^(-i) − m^(-ind)         — 실제로 얼마나 미나
"""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

LOOKBACK, K = 252, 25

c = duckdb.connect()
px = c.execute("SELECT * FROM read_parquet('.tmp/kodex_px.parquet') ORDER BY d").df()
tks = [x for x in px.columns if x != "d"]
R = np.diff(np.log(px[tks].to_numpy(dtype=float)), axis=0)
ok_all = ~np.isnan(R).any(axis=0)
R = R[:, ok_all]
tks = [t for t, o in zip(tks, ok_all) if o]
n_all = R.shape[1]
print(f"수익률 {R.shape[0]}일 × {n_all}종목")

# 시장 차감 잔차로 클러스터 (EXP-003 §5)
M_loo = (R.sum(axis=1, keepdims=True) - R) / (n_all - 1)
U = R - M_loo
win = U[-LOOKBACK:]
C = np.nan_to_num(np.corrcoef(win.T), nan=0.0)
np.fill_diagonal(C, 1.0)
lab = fcluster(linkage(squareform(np.clip(1 - C, 0, 2), checks=False), method="ward"),
               K, criterion="maxclust")
u, cnt = np.unique(lab, return_counts=True)
big = u[np.argmax(cnt)]
mem = lab == big
print(f"최대 클러스터 {mem.sum()}종목 / {n_all}  ({mem.sum()/n_all:.0%})")

X = R[-LOOKBACK:]
# 세 벤치마크
m_loo = (X.sum(axis=1, keepdims=True) - X) / (n_all - 1)          # 나만 제외
S_ind = X[:, mem].sum(axis=1, keepdims=True)
m_excl_raw = (X.sum(axis=1, keepdims=True) - S_ind) / (n_all - mem.sum())   # 산업 전체 제외
m_excl = np.repeat(m_excl_raw, n_all, axis=1)
r_ind = np.repeat(S_ind / mem.sum(), n_all, axis=1)                # 산업 평균


def beta(y, x):
    xc, yc = x - x.mean(), y - y.mean()
    return float((xc @ yc) / (xc @ xc))


b_full = np.array([beta(X[:, j], m_loo[:, j]) for j in np.where(mem)[0]])
b_excl = np.array([beta(X[:, j], m_excl[:, j]) for j in np.where(mem)[0]])
out_full = np.array([beta(X[:, j], m_loo[:, j]) for j in np.where(~mem)[0]])

print("\n① β 분포 (β=1 가정 점검)")
print(f"{'집단':<28}{'중앙값':>8}{'평균':>8}{'p10':>7}{'p90':>7}")
for nm, b in (("클러스터 내 · 전체시장 대비", b_full),
              ("클러스터 내 · 산업제외 대비", b_excl),
              ("클러스터 밖 · 전체시장 대비", out_full)):
    print(f"{nm:<28}{np.median(b):>8.2f}{b.mean():>8.2f}"
          f"{np.percentile(b,10):>7.2f}{np.percentile(b,90):>7.2f}")

print("\n② 제외 후 잔여 연동")
r_c = r_ind[:, 0]
print(f"  corr(전체시장, 산업평균)   = {np.corrcoef(m_loo[:,0], r_c)[0,1]:+.3f}")
print(f"  corr(산업제외시장, 산업평균) = {np.corrcoef(m_excl_raw[:,0], r_c)[0,1]:+.3f}")
print(f"  산업평균 분산 중 산업제외시장이 설명하는 비중 "
      f"{np.corrcoef(m_excl_raw[:,0], r_c)[0,1]**2:.1%}")

print("\n③ 전파 층 크기  (m^(-i) − m^(-ind), 클러스터 구성원 기준)")
spill = m_loo[:, mem] - m_excl[:, mem]
print(f"  |전파| 중앙값 {np.median(np.abs(spill))*100:.3f}%p · "
      f"p90 {np.percentile(np.abs(spill),90)*100:.3f}%p · "
      f"최대 {np.abs(spill).max()*100:.2f}%p")
print(f"  |전파| / |전체시장| 중앙값 "
      f"{np.median(np.abs(spill) / np.maximum(np.abs(m_loo[:, mem]), 1e-9)):.1%}")
