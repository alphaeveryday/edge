# Event Bundle 인터페이스 계약 (진기-영서)

> **계약 문서** — 진기-영서 인터페이스는 **Cloud Event Store DB 스키마**다([../adr/0026](../adr/0026-ownership-boundary-db.md), db-as-contract). 이 파일 중 스키마 경계면 서술의 변경은 공동 승인 대상(CODEOWNERS)이고, 번들 와이어 포맷(JSON·체크섬)은 Sync 양단 소유자(영서)의 스펙으로 함께 기록한다.

> **상태: 합의 진행 (v2, 2026-07-15)** — 물리 스키마(V202607150001)와 초안(ALPHA-356)을 병합했다. `[합의 필요]` 표기만 남은 열린 결정이다.

- 오너십 경계: **김진기** — Data Pipeline → Common Analysis Engine → **Cloud Event Store 적재까지** / **조영서** — DB를 소비하는 이후 전부: **Event Bundle 생성(tenant-sync-api의 DB 조회·조립)**, 전달 레코드, Sync Agent, 온프렘 ([../adr/0026](../adr/0026-ownership-boundary-db.md)).
- 전송 단위: Event Bundle (신규 + 정정 + 무효화). 무결성은 번들 단위 SHA-256 체크섬. 프로토콜(엔드포인트·cursor·에러)은 [sync-protocol.md](sync-protocol.md).

## Cloud Event Store 스키마

**물리 정의는 [`src/libs/schema/migrations/`](../../src/libs/schema/migrations/)의 Flyway SQL이다** — generated 모델 생성기가 없는 현재는 이 SQL이 계약을 정의한다([implementation.md](../implementation.md) §4). 최초 도입은 `V202607150001__replace_analysis_mart_with_etf_explanation_schema.sql`(ALPHA-359, 47개 테이블, `public` 스키마), sync cursor 정정은 `V202607150002`(ALPHA-356). 이 경로는 CODEOWNERS로 이 문서와 같은 양자 합의 게이트에 묶여 있다.

아래는 Sync 채널이 실제로 소비하는 **경계면**만 추린 것이다. 47개 전체가 인터페이스는 아니다 — 나머지는 진기 측 내부 구현이며 양자 합의 없이 바뀔 수 있다.

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

## ID 체계

- 도메인 ID(`explanation_result_id` 등)는 **Cloud 발번 TEXT** — 물리 스키마 기준. On-Prem 멱등 upsert 키 = 이 도메인 ID.
- `tenant_id`는 BIGINT(identity). 전달 단위의 멱등 처리 기준은 테넌트별 단조증가 cursor(ADR-0015 확정)이며, 저장 구조 차원의 키 설계는 전달 레코드 설계(미확정 — 아래)와 함께 확정한다.
- 정정·무효화는 대상을 `target_explanation_result_id`로 참조한다. 정정으로 생기는 재게시본은 **새 `explanation_result_id`** — On-Prem 리비전 분리 모델의 소스([../domain/state-machine.md](../domain/state-machine.md)).

## 전달 레코드

번들은 테넌트별 전달 레코드를 cursor 순으로 묶어 생성한다 — cursor 발번 시점은 테넌트별 fan-out(ADR-0021 확정). **전달 레코드의 설계 일체 — 멱등 키·저장 구조·fan-out 구현·retention·게시 상태(`publication_status`)와 전달 유형의 매핑 — 는 영서 오너십이며 미확정이다.** 확정 시 이 문서에 기록한다. `[합의 필요]`

`tenant_sync_cursor.last_cursor`(BIGINT)는 cursor 소비 추적이다 — 타입 정정은 `V202607150002`(근거: ADR-0015).

## Event Bundle JSON 구조

번들은 테넌트별 전달 레코드를 cursor 순으로 묶은 것이다. 엔트리 공통 봉투 + delivery_type별 페이로드:

```json
{
  "bundle_id": "0198...uuid",
  "tenant_id": 1,
  "generated_at": "2026-07-15T09:00:00Z",
  "cursor_from": 101,
  "cursor_to": 180,
  "entries": [
    {
      "cursor": 101,
      "delivery_type": "NEW",
      "explanation_result": { "explanation_result_id": "...", "etf_instrument_id": "...",
        "trade_date": "2026-07-15", "explanation_as_of": "...", "explanation_type": "EVENT_SUPPORTED",
        "summary": "...", "confidence_level": "MEDIUM", "primary_thread_id": "..." },
      "explanation_run": { "explanation_run_id": "...", "release_bundle_version": "..." },
      "source_events": [ { "source_event_id": "...", "...": "[합의 필요 — 경계면 컬럼 선정]" } ],
      "evidences": [ { "evidence_id": "...", "...": "[합의 필요 — 경계면 컬럼 선정]" } ]
    },
    {
      "cursor": 102,
      "delivery_type": "CORRECTION",
      "target_explanation_result_id": "...",
      "reason": "근거 공시 정정",
      "explanation_result": { "...": "정정분 전체 — 신규와 동일 형상, 새 ID" },
      "explanation_run": { "...": "..." },
      "source_events": [],
      "evidences": []
    },
    {
      "cursor": 103,
      "delivery_type": "INVALIDATION",
      "target_explanation_result_id": "...",
      "reason": "오탐지 이벤트"
    }
  ]
}
```

- **NEW·CORRECTION은 전체 상태 전달(full snapshot)** — diff/patch가 아니다. On-Prem은 도메인 ID 기준 멱등 upsert만 하면 되고, 부분 갱신 병합 로직이 필요 없다.
- CORRECTION 수신 시 On-Prem 동작(기존 발행분 UNPUBLISHED → 새 리비전 REVIEW_REQUIRED)은 [../domain/state-machine.md](../domain/state-machine.md) 소관 — 이 계약은 "정정분이 전체 형상 + 사유 + 대상 참조로 도착한다"까지만 정의한다.

## 체크섬 (무결성)

- **대상 바이트: HTTP 응답 body 전체의 UTF-8 바이트열 그대로.** 서버는 직렬화한 바이트를 그대로 전송·보관하고, Sync Agent는 **재직렬화 없이 수신 바이트에 대해** SHA-256을 계산해 대조한다 (canonical-JSON 정규화 규칙을 계약에 넣지 않기 위한 선택 — 정규화는 양 언어 구현이 어긋나는 단골 지점).
- 체크섬 값은 응답 헤더 `X-Bundle-Checksum: sha256=<hex>`로 전달한다 (body 밖 — body에 넣으면 자기 자신을 포함하는 순환).
- 검증 실패 시 저장하지 않고 재시도([sync-protocol.md](sync-protocol.md)). 검증 통과한 **수신 바이트 원본을 Raw Event Store에 그대로 보존**한다 (수신 원본 불변 원칙, 목표 계약의 서명 검증도 같은 바이트 대상).

## 미확정 요약

**영서 단독 결정(설계 후 이 문서에 기록)**: ① 전달 레코드 설계 일체(멱등 키·저장 구조·fan-out·retention·게시 상태↔전달 유형 매핑) ② Tenant Sync API 엔드포인트 계약([sync-protocol.md](sync-protocol.md)) ③ 번들에 실을 `source_events`·`evidences` 컬럼 선별(reader 자유 — 단 스키마 의존이므로 변경 감지 대상)

**진기 확인 대상(스키마)**: `tenant`·`tenant_credential` 정의 검토 — 인증 모델(sync-auth)과 함께 확정, 다르면 수축-확장으로 교체 ([ADR-0026](../adr/0026-ownership-boundary-db.md))

해소된 안건: ~~confidence 스케일~~ → 물리 스키마의 `confidence_level` enum(HIGH/MEDIUM/LOW) 채택. ~~risk_grade 존치~~ → 물리 스키마에 없음, 위험 등급 산정 주체 결정(TODO §2) 후 필요 시 확장-수축으로 추가. ~~ID 체계(UUIDv7 제안)~~ → 물리 스키마의 TEXT 도메인 ID 채택.
