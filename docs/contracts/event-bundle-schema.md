# Event Bundle 인터페이스 계약 (진기-영서)

> **계약 문서** — 이 파일의 변경은 진기·영서 공동 승인 대상이다(CODEOWNERS).

진기-영서 인터페이스는 **"Cloud Event Store 스키마 + 번들 생성 규칙"** 하나로 고정한다. 이 계약 변경은 반드시 양자 합의.

- 오너십 경계: **김진기** — Data Pipeline → Common Analysis Engine → Cloud Event Store + **Event Bundle 생성까지** / **조영서** — **Tenant Sync API부터 이후 전부** ([../README.md](../README.md)의 팀 오너십 참조).
- **번들 생성 규칙(cursor 발번·outbox fan-out)** 은 이 계약의 일부다 — 확정 스펙은 [sync-protocol.md](sync-protocol.md): 테넌트별 outbox fan-out 시점(공통 이벤트 1건 → 테넌트별 전달 레코드 N건 생성)에 각 테넌트의 sequence를 발번하고, Event Bundle은 이 outbox를 cursor 순으로 묶어 생성한다.
- 전송 단위: Event Bundle (신규 이벤트 + 정정 이벤트 + 무효화 이벤트 포함). 무결성은 번들 단위 SHA-256 체크섬.

## Cloud Event Store 스키마

**물리 정의는 [`src/libs/schema/migrations/`](../../src/libs/schema/migrations/)의 Flyway SQL이다** — generated 모델 생성기가 없는 현재는 이 SQL이 계약을 정의한다([implementation.md](../implementation.md) §4). 최초 도입은 `V202607150001__replace_analysis_mart_with_etf_explanation_schema.sql`(ALPHA-359, 48개 테이블, `public` 스키마). 이 경로는 CODEOWNERS로 이 문서와 같은 양자 합의 게이트에 묶여 있다.

아래는 Sync 채널이 실제로 소비하는 **경계면**만 추린 것이다. 48개 전체가 인터페이스는 아니다 — 나머지는 진기 측 내부 구현이며 양자 합의 없이 바뀔 수 있다.

### 번들에 실리는 것 (영서가 읽는 면)

| 테이블 | 역할 | 키 |
| --- | --- | --- |
| `explanation_result` | 고객 노출 후보 문구. 번들의 본체 | `explanation_result_id` |
| `explanation_run` | 어느 실행이 그 결과를 냈는지 + 사용한 릴리스 번들 버전 | `explanation_run_id` |
| `source_event` | 설명이 근거로 삼은 소스 이벤트 | `source_event_id` |
| `event_evidence` | 이벤트를 뒷받침하는 문서 주장 근거 | `evidence_id` |
| `event_thread` | 동일 실제 사건의 계보(정정·후속 판정의 기준) | `thread_id` |
| `release_bundle` | 고객사가 승인·적용하는 제품 버전 manifest | `bundle_version` |

**게시 grain**: `explanation_result`의 결과 grain은 `(etf_instrument_id, trade_date, explanation_as_of)`이며, 이 grain에서 `publication_status = 'PUBLISHED'`인 행은 **부분 유니크 인덱스로 하나만 강제**된다(`uq_explanation_result_published_grain`). 재게시는 기존 게시본을 `WITHDRAWN`으로 내린 뒤 새 행을 게시한다. `DRAFT`·`WITHDRAWN` 이력은 같은 grain에 여러 건 남는다.

> **합의 필요** — 여기서 `publication_status`는 **Cloud 내부의 확정 상태**이지 고객 노출 상태가 아니다(고객 노출은 On-Prem 검수를 거친 `publications`가 결정한다, [state-machine.md](../domain/state-machine.md)). Sync가 `PUBLISHED`만 번들에 싣는지, `WITHDRAWN` 전이를 무효화 이벤트로 전달하는지는 아직 정하지 않았다.

### 아직 합의 안 된 것

- **outbox 테이블** — 테넌트별 fan-out 레코드와 cursor 발번 지점. 위 마이그레이션에 **없다.** 설계는 영서 소관이며 확정 시 이 문서와 `sync-protocol.md`에 함께 기록한다([ADR-0021](../adr/0021-design-reinforcement.md)).
- **`tenant` · `tenant_credential` · `tenant_sync_cursor` · `tenant_release_history`** — 위 마이그레이션이 함께 생성했으나, 이 4개는 **Tenant Sync API 이후 영역(영서 오너십, [ADR-0019](../adr/0019-team-ownership-interface.md))** 이다. 현재 정의는 릴리스 번들 배포·동기화 상태를 담는 최소 형태이며 **영서 확정안이 우선한다** — 인증 모델(키 로테이션 등)이나 커서 의미가 다르게 정해지면 수축-확장으로 교체한다.
- 이벤트 타입별(신규/정정/무효화) 번들 JSON 구조와 체크섬 대상 바이트.
