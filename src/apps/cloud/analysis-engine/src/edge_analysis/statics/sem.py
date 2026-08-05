"""동순위 규율 — 겹치는 구간을 하나의 순위로 접는다. **SEM 은 폐기됐다.**

지운 것: `EdgeEstimate` · `exposure_slope` · `clip_to_share`.

왜 지웠나. 사건 고정효과 하의 노출 기울기 τ̂ 는 **기울기**인데 블록과 산문이
그것을 하루 `ar_ind` 의 **수준**처럼 읽었다 - 구조적 오독원이고 고칠 자리가
없다. 산출이 이미 자기모순을 자백했다: 부호 +1 로 세운 가설에 음수 기여가
붙고, τ̂·Δx 구간이 하루 총합과 겹치지 않는 날이 나왔다. FE 가 충격의 절대
크기 g 를 흡수해버리므로 τ̂·Δx 를 "오늘 이 사건이 만든 %p" 로 읽을 근거가
애초에 없었다 - 그 곱은 '오늘 노출이 패널 평균보다 이만큼 높으면 기울기만큼
더 움직인다' 는 비교정태이지 수준 귀속이 아니다.

인과의 무게를 지는 유일한 설계 기반(매칭 · SMD 균형 · 사전추세 위약 ·
재보도 위약)은 전부 ATT 경로에 있다. ATT 는 **수준 효과**라 예산과 단위가
같다(둘 다 `ar_ind`).

SEM 의 유일한 순기능은 **과대식별 검산**이었다 (구조 추정 구간이 항등식 상한을
넘으면 모형이 틀렸다는 공짜 반증). 그냥 지우면 반증 장치가 하나 사라지므로
`narrate.AdditiveBudget` 의 가법 제약이 대체한다:

    모순 ⟺ Σₖ |ATTₖ| > |B| + 1.96·σ̂_ε

개별 교차(SEM 은 엣지마다 따로 봤다)가 아니라 **합산**이라 더 엄격하다 -
엣지 m 개가 각각 예산의 절반을 주장하는 구멍을 정확히 막는다.

여기 남은 것은 순위 규율뿐이다. **구간이 겹치면 순위를 날조하지 말라**는
규칙은 어떤 추정량에도 유효하고(라쏘 계수 순위도 같다) SEM 과 무관하다.
"""
from __future__ import annotations

import numpy as np


def rank_with_ties(contribs: dict[str, np.ndarray]) -> list[tuple[str, int]]:
    """부트스트랩 표본 → 순위. 구간이 겹치면 **동순위** — 아니면 날조다.

    contribs: 이름 → 부트스트랩 |크기| 표본 (동일 길이). 점추정만 있으면 이
    함수를 쓸 수 없다 - 겹침을 판정할 재료가 없으므로 순위를 매기지 않는 것이
    정직하다.
    """
    names = list(contribs)
    qs = {n: (float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975)))
          for n, v in contribs.items()}
    order = sorted(names, key=lambda n: -float(np.median(contribs[n])))
    ranks: dict[str, int] = {}
    rank = 1
    for i, n in enumerate(order):
        if i > 0:
            prev = order[i - 1]
            # 겹치면 앞 항목과 같은 순위
            if qs[n][1] >= qs[prev][0] and qs[prev][1] >= qs[n][0]:
                ranks[n] = ranks[prev]
                continue
            rank = i + 1
        ranks[n] = rank
    return [(n, ranks[n]) for n in order]


def _selfcheck() -> None:
    rng = np.random.default_rng(0)
    r = rank_with_ties({"a": rng.normal(1.0, 0.01, 500),
                        "b": rng.normal(0.99, 0.01, 500),   # a 와 겹침 → 동순위
                        "c": rng.normal(0.2, 0.01, 500)})
    d = dict(r)
    assert d["a"] == d["b"] == 1 and d["c"] == 3


_selfcheck()

__all__ = ["rank_with_ties"]
