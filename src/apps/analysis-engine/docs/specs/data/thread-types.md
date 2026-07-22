---
doc_type: design
status: Draft
owner: engineering
created: 2026-07-08
updated: 2026-07-10
related:
  - entity-master.md
  - ../../baseline/analysis-engine-design.md
  - ../../baseline/analysis-engine-design.md
---
# 뉴스 이벤트 스레드 타입 카탈로그

## Summary

`news_thread_contract_v0_1.yaml` v0.1.0은 **53개 EVENT_TYPE_ID 각각이 어떤 identity tuple을 기준으로 같은 사건 계보(thread)로 묶이는지**를 고정하는 current logical contract다. 이 문서는 그 계약 중 오래 유지돼야 하는 부분만 남긴다.

- current: `novelty_status` 어휘, 4개 logical table의 grain/키 경계, `thread_key` 파생 입력, 53개 type의 `identity.required`/`identity.optional_discriminators` 카탈로그
- 링크로 대체: `thread_key` 직렬화 방식, `thread_id` 해시 생성, novelty status 판정 순서와 예외 처리 상세는 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 소유
- 경계: `dedup_cluster_id`는 텍스트 중복 묶음이고 `thread_id`는 구조화 identity 계보다. 둘은 같은 축이 아니며, 상호 대체할 수 없다.

이 문서는 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md)의 thread 설계에서 **타입 사전과 계약 경계**만 분리해 남긴 축약본이다. 물리 DB schema, 컬럼 타입, 인덱스, 판정 알고리즘 재서술은 다루지 않는다.

## Context

Threading은 canonical ontology event가 이미 조립된 뒤에 수행된다. 따라서 이 레이어는 raw 기사 dedup이나 event extraction을 다시 결정하지 않고, 이미 주어진 `event_type_id`와 role-filled argument를 받아 **같은 사건의 계보를 어떤 키로 보존할지**만 결정한다.

상위 구조는 [현재 아키텍처](../../baseline/analysis-engine-design.md)가 소유한다. 엔티티 역할의 의미와 role 해소 품질은 [엔티티 마스터·해소 레이어](entity-master.md), 구현 lineage와 출력 JSONL 예시는 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md)이 소유한다.

## Problem

스레드 규칙이 알고리즘 문서와 입출력 표에 섞여 있으면 세 가지 문제가 생긴다.

1. 같은 `EVENT_TYPE_ID`를 무엇으로 동일 사건으로 볼지 빠르게 확인할 수 없다.
2. `dedup_cluster_id`와 `thread_id`의 경계가 흐려져 텍스트 중복과 사건 계보가 혼동된다.
3. 새 consumer가 thread 데이터를 붙일 때 table grain보다 구현 세부에 의존하게 된다.

따라서 type catalog와 contract boundary를 별도 spec으로 고정해야 한다.

## Goals

- `novelty_status` 어휘와 contract 의미를 한곳에 고정한다.
- `event_thread`, `event_thread_link`, `thread_discovery_snapshot`, `hq_run_evidence`의 grain/PK/핵심 키를 logical 수준에서 명시한다.
- `thread_key`가 어떤 입력만으로 파생되는지와 어떤 입력으로는 파생되면 안 되는지를 명시한다.
- 53개 EVENT_TYPE_ID 전부가 정확히 한 번씩 커버된다는 규칙과 type별 `identity_roles` 카탈로그를 남긴다.

## Non-goals

- `thread_key` 문자열 조립 순서, `thread_id` 해시 방식, status 판정 우선순위 같은 **결정 알고리즘** 재서술
- `dedup_cluster_id` 생성 규칙 재정의
- physical table/warehouse schema, 컬럼 타입, 인덱스, 파티셔닝 정의
- canonical event assembly 이전 단계(raw news, entity extraction, ontology assembly) 설명

결정 로직은 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md)으로 링크한다.

## 현행 계약 상태

| 자산 | 상태 | 메모 |
|---|---|---|
| `news_thread_contract_v0_1.yaml` | current | thread type canonical source. `meta.version = 0.1.0`, `type_count = 53` |
| `event_thread` / `event_thread_link` / `thread_discovery_snapshot` / `hq_run_evidence` | current logical contract | 이름·grain·키 경계는 확정. physical schema는 이 문서 비범위 |
| physical warehouse mapping | 제안/미정 | logical 이름을 어떤 저장소/물리명으로 배치할지는 별도 계약 문서 소유 |
| `../../design/analysis-engine.md`의 알고리즘 설명 | 제안/분리 소유 | 결정 순서와 구현 세부는 해당 디자인 문서로 링크 |


## novelty_status 어휘

모든 type은 동일한 5개 vocabulary를 공유한다. type마다 status set이 달라지지 않는다.

| novelty_status | 계약 의미 | 계약상 함의 |
|---|---|---|
| `FIRST_IN_THREAD` | 해당 structured identity lineage에서 처음 관측된 canonical event | `event_thread`를 여는 최초 관측점이 된다 |
| `FOLLOW_UP_STAGE` | 기존 thread 내부의 후속 lifecycle/stage 갱신 | 기존 `thread_id`를 유지한 채 stage/state만 진전된다 |
| `CORRECTION` | 같은 thread 내부에서 정정·취소·수정 계열 갱신 | 같은 lineage 안의 corrective transition으로 해석된다 |
| `DUPLICATE_REBROADCAST` | 같은 사건 계보 안에서 텍스트 재송고/중복 유통으로 해석된 관측 | text-level duplicate signal을 보존하되 thread lineage는 유지한다 |
| `UNKNOWN` | thread identity를 안전하게 확정할 수 없음 | `thread_id`는 null일 수 있으며, 이유는 `unknown_reason` 계열 필드로 전달된다 |

상태 판정 순서, correction marker, null-handling 세부는 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 소유다. 이 문서가 고정하는 것은 **어휘와 각 어휘가 의미하는 소비 계약**뿐이다.

## 교차소스 thread 참여 (제안)

thread 계약은 **소스 중립**이다. 하나의 `thread_id`는 뉴스와 DART 공시에서 각각 정규화된 `canonical_event`를 함께 가질 수 있다. 같은 실제 사건이 두 소스에서 보도·공시될 때 이중 계산을 막기 위한 확장이며, 정합 알고리즘(대상 일치 + 내용 유사 판정, 필드별 권위 분리)은 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §6이 소유한다.

| 계약 요소 | 의미 |
|---|---|
| `source_class` | 각 event-level thread 귀속이 가지는 소스 구분(`NEWS` \| `DISCLOSURE`). `thread_key`에는 포함되지 않는다 — 소스가 달라도 identity가 같으면 같은 thread다 |
| source-agnostic 불변식 | 같은 `event_type_id` + `identity.required` 값이면 소스와 무관하게 같은 `thread_id`. 소스별로 별도 thread를 만들지 않는다 |
| 교차소스 확인 status | 다른 `source_class`의 event가 기존 thread를 확인하는 것은 `DUPLICATE_REBROADCAST`(같은 소스 텍스트 재송고)와 다르다. 별도 의미를 가지며, 권위 숫자가 유의하게 다르면 `CORRECTION`으로 본다 |
| 권위(precedence) | 시점·선후는 최초 소스(대개 뉴스), 규모·정밀 사실은 공시가 권위. thread header의 stage/opened 계산은 이 규칙을 따른다 |

교차소스 확인을 `novelty_status` 확장으로 둘지 `event_thread_link.link_kind`로 둘지는 미결이다(위 디자인 문서 Open question). 이 문서는 **thread가 소스 중립이라는 계약**만 고정하고, 판정·표현 방식은 디자인 문서로 링크한다.

## Logical table 계약

아래 4개 이름은 logical contract 이름이다. 정확한 physical 배치와 타입은 별도 계약 문서가 소유한다.

| logical table | grain | PK | 핵심 키 / 의미 | 생산 → 소비 |
|---|---|---|---|---|
| `event_thread` | 하나의 structured event lineage당 1행 | `thread_id` | `thread_id`, `thread_key`, `event_type_id`가 thread header를 정의한다. 동일 lineage의 현재 stage/opened 시점은 이 헤더에 귀속된다. | 생산: Analysis Engine threading · 소비: 후속 설명 계층, lineage 검토 |
| `event_thread_link` | 하나의 canonical `event_id`에 대한 thread 귀속 1행 | `event_id` | `event_id` ↔ `thread_id` 연결, `novelty_status`, `source_class`(`NEWS` \| `DISCLOSURE`), `asof`가 event-level thread 판정을 나타낸다. `UNKNOWN`일 때 `thread_id`는 null 가능 | 생산: Analysis Engine threading · 소비: novelty 판단, event↔thread 조인 |
| `thread_discovery_snapshot` | 하나의 canonical `event_id`에 대한 discovery 시점 snapshot 1행 | `event_id` | `thread_id`, `n_prior_events`, `days_since_prev_stage`, `unknown_reason`이 discovery 시점 관측을 표현한다. novelty 판정 값 자체는 `event_thread_link.novelty_status`가 소유한다 | 생산: Analysis Engine threading · 소비: 설명 품질 점검, novelty QA |
| `hq_run_evidence` | 하나의 `hq_run_id` 내부 evidence item당 1행 | `(hq_run_id, evidence_id)` | HQ run이 thread-aware explanation으로 넘긴 evidence trace. `event_id`와 `document_id_or_null`이 provenance를 고정한다 | 생산: Analysis Engine threading/O6 handoff · 소비: Explanation Engine HQ/L4 |

### 테이블 경계 메모

- `event_thread`는 **사건 계보 헤더**다. 개별 기사나 개별 evidence row를 담지 않는다.
- `event_thread_link`와 `thread_discovery_snapshot`는 둘 다 event-grain이지만, 전자는 **귀속 결과**, 후자는 **발견 시점 관측값**을 소유한다.
- `hq_run_evidence`는 thread 자체가 아니라 **설명 근거의 provenance bridge**다.

## 핵심 불변식

### 1. `dedup_cluster_id`와 `thread_id`는 같은 값이 될 수 없다

`dedup_cluster_id`는 near-duplicate text cluster이고, `thread_id`는 구조화된 사건 identity lineage다. 전자는 문서 중복 묶음, 후자는 사건 계보다. 하나를 다른 하나의 surrogate key로 쓰면 텍스트 재배포와 실제 lifecycle 후속 사건을 구분할 수 없게 된다.

### 2. `thread_id`는 thread contract 입력에서만 파생된다

`thread_id`가 대표하는 lineage는 아래 계약 입력에만 의존해야 한다.

- `event_type_id`
- `identity.required`
- `identity.optional_discriminators`

즉, 기사 문장 유사도·headline 표면형·임의의 downstream 설명 상태는 thread identity를 직접 바꾸는 입력이 아니다.

### 3. `identity.required`는 source profile의 `identity_roles`와 정확히 같아야 한다

53개 모든 type에서 `identity.required`는 `event_type_profiles_v0_1.identity_roles`의 복사본이다. 이 계약은 “event에 어떤 역할이 존재하면 좋다”가 아니라, **thread lineage를 고정하는 최소 역할 집합이 무엇인가**를 type별로 못 박는다.

### 4. optional discriminator는 thread를 더 잘게 나눌 수는 있어도 required identity를 대체하지 못한다

`identity.optional_discriminators`는 값이 존재할 때만 같은 `event_type_id` 안에서 lineage를 더 구체화한다. 반대로 required role이 비어 있는 사건을 optional 값만으로 thread에 강제 귀속시키면 안 된다.

### 5. missing identity의 기본 정책은 53개 type 모두 동일하다

모든 type이 `missing_identity_policy = EMIT_UNKNOWN_LINK_ONLY`를 사용한다. 즉 identity를 안전하게 채우지 못하면 synthetic thread를 억지로 만들지 않고, event-grain 결과만 남긴다. 단 `UNKNOWN` link는 종결이 아니라 재평가 대상이다 — 이후 `asof` 판정에서 thread 귀속으로 승격될 수 있다(강등 매칭·승격 규칙은 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §7 소유).

## `thread_key` 파생 입력 계약

`thread_key`는 **type-specific identity contract의 정규화된 표현**이다. 알고리즘 세부는 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md)이 소유하지만, 어떤 입력이 key에 참여하는지는 이 문서가 고정한다.

| 입력 | 왜 key에 들어가는가 |
|---|---|
| `event_type_id` | 서로 다른 ontology leaf type을 같은 lineage로 합치지 않기 위한 최상위 partition |
| `identity.required` | 같은 사건이라고 주장하기 위해 반드시 일치해야 하는 최소 identity tuple |
| `identity.optional_discriminators` | 값이 있을 때만 같은 `event_type_id` 내부를 더 좁게 나누는 보조 분기 |

### 해석 규칙

- `identity.required`는 누락되면 lineage를 확정할 수 없는 역할 집합이다.
- `identity.optional_discriminators`는 같은 사건의 후속/정정과, 실제로 다른 사건인 경우를 분리하는 보조 축이다.
- required 일부(비주체 role) 결측 이벤트의 강등 매칭과 `UNKNOWN` 재평가는 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) §7이 소유한다. 부분 identity로 thread를 신설할 수는 없다(불변식 4·5).
- `thread_key` 파생 규칙은 type catalog가 바뀌지 않는 한 안정적이어야 한다. 구현 교체는 가능하지만 입력 의미를 바꾸면 contract 변경이다.

## 53-type coverage 규칙

YAML top-level `meta.type_count`는 `53`이고, 이 숫자는 설명용 메모가 아니라 **검증해야 하는 계약 값**이다.

1. `types` 아래 EVENT_TYPE_ID는 정확히 53개여야 한다.
2. `types.keys()` 집합은 source profile event type 집합과 정확히 일치해야 한다.
3. 각 EVENT_TYPE_ID는 정확히 한 번만 나타나야 한다.
4. 각 type의 `identity.required`는 source profile `identity_roles`와 정확히 같아야 한다.
5. 각 type의 `novelty_statuses`는 동일한 5개 vocabulary를 공유해야 한다.

아래 카탈로그는 그 exact coverage를 사람이 읽을 수 있는 형태로 옮긴 것이다.

## 스레드 타입 카탈로그

### 읽는 법

- `identity.required`: 같은 thread라고 주장하려면 반드시 채워져야 하는 역할
- `identity.optional_discriminators`: 값이 있을 때만 thread를 더 세분화하는 역할
- 모든 row는 같은 type id를 key로 삼는 source profile과 1:1 대응한다

### COMPANY (24)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `COMPANY.ALLIANCE.PARTNERSHIP` | `PARTNER, PARTNER_2, PROJECT` | `PRODUCT, EFFECTIVE_DATE` |
| `COMPANY.CAPITAL.DIVIDEND_DECISION` | `ISSUER` | `AMOUNT, OLD_VALUE, NEW_VALUE, RECORD_DATE, PAYMENT_DATE` |
| `COMPANY.CAPITAL.EQUITY_ISSUANCE` | `ISSUER` | `AMOUNT, PRICE, QUANTITY, INVESTOR, USE_OF_PROCEEDS, EFFECTIVE_DATE` |
| `COMPANY.CAPITAL.IPO` | `ISSUER` | `EXCHANGE, PRICE, AMOUNT, QUANTITY, EFFECTIVE_DATE` |
| `COMPANY.CAPITAL.SHARE_BUYBACK` | `ISSUER, ANNOUNCED_DATE` | `BUYBACK_VALUE, SHARES, EFFECTIVE_DATE, DURATION` |
| `COMPANY.CAPITAL.STOCK_SPLIT` | `ISSUER` | `OLD_VALUE, NEW_VALUE, RECORD_DATE, EFFECTIVE_DATE` |
| `COMPANY.COMMERCIAL.MARKET_ENTRY` | `ISSUER` | `GEOGRAPHY, PRODUCT, AMOUNT, EFFECTIVE_DATE` |
| `COMPANY.COMMERCIAL.PRICING_ACTION` | `ISSUER` | `PRODUCT, PRICE, OLD_VALUE, NEW_VALUE, GEOGRAPHY, EFFECTIVE_DATE` |
| `COMPANY.CONTRACT.SIGNING` | `SUPPLIER, CUSTOMER, CONTRACT_OBJECT` | `CONTRACT_VALUE, CONTRACT_DURATION, EFFECTIVE_DATE` |
| `COMPANY.EARNINGS.GUIDANCE_CHANGE` | `ISSUER, METRIC, REPORTING_PERIOD` | `OLD_VALUE, NEW_VALUE, GUIDANCE_RANGE` |
| `COMPANY.EARNINGS.RESULT_RELEASE` | `ISSUER, REPORTING_PERIOD` | `METRIC, ACTUAL_VALUE, CONSENSUS_VALUE` |
| `COMPANY.FINANCING.DEBT_ISSUANCE` | `ISSUER` | `AMOUNT, PRICE, INTEREST_RATE, MATURITY_DATE, RATING_AGENCY, DEBT_INSTRUMENT` |
| `COMPANY.INVESTMENT.STAKE_ACQUISITION` | `INVESTOR, TARGET_COMPANY` | `OWNERSHIP_RATIO, DEAL_VALUE, SELLER` |
| `COMPANY.LEGAL.LAWSUIT` | `DEFENDANT, PLAINTIFF, LEGAL_ISSUE, COURT` | `PENALTY_VALUE` |
| `COMPANY.LEGAL.REGULATORY_ACTION` | `AUTHORITY, TARGET_COMPANY, LEGAL_ISSUE` | `PENALTY_VALUE, COURT, EFFECTIVE_DATE` |
| `COMPANY.MANAGEMENT.EXECUTIVE_CHANGE` | `ISSUER` | `PERSON, POSITION, EFFECTIVE_DATE, REASON` |
| `COMPANY.M_AND_A.ACQUISITION` | `ACQUIRER, TARGET_COMPANY` | `SELLER, DEAL_VALUE, OWNERSHIP_RATIO, AUTHORITY, EFFECTIVE_DATE` |
| `COMPANY.M_AND_A.MERGER` | `MERGING_ENTITY` | `TARGET_COMPANY, ACQUIRER, SELLER, DEAL_VALUE, OWNERSHIP_RATIO, EFFECTIVE_DATE` |
| `COMPANY.OWNERSHIP.INSIDER_TRANSACTION` | `PERSON, ISSUER` | `QUANTITY, PRICE, AMOUNT, EFFECTIVE_DATE` |
| `COMPANY.PRODUCT.CERTIFICATION` | `ISSUER, PRODUCT, AUTHORITY` | `STANDARD, EFFECTIVE_DATE` |
| `COMPANY.PRODUCT.LAUNCH` | `ISSUER, PRODUCT` | `PRODUCT_FAMILY, CUSTOMER, TECH_NODE, EFFECTIVE_DATE, QUANTITY` |
| `COMPANY.PRODUCTION.CAPACITY_CHANGE` | `ISSUER` | `FACILITY, PRODUCT, QUANTITY, AMOUNT, LOCATION, EFFECTIVE_DATE` |
| `COMPANY.RESTRUCTURING.SPINOFF` | `PARENT, SPUNOFF_UNIT` | `SHAREHOLDER, EFFECTIVE_DATE, AMOUNT` |
| `COMPANY.WORKFORCE.LAYOFF` | `ISSUER` | `QUANTITY, LOCATION, EFFECTIVE_DATE, REASON` |

### EXOGENOUS (6)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `EXOGENOUS.ACCIDENT.OPERATIONAL_DISRUPTION` | `OPERATOR, FACILITY, PRODUCT` | `LOCATION, CAPACITY_SHARE, EFFECTIVE_DATE` |
| `EXOGENOUS.CONFLICT.OUTBREAK` | `LOCATION` | `GEOGRAPHY, COMMODITY, EFFECTIVE_DATE` |
| `EXOGENOUS.CONFLICT.RESOLUTION` | `LOCATION` | `GEOGRAPHY, AUTHORITY, EFFECTIVE_DATE` |
| `EXOGENOUS.CYBER.SERVICE_DISRUPTION` | `OPERATOR, SERVICE` | `CUSTOMER, LOCATION, EFFECTIVE_DATE` |
| `EXOGENOUS.DISASTER.OCCURRENCE` | `HAZARD` | `LOCATION, GEOGRAPHY, QUANTITY, AMOUNT, EFFECTIVE_DATE` |
| `EXOGENOUS.HEALTH.OUTBREAK` | `PATHOGEN` | `LOCATION, GEOGRAPHY, QUANTITY, EFFECTIVE_DATE` |

### INDUSTRY (5)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `INDUSTRY.DEMAND.DEMAND_CHANGE` | `PRODUCT` | `GEOGRAPHY, REPORTING_PERIOD, OLD_VALUE, NEW_VALUE` |
| `INDUSTRY.PRICE.COMMODITY_PRICE_CHANGE` | `COMMODITY, PERIOD` | `CHANGE_VALUE, DRIVER_HINT, GEOGRAPHY` |
| `INDUSTRY.SUPPLY.CAPACITY_CHANGE` | `INDUSTRY, COMMODITY, GEOGRAPHY` | `CAPACITY_VALUE, EFFECTIVE_DATE` |
| `INDUSTRY.SUPPLY.INVENTORY_CHANGE` | `COMMODITY` | `GEOGRAPHY, REPORTING_PERIOD, OLD_VALUE, NEW_VALUE` |
| `INDUSTRY.TECHNOLOGY.STANDARD_CHANGE` | `STANDARD` | `AUTHORITY, INDUSTRY, PRODUCT, EFFECTIVE_DATE` |

### MACRO (6)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `MACRO.CREDIT.LIQUIDITY_ACTION` | `AUTHORITY` | `FACILITY, AMOUNT, INTEREST_RATE, MATURITY_DATE, EFFECTIVE_DATE` |
| `MACRO.EMPLOYMENT.DATA_RELEASE` | `INDICATOR` | `AUTHORITY, GEOGRAPHY, REPORTING_PERIOD, OLD_VALUE, NEW_VALUE, CONSENSUS_VALUE` |
| `MACRO.FX.EXCHANGE_RATE_POLICY` | `AUTHORITY` | `POLICY_RATE, CURRENCY_PAIR, GEOGRAPHY, AMOUNT, EFFECTIVE_DATE` |
| `MACRO.GROWTH.GDP_RELEASE` | `INDICATOR` | `AUTHORITY, GEOGRAPHY, REPORTING_PERIOD, OLD_VALUE, NEW_VALUE, CONSENSUS_VALUE` |
| `MACRO.INFLATION.DATA_RELEASE` | `INDICATOR` | `AUTHORITY, GEOGRAPHY, REPORTING_PERIOD, OLD_VALUE, NEW_VALUE, CONSENSUS_VALUE` |
| `MACRO.MONETARY.POLICY_RATE_DECISION` | `CENTRAL_BANK, EFFECTIVE_DATE` | `OLD_VALUE, NEW_VALUE` |

### MARKET_INFO (3)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `MARKET_INFO.ANALYST.RATING_CHANGE` | `RATED_ENTITY` | `ANALYST_FIRM, OLD_VALUE, NEW_VALUE, PRICE, REPORTING_PERIOD` |
| `MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE` | `ANALYST_FIRM, RATED_ENTITY, REPORT_DATE` | `OLD_VALUE, NEW_VALUE, RATIONALE, ESTIMATE_CHANGE` |
| `MARKET_INFO.CREDIT.RATING_CHANGE` | `RATING_AGENCY, RATED_ENTITY, REPORT_DATE` | `OLD_VALUE, NEW_VALUE, OUTLOOK, DEBT_INSTRUMENT` |

### MARKET_STRUCTURE (4)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `MARKET_STRUCTURE.EXCHANGE_OUTAGE` | `EXCHANGE` | `MARKET, EFFECTIVE_DATE, REASON` |
| `MARKET_STRUCTURE.INDEX.EXCLUSION` | `INDEX, MEMBER` | `EFFECTIVE_DATE, QUANTITY, AMOUNT` |
| `MARKET_STRUCTURE.INDEX.INCLUSION` | `INDEX, MEMBER, EFFECTIVE_DATE` | `ANNOUNCED_DATE` |
| `MARKET_STRUCTURE.TRADING_HALT` | `MEMBER, EXCHANGE` | `REASON, EFFECTIVE_DATE` |

### POLICY (5)
| EVENT_TYPE_ID | identity.required | identity.optional_discriminators |
|---|---|---|
| `POLICY.COURT.RULING` | `COURT, RULE, GEOGRAPHY` | `TARGET, LEGAL_ISSUE, EFFECTIVE_DATE` |
| `POLICY.REGULATION.RULE_CHANGE` | `RULE` | `AUTHORITY, INDUSTRY, GEOGRAPHY, EFFECTIVE_DATE` |
| `POLICY.SANCTION.IMPOSITION` | `AUTHORITY, TARGET` | `PRODUCT_OR_SCOPE, GEOGRAPHY, EFFECTIVE_DATE` |
| `POLICY.TRADE.EXPORT_CONTROL` | `AUTHORITY, TARGET, PRODUCT_OR_SCOPE, GEOGRAPHY` | `EFFECTIVE_DATE, EXEMPTION` |
| `POLICY.TRADE.TARIFF_CHANGE` | `AUTHORITY, TARGET, PRODUCT_OR_SCOPE, GEOGRAPHY` | `RATE, EFFECTIVE_DATE` |

## 대안

| 대안 | 판단 |
|---|---|
| type 규칙을 [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 같은 구현 설명 문서에 계속 묶어두기 | 알고리즘/출력 예시와 타입 사전이 같이 커져 유지보수가 어려움. catalog 분리 채택 |
| type별 규칙을 각 도메인 spec으로 분산 유지 | 같은 `identity_roles` 변경이 여러 문서 불일치를 만들 수 있음. 단일 catalog 유지 |
| `dedup_cluster_id`를 사건 thread key 대용으로 사용 | 텍스트 중복과 사건 계보를 섞어 lifecycle·correction 해석을 망가뜨리므로 기각 |

## 위험과 실패 처리

- **upstream role 누락**: required role이 채워지지 않으면 lineage를 보수적으로 확정하지 못한다. 기본 정책은 `EMIT_UNKNOWN_LINK_ONLY`다.
- **optional discriminator 과적용**: optional 값을 사실상 required처럼 해석하면 동일 사건의 후속/정정이 과도하게 쪼개진다. 반대로 optional을 무시하면 서로 다른 사건이 합쳐질 수 있다. catalog 변경은 contract version 변경으로 다뤄야 한다.
- **dedup / thread 축 혼동**: `dedup_cluster_id`를 lineage 식별자로 재사용하면 rebroadcast와 lifecycle follow-up을 구분하지 못한다.
- **문서-구현 이탈**: 사람용 catalog와 YAML source가 어긋나면 consumer가 잘못된 identity를 가정한다. source-of-truth는 YAML이며, 이 문서는 그 읽기 쉬운 투영본이다.

## Open questions

1. `thread_scope`는 v0.1 계약에서 제거했다(전량 `event_type_id`와 동일한 중복 필드였음). 딜 재분류(`STAKE_ACQUISITION`→`ACQUISITION` 등)처럼 `event_type_id`가 바뀌어도 하나의 계보로 유지해야 하는 사례가 실제로 생기면, type과 분리된 lineage 축을 재도입할지 결정한다.
2. physical warehouse 계약을 추가할 때 `thread_discovery_snapshot`의 PIT/as-of 재현 규칙을 어디까지 별도 contract로 승격할지.
3. 현재 53개 type 외 새 ontology leaf type이 추가될 때 catalog 변경을 release gate로 강제할지.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| canonical thread contract | `src/alphamale/events/ontology/resources/news_thread_contract_v0_1.yaml` | `meta.version=0.1.0`, `type_count=53`, invariants, logical table key, type별 identity catalog |
| source profile lineage | `src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json` | `identity.required == identity_roles` 규칙의 source |
| implementation-oriented companion spec | `docs/engineering/design/analysis-engine.md` | JSONL producer lineage, 예시, 구현 컨텍스트 |
| architecture owner | `docs/engineering/current-architecture.md` | Analysis/Explanation 경계와 컨테이너 흐름 |
| algorithm owner | `docs/engineering/design/analysis-engine.md` | thread_key/thread_id/status 결정 알고리즘 소유 문서 |
