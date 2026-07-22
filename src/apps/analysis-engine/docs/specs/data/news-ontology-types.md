---
doc_type: design
status: Draft
owner: engineering
created: 2026-07-08
updated: 2026-07-10
related:
  - ../../baseline/analysis-engine-design.md
  - entity-master.md
  - ../../baseline/analysis-engine-design.md
  - ../../baseline/analysis-engine-design.md
---
# 뉴스 온톨로지 타입 카탈로그

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

## Summary

`src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json`의 `dataset_version=0.1.0`를 **canonical source**로 삼아 뉴스 이벤트 ontology의 **7개 family / 53개 leaf event type** 카탈로그를 고정한다.
이 문서는 타입 참조만 소유한다. 각 leaf type의 `family`, `lifecycle_model`, `allowed_predicates`, `required_roles`, `optional_roles`, `identity_roles`, `stage_sensitive`, `projection(target_relation·src_role·dst_role·activation_stages)`를 한곳에 모아두고, 런타임 stage 운영 로직(O0-O6)·accept/review 분기·HQ activation 규칙은 다루지 않는다.

## Context

현재 뉴스 ontology 관련 설명은 파이프라인 설계와 타입 reference가 한 문서에 함께 있어, 운영 흐름을 읽으려는 사람과 타입 surface를 조회하려는 사람이 같은 문서를 왕복해야 한다. 이 문서는 그중 **타입 surface**만 분리해, design owner 문서가 재서술하지 않아도 되는 canonical catalog를 제공한다.

## Problem

타입별 predicate·role·lifecycle·projection 규칙이 여러 설명 문장 안에 흩어져 있으면, 같은 leaf type을 consumer마다 다르게 읽을 위험이 생긴다. 특히 `optional_roles`처럼 surface key는 같아도 해석 의도가 달라질 수 있어, **현재 registry가 실제로 선언한 값**을 우선 고정할 필요가 있다.

## Goals

- canonical JSON resource가 선언한 53개 leaf type의 surface를 빠짐없이 기록한다.
- family / role / predicate / lifecycle_model vocabulary를 한 문서에서 조회 가능하게 만든다.
- design 문서가 type catalog를 복사하지 않고 이 문서를 링크하게 만든다.

## Non-goals

- O0-O6 운영 로직, accept/review threshold, assertion/event/thread materialization 흐름.
- `activate_hq`, feature spec, scoring model처럼 type catalog 바깥의 runtime 소비 규칙.
- entity taxonomy 자체의 정의. 엔티티 의미와 해소 규칙은 [엔티티 마스터](entity-master.md)가 소유한다.

## Current state

| 항목 | current 상태 |
|---|---|
| canonical registry | `event_type_profiles_v0_1.json` / `dataset_version=0.1.0` |
| family 수 | 7 (`COMPANY`, `POLICY`, `INDUSTRY`, `EXOGENOUS`, `MARKET_INFO`, `MARKET_STRUCTURE`, `MACRO`) |
| leaf event type 수 | 53 |
| lifecycle_model vocabulary | 20개 |
| predicate vocabulary | 81개 |
| role vocabulary | 87개 |

## Family taxonomy

| family | leaf type 수 | 현재 포함 type |
|---|---:|---|
| `COMPANY` | 24 | `COMPANY.ALLIANCE.PARTNERSHIP`<br>`COMPANY.CAPITAL.DIVIDEND_DECISION`<br>`COMPANY.CAPITAL.EQUITY_ISSUANCE`<br>`COMPANY.CAPITAL.IPO`<br>`COMPANY.CAPITAL.SHARE_BUYBACK`<br>`COMPANY.CAPITAL.STOCK_SPLIT`<br>`COMPANY.COMMERCIAL.MARKET_ENTRY`<br>`COMPANY.COMMERCIAL.PRICING_ACTION`<br>`COMPANY.CONTRACT.SIGNING`<br>`COMPANY.EARNINGS.GUIDANCE_CHANGE`<br>`COMPANY.EARNINGS.RESULT_RELEASE`<br>`COMPANY.FINANCING.DEBT_ISSUANCE`<br>`COMPANY.INVESTMENT.STAKE_ACQUISITION`<br>`COMPANY.LEGAL.LAWSUIT`<br>`COMPANY.LEGAL.REGULATORY_ACTION`<br>`COMPANY.MANAGEMENT.EXECUTIVE_CHANGE`<br>`COMPANY.M_AND_A.ACQUISITION`<br>`COMPANY.M_AND_A.MERGER`<br>`COMPANY.OWNERSHIP.INSIDER_TRANSACTION`<br>`COMPANY.PRODUCT.CERTIFICATION`<br>`COMPANY.PRODUCT.LAUNCH`<br>`COMPANY.PRODUCTION.CAPACITY_CHANGE`<br>`COMPANY.RESTRUCTURING.SPINOFF`<br>`COMPANY.WORKFORCE.LAYOFF` |
| `POLICY` | 5 | `POLICY.COURT.RULING`<br>`POLICY.REGULATION.RULE_CHANGE`<br>`POLICY.SANCTION.IMPOSITION`<br>`POLICY.TRADE.EXPORT_CONTROL`<br>`POLICY.TRADE.TARIFF_CHANGE` |
| `INDUSTRY` | 5 | `INDUSTRY.DEMAND.DEMAND_CHANGE`<br>`INDUSTRY.PRICE.COMMODITY_PRICE_CHANGE`<br>`INDUSTRY.SUPPLY.CAPACITY_CHANGE`<br>`INDUSTRY.SUPPLY.INVENTORY_CHANGE`<br>`INDUSTRY.TECHNOLOGY.STANDARD_CHANGE` |
| `EXOGENOUS` | 6 | `EXOGENOUS.ACCIDENT.OPERATIONAL_DISRUPTION`<br>`EXOGENOUS.CONFLICT.OUTBREAK`<br>`EXOGENOUS.CONFLICT.RESOLUTION`<br>`EXOGENOUS.CYBER.SERVICE_DISRUPTION`<br>`EXOGENOUS.DISASTER.OCCURRENCE`<br>`EXOGENOUS.HEALTH.OUTBREAK` |
| `MARKET_INFO` | 3 | `MARKET_INFO.ANALYST.RATING_CHANGE`<br>`MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE`<br>`MARKET_INFO.CREDIT.RATING_CHANGE` |
| `MARKET_STRUCTURE` | 4 | `MARKET_STRUCTURE.EXCHANGE_OUTAGE`<br>`MARKET_STRUCTURE.INDEX.EXCLUSION`<br>`MARKET_STRUCTURE.INDEX.INCLUSION`<br>`MARKET_STRUCTURE.TRADING_HALT` |
| `MACRO` | 6 | `MACRO.CREDIT.LIQUIDITY_ACTION`<br>`MACRO.EMPLOYMENT.DATA_RELEASE`<br>`MACRO.FX.EXCHANGE_RATE_POLICY`<br>`MACRO.GROWTH.GDP_RELEASE`<br>`MACRO.INFLATION.DATA_RELEASE`<br>`MACRO.MONETARY.POLICY_RATE_DECISION` |

## Vocabulary

### lifecycle_model 목록

| lifecycle_model | 사용 leaf type 수 |
|---|---:|
| `CAPITAL_ACTION_LIFECYCLE` | 4 |
| `CAPITAL_ACTION_NO_LIFECYCLE` | 1 |
| `COMMERCIAL_LIFECYCLE` | 2 |
| `CONFLICT_RESOLUTION_LIFECYCLE` | 1 |
| `DATA_RELEASE_NO_LIFECYCLE` | 8 |
| `DEAL_LIFECYCLE` | 6 |
| `EXOGENOUS_SHOCK_NO_LIFECYCLE` | 3 |
| `GOVERNANCE_LIFECYCLE` | 1 |
| `INDEX_REBALANCE_LIFECYCLE` | 1 |
| `LEGAL_REGULATORY_LIFECYCLE` | 3 |
| `MARKET_OPERATIONS_LIFECYCLE` | 1 |
| `MARKET_STRUCTURE_LIFECYCLE` | 2 |
| `OFFERING_LIFECYCLE` | 1 |
| `OPERATIONS_LIFECYCLE` | 4 |
| `OWNERSHIP_DISCLOSURE_NO_LIFECYCLE` | 1 |
| `POLICY_ACTION_LIFECYCLE` | 3 |
| `POLICY_LIFECYCLE` | 4 |
| `PRODUCT_TECH_LIFECYCLE` | 2 |
| `REVISION_NO_LIFECYCLE` | 4 |
| `WORKFORCE_LIFECYCLE` | 1 |

### role 어휘

`required_roles`, `optional_roles`, `identity_roles`에 등장하는 role surface를 registry 그대로 적는다. 아래 횟수는 **몇 개 leaf type에서 등장하는가**를 뜻한다.

| role | 등장 leaf type 수 |
|---|---:|
| `ACQUIRER` | 3 |
| `ACTUAL_VALUE` | 1 |
| `AMOUNT` | 12 |
| `ANALYST_FIRM` | 3 |
| `ANNOUNCED_DATE` | 2 |
| `AUTHORITY` | 21 |
| `BUYBACK_VALUE` | 1 |
| `CAPACITY_SHARE` | 1 |
| `CAPACITY_VALUE` | 1 |
| `CENTRAL_BANK` | 2 |
| `CHANGE_VALUE` | 1 |
| `COMMODITY` | 7 |
| `CONSENSUS_VALUE` | 4 |
| `CONTRACT_DURATION` | 1 |
| `CONTRACT_OBJECT` | 2 |
| `CONTRACT_VALUE` | 1 |
| `COURT` | 5 |
| `CURRENCY_PAIR` | 1 |
| `CUSTOMER` | 4 |
| `DEAL_VALUE` | 3 |
| `DEBT_INSTRUMENT` | 2 |
| `DEFENDANT` | 2 |
| `DRIVER_HINT` | 1 |
| `DURATION` | 1 |
| `EFFECTIVE_DATE` | 40 |
| `ESTIMATE_CHANGE` | 1 |
| `EXCHANGE` | 5 |
| `EXEMPTION` | 1 |
| `FACILITY` | 4 |
| `GEOGRAPHY` | 23 |
| `GUIDANCE_RANGE` | 1 |
| `HAZARD` | 2 |
| `INDEX` | 4 |
| `INDICATOR` | 6 |
| `INDUSTRY` | 4 |
| `INTEREST_RATE` | 2 |
| `INVESTOR` | 3 |
| `ISSUER` | 32 |
| `LEGAL_ISSUE` | 5 |
| `LOCATION` | 10 |
| `MARKET` | 1 |
| `MATURITY_DATE` | 2 |
| `MEMBER` | 6 |
| `MERGING_ENTITY` | 2 |
| `METRIC` | 3 |
| `NEW_VALUE` | 13 |
| `OLD_VALUE` | 13 |
| `OPERATOR` | 4 |
| `OUTLOOK` | 1 |
| `OWNERSHIP_RATIO` | 3 |
| `PARENT` | 2 |
| `PARTNER` | 2 |
| `PARTNER_2` | 2 |
| `PATHOGEN` | 2 |
| `PAYMENT_DATE` | 1 |
| `PENALTY_VALUE` | 2 |
| `PERIOD` | 2 |
| `PERSON` | 3 |
| `PLAINTIFF` | 2 |
| `POLICY_RATE` | 2 |
| `POSITION` | 1 |
| `PRICE` | 6 |
| `PRODUCT` | 13 |
| `PRODUCT_FAMILY` | 1 |
| `PRODUCT_OR_SCOPE` | 5 |
| `PROJECT` | 2 |
| `QUANTITY` | 9 |
| `RATE` | 1 |
| `RATED_ENTITY` | 6 |
| `RATING_AGENCY` | 3 |
| `RATIONALE` | 1 |
| `REASON` | 4 |
| `RECORD_DATE` | 2 |
| `REPORTING_PERIOD` | 10 |
| `REPORT_DATE` | 2 |
| `RULE` | 4 |
| `SELLER` | 3 |
| `SERVICE` | 2 |
| `SHAREHOLDER` | 1 |
| `SHARES` | 1 |
| `SPUNOFF_UNIT` | 2 |
| `STANDARD` | 3 |
| `SUPPLIER` | 2 |
| `TARGET` | 7 |
| `TARGET_COMPANY` | 7 |
| `TECH_NODE` | 1 |
| `USE_OF_PROCEEDS` | 1 |

### predicate 어휘

| predicate | 등장 leaf type 수 |
|---|---:|
| `ACQUIRE` | 2 |
| `ADOPT` | 1 |
| `APPOINT` | 1 |
| `APPROVE` | 1 |
| `ASSIGN` | 1 |
| `BUY` | 1 |
| `CARVE_OUT` | 1 |
| `CERTIFY` | 1 |
| `CHANGE` | 1 |
| `CHARGE` | 1 |
| `CLEAR` | 1 |
| `COLLAPSE` | 1 |
| `CONSOLIDATE` | 1 |
| `CUT` | 1 |
| `DECLARE` | 1 |
| `DISMISS` | 3 |
| `DISSOLVE` | 1 |
| `EASE` | 1 |
| `ENFORCE` | 1 |
| `ENTER` | 1 |
| `ENTER_INTO` | 1 |
| `ESCALATE` | 1 |
| `EXCLUDE` | 1 |
| `EXIT` | 2 |
| `EXPAND` | 8 |
| `FALL` | 3 |
| `FILE` | 2 |
| `FINE` | 1 |
| `FORM` | 1 |
| `HALT` | 1 |
| `IMPOSE` | 3 |
| `INCLUDE` | 1 |
| `INCREASE` | 1 |
| `INITIATE` | 2 |
| `INJECT` | 1 |
| `INTERVENE` | 1 |
| `INTRODUCE` | 1 |
| `INVESTIGATE` | 1 |
| `ISSUE` | 4 |
| `LAUNCH` | 1 |
| `LIFT` | 1 |
| `LIST` | 1 |
| `LOWER` | 8 |
| `MAINTAIN` | 7 |
| `MERGE` | 1 |
| `NOMINATE` | 1 |
| `OCCUR` | 6 |
| `OMIT` | 1 |
| `OVERTURN` | 1 |
| `PRICE` | 3 |
| `PURCHASE` | 1 |
| `RAISE` | 8 |
| `REACH` | 1 |
| `RECORD` | 1 |
| `REDUCE` | 5 |
| `REJECT` | 1 |
| `RELEASE` | 5 |
| `REMOVE` | 1 |
| `REPEAL` | 1 |
| `REPLACE` | 2 |
| `REPORT` | 13 |
| `REPURCHASE` | 1 |
| `RESIGN` | 1 |
| `RESTRUCTURE` | 1 |
| `RESUME` | 5 |
| `REVISE` | 3 |
| `RISE` | 3 |
| `RULE` | 2 |
| `SELL` | 1 |
| `SET` | 3 |
| `SETTLE` | 2 |
| `SIGN` | 2 |
| `SPIN_OFF` | 1 |
| `SPLIT` | 2 |
| `SPREAD` | 1 |
| `STRIKE` | 1 |
| `SUSPEND` | 4 |
| `TAKE_OVER` | 1 |
| `UNVEIL` | 1 |
| `UPHOLD` | 1 |
| `WITHDRAW` | 4 |

## Leaf event type catalog

아래 catalog는 resource JSON surface를 그대로 기록한다. `optional_roles`는 runtime 해석을 덧씌우지 않고 **registry의 현재 필드명과 값**을 그대로 남긴다.

### `COMPANY`

#### `COMPANY.ALLIANCE.PARTNERSHIP`

- `family`: `COMPANY`
- `lifecycle_model`: `DEAL_LIFECYCLE`
- `allowed_predicates`: `FORM`, `EXPAND`, `DISSOLVE`
- `required_roles`: `PARTNER`
- `optional_roles`: `PARTNER_2`, `PROJECT`, `PRODUCT`, `EFFECTIVE_DATE`
- `identity_roles`: `PARTNER`, `PARTNER_2`, `PROJECT`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.CAPITAL.DIVIDEND_DECISION`

- `family`: `COMPANY`
- `lifecycle_model`: `CAPITAL_ACTION_NO_LIFECYCLE`
- `allowed_predicates`: `DECLARE`, `RAISE`, `LOWER`, `MAINTAIN`, `OMIT`
- `required_roles`: `ISSUER`
- `optional_roles`: `AMOUNT`, `OLD_VALUE`, `NEW_VALUE`, `RECORD_DATE`, `PAYMENT_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.CAPITAL.EQUITY_ISSUANCE`

- `family`: `COMPANY`
- `lifecycle_model`: `CAPITAL_ACTION_LIFECYCLE`
- `allowed_predicates`: `ISSUE`, `PRICE`
- `required_roles`: `ISSUER`
- `optional_roles`: `AMOUNT`, `PRICE`, `QUANTITY`, `INVESTOR`, `USE_OF_PROCEEDS`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.CAPITAL.IPO`

- `family`: `COMPANY`
- `lifecycle_model`: `CAPITAL_ACTION_LIFECYCLE`
- `allowed_predicates`: `FILE`, `PRICE`, `LIST`, `WITHDRAW`
- `required_roles`: `ISSUER`
- `optional_roles`: `EXCHANGE`, `PRICE`, `AMOUNT`, `QUANTITY`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.CAPITAL.SHARE_BUYBACK`

- `family`: `COMPANY`
- `lifecycle_model`: `OFFERING_LIFECYCLE`
- `allowed_predicates`: `REPURCHASE`, `EXPAND`
- `required_roles`: `ISSUER`
- `optional_roles`: `BUYBACK_VALUE`, `SHARES`, `EFFECTIVE_DATE`, `DURATION`
- `identity_roles`: `ISSUER`, `ANNOUNCED_DATE`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.CAPITAL.STOCK_SPLIT`

- `family`: `COMPANY`
- `lifecycle_model`: `CAPITAL_ACTION_LIFECYCLE`
- `allowed_predicates`: `SPLIT`, `CONSOLIDATE`
- `required_roles`: `ISSUER`
- `optional_roles`: `OLD_VALUE`, `NEW_VALUE`, `RECORD_DATE`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.COMMERCIAL.MARKET_ENTRY`

- `family`: `COMPANY`
- `lifecycle_model`: `COMMERCIAL_LIFECYCLE`
- `allowed_predicates`: `ENTER`, `EXPAND`, `EXIT`
- `required_roles`: `ISSUER`
- `optional_roles`: `GEOGRAPHY`, `PRODUCT`, `AMOUNT`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.COMMERCIAL.PRICING_ACTION`

- `family`: `COMPANY`
- `lifecycle_model`: `COMMERCIAL_LIFECYCLE`
- `allowed_predicates`: `RAISE`, `LOWER`, `SET`
- `required_roles`: `ISSUER`
- `optional_roles`: `PRODUCT`, `PRICE`, `OLD_VALUE`, `NEW_VALUE`, `GEOGRAPHY`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.CONTRACT.SIGNING`

- `family`: `COMPANY`
- `lifecycle_model`: `DEAL_LIFECYCLE`
- `allowed_predicates`: `SIGN`, `ENTER_INTO`
- `required_roles`: `SUPPLIER`, `CONTRACT_OBJECT`
- `optional_roles`: `CUSTOMER`, `CONTRACT_VALUE`, `CONTRACT_DURATION`, `EFFECTIVE_DATE`
- `identity_roles`: `SUPPLIER`, `CUSTOMER`, `CONTRACT_OBJECT`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `supplies`
  - `src_role`: `SUPPLIER`
  - `dst_role`: `CUSTOMER`
  - `activation_stages`: `SIGNED`, `EFFECTIVE`

#### `COMPANY.EARNINGS.GUIDANCE_CHANGE`

- `family`: `COMPANY`
- `lifecycle_model`: `REVISION_NO_LIFECYCLE`
- `allowed_predicates`: `ISSUE`, `REVISE`, `RAISE`, `LOWER`, `MAINTAIN`, `WITHDRAW`
- `required_roles`: `ISSUER`, `METRIC`
- `optional_roles`: `REPORTING_PERIOD`, `OLD_VALUE`, `NEW_VALUE`, `GUIDANCE_RANGE`
- `identity_roles`: `ISSUER`, `METRIC`, `REPORTING_PERIOD`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.EARNINGS.RESULT_RELEASE`

- `family`: `COMPANY`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `REPORT`, `RECORD`, `RELEASE`
- `required_roles`: `ISSUER`, `REPORTING_PERIOD`
- `optional_roles`: `METRIC`, `ACTUAL_VALUE`, `CONSENSUS_VALUE`
- `identity_roles`: `ISSUER`, `REPORTING_PERIOD`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.FINANCING.DEBT_ISSUANCE`

- `family`: `COMPANY`
- `lifecycle_model`: `CAPITAL_ACTION_LIFECYCLE`
- `allowed_predicates`: `ISSUE`, `PRICE`
- `required_roles`: `ISSUER`
- `optional_roles`: `AMOUNT`, `PRICE`, `INTEREST_RATE`, `MATURITY_DATE`, `RATING_AGENCY`, `DEBT_INSTRUMENT`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.INVESTMENT.STAKE_ACQUISITION`

- `family`: `COMPANY`
- `lifecycle_model`: `DEAL_LIFECYCLE`
- `allowed_predicates`: `ACQUIRE`, `INCREASE`, `REDUCE`, `EXIT`
- `required_roles`: `INVESTOR`, `TARGET_COMPANY`
- `optional_roles`: `OWNERSHIP_RATIO`, `DEAL_VALUE`, `SELLER`
- `identity_roles`: `INVESTOR`, `TARGET_COMPANY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `has_stake`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `SETTLED`, `DISCLOSED`, `CLOSED`

#### `COMPANY.LEGAL.LAWSUIT`

- `family`: `COMPANY`
- `lifecycle_model`: `LEGAL_REGULATORY_LIFECYCLE`
- `allowed_predicates`: `FILE`, `SETTLE`, `DISMISS`, `RULE`
- `required_roles`: `DEFENDANT`
- `optional_roles`: `PLAINTIFF`, `COURT`, `LEGAL_ISSUE`, `PENALTY_VALUE`
- `identity_roles`: `DEFENDANT`, `PLAINTIFF`, `LEGAL_ISSUE`, `COURT`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.LEGAL.REGULATORY_ACTION`

- `family`: `COMPANY`
- `lifecycle_model`: `LEGAL_REGULATORY_LIFECYCLE`
- `allowed_predicates`: `INVESTIGATE`, `CHARGE`, `FINE`, `SETTLE`, `CLEAR`
- `required_roles`: `AUTHORITY`, `TARGET_COMPANY`
- `optional_roles`: `LEGAL_ISSUE`, `PENALTY_VALUE`, `COURT`, `EFFECTIVE_DATE`
- `identity_roles`: `AUTHORITY`, `TARGET_COMPANY`, `LEGAL_ISSUE`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.MANAGEMENT.EXECUTIVE_CHANGE`

- `family`: `COMPANY`
- `lifecycle_model`: `GOVERNANCE_LIFECYCLE`
- `allowed_predicates`: `APPOINT`, `RESIGN`, `DISMISS`, `REPLACE`, `NOMINATE`
- `required_roles`: `ISSUER`
- `optional_roles`: `PERSON`, `POSITION`, `EFFECTIVE_DATE`, `REASON`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.M_AND_A.ACQUISITION`

- `family`: `COMPANY`
- `lifecycle_model`: `DEAL_LIFECYCLE`
- `allowed_predicates`: `ACQUIRE`, `PURCHASE`, `TAKE_OVER`
- `required_roles`: `ACQUIRER`, `TARGET_COMPANY`
- `optional_roles`: `SELLER`, `DEAL_VALUE`, `OWNERSHIP_RATIO`, `AUTHORITY`, `EFFECTIVE_DATE`
- `identity_roles`: `ACQUIRER`, `TARGET_COMPANY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `owns`
  - `src_role`: `ACQUIRER`
  - `dst_role`: `TARGET_COMPANY`
  - `activation_stages`: `CLOSED`

#### `COMPANY.M_AND_A.MERGER`

- `family`: `COMPANY`
- `lifecycle_model`: `DEAL_LIFECYCLE`
- `allowed_predicates`: `MERGE`
- `required_roles`: `MERGING_ENTITY`
- `optional_roles`: `TARGET_COMPANY`, `ACQUIRER`, `SELLER`, `DEAL_VALUE`, `OWNERSHIP_RATIO`, `EFFECTIVE_DATE`
- `identity_roles`: `MERGING_ENTITY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.OWNERSHIP.INSIDER_TRANSACTION`

- `family`: `COMPANY`
- `lifecycle_model`: `OWNERSHIP_DISCLOSURE_NO_LIFECYCLE`
- `allowed_predicates`: `BUY`, `SELL`
- `required_roles`: `PERSON`, `ISSUER`
- `optional_roles`: `QUANTITY`, `PRICE`, `AMOUNT`, `EFFECTIVE_DATE`
- `identity_roles`: `PERSON`, `ISSUER`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.PRODUCT.CERTIFICATION`

- `family`: `COMPANY`
- `lifecycle_model`: `PRODUCT_TECH_LIFECYCLE`
- `allowed_predicates`: `CERTIFY`, `APPROVE`, `REJECT`
- `required_roles`: `ISSUER`, `AUTHORITY`
- `optional_roles`: `PRODUCT`, `STANDARD`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`, `PRODUCT`, `AUTHORITY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `certified_for`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `APPROVED`, `CERTIFIED`

#### `COMPANY.PRODUCT.LAUNCH`

- `family`: `COMPANY`
- `lifecycle_model`: `PRODUCT_TECH_LIFECYCLE`
- `allowed_predicates`: `LAUNCH`, `RELEASE`, `UNVEIL`, `INTRODUCE`
- `required_roles`: `ISSUER`, `PRODUCT`
- `optional_roles`: `PRODUCT_FAMILY`, `CUSTOMER`, `TECH_NODE`, `EFFECTIVE_DATE`, `QUANTITY`
- `identity_roles`: `ISSUER`, `PRODUCT`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `produces`
  - `src_role`: `ISSUER`
  - `dst_role`: `PRODUCT`
  - `activation_stages`: `LAUNCHED`, `COMMERCIAL_SUPPLY`, `REVENUE_RECOGNIZED`

#### `COMPANY.PRODUCTION.CAPACITY_CHANGE`

- `family`: `COMPANY`
- `lifecycle_model`: `OPERATIONS_LIFECYCLE`
- `allowed_predicates`: `EXPAND`, `REDUCE`, `SUSPEND`, `RESUME`
- `required_roles`: `ISSUER`
- `optional_roles`: `FACILITY`, `PRODUCT`, `QUANTITY`, `AMOUNT`, `LOCATION`, `EFFECTIVE_DATE`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.RESTRUCTURING.SPINOFF`

- `family`: `COMPANY`
- `lifecycle_model`: `DEAL_LIFECYCLE`
- `allowed_predicates`: `SPIN_OFF`, `SPLIT`, `CARVE_OUT`
- `required_roles`: `PARENT`, `SPUNOFF_UNIT`
- `optional_roles`: `SHAREHOLDER`, `EFFECTIVE_DATE`, `AMOUNT`
- `identity_roles`: `PARENT`, `SPUNOFF_UNIT`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `COMPANY.WORKFORCE.LAYOFF`

- `family`: `COMPANY`
- `lifecycle_model`: `WORKFORCE_LIFECYCLE`
- `allowed_predicates`: `CUT`, `REDUCE`, `RESTRUCTURE`
- `required_roles`: `ISSUER`
- `optional_roles`: `QUANTITY`, `LOCATION`, `EFFECTIVE_DATE`, `REASON`
- `identity_roles`: `ISSUER`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

### `POLICY`

#### `POLICY.COURT.RULING`

- `family`: `POLICY`
- `lifecycle_model`: `LEGAL_REGULATORY_LIFECYCLE`
- `allowed_predicates`: `RULE`, `UPHOLD`, `OVERTURN`, `DISMISS`
- `required_roles`: `COURT`
- `optional_roles`: `RULE`, `TARGET`, `GEOGRAPHY`, `LEGAL_ISSUE`, `EFFECTIVE_DATE`
- `identity_roles`: `COURT`, `RULE`, `GEOGRAPHY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `regulates_or_rule_status`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `RULED`, `UPHELD`, `OVERTURNED`, `DISMISSED`

#### `POLICY.REGULATION.RULE_CHANGE`

- `family`: `POLICY`
- `lifecycle_model`: `POLICY_LIFECYCLE`
- `allowed_predicates`: `ISSUE`, `REVISE`, `REPEAL`, `ENFORCE`
- `required_roles`: `RULE`
- `optional_roles`: `AUTHORITY`, `INDUSTRY`, `GEOGRAPHY`, `EFFECTIVE_DATE`
- `identity_roles`: `RULE`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `POLICY.SANCTION.IMPOSITION`

- `family`: `POLICY`
- `lifecycle_model`: `POLICY_LIFECYCLE`
- `allowed_predicates`: `IMPOSE`, `LIFT`, `EXPAND`
- `required_roles`: `AUTHORITY`, `TARGET`
- `optional_roles`: `PRODUCT_OR_SCOPE`, `GEOGRAPHY`, `EFFECTIVE_DATE`
- `identity_roles`: `AUTHORITY`, `TARGET`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `sanctions`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `IMPOSED`, `EFFECTIVE`, `LIFTED`

#### `POLICY.TRADE.EXPORT_CONTROL`

- `family`: `POLICY`
- `lifecycle_model`: `POLICY_LIFECYCLE`
- `allowed_predicates`: `IMPOSE`, `EASE`, `EXPAND`
- `required_roles`: `AUTHORITY`
- `optional_roles`: `TARGET`, `PRODUCT_OR_SCOPE`, `GEOGRAPHY`, `EFFECTIVE_DATE`, `EXEMPTION`
- `identity_roles`: `AUTHORITY`, `TARGET`, `PRODUCT_OR_SCOPE`, `GEOGRAPHY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `restricts`
  - `src_role`: `AUTHORITY`
  - `dst_role`: `PRODUCT_OR_SCOPE`
  - `activation_stages`: `ENFORCED`, `EFFECTIVE`

#### `POLICY.TRADE.TARIFF_CHANGE`

- `family`: `POLICY`
- `lifecycle_model`: `POLICY_LIFECYCLE`
- `allowed_predicates`: `RAISE`, `LOWER`, `IMPOSE`, `REMOVE`
- `required_roles`: `AUTHORITY`
- `optional_roles`: `TARGET`, `PRODUCT_OR_SCOPE`, `GEOGRAPHY`, `RATE`, `EFFECTIVE_DATE`
- `identity_roles`: `AUTHORITY`, `TARGET`, `PRODUCT_OR_SCOPE`, `GEOGRAPHY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `tariff_applies_to`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `EFFECTIVE`, `ENFORCED`

### `INDUSTRY`

#### `INDUSTRY.DEMAND.DEMAND_CHANGE`

- `family`: `INDUSTRY`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `RISE`, `FALL`, `REPORT`
- `required_roles`: `PRODUCT`
- `optional_roles`: `GEOGRAPHY`, `REPORTING_PERIOD`, `OLD_VALUE`, `NEW_VALUE`
- `identity_roles`: `PRODUCT`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `INDUSTRY.PRICE.COMMODITY_PRICE_CHANGE`

- `family`: `INDUSTRY`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `RISE`, `FALL`, `CHANGE`
- `required_roles`: `COMMODITY`
- `optional_roles`: `CHANGE_VALUE`, `PERIOD`, `DRIVER_HINT`, `GEOGRAPHY`
- `identity_roles`: `COMMODITY`, `PERIOD`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `INDUSTRY.SUPPLY.CAPACITY_CHANGE`

- `family`: `INDUSTRY`
- `lifecycle_model`: `OPERATIONS_LIFECYCLE`
- `allowed_predicates`: `EXPAND`, `REDUCE`, `REPORT`
- `required_roles`: `INDUSTRY`
- `optional_roles`: `COMMODITY`, `CAPACITY_VALUE`, `GEOGRAPHY`, `EFFECTIVE_DATE`
- `identity_roles`: `INDUSTRY`, `COMMODITY`, `GEOGRAPHY`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `INDUSTRY.SUPPLY.INVENTORY_CHANGE`

- `family`: `INDUSTRY`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `RISE`, `FALL`, `REPORT`
- `required_roles`: `COMMODITY`
- `optional_roles`: `GEOGRAPHY`, `REPORTING_PERIOD`, `OLD_VALUE`, `NEW_VALUE`
- `identity_roles`: `COMMODITY`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `INDUSTRY.TECHNOLOGY.STANDARD_CHANGE`

- `family`: `INDUSTRY`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `ADOPT`, `REVISE`, `REPLACE`
- `required_roles`: `STANDARD`
- `optional_roles`: `AUTHORITY`, `INDUSTRY`, `PRODUCT`, `EFFECTIVE_DATE`
- `identity_roles`: `STANDARD`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

### `EXOGENOUS`

#### `EXOGENOUS.ACCIDENT.OPERATIONAL_DISRUPTION`

- `family`: `EXOGENOUS`
- `lifecycle_model`: `OPERATIONS_LIFECYCLE`
- `allowed_predicates`: `OCCUR`, `SUSPEND`, `RESUME`, `REPORT`
- `required_roles`: `OPERATOR`
- `optional_roles`: `FACILITY`, `PRODUCT`, `LOCATION`, `CAPACITY_SHARE`, `EFFECTIVE_DATE`
- `identity_roles`: `OPERATOR`, `FACILITY`, `PRODUCT`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `operation_status`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `SUSPENDED`, `RESUMED`, `NORMALIZED`

#### `EXOGENOUS.CONFLICT.OUTBREAK`

- `family`: `EXOGENOUS`
- `lifecycle_model`: `EXOGENOUS_SHOCK_NO_LIFECYCLE`
- `allowed_predicates`: `OCCUR`, `ESCALATE`, `REPORT`
- `required_roles`: `LOCATION`
- `optional_roles`: `GEOGRAPHY`, `COMMODITY`, `EFFECTIVE_DATE`
- `identity_roles`: `LOCATION`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `EXOGENOUS.CONFLICT.RESOLUTION`

- `family`: `EXOGENOUS`
- `lifecycle_model`: `CONFLICT_RESOLUTION_LIFECYCLE`
- `allowed_predicates`: `REACH`, `SIGN`, `COLLAPSE`
- `required_roles`: `LOCATION`
- `optional_roles`: `GEOGRAPHY`, `AUTHORITY`, `EFFECTIVE_DATE`
- `identity_roles`: `LOCATION`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `EXOGENOUS.CYBER.SERVICE_DISRUPTION`

- `family`: `EXOGENOUS`
- `lifecycle_model`: `OPERATIONS_LIFECYCLE`
- `allowed_predicates`: `OCCUR`, `SUSPEND`, `RESUME`, `REPORT`
- `required_roles`: `OPERATOR`
- `optional_roles`: `SERVICE`, `CUSTOMER`, `LOCATION`, `EFFECTIVE_DATE`
- `identity_roles`: `OPERATOR`, `SERVICE`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `service_status`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `SUSPENDED`, `RESUMED`, `NORMALIZED`

#### `EXOGENOUS.DISASTER.OCCURRENCE`

- `family`: `EXOGENOUS`
- `lifecycle_model`: `EXOGENOUS_SHOCK_NO_LIFECYCLE`
- `allowed_predicates`: `OCCUR`, `STRIKE`, `REPORT`
- `required_roles`: `HAZARD`
- `optional_roles`: `LOCATION`, `GEOGRAPHY`, `QUANTITY`, `AMOUNT`, `EFFECTIVE_DATE`
- `identity_roles`: `HAZARD`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `EXOGENOUS.HEALTH.OUTBREAK`

- `family`: `EXOGENOUS`
- `lifecycle_model`: `EXOGENOUS_SHOCK_NO_LIFECYCLE`
- `allowed_predicates`: `OCCUR`, `SPREAD`, `REPORT`
- `required_roles`: `PATHOGEN`
- `optional_roles`: `LOCATION`, `GEOGRAPHY`, `QUANTITY`, `EFFECTIVE_DATE`
- `identity_roles`: `PATHOGEN`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

### `MARKET_INFO`

#### `MARKET_INFO.ANALYST.RATING_CHANGE`

- `family`: `MARKET_INFO`
- `lifecycle_model`: `REVISION_NO_LIFECYCLE`
- `allowed_predicates`: `RAISE`, `LOWER`, `MAINTAIN`, `INITIATE`
- `required_roles`: `RATED_ENTITY`
- `optional_roles`: `ANALYST_FIRM`, `OLD_VALUE`, `NEW_VALUE`, `PRICE`, `REPORTING_PERIOD`
- `identity_roles`: `RATED_ENTITY`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MARKET_INFO.ANALYST.TARGET_PRICE_CHANGE`

- `family`: `MARKET_INFO`
- `lifecycle_model`: `REVISION_NO_LIFECYCLE`
- `allowed_predicates`: `RAISE`, `LOWER`, `MAINTAIN`, `INITIATE`
- `required_roles`: `RATED_ENTITY`
- `optional_roles`: `ANALYST_FIRM`, `OLD_VALUE`, `NEW_VALUE`, `RATIONALE`, `ESTIMATE_CHANGE`
- `identity_roles`: `ANALYST_FIRM`, `RATED_ENTITY`, `REPORT_DATE`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MARKET_INFO.CREDIT.RATING_CHANGE`

- `family`: `MARKET_INFO`
- `lifecycle_model`: `REVISION_NO_LIFECYCLE`
- `allowed_predicates`: `RAISE`, `LOWER`, `MAINTAIN`, `ASSIGN`, `WITHDRAW`
- `required_roles`: `RATED_ENTITY`
- `optional_roles`: `RATING_AGENCY`, `OLD_VALUE`, `NEW_VALUE`, `OUTLOOK`, `DEBT_INSTRUMENT`
- `identity_roles`: `RATING_AGENCY`, `RATED_ENTITY`, `REPORT_DATE`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

### `MARKET_STRUCTURE`

#### `MARKET_STRUCTURE.EXCHANGE_OUTAGE`

- `family`: `MARKET_STRUCTURE`
- `lifecycle_model`: `MARKET_OPERATIONS_LIFECYCLE`
- `allowed_predicates`: `OCCUR`, `SUSPEND`, `RESUME`, `REPORT`
- `required_roles`: `EXCHANGE`
- `optional_roles`: `MARKET`, `EFFECTIVE_DATE`, `REASON`
- `identity_roles`: `EXCHANGE`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MARKET_STRUCTURE.INDEX.EXCLUSION`

- `family`: `MARKET_STRUCTURE`
- `lifecycle_model`: `INDEX_REBALANCE_LIFECYCLE`
- `allowed_predicates`: `EXCLUDE`
- `required_roles`: `INDEX`, `MEMBER`
- `optional_roles`: `EFFECTIVE_DATE`, `QUANTITY`, `AMOUNT`
- `identity_roles`: `INDEX`, `MEMBER`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MARKET_STRUCTURE.INDEX.INCLUSION`

- `family`: `MARKET_STRUCTURE`
- `lifecycle_model`: `MARKET_STRUCTURE_LIFECYCLE`
- `allowed_predicates`: `INCLUDE`
- `required_roles`: `INDEX`, `MEMBER`
- `optional_roles`: `EFFECTIVE_DATE`, `ANNOUNCED_DATE`
- `identity_roles`: `INDEX`, `MEMBER`, `EFFECTIVE_DATE`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `member_of`
  - `src_role`: `MEMBER`
  - `dst_role`: `INDEX`
  - `activation_stages`: `EFFECTIVE`

#### `MARKET_STRUCTURE.TRADING_HALT`

- `family`: `MARKET_STRUCTURE`
- `lifecycle_model`: `MARKET_STRUCTURE_LIFECYCLE`
- `allowed_predicates`: `HALT`, `RESUME`
- `required_roles`: `MEMBER`
- `optional_roles`: `EXCHANGE`, `REASON`, `EFFECTIVE_DATE`
- `identity_roles`: `MEMBER`, `EXCHANGE`
- `stage_sensitive`: `true`
- `projection`:
  - `target_relation`: `trading_status`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: `HALTED`, `RESUMED`

### `MACRO`

#### `MACRO.CREDIT.LIQUIDITY_ACTION`

- `family`: `MACRO`
- `lifecycle_model`: `POLICY_ACTION_LIFECYCLE`
- `allowed_predicates`: `INJECT`, `WITHDRAW`, `EXPAND`, `REDUCE`
- `required_roles`: `AUTHORITY`
- `optional_roles`: `FACILITY`, `AMOUNT`, `INTEREST_RATE`, `MATURITY_DATE`, `EFFECTIVE_DATE`
- `identity_roles`: `AUTHORITY`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MACRO.EMPLOYMENT.DATA_RELEASE`

- `family`: `MACRO`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `REPORT`, `RELEASE`
- `required_roles`: `INDICATOR`
- `optional_roles`: `AUTHORITY`, `GEOGRAPHY`, `REPORTING_PERIOD`, `OLD_VALUE`, `NEW_VALUE`, `CONSENSUS_VALUE`
- `identity_roles`: `INDICATOR`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MACRO.FX.EXCHANGE_RATE_POLICY`

- `family`: `MACRO`
- `lifecycle_model`: `POLICY_ACTION_LIFECYCLE`
- `allowed_predicates`: `INTERVENE`, `SET`, `MAINTAIN`
- `required_roles`: `AUTHORITY`
- `optional_roles`: `POLICY_RATE`, `CURRENCY_PAIR`, `GEOGRAPHY`, `AMOUNT`, `EFFECTIVE_DATE`
- `identity_roles`: `AUTHORITY`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MACRO.GROWTH.GDP_RELEASE`

- `family`: `MACRO`
- `lifecycle_model`: `POLICY_ACTION_LIFECYCLE`
- `allowed_predicates`: `REPORT`, `RELEASE`
- `required_roles`: `INDICATOR`
- `optional_roles`: `AUTHORITY`, `GEOGRAPHY`, `REPORTING_PERIOD`, `OLD_VALUE`, `NEW_VALUE`, `CONSENSUS_VALUE`
- `identity_roles`: `INDICATOR`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MACRO.INFLATION.DATA_RELEASE`

- `family`: `MACRO`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `REPORT`, `RELEASE`
- `required_roles`: `INDICATOR`
- `optional_roles`: `AUTHORITY`, `GEOGRAPHY`, `REPORTING_PERIOD`, `OLD_VALUE`, `NEW_VALUE`, `CONSENSUS_VALUE`
- `identity_roles`: `INDICATOR`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: `event_only`
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

#### `MACRO.MONETARY.POLICY_RATE_DECISION`

- `family`: `MACRO`
- `lifecycle_model`: `DATA_RELEASE_NO_LIFECYCLE`
- `allowed_predicates`: `SET`, `RAISE`, `LOWER`, `MAINTAIN`
- `required_roles`: `CENTRAL_BANK`, `POLICY_RATE`
- `optional_roles`: `OLD_VALUE`, `NEW_VALUE`, `EFFECTIVE_DATE`
- `identity_roles`: `CENTRAL_BANK`, `EFFECTIVE_DATE`
- `stage_sensitive`: `false`
- `projection`:
  - `target_relation`: 없음
  - `src_role`: 없음
  - `dst_role`: 없음
  - `activation_stages`: 없음

## 대안

| 대안 | 판단 |
|---|---|
| [Analysis Engine 디자인](../../baseline/analysis-engine-design.md) 안에 타입 표를 계속 포함 | 운영 로직과 타입 reference가 다시 섞인다. split owner 경계를 흐리므로 채택하지 않음 |
| family별 설명 prose만 남기고 leaf catalog 생략 | 53개 leaf type의 role/predicate/lifecycle 정확성을 직접 확인할 수 없어 acceptance를 만족하지 못함 |

## 위험과 실패 처리

- **resource drift**: 후속 registry 업데이트가 생기면 이 문서는 source JSON과 어긋날 수 있다. 따라서 이 문서의 변경 근거는 항상 `event_type_profiles_v0_1.json` diff여야 한다.
- **surface name 오해석**: `optional_roles`처럼 runtime에서 enrichment로 읽힐 수 있는 필드도 catalog에서는 이름을 바꾸지 않는다. 해석 변경은 design owner 문서에서 다루고, catalog는 source surface를 우선한다.
- **projection null 의미 과잉 해석**: `target_relation`/`src_role`/`dst_role`가 비어 있는 type을 "projection 없음" 이상의 의미로 확대 해석하지 않는다. catalog에서는 단지 현재 registry 값이 비어 있음을 기록한다.

## Open questions

1. `optional_roles`를 장기적으로도 surface key로 유지할지, runtime 의미에 맞춰 별도 key로 승격할지.
2. `projection`의 null surface를 "미정"과 "의도적으로 event_only" 중 어느 쪽으로 구분할지 별도 계약이 필요한지.
3. `activate_hq`처럼 registry 안에 있지만 타입 catalog 비범위로 둔 메타데이터를 별도 reference 문서로 분리할지.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| canonical resource | `src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json` | dataset_version `0.1.0`, 7 family, 53 leaf type, predicate/role/lifecycle/projection surface의 직접 근거 |
| 보조 문서 | `docs/engineering/design/analysis-engine.md` | 타입 catalog와 운영 로직 문서의 owner boundary, `optional_roles` 해석 주석의 보조 근거 |
| 상위 아키텍처 | `docs/engineering/current-architecture.md`; `docs/engineering/c4-diagrams.md` | news ontology가 Analysis Engine 내부 타입 자산임을 보여주는 링크 대상 |
