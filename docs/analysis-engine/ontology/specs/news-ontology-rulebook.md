---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - news-ontology-criteria.md
  - event-argument-schema-v1.md
---
# 뉴스 온톨로지 표현·스레딩 규칙집 (rulebook)

> **방법:** `skill://ontology-design-criteria`. 각 규칙 = **5슬롯**(결정·하부목표 Gk·기준·이유·반-Goodhart[A불변식/B적대/C진단]) + **코퍼스 근거** + **예시**.
> **근거 기반:** v3 전수 `news_events_2026-06_07_v3.jsonl` **39,959 이벤트 / 123,985 인자** 읽기전용 집계(LLM 프로브 없음).
> **적격성:** 표현 충실성(정보 손실·왜곡 없이 올바른 추상·단위·배정) · 스레딩 충실성(같은 실사건의 사후 변화를 정확히 엮음).

---

## 클러스터 1 — 객체·수량 표현 (seed: "원유운반선 2척")

### 코퍼스 근거
- **kind 분포:** ENTITY_UNLISTED 74,179(60%) · ENTITY 29,404(24%) · QUANTITY 20,402(16%).
- **객체역할 전부 0% 티커해결 = ENTITY_UNLISTED 문자열:** PRODUCT 12,315 · CONTRACT_OBJECT 2,616 · PROJECT 2,423 · FACILITY 1,754 · PRODUCT_FAMILY 1,561 · COMMODITY 139.
- **mention 입도 스펙트럼:** PRODUCT 평균 9자·클래스수준(`그랜저`,`잠수함`,`차량용 메모리 반도체`) / CONTRACT_OBJECT 15자·**8% 카운트내장**·스코프혼입(`원유운반선 2척`,`신반포19·25차 재건축 시공권`) / PROJECT 16자·서술구(`피지컬AI 기반 로봇 자율작업 체계 개발`).
- **count-unit 인벤토리:** 개731 대508 명424 가구179 종171 **척145** 기113 세대107 톤101 GW95 건89 MW73 만톤51 호기42 ㎿40.
- **오배정 관측:** EFFECTIVE_DATE 2,433 · REPORTING_PERIOD 1,962(날짜) · AMOUNT 1,319 · NEW_VALUE 1,085(금액)이 **ENTITY_UNLISTED** → kind 어휘가 *값을 개체로* 오분류.

### R1 — 객체 추상수준 = 재현가능 클래스
- **결정:** 객체(제품/대상)는 **정규 클래스명**으로 저장(`그랜저`=모델, `유조선`=클래스, `메모리`=클래스). 스코프·카운트·프로젝트 서술구 금지.
- **Gk:** G4(역할슬롯) · G1(식별) · telos(행위타입·주체 조회).
- **기준(정성+B):** 핸들에 숫자+카운트단위·스코프어(`도입사업`/`시공권`/`개발`) 미포함; 서로 다른 이벤트에서 **재사용되는 공통명사 클래스**.
- **이유:** "유조선 계약 전부" 질의는 핸들이 클래스라야 join. `캐나다 차세대 잠수함 도입사업`은 1회성 → 조인·스레드 실패(코퍼스: CONTRACT_OBJECT 평균 15자·스코프혼입).
- **반-Goodhart:** **[B]** 핸들 재현성(≥2 이벤트 재사용) + **[A]** 스코프/카운트 토큰 미포함 구조검사. 자연표본 정확도 = **[C]** 진단.

### R2 — entity_id 자격 = 지속 개체만; 값은 절대 개체 아님
- **결정:** 회사(티커)=`ORG_KR_*`, 미상장회사=`ENTITY_UNLISTED:*`, 제품/사물 클래스=`CONCEPT:*`, 인물=`PERSON:*`, 장소=`LOCATION:*`. **날짜·금액·비율·수량은 개체 아님 → `measures`의 값.**
- **Gk:** G4 · G8(엔티티 접지) · G6(정직).
- **기준(A):** role의 value_kind가 temporal/quantity/ratio이면 **`participants`/ENTITY_* 저장 금지**(구조 불변식). 개체 kind는 지속·조인가능 개체만.
- **이유:** EFFECTIVE_DATE·AMOUNT를 ENTITY_UNLISTED로 담은 현행(관측 2,433·1,319건) → 시간/금액 질의·조인 불가.
- **반-Goodhart:** **[A]** value_kind ↔ 저장위치 불변식(값이 participants에 있으면 fail = 구조검사).

### R3 — 카운트는 객체에서 분리된 measure
- **결정:** "2척" → `measures:{role:QUANTITY, value:2, unit:"척", group_ord=객체그룹}`. 객체 mention = 클래스만.
- **Gk:** G4 · 해석명료(단사성).
- **기준(A+B):** 객체 핸들에 숫자+카운트단위 토큰 **0**; 카운트는 measure로 복원되며 `group_ord`로 객체에 바인딩.
- **이유:** 카운트 내장(현행 CONTRACT_OBJECT 8%)은 "유조선 계약 전부"와 "몇 척" 둘 다 깨뜨림. 분리하면 둘 다 가능.
- **반-Goodhart:** **[A]** 핸들 카운트토큰 0(구조) + **[B]** 카운트 measure 복원+group 바인딩(누락 시 fail).

### R4 — 단위 패밀리(폐쇄집합, 교차환산 금지)
- **결정:** 모든 measure = `(value:number, unit, unit_family)`. family: `CURRENCY{KRW,USD}` · `RATIO{PCT}` · `COUNT{척,대,기,톤,GW,MW,명,가구,세대,개,종,건,…}` · `DURATION{년,월,일}` · `SHARES{주}`.
- **Gk:** G4 · G6(정직).
- **기준(A):** unit ∈ 폐쇄 정규집합; **교차패밀리 환산 금지**(척→KRW 불가, USD→KRW 추정 금지). value는 코드가 surface에서 산술.
- **이유:** 혼합단위(%/원/척)가 자연 보존돼야 라인아이템·이벤트스터디 정확. FX 추정은 날조(G6 위반).
- **반-Goodhart:** **[A]** unit 폐쇄집합 + 무환산 불변식(패밀리 보존 구조검사).

### 워크드 예시 — "한화오션, 원유운반선 2척 2734억 수주"
| | before(현행 canonical-1.0) | after(R1–R4) |
|---|---|---|
| 객체 | `CONTRACT_OBJECT="원유운반선 2척"` (ENTITY_UNLISTED, 카운트내장, 클래스아님) | `CONTRACT_OBJECT` 유조선 → `CONCEPT:oil_tanker` (g1) |
| 카운트 | (객체에 흡수, 조회불가) | `measures: QUANTITY value=2 unit=척 family=COUNT` (g1) |
| 금액 | `CONTRACT_VALUE="2734억원"`(문자열) | `value=273400000000 unit=KRW family=CURRENCY` (g1) |
| 결과 | "유조선 계약 전부" 조인 실패 | 클래스 조인·"몇 척"·금액·혼합단위 전부 조회가능 |

---

## 클러스터 2 — 스레드 same-vs-new + 사후 변화 (seed: "같은 사건의 사후 변화를 잘 엮느냐")

### 코퍼스 근거 (`thread_run_v3`: 20,201 스레드 / 28,506 링크)
- **다중-이벤트 스레드 = 3,370 (17%)** = 사후변화 케이스; 크기 2:1,877·3:652·…·**max 184**(장기 saga).
- **novelty_status:** FIRST 20,201 · FOLLOW_UP_STAGE 8,305 · **UNKNOWN 11,453(29%)**. **CORRECTION·DUPLICATE_REBROADCAST = 0**(미검출).
- **current_stage = 100% UNKNOWN** → 변화를 순서지을 축이 비어있음(stage 미추출; D4 의존).
- 값판정 가능 다중스레드: **값 상이(개정/정정) 216 vs 값 동일(재방송/후속) 255** → 현행 어휘로 구분 불가.

### R5 — same-vs-new 경계 = identity 기반
- **결정:** 같은 스레드 ⟺ 같은 `(type + identity_roles의 정규화 entity_id)`. **identity 값 변경 = 새 이벤트/스레드**; 비-identity(값·단계·범위·부가역할) 변경 = **같은 스레드 업데이트**.
- **Gk:** G1(식별) · G2(스레딩).
- **기준(A):** `thread_key = type + identity entity_id`(정규화, verbatim 아님); identity 결핍 시 강제연결 금지(UNKNOWN link).
- **이유:** 현행 UNKNOWN 29%는 verbatim identity 파편화 산물. entity_id 키라야 신·구표기·별칭이 한 스레드로(D9).
- **반-Goodhart:** **[A]** 결정론 키 + **precision 우선**(불확실 시 병합 안 함). merge recall(자연) = **[C]**.

### R6 — 변화유형(novelty) 어휘 = 사후변화를 유형으로 구분
- **결정:** novelty_status 확장 — FIRST_IN_THREAD · **STAGE_PROGRESSION** · **VALUE_REVISION** · **CORRECTION** · **SCOPE_AMENDMENT** · **CANCELLATION** · **DUPLICATE_REBROADCAST** · UNKNOWN.
- **Gk:** G2.
- **기준(B):** stage 전진→STAGE_PROGRESSION; 비-identity 수치변경→VALUE_REVISION; 원문 정정마커(정정공시/오류/변경)→CORRECTION; 대상 증감→SCOPE_AMENDMENT; 무산→CANCELLATION.
- **이유:** 현행 FIRST/FOLLOW_UP/UNKNOWN + stage 100% UNKNOWN → 값개정 216·재방송 255가 뒤섞임. 유형 없이는 선반영·중복 판단 불가.
- **의존:** **stage 추출(D4) 선행 필수** — 축이 비면 STAGE_PROGRESSION 판정 불가.
- **반-Goodhart:** **[B]** 각 유형은 관측신호로만 판정(stage delta / value delta / 정정마커 / dedup 근접) — 상수 라벨은 hard 케이스에서 실패.

### R7 — dedup(재방송) ≠ thread(계보): 엄격 분리
- **결정:** `dedup_cluster_id`(텍스트 근접중복)와 `thread_id`(의미 계보)는 **별개**. 내용동일 재보도 = DUPLICATE_REBROADCAST(같은 스레드, **새 상태 아님** → 상태전이·선반영 카운트에서 제외).
- **Gk:** G2 · G6(정직: 재방송을 새 상태로 세면 신호 중복계산).
- **기준(A):** `dedup_cluster_id` 동일(≠self) ⟹ novelty=DUPLICATE_REBROADCAST 강제; **불변식 `thread_id ≠ dedup_cluster_id`**.
- **이유:** 현행 DUPLICATE_REBROADCAST=0(미검출) → 재방송 255가 FOLLOW_UP/UNKNOWN으로 새어 사후변화 왜곡.
- **반-Goodhart:** **[A]** dedup 동일 ⟹ 재방송(구조) + thread≠dedup 불변식.

### 워크드 예시 — 계약 saga
`D_a`(6/1 "수주 임박", RUMORED) → `D_b`(6/10 "2척 2734억 계약", DEFINITIVE_SIGNED) → `D_c`(6/10 재보도, 동일) → `D_d`(7/1 "3천억으로 정정")
- `thread_key = CONTRACT.SIGNING | ORG_KR_042660 | CONCEPT:oil_tanker` (R5 — 표기변이 무관)
- `D_a→D_b` = **STAGE_PROGRESSION** · `D_c` = **DUPLICATE_REBROADCAST**(dedup 동일 → 상태전이 제외, R7) · `D_d` = **VALUE_REVISION**(2734억→3천억, R6)
- 결과: 에이전트가 "이 계약의 실제 사후 전개"를 재방송·정정·단계 혼동 없이 재구성.

---

## 클러스터 3 — 값-kind 어휘 재설계 (seed: 날짜·금액이 ENTITY_UNLISTED)

### 코퍼스 근거
- **값-타입 역할인데 ENTITY_UNLISTED로 저장:** PRICE 100% · RATIONALE 100% · AMOUNT 99% · REPORTING_PERIOD 99% · OLD_VALUE 95% · NEW_VALUE 90% · EFFECTIVE_DATE 79% · OWNERSHIP_RATIO 42%.
- **반면** CONTRACT_VALUE 90% · TRADE_VALUE 100% · CONTRACT_DURATION 92%는 QUANTITY → 같은 의미범주(금액/기간)가 역할따라 갈림.
- **원인:** kind 어휘 `{ENTITY, ENTITY_UNLISTED, QUANTITY}`에 **날짜·비율·가격 슬롯이 없음** → ENTITY_UNLISTED로 누출.

### R8 — value_kind 분류학
- **결정:** `value_kind ∈ {ENTITY, MONEY, RATIO, COUNT, DATE, DURATION, SHARES, TEXT}`. 날짜=DATE · 금액=MONEY · 비율=RATIO · 수량=COUNT.
- **Gk:** G4 · G6.
- **기준(A):** 모든 값역할은 비-ENTITY value_kind; DATE/MONEY/RATIO가 ENTITY_*로 저장되면 fail.
- **이유:** 현행 3종은 슬롯 부재로 값을 개체로 오분류(PRICE 100%·AMOUNT 99%·EFFECTIVE_DATE 79%).
- **반-Goodhart:** **[A]** 폐쇄집합 + "값역할 ∌ ENTITY" 불변식.

### R9 — kind ⊥ resolution (해결상태 분리)
- **결정:** 개체는 `value_kind=ENTITY` + 별도 `resolution ∈ {LISTED, UNLISTED, CONCEPT, COHORT}`. **ENTITY_UNLISTED를 kind로 쓰지 않음.**
- **Gk:** G4 · G8.
- **기준(A):** kind enum에 UNLISTED 없음; 미해결은 `resolution` 필드로.
- **이유:** 현행은 resolution(UNLISTED)을 kind에 섞어 ISSUER가 ENTITY/ENTITY_UNLISTED(73/27)로 갈림 — 같은 value_kind인데 다르게 보임.
- **반-Goodhart:** **[A]** kind·resolution 축 분리(구조 불변식).

---

## 클러스터 4 — 역할 의미 일관성 (seed: 한 역할이 여러 kind)

### 코퍼스 근거
- 혼합-kind 역할 **27개**. 이 중 **ENTITY↔QUANTITY flip = 진짜 결함:** EFFECTIVE_DATE(1932/501) · DEAL_VALUE(431/1391) · NEW_VALUE(975/110) · OWNERSHIP_RATIO(233/316) · CONTRACT_VALUE(194/1740) · BUYBACK_VALUE · QUANTITY · SHARES.
- ENTITY↔ENTITY_UNLISTED flip(ISSUER 73/27 등)은 **결함 아님** = resolution 차이(R9).

### R10 — 역할 → value_kind 전역 함수
- **결정:** 같은 역할명은 **모든 타입·이벤트에서 동일 value_kind**(min/max/required만 타입별 가변).
- **Gk:** G4.
- **기준(A):** 역할별 value_kind 단일; ENTITY-family↔value-family flip = fail(전역 lint).
- **이유:** 관측 8역할 flip → 같은 컬럼에 개체와 숫자 혼재 → 조인·해석 불가.
- **반-Goodhart:** **[A]** `role→value_kind` 함수적(위반 0 강제 = 전역 불변식).

---

## 클러스터 5 — 시간·PIT 표현 (seed: 이벤트스터디 PIT 정확성)

### 코퍼스 근거
- 날짜역할: EFFECTIVE_DATE 2,433 · REPORTING_PERIOD 1,962 · MATURITY_DATE 73.
- 표현 판정(n=4,468): **미래-dated 5%**(효과 미실현·PIT민감) · 과거 7% · **모호(분기/반기/예정/미정) 30%** · **파싱불가 58%**.
- EFFECTIVE_DATE 실제형태: `이달 1일`·`오는 2일`·`6월부터`·`2분기` → **58%가 deictic/상대형** = published_at 앵커 없이는 무의미.

### R11 — 세 시각 분리: report-time / event-time / available_at
- **결정:** 이벤트마다 `published_at`(보도=report-time) · `event_time`(발생/발효, EFFECTIVE_DATE 등에서 해석) · `available_at`(정보 가용=PIT 앵커, 기본=published_at). **상대·deictic 날짜는 published_at 기준 코드가 절대화.**
- **Gk:** G6(PIT·정직) · G2(시간축).
- **기준(A):** 상대날짜 → published_at 앵커로 절대화; `event_time`·`available_at` 별도 필드; available_at ≤ 조회시각.
- **이유:** EFFECTIVE_DATE 58% deictic(`이달`·`오는`) → 앵커 없으면 시간축·이벤트스터디 붕괴.
- **반-Goodhart:** **[A]** 상대→절대 결정론 해석(published_at 함수) + available_at 필드 필수.

### R12 — 미래-dated 효과의 PIT 정직
- **결정:** `event_time > available_at`(미래 발효)이면 사건은 지금 알려짐(available_at=보도시각)이나 **효과/결과는 미실현** → outcome을 실현값으로 저장 금지(선언만).
- **Gk:** G6 · G5(미래효과를 결과로 굽지 않음).
- **기준(A):** `event_time>available_at`인 outcome은 `realized=false`; PIT 계산은 available_at에서만.
- **이유:** 미래-dated 5%. 미래효과를 지금 실현으로 취급 = look-ahead 편향(이벤트스터디 오염).
- **반-Goodhart:** **[A]** `realized = (event_time ≤ available_at)` 구조 도출(날조 불가).

### R13 — 시간 입도·모호성 명시
- **결정:** temporal 값 = `(value, granularity ∈ {DAY,MONTH,QUARTER,HALF,YEAR,RELATIVE,UNKNOWN})`. 모호(`2분기`·`하반기`·`예정`)는 정밀일자 날조 금지 → granularity로 표기.
- **Gk:** G6(정직) · G4.
- **기준(A):** 정밀도 없는 날짜에 가짜 DAY 부여 금지; `granularity` 필수.
- **이유:** 모호 30%(분기/반기/예정) → 임의 일자 채우면 거짓 정밀.
- **반-Goodhart:** **[A]** granularity 필수 + "모호표현 → DAY 금지" 구조검사.

---

## 루프 상태
- **완료:** C1 객체·수량 · C2 스레드·사후변화 · C3 값-kind · C4 역할일관성 · C5 시간·PIT — **R1–R13**.
- **다음 후보:** 타입별 positive5/hard-negative5 수용셋(D1/D4 게이트 실체화) · C6 엔티티 마스터 트리(상위층·나중) · 질의배터리 작성.
