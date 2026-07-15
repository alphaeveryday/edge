# Sync 프로토콜 계약 (확정 결정)

> **계약 문서** — 이 파일의 변경은 진기·영서 공동 승인 대상이다(CODEOWNERS). 인터페이스 계약의 정의는 [event-bundle-schema.md](event-bundle-schema.md).

**MVP 구현 스펙**:

- **Cursor**: 테넌트별, Cloud 서버가 발번하는 단조 증가 sequence. Sync Agent는 마지막 처리 cursor를 로컬 저장하고 `?after={cursor}`로 Pull.
- **Cursor 발번 시점 (확정, 2026-07-13)**: 공통 이벤트는 비개인화 단일 레코드지만 cursor는 테넌트별이므로, **테넌트별 outbox fan-out 시점**(공통 이벤트 1건 → 테넌트별 전달 레코드 N건 생성)에 각 테넌트의 sequence를 발번한다. Event Bundle은 이 outbox를 cursor 순으로 묶어 생성한다. 이 fan-out 규칙은 "Cloud Event Store 스키마 + 번들 생성 규칙" 인터페이스 계약([event-bundle-schema.md](event-bundle-schema.md))의 일부이므로 변경 시 진기-영서 양자 합의 대상.
- **전송 단위**: Event Bundle (신규 이벤트 + 정정 이벤트 + 무효화 이벤트 포함).
- **무결성**: 번들 단위 SHA-256 체크섬. 검증 실패 시 저장하지 않고 재시도.
- **폴링 주기**: 기본 1~5분 (테넌트 설정 가능).
- **전달 보장**: at-least-once. On-Prem 저장은 도메인 ID(`explanation_result_id` 등 — [event-bundle-schema.md](event-bundle-schema.md) ID 체계) 기반 **멱등 upsert** — 중복 수신은 무해해야 한다.
- **순서**: cursor 순 처리. 서버 발번 단조증가 cursor를 순차 소비하므로 정정/무효화는 항상 대상 원본 이벤트보다 늦게 도착한다 (원본 cursor < 정정 cursor). 정정/무효화는 대상 원본을 `target_explanation_result_id`로 참조한다. "원본 미수신 상태의 정정"은 gap(sequence 누락)에서만 발생할 수 있으므로, 보류-재처리 로직은 gap 감지(목표 계약)와 함께 도입한다. MVP는 순차 소비 보장 하나로 순서를 담보한다. (참고: 서버 발번 sequence 구조에서는 gap 감지가 수신 cursor의 불연속 확인만으로 가능해 구현 비용이 낮다 — walking skeleton 안정화 후 목표 계약에서 MVP로 조기 승격 검토 후보.)

**목표 계약 (문서상 명시, 구현은 후순위)**:

- MVP 스펙 + **벤더 개인키 기반 번들 서명** — 감사 시 "이 콘텐츠는 벤더가 발행한 원본"임을 증명. Raw Event Store에 서명 원본 보존.
- 순서 보장 및 gap 감지(sequence 누락 시 재요청)의 명시적 계약화.

> **엔드포인트 계약(응답 포맷·상한·에러 시맨틱·규모 가정)은 미확정** — Tenant Sync API 오너(영서)가 설계해 이 문서에 기록한다.
