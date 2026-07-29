---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
related:
  - news-normalization-v3.md
  - ../../../../src/libs/ontology/src/edge_ontology/resources/process/news_thread_contract_v0_1.yaml
  - ../../../../src/libs/ontology/src/edge_ontology/resources/relation/role_bindings_v0_1.yaml
  - news-ontology-criteria.md
---
# 이벤트 아규먼트 스키마 v1 — 측정가능한 문제 정의 + 최선 스키마 결정

> **상위 기준:** 이 문서는 `news-ontology-criteria.md`(방법: `skill://ontology-design-criteria`)의 **적용결과**다. 아래 P0–P6은 고정 법칙이 아니라 그 기준에서 **도출된 특수사례**이며, 게이트/진단 구분은 상위 문서의 반-Goodhart class(A 불변식·B 적대시험만 게이트, 통계정확도는 진단)를 따른다.

> **베이크오프 근거:** `experiment/` (60 뉴스 × 4 스키마, deepseek-v4-flash, temp 0, 재실행 결정성) · `extract_out.json`·`scored.json`·`gold.json`·`dart_resolved.json`·`agent_out.json`
> **판정 재계산(무-API, 캐시):** 2026-07-22, 아래 §2·§6 수치는 캐시 재산출로 검증됨

## Summary

딥시크 아규먼트 추출 스키마를 **"하나의 `arguments[]` + `kind` 판별자"(현행 canonical-1.0 = 실험의 S1)** 에서 **7축 타입분리 스키마(실험의 S4 계열 + 4개 교정)** 로 옮긴다. 결정 근거는 4-스키마 베이크오프 + 다중-바인딩 프로브의 실측이다:

- **타입안전·조인·스레딩에서 타입분리(S4)가 flat-merged(S1)를 이긴다** — union-dispatch 0%, 개체↔수량 오염 0건, 단계 포착 81%(진짜-딜)·포착시 정확도 85%. flat-merged는 dispatch 100%·모호역할 3건이고 단계 슬롯이 없어 스레딩 구조적 불가.
- **4개 교정으로 S4의 약점을 제거한다**: ① `value` 산술 LLM→**코드**(원문 span만 LLM) ② `basis` 미명시=**UNKNOWN**(날조 금지) ③ 스레드 키 = **정규화 entity_id**(결정성 75%→≥90%) ④ **`group` 바인딩 키**로 다중-필러 (개체↔값) 짝 보존.
- **DART 연결은 금액매칭이 결정론(계약 9%, 중앙값 19배 큰 계약), 날짜근접은 모호**. 공시 없으면 값을 **UNRESOLVED로 명시**(에이전트 24/24 날조 거부 검증). 각 measure에 `value_source ∈ {PARSED, DART, UNRESOLVED}` 출처를 실어 조인·탐색이 모호성 없이 가능.
- **한 사건에 여러 제품·기간·단위가 오는 다중-바인딩은 flat 배열로 표현 불가**(단사성 위반, 프로덕션 11.6%에 실재). `group` 키로 라인아이템을 묶어 해결 — 실측 바인딩 정확도 **flat 31% → group 94%**(§3.1).

원칙(추론계약 v3 승계): **LLM은 내용(원문 span·범주 선택)을 채우고, 코드가 형식·산술·검증·조인·스레딩을 소유한다.**

---

## 1. 문제를 측정가능하게 (요구 → P0–P6 지표)

"좋은 스키마"를 취향이 아니라 **통과/실패가 갈리는 지표**로 정의한다. 지표는 두 계열이다:
- **운영 지표 P0–P5** — 기계가 파싱·조인·스레드·연결·처리할 수 있는가. v2 실패(novelty UNKNOWN 76%·completeness 37.5%)에서 역산한 **필요조건**이나, 각 항목은 대리지표다: `type_agree`는 라벨 일치일 뿐 의미 정합이 아니고, `key_determinism`은 안정성일 뿐 정확성이 아니다 → **필요하나 불충분**.
- **의미 지표 P6(해석 명료성)** — 구조만 보고 원 사건을 **유일·정확**하게 복원할 수 있는가. Q1의 "왜 이 정의인가"에 대한 답: 스키마는 사건의 *부호화*이고, 명료성 = 그 부호화가 **단사(injective, 무손실) + 건전(sound, 근거보유)** 이라는 형식 속성이다. 복호가 유일할 필요충분조건이 단사성이므로 P6는 임의 지표가 아니라 정의 자체다.

| # | 요구(원문) | 지표 | 임계(PASS) | 측정법 |
|---|---|---|---|---|
| **P0** | 다양한 타입에서 **파싱 쉬움** | `raw_json_ok`, `type_valid` | 100% JSON · ≥99% type∈메뉴 | 배치 JSON 파싱률 + 타입 enum 검사 |
| **P1** | 아규먼트 **정규화·타입안전** | `union_dispatch=0`, 개체↔수량 오염, 술어 어휘 | dispatch 0 · 오염 ≤2 · 술어 ≤1.5/타입 | 슬롯별 kind-분기 필요수, 교차오염 카운트, distinct predicate |
| **P2** | **조인** 용이 | `key_determinism`, `key_nonnull` | ≥90% · 100% | 재실행 키 동일률(temp0), 키 비-null률 |
| **P3** | **스레드 연결** 명료 | 단계 포착률·포착시 정확도(진짜-딜) | ≥80% · ≥80% | pro-gold 대비 단계 포착/정확 |
| **P4** | **DART·타 소스 연결** | 결정론 링크율 + 출처 정직 | 링크 명시 100% | 금액매칭 결정론 vs 모호 분리, `value_source` 채움률 |
| **P5** | **추출 효율** | 아이템당 completion 토큰, 1-콜 | 예산 내 · 단일 콜 | usage.completion_tokens/n |
| **P6** | 타입·사건 **해석 명료성** | 단사성(충돌률)·바인딩 정확도·역할 특정성·왕복복원 | 충돌 0 · 바인딩 ≥90% · generic역할 ≤5% | §1.1 |

### 1.1 해석 명료성(P6)을 어떻게 재는가 — Q2 응답

명료성 = "구조 → 사건" 복호가 **유일·정확**함. 네 성분으로 측정한다:

- **P6a 단사성(구조 가역성)** — 서로 다른 실사건이 서로 다른 구조로 가야 함. 측정 = 충돌률: 골드 사건에서 바인딩만 뒤바꾼 변형이 구조상 구별되는가. **결정론·API 불필요.** 현행 flat 배열은 역할 반복 시 `flat(A)==flat(B)`로 붕괴 → 단사성 위반(§3.1 증명).
- **P6b 바인딩 정확도** — 다중-필러 사건에서 (개체↔값↔단위↔기간) 짝이 골드와 일치하는가. **실측(§3.1): flat 31% vs group 94%.**
- **P6c 역할 특정성** — measure/participant 역할이 온톨로지 특정역할인가 generic("value"/"amount")인가. generic은 해석 불가(`diag.py` 카운트).
- **P6d 왕복복원** — 독립 심판이 구조만 받아 사건 재서술 → 원문과 의미동치율(`adjudicate.py`류 pro-judge).

부가 불변식(에이전트 탐색 신뢰):
- **정직성**: 미해결 값 추정·날조 0건(`resolvable=false` 명시).
- **감사성**: 모든 개체·수량은 원문 verbatim span 보유(스팬 검증 통과).

---

## 2. 베이크오프 — 무엇을 재고 무엇이 이겼나

동일 60뉴스·동일 온톨로지 다이제스트·temp0로 4개 후보를 붙였다 (`experiment/run_extract.py`).

| 후보 | 형태 | 한 줄 특징 |
|---|---|---|
| S1 flat-merged | `arguments[{role,kind,value,unit}]` + subject/object_roles | **현행 canonical-1.0과 동형**. 개체·수량을 한 배열에 담고 `kind`로 분기 |
| S2 triple | `triples[{subject,predicate,object}]` 자유술어 | 술어 통제 없음 |
| S3 wide-flat | `subject_id/object_id/value/unit` 고정 슬롯 | 완전 평면, 다중참가자·다중수량 표현 불가 |
| **S4 typed-axis** | `type / predicate / lifecycle.stage / participants[] / measures[]` | **5축 타입분리** (채택안의 뼈대) |

**실측 (`scored.json`, 재계산 검증):**

| 지표 | S1 | S2 | S3 | **S4** |
|---|---|---|---|---|
| P0 raw_json_ok | 6/6 | 6/6 | 6/6 | 6/6 |
| P0 type_agree_v3 | 76.7% | — | 75.0% | **81.7%** |
| P1 union-dispatch | 100% | 100% | — | **0%** |
| P1 모호/오염 | 모호역할 3, val오류 1 | 자유술어 **45종** | — | 개체오염 0·수량오염 2 |
| P2 key_determinism | **90.0%** | 70.0% | 75.0% | 75.0% |
| P2 key_nonnull | 100% | 100% | 100% | 100% |
| P3 단계 포착(marked) | 0%(슬롯無) | 0% | 60.9% | **69.6%** |
| P3 단계 정확·포착시(진짜-딜) | — | — | 82% | **85%** |
| P5 completion tok(합) | 7978 | 4233 | 5948 | 9534 |

**판정:** S4가 P0·P1·P3에서 우위. 지는 두 축은 교정 가능한 결함이다:
- **P2 결정성 75%**: 근본원인은 스레드 키를 LLM verbatim mention으로 만들어 표기변이("2분기"/"상반기")가 키를 가름(v3 알려진 스레드 파편화, KDDX 26건→11스레드). → **키를 정규화 entity_id로** 만들면 LLM 계약 변경 없이 결정성 상승(§7).
- **P5 토큰 9534(최고)**: S4가 `value` 숫자까지 LLM에 시켰기 때문. → **value를 코드로** 이관하면 LLM은 span 문자열만 내므로 토큰 감소 + 산술오류 제거(§3-①, §4).
- **P3 단계 raw 56.5%의 착시**: 오라클(제목 정규식)이 23 marked 중 7건을 비-딜로 오탐. pro-gold 진짜-딜 16건 기준으로 재계산하면 포착 81%·포착시 정확 85%(제목-오라클 자체는 진짜-딜에서 94% 정확 → 56.5%는 오라클 잡음, 스키마 결함 아님).

---

## 3. 결정 — 7축 타입분리 추출 계약 (LLM 출력)

딥시크가 **콜당** 내는 스키마. 배치는 §B3(v3)대로 event_type 동질 그룹.

```json
{"items": [{
  "i": 0,
  "type": "COMPANY.CONTRACT.SIGNING",
  "predicate": "SIGN",
  "trigger": "원유운반선 2척을 2734억원에 계약했다",
  "stage": "DEFINITIVE_SIGNED",
  "participants": [
    {"role": "SUPPLIER",        "slot": "subject", "mention": "한화오션",      "group": 0},
    {"role": "CONTRACT_OBJECT", "slot": "object",  "mention": "원유운반선 2척", "group": 1}
  ],
  "measures": [
    {"role": "CONTRACT_VALUE", "surface": "2734억원", "basis": "UNKNOWN", "group": 1}
  ],
  "confidence": "H"
}]}
```

**7개 축과 규칙 (participants·measures를 제외한 각 축은 정확히 한 종류의 값):**

1. **`type`** — 타입 메뉴(`ontology_ref.txt` 53종)에서만. 게이트/상위 판정이 확정하면 그대로, 미확정이면 콜에서 선택.
2. **`predicate`** — 그 타입의 `pred:` 메뉴에서만(통제 어휘). **현행 `deferred` 폐지 — 채운다.** S2 자유술어 45종 폭발이 근거. 시장방향/감성 술어 금지(온톨로지-rigor §02 §5).
3. **`trigger`** — type·predicate 판정 근거가 된 **원문 verbatim span**. 감사·스팬검증용.
4. **`stage`** — 단계축에서. DEAL: `RUMORED < PROPOSED < PREFERRED_BIDDER < MOU_LOI < DEFINITIVE_SIGNED < EFFECTIVE < CLOSED`, 종결 `CANCELLED`. `stage_sensitive=false` 타입은 `null`. 스레딩의 필수 입력(§7).
5. **`participants[]`** — **개체값 역할만.** `{role, slot(subject|object), mention, group}`. `mention` 원문 복사. 다중참가자 허용(배열). 수량 역할 진입 금지.
6. **`measures[]`** — **수량값 역할만.** `{role, surface, basis, group}`. `surface` 원문 복사. `basis ∈ {TOTAL, ANNUAL, UNKNOWN}` — 원문에 총액/연간 명시 없으면 반드시 UNKNOWN. `value`/`unit`은 **LLM이 내지 않음**(코드 산출, §4). `unit`은 measure마다 개별(혼합 단위 허용).
7. **`group`(바인딩 키)** — 정수. **같은 group = 하나의 라인아이템**(서로 귀속되는 개체·수량 묶음). `group:0` = 이벤트 전역(공유 역할, 예 SUPPLIER). 한 사건에 (제품·기간·금액·단위) 튜플이 여럿이면 각 튜플에 서로 다른 group을 매긴다 → 바인딩 소실 방지(§3.1).

**S4 대비 4개 교정 (실측 결함 제거):**

| 교정 | 근거 | 효과 |
|---|---|---|
| ① `value` LLM→코드 | `classify.py`: LLM 산술 오류, 결정론 파서(`parser_test.py` 개선판: 조/억/만/천·USD무환산·범위flag) 정확 | 산술오류 0·토큰↓·결정론 |
| ② `basis` 기본 UNKNOWN | `gold.json`: measure 7건 전부 `basis_stated=false`; LLM이 TOTAL 채운 건 날조 | 정직성 불변식 충족 |
| ③ 스레드 키 = entity_id | P2 75%의 근본원인이 verbatim 키 | 결정성↑(§7) |
| ④ `group` 바인딩 키 | flat 배열은 역할 반복 시 (개체↔값) 짝 소실(§3.1 증명·실측 flat 31%) | 바인딩 94%·단사성 회복 |

메뉴에 없는 중요 수치는 v3대로 `role:"NEW:<제안명>"`으로 수용(드롭 금지) → 리뷰큐(스키마 진화 채널, v3 §3).

### 3.1 다중 바인딩 — 한 사건에 여러 기간·제품·단위 (Q3 응답)

**문제.** 한 계약/사건에 (제품, 기간, 금액, 단위) 조합이 여럿이면 — 예 "삼성전자 61만원·하이닉스 400만원", "빽보이피자 20%·할메가커피 200원"(단위 %/원 혼합), "삼성 140조·SK 100조·셀트리온 2조"(3-way) — **역할만으로는 어느 값이 어느 개체에 붙는지 결정 불가**. 이것이 P6a(단사성) 위반이다.

**빈도(가설 아님).** 프로덕션 v3 전수(39,959 이벤트) 스캔: **역할 중복 이벤트 11.6%** (개체역할 중복 9.1%·수량역할 중복 3.1%). 타입별로 SHARE_BUYBACK 44%·MERGER 43%·STAKE_ACQUISITION 36%·M&A 20%·PARTNERSHIP 14%·CONTRACT.SIGNING 7.4%. 현행 `TARGET_PRICE_CHANGE` 출력은 이미 삼성전자↔목표가와 하이닉스↔목표가를 **분리 배열로 뱉어 바인딩을 잃은 채** 저장 중(라이브 데이터 결함).

**단사성 위반 증명 (결정론·API 불필요).** 서로 다른 두 계약 A{반도체장비:500억/3년, 디스플레이:300억/5년}, B{반도체장비:300억/5년, 디스플레이:500억/3년}을 flat 배열로 부호화하면 정렬 후 `flat(A)==flat(B)` → **동일 구조로 붕괴, 복호 불가**. group 튜플로 부호화하면 `group(A)≠group(B)` → 구별됨(가역).

**교정 = 라인아이템 그룹 키(`group`).** 같은 라인아이템의 개체·수량에 같은 정수를, 공유 역할에 `0`을 매긴다. 배열은 평면 유지(조회 롱테이블에 `group_ord` 컬럼 1개 추가, §8), 단위는 measure마다 개별이라 혼합 단위(%/원/주)가 자연히 보존된다.

```json
{"type":"COMPANY.COMMERCIAL.PRICING_ACTION","predicate":"CHANGE","trigger":"빽보이피자 20%·할메가커피 200원",
 "participants":[{"role":"PRODUCT","slot":"object","mention":"빽보이피자","group":1},
                 {"role":"PRODUCT","slot":"object","mention":"할메가커피","group":2}],
 "measures":[{"role":"PRICE_CHANGE","surface":"20%","basis":"UNKNOWN","group":1},
             {"role":"PRICE_CHANGE","surface":"200원","basis":"UNKNOWN","group":2}]}
```

**실측 (`experiment/mb_probe.py`·`mb_out.json`, 실제 헤드라인 16건: 혼합단위·3-way 포함, deepseek-v4-flash, temp0):**

| 스키마 | 바인딩 exact-match | pair recall | 배열순서 안정성 |
|---|---|---|---|
| **FLAT**(현행 정본 형태) | **31%** (5/16) | 32% (11/34) | 100% |
| **GROUP**(교정안) | **94%** (15/16) | 97% (33/34) | — |

순서는 100% 안정적이므로 flat 실패는 순서 문제가 아니라 **구조가 바인딩을 담지 못함**이다: 모델이 개체 슬롯에 현저개체(SK증권·이마트·최태원)를 채워 삼성전자↔400만원처럼 오결합. GROUP의 유일한 실패 1건도 골드 자체가 논쟁적인 헤드라인. → **P6b 바인딩 정확도로 측정되며, 교정이 31%→94%로 결함을 제거함이 실증됨.**

---

## 4. 분업 계약 — LLM vs 코드 (누가 무엇을 소유)

| 필드 | 소유 | 규칙 |
|---|---|---|
| `type`,`predicate`,`stage`,`participants.role/slot`,`measures.role/basis` | **LLM** | 범주 선택(메뉴 밖 금지) |
| `trigger`,`participants.mention`,`measures.surface` | **LLM** | 원문 verbatim 복사(자르기·정규화 금지) |
| `measures.value`,`measures.unit` | **코드** | `surface`를 결정론 파서로 산술. `unit ∈ {KRW,USD,PCT,COUNT,DAYS}`. `value_source=PARSED`, `parse_flag ∈ {ok,approx_or_range,no_number}` |
| span 검증 | **코드** | `NFKC+공백축약` 후 `norm(mention/surface) ⊂ norm(제목+리드)`. 실패→인자 드롭+completeness 강등 (v3 C1) |
| `participants.entity_id`,`entity_kind` | **코드** | alias_map → `ORG_KR_{ticker}`/`CONCEPT_*`/`COHORT_*`, 미매치→`ENTITY_UNLISTED{name}` 보존 (v3 C2) |
| `event_id`,`thread_id`,`completeness` | **코드** | §5·§7 |
| DART 값 주입 | **코드** | 금액매칭 결정론만 자동, `value_source=DART` (§6) |
| `participants.group`,`measures.group` | **LLM emit + 코드 검증** | 같은 라인아이템=같은 정수, 공유역할=0. 코드가 `group_ord`로 정규화·카디널리티 검증 (§3.1) |

이 분업이 P5(효율)와 정직성을 동시에 만족시킨다: LLM 출력이 짧아지고(숫자 산출 제거), 값·키·조인은 전부 재현 가능.

---

## 5. 정본 이벤트 v1.1 — 타입분리 보존 + 하위호환

현행 `canonical-event-1.0`은 개체·수량을 하나의 `arguments[]`(+`normalized.kind`)로 **병합**하고 `proposition.predicate_id=null`(deferred)이다 — 즉 실험의 S1. P1(타입안전)·P3(스레딩) 근거상 **타입분리를 정본까지 보존**한다.

온톨로지-rigor 변경통제(§02 §11: 의미변경=버전업, 삭제금지)에 따라 **가산적 v1.1**:

```json
{
  "schema_version": "canonical-event-1.1",
  "event_id": "…#0",
  "event_type_id": "COMPANY.CONTRACT.SIGNING",
  "proposition": {"predicate_id": "SIGN", "predicate_source": "llm-extract-v3", "subject_roles": ["SUPPLIER"], "object_roles": ["CONTRACT_OBJECT"]},
  "lifecycle": {"stage": "DEFINITIVE_SIGNED", "stage_source": "llm-extract-v3"},
  "participants": [{"role_id":"SUPPLIER","slot":"subject","mention":{"text":"한화오션"},"normalized":{"kind":"ENTITY","entity_id":"ORG_KR_042660"},"group_ord":0}],
  "measures": [{"role_id":"CONTRACT_VALUE","surface":{"text":"2734억원"},"value":273400000000,"unit":"KRW","basis":"UNKNOWN","value_source":"PARSED","parse_flag":"ok","group_ord":1}],
  "completeness": "complete",
  "confidence": "H"
}
```

변경점(전부 가산):
- `proposition.predicate_id` **채움**(deferred 종료), `object_roles` 채움.
- `lifecycle.stage` **신설**(스레딩 입력).
- `arguments[]` → `participants[]`+`measures[]` **분리**.
- `measures`에 `value_source`·`parse_flag` 출처 신설.
- `participants`·`measures`에 `group_ord` 바인딩 키 신설(다중-필러 가역, §3.1).

**마이그레이션(하위호환):** 전환 1릴리스 동안 조립기가 `arguments[]`를 `participants+measures`에서 **파생 생성**해 병기(canonical-1.0 소비자 = `threading.py`·그래프 투영 무중단). 소비자 이관 후 `arguments[]` 제거. `ontology_version`은 생성시점 보존(rigor §02 §11).

---

## 6. DART·타 소스 연결 (P4)

**결정론 링크 = 금액매칭.** 이벤트의 상장 공급사 `entity_id=ORG_KR_{ticker}` → DART `corp_code` → 단일판매·공급계약 공시. 매칭 규칙 `|dart_value − measures.value| / dart_value < 0.08`.

실측 퍼널 (7월 CONTRACT.SIGNING, `dart_resolved.json` 재계산):

| 구간 | 건수(스캔 1088) | 성질 |
|---|---|---|
| DART 금액매칭 | 101 (9%) · value+years 90 | **결정론** → value/years/basis/revenue_pct `value_source=DART`(권위) |
| DART 날짜근접 | 339 (31%) | **모호** → 자동조인 금지, 리뷰 후보 |
| 뉴스금액만(공시無) | 257 (24%) | value만 `PARSED`, years/basis **UNRESOLVED** |

- 금액매칭 계약 중앙값 **2,849억** vs 뉴스금액만 152억 = **19배** → 결정론이 걸리는 건 큰 계약(경제적 상위). 롱테일 소액은 데이터 자체 모호.
- **정직성 검증(`agent_out.json`)**: v4-pro 에이전트가 공시 없는 24건 전부 `resolvable=false`로 값 날조 거부, DART 있는 36건 전부 `value_source=DART`. → 스키마의 `value_source`/`basis=UNKNOWN` 채널이 실제로 탐색 모호성을 없앰.

**계약:** 각 measure는 `value_source`로 출처를 실어야 하며, 코드는 금액매칭에서만 DART를 자동 주입한다. 날짜근접·공시無는 절대 값을 지어내지 않는다.

---

## 7. 스레드 연결 (P3) — 키를 entity_id로

스레딩은 `news_thread_contract`대로 `thread_key = event_type_id + identity_roles의 정체값`. 현행은 정체값이 **verbatim mention**이라 표기변이가 스레드를 가른다(P2 결정성 75%의 근본원인, v3 스레드 파편화).

**교정:** identity 역할의 정체값을 **코드가 정규화한 값**으로 만든다 —
- 상장사(`ORG_KR_{ticker}`)는 티커로(결정론 완료),
- 미상장/제품/기간은 `norm()`(NFKC+공백축약) 후 정규화 문자열로(차기: 클러스터 정규화),
- identity 결핍 시 강제연결 금지, `UNKNOWN` 링크만(`missing_identity_policy`).

`stage`(§3-4)가 채워지므로 `novelty_status`(FIRST_IN_THREAD/FOLLOW_UP_STAGE/CORRECTION/…)와 단계 순서 판정이 명료해진다. 이것이 S1/S2 대비 S4의 구조적 우위(단계 슬롯 부재 시 스레딩 불가)를 실현.

---

## 8. 에이전트 조회 뷰 — 평면 읽기모델

정본(§5)은 쓰기모델(중첩). 에이전트 조회·조인은 **평면 뷰**로 제공한다(코드가 정본에서 파생).

```
event_fact(event_id PK, event_type_id, family, predicate_id, stage, thread_id,
           published_at, confidence, completeness,
           subject_entity_id, subject_role)                 -- 이벤트당 1행
event_participant(event_id, role_id, slot, mention, entity_id, entity_kind, group_ord)  -- 개체 롱테이블
event_measure(event_id, role_id, value, unit, basis, value_source, surface, group_ord)  -- 수량 롱테이블
event_thread(thread_id PK, event_type_id, thread_key, current_stage, opened_at)
```

전형 조인(모호성 없음):
- **크기순 계약 탐색**: `event_measure ⋈ event_fact` where role_id='CONTRACT_VALUE' order by value — `value_source`로 DART/PARSED 신뢰도 필터.
- **주체 붙이기**: `event_fact ⋈ event_participant(slot='subject') ⋈ prices/시총 on entity_id`.
- **스레드 타임라인**: `event_fact where thread_id=? order by published_at` — stage 전이 관찰.
- **DART 대조**: `event_measure ⋈ dart_filings on (entity_id, |value−dart_value|/dart_value<0.08)`.
- **라인아이템 복원(다중 제품·단위)**: `event_participant ⋈ event_measure USING (event_id, group_ord)` — group_ord로 (제품↔가격↔단위)가 정확히 묶임. `GROUP BY event_id, group_ord`로 튜플 재구성.

타입분리 덕에 조인이 kind-분기 없이 정적 타입으로 걸린다(P1 → P2 조인 용이의 직접 귀결).

---

## 9. 수용 기준 (사전선언 — 미달 축 재검토)

채택안(7축 + 4교정)이 아래를 통과해야 "완료".

1. **P0** JSON 100% · type_valid ≥99% — S4 실측 6/6·100%.
2. **P1** union-dispatch **0** · 개체↔수량 오염 ≤2 · predicate distinct ≤ 1.5×타입수 — S4 0%·오염 2·통제어휘 **PASS**.
3. **P2** key_determinism ≥90% — 현 75%, **entity_id 키(§7) 적용 후 재측정**이 게이트.
4. **P3** 단계 포착 ≥80% · 포착시 정확 ≥80%(진짜-딜) — 81%·85% **PASS**.
5. **P4** 링크 출처 100% 명시 · 금액매칭만 자동 DART — 결정론 9%·모호 분리 **설계 충족**, `value_source` 전건 채움이 게이트.
6. **P5** 아이템당 토큰 ≤ 현행 S4(9534/60) — value 코드이관으로 감소 예상, 재측정 게이트.
7. **정직성** 미해결 값 날조 0 — 에이전트 24/24 **PASS**.
8. **P6** 단사성 충돌 0 · 바인딩 ≥90% · generic역할 ≤5% — 단사성 **설계 충족**(§3.1 증명), 바인딩 **94% PASS**(실측), 역할 특정성은 프롬프트에 qty메뉴 강제 후 재측정 게이트.

---

## 10. 경계 (Always / Ask first / Never)

- **Always**: 개체·수량 원문 verbatim span 보유 + 스팬검증 통과 · `value`는 코드 산술 · `basis` 미명시=UNKNOWN · DART 자동주입은 금액매칭만.
- **Ask first**: 정본 `arguments[]` 완전 제거(하위호환 종료 시점) · 새 술어/역할/타입 추가(rigor 타입 하나씩·불변식 통과 후) · DART 매칭 임계(0.08) 변경.
- **Never**: 개체·수량을 한 배열로 재병합(union-dispatch 회귀) · 자유술어 허용 · 미해결 값 추정/환산/날조 · score/impact를 스키마에 저장(rigor 금지).

---

## 근거 / 출처

| 구분 | 경로 |
|---|---|
| 베이크오프 러너·스코어 | `experiment/run_extract.py`, `score.py`, `scored.json` |
| pro-gold 심판(단계·금액) | `experiment/adjudicate.py`, `gold.json` |
| 결함 귀속(basis·value·count) | `experiment/classify.py`, `diag.py` |
| 결정론 금액 파서 | `experiment/parser_test.py`, `norm_contracts.py` |
| DART 해결 퍼널 | `experiment/dart_resolve.py`, `decide.py`, `decide2.py`, `dart_resolved.json` |
| 에이전트 정직성 | `experiment/agent_verify.py`, `agent_out.json` |
| 다중-바인딩 프로브(P6b) | `experiment/mb_probe.py`, `mb_gold.json`, `mb_out.json` (flat 31% vs group 94%) |
| 역할중복 빈도 스캔 | v3 전수 39,959 이벤트 → 중복 11.6% (재현: `news_events_2026-06_07_v3.jsonl` role_id 카운트) |
| 현행 추출/조립 | `ops/collect/teacher_extract_v3.py`, `assemble_v3.py` |
| 상위 파이프라인 계약 | `docs/engineering/specs/data/news-normalization-v3.md` |
| 온톨로지 SSOT | `src/alphamale/events/ontology/resources/{ontology_ref.txt, event_type_profiles_v0_1.json, feature_specs_*, news_thread_contract_v0_1.yaml, entity_mapping_contract_v0_1.yaml}` |
