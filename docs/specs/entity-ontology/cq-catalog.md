# 엔티티 온톨로지 CQ 카탈로그 (EO-CQ v1)

엔티티 체계(분류·속성·관계)가 답해야 할 역량 질문의 정본. 모든 셰이프 슬롯·관계는 여기 있는 CQ 최소 1개에 추적되어야 존재가 정당화된다(입장 규칙). 추적은 [ontology.sqlite](ontology.sqlite) `cq_trace`가 전수 보관한다.

**기준 문서**: [../news-ontology-cohort-cq.md](../news-ontology-cohort-cq.md) (event-ontology repo `docs/specs/news-ontology-cohort-cq.md`에서 2026-07-24 반입, 원본 무수정). 코호트 케이스 90개 중 **엔티티 능력에 의존하는 케이스**를 역추적해 CQ로 승격했고(출처 열의 `#n` = 코호트 케이스 번호, `발견⑤` = 원본 §6 발견 번호), 소비자 4곳(해소기·코호트·분석엔진·콘솔) 수요를 추가했다.

## A. 해소기 (entity resolution·전파)

| id | 질의 | 출처 | 요구 능력 |
|---|---|---|---|
| EO-CQ-01 | 표면형 문자열 하나가 주어졌을 때 어느 캐노니컬 엔티티로 해소되는가? | 해소기 (top_unresolved 수확) | entity_alias 축, 정규화 규칙 |
| EO-CQ-02 | 동명 표면형(복수 후보)일 때 무엇으로 판별하거나 안전하게 포기하는가? | 해소기, normalize_news 동명 제외 전례 | is_ambiguous, AMBIGUOUS 상태 |
| EO-CQ-03 | 인물 멘션("정의선")을 소속 회사로 전파할 수 있는가? | #16 (CEO 교체 N=73) | PERSON 마스터 + ceo_of·officer_of |
| EO-CQ-04 | 브랜드·제품 멘션을 소유·생산 기업으로 전파할 수 있는가? | #21 (LAUNCH N=400), #49 (CONCEPT 0건) | BRAND·PRODUCT concept + owns_brand·produces |
| EO-CQ-05 | 역할 슬롯에 잘못된 종류의 엔티티가 접지되는 것을 스키마·계약이 차단하는가? | 발견⑤ (AUTHORITY에 보통주 접지, RULE에 삼성전자 67건) | 역할→entity_kind 제약, kind→서브타입 사상 |

## B. 준거집합(코호트) 구성

| id | 질의 | 출처 | 요구 능력 |
|---|---|---|---|
| EO-CQ-06 | 비상장 자회사의 사건을 상장 모회사로 귀속할 수 있는가? | #48 (kind 2종뿐·is_listed 없음) | subsidiary_of + 상장 여부 판정(issuer_of 파생) |
| EO-CQ-07 | 특정 기업의 고객사·공급사 1홉 코호트를 방향 있게 구성할 수 있는가? | #10 (삼성 SUPPLIER 조인 0건), #43 | supplies 방향 페어 + 관계 저장 |
| EO-CQ-08 | 수출통제→공급사→고객사 2홉 전파 코호트가 가능한가? | #50 (관계+stage 이중 선행) | restricts + supplies 합성, stage 게이트 |
| EO-CQ-09 | 사건 주체가 대기업집단 계열인지 독립기업인지 구분할 수 있는가? | #75 (매칭 좌표) | subsidiary_of 사슬(전이 폐포) |
| EO-CQ-10 | 유니버스 마스터로 "12개월 무사건 대조군"을 만들 수 있는가? | #56 (마스터 없음, instrument 342 근사) | COMPANY 마스터 완전성 + status |
| EO-CQ-11 | 섹터·테마 좌표로 코호트를 층화할 수 있는가? | #29 (sector 미탑재), #42 | SECTOR·THEME concept + in_sector |
| EO-CQ-12 | 기관(AUTHORITY)별 제재·인허가 이력을 집계할 수 있는가? | #19·20·44 (접지 오염) | AUTHORITY 마스터 + 별칭(공정위↔공정거래위원회) |
| EO-CQ-13 | 합병 양측·원고/피고 페어를 방향 있게 복원할 수 있는가? | #46·47 (페어 실측 0) | identity 역할 페어 보존(event_argument) — 관계 아님 |

## C. 분석엔진·피처

| id | 질의 | 출처 | 요구 능력 |
|---|---|---|---|
| EO-CQ-14 | 이벤트 시점에 엔티티 상태(시총·상장여부·TTM 매출)를 조인할 수 있는가? | 발견⑧ (entity_state 부재), 리소스 common_features | entity_state 조인 경로(시계열·프로필) — 그래프 밖 |
| EO-CQ-15 | 관세·수출통제의 대상 품목→노출 기업을 계산할 수 있는가? | #28 (SCOPE 역할 데이터 없음) | tariff_applies_to·restricts + PRODUCT 연결 |

## D. 콘솔·검토

| id | 질의 | 출처 | 요구 능력 |
|---|---|---|---|
| EO-CQ-16 | 기업 하나의 현재 프로필(경영진·브랜드·제품·지주관계·종목)을 한 번에 조회할 수 있는가? | 콘솔 (검토 화면) | 참조층 관계 전체 + issuer_of |
| EO-CQ-17 | 미해소 표면형을 빈도순으로 검토·승격할 수 있는가? | 해소 운영 (마스터 큐레이션 큐) | entity_mention 집계 + 승격 절차 |
| EO-CQ-18 | 임의 관계·엔티티의 출처(공시/뉴스/큐레이션)와 확신도를 추적할 수 있는가? | 검토 거버넌스, GLEIF validation 동형 | source_kind·confidence·근거 FK |

## 우선순위·상태 규약

- **P1** = 코호트 케이스 상태 G/Y를 직접 막고 있는 것 (EO-CQ-05·06·07·11·12) — 발견 ②⑤⑧의 직결 해소.
- **P2** = 전파·2홉·프로필 (EO-CQ-03·04·08·09·16).
- **P3** = 운영·감사 (나머지).
- 상태: `SCHEMA-READY`(현행 스키마로 답 가능) / `NEEDS-FILL`(스키마 있음·데이터 없음) / `NEEDS-DESIGN`(이 설계가 채움). 전수는 sqlite `cq` 테이블.

## 경계 (원본 §8 승계)

- CQ 추가·삭제는 수요 근거 필수 (코호트 케이스 번호 또는 소비자 지목).
- EO-CQ-13처럼 **관계가 아니라 이벤트 역할 보존**으로 답해야 하는 CQ를 관계로 오배정하지 않는다 (지속성 테스트).
- EO-CQ-14는 그래프 밖(시계열·프로필 조인)이 정답 — 속성을 관계로 승격하지 않는다.
