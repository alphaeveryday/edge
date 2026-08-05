"""뉴스 정렬 + 등가중 대조 — 어느 고유 층이 사건을 실제로 집어내나.

[T7]  |고유| 큰 날에 뉴스가 있나 (A·D 공통 고유)
[T8]  산업 초과가 큰 날, 그 산업 몇 종목에 뉴스가 걸리나
      1종목 -> 한 종목이 민 것 / 다수 -> 진짜 산업 사건
[T12] 큰 고유인데 뉴스 0 인 비율 = 설명 실패 상한
[T13] 시총가중 vs 등가중 — 어느 쪽이 사건과 잘 붙나
[T14] m 의 분산 중 지배 종목 몫 / 등가중 베타 대조
"""
import duckdb
import numpy as np

z = np.load(".tmp/ad_layers.npz", allow_pickle=True)
dates, tks, w, lab = z["dates"], list(z["tks"]), z["w"], z["lab"]
ret, m_loo, m_exind, idio, mkt = (z[k] for k in
                                  ("ret", "m_loo", "m_exind", "idio", "mkt"))
ind_avg, N = z["ind_avg"], len(tks)

# ── 등가중 대조군 ────────────────────────────────────────────────────────
we = np.full(N, 1.0 / N)
mkt_e = ret @ we
idio_e = ret - (mkt_e[:, None] - ret * we) / (1 - we)

print("[T14] 시장 지수의 성질")
print(f"  시총가중  HHI {(w**2).sum():.4f}  실효종목수 {1/(w**2).sum():.1f}  "
      f"sd {mkt.std()*100:.2f}%p")
print(f"  등가중    HHI {(we**2).sum():.4f}  실효종목수 {1/(we**2).sum():.1f}  "
      f"sd {mkt_e.std()*100:.2f}%p")
vshare = (w**2 * ret.var(axis=0)) / mkt.var()
o = np.argsort(-vshare)[:3]
print("  var(m) 중 개별 종목 자기분산 몫: " +
      " · ".join(f"{tks[j]} {vshare[j]:.0%}" for j in o) +
      f"  (합 {vshare.sum():.0%} — 나머지는 공분산)")


def beta(y, x):
    xc = x - x.mean()
    return float((xc @ (y - y.mean())) / (xc @ xc))


W = 252
bc = np.array([beta(ret[-W:, j], m_loo[-W:, j]) for j in range(N)])
be = np.array([beta(ret[-W:, j], ((mkt_e[:, None] - ret * we) / (1 - we))[-W:, j])
               for j in range(N)])
print(f"\n  베타 중앙  시총가중 {np.median(bc):.2f}  |  등가중 {np.median(be):.2f}"
      f"   (|b-1|>0.3 비율 {np.mean(np.abs(bc-1)>.3):.0%} vs {np.mean(np.abs(be-1)>.3):.0%})")

# ── 뉴스 ────────────────────────────────────────────────────────────────
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
A, sub = A[rows], np.s_[rows]
print(f"\n뉴스 창 {dates[rows[0]]}~{dates[rows[-1]]} · {len(rows)}거래일 · "
      f"뉴스 붙은 종목 {(A.sum(0)>0).sum()}/{N}")

# 종목별 자기 분포 상위 20% = 뉴스 급증일 (기저율 편차 제거)
thr = np.array([np.percentile(A[:, j][A[:, j] > 0], 80) if (A[:, j] > 0).sum() >= 5
                else np.inf for j in range(N)])
spike = A >= thr[None, :]
live = np.isfinite(thr)
print(f"  급증 판정 가능 종목 {live.sum()} · 급증일 비율 {spike[:, live].mean():.1%}")


def align(x, name):
    """|고유| 상위 10% 날에 뉴스 급증이 몰리나."""
    v = np.abs(x[sub])[:, live]
    s = spike[:, live]
    hi = v >= np.percentile(v, 90, axis=0, keepdims=True)
    p_hi, p_lo = s[hi].mean(), s[~hi].mean()
    r_sp = np.median(v[s]) / np.median(v[~s])
    print(f"  {name:<12} P(뉴스급증 | |고유| 상위10%) {p_hi:.1%}  vs  기저 {p_lo:.1%}"
          f"   lift {p_hi/max(p_lo,1e-9):.2f}x   |  "
          f"|고유| 중앙 급증일/평상 {r_sp:.2f}x")
    return hi, s


print("\n[T7·T13] 고유-뉴스 정렬")
hi_c, S = align(idio, "시총가중")
align(idio_e, "등가중")
align(ret, "무차감 r_i")

print(f"\n[T12] 큰 고유인데 뉴스 0  {1 - A[:, live][hi_c].astype(bool).mean():.0%} "
      f"— 설명 실패 상한 (커버리지 목표 50% 대비)")

print("\n[T8] 산업 초과가 클 때 몇 종목에 뉴스가 걸리나")
exc = (ind_avg - m_exind)[sub]
seen, out = set(), []
for c in np.unique(lab):
    mem = np.where((lab == c) & live)[0]
    if len(mem) < 3 or c in seen:
        continue
    seen.add(c)
    e = np.abs(exc[:, mem[0]])
    hi = e >= np.percentile(e, 90)
    k_hi = spike[:, mem][hi].sum(axis=1)
    k_lo = spike[:, mem][~hi].sum(axis=1)
    out.append((len(mem), k_hi.mean(), k_lo.mean(),
                (k_hi >= 3).mean(), (k_hi == 0).mean()))
out = np.array(out)
print(f"  대상 클러스터 {len(out)}개 (3종목 이상)")
print(f"  초과 상위10% 날 뉴스급증 종목수 평균 {out[:,1].mean():.2f} "
      f"vs 평상 {out[:,2].mean():.2f}   lift {out[:,1].mean()/max(out[:,2].mean(),1e-9):.2f}x")
print(f"  그중 3종목 이상 동시 급증 {out[:,3].mean():.0%}  ·  "
      f"한 종목도 급증 안 함 {out[:,4].mean():.0%}")
