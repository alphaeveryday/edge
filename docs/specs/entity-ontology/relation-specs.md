# 관계 명세서 (v1)

관계 어휘의 정본 명세. 관계 하나 = 9필드 {표준 앵커, 방향, 역명, 전이성, 기수성, **판별식**, 시간 지위, 채움 소스, **NOT**}. 게이트(G1 CQ · G2 표준 앵커 · G3 판별식 · G4 OntoClean · G5 형식 성질 · G6 채움 가능성)를 전부 통과해야 등재. 전수 데이터·추적성은 [ontology.sqlite](ontology.sqlite) `relation`·`cq_trace`, 수용/기각/유예 전건은 `decision`.

운영 규칙(병합 우선순위·업서트·저장 계약)은 [../../contracts/entity-relations.md](../../contracts/entity-relations.md)가 SSOT — 이 문서는 그 근거층이다.

## 1. 참조 지식층 (source_kind=REFERENCE, 현재 상태·무이력) — 6종

| code | 방향 (S→O) | 역명(조회) | 전이 | 기수성 | 표준 앵커 | 판별식 | 채움 소스 | NOT |
|---|---|---|---|---|---|---|---|---|
| `ceo_of` | PERSON→COMPANY | has_ceo | ✗ | 회사당 0..n(공동대표 허용) | Wikidata P169 | **등기 대표이사** — "그 법인"의 CEO(그룹 총수 아님, P169 주석 승계) | 뉴스 추출 승인·큐레이션·(후속)DART 임원 | 회장·총수·오너 일가 지위 자체(≠대표이사) |
| `officer_of` | PERSON→COMPANY | has_officer | ✗ | N:M | FIBO 임원 관계군 | **등기 임원**(사내·사외이사·감사) | 상동 | 비등기 집행임원·직원 |
| `subsidiary_of` | COMPANY→COMPANY | has_subsidiary | **✓**(사슬) | 자회사당 모회사 0..1 | GLEIF IsDirectlyConsolidatedBy · FIBO hasSubsidiary · schema.org parentOrganization · Wikidata P749 | **연결회계 기준**(지배력) — GLEIF 정합. 지분율 수치는 관계 속성이 아니라 공시 fact 참조 | 큐레이션·(후속)DART 계열회사·공시 | 단순 지분 보유(→이벤트층 `has_stake`)·대기업집단 동일 소속(비지배) |
| `owns_brand` | COMPANY→BRAND | brand_owner | ✗ | N:M | schema.org Brand · Wikidata P127+brand | 상표의 **보유·운영 주체**(라이선시 아님) | 큐레이션·뉴스 추출 승인 | 라이선스 사용권·유통권 |
| `produces` | COMPANY→PRODUCT | produced_by | ✗ | N:M | schema.org manufacturer(역) · Wikidata P1056 | **자사 생산·판매 주력 제품**(현재 포트폴리오) | 큐레이션·뉴스 추출 승인 | 유통·리셀·단순 판매 대행 |
| `in_sector` | COMPANY→SECTOR | sector_members | ✗ | 회사당 1차 섹터 0..1(+테마 N) | Wikidata P452(industry) · schema.org isicV4 계열 | **외부 분류체계 승계**(KRX 업종·GICS) — 자체 판정 금지 | 마스터 피드(KRX)·큐레이션 | 테마(투기적 묶음)와 산업분류의 혼동 — 테마는 concept(THEME) 별도 |

- `in_sector`는 이번 개정의 유일한 **신규 어휘**다. 근거: EO-CQ-11(층화 좌표), 케이스 #29·42, 앵커 P452 — G1~G6 전부 통과 (결정 로그 D-07).
- 시간 지위: 전 관계 snapshot(업서트·무이력). 이력이 필요해지는 순간 이벤트층이 담당 — 참조층에 기간 소급 금지.

## 2. 이벤트 파생층 (source_kind=EVENT_DERIVED·DECLARED, 유효기간) — 9종

thread 계약 `relation` 선언 승계(발명 0). 코호트 케이스 추적 추가.

| code | 방향 | 소스 타입 | lifecycle | 개시/마감 | 코호트 케이스 |
|---|---|---|---|---|---|
| `owns` | ACQUIRER→TARGET_COMPANY | M_AND_A.ACQUISITION | DEAL | EFFECTIVE / CANCELLED(성사 전 미개시) | #11·12·13·46 |
| `has_stake` | INVESTOR→TARGET_COMPANY | INVESTMENT.STAKE_ACQUISITION | DEAL | 보고 EFFECTIVE_DATE / EXIT | #14 |
| `supplies` | SUPPLIER→CUSTOMER | CONTRACT.SIGNING + supply_contract_fact | DEAL | 계약 시작(공시 우선) / 종료 | #9·10·43·50·77 |
| `produces` | ISSUER→PRODUCT | PRODUCT.LAUNCH | PRODUCT_TECH | EFFECTIVE_DATE·SHIPPING / DISCONTINUED | #21 |
| `certified_for` | ISSUER→PRODUCT | PRODUCT.CERTIFICATION | PRODUCT_TECH | EFFECTIVE_DATE / REJECTED | #20·80 |
| `restricts` | AUTHORITY→TARGET | TRADE.EXPORT_CONTROL | POLICY | EFFECTIVE / LIFTED·EASE | #30·50·78 |
| `tariff_applies_to` | AUTHORITY→TARGET | TRADE.TARIFF_CHANGE | POLICY | EFFECTIVE / REMOVE | #28 |
| `sanctions` | AUTHORITY→TARGET | SANCTION.IMPOSITION | POLICY | EFFECTIVE / LIFT | (데이터 0 — 발견⑦, projection 후순위) |
| `member_of` | MEMBER→INDEX | INDEX.INCLUSION | MARKET_STRUCTURE | EFFECTIVE_DATE / **EXCLUSION 역이벤트** | #25·81 |

- UNKNOWN thread(identity 결측)에서는 관계 미생성 — 승격 시 동반 생성. 현행 UNKNOWN 63.7%(발견③)가 이 층 실효성의 선행 지표: threading 개선이 관계 projection보다 먼저다.
- 참조층 `produces`와 같은 코드 공유, `source_kind`로 구분 (공시·이벤트 근거가 있으면 그 층이 우선).

## 3. 정적 파생층 — 2종 (복제 저장 금지)

| code | 방향 | 정본 | 규칙 |
|---|---|---|---|
| `issuer_of` | COMPANY→EQUITY | `equity_profile.issuer_actor_id` | entity_relation 복제 금지 — 조회 UNION. is_listed 파생의 원천 (EO-CQ-06) |
| `constituent_of` | EQUITY→ETF | `etf_holding_snapshot` | 시점별 정본 존재 — 평탄화 금지 |

## 4. 게이트 결정 로그 (요약 — 전수 sqlite `decision`)

| id | 대상 | 판정 | 근거 |
|---|---|---|---|
| D-01 | 참조층 5종(ceo_of·officer_of·subsidiary_of·owns_brand·produces) | 수용 | EO-CQ-03·04·06·07·09·16, 앵커 전건 존재 |
| D-02 | 이벤트층 9종 | 수용(projection 후속) | thread 계약 승계, 코호트 케이스 추적 |
| D-03 | 정적 2종 | 수용(비저장) | 정본 테이블 존재 |
| D-04 | 상태형 4종(operation·service·trading_status·regulates_or_rule_status) | **기각** | 지속성 테스트 실패 — object가 상대 엔티티 아님(이벤트 소관) |
| D-05 | PARTNERSHIP | **기각(보류)** | owl:SymmetricProperty — 단방향 저장 모델 부적합, thread 계약도 relation:null |
| D-06 | 지분율·직함 상세를 관계 속성으로 | **기각** | 관계≠이벤트 복제 — 수치는 공시 fact·이벤트 소관 |
| D-07 | `in_sector` 신설 | 수용 | EO-CQ-11·케이스 #29, 앵커 P452, 판별식=외부 분류 승계 |
| D-08 | `ultimate_parent_of`(최상위 지배) | **유예** | GLEIF 앵커는 있으나 subsidiary_of 전이 폐포로 파생 가능 — 별도 저장 불요(EO-CQ-09) |
| D-09 | RULE·LOCATION·HAZARD·INDEX 마스터 | **유예** | 실측 0건(발견⑦⑧) — 데이터 없는 마스터는 오염 위험만 |
| D-10 | 설립일·legal form·ISIN 속성 | **유보** | 앵커(GLEIF L1·ISO 20275) 있으나 대응 CQ 없음 — 게이트 미충족 |
