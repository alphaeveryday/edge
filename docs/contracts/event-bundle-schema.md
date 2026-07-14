# Event Bundle 인터페이스 계약 (진기-영서)

> **계약 문서** — 이 파일의 변경은 진기·영서 공동 승인 대상이다(CODEOWNERS).

> **상태: 초안 (ALPHA-356, 2026-07-15)** — `[합의 필요]` 표기 항목은 진기-영서 합의 세션에서 확정한다. 표기 없는 항목은 기확정 결정(ADR-0015·0021)에서 도출된 제안값이다.

진기-영서 인터페이스는 **"Cloud Event Store 스키마 + 번들 생성 규칙"** 하나로 고정한다. 이 계약 변경은 반드시 양자 합의.

- 오너십 경계: **김진기** — Data Pipeline → Common Analysis Engine → Cloud Event Store + **Event Bundle 생성까지** / **조영서** — **Tenant Sync API부터 이후 전부** ([../README.md](../README.md)의 팀 오너십 참조).
- **번들 생성 규칙(cursor 발번·outbox fan-out)** 은 이 계약의 일부다 — 확정 스펙은 [sync-protocol.md](sync-protocol.md): 테넌트별 outbox fan-out 시점(공통 이벤트 1건 → 테넌트별 전달 레코드 N건 생성)에 각 테넌트의 sequence를 발번하고, Event Bundle은 이 outbox를 cursor 순으로 묶어 생성한다.
- 전송 단위: Event Bundle (신규 이벤트 + 정정 이벤트 + 무효화 이벤트 포함). 무결성은 번들 단위 SHA-256 체크섬.

## ID 체계

- 모든 도메인 ID는 **Cloud가 발번하는 UUIDv7** (시간 정렬 → 인덱스 지역성, 발번 조정 불요). 종류: `event_id`, `candidate_id`, `evidence_id`.
- On-Prem 멱등 upsert 키 = 이 도메인 ID. 전달(delivery) 단위의 멱등 키 = `(tenant_id, cursor)`.
- 정정·무효화는 대상을 `target_event_id`로 참조한다. 정정으로 생기는 새 콘텐츠는 **새 `candidate_id`** 를 받는다 (On-Prem 리비전 분리 모델의 소스 — [../domain/state-machine.md](../domain/state-machine.md)).

## Cloud Event Store 논리 스키마 (계약 표면)

물리 DDL·인덱스는 진기 소유 내부 구현이다. 계약은 아래 **논리 필드**까지다 — 번들 페이로드가 여기서 파생된다.

**events** — 가격 변동 이벤트 (비개인화 공통)

| 필드 | 타입 | 설명 |
|---|---|---|
| event_id | uuid(v7) | PK |
| event_type | text | MVP: `PRICE_MOVEMENT` 고정 (enum 확장은 로드맵) |
| market | text | `KRX` (MVP 한국주식 한정) |
| ticker | text | 종목 코드 |
| name | text | 종목명 (표시용 스냅샷) |
| change_rate | numeric | 등락률 (%) |
| direction | text | `UP` / `DOWN` |
| base_time | timestamptz | 기준 시각 (이벤트 탐지 기준점) |
| status | text | `ACTIVE` / `CORRECTED` / `INVALIDATED` — Cloud 측 관리 상태 |
| created_at | timestamptz | |

**explanation_candidates** — AI 설명 후보 (이벤트당 1..n, MVP는 1)

| 필드 | 타입 | 설명 |
|---|---|---|
| candidate_id | uuid(v7) | PK |
| event_id | uuid | FK → events |
| analysis_type | text | MVP: `PRICE_MOVEMENT` |
| body | text | 설명 후보 문구 ("공개 정보 기반 변동 요인 후보" 표현 원칙 준수) |
| confidence | numeric | 신뢰도 0.00~1.00 `[합의 필요 — 산출 스케일·의미는 준영 산출물 기준]` |
| counter_factors | text[] | 반대 요인 |
| risk_grade | text | `[합의 필요 — 산정 주체(Cloud AI vs On-Prem Compliance)가 TODO §2 미결. Cloud 산정으로 확정되면 이 필드, 아니면 제거]` |
| created_at | timestamptz | |

**evidences** — 설명 근거 (이벤트당 0..n)

| 필드 | 타입 | 설명 |
|---|---|---|
| evidence_id | uuid(v7) | PK |
| event_id | uuid | FK → events |
| kind | text | `NEWS` / `DISCLOSURE` / `PRICE` / `FLOW` |
| payload | jsonb | kind별 형상: NEWS·DISCLOSURE = `{title, source, published_at, url}` / PRICE·FLOW = `{metric, value, window}` `[합의 필요 — 파이프라인 정제 산출물 필드에 맞춰 확정]` |
| created_at | timestamptz | |

**tenant_outbox** — 테넌트별 전달 레코드 (fan-out 산출물, 번들 생성의 유일한 소스)

| 필드 | 타입 | 설명 |
|---|---|---|
| tenant_id | uuid | PK 일부 |
| cursor | bigint | PK 일부. **테넌트별 단조 증가** — fan-out 트랜잭션 안에서 `해당 테넌트 last cursor + 1`로 발번 |
| delivery_type | text | `NEW` / `CORRECTION` / `INVALIDATION` |
| event_id | uuid | 대상 이벤트 |
| reason | text | CORRECTION·INVALIDATION 필수 (Super Admin 사유 입력 필수 정책) |
| enqueued_at | timestamptz | |

- **fan-out 직렬화 규칙**: 한 테넌트의 outbox 행 생성은 **단일 writer(fan-out 워커)가 테넌트별로 직렬 처리**하고, cursor 발번과 행 INSERT는 같은 트랜잭션이다. DB sequence(`nextval`)를 쓰지 않는다 — sequence는 트랜잭션 밖에서 발번되어 **커밋 순서 ≠ cursor 순서**가 될 수 있고, 그 gap은 소비자가 이벤트를 영구히 건너뛰게 만든다. 이 규칙이 "순차 소비만으로 순서 담보"(ADR-0015)의 성립 조건이다.
- outbox retention·정리 정책: `[합의 필요 — 전 테넌트 소비 완료분 정리 주기, retention 초과 테넌트의 full resync 경로]`

## Event Bundle JSON 구조

번들은 outbox를 cursor 순으로 묶은 것이다. 엔트리 공통 봉투(envelope) + delivery_type별 페이로드:

```json
{
  "bundle_id": "0197...uuid7",
  "tenant_id": "t-...",
  "generated_at": "2026-07-15T09:00:00Z",
  "cursor_from": 101,
  "cursor_to": 180,
  "entries": [
    {
      "cursor": 101,
      "delivery_type": "NEW",
      "event": { "event_id": "...", "event_type": "PRICE_MOVEMENT", "market": "KRX",
                 "ticker": "005930", "name": "삼성전자", "change_rate": 4.2,
                 "direction": "UP", "base_time": "..." },
      "candidates": [ { "candidate_id": "...", "analysis_type": "PRICE_MOVEMENT",
                        "body": "...", "confidence": 0.82, "counter_factors": ["..."] } ],
      "evidences": [ { "evidence_id": "...", "kind": "NEWS",
                       "payload": { "title": "...", "source": "...", "published_at": "...", "url": "..." } } ]
    },
    {
      "cursor": 102,
      "delivery_type": "CORRECTION",
      "target_event_id": "...",
      "reason": "근거 공시 정정",
      "event": { "...정정 반영된 이벤트 전체 (신규와 동일 형상)..." },
      "candidates": [ { "candidate_id": "새 ID", "...": "정정된 설명 후보 전체" } ],
      "evidences": [ "...(정정 반영분 전체)..." ]
    },
    {
      "cursor": 103,
      "delivery_type": "INVALIDATION",
      "target_event_id": "...",
      "reason": "오탐지 이벤트"
    }
  ]
}
```

- **NEW·CORRECTION은 전체 상태 전달(full snapshot)** 이다 — diff/patch가 아니다. On-Prem은 도메인 ID 기준 멱등 upsert만 하면 되고, 부분 갱신 병합 로직이 필요 없다.
- CORRECTION 수신 시 On-Prem 동작(기존 발행분 UNPUBLISHED → 새 리비전 REVIEW_REQUIRED)은 [../domain/state-machine.md](../domain/state-machine.md) 소관 — 이 계약은 "정정분이 전체 형상 + 사유 + 대상 참조로 도착한다"까지만 정의한다.

## 체크섬 (무결성)

- **대상 바이트: HTTP 응답 body 전체의 UTF-8 바이트열 그대로.** 서버는 직렬화한 바이트를 그대로 전송·보관하고, Sync Agent는 **재직렬화 없이 수신 바이트에 대해** SHA-256을 계산해 대조한다 (canonical-JSON 정규화 규칙을 계약에 넣지 않기 위한 선택 — 정규화는 양 언어 구현이 어긋나는 단골 지점).
- 체크섬 값은 응답 헤더 `X-Bundle-Checksum: sha256=<hex>`로 전달한다 (body 밖 — body에 넣으면 자기 자신을 포함하는 순환).
- 검증 실패 시 저장하지 않고 재시도([sync-protocol.md](sync-protocol.md)). 검증 통과한 **수신 바이트 원본을 Raw Event Store에 그대로 보존**한다 (수신 원본 불변 원칙, 목표 계약의 서명 검증도 같은 바이트를 대상으로 한다).

## 미확정 요약 (합의 세션 안건)

1. `confidence` 스케일·의미 (준영 산출물 정의에 종속)
2. `risk_grade` 존치 여부 — 위험 등급 산정 주체 결정(TODO §2)에 종속
3. `evidences.payload` kind별 필드 확정 (파이프라인 정제 산출물 대조)
4. outbox retention·full resync 경로
5. 일일 이벤트 규모 가정 검증 ([sync-protocol.md](sync-protocol.md) 기재값)
