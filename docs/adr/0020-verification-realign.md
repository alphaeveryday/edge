# ADR-0020: 검증 반영 — 규제 서사 재정렬 및 설계 정합 일괄 정정

- 상태: 승인됨
- 날짜: 2026-07-13
- 결정 로그: 확정 결정 #9 (2026-07-13)

## 맥락
컨텍스트 문서 v2.0 초안에 대한 검증에서 규제 서사·프로토콜 순서 조항·격리 검증·콘솔 상태 정의·커버리지 표기의 정합 문제가 발견되어 일괄 반영했다.

## 결정
- 규제 서사를 "회피"에서 "통제권·결과책임" 프레임으로 재정렬 ([../context.md](../context.md))
- Sync 프로토콜 순서 조항 정리 — gap 감지와 보류 로직 연동 ([../contracts/sync-protocol.md](../contracts/sync-protocol.md))
- 인증서-테넌트 인가 검증 명시 ([../contracts/sync-auth.md](../contracts/sync-auth.md))
- Tenants 목록 상태 = 연결 상태로 정의 ([../console-ia/super-admin-console.md](../console-ia/super-admin-console.md))
- MVP 커버리지 한국주식 한정, 미국주식은 로드맵 이동 ([../roadmap.md](../roadmap.md))
- 무효화 시 publications 전이 명시 ([../domain/state-machine.md](../domain/state-machine.md))

## 대안
검증 전 초안 서술 유지 — 규제 완화 흐름과 배치되는 "회피" 논거는 시효가 짧아 배제.

## 결과
- 모든 대외 문서·발표에서 "통제권·감사 재현성" 프레임을 사용한다.
- 이후 2차 검증·보강이 이어졌다 — [ADR-0021](0021-design-reinforcement.md), [ADR-0022](0022-verification-round2.md).
