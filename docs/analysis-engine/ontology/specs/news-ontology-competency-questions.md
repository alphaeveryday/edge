---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - news-ontology-criteria.md
  - news-ontology-query-battery.md
  - news-ontology-rulebook.md
  - news-ontology-acceptance-sets.md
  - news-event-schema-final.md
  - event-argument-schema-v1.md
---
# 온톨로지 컴피턴시 질문 (CQ) — 애널리스트 최고결론 상한 기준

> **CQ(Competency Question) = 온톨로지가 반드시 뒷받침해야 하는 소비자 질문.** 범위를 정의하고 검증 게이트가 된다(Grüninger-Fox).
> **소비자(이번):** 애널리스트 + **가격변동 정적분석기**. 이건 `query-battery`의 telos-오라클을 **조회/조인/스레드 → 결론 상한(conclusion ceiling)** 으로 **확장**한다(대체 아님).
> **참고(only):** exploration 벤치(삭제됨, git HEAD)의 애널리스트 hard-question 축(A 주장신뢰·B 신규/surprise·C scope·D/E impact/reaction·projection·uncertainty)·6 hard-fail·`hq_answerable_rate=0.12`. 벤치는 **영감**이고, 본 CQ는 우리 53타입·20 lifecycle·역할·수량·`derived`·thread·entity_mapping에 **grounded**된다.

---

## 0. 층경계 — 결론-tier CQ의 정답이 왜 "결론 저장"이 아닌가 (반-Goodhart 심장)

온톨로지는 **해석중립**이다: 방향·호재/악재·impact·score를 저장하지 않는다(§criteria G5, `feature_specs`에서 `direction`·`role_in_impact`·`discovery_hypotheses` 제거됨). 그래서 "온톨로지가 옳은 결론을 뱉는가?"로 CQ를 정의하면 **Goodhart 미끼**다 — 해석을 온톨로지에 밀반입(G5 위반)하고 결론은 과적합 가능한 저장 추측이 된다.

**대신 결론-tier CQ는 세 성질로 만족된다 (전부 불변식[A]/적대[B] 검증가능):**

- **(S) 충분성** — 최고결론이 요구하는 **모든 입력 필드가 존재·명료**(단사·건전·바인딩)한가. `[A/B]`
- **(N) 층중립** — 온톨로지가 결론(방향·impact·score)을 **저장하지 않는가**. 그 필드가 스키마에 없음 = 구조검사(연역). `[A]`
- **(H) 정직** — 못 구하는 입력을 **UNKNOWN/UNRESOLVED로 표기**하고 backlog에 매핑하는가. `[B]` 공시無 날조 거부.

**게임 불가:** 상한이 **정직한 gap 보고를 포함**하므로 "결론을 더 많이 낸다"로 못 이긴다 — 없는 걸 지어내면 (H) 실패. 이래서 `hq_answerable_rate`(0.12)는 **게이트가 아니라 진단[C]** 이며, 통과조건은 "그 gap을 backlog로 정직 매핑"이다(높인다고 좋은 게 아님).

**query-battery와의 관계:** `Q1–Q9`(조회 오라클, 사건을 찾아온다)는 본 CQ의 **하위 입력**이다. 예: CQ9 confounder 조회 = `Q7`(시간창) + `Q1`(주체). 본 CQ는 찾아온 사건으로 **애널리스트 최고결론까지** 간다.

---

## 1. "최고결론" 상한이란 무엇인가 (정직한 천장)

한 애널리스트가 **(이벤트 구조 + 정적 가격반응)만으로 방어가능하게** 말할 수 있는 최대치:

> 정확분류 → 실제·신규성 → durable 상태변화 여부 → **특정 주체에 대한 상대 materiality** → 기대대비/가격반응 귀속 → **정직한 미지(未知)**.

**천장은 전지(全知)가 아니라 규율이다** — PIT(무-룩어헤드)·confounder 통제·정직(모름을 모름으로). 벤치의 16점 rubric + 6 hard-fail이 실은 "정직·규율"을 잰 이유가 이것. 정적분석기(가격변동)는 이 중 **기대대비·반응귀속**을 담당하는 상위 소비자이며, 온톨로지는 그 입력 기질(PIT 좌표·confounder 집합·반응배분 결정자·arb_spread)을 제공한다.

**하부목표는 §criteria의 G1–G8을 그대로 승계**(고아 방지). 결론-tier는 그 위에 소비자향 **합성 밴드** 2개를 노출한다: **materiality 사이징**(G4+G8+`derived`)과 **반응 귀속**(G2+G6+G8). 새 subgoal 아님 — 합성이다.

---

## 2. CQ 배터리 — 애널리스트 결론 파이프라인 순서

각 CQ: **질의(구체 referent)** · **최고결론(ceiling)** · **온톨로지 substrate(정확한 필드)** · **층분리(온톨로지 제공 vs 상위 계산)** · **검증[A/B/C]** · **답변가능성**.
답변가능성 = `NOW`(substrate 실재·검증) · `READY-GAP`(공식·슬롯 실재, 값 소싱 필요) · `GATED`(추출/재학습/마스터 선행).

### 밴드 1 — 무엇이 일어났나 (identity & reality)

**CQ1 — 사건 분류·자매 변별**
- 질의: "`맥쿼리, 가비아 6300억 상폐 추진`은 M&A인가 STAKE·PARTNERSHIP·MERGER와 안 섞이나?"
- 최고결론: 사건의 정확한 class(모든 후속 결론의 전제).
- substrate: `types/*.yaml` `type_id`·`predicates`(통제메뉴)·`roles.identity`; `acceptance-sets` pos5/hard-neg5.
- 층분리: 온톨로지 **직답**(상위 없음).
- 검증: **[B]** 자매 hard-neg5 전수거절(D1) / 자연표본 type_agree=**[C]**.
- 답변가능성: `NOW`(경계 정의·게이트) · 프로덕션 분류모델 정확도는 재학습 `GATED`(§gold-spec).

**CQ2 — 문서 성격(사건 vs 비사건)**
- 질의: "`$5T 시총 돌파`·`지속가능경영보고서 발간`·중립 리포트가 신규 canonical event로 생성되지 않는가?"
- 최고결론: 오피니언·홍보·시황을 사건으로 날조하지 않음.
- substrate: `doc_class ∈ {EVENT, OPINION_OR_ANALYSIS, PROMOTIONAL, (NO_EVENT_MARKET_COMMENTARY)}`.
- 층분리: 온톨로지 직답.
- 검증: **[B]** null_opinion/promo hard 케이스 거절(벤치 HF_NULL_EVENT 대응) + doc_class 흡수 후보(AWARD 72·MILESTONE-시총 43)는 EVENT 승격 금지(§acceptance-sets G).
- 답변가능성: `NOW`(doc_class 축) · ESG 등 홈부재 타입은 evolution `GATED`.

### 밴드 2 — 진짜·새로운가 (novelty · lineage · PIT)

**CQ3 — 신규성/계보/사후변화**
- 질의: "이 계약 보도는 신규인가, 스레드의 `STAGE_PROGRESSION`·`VALUE_REVISION`·`CORRECTION`·`DUPLICATE_REBROADCAST`인가?"
- 최고결론: 이미-반영된 후속 vs 진짜 신규정보(선반영 판단의 전제).
- substrate: `event_thread(thread_id, thread_key=type+identity entity_id, current_stage)` · `event_thread_link(novelty_status, dedup_cluster_id)` · `correction_markers{AMEND,CORRECT,CANCEL,REVISE,RESTATE}` · `thread_discovery_snapshot(n_prior_events, days_since_prev_stage)`.
- 층분리: 온톨로지가 스레드·novelty 유형; 상위가 "선반영 정도" 해석.
- 검증: **[A]** 불변식 `thread_id≠dedup_cluster_id`(R7) + **[B]** novelty를 관측신호(stage delta/value delta/정정마커/dedup 근접)로만 판정(R6) — 상수라벨은 hard 케이스 실패.
- 답변가능성: `GATED` — thread·dedup은 `NOW`이나 `STAGE_PROGRESSION`/`CORRECTION` 판정은 **stage 추출(D4) 선행**(현재 current_stage 100% UNKNOWN, §rulebook C2).

**CQ4 — PIT 정직 (무-룩어헤드)**
- 질의: "`6/1 시점`에 알 수 있던 것만 썼는가 — 미래 문서·미래 alias/master·미래 발효효과 배제?"
- 최고결론: look-ahead 0(이벤트스터디 오염 방지, 벤치 #1 hard-fail).
- substrate: `published_at`·`event_time`·`available_at`(PIT 앵커)·`realized`; `entity_mapping` PIT(미래 alias/master 금지); `granularity∈{DAY,…,RELATIVE,UNKNOWN}`(R13).
- 층분리: 온톨로지가 시각 3분리·realized; 상위는 available_at에서만 계산.
- 검증: **[A]** `realized=(event_time≤available_at)` 구조도출(R12) + 상대날짜→published_at 절대화(R11) = 날조불가.
- 답변가능성: `NOW`(불변식).

### 밴드 3 — 상태를 바꾸나 (stage-gated durability = projection 상한) ★핵심

**CQ5 — 단계 판정과 durable 그래프변화 정당성**
- 질의: "이 M&A는 lifecycle 어느 단계이며, 그 단계가 `owns` 엣지(durable) 생성을 정당화하나?"
- 최고결론: **rumor≠소유.** 단계별 투영: `RUMORED→EVENT_ONLY` · `DEFINITIVE_SIGNED(승인대기)→PENDING_EDGE` · `CLOSED→DURABLE_EDGE`(owns 생성 + 이전 스레드 연결). (투영 어휘는 벤치 참고; 우리는 substrate만 저장.)
- substrate: `lifecycle.stage` + `lifecycle_models_v0_1.yaml`(순서 stages + terminal) + `news_thread_contract` `relation{owns, has_stake, supplies, produces, certified_for, restricts, sanctions, tariff_applies_to, null}`.
- 층분리: **온톨로지 = {relation, 순서 stages, terminal}**; **상위(정적분석기/에이전트) = stage-gate로 EVENT_ONLY/PENDING/DURABLE 결정**(투영 판정은 해석 → 상위층).
- 검증: **[A]** `stage∈model.stages` 구조 + relation 정의 + `CANCELLED/DISMISSED` 등 terminal; **[B]** 3단계 적대셋 — `CLOSED` 이전 `owns` 생성 시 실패(벤치 HF_PROJECTION_STAGE 대응).
- 답변가능성: `GATED` — relation·순서 stages는 `NOW`; **stage 추출(D4)은 inert-until-retrain**(§gold-spec).

### 밴드 4 — 얼마나 큰가·누구에게 (materiality 사이징) — `derived` 소비

**CQ6 — 정량 규모·단위·basis·출처**
- 질의: "`비에이치아이 1883억 LNG 발전설비 공급계약`의 금액·단위·basis(TOTAL/ANNUAL)·value_source는? deal_value를 매출로 오독 안 하나?"
- 최고결론: 정확 정규화 규모 + 출처(치명 측정오류 0).
- substrate: `measures{value, unit, unit_family, basis∈{TOTAL,ANNUAL,UNKNOWN}, value_source∈{PARSED,DART,UNRESOLVED}, group_ord}`; DART 금액매칭 `|Δ|/dart<0.08`.
- 층분리: 온톨로지가 정규화 값+출처; 상위 없음(직답).
- 검증: **[A]** unit-family 무환산·`span⊂원문`(R4/R8/D8) + **[B]** 공시無→`UNRESOLVED` 강제(에이전트 24/24 날조거부).
- 답변가능성: `NOW`.

**CQ7 — 상대 materiality (특정 주체 대비)**
- 질의: "이 계약이 비에이치아이에 얼마나 material한가 = `revenue_share`? 이 딜은 인수자 대비 = `deal_size_ratio`? 지분격차 = `stake_gap_to_control`?"
- 최고결론: **needle-moving인가** — 애널리스트 사이징의 핵심.
- substrate: `types/*.yaml` `derived`{`revenue_share`=annualized_value/revenue_ttm[SUPPLIER], `op_impact_share`, `deal_size_ratio`=DEAL_VALUE/market_cap[ACQUIRER], `financing_stretch`, `deal_to_investor_mcap`, `stake_gap_to_control`, `spinoff_to_mcap`} + `common_features` entity_state{`revenue_ttm`, `market_cap`, `total_cash`, `op_profit_ttm`, `op_margin_ttm`} on `entity_id`.
- 층분리: **온톨로지 = 분자(measure)+분모(entity_state)+공식(objective `derived`)**; **상위 = "크다/작다" 판정**(해석). 공식의 `desc`("크기 판단의 핵심")는 근거이지 결론 아님.
- 검증: **[A]** `derived.inputs ⊆ 실재필드`(bundle validation) + **층중립[A]** 방향/impact 미저장(구조검사).
- 답변가능성: `READY-GAP` — 공식·역할은 `NOW`; entity_state 값 소싱 필요(= `hq_answerable_rate` 현실: 분자 준비, 분모 데이터-gap → backlog `unlisted_organization_master`·재무 스냅샷).

### 밴드 5 — 시장이 반영했나 (가격변동 정적분석기 밴드) ★사용자 초점

**CQ8 — 기대 대비 surprise**
- 질의: "`삼성전자 2Q 영업익` 헤드라인 beat가 컨센/전년比 대비 진짜 surprise인가, 증수감익(mixed)인가?"
- 최고결론: 기대 vs surprise(방향·크기), 헤드라인 착시 제거.
- substrate: `measures{ACTUAL_VALUE, CONSENSUS_VALUE, OLD_VALUE, NEW_VALUE, GUIDANCE_RANGE_LOW/HIGH}` + `derived{guidance_change_pct, yoy_growth, implied_op_margin_q, margin_delta_yoy, rev_profit_divergence}` + entity_state{`metric_year_ago_value`, `year_ago_revenue_q`, `year_ago_op_q`}.
- 층분리: 온톨로지 = actual·baseline·yoy·괴리 substrate; 상위 = surprise 판정.
- 검증: **[A]** baseline PIT(전년치=과거, 컨센=available_at 시점) + **[B]** `rev_profit_divergence`(증수감익) mixed-signal hard 케이스(벤치 "headline beat, revenue miss").
- 답변가능성: `READY-GAP` — yoy/margin/괴리 substrate `NOW`; 컨센서스 값 소싱 필요(backlog).

**CQ9 — 가격/거래량 반응과 귀속 (정적분석기 최대출력)** ★
- 질의: "`SK하이닉스 청주공장 화재` 창의 주가/거래량 반응을 그 화재에 귀속 가능한가 — 같은 날 `메모리 가격 폭등`이 confounder 아닌가?"
- 최고결론: abnormal/residual return + **confounder 통제 귀속** — 정적분석기가 낼 수 있는 최대치. (반응을 단일사건에 단정귀속 = 벤치 HF_REACTION_ATTRIBUTION.)
- substrate: (i) `event_fact(available_at PIT 앵커, entity_id)` (ii) **confounder 집합 = 같은 `entity_id`·같은 창의 다른 `event_fact` 행**(thread 내 + family 교차) (iii) `derived{arb_spread`=시장 P(close) 역산, `OFFER_PREMIUM`(PIT-computable), `partner_mcap_asymmetry`=반응배분 1차 결정자, `price_vs_acq_avg}`.
- 층분리: **온톨로지 = (i)PIT 이벤트 좌표 (ii)confounder 후보집합 (iii)반응배분 결정자·시장역산치**; **정적분석기 = residual/abnormal 계산·귀속 판정**; **에이전트 = 최종 결론.** 온톨로지는 가격 시계열도 결론도 저장 안 함.
- 검증: **[A]** available_at 앵커 + 같은창 다중이벤트 조회 가능(구조) + **[B]** confounder 존재 시 단정귀속 거절.
- 답변가능성: `READY-GAP`/`상위` — 이벤트 좌표·confounder 집합·`arb_spread`/`OFFER_PREMIUM`은 `NOW`(PIT-computable); 가격/거래량 데이터 + residual 모델 = **상위 정적분석기 별도 구축**(§criteria "이벤트스터디는 나중·동적"). **정적분석기가 사는 자리.**

### 밴드 6 — 방향·접지·정직 (join 정확성 & 정직한 gap)

**CQ10 — 역할 방향 정확성**
- 질의: "acquirer≠target, supplier≠customer, plaintiff≠defendant, authority≠target이 뒤집히지 않았나?"
- 최고결론: 정확한 인과 방향(누가 누구에게).
- substrate: `participants.slot∈{subject,object}` + `roles.identity/primary` + `role→value_kind` 전역함수(R10).
- 층분리: 온톨로지 직답.
- 검증: **[A]** role→value_kind 함수적(위반0) + slot 구조 + **[B]** role-inversion 가드(벤치 HF_ROLE_INVERSION).
- 답변가능성: `NOW`.

**CQ11 — 엔티티 접지·크로스소스 조인**
- 질의: "주체를 stable `entity_id`로 접지해 가격·시총·피어·과거이벤트와 조인 가능한가? 미매핑은 날조 대신 리뷰큐인가?"
- 최고결론: 조인 backbone(모든 materiality·반응 CQ의 전제).
- substrate: `entity_id`·`entity_kind{ISSUER, COMPANY_ENTITY, PRODUCT_OR_CONCEPT, COHORT, AUTHORITY_OR_RULE, LOCATION_OR_HAZARD, INDEX_OR_EXCHANGE}`·`resolution{LISTED,UNLISTED,CONCEPT,COHORT}`·`alias_map`.
- 층분리: 온톨로지 = 접지·리뷰큐; 상위 = 관계·계층 추론.
- 검증: **[A]** `kind⊥resolution`(R9)·결정론 키 + 미매핑→리뷰큐(날조0).
- 답변가능성: `PARTIAL` — 상장사 `NOW`; 미상장·제품·authority·index는 backlog 6종(`unlisted_organization_master`·`product_revenue_concept_graph`·`supplier_customer_network`·`official_policy_rule_master`·`geospatial_asset_registry`·`index_constituent_flow_model`) `GATED`.

**CQ12 — 정직한 gap 보고 (honesty 천장)**
- 질의: "이 결론에서 데이터로 **못 답하는** 부분을 온톨로지가 UNKNOWN/UNRESOLVED로 표기하고, 그 gap이 어느 backlog에 매핑되나?"
- 최고결론: **정직한 애널리스트는 모름을 안다** — 상한의 필수 성분(벤치 uncertainty_gaps).
- substrate: `basis=UNKNOWN`·`value_source=UNRESOLVED`·`resolution`·`missing_identity_policy=EMIT_UNKNOWN_LINK_ONLY`·`granularity=UNKNOWN`·`future_entity_backlog`·`future_feature_backlog`.
- 층분리: 온톨로지 = gap 표기+backlog 매핑; 상위 = gap 하에서의 조건부 결론.
- 검증: **[A]** 정직 불변식(미해결→UNKNOWN, 평균/0 대체 금지) + **[B]** 공시無 24/24 거부.
- 답변가능성: `NOW` — **이 CQ가 `hq_answerable_rate=0.12`를 형식화**: 온톨로지는 심층결론 대부분이 substrate-gap임을 **정확히 보고**하고 각 gap을 backlog id로 매핑해야 한다(진단[C]이지 게이트 아님).

---

## 3. 추적성 (CQ → G1–G8, 고아 0)

| CQ | 하부목표 | CQ | 하부목표 |
|---|---|---|---|
| CQ1 분류·자매 | G3·G5 | CQ7 materiality | G4·G8 |
| CQ2 doc_class | G3·G6 | CQ8 surprise | G4·G6 |
| CQ3 novelty | G1·G2 | CQ9 반응·귀속 | G6·G8·G2 |
| CQ4 PIT | G6 | CQ10 역할방향 | G4 |
| CQ5 stage·durable | G2·G1 | CQ11 접지 | G8 |
| CQ6 규모·출처 | G4·G6 | CQ12 정직 gap | G6·G7 |

G1–G8 전부 ≥1 CQ로 덮임 · 모든 CQ가 어떤 Gk로 추적됨 · 고아 CQ 0.

---

## 4. 답변가능성 원장 (정직 매핑 — 이게 "12%"의 실체)

| 상태 | CQ | 막는 것 / 매핑 |
|---|---|---|
| `NOW` | CQ1·CQ2·CQ4·CQ6·CQ10·CQ12 | substrate 실재·불변식/적대 검증 |
| `READY-GAP` | CQ7·CQ8 | 공식·슬롯 실재, **값 소싱**(entity_state·컨센서스) 필요 → 재무 스냅샷·backlog |
| `GATED` | CQ3·CQ5 | **stage 추출(D4) inert-until-retrain**(§gold-spec) — novelty·durability의 축 |
| `상위 별도` | CQ9 반응계산 | 가격 시계열 + residual 모델 = **정적분석기 상위층**(온톨로지는 좌표·confounder·arb_spread만) |
| `PARTIAL` | CQ11 | 미상장·제품·authority·index **마스터 6종**(backlog) |

**정직 결론:** 결론 상한의 **뼈대(분류·PIT·규모·방향·정직·좌표)는 `NOW` 도달**. **살(materiality 값·surprise 컨센·durability 단계·반응 계산·엔티티 마스터)은 gap** — 온톨로지 결함이 아니라 **데이터·마스터·상위층·재학습**의 문제이며 전부 backlog에 매핑된다. 이 정직 매핑 자체가 CQ12의 통과다.

---

## 5. 충돌 우선순위 (사전식, 이유 — 평균 금지, §criteria 승계 + 결론-tier 1건 추가)

1. **불가침** — 정직(G6)·층중립(G5). 거짓/해석 밀반입은 연구기반 무효.
2. **결론 정직 > 결론 완결** — gap을 지어내느니 "gapped"로 남긴다(CQ12가 CQ7/8/9를 이긴다; 없는 materiality·surprise·귀속 날조 금지).
3. **식별 정확**(G1) — 오병합이 누락보다 질의를 더 망침(merge precision 우선).
4. **방향·바인딩·접지**(G4·G8) — 조인·이벤트스터디 정확성.
5. **materiality/reaction substrate**(derived·좌표) — 커버리지.
6. **효율** — 정확성과 거래 금지. 전 축 **precision>recall**.

---

## 6. 워크드 예시

### 6.1 M&A 3단계 — CQ5(durable) + CQ1(분류) + CQ4(PIT)
동일 스레드 `thread_key = COMPANY.M_AND_A.ACQUISITION | ORG_A | ORG_B` (relation=`owns`):

| 보도 | stage | 온톨로지 제공 | 상위 투영결정(해석) | 정직 |
|---|---|---|---|---|
| "A, B 인수 **검토**(관계자)" | `RUMORED` | stage+relation | `EVENT_ONLY` (owns 금지) | 공식확인 UNRESOLVED |
| "A, B 인수 **본계약**(반독점 대기)" | `DEFINITIVE_SIGNED` | stage+`DEAL_VALUE`+`AUTHORITY`+`OFFER_PREMIUM`(PIT) | `PENDING_EDGE`(blocking=반독점) | remedy_risk UNKNOWN |
| "A, 승인 후 인수 **완료**" | `CLOSED` | stage(terminal)+novelty `STAGE_PROGRESSION` | `DURABLE_EDGE`(owns 생성+스레드 연결) | — |

온톨로지는 stage·relation·스레드만 저장; **"언제 owns를 만드나"는 상위 stage-gate.** `CLOSED` 이전 owns 생성 = 적대셋 실패(CQ5 [B]).

### 6.2 confounder 귀속 — CQ9(정적분석기) + CQ11(접지)
`SK하이닉스 청주공장 화재`(EXOGENOUS.ACCIDENT, `entity_id=ORG_KR_000660`, available_at=D) ∧ 같은 창 `메모리 가격 폭등`(INDUSTRY.PRICE.COMMODITY_PRICE_CHANGE, 같은 섹터).
- 온톨로지 제공: 두 `event_fact`(같은 entity_id/섹터·같은 창) = **confounder 후보집합** + PIT 좌표.
- 정적분석기: residual return 계산 후 **단일귀속 거부**(동시 이벤트 존재) → "화재 순효과 식별불가, 두 요인 공존" (CQ9 [B], 벤치 HF_REACTION_ATTRIBUTION 회피).
- 온톨로지가 없으면: 반응을 화재에 단정 → 오귀속.

### 6.3 계약 materiality — CQ6 + CQ7
`비에이치아이, 1883억 LNG 발전설비 공급계약`(CONTRACT.SIGNING):
- CQ6: `CONTRACT_VALUE=188,300,000,000 KRW, basis=UNKNOWN, value_source=PARSED` (공시매칭 시 DART).
- CQ7: `revenue_share = annualized_value / revenue_ttm[ORG_KR_비에이치아이]` — 온톨로지가 분자·분모·공식 제공. **"매출 대비 크다"는 상위 판정**(온톨로지는 비율만, 호재판정 없음).

---

## 7. 경계 (Always / Ask first / Never)

- **Always:** CQ 만족 = (S)충분+(N)층중립+(H)정직 셋 다. gap은 UNKNOWN/UNRESOLVED + backlog 매핑. 골드는 원자료에서.
- **Ask first:** 새 CQ 편입(telos-유래 + held-out + 골드 3바 통과) · `derived` 공식 변경(측정형태 바뀜) · 정적분석기 상위층 인터페이스 확정.
- **Never:** 결론(방향·impact·score·호재/악재)을 온톨로지·CQ 골드에 저장 · `hq_answerable_rate` 같은 소프트프록시를 게이트로 승격 · gap을 값으로 날조 · 미래정보로 반응/귀속 계산.

---

## 8. 상태 / 다음

- **완료(설계):** 결론-tier CQ 12문(§2) + **심층 CQ 18문(§9: 집합·관계·다중홉·임계·행위자)** · 층경계 반-Goodhart(S/N/H) · 깊이 이론(4축+2정련자·정리 1–4) · G1–G8 추적 · 답변가능성 원장 · 충돌 우선순위 · 워크드 4.
- **게이트(green-light 시):** CQ별 dev/held-out 골드 주석(원자료 산출) + 정직-매핑 감사. `NOW` 6문은 즉시 적대셋(§acceptance-sets)으로 검증가능.
- **선행 의존:** CQ3·CQ5 = **stage 추출(D4) 재학습**(§gold-spec) · CQ7·CQ8 = 재무/컨센 스냅샷 · CQ9 = 정적분석기 상위층 · CQ11 = 엔티티 마스터 6종(backlog).

## 9. 심층 CQ — 관계·집합·시퀀스·임계·행위자 (깊이 이론 + 배터리)

> 사용자 시드: ①"어떤 사건을 겪은 기업들"(집합) ②"어떤 사건의 누구와 거래한 기업들"(관계·다중홉) ③"얼마 이상 거래"(정량 임계) ④"어떤 정치인의 규제"(행위자 귀속). §2가 **단일사건 결론(D0)**이면 여기는 **그래프 위 집합·관계·시퀀스 결론**이다.

### 9.1 깊이의 이론 — 4축 + 2정련자

기질 = 그래프. 노드 `{event_fact, entity(entity_id), event_thread, measure, entity_state}`. 엣지 `{event→participant(역할타입 subject/object), event→measure, event→thread(stage·novelty), event→relation(owns/has_stake/supplies/produces/certified_for/restricts/sanctions/tariff_applies_to), entity→entity_state}`.

깊이 = 좌표 **(h, q, ℓ, v)** + 정련자 **(m, a)**:
- **h 홉수** — 0 사건내부 · 1 주체앵커 · 2+ 전이(이웃의 이웃).
- **q 양화** — point · set · grouped(코호트=set-of-sets) · relation(쌍).
- **ℓ 논리·시간** — atomic · ∧/∨ · 순서(lead-lag) · 반사실.
- **v 엣지유효성** — observed-raw(모든 공동참여, 반드시 stage/novelty 필터) vs projected-durable(stage-gated relation만).
- **m 정량 임계**("얼마 이상") — measure/derived ≥ θ 필터.
- **a 행위자 접지**("정치인") — 기관 → 개인(정치인) 귀속 깊이.

### 9.2 정리 (깊이의 제1원리 — 왜 깊을수록 정직이 지배하나)

**정리1 (Observed⊊True · 홉 단조).** 관측그래프 G_obs(뉴스 명시) ⊆ 진짜그래프 G_true. h=0,1은 G_obs≈충분; **h↑일수록 격차 단조 증가**(뉴스는 거래의 표본, 전수 아님). ∴ 깊은 relational CQ의 정답 = **관측 슬라이스 한정** 또는 **backlog 매핑 gap**. `observed_scope` 명시 필수, "전수" 참칭 = (H)정직 위반.

**정리2 (그래프-수준 stage-gate).** projected-durable 다중홉은 각 엣지가 durable stage(owns=CLOSED). rumored/pending을 소유엣지로 전파 = **그래프-수준 HF_PROJECTION_STAGE**(CQ5 다중홉 일반화). raw 순회는 stage/novelty 필터 필수(루머·재방송을 실사건 계산 금지, R7).

**정리3 (정량 임계의 전제 — "얼마 이상").** value≥θ 필터는 (i) `value_source∈{PARSED,DART}`로 resolved (ii) `basis` 정규화(TOTAL↔ANNUAL 통일, 무환산 R4) (iii) `UNRESOLVED` 제외. 미해결을 임계에 넣으면 거짓집합; annual θ와 total θ 혼용도 거짓. → 임계 코호트는 반드시 `observed_scope` + basis 명시.

**정리4 (행위자 접지 깊이 — "정치인").** 정책을 기관이 아닌 **개인(정치인)**에 귀속하려면 person/authority 마스터 필요. 현행 `AUTHORITY_OR_RULE`=source-string(마스터 없음) → "트럼프"="Trump"="美 대통령" 파편화(R5의 authority판). ∴ 정치인-귀속 CQ는 **GATED**(신규 backlog 후보: politician/authority 마스터). 관측(authority mention 문자열)만 NOW.

**따름정리 (집합 양화 정직).** 코호트(D2)는 `novelty=FIRST_IN_THREAD` + `dedup_cluster_id`로 중복제거 — 재방송·후속을 별개 기업/사건으로 세면 코호트 팽창(R7).

### 9.3 배터리 (깊이별 · 좌표 태그)

**D2 — 집합 노출 코호트 (어떤 사건을 겪은 기업들)**

| CQ | 질의(구체) | 좌표 | substrate / traversal | 답변가능성 |
|---|---|---|---|---|
| CQ13 단순노출 | "7월 유상증자(COMPANY.CAPITAL.EQUITY_ISSUANCE)한 기업 전부" | h1·grouped·atomic·raw | `event_fact[type,window]` group by `subject_entity_id` | `NOW` |
| CQ14 **임계 코호트** | "딜 5000억↑ / 계약 revenue_share≥20% 기업" | +m | `event_measure[DEAL_VALUE]≥θ` / `derived.revenue_share≥.2`, value_source∈{PARSED,DART} | `READY-GAP` +scope(정리3) |
| CQ15 결과·생존 | "무산(CANCELLED) vs 성사(CLOSED) 딜 기업" | h1·grouped·atomic·thread | `event_thread.current_stage∈{CANCELLED,CLOSED}` | `GATED`(stage) |
| CQ16 신규성-정직 | "재방송·후속 제외 처음 리콜 겪은 기업" | +dedup | `novelty=FIRST_IN_THREAD` 필터(따름정리) | `NOW`-partial |

**D3 — 1홉 거래상대 (누구와 거래/규제)**

| CQ | 질의 | 좌표 | substrate / traversal | 답변가능성 |
|---|---|---|---|---|
| CQ17 직접상대 | "삼성전자가 CUSTOMER인 공급계약의 SUPPLIER 전부" | h1·relation·atomic·raw | `event_participant[role=CUSTOMER,id=삼성] ⋈ USING(event_id) [role=SUPPLIER]` | `PARTIAL`(unlisted) |
| CQ18 **상대+임계** | "삼성전자와 1000억↑ 계약한 공급사" | +m | CQ17 ⋈ `event_measure[CONTRACT_VALUE]≥θ`(basis 정규화) | `PARTIAL`+scope(정리3) |
| CQ19 **정치인/기관 규제 대상** | "트럼프(AUTHORITY)가 추진한 관세·제재의 TARGET 기업" | +a | `event[POLICY.TRADE.TARIFF_CHANGE\|SANCTION].participant[role=AUTHORITY≈정치인] → [role=TARGET]` | `GATED`(정치인 master, 정리4) |
| CQ20 공통 authority 피해 | "같은 규제기관 제재 co-target 기업" | h1·relation | `participant[role=AUTHORITY=X]` group → `TARGET_COMPANY` | `NOW`-partial / `GATED`(master dedup) |

**D4 — 다중홉 전이·전파 (거래상대의 상대·2차 피해)**

| CQ | 질의 | 좌표 | substrate / traversal | 답변가능성 |
|---|---|---|---|---|
| CQ21 공급망 전파(관측) | "화재난 공장 운영사의 (관측)고객사" | h2·relation·atomic·raw | `EXOGENOUS.ACCIDENT[OPERATOR=E] → E as SUPPLIER in CONTRACT → CUSTOMER` | observed `NOW` / true `GATED`(supplier_customer_network, 정리1) |
| CQ22 소유그래프 2홉 | "A가 owns(CLOSED)한 B가 has_stake한 C" | h2·relation·atomic·**projected** | durable relation 순회, **각 엣지 CLOSED/DISCLOSED** | `GATED`(정리2 stage-gate) |
| CQ23 정치인 규제 2차전파 | "정치인 X 규제 대상기업 → 그 공급/고객사" | h2·+a·+relation | CQ19 → supplier/customer hop | `GATED`(정치인+공급망 master) |
| CQ24 컨셉 노출 | "이 관세 품목(PRODUCT_OR_SCOPE=CONCEPT)에 노출된 기업" | h2·relation | policy `PRODUCT_OR_SCOPE` → issuer 노출 | `GATED`(product_revenue_concept_graph) |

**D5 — 결합패턴 (공기·교집합)**

| CQ | 질의 | 좌표 | substrate | 답변가능성 |
|---|---|---|---|---|
| CQ25 신호충돌 | "유상증자 ∧ 같은분기 대주주 지분매각(OWNERSHIP.INSIDER_TRANSACTION) 기업" | h1·set·∧·raw | 두 event set ∩ (entity_id, 분기창) | `NOW` |
| CQ26 confounder 집합 | "같은 창·주체 2+ 이벤트(반응 귀속불가)" | set·∧ | `event_fact` group by (entity_id,window) having count≥2 (=CQ9 일반화) | `NOW` |

**D6 — 시퀀스·선반영 (lead-lag·전이)**

| CQ | 질의 | 좌표 | substrate | 답변가능성 |
|---|---|---|---|---|
| CQ27 lead-lag | "가이던스 하향(LOWER) 후 ≤30일 어닝 미스 기업" | h1·set·순서·raw | 두 event ordered by `available_at`, same ISSUER | `READY-GAP`(컨센) |
| CQ28 스레드 전이·선반영 | "RUMORED→CLOSED 성사 딜의 단계별 가격경로" | 순서·thread·+상위 | thread stages + `available_at` + 가격조인(상위) | `GATED`(stage)+상위 |

**D7 — 반사실·abstention (정직 상한)**

| CQ | 질의 | 좌표 | 정답(정직) | 답변가능성 |
|---|---|---|---|---|
| CQ29 진짜모집단 | "이 관세로 실제 매출타격 본 기업 전부" | h2·+m·반사실 | **집합 날조 금지** → 관측 TARGET 한정 + product_revenue_concept_graph gap (정리1 극한) | `GATED` |
| CQ30 반사실 | "이 인수 무산됐다면 영향받았을 기업" | 반사실 | 상위·모델 소관 → **abstain** + 관측사건만 | `상위` |

### 9.4 심층 추적성 + 정직 원장

- **추적(고아 0):** CQ13-16→G1·G8(코호트=엔티티 집합) · CQ17-20→G4·G8(역할방향·접지) · CQ21-24→G8·G2(전이·스레드) · CQ25-26→G1·G6(교집합·confounder) · CQ27-28→G2·G6(시간축·PIT) · CQ29-30→G6·G7(정직·진화).
- **원장(정리1의 실증):** `NOW` CQ13·17(부분)·25·26 · `READY-GAP` CQ14·18·27 · `GATED` CQ15·22·28(stage), CQ19·23(정치인 master), CQ21·24·29(공급망·컨셉 master), CQ20(authority dedup) · `상위` CQ28·30. → **h↑·a↑·반사실일수록 GATED 비중 급증**(정리1·4를 데이터가 확증). **신규 backlog 후보: politician/authority 마스터**(정리4).

### 9.5 워크드 — 공급망 전파 + 임계 + 정치인 (정리 1·2·3·4 동시)

"**트럼프 관세(정치인)로 1,000억↑(임계) 타격받은 반도체 공급사(전파)**" 분해:
- **a(정치인):** `POLICY.TRADE.TARIFF_CHANGE` `AUTHORITY≈"트럼프"` → 관측 mention `NOW`; "트럼프=Trump" 통합·타 정책 구분은 `GATED`(정치인 master, 정리4).
- **관측 TARGET(h1):** 관세의 `TARGET`/`PRODUCT_OR_SCOPE` 기업 = `NOW`(명시분).
- **전파(h2):** TARGET의 공급사/고객사 = 관측 CONTRACT 슬라이스만 `NOW`; **진짜 공급망 `GATED`**(supplier_customer_network, 정리1).
- **임계 m(1,000억↑):** `CONTRACT_VALUE≥θ` 단 `value_source∈{PARSED,DART}`·`basis` 통일·`UNRESOLVED` 제외 → **`observed_scope` 명시**(정리3).
- **정직 결론:** "관측범위 내 X사(공급계약 명시·resolved·≥θ)"로 **한정 보고** + 나머지는 politician/supplier/concept master gap 매핑. **"타격받은 기업 전부"라 참칭 금지**(정리1).

### 9.6 경계 (심층 전용 Never)
- **Never:** 관측 슬라이스를 **전수로 참칭**(observed_scope 누락) · **durable 아닌 엣지로 전파**(정리2) · **novelty 미필터 코호트**(재방송 팽창) · **UNRESOLVED/basis 불일치를 임계에 포함**(정리3) · **정치인 귀속을 마스터 없이 단정**(파편화, 정리4) · 반사실을 관측으로 저장.

## 근거 / 출처
- 방법: `skill://ontology-design-criteria` · 상위기준 `news-ontology-criteria.md`(G1–G8) · 조회오라클 `news-ontology-query-battery.md`(Q1–Q9) · 규칙 `news-ontology-rulebook.md`(R1–R13) · 적대셋 `news-ontology-acceptance-sets.md`(D1) · 스키마 `news-event-schema-final.md`.
- 타입/역할/수량/derived/entity_state SSOT: `src/alphamale/events/ontology/resources/{common_features_v0_1.yaml, types/*.yaml, lifecycle_models_v0_1.yaml, news_thread_contract_v0_1.yaml, entity_mapping_contract_v0_1.yaml}`.
- 참고(영감, 미저장): exploration 벤치(git HEAD `data/fixtures/events/exploration_bench/**`) — 애널리스트 hard-question 축·6 hard-fail·hq_answerable_rate.
