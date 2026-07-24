# 엔티티 타입 백본과 셰이프 명세 (v1)

타입별 "무엇을 가져야 하는가"의 정본. 형식은 셰이프 명세(슬롯 + 레인 + 기수성 + 필수성 — SHACL NodeShape 개념의 PG 구현: NOT NULL=minCount1, UNIQUE=maxCount1, CHECK=sh:in). 전수 슬롯 데이터는 [ontology.sqlite](ontology.sqlite) `shape_slot`, 그래프는 [graph.html](graph.html).

## 1. 타입 백본 — OntoClean 검증

메타속성: +R(rigid, 존재 내내 유지) / ~R(anti-rigid, 역할) / +I(식별 기준 보유) / +D(외부 의존). 제약: **~R은 타입이 될 수 없다 → 관계로 강등**.

| 후보 | 메타속성 | 판정 | 저장처 |
|---|---|---|---|
| COMPANY | +R +I +U | 타입 | `actor(COMPANY)` |
| PERSON | +R +I(복합 — §3) +U | 타입 | `actor(PERSON)` |
| AUTHORITY(정부·기관) | +R +I +U | 타입 | `actor(GOVERNMENT·INSTITUTION)` |
| BRAND | +R +I(복합) | 타입 (법인 아님 — schema.org Brand 정합) | `concept(BRAND)` |
| PRODUCT·PRODUCT_FAMILY | +R +I(트리 경로) | 타입 | `concept(...)` + `parent_concept_id` |
| SECTOR·THEME | +R +I | 타입 (코호트 층화 좌표, EO-CQ-11) | `concept(SECTOR·THEME)` |
| EQUITY·ETF | +R +I | 타입 | `instrument(...)` |
| **CEO·임원** | **~R** +D −I | **타입 금지 → 관계** `ceo_of`·`officer_of` | `entity_relation` |
| **상장사** | **~R**(phased — 상폐돼도 같은 회사) | **서브타입 금지 → 파생** | `issuer_of` 조인으로 판정 (EO-CQ-06) |
| **대기업집단 계열사** | **~R** +D | **타입 금지 → 관계 사슬** | `subsidiary_of` 전이 폐포 (EO-CQ-09) |
| RULE·LOCATION·HAZARD·INDEX | +R | 타입이나 **v1 유예** — 코퍼스 실측 0건(원본 발견⑦⑧) | `concept(...)` 예약 |

## 2. 슬롯 우선순위 사다리 (모든 타입 공통 절차)

0. **분류**: 백본 1개(rigid만) + 파셋(변할 수 있는 소속 — 섹터는 파셋이지 서브클래스가 아님)
1. **식별**: +I 공급 슬롯 — 비면 마스터 등재 금지 (`entity_mention`에 머무름)
2. **본질**: 존재 내내 불변 (GLEIF LEI-CDF L1이 법인 기준 카탈로그)
3. **기술**: CQ가 요구하는 것만 (없으면 기각 — Gruber 최소 개입)
4. **관계**: [relation-specs.md](relation-specs.md)의 게이트 통과분

## 3. 타입별 셰이프 (요약 — 전수는 sqlite)

### COMPANY — `actor(COMPANY)` + `company_profile`
| 레인 | 슬롯 | 현행 | 판정 근거 |
|---|---|---|---|
| 분류 | actor_type=COMPANY (백본) | ✅ `ck_actor_type` | rigid |
| 분류·파셋 | 섹터 → `in_sector` 관계 | 공백 | EO-CQ-11, 분류 아닌 파셋(섹터 변경≠정체성 변경) |
| 식별 | `dart_corp_code` (UNIQUE) | ✅ | C-급 골드 조인키(공시번호↔사건, 케이스 #60), GLEIF RA-ID 대응 |
| 식별(보조) | 정규화 정식명 + `country_code` | ✅ display_name·country_code | 미상장 잠정 식별 — 동명 충돌 시 AMBIGUOUS |
| 본질 | 설립일·legal form(ISO 20275) | 공백 | **유보** — GLEIF L1 앵커는 있으나 대응 CQ 없음 (게이트: 앵커∧CQ 필요) |
| 기술 | `profile_as_of_date` | ✅ | 저빈도 갱신 슬롯의 as_of 패턴 |
| 파생 | is_listed | 컬럼 금지 | `issuer_of`(equity_profile) 존재로 파생 (EO-CQ-06·14) — 이중 저장 금지 |
| 관계 | ceo_of·officer_of(수신)·subsidiary_of·owns_brand·produces·in_sector·issuer_of | 관계층 | relation-specs |

### PERSON — `actor(PERSON)`
| 레인 | 슬롯 | 판정 근거 |
|---|---|---|
| 분류 | actor_type=PERSON (백본) | ✅ CHECK 이미 존재 |
| 식별 | **이름 단독 식별 불가** — (정규화명 + 소속 회사) 복합 | 동명이인. Wikidata도 인물은 참조 필수 |
| **등재 게이트** | **관계 동반 등재만 허용**: `ceo_of`/`officer_of` ≥1개와 동시 생성 | +I를 관계가 공급하는 유일 타입. 이름만 있는 인물 행 금지 — 접지 오염(발견⑤)의 인물판 예방 |
| 본질 | country_code(선택) | GLEIF 대응 없음, 낮은 우선 |
| 관계 | ceo_of·officer_of(발신) | EO-CQ-03, 케이스 #16 |

### AUTHORITY — `actor(GOVERNMENT·INSTITUTION)`
| 레인 | 슬롯 | 판정 근거 |
|---|---|---|
| 분류 | actor_type=GOVERNMENT\|INSTITUTION | ✅ |
| 식별 | 정규화 기관명 (전역 유일) + **별칭 필수 등재**(약칭: 공정위↔공정거래위원회) | EO-CQ-12, 발견⑤ 접지 오염의 직접 해소 — 역할→kind 제약과 별칭이 세트 |
| 관계 | restricts·tariff_applies_to·sanctions(발신, 이벤트층) | 케이스 #19·28·30·44 |

### BRAND · PRODUCT · PRODUCT_FAMILY — `concept`
| 레인 | 슬롯 | 판정 근거 |
|---|---|---|
| 분류 | concept_type (CHECK 없음 — 어휘는 이 명세가 정본) | V202607150003 전례 |
| 분류·트리 | `parent_concept_id`: PRODUCT ⊂ PRODUCT_FAMILY ⊂ BRAND | 개념 내 위계는 트리 소관 (관계 테이블 금지) |
| 식별 | BRAND = (소유사, 정규화 브랜드명) 복합 · PRODUCT = (트리 경로, 정규화명) | 브랜드·제품명은 전역 유일 아님 ("갤럭시" 단독 금지) |
| **등재 게이트** | BRAND는 `owns_brand` 동반, PRODUCT는 parent 또는 `produces` 동반 | 고아 concept 금지 — 케이스 #49의 "concept 0건"을 채우되 오염 없이 |
| 관계 | owns_brand·produces(수신), tariff_applies_to·restricts의 대상(이벤트층) | EO-CQ-04·15 |

### SECTOR · THEME — `concept(SECTOR·THEME)`
- 식별: 분류체계 코드 승계(KRX 업종·GICS — 자체 발명 금지). `in_sector` 수신. 코호트 층화 좌표 전용 (EO-CQ-11, 케이스 #29·42).

### EQUITY · ETF — `instrument`
- 완비: `(market_code, ticker)` UNIQUE + `issuer_actor_id` FK. ISIN은 **유보**(ADR-0027 — 필요 시 속성 확장). 주가·시총 등 측정치는 시계열 테이블 소관 (EO-CQ-14, 그래프 밖).

### RULE · LOCATION · HAZARD · INDEX(노드) — v1 유예
- 근거: 원본 발견⑦(수집 공백)·⑧(concept 0건) — 채울 데이터가 없는 마스터는 오염 위험만 있다. `concept_type` 어휘만 예약하고 등재 게이트는 후속 개정에서 정의.

## 4. 채움률 계측 (GLEIF validation 동형)

필수 슬롯 충족률·식별 슬롯 결측률·등재 게이트 위반 0건이 품질 지표다. 전수 정의는 sqlite `shape_slot.required`, 계측 구현은 후속(멘션 적재 티켓)에서 quality log에 편입한다.
