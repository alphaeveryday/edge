---
doc_type: design
status: Accepted
owner: event-research
created: 2026-07-08
updated: 2026-07-11
related:
  - STATE.md
  - event-feature-thread-discovery.md
  - golden-data-inference.md
---
# 이벤트 온톨로지 설계

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

## Summary

이 문서는 alphamale 뉴스 이벤트 온톨로지의 현재 패키지 배치와 실행 경계를 한 곳에 정리한다. 계층은 타입 레지스트리 → `TypeSpec`/`Registry` 로더 → 운영 프로필 → feature registry → 런타임 소비자 순이다. 세부 경로와 검증 자산은 문서 끝 `근거/출처`에 모았다.

핵심 상태는 세 가지다. feature registry의 canonical source는 `src/alphamale/events/ontology/resources/` 아래에 있고, root 2타입과 partA~E 22타입은 항상 하나의 24-type merged view로 읽힌다. `thread_identity`는 아직 deferred implementation contract이며, YAML schema, validator, runtime consumer가 함께 갱신되기 전까지 current runtime contract에 포함되지 않는다. 현재 확인한 런타임 소비자는 raw data path가 아니라 패키지 공개 로더를 통해 ontology/profiles를 읽는다.

## Context

이 문서는 `docs/research/event-modeling/` 아래의 current-state design 요약이며, `Accepted` 상태와 canonical frontmatter/section 순서를 유지한다.

패키지 기준 타입 레지스트리는 타입 id, predicate 집합, required role, stage 표기를 담는 최소 계약이다. 운영 의미는 packaged profile이 lifecycle·projection·HQ 활성화 규칙으로 보강하고, bench/gold quality snapshot은 아래 generated block을 authoritative source로 사용한다.

현재 bench/gold quality snapshot은 아래 generated block을 authoritative values로 둔다.

<!-- metrics:start ontology-bench-gold-snapshot -->
**Bench and gold-quality snapshot**

Current generated quality snapshot for ontology-adjacent bench and labeling assets.

_Generated from `data/manifests/events/model_metrics.yaml` via `uv run alphamale events gold metrics-sync render-docs`._

| Metric ID | Snapshot |
|---|---|
| `events.gold.title.en` | written_rows=41920; event_rows=13794; event_type_count_total=14470 |
| `events.gold.title.ko` | labeled_rows=6000; gold_rows=4295; event_rows=1090; review_rows_low_conf=1705 |
<!-- metrics:end -->

## Problem

현재 온톨로지 설계는 타입 최소계약, 운영 프로필, feature registry가 서로 다른 책임을 가지면서도 하나의 패키지 namespace 아래로 수렴해 있다. `alphamale.events.ontology`는 `TypeSpec` 축약 표현을 파싱하고, packaged profile은 lifecycle/projection을 확장하며, packaged feature registry는 도메인 변수 계약을 유지한다.

또한 현재 구현 상태와 설계 의도는 이전 raw-path 시기보다 더 잘 정렬돼 있다. `tests/events/test_feature_specs.py`는 packaged loader가 만든 merged registry view를 기준으로 profile coverage를 검증하고, root 2타입과 partA~E 22타입은 runtime에서 항상 하나의 24-type registry로 합쳐진다.

## Goals

- 현재 이벤트 온톨로지의 계층별 책임과 artifact 경계를 한 문서로 정리한다.
- event type / role / lifecycle / profile 설계를 현재 구현 기준으로 설명한다.
- root + partA~E를 합친 feature registry coverage와 현재 known gap을 명시한다.
- source/spec와 generated output을 구분해 현재 검증 상태를 기록한다.

## Non-goals

- 이 문서는 아직 runtime에 연결되지 않은 feature registry를 이미 실행 계약으로 간주하지 않는다.
- 현재 event-ontology runtime contract는 `thread_identity`를 포함하지 않는다. YAML schema, validator, runtime consumer는 deferred implementation contract가 세 층을 함께 갱신하기 전까지 이를 emit, require, reference하지 않는다.
- 이 문서는 코드, 스키마, 테스트를 수정하지 않는다. 현재 관찰 가능한 상태만 요약한다.

## Current-State Contract
### Source/Sink map

| ERD 구간 | 필요한 입력 테이블/아티팩트 | 최종 출력 마트/브리지 | 중간 산출(최종 아님) | 상태/owner |
|---|---|---|---|---|
| 패키지 ontology source | `src/alphamale/events/ontology/resources/ontology_ref.txt`, `src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json`, `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`, `src/alphamale/events/ontology/resources/feature_specs_parts/partA~E.yaml` | 없음 — 직접 mart owner 아님 | `Registry.types`, `TypeSpec` logical row, `profiles[item]`, merged feature registry logical table | current, logical source |
| epoch/type runtime contract | registry/profiles/feature registry logical tables | `epoch_out/<month>.parquet`의 `event_type`, registry-backed accept/review decision contract | 없음 | current, runtime consumer output |
| canonical/bench runtime contract | registry/profiles/feature registry logical tables | `news_events_*.jsonl`의 canonical event fields(`predicate_id`,`arguments`,`completeness`), `data/manifests/events/exploration_bench_report_v0_1.json`의 bench output contract | per-case bench result logical artifact | current, runtime consumer output |
| ontology draft proposal sink | `src/alphamale/events/ontology/resources/ontology_ref.txt`, label/example title evidence logical artifact, 기존 `data/manifests/events/ontology_drafts.jsonl` | `data/manifests/events/ontology_drafts.jsonl` | 없음 | current, append-only sink |

### 1. Layered ontology architecture

| 계층 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | owner 문서/비고 |
|---|---|---|---|
| 기준 레지스트리 | `src/alphamale/events/ontology/resources/ontology_ref.txt` | `TypeSpec(type_id, predicates, required_roles, note, stage)` logical table | canonical event type 최소계약의 packaged source |
| 파서/로더 | registry 원문 logical line | runtime `Registry` logical view | 공개 runtime contract |
| 운영 프로필 | `src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json` | lifecycle/projection/HQ profile logical table | 24개 운영 profile 집합의 기반 |
| 도메인 변수 레지스트리 | `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`, `feature_specs_parts/partA~E.yaml` | merged feature registry logical table | packaged split authoring + merged loader view |
| 런타임 소비자 | registry/profiles/feature registry logical tables | `src/alphamale/events/benchmarks/exploration.py`의 bench labels, `src/alphamale/events/assembly/assemble.py`의 canonical event fields, `src/alphamale/events/epoch/runner.py`의 registry-backed accept/review decisions | 이 overview는 spec/resource/logical artifact 매핑을 제공하고 단일 physical sink를 주장하지 않음 |

현재 구조의 핵심은 “한 파일이 모든 의미를 갖는 것”이 아니라, 타입 namespace / lifecycle profile / feature contract가 층별로 분리되어 있으면서도 loader/test/runtime 전반에서 하나의 실행 경로로 봉합되었다는 점이다.

### 2. Event-type, role, lifecycle, profile model

#### Event type

이벤트 타입의 최소 표현은 `TypeSpec`이다. 현재 파서가 유지하는 필드는 `type_id`, `predicates`, `required_roles`, `note`, `stage` 다섯 개뿐이다. 따라서 packaged registry는 namespace와 최소 proposition contract의 기준이고, 더 풍부한 운영 의미는 profile 계층으로 올라간다.
아래는 필드 의미를 설명하기 위한 값 예시 JSON이다.

**`TypeSpec` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `type_id` | 정규 이벤트 타입 식별자 |
| `predicates` | 타입을 지지하는 핵심 술어 집합 |
| `required_roles` | canonical event card에 반드시 있어야 하는 역할 |
| `note` | 타입 경계·제외 규칙 메모 |
| `stage` | stage-sensitive 여부를 나타내는 파싱 결과 |

<details>
<summary>예시 JSON</summary>

```json
{
 "type_id": "COMPANY.CONTRACT.SIGNING",
 "predicates": ["SIGN", "ENTER_INTO"],
 "required_roles": ["SUPPLIER", "CONTRACT_OBJECT"],
 "note": null,
 "stage": true
}
```

</details>

#### Role model

역할 설계는 세 층으로 나뉘지만, 이 draft에서는 event type profile을 **machine-readable role presence rule**로 읽는 계약을 제안한다.

1. packaged registry는 `req:`로 required role 최소집합을 정의한다 (`src/alphamale/events/ontology/resources/ontology_ref.txt`).
2. event type profile은 현재 resource surface에서 `required_roles`, `optional_roles`, `identity_roles`를 분리해 담고 있다. 다만 계약 의미는 단순 “optional” 구분이 아니라 다음 rule 집합으로 해석하는 방향을 둔다 (`src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json`).
 - `required_roles` → `class=required`, `on_missing=REVIEW_QUEUE`
 - `optional_roles` → **legacy resource key**이며 계약 의미는 `enrichment_roles`, `on_missing=ALLOW_UNKNOWN_GAP`
 - `identity_roles` → completeness가 아니라 event/thread 식별에 쓰는 별도 semantics
3. feature registry는 타입별 `primary_roles`를 두지만, validator는 이 값이 profile role 집합의 부분집합이어야 한다고 강제한다 (`src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`, `tests/events/test_feature_specs.py`).

즉, role 모델은 “required for canonical event card”와 “passable enrichment for extraction completeness”와 “identity for event binding”과 “primary for valuation state binding”을 구분하는 구조다.

**제안 role presence rule term schema**

| 필드 | 의미 |
|---|---|
| `role_id` | ontology role/metric identifier |
| `class` | `required` 또는 `enrichment` |
| `on_missing` | `REVIEW_QUEUE` 또는 `ALLOW_UNKNOWN_GAP` |
| `gap_label` | audit/quality layer가 남길 gap 라벨 |

**Role 필드별 의미**

| 필드 | 의미 |
|---|---|
| `required_roles` | canonical card 최소 계약. 하나라도 비면 review queue 대상으로 본다 |
| `optional_roles` | **legacy resource key**. 계약 의미는 `enrichment_roles`이며, 부재 값은 UNKNOWN/gap으로 남기고 pass 가능하다 |
| `identity_roles` | 동일 event/thread 식별에 직접 쓰는 역할 |
| `primary_roles` | valuation state를 어느 주체에 바인딩할지 정하는 역할 |

| 부재 상황 | 제안 처리 |
|---|---|
| required role 누락 | canonical 최소계약 미충족으로 review queue |
| enrichment role/metric 누락 (`optional_roles`) | event card는 통과 가능하되 `UNKNOWN/gap`으로 남김 |

<details>
<summary>`COMPANY.CONTRACT.SIGNING` 예시 JSON</summary>

```json
{
 "event_type_id": "COMPANY.CONTRACT.SIGNING",
 "required_roles": ["SUPPLIER", "CONTRACT_OBJECT"],
 "optional_roles": ["CUSTOMER", "CONTRACT_VALUE", "CONTRACT_DURATION", "EFFECTIVE_DATE"],
 "identity_roles": ["SUPPLIER", "CUSTOMER", "CONTRACT_OBJECT"],
 "primary_roles": ["SUPPLIER", "CUSTOMER"],
 "role_presence_rules": [
 {"role_id": "SUPPLIER", "class": "required", "on_missing": "REVIEW_QUEUE", "gap_label": "missing_supplier"},
 {"role_id": "CONTRACT_OBJECT", "class": "required", "on_missing": "REVIEW_QUEUE", "gap_label": "missing_contract_object"},
 {"role_id": "CUSTOMER", "class": "enrichment", "resource_key": "optional_roles", "on_missing": "ALLOW_UNKNOWN_GAP", "gap_label": "customer_unknown"},
 {"role_id": "CONTRACT_VALUE", "class": "enrichment", "resource_key": "optional_roles", "on_missing": "ALLOW_UNKNOWN_GAP", "gap_label": "contract_value_unknown"}
 ]
}
```

</details>

#### Lifecycle and projection

lifecycle은 두 계층에서 표현된다. packaged registry의 `STAGE` 표시는 타입이 stage-sensitive임을 시사하고, `TypeSpec.stage`가 이를 파싱한다. 운영 프로필은 이를 더 구체화해 `lifecycle_model`, `stage_sensitive`, `projection.activation_stages`, `pre_activation_decision`을 정의한다. 예를 들어 `COMPANY.PRODUCT.LAUNCH`는 `PRODUCT_TECH_LIFECYCLE`과 `LAUNCHED → COMMERCIAL_SUPPLY → REVENUE_RECOGNIZED` 활성화 단계를 가진다.

feature registry 쪽에서는 `common_blocks.event_core.stage`와 `common_blocks.thread_state.*`가 lifecycle 관찰을 위한 공통 상태를 제공한다 (`src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`). 즉 lifecycle의 현재 구현은 **type-level stage sensitivity는 profile**, **state snapshot은 feature registry**가 맡는 이중 구조다.

**Lifecycle / 운영 프로필 필드별 의미**

| 필드 | 의미 |
|---|---|
| `STAGE` | registry 원문에서 stage-sensitive 타입임을 표시하는 토큰 |
| `TypeSpec.stage` | 파서가 추출한 stage-sensitive 플래그 |
| `family` | 상위 도메인 계열 |
| `allowed_predicates` | profile이 허용하는 술어 집합 |
| `lifecycle_model` | 타입이 따르는 단계 모델 이름 |
| `stage_sensitive` | profile 차원의 단계 민감도 플래그 |
| `projection.target_relation` | projection이 만들 관계 이름 |
| `projection.activation_stages` | graph projection이 활성화되는 단계 집합 |
| `pre_activation_decision` | 활성화 전 projection 처리 정책 |
| `activate_hq.always` | 항상 활성화하는 HQ 질문 코드 묶음 |

<details>
<summary>예시 JSON</summary>

```json
{
 "STAGE": "COMPANY.PRODUCT.LAUNCH... STAGE",
 "TypeSpec": {
 "stage": true
 },
 "family": "COMPANY",
 "allowed_predicates": ["LAUNCH", "RELEASE", "UNVEIL", "INTRODUCE"],
 "lifecycle_model": "PRODUCT_TECH_LIFECYCLE",
 "stage_sensitive": true,
 "projection": {
 "target_relation": "produces",
 "activation_stages": ["LAUNCHED", "COMMERCIAL_SUPPLY", "REVENUE_RECOGNIZED"],
 "pre_activation_decision": "EVENT_ONLY"
 },
 "activate_hq": {
 "always": ["A1", "A2", "B1"]
 }
}
```

</details>

### 3. Domain-variable registry schema

루트 feature registry 헤더는 타입별 허용 section과 공통 설계 원칙을 직접 선언한다. 원칙은 PIT, NULL 정직성, 결합 금지, lineage 명시다 (`src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`). 타입별 section은 `quantities`, `event_attrs`, `entity_state`, `expectation`, `context`, `thread`, `derived`, `direction`, `discovery_hypotheses`다.

공통 블록은 다음 네 묶음으로 물질화되어 있다 (`src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`).

- `event_core`: stage, source authority, novelty, session timing 같은 이벤트 자체 메타 상태
- `entity_state`: market cap, ADV, revenue, leverage, valuation, sector 등 주체별 PIT 분모
- `expectation_state`: pre-event drift, volume anomaly, short balance, news flow, consensus availability 같은 선반영 상태
- `thread_state`: prior stage, days since prev stage, prior outcome, correction/denial flag 같은 계보 상태

**공통 상태 블록 필드별 의미**

| 필드 | 의미 |
|---|---|
| `event_core.stage` | 현재 관측된 lifecycle 단계 스냅샷 |
| `event_core.source_authority_rank` | 원소스 권위 수준 |
| `entity_state.market_cap` | 주체별 PIT 시가총액 |
| `expectation_state.pre_event_drift_5d` | 이벤트 전 5일 초과수익률 |
| `thread_state.days_since_prev_stage` | 직전 단계 이후 경과일 |

<details>
<summary>예시 JSON</summary>

```json
{
 "event_core": {
 "stage": "SIGNED",
 "source_authority_rank": 5
 },
 "entity_state": {
 "market_cap": 1800000000000
 },
 "expectation_state": {
 "pre_event_drift_5d": 0.034
 },
 "thread_state": {
 "days_since_prev_stage": 42
 }
}
```

</details>

**타입 섹션 / validator 필드별 의미**

| 필드 | 의미 |
|---|---|
| `quantities` | 원문에서 직접 추출하는 수치 묶음 |
| `event_attrs` | 원문에서 추출하는 범주·불리언 묶음 |
| `entity_state` | 주체별 PIT 상태 묶음 |
| `expectation` | 사전 기대·선반영 상태 묶음 |
| `context` | 산업·매크로·레짐 상태 묶음 |
| `thread` | 계보·전이 상태 묶음 |
| `derived` | 공식과 입력으로 계산되는 파생 변수 묶음 |
| `direction` | 주체별 driver/sign 규칙 묶음 |
| `discovery_hypotheses` | 후속 검증용 패턴 가설 목록 |
| `primary_roles` | validator가 profile role 부분집합인지 확인하는 핵심 역할 집합 |

<details>
<summary>예시 JSON</summary>

```json
{
 "quantities": {
 "CONTRACT_VALUE": {
 "dtype": "float"
 }
 },
 "event_attrs": {
 "binding_level": {
 "dtype": "enum"
 }
 },
 "entity_state": {
 "supplier_backlog": {
 "scope": "SUPPLIER",
 "dtype": "float"
 }
 },
 "expectation": {
 "pre_event_drift_5d": {
 "dtype": "float"
 }
 },
 "context": {
 "policy_regime": {
 "dtype": "enum"
 }
 },
 "thread": {
 "days_since_prev_stage": {
 "dtype": "float"
 }
 },
 "derived": {
 "revenue_share": {
 "formula": "annualized_value / revenue_ttm[SUPPLIER]"
 }
 },
 "direction": {
 "SUPPLIER": {
 "driver": "revenue_growth",
 "sign": "+"
 }
 },
 "discovery_hypotheses": ["revenue_share 구간별 |AR| 단조성"],
 "primary_roles": ["SUPPLIER", "CUSTOMER"]
}
```

</details>

draft 문서의 discovery table 정의도 현재 경계 해석에 중요하다. 해당 문서는 downstream으로 넘길 최소 사실 집합을 `Identity`, `Lifecycle`, `Raw extracted`, `Entity/context`, `Thread features`, `Derived`, `Observation flags`, `Combiner boundary`로 제한하고, 최종 impact 판단은 포함하지 않는다고 못 박는다. 이는 root header의 “결합 금지” 원칙과 같은 방향이며, 현재 feature registry가 상태변수 계약에 머물러야 한다는 해석을 보강한다.

**Discovery table contract 필드별 의미**

| 필드 | 의미 |
|---|---|
| `Identity` | downstream이 event/thread를 식별하는 최소 키 묶음 |
| `Lifecycle` | 현재 단계와 전이 맥락 |
| `Raw extracted` | 기사 원문에서 직접 뽑은 값 |
| `Entity/context` | 주체·산업·매크로 배경 상태 |
| `Thread features` | 선행 이벤트/전이 기반 계보 특징 |
| `Derived` | raw/state를 조합해 계산한 파생 값 |
| `Observation flags` | 정정·결측·주의 신호 |
| `Combiner boundary` | downstream combiner에 넘기되 impact 판단은 포함하지 않는 경계 |
**raw input requirement → downstream output bridge (logical ontology tables)**

| raw input requirement | downstream output | bridge |
|---|---|---|
| `Raw extracted` logical table | `Derived` logical fields | feature registry formula와 direction rule이 raw/state를 조합 |
| `Lifecycle`, `Entity/context`, `Thread features` logical tables | `Combiner boundary` logical table | downstream이 식별·상태·계보를 읽되 최종 impact 판단은 여기서 쓰지 않음 |
| registry `required_roles` + profile role/lifecycle semantics | canonical event의 `arguments`, `completeness`, bench의 `projection_decision` | ontology logical tables가 runtime contract field로 투영됨 |

<details>
<summary>예시 JSON</summary>

```json
{
 "Identity": {
 "event_id": "event:company-contract-373220-2026-07-08"
 },
 "Lifecycle": {
 "stage": "SIGNED"
 },
 "Raw extracted": {
 "CONTRACT_VALUE": 120000000000
 },
 "Entity/context": {
 "issuer": "373220",
 "country": "KR"
 },
 "Thread features": {
 "days_since_prev_stage": 42
 },
 "Derived": {
 "revenue_share": 0.083
 },
 "Observation flags": {
 "correction_or_denial_flag": false
 },
 "Combiner boundary": {
 "impact_score": "NOT_INCLUDED"
 }
}
```

</details>

### 4. Coverage and physical split

현재 feature registry의 물리 배치는 다음과 같다.

| 파일 | 도메인 초점 | 타입 수 |
|---|---|---:|
| `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml` | 루트 본체, 공통 블록, 예시 2타입 | 2 |
| `src/alphamale/events/ontology/resources/feature_specs_parts/partA.yaml` | 제품 라이프사이클 | 3 |
| `src/alphamale/events/ontology/resources/feature_specs_parts/partB.yaml` | 실적·자본정책 | 4 |
| `src/alphamale/events/ontology/resources/feature_specs_parts/partC.yaml` | 정책·통상·규제·소송 | 6 |
| `src/alphamale/events/ontology/resources/feature_specs_parts/partD.yaml` | 산업 사이클·공급망 리스크 | 4 |
| `src/alphamale/events/ontology/resources/feature_specs_parts/partE.yaml` | 시장 미시구조·크레딧·매크로 | 5 |

합산하면 root 2 + partA 3 + partB 4 + partC 6 + partD 4 + partE 5 = 24다. 이 값은 root 메타의 `type_count: 24`와 일치하고, 운영 profile 집합도 24개 타입을 전제로 구성돼 있다.

### 5. Current implementation status and known gaps

#### 5.1 Implemented and observable today

- `event_type_profiles_v0_1.json`은 lifecycle/projection/role 확장 프로필로 이미 물질화돼 있다.
- feature registry는 공통 블록과 24개 타입 설계를 split authoring 형태로 보유한다.
- 벤치 리포트와 gold/label 통계는 이미 물질화된 generated output이며, 현재 수치 snapshot은 문서 앞의 generated metrics block을 authoritative source로 둔다.

#### 5.2 Known gaps

- 루트 registry는 24개 전체가 아니라 2개 타입만 가진다.
- `[INFERENCE]` split authoring 자체는 여전히 merge contract drift 위험을 남기지만, validator/test 경로는 이미 root+parts 병합 view를 사용한다.
- `thread_identity`는 deferred implementation contract로만 존재하고, 현재 root schema 헤더와 validator section 목록에는 없다.
- `[INFERENCE]` 현재 확인한 runtime extraction 경로는 feature registry를 직접 사용하지 않는다. `exploration_bench.py`는 bench cases/profiles/rubric/HQ만 로드하고, `assemble_events.py`는 `load_registry()`로 ontology TypeSpec만 읽어 event card를 조립한다.
- generated bench output에는 여전히 `exposure_master`, `graph_neighbors`, `market_observation`, `official_ir_origin_source` 같은 data gap이 남아 있다.

## Alternatives

1. **단일 monolith registry만 유지**
 - 장점: test loader가 단순하고 “한 파일 = 한 계약”이 된다.
 - 단점: 현재 24타입 규모에서도 파일이 빠르게 비대해지며, 도메인별 authoring ownership이 약해진다. 현재 repo는 이미 partA~E로 분할 authoring 쪽으로 이동했다.
2. **split registry + 명시적 merge/load 단계**
 - 장점: 현재 authoring layout을 유지하면서 validator/runtime에 단일 merged view를 줄 수 있다.
 - 단점: merge contract를 별도 관리해야 한다.
 - 현재 상태는 validator/test 경로에서 이미 이 대안으로 정렬돼 있다. `load_ontology_bundle()`가 `load_feature_registry()` merged view를 만들고, `tests/events/test_feature_specs.py`가 그 결과를 바로 검증한다.
3. **운영 semantics를 profiles에만 집중하고 feature registry를 축소**
 - 장점: runtime artifact 수를 줄인다.
 - 단점: `quantities`/`entity_state`/`derived`/`direction`/`discovery_hypotheses` 같은 도메인 변수 계약이 사라져 연구·검증·impact boundary가 약해진다.

## Risks

- **계층 드리프트 위험**: external registry, profile, feature registry가 서로 다른 속도로 변하면 type id / role / lifecycle 설명이 어긋날 수 있다.
- **검증 드리프트 위험**: split authoring과 merged validation 계약이 앞으로 어긋나면 regression이 test에 잡히지 않을 수 있다. 현재는 `load_ontology_bundle()`/`load_feature_registry()`가 이 경계를 봉합하지만, merge 규칙이 바뀌면 다시 확인해야 한다.
- **설계 과대해석 위험**: `thread_identity` deferred implementation contract나 feature registry를 “이미 runtime 반영됨”으로 오해하면, 문서 계약과 구현 경계가 흐려진다.
- **generated metric 과신 위험**: bench pass rate와 gold stats는 진행 신호이지 ontology completeness 증명이 아니다.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 문서 규칙 | `docs/README.md` | canonical design 위치, frontmatter, evidence rule |
| 최소 타입 계약 | `src/alphamale/events/ontology/resources/ontology_ref.txt`<br>`src/alphamale/events/ontology/domain/model.py`<br>`src/alphamale/events/ontology/domain/parser.py`<br>`src/alphamale/events/ontology/registry/model.py`<br>`src/alphamale/events/ontology/registry/loader.py`<br>`src/alphamale/events/ontology/__init__.py` | `TypeSpec`/`Registry` 최소 계약 |
| 운영 프로필 | `src/alphamale/events/ontology/resources/event_type_profiles_v0_1.json` | lifecycle/projection/HQ 운영 semantics |
| feature registry authoring | `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`<br>`src/alphamale/events/ontology/resources/feature_specs_parts/partA.yaml` ~ `partE.yaml` | common block, split authoring, 24-type coverage |
| loader/validator 경계 | `src/alphamale/events/ontology/bundle.py`<br>`src/alphamale/events/ontology/features/loader.py`<br>`tests/events/test_feature_specs.py`<br>`tests/events/test_events_ontology.py` | merged view validation, public API 검증 |
| runtime consumer 경계 | `src/alphamale/events/benchmarks/exploration.py`<br>`src/alphamale/events/assembly/assemble.py`<br>`src/alphamale/events/epoch/runner.py` | current consumer가 ontology/profiles를 읽는 경로 |
| generated snapshot | `data/manifests/events/exploration_bench_report_v0_1.json`<br>`data/manifests/events/gold_title_stats.json`<br>`data/manifests/events/ko_gold_stats.json` | bench/gold snapshot과 남은 data gap |
| deferred/draft 경계 | `docs/research/event-modeling/event-feature-thread-discovery.md` | `thread_identity` deferred boundary, discovery table 범위 |

## Rollout

이 문서는 current-state 요약이므로 아래는 **향후 정렬 순서**로 읽어야 한다.

1. 이 문서는 `docs/research/event-modeling/`의 현재 accepted ontology contract 요약 진입점이다. 위치와 frontmatter는 canonical 문서 위치 규칙을 따른다.
2. split registry는 공식 authoring 형식으로 유지한다. validator와 test는 계속 merged view를 canonical validation input으로 사용하고, runtime consumer는 feature registry를 실제로 읽는 구현이 들어오기 전까지 ontology/profile 경로를 유지한다.
3. `thread_identity`는 deferred implementation contract로 유지한다. current contract 승격은 YAML schema, validator, runtime consumer가 함께 이동하는 같은 변경 단위에서만 허용한다.
4. feature registry를 runtime contract로 승격할 때도 role/lifecycle의 source of truth는 profile에 두고, feature registry는 domain variable과 discovery hypothesis 계약을 맡는 분리를 유지한다.
## Decisions

1. split authoring(`partA~E`)은 공식 방식으로 유지한다. merged artifact/view는 validator와 loader가 소비하는 실행 입력이며, authoring source of truth를 대체하지 않는다.
2. role/lifecycle의 source of truth는 profile이 맡는다. feature registry는 domain variable, `primary_roles`, discovery hypothesis를 소유하며, profile role/lifecycle을 다시 권위 계약으로 중복 선언하지 않는다.
3. `thread_identity`의 qualifier/window/lifecycle override는 deferred implementation contract다. YAML schema, validator, runtime consumer가 함께 이동하기 전에는 current contract로 올리지 않는다.
4. bench report의 data gap은 source family별로 메운다: `exposure_master`는 disclosure/source-authority 계열, `graph_neighbors`는 graph 계열, `market_observation`은 price/market data 계열, `official_ir_origin_source`는 IR/origin 계열 ownership으로 본다.
5. 외부 canonical registry에만 있는 타입의 operationalization 우선순위는 ontology_ref 존재 여부가 아니라 support/review frequency와 HQ value로 정한다. 즉 accepted/review 흐름에서 반복적으로 나타나고 HQ evidence 가치가 큰 타입부터 24-profile 묶음 다음 순서로 편입한다.
