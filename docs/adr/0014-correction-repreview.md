# ADR-0014: 정정 이벤트 처리 — 무조건 재검수

- 상태: 승인됨
- 날짜: 2026-07-12
- 결정 로그: 확정 결정 #3 (2026-07-12)

## 맥락
핵심 가치 3번 — "검수 없이 고객 노출 문구가 변경되는 경로가 존재하지 않는다". Cloud가 발행하는 정정 이벤트가 온프렘의 이미 발행된 콘텐츠를 어떻게 바꿀 수 있는지, 그 경로에 검수를 어떻게 강제할지 정해야 했다.

## 결정
무조건 재검수: 기존 발행분 UNPUBLISHED → Review Queue 회귀. 검수 없는 문구 변경 경로 없음.

AUTO_PUBLISHED였던 콘텐츠도 동일하게 처리한다. 정정 건에 자동 노출 경로는 없다. 무효화(노출 "제거")는 보수적 방향이므로 검수 불요·자동 허용. 상세 플로우는 [../domain/state-machine.md](../domain/state-machine.md).

## 대안
위험등급/정정 유형별 자동 반영 vs 재검수를 테넌트 정책으로 분기 — 확장 로드맵(MVP 아님)으로 미룬다 ([../roadmap.md](../roadmap.md)).

## 결과
- Super Admin은 정정 시 사유 입력 필수.
- 정정은 리비전 분리 모델로 구현된다 — 구 item CORRECTED 종결 + `supersedes_item_id` 참조 신규 리비전 재검수 ([ADR-0021](0021-design-reinforcement.md), [../domain/state-machine.md](../domain/state-machine.md)).
