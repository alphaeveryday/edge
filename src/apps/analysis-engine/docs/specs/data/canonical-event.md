---
doc_type: spec
status: Draft
owner: engineering
created: 2026-07-10
updated: 2026-07-10
related:
  - news-ontology-types.md
  - thread-types.md
  - disclosure-types.md
  - ../../baseline/analysis-engine-design.md
  - ../../baseline/data-ingestion.md
---
# Canonical Event 계약

## Summary

- `canonical_event`는 **원문에서 정규화된 "타입 있는 사건 관측" 1건**이다. 이 문서가 그 행의 **논리 필드 계약**을 소유한다 — [스레드 타입 카탈로그](thread-types.md)가 "별도 계약 문서 소유"로 위임했던 대상이 이 문서다.
- grain 경계 두 개를 고정한다: **문서 ≠ 이벤트**(기사·공시 1건 → 이벤트 0~N건), **실세계 사건 ≠ 이벤트**(같은 사건이 소스별 이벤트 여러 건 — 사건의 단일성은 `thread_id`가 표현하고, 이벤트 row는 병합하지 않는다).
- physical 배치·컬럼 타입은 비범위다(웨어하우스 계약 미결 — [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) Open questions 4와 동일 항목).

## 경계 (재서술 금지)

| 소유 | 문서 |
|---|---|
| 타입 53종·role·lifecycle·identity_roles 어휘 | [뉴스 ontology 타입 카탈로그](news-ontology-types.md) |
| threading 판정·novelty·교차소스 정합 | [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §6·§7 |
| thread 테이블(grain/PK) | [스레드 타입 카탈로그](thread-types.md) |
| `available_at`/`ingested_at` 수집 경계 | [Data Ingestion 디자인](../../baseline/data-ingestion.md) |
| 공시 정밀 사실 필드 | [공시 타입 카탈로그](disclosure-types.md) |

## 논리 필드 계약

| 필드군 | 필드 | 의미 |
|---|---|---|
| 식별 | `event_id` | PK. 관측 1건 식별자 |
| 타입 | `event_type_id` | 53종 카탈로그 leaf 중 하나 |
| 소스 | `source_class` | `NEWS` \| `DISCLOSURE`. **thread_key에 불참여** |
| 시간 | `available_at` | 코퍼스 기준 공개 시각 — PIT 축. precedence 세션 버킷은 저장 필드가 아니라 (이벤트, 설명일) 쌍의 조회 시 파생값 |
| identity | `identity.required` | 타입별 `identity_roles` 값. 정규화: 엔티티=엔티티 마스터 canonical id, 날짜=ISO-8601. **thread_key 재료.** 주체(anchor)/비주체 소비 등급은 §7 |
| identity | `identity.optional_discriminators` | key 문자열 불참여, 흡수/분리 축 |
| payload | `payload.stage` | lifecycle stage (`stage_sensitive` 타입만) — FOLLOW_UP 판정 재료 |
| payload | `payload.assertions[]` | `{field, value, precision}` — 규모 surprise·CORRECTION 판정 재료 |
| provenance | `provenance.document_id` | 원문 참조. 근거 스팬·교차소스 부착은 `event_evidence`(별도 grain) |

**이 행에 없는 것 (자주 혼동되는 3개):**

- `novelty_status` — threading 산출물. `event_thread_link` 소유.
- `dedup_cluster_id` — 텍스트 중복 축. 사건 계보와 별개(스레드 카탈로그 불변식 1).
- precedence 버킷(PRE_OPEN/…) — 저장 금지, 조회 시 파생(current-architecture 위험 절).

## Worked example — 교차소스 (뉴스 선행 → 공시 확정)

같은 실세계 사건 1건이 물리적으로 남는 전체 모습. canonical_event는 2행으로 **쪼개지고**, thread 1개로 **묶인다**. 아래는 `asof 2026-07-10`(재평가 승격 후) 기준 최종 상태다.

```json
{
  "canonical_event": [
    {
      "event_id": "ev_news_8f21",
      "event_type_id": "COMPANY.CONTRACT.SIGNING",
      "source_class": "NEWS",
      "available_at": "2026-07-09T08:50:00+09:00",
      "identity": {
        "required": { "SUPPLIER": "247540" },
        "optional_discriminators": { "CUSTOMER": null, "CONTRACT_OBJECT": null }
      },
      "payload": {
        "stage": "RUMORED",
        "assertions": [
          { "field": "CONTRACT_VALUE", "value": 1.0e12, "precision": "estimate" }
        ]
      },
      "provenance": { "document_id": "bigkinds:20260709.0851.1234" }
    },
    {
      "event_id": "ev_disc_c774",
      "event_type_id": "COMPANY.CONTRACT.SIGNING",
      "source_class": "DISCLOSURE",
      "available_at": "2026-07-09T18:02:00+09:00",
      "identity": {
        "required": { "SUPPLIER": "247540", "CUSTOMER": "북미완성차A", "CONTRACT_OBJECT": "양극재" },
        "optional_discriminators": {}
      },
      "payload": {
        "stage": "CONFIRMED",
        "assertions": [
          { "field": "CONTRACT_VALUE", "value": 1.2e12, "precision": "exact" },
          { "field": "CONTRACT_DURATION", "value": "2026-07~2029-06" }
        ]
      },
      "provenance": { "document_id": "dart:20260709800123" }
    }
  ],
  "event_thread": [
    {
      "thread_id": "th_<sha256(thread_key+opening_discriminators)[:32]>",
      "thread_key": "COMPANY.CONTRACT.SIGNING|CONTRACT_OBJECT=양극재|CUSTOMER=북미완성차A|SUPPLIER=247540",
      "event_type_id": "COMPANY.CONTRACT.SIGNING",
      "opened_at": "2026-07-09T08:50:00+09:00",
      "n_events": 2,
      "asof": "2026-07-10"
    }
  ],
  "event_thread_link": [
    { "event_id": "ev_news_8f21", "thread_id": "th_…", "novelty_status": "FIRST_IN_THREAD",
      "source_class": "NEWS", "asof": "2026-07-10" },
    { "event_id": "ev_disc_c774", "thread_id": "th_…", "novelty_status": "FOLLOW_UP_STAGE",
      "source_class": "DISCLOSURE", "asof": "2026-07-10" }
  ],
  "thread_discovery_snapshot": [
    { "event_id": "ev_news_8f21", "thread_id": null, "n_prior_events": 0,
      "days_since_prev_stage": null, "unknown_reason": "MISSING_IDENTITY_FIELD", "asof": "2026-07-09" },
    { "event_id": "ev_disc_c774", "thread_id": "th_…", "n_prior_events": 0,
      "days_since_prev_stage": null, "unknown_reason": null, "asof": "2026-07-09" }
  ],
  "event_evidence": [
    { "evidence_id": "evd_5510", "event_id": "ev_news_8f21", "source_class": "DISCLOSURE",
      "evidence_type": "FILING", "document_id": "dart:20260709800123",
      "fields_overridden": ["CONTRACT_VALUE", "CUSTOMER", "CONTRACT_DURATION"] }
  ]
}
```

읽는 법 — 이 예시가 계약의 핵심 규칙 5개를 그대로 시연한다:

1. **뉴스는 required(CUSTOMER 등) 부분 결측** → 발견 시점(07-09 asof) 판정은 `UNKNOWN`(snapshot에 그대로 보존, 카탈로그 불변식 5 `EMIT_UNKNOWN_LINK_ONLY`). 부분 identity로 thread를 열지 않는다.
2. **공시가 완전 identity로 thread를 연다**(07-09 18:02, 그 시점 asof에서는 `FIRST_IN_THREAD`). 다음 `asof`(07-10) 재평가에서 뉴스가 §7 0b 강등 매칭으로 귀속 승격된다.
3. **novelty는 계보 내 관측 시간순으로 재판정된다**: 승격 후 뉴스(08:50)가 `FIRST_IN_THREAD`, 공시(18:02)는 `FOLLOW_UP_STAGE`로 갱신. link는 최신 `asof` 판정을 담고, snapshot은 각자의 발견 시점을 불변으로 남긴다 — 그래서 위 JSON에서 link asof(07-10)와 snapshot asof(07-09)가 다르다. thread `opened_at`도 08:50으로 당겨져 precedence 앵커가 소문 시각이 된다.
4. **thread 테이블로 들어가는 이벤트 컬럼은 `event_id`뿐** — 나머지(novelty_status, n_prior_events, gap)는 전부 판정 산출물.
5. **숫자 권위 이동은 row 병합이 아니라 `event_evidence` 부착**(`fields_overridden`) — 두 이벤트의 `available_at`이 각자 보존되어 PIT가 유지된다.

## 검증 방법

- `identity.required` 필드 집합이 카탈로그 `identity_roles`와 53타입 전부 정확히 일치하는지(스레드 카탈로그 불변식 3 계승).
- 모든 행에 `available_at`·`source_class` 필수 — 결측은 수집 계약 위반이며 NULL로 통과시키지 않는다.
- `payload.assertions` 없는 이벤트가 `CORRECTION` 판정을 받지 않는지(§7 판정 재료 부재).
- 재평가 승격 시 link `asof` 갱신·snapshot 불변·thread `opened_at` 당김이 함께 일어나는지(위 예시 재현 테스트).

## Open questions

1. 물리 저장(웨어하우스) 스키마 — [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) Open questions 4와 공동 마감.
2. `payload.assertions`의 field 사전 — 공시 정밀 사실([공시 타입 카탈로그](disclosure-types.md))과 필드명 정렬.
