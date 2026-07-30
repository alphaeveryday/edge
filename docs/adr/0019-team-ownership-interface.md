# ADR-0019: 팀 오너십 — Sync 프로토콜 양단 단일 오너, 인터페이스는 계약 하나로 고정

- 상태: 승인됨
- 날짜: 2026-07-12
- 결정 로그: 확정 결정 #8 (2026-07-12)

## 맥락
하이브리드 피벗으로 컴포넌트 경계가 재편되면서 3인(김진기·조영서·정준영)의 오너십 경계와, 경계 간 인터페이스를 어디에 둘지 다시 정해야 했다.

## 결정
진기: Event Bundle 생성까지 / 영서: Tenant Sync API 이후 전부 / 준영: AI·ML. 인터페이스 = Event Store 스키마 + 번들 규칙.

- **김진기**: Data Pipeline → Common Analysis Engine → Cloud Event Store + **Event Bundle 생성까지**.
- **조영서**: **Tenant Sync API부터 이후 전부** — Sync Agent, Compliance Engine, Tenant Console (API), Serving API, Super Admin Console API. Sync 프로토콜 양단을 단일 오너가 설계.
- **정준영**: AI/ML — 설명 후보 생성, 신뢰도/반대 요인 산출.
- 진기-영서 인터페이스는 **"Cloud Event Store 스키마 + 번들 생성 규칙"** 하나로 고정한다. 이 계약 변경은 반드시 양자 합의.

## 대안
원문(컨텍스트 문서 v2.0)에 검토 대안이 별도로 기록되지 않았다.

## 결과
- 계약 문서는 [../contracts/event-bundle-schema.md](../contracts/event-bundle-schema.md)·[../contracts/sync-protocol.md](../contracts/sync-protocol.md)이며, 양자 합의는 CODEOWNERS(진기·영서)로 강제한다.
- Sync 프로토콜 양단(Tenant Sync API·Sync Agent)을 단일 오너(영서)가 설계해 프로토콜 정합을 한 사람이 책임진다.
