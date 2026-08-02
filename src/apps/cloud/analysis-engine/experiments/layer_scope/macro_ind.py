"""매크로 충격과 산업 층 — 등가중 반론 검정.

M1 시총가중 지수는 '삼성 사건'과 '공통 충격'을 구분하나
   지수가 말하는 시장 이동 vs 나머지 195종목에 실제로 일어난 이동
M2 공통성 — 시장 층이 설명하는 종목 수. 진짜 공통충격이면 다수가 따라간다
M3 등가중 + 산업 초과 층 (원래 nested means 3층) 재검정 — 시총가중에서만 기각했다
M4 산업구조 뭉개짐 정량 — 등가중에서 산업 실효비중이 얼마나 왜곡되나
"""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

z = np.load(".tmp/ad_layers.npz", allow_pickle=True)
dates, tks, w, ret = z["dates"], list(z["tks"]), z["w"], z["ret"]
N, W = ret.shape[1], 252
we = np.full(N, 1.0 / N)
mkt_c, mkt_e = ret @ w, ret @ we
m_c = (mkt_c[:, None] - ret * w) / (1 - w)
m_e = (mkt_e[:, None] - ret * we) / (1 - we)
idio_c, idio_e = ret - m_c, ret - m_e
s = int(w.argmax())

print("[M1] 지배 종목이 크게 움직인 날, 지수는 무엇을 말하나")
hi = np.abs(idio_e[:, s]) >= np.percentile(np.abs(idio_e[:, s]), 90)
oth = np.delete(np.arange(N), s)
real = ret[:, oth].mean(axis=1)          # 나머지 195종목에 실제로 일어난 일
print(f"  {tks[s]} 고유 상위10% {hi.sum()}일 — 그날 평균")
print(f"    {tks[s]} 수익률        {ret[hi, s].mean()*100:+.2f}%p")
print(f"    시총가중 지수 이동    {mkt_c[hi].mean()*100:+.2f}%p")
print(f"    등가중  지수 이동     {mkt_e[hi].mean()*100:+.2f}%p")
print(f"    나머지 195종목 실제   {real[hi].mean()*100:+.2f}%p  <- 진실")
print(f"  |시총지수 - 진실| 중앙 {np.median(np.abs(mkt_c[hi]-real[hi]))*100:.3f}%p"
      f"  ·  |등가지수 - 진실| 중앙 {np.median(np.abs(mkt_e[hi]-real[hi]))*100:.3f}%p")
print(f"  전체일 기준        {np.median(np.abs(mkt_c-real))*100:.3f}%p"
      f"  ·  {np.median(np.abs(mkt_e-real))*100:.3f}%p")

print("\n[M2] 시장 층이 클 때 실제로 몇 종목이 따라가나 (공통성)")
for nm, mk in (("시총가중", mkt_c), ("등가중", mkt_e)):
    big = np.abs(mk) >= np.percentile(np.abs(mk), 90)
    frac = np.array([(np.sign(ret[i]) == np.sign(mk[i])).mean() for i in np.where(big)[0]])
    r2 = np.median([np.corrcoef(ret[-W:, j], mk[-W:])[0, 1] ** 2 for j in range(N)])
    print(f"  {nm:<8} 급변일 동조 종목비율 중앙 {np.median(frac):.0%}"
          f"  |  종목별 R² 중앙 {r2:.3f}  |  지수 sd {mk.std()*100:.2f}%p")

# ── 등가중 클러스터 ──────────────────────────────────────────────────────
u = ret[-W:] - m_e[-W:]
C = np.nan_to_num(np.corrcoef(u.T), nan=0.0)
np.fill_diagonal(C, 1.0)
lab = fcluster(linkage(squareform(np.clip(1 - C, 0, 2), checks=False), "ward"),
               25, criterion="maxclust")
# nested means 3층: m_eq^(-i) + (산업평균^(-i) - m_eq^(-i)) + (r_i - 산업평균^(-i))
cavg = np.zeros_like(ret)
for i in range(N):
    mm = (lab == lab[i]) & (np.arange(N) != i)
    cavg[:, i] = ret[:, mm].mean(axis=1) if mm.sum() else m_e[:, i]
ind_layer = cavg - m_e
idio3 = ret - cavg
sz = np.bincount(lab)[1:]
print(f"\n[M3] 등가중 nested means 3층  (K=25, 크기 중앙 {int(np.median(sz))} 최대 {sz.max()})")
print(f"  항등식 잔차 {np.abs(ret - m_e - ind_layer - idio3).max():.1e}")
vr = ret[-W:].var(axis=0)
for nm, a in (("시장", m_e), ("산업", ind_layer), ("고유", idio3)):
    print(f"  {nm} 분산몫 {np.median(a[-W:].var(axis=0)/vr):>6.1%}"
          f"   |층| 중앙 {np.median(np.abs(a))*100:.3f}%p")
o1 = np.median([np.corrcoef(m_e[-W:, j], ind_layer[-W:, j])[0, 1] for j in range(N)])
o2 = np.median([np.corrcoef(ind_layer[-W:, j], idio3[-W:, j])[0, 1] for j in range(N)])
print(f"  corr(시장,산업) {o1:+.3f}  corr(산업,고유) {o2:+.3f}")

# ── 뉴스 정렬 (횡단면) ──────────────────────────────────────────────────
con = duckdb.connect()
nw = con.execute("SELECT CAST(d AS VARCHAR) d, tk, n_art FROM "
                 "read_parquet('.tmp/news_tk.parquet')").fetchall()
di = {d: i for i, d in enumerate(dates)}
ti = {t: j for j, t in enumerate(tks)}
A = np.zeros_like(ret)
for d, t, n in nw:
    if d in di and t in ti:
        A[di[d], ti[t]] = n
rows = np.array([i for d, i in di.items() if "2026-04-25" <= d <= "2026-07-31"])
A = A[rows]
thr = np.array([np.percentile(A[:, j][A[:, j] > 0], 80) if (A[:, j] > 0).sum() >= 5
                else np.inf for j in range(N)])
live = np.isfinite(thr)
S = (A >= thr[None, :])[:, live]
rng = np.random.default_rng(0)


def xs(x):
    v = np.abs(x[rows])[:, live]
    h = v >= np.percentile(v, 90, axis=1, keepdims=True)
    return np.array([S[i][h[i]].mean() for i in range(len(rows))])


print(f"\n  뉴스 횡단면 정렬 (기저 {S.mean():.1%})")
base = xs(idio_e)
for nm, x in (("2층 고유 (r-m_eq)", idio_e), ("3층 고유 (r-산업평균)", idio3),
              ("3층 산업 층", ind_layer)):
    v = xs(x)
    d = v - base
    idx = rng.integers(0, len(v), (5000, len(v)))
    p = float(min((d[idx].mean(1) <= 0).mean(), (d[idx].mean(1) >= 0).mean()) * 2)
    print(f"    {nm:<22}{v.mean():.1%}   vs 2층고유 {d.mean()*100:+.2f}%p (p={p:.3f})")

print("\n[M4] 산업구조 왜곡 — 클러스터별 실효 비중")
print(f"{'클러스터':>8}{'종목':>5}{'시총가중':>9}{'등가중':>8}{'배율':>7}")
for c in np.unique(lab)[:8]:
    mm = lab == c
    a, b = w[mm].sum(), we[mm].sum()
    print(f"{c:>8}{mm.sum():>5}{a:>8.1%}{b:>8.1%}{a/b:>7.2f}x")
tot = np.array([[w[lab == c].sum(), we[lab == c].sum()] for c in np.unique(lab)])
print(f"  최대 왜곡 {np.max(tot[:,0]/tot[:,1]):.1f}x  ·  "
      f"시총 최대 클러스터 {tot[:,0].max():.0%} vs 등가 {tot[np.argmax(tot[:,0]),1]:.0%}")
