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


def path_summary(res: dict, marks: list[str]) -> list[tuple[str, float, float, float]]:
    """시장 사건 시각으로 분절한 (구간, 시장 몫, 고유 몫, β 평균).

    분절점을 **시장 사건 시각**으로 잡는 것이 핵심이다: 임의 등분하면 "왜 이 구간
    에서" 에 답이 없고, 종목 사건으로 잡으면 시장 충격을 종목 이야기로 위장한다.
    실측 000660 07-29: 10:17 코스피 서킷브레이커로 분절하면 붕괴 -13.00%p 중
    시장이 -13.37%p, 고유는 +0.37%p 라는 것이 드러난다 - 종목 이야기가 아니다.
    """
    rows = path_layers(res)
    if not rows:
        return []
    cuts = sorted({m for m in marks if rows[0][0] <= m <= rows[-1][0]})
    edges = ["00:00", *cuts, "99:99"]
    out: list[tuple[str, float, float, float]] = []
    for a, z in zip(edges, edges[1:]):
        g = [r for r in rows if a <= r[0] < z]
        if not g:
            continue
        out.append((f"{g[0][0]}–{g[-1][0]}",
                    sum(r[1] for r in g), sum(r[2] for r in g),
                    float(np.mean([r[3] for r in g]))))
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
