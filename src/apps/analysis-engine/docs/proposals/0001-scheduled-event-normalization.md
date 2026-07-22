---
doc_type: proposal
status: Proposed
owner: engineering
created: 2026-07-16
updated: 2026-07-20
related:
  - 0003-market-expectation.md
  - ../specs/data/canonical-event.md
  - ../specs/data/thread-types.md
---
# 제안 0001 — 예정 발표 일정의 정규화와 사건 계보 통합 (scheduled_event)

## Context

금리 결정, 물가 발표, 지수 종목 교체처럼 **날짜가 미리 정해진 발표**들이 지금은 문서·공지 형태로 흩어져 있어 기계가 쓰지 못한다. 그 결과:

1. **경쟁 사건 완전성** — "그 시각에 다른 주요 발표는 없었다"는 예정 발표 달력이 있어야 성립한다. 없으면 사후 검색 의존.
2. **예정/돌발 구분** — 같은 내용이라도 예정된 발표와 돌발 속보는 반응 해석이 다르다. 판정은 발표 시각이 아니라 **예고의 존재**로 해야 한다.
3. **반응 측정 전제** — 발표 전·후 창을 자르려면 예정 시각이 사전 레코드로 존재해야 한다.

## Goals

- 예정 발표 일정을 **하나의 표준 레코드(`scheduled_event`)**로 정규화한다.
- 같은 계열 발표를 **하나의 사건 계보(thread)**로 묶는다.
- "그날 예정된 다른 발표가 있었나"를 기계가 답할 수 있게 한다.

## Non-goals

- 숫자 시계열(정형) 수집 — 이 문서 범위 밖. 대상은 비정형 원천(한국은행 공보, 통계청 일정, KRX 공지, MSCI 보도자료, 파생 만기 규칙)뿐.
- 물리 스키마·테이블 배치 확정 — 논리 계약만 고정.

## Design

### `scheduled_event` 레코드

저장: `data/interim/events/scheduled_events.jsonl`. PK: `(series_id, effective_at)`.

| 필드 | 뜻 | 예 |
|---|---|---|
| `series_id` | 발표 계열 식별자 | `BOK_MPC`, `KOSTAT_CPI`, `KRX_K200_REBAL`, `MSCI_KR_REVIEW`, `KRX_OPT_EXPIRY` |
| `event_type` | 온톨로지 타입 참조 | 타입 레지스트리 id |
| `announce_at` | 일정이 공표된 시각 | `2025-12-20T10:00+09:00` |
| `effective_at` | 발표·시행 예정 시각 | `2026-01-16T09:50+09:00` |
| `periodicity` | 정기 여부·주기 | `8/year`, `monthly`, `semiannual`, `ad-hoc` |
| `entities` | 관련 주체·종목 | `["KOSPI200"]` |
| `source_url` | 원문 링크 (수동 수집분 필수) | 공보·보도자료 URL |
| `status` | `scheduled` / `realized` / `cancelled` | |
| `realized_event_id` | 실현 후 연결되는 정규 사건 id | `ev_news_...` |

### 통합 규칙 3개

1. **시점 고정 매핑** — 예정 레코드는 발표 **전**에는 정규 사건이 아니다. `effective_at`이 지나 실제 발표가 관측되면 [정규 사건](../specs/data/canonical-event.md)이 생성되고 `realized_event_id`로 연결된다. 이 링크의 존재가 곧 "예정된 발표였다" 근거(별도 판정 불필요).
2. **경쟁 사건 등록** — 원인 판정 시 조회하는 경쟁 사건 타임라인에 `scheduled_event`가 자동 포함된다. 판정 창과 `effective_at`이 겹치면 경쟁 후보로 나열.
3. **계보 연결** — 같은 `series_id`는 하나의 [사건 계보](../specs/data/thread-types.md)로 묶인다. 계보로 "지난 발표 때 반응"(발화 이력)과 "이번이 새로운가"(신규성) 판정에 같은 축을 쓴다.

### ERD 통합 부속 (논리)

기존 테이블 무변경, 신규 3개 + 관계 5개. 기존 축과의 접점은 `실현_소스이벤트_ID` 하나. 경쟁 사건은 조회 뷰(`정규화이벤트 ∪ 예정발표`), 계보는 스레드 키 `CAL:<발표계열_ID>`로 해결.

```dbml
Table "발표계열" as calendar_series {
  "발표계열_ID" text [pk, note: 'BOK_MPC, KOSTAT_CPI, KRX_K200_REBAL, MSCI_KR_REVIEW, KRX_OPT_EXPIRY']
  "계열명" text [not null]
  "주관_행위자_ID" text [not null]
  "이벤트_유형_코드" text
  "주기" text [not null]
  "기본_시장시계열_ID" text
}
Table "예정발표" as scheduled_event {
  "예정발표_ID" text [pk]
  "발표계열_ID" text [not null]
  "공표시각" timestamp
  "예정시각" timestamp [not null]
  "상태" text [not null, note: 'SCHEDULED, REALIZED, CANCELLED']
  "실현_소스이벤트_ID" text [note: '링크 존재 = 예정된 발표 근거. NULL 허용']
  "출처_URL" text [not null]
  "적재시각" timestamp
}
Table "예정발표_엔터티역할" as scheduled_event_argument {
  "예정발표_ID" text [not null]
  "역할_코드" text [not null, note: '대상지수, 편입, 편출']
  "엔터티_ID" text [not null]
}
Ref: calendar_series."주관_행위자_ID" > actor."행위자_ID"
Ref: calendar_series."기본_시장시계열_ID" > market_series."시장시계열_ID"
Ref: scheduled_event."발표계열_ID" > calendar_series."발표계열_ID"
Ref: scheduled_event."실현_소스이벤트_ID" > canonical_event."소스이벤트_ID"
Ref: scheduled_event_argument."예정발표_ID" > scheduled_event."예정발표_ID"
Ref: scheduled_event_argument."엔터티_ID" > entity."엔터티_ID"
```

## Alternatives

간단히만 기록(상세 검토 예정): 예정 일정을 별도 정규화 없이 뉴스 사건과 동일 파이프라인으로 처리하는 안이 있으나, 예정은 "사건"이 아니라 "사건의 예고"라 `document`/`정규화이벤트` enum을 확장해야 해 경계가 흐려진다. 그래서 별도 레코드 + 링크(`실현_소스이벤트_ID`) 방식을 택했다.

## Rollout — 승격 조건

- 예시 5건 수동 정규화: 금리 결정·물가 발표·KOSPI200 정기 변경·MSCI 반기 리뷰·옵션 만기 각 1.
- 검증 규칙 3개: PK 중복 없음 / `realized`는 `realized_event_id` 필수 / `effective_at` 과거인데 `scheduled`로 남은 레코드 리포트.
- 경쟁 사건 조회 1건이 이 레코드를 실제 반환하는 통합 확인.
- 승인 시 → `baseline`/`specs` 흡수 + `decisions/` ADR 증류, ERD는 reference로 편입.

## References

- 정규 사건 계약: [canonical-event.md](../specs/data/canonical-event.md)
- 계보 계약: [thread-types.md](../specs/data/thread-types.md)
- 서프라이즈 강등(이 달력을 전제): [제안 0003](0003-market-expectation.md)
