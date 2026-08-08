"""A vs D 시총가중 실증 — 층 구조·베타·뉴스 정렬.

A:  r_i = m^(-i)                      + (r_i - m^(-i))
D:  r_i = m^(-ind) + (m^(-i) - m^(-ind)) + (r_i - m^(-i))
    두 안의 고유 층은 동일하다. D 는 시장 층만 둘로 쪼갠다.

KSIC 미확보(2건) -> 시장차감 잔차 ward K=25 클러스터를 산업 대리로 쓴다.
가중은 07-15~31 스냅샷 14개의 종목별 중앙값을 정적으로 쓴다(원본은 14일뿐).
"""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

K, WIN = 25, 252
con = duckdb.connect()

px = con.execute("SELECT * FROM read_parquet('.tmp/kodex_px.parquet') ORDER BY d").df()
dates = px["d"].to_numpy()[1:]
cols = [c for c in px.columns if c != "d"]
ret = np.diff(np.log(px[cols].to_numpy(dtype=float)), axis=0)

wmap = dict(con.execute(
    "SELECT tk, median(w) FROM read_parquet('.tmp/kodex_w.parquet') "
    "WHERE tk <> 'KRD010010001' GROUP BY 1").fetchall())

keep = [j for j, c in enumerate(cols)
        if not np.isnan(ret[:, j]).any() and c[1:] in wmap]
ret = ret[:, keep]
tks = [cols[j][1:] for j in keep]
w = np.array([wmap[t] for t in tks])
w = w / w.sum()
N = len(tks)
print(f"수익률 {ret.shape[0]}일 x {N}종목 | 최대비중 {w.max():.1%} ({tks[int(w.argmax())]}) "
      f"| 상위5 {np.sort(w)[-5:].sum():.1%} | HHI {(w**2).sum():.4f}")

# ── 시장·클러스터 ────────────────────────────────────────────────────────
mkt = ret @ w                                        # 시총가중 시장
m_loo = (mkt[:, None] - ret * w) / (1 - w)           # A: 자기 제외 재정규화
idio = ret - m_loo                                   # A·D 공통 고유

u = ret[-WIN:] - m_loo[-WIN:]
C = np.nan_to_num(np.corrcoef(u.T), nan=0.0)
np.fill_diagonal(C, 1.0)
lab = fcluster(linkage(squareform(np.clip(1 - C, 0, 2), checks=False), "ward"),
               K, criterion="maxclust")

W_ind = np.array([w[lab == lab[i]].sum() for i in range(N)])
S_ind = np.column_stack([ret[:, lab == lab[i]] @ w[lab == lab[i]] for i in range(N)])
m_exind = (mkt[:, None] - S_ind) / (1 - W_ind)       # D: 산업 전체 제외
spill = m_loo - m_exind                              # D: 전파 층
ind_avg = S_ind / W_ind                              # 산업 시총가중 평균

sz = np.bincount(lab)[1:]
print(f"클러스터 K={K} | 크기 중앙 {int(np.median(sz))} 최대 {sz.max()} "
      f"| 최대 클러스터 비중 {W_ind.max():.1%}")

# ── T1 항등식 · T2 ETF 합산 ──────────────────────────────────────────────
print("\n[T1] 항등식 잔차")
print(f"  A  max|r - (m_loo + idio)|            {np.abs(ret - m_loo - idio).max():.2e}")
print(f"  D  max|r - (m_exind + spill + idio)|  "
      f"{np.abs(ret - m_exind - spill - idio).max():.2e}")

agg_err = (m_loo @ w) - mkt
print("\n[T2] ETF 합산 오차  (Sum w_i·m^(-i) vs m) — A·D 동일")
print(f"  |오차| 중앙 {np.median(np.abs(agg_err))*100:.4f}%p · "
      f"p90 {np.percentile(np.abs(agg_err),90)*100:.4f}%p · "
      f"최대 {np.abs(agg_err).max()*100:.3f}%p")
print(f"  |오차| / |m| 중앙 {np.median(np.abs(agg_err)/np.maximum(np.abs(mkt),1e-9)):.2%}"
      f"   (= 고유 층 가중합이 0이 아닌 정도)")


def beta(y, x):
    xc = x - x.mean()
    return float((xc @ (y - y.mean())) / (xc @ xc))


X, L, E, I, IA = (a[-WIN:] for a in (ret, m_loo, m_exind, idio, ind_avg))
bA = np.array([beta(X[:, j], L[:, j]) for j in range(N)])
bD = np.array([beta(X[:, j], E[:, j]) for j in range(N)])
bS = np.array([beta(IA[:, j], E[:, j]) for j in range(N)])   # 산업평균 vs 산업제외시장

print("\n[T3] 베타 — 층 계수 1이 정당한가")
print(f"{'':<34}{'중앙':>7}{'평균':>7}{'p10':>7}{'p90':>7}{'|b-1|>0.3':>10}")
for nm, b in (("A  r_i ~ m^(-i)", bA), ("D  r_i ~ m^(-ind)", bD),
              ("D  산업평균 ~ m^(-ind)", bS)):
    print(f"{nm:<34}{np.median(b):>7.2f}{b.mean():>7.2f}"
          f"{np.percentile(b,10):>7.2f}{np.percentile(b,90):>7.2f}"
          f"{(np.abs(b-1)>0.3).mean():>9.0%}")

print("\n[T4] 층 직교성 (종목별 corr 의 중앙값)")
for nm, a, b in (("m^(-ind) vs 전파", E, L - E), ("m^(-ind) vs 고유", E, I),
                 ("전파 vs 고유", L - E, I), ("m^(-i) vs 고유 [A]", L, I)):
    r = [np.corrcoef(a[:, j], b[:, j])[0, 1] for j in range(N)
         if b[:, j].std() > 1e-12 and a[:, j].std() > 1e-12]
    print(f"  {nm:<22}{np.median(r):+.3f}   (p10 {np.percentile(r,10):+.2f} "
          f"p90 {np.percentile(r,90):+.2f})")

print("\n[T5] 분산 배분 (종목별 var(층)/var(r) 중앙값)")
vr = X.var(axis=0)
for nm, a in (("m^(-ind)", E), ("전파", L - E), ("m^(-i) [A 시장]", L), ("고유", I)):
    print(f"  {nm:<18}{np.median(a.var(axis=0)/vr):>7.1%}")

sp = np.abs(L - E)
print("\n[T6] 전파 층 크기 (시총가중)")
print(f"  |전파| 중앙 {np.median(sp)*100:.3f}%p · p90 {np.percentile(sp,90)*100:.3f}%p "
      f"· 최대 {sp.max()*100:.2f}%p")
print(f"  |전파| / |m^(-i)| 중앙 "
      f"{np.median(sp/np.maximum(np.abs(L),1e-9)):.1%}   [등가중 실측 6.5%]")
print(f"  corr(m, 산업평균) 중앙        {np.median([np.corrcoef(mkt[-WIN:],IA[:,j])[0,1] for j in range(N)]):+.3f}")
print(f"  corr(m^(-ind), 산업평균) 중앙 {np.median([np.corrcoef(E[:,j],IA[:,j])[0,1] for j in range(N)]):+.3f}")

print("\n[T9] 시장 급변일에 고유가 부푸나 (베타!=1 아티팩트)")
q = np.abs(mkt[-WIN:]) >= np.percentile(np.abs(mkt[-WIN:]), 90)
print(f"  |고유| 중앙  급변일 {np.median(np.abs(I[q]))*100:.3f}%p  vs  "
      f"평상일 {np.median(np.abs(I[~q]))*100:.3f}%p  "
      f"(비 {np.median(np.abs(I[q]))/np.median(np.abs(I[~q])):.2f}x)")

ac = [np.corrcoef(I[:-1, j], I[1:, j])[0, 1] for j in range(N) if I[:, j].std() > 1e-12]
print(f"\n[T10] 고유 층 lag-1 자기상관 중앙 {np.median(ac):+.3f} "
      f"(|rho|>0.15 종목 {np.mean(np.abs(ac)>0.15):.0%}) — 0 이어야 진짜 고유")

print("\n[T11] 지배 종목 과잉차감 — corr(최대비중 종목 고유, 타 종목 고유)")
d0 = int(w.argmax())
same = lab == lab[d0]
cr = np.array([np.corrcoef(I[:, d0], I[:, j])[0, 1] for j in range(N) if j != d0])
msk = np.array([same[j] for j in range(N) if j != d0])
print(f"  같은 클러스터 {msk.sum():>3}종목  중앙 {np.median(cr[msk]):+.3f}")
print(f"  다른 클러스터 {(~msk).sum():>3}종목  중앙 {np.median(cr[~msk]):+.3f}"
      f"   <- 음수면 {tks[d0]}(w={w[d0]:.0%}) 움직임이 남의 고유에 반대로 샌다")

np.savez(".tmp/ad_layers.npz", dates=dates.astype("datetime64[D]").astype(str),
         tks=np.array(tks), w=w, lab=lab, ret=ret, m_loo=m_loo,
         m_exind=m_exind, idio=idio, mkt=mkt, ind_avg=ind_avg)
print("\n-> .tmp/ad_layers.npz")
