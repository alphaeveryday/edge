# ADR-0015: Sync 프로토콜 — cursor 기반 delta, MVP 스펙과 목표 계약 분리

- 상태: 승인됨
- 날짜: 2026-07-12
- 결정 로그: 확정 결정 #4 (2026-07-12)

## 맥락
On-Prem Sync Agent가 Cloud의 Event Bundle을 어떤 계약으로 가져올지 — 커서, 무결성, 재시도, 순서 보장의 수준을 MVP와 목표로 나눠 확정해야 했다.

## 결정
MVP: 테넌트별 단조증가 cursor + SHA-256 체크섬 + 폴링 1~5분 + at-least-once/멱등 upsert. 목표: + 번들 서명 + gap 감지.

전체 스펙은 [../contracts/sync-protocol.md](../contracts/sync-protocol.md).

## 대안
순서 보장·gap 감지를 MVP에 포함 — "원본 미수신 상태의 정정"은 gap(sequence 누락)에서만 발생할 수 있으므로, 보류-재처리 로직은 gap 감지(목표 계약)와 함께 도입하고 MVP는 순차 소비 보장 하나로 순서를 담보한다. (서버 발번 sequence 구조에서는 gap 감지가 수신 cursor의 불연속 확인만으로 가능해 구현 비용이 낮다 — walking skeleton 안정화 후 MVP로 조기 승격 검토 후보.)

## 결과
- On-Prem 저장은 이벤트 ID 기반 멱등 upsert — 중복 수신은 무해해야 한다.
- 벤더 개인키 기반 번들 서명(감사 시 "벤더가 발행한 원본" 증명)은 목표 계약으로 문서상 명시, 구현은 후순위.
- cursor 발번 시점은 테넌트별 outbox fan-out으로 후속 확정됐다 ([ADR-0021](0021-design-reinforcement.md)).
