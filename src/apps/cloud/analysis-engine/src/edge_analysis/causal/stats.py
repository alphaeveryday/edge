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
