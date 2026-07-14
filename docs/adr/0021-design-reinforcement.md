# ADR-0021: 설계 보강 — DMZ 배치·cursor 발번 시점·정정 리비전 모델 확정

- 상태: 승인됨
- 날짜: 2026-07-13
- 결정 로그: 확정 결정 #10 (2026-07-13)

## 맥락
검증 반영([ADR-0020](0020-verification-realign.md)) 이후 남은 설계 미결점 — 네트워크 배치, cursor 발번 시점, 정정의 레코드 모델 — 을 확정했다.

## 결정
- 규제 서사의 "연내 전면 해제"를 "검토 중(시한 미공표)"으로 완화 — 당국이 시한을 공표한 바 없음 ([../context.md](../context.md))
- Sync Agent DMZ 배치 + 단일 목적지 outbound 화이트리스트 확정 ([../context.md](../context.md))
- cursor 발번 시점 = 테넌트별 outbox fan-out으로 확정, 인터페이스 계약 편입 ([../contracts/sync-protocol.md](../contracts/sync-protocol.md), [../contracts/event-bundle-schema.md](../contracts/event-bundle-schema.md))
- gap 감지 조기 승격 검토 메모 ([../contracts/sync-protocol.md](../contracts/sync-protocol.md))
- 정정 = 리비전 분리 모델 확정: 구 item CORRECTED 종결 + supersedes 참조 신규 리비전 재검수 ([../domain/state-machine.md](../domain/state-machine.md))

## 대안
정정을 단일 레코드의 상태 왕복으로 처리 — 리비전 분리를 택해 배제. 감사 재현은 리비전 체인을 따라 "어느 시점에 어느 문구가 노출되었는지"를 완전 복원한다.

## 결과
- fan-out 규칙이 진기-영서 인터페이스 계약의 일부가 되어 변경 시 양자 합의 대상이다.
- "외부와 닿는 것은 DMZ의 Sync Agent 하나, 방향은 outbound 하나, 목적지는 하나"가 준법감시인 대상 설명 문구가 된다.
