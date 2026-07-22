---
doc_type: adr
status: Accepted
owner: engineering
created: 2026-07-10
updated: 2026-07-10
related:
  - ../baseline/analysis-engine-design.md
  - ../specs/price-decomposition-engine.md
  - ../baseline/analysis-engine-design.md
---
# ADR-001: 가격 분해와 이슈 분석의 모듈 경계

## Context

가격 관찰, 상관 자산 설명, 뉴스·공시 해석을 한 단계에서 수행하면 공통 움직임을 개별 사건의 효과로 과대해석하기 쉽다.

## Decision

1. 가격 분해를 이슈 분석보다 먼저 실행한다.
2. 시장과 피어로 설명한 뒤 남은 잔차의 유의성을 진입 게이트로 사용한다.
3. 잔차가 작으면 가격 중심 설명으로 종료할 수 있다.
4. 잔차가 의미 있을 때만 대상 자산의 기존 기대와 새 이벤트를 깊게 분석한다.
5. 이벤트 이후 가격은 F/G 단계의 정합성 검증에만 사용한다.
6. 교차 자산 이슈 탐색은 한국 대상 자산에서 미국 설명 자산까지 최대 2단계로 제한한다.

## Alternatives

- **이벤트 우선 분석:** 뉴스가 많은 날 과잉 설명하기 쉽다.
- **가격과 이벤트 동시 결합:** 책임과 실패 원인을 분리하기 어렵다.
- **잔차 없이 모든 이벤트 분석:** 비용이 크고 사용자 설명이 장황해진다.

## Consequences

- 가격 모듈과 이벤트 모듈의 입력·출력 계약이 분리된다.
- 잔차 임계값 오류가 분석 진입률에 직접 영향을 준다.
- price-only 경로도 완전한 최종 결과로 취급해야 한다.

## Revisit when

잔차 게이트가 중요한 이벤트를 반복적으로 누락하거나, 교차 자산 2단계 제한 때문에 설명력이 구조적으로 낮아질 때 재검토한다.

결정 2의 '시장과 피어로 설명한 뒤' 문구는 identity-first 아키텍처 채택으로 '항등식·요인 분해로 설명한 뒤' 일반화 개정 후보다([current-architecture](../baseline/analysis-engine-design.md) Open questions 참조).
