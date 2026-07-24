# 엔티티 관계 계약 — 관계 트리·별칭·미해소 처리 (v0.1)

엔티티 **타입 위계·프로퍼티 배치·관계 어휘·방향·병합 규칙**과, 엔티티 kind → DB 사상, 미해소(파싱 실패) 엔티티 처리 정책의 SSOT.
물리 저장 구조는 `src/libs/schema/migrations-cloud/V202607241600__add_entity_relation_alias_mention.sql`, 결정 배경은 [ADR-0039](../adr/0039-entity-relation-schema.md). (ALPHA-509)

관계는 **2층**이다: **참조 지식 그래프**(§2 — 현재-상태 지식, 뉴스 해소의 기반)와 **이벤트 파생 관계**(§3 — 유효기간을 갖는 사건 산출물). 어휘 출처: 이벤트 파생 어휘는 `edge_ontology` thread 계약(`news_thread_contract_v0_1.yaml`)의 `relation` 값을 **승계**하고(발명 금지, 리소스 통째 교체 규약 ALPHA-539), 참조 층 어휘는 엔티티 마스터 소관이라 **이 문서가 정본**이다.

## 1. 엔티티 kind → DB 사상

온톨로지 `entity_mapping_contract_v0_1.yaml`의 7 kind 는 **새 서브타입 테이블 없이** 기존 `entity` 3서브타입(+`market_series`)의 어휘로 수용한다. 캐노니컬 ID 는 ADR-0027 규약(ULID 서로게이트) 그대로다.

| entity_kind | 캐노니컬 저장처 | 비고 |
|---|---|---|
| ISSUER | `actor(COMPANY)` + 발행 증권은 `instrument(EQUITY)` | **회사의 캐노니컬 ID 는 actor** — 종목(instrument)이 아니다. 현행 해소기의 "보통주 instrument 수렴"은 관계 저장에는 쓰지 않는다 |
| COMPANY_ENTITY | `actor(COMPANY)` | 미상장 포함. 마스터 등재 전에는 `entity_mention` 에만 존재 |
| AUTHORITY_OR_RULE | 기관 = `actor(GOVERNMENT·INSTITUTION)`, 규정 = `concept(RULE)` | |
| PRODUCT_OR_CONCEPT | `concept(BRAND·PRODUCT_FAMILY·PRODUCT·TECHNOLOGY·SEGMENT)` | 고아였던 `concept` 테이블의 첫 실사용. 개념 내 위계는 `parent_concept_id` 트리(§2.2) |
| COHORT | `concept(SECTOR·THEME·COHORT)` | |
| LOCATION_OR_HAZARD | `concept(LOCATION·HAZARD)` | |
| INDEX_OR_EXCHANGE | 지수 = `market_series(INDEX)` 참조 + 관계용 노드는 `concept(INDEX)`, 거래소 = `actor(INSTITUTION)` | `entity_relation` 양끝은 `entity` FK 라 market_series 를 직접 가리킬 수 없다 — 지수 concept 행이 `market_series` 를 속성으로 참조(후속 확장) |

`actor_type`·`concept_type` 값 어휘는 위 표가 정의한다. `concept_type` 의 DB CHECK 는 두지 않는다(어휘 미확정 시 적재 오염 방지 전례 — V202607150003).

## 2. 참조 지식 그래프 — 현재-상태 지식 (source_kind = REFERENCE)

뉴스에서 추출될 엔티티들(사람·브랜드·기업·제품)의 **개념 위계와 크로스 링크**. 이 층은 **현재 시간의 지식만** 유지한다 — point-in-time 이력 없음, 사실 변경 시 행 교체.

### 2.1 엔티티 vs 프로퍼티 판별 기준

**"뉴스에서 독립적으로 재등장·참조될 수 있으면 엔티티, 아니면 프로퍼티(컬럼·기존 시계열)."**

| 대상 | 판정 | 저장처 |
|---|---|---|
| 경영진(사람) | 엔티티 | `actor(PERSON)` — 이름은 `entity.display_name` |
| 브랜드 · 제품 · 제품군 | 엔티티 | `concept(BRAND · PRODUCT_FAMILY · PRODUCT)` |
| 지주·모자 관계 | 관계 | `entity_relation(subsidiary_of)` |
| **주식 가격 · 시총** | 프로퍼티(시계열) | 기존 `price_daily` 등 — **그래프에 넣지 않는다.** 회사 → `issuer_of`(정본 `equity_profile`) → 종목 → 시계열 조인이 조회 경로 |
| 티커 · corp_code · 국적 | 프로퍼티(스칼라) | `instrument.ticker` · `company_profile.dart_corp_code` · `actor.country_code` 컬럼 |

### 2.2 위계 배치 규칙

- **타입 위계**: `entity` → `actor(COMPANY·PERSON·GOVERNMENT·INSTITUTION)` / `instrument(EQUITY·ETF)` / `concept(BRAND·PRODUCT_FAMILY·PRODUCT·SECTOR·THEME·…)`. `actor_type` 의 `PERSON` 은 기존 CHECK 에 이미 있다.
- **개념 내 위계는 `concept.parent_concept_id` 트리**가 담당한다 (제품 ⊂ 제품군 ⊂ 브랜드). 관계 테이블에 개념-개념 위계를 중복 저장하지 않는다.
- **타입 경계를 넘는 링크만 `entity_relation`** 에 둔다 (사람↔회사, 회사↔회사, 회사↔개념).

### 2.3 참조 관계 어휘 v0.1

| relation_code | subject → object | 의미 |
|---|---|---|
| `ceo_of` | `actor(PERSON)` → `actor(COMPANY)` | 대표이사 |
| `officer_of` | `actor(PERSON)` → `actor(COMPANY)` | 그 외 임원·경영진 |
| `subsidiary_of` | `actor(COMPANY)` → `actor(COMPANY)` | 지주·모자 관계 — 자회사→모회사 방향("소속" 관행, `member_of` 와 동일) |
| `owns_brand` | `actor(COMPANY)` → `concept(BRAND)` | 브랜드 소유 |
| `produces` | `actor(COMPANY)` → `concept(PRODUCT)` | 현재 제품 포트폴리오 — 이벤트 파생 층(§3)과 **같은 코드**를 쓰고 `source_kind` 로 층을 구분한다 |

### 2.4 시맨틱 — 현재 지식 업서트

- 멱등키 = `(subject, relation_code, object)` 부분 유니크(`uq_entity_relation_reference`). 같은 사실은 한 행, 재확인은 `asof` 갱신, 사실 변경(CEO 교체 등)은 **행 교체** — 이력을 남기지 않는다.
- `valid_from`/`valid_to` 는 이 층에서 쓰지 않는다(NULL 고정). 이력이 필요해지는 시점에 이벤트 파생 층이 그 역할을 한다 — 참조 층에 기간을 소급 추가하지 않는다.
- `confidence` : 큐레이션·공식 마스터 = NULL(확정), 뉴스 추출 승인분 = 추출 신뢰도.

### 2.5 구축 파이프라인 (뉴스 추출과의 접속)

1. **시드**: 현 유니버스의 지주·대표이사·브랜드·주력 제품 큐레이션. `company_profile.dart_corp_code` 가 이미 있으므로 DART 임원·계열회사 조회가 자동 시드 소스 후보(후속).
2. **추출**: 태깅이 뽑는 역할 텍스트 → `entity_mention(kind_hint = PERSON·BRAND·…)` → 별칭 해소(§6).
3. **승격**: 미해소 `normalized_text` 빈도 상위 = 신규 엔티티 후보 큐 → 마스터 등재(ULID 발번) → 문서에서 추출된 관계 후보("A 는 B 의 자회사")를 승인 후 REFERENCE 업서트. **추출 즉시 자동 업서트는 금지** — 참조 층은 해소의 기반이라 오염 비용이 크다.
4. **역이용**: 해소기가 그래프를 되먹임한다 — "정의선" 검출 시 `ceo_of` 를 타고 회사로 해소, 브랜드 멘션을 `owns_brand` 로 회사에 전파. 그래프가 해소율을 올리고 해소가 그래프를 키운다.


## 3. 이벤트 파생 관계 어휘 v0.1 — 타입·방향 (source_kind = EVENT_DERIVED · DECLARED)

방향은 **subject → object 단방향 저장**이 원칙이다(역방향은 조회로 얻는다). 어휘는 thread 계약의 관계형 9종 + 기존 테이블에서 파생되는 정적 2종.

### 3.1 이벤트 파생 관계

| relation_code | subject (역할) | object (역할) | 소스 이벤트 타입 | 개시(valid_from) | 마감(valid_to) |
|---|---|---|---|---|---|
| `produces` | ISSUER | PRODUCT | COMPANY.PRODUCT.LAUNCH | EFFECTIVE_DATE 또는 SHIPPING 도달 | DISCONTINUED |
| `certified_for` | ISSUER | PRODUCT | COMPANY.PRODUCT.CERTIFICATION | EFFECTIVE_DATE | REJECTED·철회 정정 |
| `owns` | ACQUIRER | TARGET_COMPANY | COMPANY.M_AND_A.ACQUISITION | EFFECTIVE 도달 | CANCELLED (성사 전 취소 시 관계 미개시) |
| `has_stake` | INVESTOR | TARGET_COMPANY | COMPANY.INVESTMENT.STAKE_ACQUISITION | 보고서 EFFECTIVE_DATE | EXIT predicate 이벤트 |
| `supplies` | SUPPLIER | CUSTOMER | COMPANY.CONTRACT.SIGNING, 공시 `supply_contract_fact` | 계약 시작일(공시 우선) | 계약 종료일 |
| `restricts` | AUTHORITY | TARGET | POLICY.TRADE.EXPORT_CONTROL | EFFECTIVE | LIFTED·EASE |
| `tariff_applies_to` | AUTHORITY | TARGET | POLICY.TRADE.TARIFF_CHANGE | EFFECTIVE | REMOVE |
| `sanctions` | AUTHORITY | TARGET | POLICY.SANCTION.IMPOSITION | EFFECTIVE | LIFT |
| `member_of` | MEMBER | INDEX | MARKET_STRUCTURE.INDEX.INCLUSION | EFFECTIVE_DATE | **INDEX.EXCLUSION 이벤트가 마감** |

- 개시·마감의 일반 규칙: `valid_from` = 역할값 `EFFECTIVE_DATE` > lifecycle `EFFECTIVE`(계열 stage) 도달일 > 단발 이벤트의 `event_date`. `valid_to` = terminal stage(`lifecycle_models_v0_1.yaml`) 도달일 또는 역이벤트. 판정 불가면 NULL(열린 구간).
- 관계에 안 싣는 것: 이벤트의 나머지 역할·수량(AUTHORITY, CONTRACT_OBJECT, DEAL_VALUE 등)은 관계 속성이 아니라 **이벤트(thread) 소관**이다. 관계는 `source_thread_id`/`source_fact_id` 로 근거를 가리킨다.
- **UNKNOWN thread 에서는 관계를 만들지 않는다** — identity 결측 이벤트는 subject/object 를 확정할 수 없다. thread 승격 시 관계도 함께 생성된다.
- 상태형 relation 4종(`operation_status`·`service_status`·`trading_status`·`regulates_or_rule_status`)은 엔티티-엔티티 관계가 아니라 **엔티티 상태 변화**라 v0.1 에서 제외한다(이벤트로 충분). `COMPANY.ALLIANCE.PARTNERSHIP` 은 대칭 관계라 단방향 모델에 맞지 않아 thread 계약도 `relation: null` — 어휘 후보로만 남긴다.

### 3.2 정적 관계 (기존 테이블 파생 — 중복 저장 금지)

| relation_code | subject → object | 정본 테이블 | 규칙 |
|---|---|---|---|
| `issuer_of` | ACTOR → INSTRUMENT | `equity_profile.issuer_actor_id` | `entity_relation` 에 **복제 적재하지 않는다**. 관계 조회가 필요하면 조회 계층에서 UNION |
| `constituent_of` | INSTRUMENT → ETF | `etf_holding_snapshot` (시점별) | 동일 — 이미 일자 grain 시계열이 있으므로 관계 테이블로 평탄화하지 않는다 |

## 4. 병합 규칙 (동일 (subject, relation, object))

1. **소스 우선순위**: `DECLARED`(공시 fact) > `EVENT_DERIVED`(뉴스 thread) > `REFERENCE`(큐레이션·마스터 피드). 공시가 있으면 하위 소스 행은 조회에서 가려진다.
2. **동급 소스 중첩**: `asof` 최신 행이 이긴다. 이전 행은 삭제하지 않는다(관측 보존 — 재현 가능성).
3. **정정 전파**: thread 의 CORRECTION 이 관계 성립 자체를 뒤집으면(오보 등) 해당 thread 파생 행의 `valid_to` 를 정정 시점으로 마감한다. 행 삭제는 하지 않는다.
4. 병합은 **저장이 아니라 조회 계층의 책임**이다 — 저장은 소스별 관측 단위(멱등키: thread×relation, fact×relation, 참조 층은 s·r·o), 우선순위 적용 뷰는 projection 구현 티켓에서 추가한다.

## 5. 미해소 엔티티 처리 정책

기존 원칙("해소 실패 = DB 미적재 + quality log 계측")은 유지하되, **표면 문자열의 정본 보존처**를 추가한다.

- `entity_mention` 이 문서·주장에서 관측된 표면형을 해소 상태(`RESOLVED`·`UNRESOLVED`·`AMBIGUOUS`)와 함께 보존한다. `RESOLVED ↔ entity_id NOT NULL` 은 CHECK 로 강제된다 — **placeholder 마스터 행 생성은 금지**(스키마가 차단).
- 확정 링크의 소유권은 불변: `assertion_argument`·`event_argument` 는 여전히 해소 성공분만 싣는다(NOT NULL FK 유지).
- **승격 절차**: 재해소 배치가 `UNRESOLVED`·`AMBIGUOUS` 멘션을 별칭 확충·마스터 등재 후 재평가 → `RESOLVED` 로 갱신(`resolved_at` 기록) → 후속 재조립이 argument·thread 를 보강한다. `event_thread_link` UNKNOWN 재평가와 같은 패턴.
- `normalized_text` 빈도 집계가 **미상장 조직 마스터(unlisted_organization_master) 큐레이션 큐**가 된다 — future_entity_backlog 1번의 해결 경로.
- 동명 충돌(AMBIGUOUS)은 아무 후보도 고르지 않는다(현행 `entity_resolution._AMBIGUOUS` 규칙 유지).

## 6. 별칭 규약 (`entity_alias`)

- `alias_norm` 정규화: 소문자화·공백 제거·법인 접미사((주)·㈜·주식회사·Inc·Corp 류) 제거. 구현은 해소기와 단일 함수를 공유한다(이중 구현 금지).
- `alias_type`: `FULL_NAME`(정식명) · `ABBREV`(약칭) · `TICKER`(코드 표기) · `ENGLISH_NAME` · `OLD_NAME`(구명, `valid_to` 필수 권장) · `CURATED`(수동 등재).
- 같은 `alias_norm` 이 복수 엔티티에 존재하면 그 별칭은 결정적 매칭에서 제외하고(`is_ambiguous` 마킹) 계측만 한다 — normalize_news 의 동명 제외 전례.
- 해소 순서: 완전일치 3축(티커·정식명·display_name) → 별칭 축. LLM 재호출·유사도 매칭은 여전히 범위 밖.
- 시드 소스: `load_assertions` quality log 의 `top_unresolved`(설계상 이 용도), KRX 정식명 변형, 큐레이션.

## 7. 후속 구현 경계 (이 계약이 정의만 하고 구현하지 않는 것)

| 후속 | 내용 |
|---|---|
| 별칭 해소 구현 | `entity_resolution` 4축 확장 + alias 시드 적재 |
| 참조 그래프 시드 | 현 유니버스 지주·대표이사·브랜드·제품 큐레이션 적재 + DART 임원·계열회사 소스 검토 |
| 멘션 적재 | `load_assertions`·`assemble_events` 미해소 경로에서 `entity_mention` INSERT + 재해소 배치 |
| 관계 projection | thread·`supply_contract_fact` → `entity_relation` 적재 스텝 + 병합 조회 뷰 |
| 리소스 개정 반입 | 실험실 확정 시 `entity_mapping_contract` v0.2 통째 교체 + 이 문서 정합 갱신 |
