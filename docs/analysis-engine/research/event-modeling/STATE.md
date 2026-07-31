---
doc_type: state
status: Draft
owner: event-research
created: 2026-07-10
updated: 2026-07-11
related:
  - event-ontology.md
  - golden-data-generation.md
  - golden-data-inference.md
  - adaptive-review-loop.md
  - event-feature-thread-discovery.md
---
# 이벤트 모델링 연구 상태

| 항목 | 현재 상태 |
|---|---|
| 현재 결론 | 타입 레지스트리, 운영 프로필, 골든 데이터, 월별 accept/review, canonical event 조립까지는 문서화돼 있다. |
| 신뢰도 | 중간. 일부 producer와 fixture는 구현 근거가 있으나 warehouse table과 완전한 adaptive replay는 아직 logical contract다. |
| 가장 강한 근거 | 온톨로지 profile, feature registry, gold 산출물, review queue, thread JSONL producer의 구체 계약이 존재한다. |
| 가장 큰 위험 | 구현된 산출물과 `[INFERENCE]` 물리 테이블을 혼동하거나, bench 결과를 production runtime과 동일시할 수 있다. |
| 다음 행동 | event/thread persistence와 review→gold→ontology bump→replay 경계를 실제 실행 계약으로 좁혀 검증한다. |

## Current boundaries

- required role 누락은 review, enrichment 누락은 `UNKNOWN/gap`으로 통과한다.
- dedup cluster와 event thread는 다른 식별자다.
- thread identity는 구조화 역할 기반으로 결정한다.
- 골든 데이터의 저신뢰 행은 자동 확정하지 않는다.
- adaptive ontology bump와 replay orchestration은 아직 설계 상태다.
