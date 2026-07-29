"""추론 원시함수 — 순수. I/O 없음.

`storm` 실험에서 승격했다. 여기 있는 것은 전부 배열 in / 숫자 out 이고,
데이터 접근은 `edge_analysis.adapters` 가 담당한다 — 그래야 클라우드에서
같은 로직이 돈다(로컬 DuckDB·절대경로 의존이 실험판의 이식을 막고 있었다).

**`placebo` 가 이 모듈의 핵심이다.** 재표집을 어떻게 만들었는지가 곧 식별전략이고,
그래서 `null_kind` 를 반드시 선언하게 한다. 무슨 주장을 검정했는지가 거기서 정해진다.
"""
from __future__ import annotations

import numpy as np


def residualize(y: np.ndarray, on: list[np.ndarray]) -> np.ndarray:
    """y 에서 통제변수들의 선형 성분을 뺀다. 무엇을 통제할지는 **에이전트가 정한다.**

    **함정:** 절편이 포함되므로 `residualize(y, on).sum()` 은 **구조적으로 0** 이다.
    누적 초과수익(CAR)을 재려면 이걸 쓰면 안 된다 - 적합 창에 `fit()`,
    사건 창에 `predict()` 를 써서 표본외로 빼라. 창내 잔차 누적은 언제나 0 이다.
    이 함수가 맞는 곳: 상관·분산·순위처럼 **합이 아닌** 통계량.
    """
    if not on:
        return y - y.mean()
    Amat = np.column_stack([np.ones(len(y))] + [np.asarray(c, dtype=float) for c in on])
    beta, *_ = np.linalg.lstsq(Amat, y, rcond=None)
    return y - Amat @ beta



def fit(y: np.ndarray, on: list[np.ndarray]) -> np.ndarray:
    """적합 창에서 계수를 구한다. **표본외 적용용** - 이벤트 스터디는 이걸 써야 한다."""
    Amat = np.column_stack([np.ones(len(y))] + [np.asarray(c, dtype=float) for c in on])
    beta, *_ = np.linalg.lstsq(Amat, y, rcond=None)
    return beta



def predict(coef: np.ndarray, on: list[np.ndarray]) -> np.ndarray:
    """다른 창에 계수를 적용한다."""
    n = len(on[0]) if on else 0
    return np.column_stack([np.ones(n)] + [np.asarray(c, dtype=float) for c in on]) @ coef



def placebo(stat, obs, nulls, *, two_sided: bool = True,
            null_kind: str = "date") -> dict:
    """**임의의 통계량을 귀무분포에 붙인다.** 이 함수는 stat 이 무엇인지 모른다.

      stat   : (세계) -> float | None      None 이면 그 세계는 버린다
      obs    : 관측된 세계
      nulls  : 주장이 거짓인 세계들 (반복가능)

    재표집(nulls 를 어떻게 만들었나)이 곧 식별전략이다 - 반증층이 감사한다.

    `null_kind` 를 반드시 선언해라. 이게 무슨 주장을 검정했는지를 정한다:
      "date"   다른 날을 섞음  -> "이 날이 특별한가". **귀속 근거가 아니다.**
                셀이 큰 특이수익으로 선정됐으므로 이 검정은 거의 자동으로 유의하다(선택 순환).
      "label"  분류 딱지를 섞음 -> "이 경로가 맞나". **귀속 근거가 된다.**
      "time"   시각을 섞음      -> "이 시점이 특별한가". 사건 정렬의 근거.
      "entity" 종목을 섞음      -> 횡단면 특이성.
    """
    o = stat(obs)
    if o is None:
        return {"testable": False, "reason": "관측 세계에서 통계량 계산 불가"}
    d = np.array([v for w in nulls if (v := stat(w)) is not None], dtype=float)
    if len(d) < 20:
        return {"testable": False, "reason": f"위약 표본 {len(d)}개 - 20 미만",
                "obs": float(o), "n_null": len(d)}
    if max(abs(o), float(np.abs(d).max())) < 1e-10:
        return {"testable": False, "n_null": len(d), "obs": float(o),
                "reason": "통계량이 사실상 상수 0 - **퇴화**. 창내에서 절편 포함 적합 후 "
                          "그 창의 잔차를 더하지 않았나? 잔차합은 구조적으로 0이다. "
                          "fit() 을 적합 창에, predict() 를 사건 창에 써라."}
    # 귀무분포가 상수면 통계량이 순열에 **반응하지 않는다** - 검정이 아니다.
    # 실측: obs=0.045, null_sd=0.0, p=1/1001. stat 이 world['x'] 대신 바깥 x 를 읽었다.
    # 기존 퇴화 가드는 통계량이 0 일 때만 걸려서 이걸 통과시켰다.
    # std 는 평균 차감 때문에 동일값에서도 ~1e-17 이 남는다. ptp 를 써야 정확히 0 이다.
    if float(np.ptp(d)) <= 1e-12 * max(abs(float(o)), 1.0):
        return {"testable": False, "obs": float(o), "n_null": len(d), "null_sd": 0.0,
                "null_kind": null_kind,
                "reason": "귀무분포 분산이 0 - 통계량이 순열에 반응하지 않는다. "
                          "stat(world) 가 world 안의 값을 실제로 읽는지 확인해라 "
                          "(바깥 변수를 닫아 버리면 순열이 무의미해진다)."}
    cmp = (np.abs(d) >= abs(o)) if two_sided else (d >= o)
    return {
        "testable": True,
        "null_kind": null_kind,
        "obs": float(o),
        "p": float((1 + cmp.sum()) / (1 + len(d))),
        "n_null": len(d),
        "null_med": float(np.median(d)),
        "null_q05": float(np.quantile(d, 0.05)),
        "null_q95": float(np.quantile(d, 0.95)),
        "null_sd": float(d.std()),
    }


SCHEMA = """측정 원시함수. **전략은 없다 - 네가 만든다.**

  series(entity, kind, grain, w0, w1) -> (인덱스, 값)
      entity='MARKET' 은 시장. kind: return|close|volume. grain: 5min|daily
  panel(entities, kind, grain, w0, w1) -> (공통인덱스, {entity: 배열})
      **여러 종목 한 번에.** 인덱스는 전 종목 교집합이라 바로 회귀에 넣을 수 있다
  peer_index(members, w0, w1, weight) -> (인덱스, 배열)
      **동종 포트폴리오 수익률 지수.** peers() 결과를 그대로 넣어라. weight='equal'|'cap'
  fit(y, on) -> 계수         적합 창에서. **CAR 은 반드시 이걸로**
  predict(coef, on) -> 예측  사건 창에 적용
  residualize(y, on) -> 잔차  창내. 합은 항상 0 - 상관·분산용
  days(w0, w1) -> 거래일 목록
  placebo(stat, obs, nulls, null_kind=...) -> {obs, p, n_null, null_kind, ...}
      null_kind: "date"|"label"|"time"|"entity" - **반드시 선언**. 무슨 주장을 검정했는지 정한다
      date 는 "이 날이 특별한가"만 답한다. 귀속에는 label/time/entity 가 필요하다
      stat  : (세계) -> 수 | None
      obs   : 관측 세계
      nulls : **주장이 거짓인 세계들** ← 이걸 어떻게 만드는지가 곧 식별전략이다

관례: 세계는 아무 객체나 된다 - 날짜 하나여도 되고 (라벨, 날짜) 튜플이어도 된다.
stat 이 그걸 해석할 수 있으면 된다."""


if __name__ == "__main__":
    # 자체검사: 원시함수만으로 두 통계량을 검정한다.
    # 하나는 **일부러 퇴화시켜** 가드가 잡는지 본다.
    import datetime as dt

    tau = dt.datetime(2026, 6, 1, 11, 8, 47)
    lo, hi = tau.time(), (tau + dt.timedelta(minutes=90)).time()
    mi, _ = series("MARKET", grain="5min")
    M = dict(zip(mi, _))
    S = dict(zip(*series("ORG_KR_000660", grain="5min")))
    D = days()
    W = {d: [t for t in mi if t.date() == d and lo <= t.time() <= hi and t in S] for d in D}
    K = 30  # 적합 창: 직전 30 거래일의 같은 시각창

    def car(day):
        """τ~τ+90분 CAR. β 는 **직전 30일 같은 시각창**에서 적합 - 표본외."""
        i = D.index(day)
        if i < K or len(W[day]) < 5:
            return None
        te = [t for d in D[i - K:i] for t in W[d]]
        if len(te) < 100:
            return None
        c = fit(np.array([S[t] for t in te]), [np.array([M[t] for t in te])])
        ts = W[day]
        y = np.array([S[t] for t in ts])
        if y.std() < 1e-12:
            return None
        return float((y - predict(c, [np.array([M[t] for t in ts])])).sum())

    r = placebo(car, tau.date(), [d for d in D if d != tau.date()])
    print(f"CAR(표본외 β)  θ = {r['obs']*100:+.3f}pp   p = {r['p']:.3f}   위약 {r['n_null']}일")
    print(f"               귀무 sd {r['null_sd']*100:.2f}pp  [{r['null_q05']*100:+.2f}, "
          f"{r['null_q95']*100:+.2f}]")
    assert r["testable"] and r["null_sd"] > 1e-5, r

    def car_bad(day):
        """일부러 틀린 판 - 창내 적합. 잔차합은 구조적으로 0."""
        ts = W[day]
        if len(ts) < 5:
            return None
        y = np.array([S[t] for t in ts])
        if y.std() < 1e-12:
            return None
        return float(residualize(y, [np.array([M[t] for t in ts])]).sum())

    rb = placebo(car_bad, tau.date(), [d for d in D if d != tau.date()])
    print(f"CAR(창내 β)    -> {rb['reason'][:46]}")
    assert not rb["testable"] and "퇴화" in rb["reason"], rb

    # 같은 placebo 가 완전히 다른 통계량도 받는다 - 전략 비의존성
    g = ["ORG_KR_240810", "ORG_KR_084370", "ORG_KR_036930", "ORG_KR_319660"]
    P = {c: dict(zip(*series(c, grain="5min"))) for c in g}

    def lam1(day):
        """일중 초과수익 상관의 λ₁ 비중. 첫 봉(오버나이트)은 뺀다."""
        ts = [t for t in mi if t.date() == day and all(t in P[c] for c in g)][1:]
        if len(ts) < 40:
            return None
        m = np.array([M[t] for t in ts])
        E = np.array([residualize(np.array([P[c][t] for t in ts]), [m]) for c in g])
        if (E.std(axis=1) < 1e-12).any():
            return None
        w = np.sort(np.linalg.eigvalsh(np.corrcoef(E)))[::-1]
        return float(w[0] / w.sum())

    r2 = placebo(lam1, dt.date(2026, 6, 1), [d for d in D if d != dt.date(2026, 6, 1)],
                 two_sided=False)
    print(f"λ₁ 비중        {r2['obs']:.3f}   p = {r2['p']:.3f}   위약 {r2['n_null']}일 "
          f"(중앙 {r2['null_med']:.3f})")
    assert r2["testable"], r2

    print("\nmeasure selfcheck ok — 전략 0줄 · 퇴화 가드 작동", file=sys.stderr)



def permute(x, strata=None, n: int = 1000, seed: int = 0) -> list:
    """처치 라벨 순열 귀무. **설계가 조건화한 것을 귀무도 보존한다.**

    `strata` 를 주면 층 안에서만 섞는다. 대조군을 날짜·산업 안에서 만들었다면
    층도 날짜·산업이어야 한다 - 안 그러면 귀무 분산이 층 효과로 부풀고 검정이 무의미해진다.
    실측: 자유순열 귀무sd 0.0088 vs 날짜내 0.0077.

    반환은 placebo 의 `nulls` 로 그대로 넣는 [{"x": 배열}, ...] 다.
    """
    rng = np.random.default_rng(seed)
    x = np.asarray(x)
    if strata is None:
        return [{"x": rng.permutation(x)} for _ in range(n)]
    s = np.asarray(strata)
    if len(s) != len(x):
        raise ValueError(f"strata 길이 {len(s)} != x 길이 {len(x)}")
    groups = [np.where(s == v)[0] for v in dict.fromkeys(s.tolist())]
    out = []
    for _ in range(n):
        xp = x.copy()
        for g in groups:
            if len(g) > 1:
                xp[g] = rng.permutation(x[g])
        out.append({"x": xp})
    return out
