---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-24
updated: 2026-07-24
method: skill://ontology-design-criteria
related:
  - news-ontology-competency-questions.md
  - news-ontology-query-battery.md
  - news-ontology-criteria.md
---
# 준거집합 CQ v2 — 코호트 배터리 (90케이스) + 커버리지 스냅샷

> **재편 원리:** 온톨로지 품질의 제1 측정 = **요구 준거집합(cohort)을 오탐·미탐 없이 구성하는 능력.** 케이스(수요)가 상위 조직 원리이고, 축(단위·PIT·동질성·의존구조·재현성)은 ① 케이스별 채점 루브릭 ② FP/FN 원인 분류계로 **강등**된다. 기존 CQ 30문(`news-ontology-competency-questions.md`)은 케이스들의 행-수준(L1) 전제조건 진단층으로 흡수된다.

## 0. 문제의 형식화

케이스 c = (모집단 술어 φ_c, 시간창 W, 요구 좌표 X_c). 시스템 산출 Ŝ_c vs 골드 S_c:

- **품질(c) = (P, R)** — P = 1−|Ŝ\S|/|Ŝ|, R = 1−|S\Ŝ|/|S| + **모든 FP/FN에 원인축 라벨** (수집누락/doc_class 오분류/타입 오분류/미접지·오접지/dedup 과잉·미달/stage 오판/창 이탈/값 결측).
- 케이스는 수요에서 유래하므로 굽힐 수 없다(반-Goodhart). 축-우선 배터리는 합성 마이크로테스트로 통과를 연기할 수 있어 폐기.
- 이벤트 스터디 관점: 준거집합 오류는 추정량 오류항으로 1:1 전파 — 결측상관(선택편의), 비사건 혼입(감쇠), 중복(가짜 유의성), t0 오차(창 이탈·룩어헤드), 이질 혼합(상쇄), 좌표 오차(EIV), 오염 미식별(교란 합산), 조인 오류(생존편의), 군집 무시(SE 과소), 버전 드리프트(비재현).

## 1. 케이스 카드 계약

필수 필드: `id · tier · 질의(자연어) · φ(기질 술어) · 골드등급 · 판별 스토리(무엇이 틀리기 쉬운가 — 없으면 슬롯 박탈) · 상태`.

**골드 3등급** — ① **C(census)**: 외부 전수 대조 가능(DART·KRX·신평사·지수사업자) → 진짜 recall 측정, 배터리의 척추. ② **P(pooled)**: 뉴스-온리, 풀링+판정 → recall은 **하한만**(정직 표기). ③ **A(adversarial)**: 자매타입·비사건·재방송 미끼 포함 판별셋.

상태 코드 — **G**: 지금 스키마+데이터로 구성 가능(실측 N 병기) · **Y**: 스키마 표현 가능하나 데이터 결측/오염 · **R**: 스키마 결측(선행 PR 필요).

## 2. T1 — 많이 요구하는 30 (수요 근거: 코퍼스 빈도 + 표준 스크린)

| # | 준거집합 | φ (기질) | 골드 | 상태 (실측 N, 2026-07-08~24) |
|---|---|---|---|---|
| 1 | 분기 어닝 beat | RESULT_RELEASE, ACTUAL>CONSENSUS | P | **R** — 값·컨센 좌표 부재 (타입 N=573) |
| 2 | 가이던스 하향 | GUIDANCE_CHANGE + pred=LOWER(assertion 조인) | P | G△ N=13 |
| 3 | 유상증자 발표 | EQUITY_ISSUANCE | **C**(DART) | G N=51 |
| 4 | 자사주 매입 결정 | SHARE_BUYBACK | **C** | G N=44 |
| 5 | 배당 증액/삭감 | DIVIDEND_DECISION + OLD/NEW 값 | **C** | **R** 방향값 부재 (타입 N=38) |
| 6 | 무상증자·액면분할 | STOCK_SPLIT | **C** | G N=1 (희소) |
| 7 | IPO 상장 확정 | IPO + stage=EFFECTIVE | **C**(KRX) | Y — stage 오염 (타입 N=297) |
| 8 | CB·주식연계채 발행 | DEBT_ISSUANCE(+pred) | **C** | G△ N=60 |
| 9 | 1000억↑ 공급계약 | CONTRACT.SIGNING + VALUE≥θ(basis 정규화) | **C** | 존재 G N=282 / 임계 **R** |
| 10 | 삼성전자 고객 수주사 | CUSTOMER=삼성 → SUPPLIER | P | Y — 상대역 미조립, 실측 조인 0건 |
| 11 | M&A 인수측 | ACQUISITION, role=ACQUIRER | **C** | G N=82 |
| 12 | M&A 피인수측 | role=TARGET_COMPANY | **C** | Y — 역할 데이터 0 |
| 13 | 무산 M&A | thread stage=CANCELLED | P | Y — thread stage 전무 |
| 14 | 5%↑ 신규 지분보고 | STAKE_ACQ + RATIO≥5 | **C**(5%룰) | 존재 G N=60 / 비율 **R** |
| 15 | 내부자 매도 | INSIDER_TRANSACTION 방향(pred) | **C** | Y△ N=39 (방향 미검증) |
| 16 | CEO 교체 | EXECUTIVE_CHANGE | C부분 | G N=73 |
| 17 | 희망퇴직·구조조정 | WORKFORCE.LAYOFF | P | G N=25 |
| 18 | 피소 기업(피고) | LAWSUIT, role=DEFENDANT | P | G N=69 |
| 19 | 공정위 제재 대상 | REGULATORY_ACTION, AUTH=공정위 | C부분 | Y — AUTHORITY 접지 오염 |
| 20 | 인허가 획득 | CERTIFICATION (+AUTH별) | P | G N=91 / 기관별 Y |
| 21 | 신제품 출시 | PRODUCT.LAUNCH | P+A | G N=400 |
| 22 | 증설 발표 | CAPACITY_CHANGE pred=EXPAND | **C** | G N=257 |
| 23 | 리콜·판매중단 | (타입 부재 — REGULATORY/PRICING 근사) | P | Y — 타입 갭 후보 |
| 24 | 거래정지 | TRADING_HALT | **C**(KRX) | G N=179 |
| 25 | 지수 편입/편출 | INDEX.IN/EXCLUSION | **C** | G N=8 |
| 26 | 신용등급 강등 | CREDIT.RATING_CHANGE + 방향 | **C** | G△ N=18 |
| 27 | 목표가 하향 | TARGET_PRICE_CHANGE | P+A | G N=36 |
| 28 | 관세 대상 품목·기업 | TARIFF_CHANGE, SCOPE→TARGET | P | Y — SCOPE 역할 데이터 없음 (N=17) |
| 29 | 금통위 결정일 금융주 창 | POLICY_RATE × sector | **C** | Y — sector 좌표 미탑재 (N=9) |
| 30 | 수출통제 노출 | EXPORT_CONTROL | P | G N=3 (희소) |

## 3. T2 — 지금까지 못했던 30 (가능케 한 기질 명기)

| # | 준거집합 | 가능케 한 기질 | 골드 | 상태 |
|---|---|---|---|---|
| 31 | 재방송·후속 제거 최초보도 클린 코호트 | novelty=FIRST_IN_THREAD | P+A | G N=196 (단 UNKNOWN 64% → R 하한 경고) |
| 32 | 루머 딜 vs 확정 딜 분리 | stage | P | Y — stage 오염·미추출 |
| 33 | 확정 후 무산(SIGNED→CANCELLED) | 스레드 경로 | P | Y |
| 34 | as-of 지식상태 재구성 | PIT available_at | A | G (정정마커 부분) |
| 35 | 사후 정정 원보도 토글 | novelty=CORRECTION | P | Y — 방출 0건 |
| 36 | 멀티기업 기사 사별 금액 정귀속 | group_ord 바인딩 | A | **R** — 컬럼 부재 |
| 37 | 연간화 임계(500억↑) | basis+annualized_value | C부분 | **R** — event_measure 부재 |
| 38 | revenue_share≥20% 계약 | derived+entity_state | P | **R** |
| 39 | 시총 30%↑ 딜 | deal_size_ratio | C부분 | **R** |
| 40 | 지배권 격차 5%p 이내 취득 | stake_gap_to_control(KR) | C부분 | **R** |
| 41 | ±3d 단독사건 클린 인증 | confounder census | P감사 | G — **실측 clean rate 1.4%** (53/3,851) |
| 42 | 산업 공통충격 동반 제외 | family 교차 | P | G |
| 43 | 사고기업의 관측 고객사 1홉 | relation+방향 | P | Y — 관계역 0 |
| 44 | authority별 제재 이력 | AUTHORITY 접지 | C부분 | Y — 접지 오염 |
| 45 | 동일 정책 파생 군집 key | 스레드·정책 귀속 | P | Y — thread_id만 존재 |
| 46 | 합병 양측 페어 | 2-slot 페어 | C부분 | Y — 페어 실측 0 |
| 47 | 원고·피고 반대처치 페어 | slot 방향 | P | Y — PLAINTIFF 부재 |
| 48 | 비상장 자회사→모회사 귀속 | entity_kind+is_listed | P | Y — kind 2종뿐, is_listed 없음 |
| 49 | CONCEPT 노출 | PRODUCT_OR_CONCEPT | P | **R** — concept 0건·역할 없음 |
| 50 | 수출통제→공급사→고객사 2홉 | multi-hop+stage-gate | P | **R** — 관계+stage 이중 선행 |
| 51 | 발표≠발효 lag | event_date vs available_at | C부분 | G△ (realized 플래그 없음) |
| 52 | 장후 보도 익일 세션 정렬 | published_at KST | A | G — post 911건 실측 |
| 53 | 가이던스 하향→30d 내 미스 | 타입 2종 ordered join | P | G 실측 3건 (창 짧음) |
| 54 | 스레드 생존(CLOSED vs CANCELLED) | 종결 상태 | P | Y |
| 55 | 반복 인수자(12M 3건↑) | subject 스레드 집계 | C부분 | G△ (기간 부족) |
| 56 | 12개월 무사건 대조군(부재 인증) | 유니버스 마스터 | P감사 | Y — 마스터 없음(instrument 342 근사) |
| 57 | 재방송 증폭 상위 | DUPLICATE_REBROADCAST 카운트 | P | Y — 방출 0건 |
| 58 | 협상 중 금액 변한 딜 | VALUE_REVISION | P | **R** — 어휘+값 이중 부재 |
| 59 | 사건밀도 레짐 | 표본-수준 집계 | 자기대조 | G — p90=21, max=150/일 |
| 60 | 공시-뉴스 레이스 | available_at vs DART 접수시각 | **C필수** | Y — disclosure_document 22건, 조인키 미확립 |

## 4. T3 — 흥미로운 실험 30 (준거집합 = 실험 설계)

61 단계별 CAR 분해(RUMORED/SIGNED/CLOSED) [Y-stage] · 62 루머 적중률 [Y-stage] · 63 최초보도 전 드리프트(누출 후보) [G△-price_daily 有] · 64 정정 반전 [Y-novelty] · 65 재방송 추가반응(이중계산 실증) [Y-novelty] · 66 커버리지 편의 대상화(상/하위 매칭) [G] · 67 공시선행 vs 뉴스선행 반응속도 [Y-#60] · 68 컨센 부재 소형주 반응 [R] · 69 증수감익 분해 [R] · 70 가이던스 워크다운 체인 [G△] · 71 5%룰 문턱 비선형 [R] · 72 평단 대비 손익별 행동 [R] · 73 NPS 캐스팅보트 [R-entity_state] · 74 자사주 방어 발동 [R] · 75 대기업집단 vs 독립 매칭 [Y-좌표] · 76 시총 비대칭 제휴 [R] · 77 사고→고객사 lag [Y-관계] · 78 관세 스테이지 사다리 [Y-stage] · 79 판결 vs 행정처분 [G N=30/36] · 80 인허가 승인/거부 대칭 [G△-pred] · 81 지수 발표일 vs 실효일 이중 창 [Y] · 82 거래정지 해제 첫 세션 [G N=179] · 83 강등 전 뉴스밀도 [G] · 84 군집일 SE 교정 실증 [G-A8] · 85 이중 t0(루머/확인) δ 민감도 [Y-stage] · 86 어닝시즌 confounder 밀도 [G] · 87 vocab bump 표본 diff [G-운영] · 88 무사건 대조군 숨은 사건 감사 [P감사] · 89 오류 주입→CAR 왜곡 보정 [방법론] · 90 P/R 열화→추정편의 탄성(전달함수) [방법론]

**#89·90은 게이트가 아니라 방법론**: 품질→추정 전달함수를 먼저 재서 P/R 게이트 임계를 데이터로 선언한다.

## 5. 축의 재배치 — 채점 루브릭 + 오류 라벨

케이스별 리포트 = (P, R) + 부속 감사열: 중복률(A2) · δ 분포(A3) · 층화좌표 결측률(A4) · 좌표 오차율(A5) · clean rate(A6) · 접지율(A7) · cluster key 유무(A8) · 재현 diff(A9). 모든 FP/FN은 원인축 1개로 라벨 → 파이프라인 수정 Pareto 산출. **축당 최소 3케이스 커버리지 제약**(케이스 선정의 Goodhart 차단; 현행 행렬에서 A8은 #45·84·27뿐 — 하한 준수 확인).

## 6. 커버리지 스냅샷 — 골든 기간 P0 실측 (2026-07-24, dev Cloud Event Store)

**골든 기간 P0 = 2026-07-08 ~ 2026-07-24** (17일, source_event N=3,851, 유일 가용 구간). 결측 구조도 원장에 기록: **7/19 수집 0건, 7/18 30건**(파이프라인 부트스트랩기) — A1 결측 원장 1호.

**집계: G 34 / Y 39 / R 17** (T1: 17/9/4 · T2: 9/13/8 · T3: 8/17/5)

| 발견 | 실측 | 영향 케이스 | 조치 |
|---|---|---|---|
| ① stage 자유텍스트 오염 | 이벤트 grain 94% NULL + 나머지에 비통제값 43종("completed"·"체결"·"부인"…), thread current_stage 100% NULL | #7·13·32·33·54·61·62·78·85 (9케이스) | v4 D4 stage 추출 재학습 + 통제어휘 CHECK — GATED 확정 |
| ② 단일역할 조립 | 방향 페어(ACQ+TGT, SUP+CUS, PLA+DEF) 실측 **0건**; TARGET_COMPANY·CUSTOMER·PLAINTIFF 역할 자체 부재; 삼성 SUPPLIER 코호트 0 | #10·12·43·46·47·77 + 2홉 전부 | v4 participants[] 다중역할 포팅 선행 |
| ③ novelty 미방출 2종 | CORRECTION·DUPLICATE_REBROADCAST 0건, UNKNOWN 63.7% | #35·57·64·65, #31의 R 하한 | threading 로직 보강 + UNKNOWN 축소 |
| ④ t=0 충실도 | published→available δ 중앙값 **9.5h**, p90 **3.1일** (n=30,077) | A3 전체, #52 (post 911건은 세션 정렬 가능) | available_at 의미론 재검(수집시각→보도시각), δ 감사 상설화 |
| ⑤ 접지 오염 | AUTHORITY 역할에 "…보통주" instrument 접지, RULE 역할에 삼성전자 67건 | #19·20·44, A7 전반 | 역할→entity_kind 제약(v4 entity_mapping_contract) |
| ⑥ clean rate 1.4% | ±3d 단독사건 53/3,851 | #41, 이벤트 스터디 실효 N | dedup 개선(③)과 동시 재측정 — 진짜 오염이 아니라 중복 팽창 혐의 |
| ⑦ 타입 데이터 공백 8종 | MACRO 3(고용·GDP·물가)·EXOGENOUS 3(사이버·재해·보건)·EXCHANGE_OUTAGE·SANCTION | 해당 T1 케이스 골드 불가 | 수집 소스 확장 또는 케이스 유예 |
| ⑧ 스키마 결측(R 17종의 원인) | event_measure·group_ord·slot·entity_kind·entity_state·consensus·concept(0건)·realized 부재; predicate는 assertion grain에만(조인 필요) | #1·5·9·14·36~40·49·50·58·68~74·76 | edge 스키마 확장 PR(값 아규먼트 축) — 기존 통합계획 PR 2와 일치 |
| ⑨ 사이드 기질 존재 | price_daily 3,766 · supply_contract_fact 13 · disclosure_fact 48 · actor 311 · thread_discovery_snapshot 전건 | T3 가격측 실험 즉시 가능 | event_price_observation(0건) 채우면 #63·83·84 가동 |

## 7. 골드 구축 계획

1. **파일럿 5**: #3 유증(C·DART) · #9 공급계약(C·단일판매공시) · #24 거래정지(C·KRX) · #31 클린 최초보도(P+A) · #41 오염 인증(P감사). 기간 = P0.
2. C-급은 기계 대조(공시번호↔사건 매칭키) → 상시 게이트화. P-급은 케이스당 골드 30~50건.
3. 케이스 수준 dev/held-out **60/30 분할** — held-out은 스키마 튜닝 금지(§query-battery 원칙 승계).
4. 정직 원장: P-급 recall은 하한. #88이 하한의 품질을 측정.

## 8. 경계

- **Always:** 케이스 편입 = 수요 근거 + 판별 스토리 + 골드 산출법 3종 세트. FP/FN엔 원인축 라벨.
- **Ask first:** 케이스 추가/삭제(수요 재검), 골드등급 강등(C→P), P/R 게이트 임계 선언(#90 전달함수 선행 권장).
- **Never:** 축-우선 마이크로테스트로 게이트 대체 · P-급 recall을 진짜 recall로 참칭 · held-out으로 튜닝 · 관측 슬라이스를 전수로 참칭.
