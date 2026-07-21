# Sync 프로토콜 계약 (확정 결정)

> **계약 문서** — 이 파일의 변경은 진기·영서 공동 승인 대상이다(CODEOWNERS). 인터페이스 계약의 정의는 [event-bundle-schema.md](event-bundle-schema.md).

**MVP 구현 스펙**:

- **Cursor**: 테넌트별, Cloud 서버가 발번하는 단조 증가 sequence. 온프렘은 마지막 처리 cursor를 durable 저장하고 `?after={cursor}`로 Pull한다. Pull을 개시하는 주체는 DMZ의 **Sync Agent**이고, 권위 committed cursor의 durable 저장 주체는 2모듈 표준에서 **Intake(내부망)**·단일 모듈 옵션에서 sync-agent다([ADR-0036](../adr/0036-sync-agent-intake-topology.md)). 2모듈에서 Pull 재개점은 **Intake의 committed cursor가 권위**다 — Sync Agent의 DMZ-local 마크는 Intake의 **commit-ack까지만 전진**(내부 채널 ack)하므로, 전송·commit 실패 시 그 번들을 재-Pull하고 멱등 upsert가 중복을 dedup해 **유실 없이** 수렴한다. ack 없이 마크를 먼저 전진시키면 `?after=`가 미commit 번들을 건너뛰어 유실되므로 ack가 정확성 필수다(ADR-0036).
- **Cursor 발번 시점 (확정, 2026-07-13)**: 공통 이벤트는 비개인화 단일 레코드지만 cursor는 테넌트별이므로, **테넌트별 outbox fan-out 시점**(공통 이벤트 1건 → 테넌트별 전달 레코드 N건 생성)에 각 테넌트의 sequence를 발번한다. Event Bundle은 이 outbox를 cursor 순으로 묶어 생성한다. 이 fan-out 규칙은 "Cloud Event Store 스키마 + 번들 생성 규칙" 인터페이스 계약([event-bundle-schema.md](event-bundle-schema.md))의 일부이므로 변경 시 진기-영서 양자 합의 대상.
- **전송 단위**: Event Bundle (신규 이벤트 + 정정 이벤트 + 무효화 이벤트 포함).
- **무결성**: 번들 단위 SHA-256 체크섬. 검증 실패 시 저장하지 않고 재시도.
- **폴링 주기**: 기본 1~5분 (테넌트 설정 가능).
- **전달 보장**: at-least-once. 중복·재-Pull(재시도·ack 유실 포함) 수신은 **무해해야 한다** — 요구사항은 ① 유실(skip) 없음 ② immutable raw 레코드 손상·충돌 없음이다.
  - **도메인 저장**: 도메인 ID(`explanation_result_id` 등 — [event-bundle-schema.md](event-bundle-schema.md) ID 체계) 기반 **멱등 upsert**.
  - **raw 번들 저장 dedup은 cursor 기준**(바이트 기준 아님) — 재-Pull 응답은 `bundle_id`·`generated_at`를 매 응답 새로 발번해 같은 범위라도 바이트·체크섬이 달라지므로, 바이트로 dedup하면 immutable raw(cursor 키)를 덮어쓰거나 PK 충돌한다. **정확한 dedup 키(번들 cursor 범위 vs 이벤트 cursor 단위)·부분 겹침(번들 경계가 pull마다 다른 경우) 처리는 sync 채널 구현에서 확정한다** — 온프렘 sync는 walking skeleton 미구축이라 이 표면을 지금 고정하지 않는다(아래 '제안' 원칙과 동일). 토폴로지 결정([ADR-0036](../adr/0036-sync-agent-intake-topology.md))은 위 두 요구사항만 고정하고 dedup 메커니즘은 구현에 위임한다.
- **순서**: cursor 순 처리. 서버 발번 단조증가 cursor를 순차 소비하므로 정정/무효화는 항상 대상 원본 이벤트보다 늦게 도착한다 (원본 cursor < 정정 cursor). 정정/무효화는 대상 원본을 `target_explanation_result_id`로 참조한다. "원본 미수신 상태의 정정"은 gap(sequence 누락)에서만 발생할 수 있으므로, 보류-재처리 로직은 gap 감지(목표 계약)와 함께 도입한다. MVP는 순차 소비 보장 하나로 순서를 담보한다. (참고: 서버 발번 sequence 구조에서는 gap 감지가 수신 cursor의 불연속 확인만으로 가능해 구현 비용이 낮다 — walking skeleton 안정화 후 목표 계약에서 MVP로 조기 승격 검토 후보.)

**엔드포인트 와이어 스펙 (제안 — tenant-sync-api 스캐폴드 기준, ALPHA-358)**:

> 아래는 스캐폴드 구현이 도입한 구체 스펙이다. 아직 합의 전이므로 **제안** 상태로 두고, 확정 시 위 "MVP 구현 스펙"으로 승격한다. Sync Agent 구현은 이 스펙이 확정되기 전까지 이 표면에 고정 의존하지 않는다.

- **경로**: `GET /api/v1/sync/bundle?after={cursor}&limit={n}` — `limit` 생략 시 100, 최대 500 (범위 밖은 400).
- **신규 없음**: `204 No Content` — 빈 번들은 만들지 않는다.
- **무결성 헤더**: `X-Bundle-Checksum: sha256=<hex>` — 체크섬 대상은 **응답 body 바이트열 그대로**. 서버는 직렬화를 한 번만 수행하고 같은 바이트를 body로 보낸다 (재직렬화·body 가공 필터 금지).
- **필드 표기**: snake_case (`bundle_id`·`cursor_from`·`delivery_type` …).
- **에러 응답**: 4xx/5xx만 공통 봉투(jvm-common `ApiResponse`) — 파라미터 오류는 400 + `SYNC4001`(after)·`SYNC4002`(limit). 성공(200) 본문은 봉투 없이 계약 와이어 포맷 그대로(체크섬 대상이므로). 401·403·410 외 4xx는 소비자 버그 신호로 재시도 없이 표면화한다.

**목표 계약 (문서상 명시, 구현은 후순위)**:

- MVP 스펙 + **벤더 개인키 기반 번들 서명** — 감사 시 "이 콘텐츠는 벤더가 발행한 원본"임을 증명. Raw Event Store에 서명 원본 보존.
- 순서 보장 및 gap 감지(sequence 누락 시 재요청)의 명시적 계약화.

> **엔드포인트 계약(응답 포맷·상한·에러 시맨틱·규모 가정)은 미확정** — Tenant Sync API 오너(영서)가 설계해 이 문서에 기록한다.
