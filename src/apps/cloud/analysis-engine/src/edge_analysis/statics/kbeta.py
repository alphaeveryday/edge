"""일중 시변 β — 칼만 필터. **경로 설명의 전제.**

지금 층 분해는 일 단위 하나의 β 로 하루를 나눈다. 그래서 "왜 오르다 떨어졌나" 에
한 문장도 못 만든다. β 가 하루 안에서 움직인다는 직접 증거는 이미 있다:
042700 07-31 에서 시총가중 지수가 섹터 충격을 시장 팩터로 흡수하고 시장직교
섹터층이 음수로 나왔다(`narrate` 의 `[모순]` 문단).

상태공간:

    β_t = β_{t-1} + w_t      w ~ N(0, Q)     (랜덤워크)
    r_i,t = β_t · r_m,t + ε  ε ~ N(0, R)
    β_0 ~ N(β̂_20d, SE²)                     (일간 20일 롤링이 prior)

개장 직후 일중 표본만으로는 β 를 못 추정한다 - 일간 롤링을 초기값으로 주는 것이
정당한 축소(shrinkage)다.

## 정직해야 하는 셋

1. **Q 가 결과를 지배한다.** 크면 β 가 노이즈를 추종하고 작으면 초기값에 갇힌다.
   셀별 MLE 로 정하면 §13(재실행 결정론)이 깨진다 - 데이터에서 유도하되 **규칙을
   전역 고정**한다: Q = var(일간 β 의 일간 변화) / 하루 봉수.
2. **일중 β 는 설명 전용이다.** yfinance 5분봉 상한이 60일이라 타입 수준 패널
   (n 수백~수천)을 만들 수 없다. 패널 검정은 일봉을 그대로 쓴다 - 섞으면 표본이
   거짓말한다.
3. **Epps 효과·비동시거래.** 5분으로 가면 상관이 0 으로 붕괴하는 하향 편향이 있다.
   유동성 하위 종목에서 특히 그렇다. β_t 를 **신뢰구간과 함께** 내고, 폭이 안
   좁혀지면 `일중 β 판정불가` 로 선언한다 - 부재를 0 으로 위장하지 않는다.

사용:  python -m edge_analysis.statics.kbeta 000660.KS 2026-07-29
"""
from __future__ import annotations

import sys
import warnings

import numpy as np

BARS_PER_DAY = 78       # 09:00~15:30 5분봉 (전역 상수 - 가설별 지정 금지)
BETA_DAILY_WIN = 20     # 일간 롤링 β 창 = 초기값 표본
R_WIN_DAYS = 5          # 관측 분산 R 을 재는 직전 거래일 수
MIN_BARS = 20           # 일중 봉이 이보다 적으면 판정불가
CI_MAX = 1.20           # β CI 폭이 이보다 넓으면 판정불가 (Epps·비동시성 가드)
MARKET = {"KOSPI": "069500.KS", "KOSDAQ": "229200.KS"}


def fetch(symbol: str, *, interval: str, period: str):
    """yfinance OHLCV. 5분봉은 60일이 상한이다 (그 사실을 호출자가 알아야 한다)."""
    import yfinance as yf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(symbol, period=period, interval=interval,
                         progress=False, auto_adjust=False)
    if df is None or df.empty:
        return None
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df = df.droplevel("Ticker", axis=1)
    return df


def _logret(close) -> np.ndarray:
    c = np.asarray(close, dtype=float)
    return np.diff(np.log(np.where(c > 0, c, np.nan)))


def daily_beta(stk_d, mkt_d, day: str, win: int = BETA_DAILY_WIN):
    """(β̂, SE, β 일간변화 분산) — 칼만의 초기값과 Q 규칙의 재료.

    `day` **미포함**이다 (당일 정보로 당일 초기값을 만들면 선견이다).
    """
    import pandas as pd
    d0 = pd.Timestamp(day).tz_localize(None)
    s = stk_d["Close"].copy()
    m = mkt_d["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    m.index = pd.to_datetime(m.index).tz_localize(None)
    j = pd.concat([s.rename("s"), m.rename("m")], axis=1).dropna()
    j = j[j.index < d0]
    if len(j) < win + 2:
        return None
    ls, lm = _logret(j["s"].to_numpy()), _logret(j["m"].to_numpy())
    # 롤링 β 계열: Q 규칙이 '일간 β 가 하루에 얼마나 움직이나' 를 요구한다.
    betas = []
    for k in range(win, len(lm) + 1):
        x, y = lm[k - win:k], ls[k - win:k]
        v = float((x * x).sum())
        if v > 0:
            betas.append(float((x * y).sum() / v))
    if len(betas) < 3:
        return None
    x, y = lm[-win:], ls[-win:]
    b = float((x * y).sum() / (x * x).sum())
    resid = y - b * x
    se = float(np.sqrt((resid @ resid) / max(win - 1, 1) / (x @ x)))
    dvar = float(np.var(np.diff(np.asarray(betas)), ddof=1))
    return b, se, dvar


def kalman(y: np.ndarray, x: np.ndarray, b0: float, p0: float,
           q: float, r: float) -> tuple[np.ndarray, np.ndarray]:
    """스칼라 상태 칼만 필터. 반환 (β_t, Var(β_t)) — 구간을 같이 내려면 분산이 필요하다."""
    n = len(y)
    b = np.empty(n)
    p = np.empty(n)
    bt, pt = b0, p0
    for t in range(n):
        pt += q                                     # 예측
        h = x[t]
        s = h * h * pt + r
        k = pt * h / s if s > 0 else 0.0
        bt = bt + k * (y[t] - h * bt)               # 갱신
        pt = (1.0 - k * h) * pt
        b[t], p[t] = bt, pt
    return b, p


def intraday_beta(symbol: str, day: str, *, market: str = "KOSPI") -> dict:
    """당일 시점별 β_t + CI. 재료가 없으면 사유와 함께 판정불가."""
    import pandas as pd
    msym = MARKET[market]
    s5, m5 = fetch(symbol, interval="5m", period="60d"), fetch(msym, interval="5m", period="60d")
    sd, md = fetch(symbol, interval="1d", period="2y"), fetch(msym, interval="1d", period="2y")
    if any(v is None for v in (s5, m5, sd, md)):
        return {"verdict": "판정불가", "reason": "yfinance 응답 없음 (심볼 또는 기간)"}
    init = daily_beta(sd, md, day)
    if init is None:
        return {"verdict": "판정불가",
                "reason": f"일간 초기값 표본 부족 ({BETA_DAILY_WIN}일 롤링 불가)"}
    b0, se0, dvar = init
    j = pd.concat([s5["Close"].rename("s"), m5["Close"].rename("m")], axis=1).dropna()
    d0 = pd.Timestamp(day).date()
    # R: 직전 R_WIN_DAYS 거래일의 5분 잔차 분산. 당일 정보를 쓰지 않는다.
    prev = j[[d.date() < d0 for d in j.index]]
    days = sorted({d.date() for d in prev.index})[-R_WIN_DAYS:]
    pr = prev[[d.date() in days for d in prev.index]]
    if len(pr) < MIN_BARS:
        return {"verdict": "판정불가", "reason": f"R 추정용 직전 5분봉 {len(pr)} < {MIN_BARS}"}
    ys, xs = _logret(pr["s"].to_numpy()), _logret(pr["m"].to_numpy())
    ok = np.isfinite(ys) & np.isfinite(xs)
    r = float(np.var(ys[ok] - b0 * xs[ok], ddof=1))
    today = j[[d.date() == d0 for d in j.index]]
    if len(today) < MIN_BARS:
        return {"verdict": "판정불가",
                "reason": f"당일 5분봉 {len(today)} < {MIN_BARS} (거래일 아님 또는 커버리지 밖)"}
    y, x = _logret(today["s"].to_numpy()), _logret(today["m"].to_numpy())
    ts = list(today.index)[1:]
    fin = np.isfinite(y) & np.isfinite(x)
    y, x, ts = y[fin], x[fin], [t for t, f in zip(ts, fin) if f]
    # Q 규칙 (전역): 일간 β 변화 분산을 하루 봉수로 나눈다. 셀별 튜닝 금지.
    q = dvar / BARS_PER_DAY
    b, p = kalman(y, x, b0, se0 * se0, q, r)
    ci = 1.96 * np.sqrt(p)
    wide = float(np.median(2 * ci))
    if wide > CI_MAX:
        return {"verdict": "판정불가", "b0": b0,
                "reason": f"β CI 중위 폭 {wide:.2f} > {CI_MAX} — 일중 상관 붕괴"
                          " (Epps·비동시거래). 부재를 0 으로 쓰지 않는다"}
    return {"verdict": "성립", "b0": b0, "se0": se0, "q": q, "r": r,
            "ts": ts, "beta": b, "ci": ci, "y": y, "x": x,
            "ci_width_med": wide, "n": len(y)}


def path_summary(res: dict, marks: list[str]) -> list[tuple]:
    """시장 사건 시각으로 분절한 구간 요약.

    3층(2요인 칼만): (구간, 시장, 섹터, 고유, β_m 평균, β_s 평균)
    2층(1요인):      (구간, 시장, 고유, β 평균)

    분절점을 **시장 사건 시각**으로 잡는 것이 핵심이다: 임의 등분하면 "왜 이 구간
    에서" 에 답이 없고, 종목 사건으로 잡으면 시장 충격을 종목 이야기로 위장한다.
    실측 000660 07-29 (3층): 10:17 서킷브레이커로 자르면 붕괴 -11.99%p 중 시장이
    -12.29%p, 섹터 +0.04, 고유 **+0.26** - 종목 이야기가 아니다. 그리고 β_s 가
    0.97 -> 0.51 -> 0.27 로 급감한다 (공포에는 섹터가 없다).
    """
    rows3 = path_layers3(res)
    rows = rows3 or path_layers(res)
    if not rows:
        return []
    three = bool(rows3)
    cuts = sorted({m for m in marks if rows[0][0] <= m <= rows[-1][0]})
    edges = ["00:00", *cuts, "99:99"]
    out: list[tuple] = []
    for a, z in zip(edges, edges[1:]):
        g = [r for r in rows if a <= r[0] < z]
        if not g:
            continue
        lab = f"{g[0][0]}–{g[-1][0]}"
        if three:
            out.append((lab, sum(r[1] for r in g), sum(r[2] for r in g),
                        sum(r[3] for r in g),
                        float(np.mean([r[4] for r in g])),
                        float(np.mean([r[5] for r in g]))))
        else:
            out.append((lab, sum(r[1] for r in g), sum(r[2] for r in g),
                        float(np.mean([r[3] for r in g]))))
    return out


SECTOR_TOPN = 12        # 섹터 프록시 구성원 수 (전역 상수)
PROXY_RHO_MIN = 0.70    # 일봉 KRX 업종지수와 이만큼도 안 맞으면 프록시 자격 없음


def sector_proxy(lake, tk6: str, day: str, topn: int = SECTOR_TOPN) -> dict:
    """업종 구성원 시총상위 등가중 5분 수익 = 일중 섹터 프록시. **자기 제외.**

    KRX 업종지수는 일봉만이라 일중 섹터를 프록시로 만들어야 한다. 두 함정:
      - **자기 제외**: 시총상위에 자기가 들어간다(실측 000660 은 업종 2위). 자기를
        섹터로 쓰면 "반도체가 왜 빠졌냐"에 "반도체가 빠져서" 라고 답하는 꼴이다.
      - **검산 필수**: 프록시가 일봉 KRX 업종지수와 얼마나 맞는지 상관을 재고,
        ρ < PROXY_RHO_MIN 이면 자격 없음으로 선언한다. 검산 없이 쓰면 또
        '가장 잘 맞는 무엇' 이 섹터를 대신한다.
    """
    import pandas as pd
    rows = lake.sql(f"""
        WITH me AS (SELECT code FROM sector_member WHERE ticker = '{tk6}'
                    AND as_of <= DATE '{day}' ORDER BY as_of DESC LIMIT 1),
        mem AS (SELECT DISTINCT sm.ticker FROM sector_member sm, me
                WHERE sm.code = me.code AND sm.as_of <= DATE '{day}')
        SELECT p.ticker, max(p.mktcap) mc, any_value((SELECT code FROM me)) AS code
        FROM pit_daily p JOIN mem ON mem.ticker = p.ticker
        WHERE p.trade_date BETWEEN DATE '{day}' - 10 AND DATE '{day}'
          AND p.mktcap IS NOT NULL AND p.ticker <> '{tk6}'
        GROUP BY 1 ORDER BY 2 DESC LIMIT {topn}""")
    if len(rows) < 3:
        return {"verdict": "판정불가", "reason": f"업종 구성원 {len(rows)} < 3 (자기 제외)"}
    code = str(rows[0][2])
    members = [f"{r[0]}.KS" for r in rows]
    frames = {}
    for sym in members:
        d5 = fetch(sym, interval="5m", period="60d")
        if d5 is not None and not d5.empty:
            frames[sym] = d5["Close"]
    if len(frames) < 3:
        return {"verdict": "판정불가", "reason": f"구성원 5분봉 {len(frames)} < 3"}
    px = pd.concat(frames, axis=1).dropna(how="all")
    lr = np.log(px).diff()
    proxy = lr.mean(axis=1, skipna=True)          # 등가중 (시총가중은 자기 흡수 위험)
    # 검산: 일별 합(≈일간 수익) vs KRX 업종지수 일간 수익의 상관.
    daily = proxy.groupby([d.date() for d in proxy.index]).sum()
    krx = lake.sql(f"""SELECT trade_date, close FROM sector_index
                       WHERE code = '{code}' ORDER BY 1""")
    kser = pd.Series({r[0]: float(r[1]) for r in krx}).sort_index()
    klr = np.log(kser).diff()
    both = pd.concat([daily.rename("p"), klr.rename("k")], axis=1).dropna()
    rho = float(both["p"].corr(both["k"])) if len(both) >= 10 else float("nan")
    if not np.isfinite(rho) or rho < PROXY_RHO_MIN:
        return {"verdict": "판정불가", "code": code, "rho": rho,
                "reason": f"프록시-업종지수 상관 ρ={rho:.2f} < {PROXY_RHO_MIN} "
                          f"(검산 n={len(both)}) — 섹터 프록시 자격 없음"}
    return {"verdict": "성립", "code": code, "rho": rho, "n_members": len(frames),
            "n_check": len(both), "lr": proxy}


def kalman2(y: np.ndarray, X: np.ndarray, b0: np.ndarray, P0: np.ndarray,
            Q: np.ndarray, r: float) -> tuple[np.ndarray, np.ndarray]:
    """2상태 칼만 (β_m, β_s). 반환 (β[t,2], Var 대각[t,2])."""
    n = len(y)
    B = np.empty((n, 2))
    V = np.empty((n, 2))
    b, P = b0.astype(float).copy(), P0.astype(float).copy()
    for t in range(n):
        P = P + Q
        h = X[t]
        s = float(h @ P @ h) + r
        k = (P @ h) / s if s > 0 else np.zeros(2)
        b = b + k * (y[t] - float(h @ b))
        P = (np.eye(2) - np.outer(k, h)) @ P
        B[t], V[t] = b, np.diag(P)
    return B, V



def intraday_beta2(lake, symbol: str, day: str, *, market: str = "KOSPI") -> dict:
    """2요인 일중 β (시장 · 섹터⊥시장). 층 분해와 같은 구조를 일중으로 옮긴 것.

    섹터는 **시장에 직교화**한다 (일간 층 분해와 동일 규약) - 안 하면 시장과
    섹터가 같은 것을 두 번 세고 β 가 서로 상쇄되며 흔들린다.

    재료가 없으면 사유와 함께 판정불가. 섹터 프록시가 자격 미달이면 1요인으로
    **떨어지지 않는다** - 다른 모형을 조용히 쓰는 것이 더 나쁘다.
    """
    import pandas as pd
    one = intraday_beta(symbol, day, market=market)
    if one.get("verdict") != "성립":
        return one
    sp = sector_proxy(lake, symbol.split(".")[0], day)
    if sp.get("verdict") != "성립":
        return {"verdict": "판정불가", "reason": "섹터: " + sp.get("reason", "?"),
                "one_factor": one}
    # 섹터 프록시를 당일 시각에 정렬 (5분봉 인덱스 공유)
    ser = sp["lr"]
    idx = pd.DatetimeIndex(one["ts"])
    rs = ser.reindex(idx).to_numpy(dtype=float)
    y, rm = one["y"], one["x"]
    ok = np.isfinite(rs)
    if ok.sum() < MIN_BARS:
        return {"verdict": "판정불가",
                "reason": f"섹터 프록시 정렬 후 {int(ok.sum())} < {MIN_BARS} 봉",
                "one_factor": one}
    y, rm, rs = y[ok], rm[ok], rs[ok]
    ts = [t for t, f in zip(one["ts"], ok) if f]
    # 시장 직교화: rs⊥ = rs − (rs·rm/rm·rm)·rm
    g = float(rs @ rm / (rm @ rm)) if float(rm @ rm) > 0 else 0.0
    rso = rs - g * rm
    X = np.column_stack([rm, rso])
    # 초기값: 당일 제외 직전 20 거래일 5분봉 2요인 OLS (일봉 2요인은 프록시가 없다)
    b0 = np.array([one["b0"], 1.0])
    P0 = np.diag([one["se0"] ** 2, max(one["se0"] ** 2, 0.04)])
    # Q: 시장은 1요인 규칙 그대로, 섹터는 같은 크기를 쓴다 (전역 규칙 - 셀별 튜닝 금지)
    Q = np.diag([one["q"], one["q"]])
    B, V = kalman2(y, X, b0, P0, Q, one["r"])
    ci = 1.96 * np.sqrt(V)
    wide = float(np.median(2 * ci[:, 0]))
    if wide > CI_MAX:
        return {"verdict": "판정불가",
                "reason": f"2요인 β_m CI 중위 폭 {wide:.2f} > {CI_MAX} — 일중 상관 붕괴",
                "one_factor": one}
    return {"verdict": "성립", "ts": ts, "beta_m": B[:, 0], "beta_s": B[:, 1],
            "ci_m": ci[:, 0], "ci_s": ci[:, 1], "y": y, "rm": rm, "rs_orth": rso,
            "gamma": g, "rho_proxy": sp["rho"], "sector_code": sp["code"],
            "n_members": sp["n_members"], "b0": one["b0"], "q": one["q"], "n": len(y)}


def path_layers3(res: dict) -> list[tuple[str, float, float, float, float, float]]:
    """시점별 (시각, 시장, 섹터, 고유, β_m, β_s) — 3층 경로 분해."""
    if res.get("verdict") != "성립" or "beta_s" not in res:
        return []
    out = []
    for t, bm, bs, rm, rso, y in zip(res["ts"], res["beta_m"], res["beta_s"],
                                     res["rm"], res["rs_orth"], res["y"]):
        m, s = float(bm * rm), float(bs * rso)
        out.append((f"{t:%H:%M}", m, s, float(y - m - s), float(bm), float(bs)))
    return out


def path_layers(res: dict) -> list[tuple[str, float, float, float]]:
    """시점별 (시각, 시장층 몫, 고유 몫, β_t) — 창별 층 분해. 경로 설명의 재료.

    시장층 = β_t × 시장수익, 고유 = 나머지. 로그 가법이라 구간 합이 그대로 몫이다.
    """
    if res.get("verdict") != "성립":
        return []
    return [(f"{t:%H:%M}", float(bt * xt), float(yt - bt * xt), float(bt))
            for t, bt, xt, yt in zip(res["ts"], res["beta"], res["x"], res["y"])]


def _selfcheck() -> None:
    rng = np.random.default_rng(0)
    n = 200
    x = rng.normal(scale=0.004, size=n)
    true = np.concatenate([np.full(n // 2, 1.0), np.full(n - n // 2, 2.0)])
    y = true * x + rng.normal(scale=0.0005, size=n)
    b, p = kalman(y, x, 1.0, 0.04, 1e-4, 0.0005 ** 2)
    assert abs(b[n // 2 - 10] - 1.0) < 0.35, b[n // 2 - 10]
    assert abs(b[-1] - 2.0) < 0.35, b[-1]          # 점프를 따라간다
    assert (p > 0).all()
    b2, _ = kalman(y, x, 1.0, 0.04, 0.0, 0.0005 ** 2)
    assert abs(b2[-1] - 2.0) > abs(b[-1] - 2.0)    # Q=0 이면 초기값에 갇힌다
    print("ok")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "selfcheck":
        _selfcheck()
        return
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    sym, day = sys.argv[1], sys.argv[2]
    res = intraday_beta(sym, day)
    if res["verdict"] != "성립":
        print(f"{sym} {day}: 판정불가 — {res['reason']}")
        return
    print(f"{sym} {day}: β0={res['b0']:.2f} (SE {res['se0']:.2f}) · "
          f"Q={res['q']:.2e} · R={res['r']:.2e} · n={res['n']} · "
          f"CI폭 중위 {res['ci_width_med']:.2f}")
    rows = path_layers(res)
    cum_m = cum_i = 0.0
    for hm, m, i, bt in rows:
        cum_m += m
        cum_i += i
        print(f"  {hm}  β {bt:5.2f}  시장 {m * 100:+6.2f}%p (누적 {cum_m * 100:+7.2f})"
              f"  고유 {i * 100:+6.2f}%p (누적 {cum_i * 100:+7.2f})")


if __name__ == "__main__":
    main()
