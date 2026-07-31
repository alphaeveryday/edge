---
doc_type: rdr
status: Accepted
owner: price-research
created: 2026-07-10
updated: 2026-07-10
related:
  - ../STATE.md
  - ../experiments/EXP-001-raw-top3-peer-regression.md
  - ../experiments/EXP-002-market-peer-model.md
  - ../../../engineering/specs/price-decomposition-engine.md
---
# RDR-001: 가격 설명 요인 선택

## Decision

개별 종목의 가격 설명 모델은 다음 두 요인만 사용한다.

1. broad market return
2. 시장 수익률에 직교화한 exact-3 peer return

피어는 직전 252거래일 상관 기준 top-3이며 동일 GICS theme 또는 sub-industry 안에서 선택한다. 잔차는 원인이 아니라 이 두 축으로 설명되지 않은 가격 움직임이다.

적용 레벨은 개별 종목이다. ETF는 이 모델의 직접 대상이 아니다: ETF 레벨 1차 분해는 구성종목 기여 항등식이 소유하고, 이 2-leg 모델은 구성종목의 공통·고유 분해(L2)에 적용한다. 피어 ETF를 설명축으로 쓰는 것은 보유 겹침으로 인한 순환성 때문에 금지한다.

## Why

- 30일 raw-return 실험에서 상관 top-3는 random-3보다 높은 동시점 설명력을 보였다.
- 시장 요인과 피어 요인을 분리하면 같은 공통 움직임을 두 번 설명하는 위험을 줄일 수 있다.
- 설명축을 제한하면 사용자에게 각 기여분과 잔차를 명확하게 제시할 수 있다.

## Alternatives

| 대안 | 판단 |
|---|---|
| 시장-only | 단순하지만 동종 종목 동조를 잔차로 남길 수 있다. |
| sector/correlated-asset 다중 leg | 설명은 풍부하지만 중복·다중공선성과 운영 복잡도가 커진다. |
| 30일 동적 peer | 변화에 빠르지만 과적합과 높은 turnover 위험이 있다. |

## Consequences

- exact-3를 만족하지 못한 종목은 별도 fallback 또는 review가 필요하다.
- 252일 정책은 EXP-002로 계속 검증한다.
- 사용자 문구에서 “원인” 대신 “설명분”과 “잔차”를 사용한다.
- ETF 제품 경로에서는 이 모델이 구성종목 레벨에서만 실행되고, ETF 레벨 합성은 항등식 레이어가 담당한다(`engineering/current-architecture.md` 참조).

## Revisit when

EXP-002에서 시장-only 대비 일관된 개선이 없거나, GICS 제한이 충분한 피어를 제공하지 못할 때 재검토한다.
