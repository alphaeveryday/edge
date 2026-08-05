"""층 분해 커버리지 실증 v2 — 상관 클러스터 기반 군집증분 층 포함.

분해:  r_i = m^(-i) + (r̄_c^(-i) − m^(-i)) + (r_i − r̄_c^(-i))
스코프: (클러스터, 군집증분) 또는 (종목, 고유)
질문:  군집+고유 순 기여 50%를 덮는 데 스코프가 몇 개 필요한가.
       그리고 군집 스코프의 캐시 이득(1워크플로우가 종목 몇 개를 덮나)은 얼마인가.
"""
import duckdb
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

LOOKBACK = 252
MIN_CL = 5
COVER = 0.50

c = duckdb.connect()
px = c.execute("SELECT * FROM read_parquet('.tmp/kodex_px.parquet') ORDER BY d").df()
wt = c.execute("SELECT * FROM read_parquet('.tmp/kodex_w.parquet')").df()

dates = px["d"].values
tks = [x for x in px.columns if x != "d"]
P = px[tks].to_numpy(dtype=float)
R = np.diff(np.log(P), axis=0)                       # (T-1, N)
rdates = dates[1:]
print(f"수익률 행렬 {R.shape}  {rdates[0]} ~ {rdates[-1]}")

wdays = sorted(wt["d"].unique())
print(f"보유비중 날짜 {len(wdays)}: {wdays[0]} ~ {wdays[-1]}")


def cluster_at(t_idx, k_target):
    """[t-252, t-1] 수익률 상관 → 계층 클러스터. 반환: 라벨 배열 (N,)"""
    win = R[max(0, t_idx - LOOKBACK):t_idx]
    ok = ~np.isnan(win).any(axis=0)
    C = np.corrcoef(win[:, ok].T)
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    D = squareform(np.clip(1.0 - C, 0, 2), checks=False)
    lab_ok = fcluster(linkage(D, method="average"), k_target, criterion="maxclust")
    lab = np.full(len(tks), -1)
    lab[ok] = lab_ok
    # 작은 클러스터는 군집 자격 없음 → -1 (고유로 흡수)
    for u, n in zip(*np.unique(lab_ok, return_counts=True)):
        if n < MIN_CL:
            lab[(lab == u)] = -1
    return lab


def decompose(r, w, lab):
    """LOO nested means. 반환 (mkt, inc, idio) 각 (N,)"""
    ok = ~np.isnan(r) & (w > 0)
    n = ok.sum()
    S = np.nansum(r[ok])
    mkt = np.full(len(r), np.nan)
    mkt[ok] = (S - r[ok]) / (n - 1)
    inc = np.zeros(len(r))
    for u in np.unique(lab[ok]):
        if u < 0:
            continue
        m = ok & (lab == u)
        nc = m.sum()
        if nc < MIN_CL:
            continue
        Sc = r[m].sum()
        inc[m] = (Sc - r[m]) / (nc - 1) - mkt[m]
    idio = np.full(len(r), np.nan)
    idio[ok] = r[ok] - mkt[ok] - inc[ok]
    return mkt, inc, idio, ok


for K in (8, 15, 25):
    print(f"\n{'='*78}\n클러스터 목표 K={K}\n{'='*78}")
    print(f"{'날짜':<12}{'ETF%p':>8}{'시장':>7}{'군집':>7}{'고유':>7}{'시장지배':>9}"
          f"{'스코프':>7}{'군집스':>7}{'종목스':>7}{'덮은종목':>9}")
    need, cl_used, stk_used, dominant_n = [], [], [], 0
    for wd in wdays:
        ti = int(np.searchsorted(rdates, np.datetime64(wd)))
        if ti <= LOOKBACK or ti >= len(rdates):
            continue
        wmap = dict(zip(wt[wt["d"] == wd]["tk"], wt[wt["d"] == wd]["w"]))
        w = np.array([wmap.get(t[1:], 0.0) for t in tks], dtype=float)
        r = R[ti]
        if (~np.isnan(r) & (w > 0)).sum() < 50:      # 거래일 정렬 실패 → 건너뛴다
            print(f"{str(wd)[:10]:<12}  건너뜀 (유효 {(~np.isnan(r) & (w > 0)).sum()})")
            continue
        lab = cluster_at(ti, K)
        mkt, inc, idio, ok = decompose(r, w, lab)

        assert np.nanmax(np.abs(mkt[ok] + inc[ok] + idio[ok] - r[ok])) < 1e-12

        c_mkt = float(np.nansum(w[ok] * mkt[ok]))
        c_inc = float(np.nansum(w[ok] * inc[ok]))
        c_idio = float(np.nansum(w[ok] * idio[ok]))
        tot_abs = abs(c_mkt) + abs(c_inc) + abs(c_idio)
        share = abs(c_mkt) / tot_abs if tot_abs else 0.0
        dom = share >= 0.50
        dominant_n += dom

        # 스코프 후보: (클러스터, 군집증분) + (종목, 고유)
        cand = []
        for u in np.unique(lab[ok]):
            if u < 0:
                continue
            m = ok & (lab == u)
            cand.append(("군집", int(u), float((w[m] * inc[m]).sum()), int(m.sum())))
        for i in np.where(ok)[0]:
            cand.append(("고유", i, float(w[i] * idio[i]), 1))

        target = c_inc + c_idio
        if abs(target) < 1e-12:
            continue
        sgn = np.sign(target)
        cand.sort(key=lambda x: -sgn * x[2])
        acc, k, nc, ns, cov_stk = 0.0, 0, 0, 0, 0
        for kind, _, ctr, size in cand:
            acc += ctr
            k += 1
            cov_stk += size
            nc += kind == "군집"
            ns += kind == "고유"
            if abs(acc) >= abs(target) * COVER and np.sign(acc) == sgn:
                break
        need.append(k); cl_used.append(nc); stk_used.append(ns)
        print(f"{str(wd)[:10]:<12}{(c_mkt+c_inc+c_idio)*100:>8.2f}{c_mkt*100:>7.2f}"
              f"{c_inc*100:>7.2f}{c_idio*100:>7.2f}{share:>8.0%}{'*' if dom else ' '}"
              f"{k:>7}{nc:>7}{ns:>7}{cov_stk:>9}")

    a = np.array(need)
    print(f"\n스코프 수  중앙값 {np.median(a):.0f} · 평균 {a.mean():.1f} · "
          f"범위 [{a.min()}, {a.max()}] · p90 {np.percentile(a,90):.0f}")
    print(f"구성  군집 스코프 {np.mean(cl_used):.1f} · 종목 스코프 {np.mean(stk_used):.1f}")
    print(f"시장 지배일 {dominant_n}/{len(need)}")
