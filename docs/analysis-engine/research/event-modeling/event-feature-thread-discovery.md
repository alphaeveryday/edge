---
doc_type: design
status: Draft
owner: event-research
created: 2026-07-08
updated: 2026-07-11
related:
  - STATE.md
  - event-ontology.md
  - ../../engineering/specs/data/thread-types.md
---
# 이벤트 피처·스레드 탐색 설계

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

## Summary

이 문서는 이벤트 피처 산출용 **discovery substrate**를 구현 전에 정렬하는 설계안이다. 핵심 목표는 `available_at` 기준으로 이벤트별 상태변수와 thread 계보 정보를 안정적으로 수집·정규화해 **최종 impact combiner 직전**까지 제공하는 것이다. 범위 안에는 PIT/no-lookahead, UNKNOWN 처리, feature spec registry 연계, thread identity/lifecycle/DAG/dedup/SCD2, 전이 확률 추정용 관측 구조, discovery table 정의가 포함되고, 범위 밖에는 중간 impact score 설계, 시장반응 outcome 결합, 그래프 최종 액션 튜닝이 포함된다.

## 입출력 테이블 맵
> 아래 logical table/artifact 이름은 설계상 requirement-level 이름이다. 현재 물리 저장이 명시되지 않은 곳은 `[INFERENCE]`로 표기한다.

| ERD 구간 | 필요한 입력 테이블/아티팩트 | 최종 출력 마트/브리지 | 중간 산출(최종 아님) | 상태/owner |
|---|---|---|---|---|
| Lineage bridge boundary | `news_events.payload(JSON canonical event)` raw input requirement + `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml` + logical `event_lineage` prior edges [INFERENCE] | logical `event_lineage` [INFERENCE] | in-memory `event_card_slice`, `feature_spec_slice`, `pre_thread_qualifiers`, candidate edge rows [INFERENCE] | logical bridge owner; dedup cluster와 별도 계약 |
| Thread substrate boundary | logical `event_lineage` [INFERENCE] + dedup/origin refs + as-of cut artifact [INFERENCE] | logical `thread_history_scd2` + derived `thread_id` [INFERENCE] + logical `thread_transition_observation` [INFERENCE] | connected-component / lifecycle state derivation, censoring/UNKNOWN observation state [INFERENCE] | logical owner; thread/current boundary |
| Discovery mart boundary | `news_events.payload(JSON canonical event)` raw input requirement + `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml` + as-of `entity_state`/`expectation`/`context` snapshot artifacts [INFERENCE] + logical `thread_history_scd2`/`thread_transition_observation` [INFERENCE] | logical `event_feature_discovery` [INFERENCE] | row assembly / feature hydration artifacts [INFERENCE] | downstream handoff current |
| Final impact boundary | logical `event_feature_discovery` [INFERENCE] | `final_impact_input` [INFERENCE] | final impact output artifact [INFERENCE] | boundary only; 직접 impact mart owner 아님 |

## 근거/출처

이 문서의 owner spec, registry, 검증 하네스, 현재 저장소 근거는 아래에 모은다.

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 레퍼런스 spec | `docs/archive/events/news-event-integration-spec.md` | historical owner spec. current 구현 경계는 이 문서와 `event-ontology.md`가 소유한다. |
| 현재 코드/데이터 근거 | `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml` | feature registry SSOT와 `thread_identity` 확장 논의의 기준 |
| 현재 코드/데이터 근거 | `tests/events/test_feature_specs.py` | registry validator가 이미 강제하는 최소 계약 확인 |
| 현재 코드/데이터 근거 | `src/alphamale/events/benchmarks/exploration.py` | 현재 pipeline의 discovery gap과 bench 흐름 확인 |

## Problem

지금은 이벤트 카드와 feature registry는 있으나, **thread를 PIT로 추적하며 feature를 발견·집계하는 공통 substrate**는 명시적으로 분리되어 있지 않다. 그 결과 다음 문제가 남아 있다.

1. 동일 사건의 후속 기사·정정·승인·종결을 하나의 thread로 묶는 기준이 불명확하다.
2. `UNKNOWN`이어야 할 값을 0처럼 취급할 위험이 있어 발견 편향이 생길 수 있다.
3. type별 raw/state/thread feature와 최종 impact 결합 경계가 흐려지면 중간 점수 난립으로 이어진다.
4. 전이 확률을 나중에 학습하려 해도, censoring과 미관측을 구분하는 관측 구조가 없다.

## Goals

| 목표 | 설명 |
|---|---|
| PIT 보존 | 모든 feature는 `available_at` 시점에서만 계산 가능해야 한다. |
| UNKNOWN 정직성 | 값 없음은 0이 아니라 `UNKNOWN`으로 남긴다. |
| Registry 우선 | 타입별 feature 정의의 단일 진실원천은 `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`이다. |
| Thread substrate 확립 | identity, lifecycle, dedup, SCD2 history를 공통 구조로 분리한다. |
| 전이 관측 가능화 | stage 전이 확률 추정에 필요한 관측/censoring 구조를 남긴다. |
| Combiner 경계 고정 | feature 산출과 최종 impact combiner를 명확히 분리한다. |

## Non-goals

| 비목표 | 제외 이유 |
|---|---|
| 중간 impact score 생성 | registry 원칙상 feature는 상태변수이며 중간 합성치는 금지다. |
| realized return, CAR, next-stage outcome를 feature로 저장 | PIT 위반이며 outcome join 컬럼의 영역이다. |
| 전체 feature registry를 문서에 재복제 | machine-readable 상세는 YAML이 기준이다. |
| 그래프 projection 정책 세부 확정 | 본 문서는 discovery substrate 정렬이 목적이며 최종 projection 튜닝은 후속 범위다. |
| 시장반응 모델/최적화 | 최종 impact combiner 이후의 문제다. |

## Core Principles

1. **PIT / no-lookahead**: 시간 기준은 `available_at`이며, alias/master/history는 `effective_from <= available_at`만 사용한다.
2. **UNKNOWN-not-zero**: 미관측, 비적용, 아직 미도달은 서로 다른 상태로 남긴다.
3. **Registry-first**: 공통 블록 + 타입 특화 블록 + derived lineage는 registry에서 선언하고 substrate는 이를 실행한다.
4. **Thread-first lineage**: 개별 기사 이벤트와 장기 사건 thread를 분리한다. dedup cluster와 thread는 같은 개념이 아니다.
5. **SCD2 for evolving state**: thread와 entity의 상태 이력은 `valid_from`/`valid_to`로 보존한다.
6. **Single combiner boundary**: 방향·크기·완결성·선반영·리스크 신호는 feature로 남기고, 결합은 마지막 한 지점에서만 한다.

## Proposed Design

### 1. Logical Components

| 컴포넌트 | 책임 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 |
|---|---|---|---|
| Event Card Loader | canonical event와 `available_at`를 event-card 관측치로 정리 | `news_events.payload(JSON canonical event)` raw input requirement + origin/dedup artifact | in-memory `event_card_slice` artifact [INFERENCE] |
| Feature Spec Registry | 타입별 선언 조회. `thread_identity`도 여기서 선언 | `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml` | in-memory `feature_spec_slice` artifact [INFERENCE] |
| Pre-thread Qualifier Extractor | thread key에 필요한 role/event-card/raw attr만 먼저 채움 | `event_card_slice` + `feature_spec_slice.thread_identity.qualifiers` | `pre_thread_qualifiers` artifact [INFERENCE] |
| Thread Resolver | registry의 `thread_identity`와 pre-thread qualifier로 동일 사건 후보를 결정하고 `event_lineage` edge를 제안 | `pre_thread_qualifiers` + logical `event_lineage` prior edges [INFERENCE] | logical `event_lineage` candidate edges [INFERENCE] |
| Thread History Store | canonical `event_lineage`를 저장하고 connected component에서 `thread_id`를 파생 | logical `event_lineage` + stage 변화 + corrections + dedup refs | logical `thread_history_scd2` + derived `thread_id` [INFERENCE] |
| Transition Observer | stage 전이 관측/미관측/censoring 기록 | logical `thread_history_scd2` [INFERENCE] + as-of cut artifact | logical `thread_transition_observation` [INFERENCE] |
| Discovery Table Builder | feature·thread·관측 상태를 한 행으로 정리 | `event_card_slice` + as-of snapshot artifacts [INFERENCE] + logical `thread_history_scd2`/`thread_transition_observation` [INFERENCE] + `feature_spec_slice` | logical `event_feature_discovery` [INFERENCE] |
| Final Impact Combiner | discovery table의 feature만 소비해 최종 판단 | logical `event_feature_discovery` [INFERENCE] | `final_impact_input` / final impact output artifact [INFERENCE] |

<details><summary>예시 JSON</summary>

```json
[
  {
    "component": "Event Card Loader",
    "input": {
      "event_id": "evt_20260314_kr_00017",
      "available_at": "2026-03-14T08:31:00Z",
      "dedup_cluster_ref": "dup_20260314_01"
    },
    "output": {
      "event_type_id": "COMPANY.CONTRACT.SIGNING",
      "primary_roles": ["SUPPLIER", "CUSTOMER"],
      "stage": "SIGNED_BINDING"
    }
  },
  {
    "component": "Feature Spec Registry",
    "input": {
      "event_type_id": "COMPANY.CONTRACT.SIGNING"
    },
    "output": {
      "derived": ["annualized_value", "revenue_share"],
      "thread_identity": {
        "qualifiers": ["SUPPLIER", "CUSTOMER", "contract_kind", "CONTRACT_DURATION"]
      }
    }
  },
  {
    "component": "Thread Resolver",
    "input": {
      "event_type_id": "COMPANY.CONTRACT.SIGNING",
      "primary_roles": ["SUPPLIER", "CUSTOMER"],
      "prior_thread": "thr_8f2d7a1c"
    },
    "output": {
      "lineage_edge_kind": "ADVANCES_STAGE",
      "novelty": "FOLLOW_UP"
    }
  },
  {
    "component": "Final Impact Combiner",
    "input": {
      "revenue_share": 0.1333,
      "binding_level": "SIGNED_BINDING",
      "role_in_impact_tags": ["magnitude", "completion", "risk"]
    },
    "output": {
      "supplier_impact_sign": "+",
      "confidence_bucket": "HIGH"
    }
  }
]
```

</details>

### 2. End-to-end Flow

1. 이벤트 카드는 `available_at` 기준으로 ingestion 된다.
2. registry에서 해당 type의 공통/특화 feature 집합과 `thread_identity`를 먼저 읽는다.
3. thread key에 필요한 role, event-card field, raw `quantities`/`event_attrs`만 pre-thread pass에서 먼저 채운다. 이 단계에서 필요한 qualifier가 없으면 `UNKNOWN_MISSING_QUALIFIER`로 남기고 보수적으로 새 후보 thread를 만든다.
4. dedup cluster는 원문 재유통 정리용으로만 사용하고, thread 결정은 **type + primary roles + pre-thread qualifier values**로 별도 수행한다.
5. 전체 quantities/event attrs를 확정하고, entity/expectation/context/thread feature는 as-of 조회로 채운다.
6. 값이 없으면 0으로 대체하지 않고 `UNKNOWN` 상태와 이유를 남긴다.
7. thread lifecycle은 DAG 상 현재 노드와 이전 노드를 기록하고, 종결되지 않은 thread는 right-censored 관측으로 남긴다.
8. discovery table은 이 결과를 row-wise로 정리해 downstream combiner에 제공한다.

### 3. Feature Spec Boundary

registry는 계속 다음만 선언한다.

| 구역 | 의미 | 비고 |
|---|---|---|
| `quantities` | 기사 원문에서 직접 추출되는 수치 사실 | type-specific |
| `event_attrs` | 기사 원문에서 직접 추출되는 상태/분류 값 | type-specific |
| `entity_state` | as-of 시점 엔티티 상태 | 공통 + type-specific |
| `expectation` | as-of 시점 기대/컨센서스 상태 | 공통 + type-specific |
| `context` | as-of 시점 외부/상황 맥락 | 공통 + type-specific |
| `thread` | thread lineage/lifecycle 파생치 | 공통 + type-specific |
| `thread_identity` | thread qualifier, observation window, lifecycle override | registry-owned contract; `event_lineage` 생성 입력 |
| `derived` | 선언된 입력만 쓰는 계산식 | lineage 명시 필수 |
| `direction` | role별 영향 방향의 도메인 규칙 | feature가 아니라 combiner 입력 제약 |
| `discovery_hypotheses` | 나중에 검증할 패턴 가설 | 모델이 아니라 연구 backlog |


| 구역 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | 비고 |
|---|---|---|---|
| `quantities`, `event_attrs` | `event_card_slice` 안의 canonical raw facts | `event_feature_discovery`.Raw extracted [INFERENCE] | pre-thread qualifier source로도 사용 가능 |
| `entity_state` | as-of `entity_state` snapshot artifact [INFERENCE] | `event_feature_discovery`.Entity/context [INFERENCE] | `available_at` 이전만 허용 |
| `expectation` | as-of `expectation` snapshot artifact [INFERENCE] | `event_feature_discovery`.Entity/context [INFERENCE] | consensus/expectation 영역 |
| `context` | as-of `context` snapshot artifact [INFERENCE] | `event_feature_discovery`.Entity/context [INFERENCE] | 외부/상황 맥락 |
| `thread` | logical `thread_history_scd2` / `event_lineage` [INFERENCE] | `event_feature_discovery`.Thread features [INFERENCE] | 계보/신뢰 파생치 |
| `thread_identity` | `feature_spec_slice.thread_identity` | logical `event_lineage` + `thread_history_scd2` seed [INFERENCE] | discovery row에 직접 저장하지 않고 resolver 입력으로 사용 |
| `derived` | registry가 선언한 lineage 입력 필드 + loaded discovery inputs | `event_feature_discovery`.Derived [INFERENCE] | 선언식 lineage 필수 |
| `direction` | registry `direction` 규칙 | `event_feature_discovery.direction_rules_ref` → combiner 입력 [INFERENCE] | 최종 impact 자체는 아님 |
| `discovery_hypotheses` | registry backlog 텍스트 | research backlog artifact | downstream output table 아님 |

<details><summary>예시 JSON</summary>

```json
{
  "quantities": {
    "CONTRACT_VALUE": {
      "basis": "TOTAL",
      "value": 120000000000
    },
    "CONTRACT_DURATION": 1095
  },
  "event_attrs": {
    "binding_level": "SIGNED_BINDING",
    "disclosure_channel": "DART_FILING"
  },
  "entity_state": {
    "revenue_ttm": {
      "SUPPLIER": 300000000000
    },
    "op_margin_ttm": {
      "SUPPLIER": 0.1
    }
  },
  "expectation": {
    "consensus_revenue_growth_1y": {
      "SUPPLIER": 0.08
    }
  },
  "context": {
    "same_counterparty_events_90d": 3
  },
  "thread": {
    "n_prior_events": 1,
    "days_since_prev_stage": 42
  },
  "derived": {
    "annualized_value": 40000000000,
    "revenue_share": 0.1333
  },
  "direction": {
    "SUPPLIER": {
      "sign": "+"
    },
    "CUSTOMER": {
      "sign": "context"
    }
  },
  "discovery_hypotheses": ["MOU→SIGNED 전환율과 잔여 반응"]
}
```

</details>

중요한 경계는 다음과 같다.

- `role_in_impact`는 **분류 태그**이지 점수가 아니다.
- `direction`은 부호 규칙이지 최종 종목 impact 결과가 아니다.
- `derived`는 허용되지만, `impact_score_like_feature`는 허용되지 않는다.

`thread_identity`는 현재 registry/test 계약에 없는 **명시적 확장**이다. 이 문서가 채택되면 YAML 스키마와 `tests/events/test_feature_specs.py`를 함께 확장한다.

| `thread_identity` 하위 필드 | 의미 |
|---|---|
| `qualifiers` | 같은 사건 여부를 가르는 선행 식별 축 |
| `observation_window_days` | type별 전이 관측창 override |
| `lifecycle_edges` | 허용하는 stage 전이/보정 edge 목록 |
| `resolution_edges` | 정정·반박·해소 계열 resolution edge enum |

<details><summary>예시 JSON</summary>

```json
{
  "thread_identity": {
    "qualifiers": ["SUPPLIER", "CUSTOMER", "contract_kind", "CONTRACT_DURATION"],
    "observation_window_days": 365,
    "lifecycle_edges": [
      ["MOU_NON_BINDING", "SIGNED_BINDING", "ADVANCES_STAGE"],
      ["SIGNED_BINDING", "EFFECTIVE", "ADVANCES_STAGE"],
      ["SIGNED_BINDING", "SIGNED_BINDING", "CORRECTS"]
    ],
    "resolution_edges": ["CORRECTS", "DENIES", "RESOLVES"]
  }
}
```

</details>


validator 확장 계약:

- `thread_identity.qualifiers`는 profile role, event-card field, 또는 같은 type의 pre-thread raw feature id(`quantities`/`event_attrs`)만 참조한다. `entity_state`, `expectation`, `context`, `thread`, `derived`는 thread 결정 이후에 계산되므로 qualifier로 금지한다.
- `observation_window_days`는 양의 정수다.
- `lifecycle_edges[*].EDGE_KIND`는 허용 edge enum에 속한다.
- `resolution_edges[*]`는 허용 resolution enum(`CORRECTS`, `DENIES`, `RESOLVES`)에 속한다.
- `thread_identity`가 없으면 기본값은 `qualifiers = primary_roles`, `observation_window_days = 365`, `lifecycle_edges = common DAG`, `resolution_edges = [CORRECTS, DENIES, RESOLVES]`다.

### 4. Thread Identity and Lifecycle

#### 4.1 Identity

thread는 기사나 dedup cluster가 아니라 **같은 경제적 사건의 연속 관측**이다.

- 기본 identity 축: `event_type_id` + registry가 지정한 `primary_roles`
- 타입별 qualifier 축은 `feature_specs_v0_1.yaml`의 `thread_identity.qualifiers`에 선언한다. 예: 계약이면 `SUPPLIER`, `CUSTOMER`, 계약 객체/기간/지역; M&A면 `ACQUIRER`, `TARGET_COMPANY`, deal structure.
- `thread_id`는 별도 진실원천이 아니다. canonical 물리 관계는 기존 spec의 `event_lineage`(Event→Event)이고, `thread_id`는 같은 lineage connected component를 registry version과 함께 안정적으로 해시한 파생 식별자다.
- 정정/반박/후속 승인 기사는 기존 thread에 연결되고, qualifier가 달라 경제적으로 독립이면 새 thread를 만든다.

#### 4.2 Lifecycle DAG

thread lifecycle은 선형 체인보다 DAG로 본다.

- 이유 1: 같은 사건이 `RUMORED → ANNOUNCED → SIGNED_PENDING_APPROVAL → EFFECTIVE`처럼 진행할 수 있다.
- 이유 2: 정정/반박/재개/철회 같은 가지가 생길 수 있다.
- 이유 3: dedup cluster와 lifecycle transition은 시간 폭이 다르다.

실행 원칙:

- stage node는 event card의 `stage`를 따른다.
- edge는 `from_event -> to_event` 관계이며 `lineage_edge_kind`를 가진다: `ADVANCES_STAGE`, `REBROADCAST_OF`, `CORRECTS`, `DENIES`, `RESOLVES`, `SPLITS_FROM`.
- correction/denial은 과거 노드를 in-place 수정하지 않는다. 새 branch node를 append하고, fold 결과로 현재 유효 상태를 계산한다.
- resolution 계열 stage 또는 denial/correction으로 무효화된 주장은 해당 SCD2 row의 `state_valid_to`를 새 node의 `available_at`으로 닫는다.
- novelty는 `FIRST_OBSERVED`, `FOLLOW_UP`, `REBROADCAST`, `CORRECTION`을 유지한다.

#### 4.3 Dedup and SCD2

- dedup cluster: 동일 보도의 재유통/준중복 묶음
- thread: 동일 경제 사건의 계보 묶음
- 둘은 조인되지만 같은 key를 쓰지 않는다.

thread/history 저장은 SCD2를 따른다.

이 필드군은 logical `event_lineage`와 dedup reference를 읽어 logical `thread_history_scd2` [INFERENCE]에 쓰는 requirement row다.

| 필드 | 의미 |
|---|---|
| `thread_id` | `event_lineage` connected component에서 파생한 사건 계보 식별자 |
| `lineage_edge_kind` | `ADVANCES_STAGE`, `REBROADCAST_OF`, `CORRECTS`, `DENIES`, `RESOLVES`, `SPLITS_FROM` |
| `state_valid_from` | 이 상태가 처음 관측된 `available_at` |
| `state_valid_to` | 다음 유효 상태가 관측되기 전까지의 종료 시각 |
| `current_stage` | fold 결과로 계산한 현 시점 stage |
| `parent_thread_id` | 분기/병합이 있으면 상위 계보 |
| `dedup_cluster_ref` | 동일 기사군 참조 |
| `correction_flag` | 반박·정정 branch 존재 여부 |

<details><summary>예시 JSON</summary>

```json
{
  "thread_history_row": {
    "thread_id": "thr_8f2d7a1c",
    "lineage_edge_kind": "ADVANCES_STAGE",
    "state_valid_from": "2026-03-14T08:31:00Z",
    "state_valid_to": "2026-04-25T07:10:00Z",
    "current_stage": "SIGNED_BINDING",
    "parent_thread_id": null,
    "dedup_cluster_ref": "dup_20260314_01",
    "correction_flag": false
  }
}
```

</details>

### 5. Transition Probabilities with Censoring and UNKNOWN

전이 확률은 지금 당장 모델링하지 않더라도, **관측 구조**는 지금 설계해야 한다.

| 항목 | 설계 원칙 |
|---|---|
| 관측 단위 | `thread_id` 기준 stage transition 후보 행 |
| 시작 시점 | 특정 stage가 처음 관측된 `available_at` |
| 종료 시점 | 다음 stage 관측 시점 또는 관측 종료 시점 |
| 기본 관측창 | 전역 기본 365일. registry의 `thread_identity.observation_window_days`가 있으면 type별 override |
| 최소 분모 | `n_at_risk >= 30`이면 추정 가능. 그 미만은 확률값 대신 `UNKNOWN_SPARSE` |
| right censoring | 관측창이 끝나기 전 다음 stage 없이 현재 as-of가 도달한 경우 `CENSORED_OPEN` |
| observed zero | 최소 분모와 관측창을 만족했는데 전이 발생 0회인 경우만 `OBSERVED_ZERO` |
| 경쟁 전이 | 한 stage에서 여러 다음 stage 가능성을 별도 cell로 유지 |

| 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | 비고 |
|---|---|---|
| logical `thread_history_scd2.state_valid_*` + `feature_spec_slice.thread_identity.observation_window_days` + as-of cut artifact [INFERENCE] | logical `thread_transition_observation` [INFERENCE] → `event_feature_discovery` observation flags | 0 대신 `UNKNOWN_*`/`CENSORED_OPEN`/`OBSERVED_ZERO`를 보존 |

권장 표현은 `P(next_stage = B | current_stage = A, feature bucket, as-of cohort)` 형태다. 단, 값이 없을 때는 0을 채우지 않고 아래 셀 상태를 구분한다.

| 셀 상태 | 의미 |
|---|---|
| `OBSERVED_ZERO` | 충분한 분모가 있었고 관측창 내 0회 발생 |
| `UNKNOWN_SPARSE` | 분모가 30 미만이거나 qualifier 누락으로 at-risk 집단을 만들 수 없음 |
| `CENSORED_OPEN` | 아직 thread가 열려 있어 관측창 종료 전 결론 보류 |

<details><summary>예시 JSON</summary>

```json
{
  "known_transition": {
    "thread_id": "thr_8f2d7a1c",
    "from_stage": "MOU_NON_BINDING",
    "candidate_next_stage": "SIGNED_BINDING",
    "stage_started_at": "2026-02-01T09:00:00Z",
    "observed_until": "2026-03-14T08:31:00Z",
    "observation_window_days": 365,
    "n_at_risk": 42,
    "n_transition": 1,
    "cell_state": "KNOWN"
  },
  "observed_zero": {
    "n_at_risk": 42,
    "n_transition": 0,
    "window_closed": true,
    "cell_state": "OBSERVED_ZERO"
  },
  "unknown_sparse": {
    "n_at_risk": 12,
    "cell_state": "UNKNOWN_SPARSE"
  },
  "censored_open": {
    "as_of": "2026-07-08",
    "window_end": "2026-12-31",
    "next_stage": null,
    "cell_state": "CENSORED_OPEN"
  }
}
```

</details>

### 6. Discovery Table

discovery table은 registry 복사본이 아니라, **한 event/thread 관측이 downstream에 넘길 최소 사실 집합**이다.

| 컬럼군 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | 목적 |
|---|---|---|---|
| Identity | `event_card_slice` + logical `thread_history_scd2` [INFERENCE] | `event_feature_discovery.event_id`, `thread_id`, `event_type_id`, `available_at` [INFERENCE] | PIT 기준행 고정 |
| Lifecycle | logical `thread_history_scd2` / `event_lineage` [INFERENCE] | `event_feature_discovery.current_stage`, `prior_stage_max`, `days_since_prev_stage`, `novelty_status` [INFERENCE] | 계보 상태 전달 |
| Raw extracted | `event_card_slice.quantities` / `event_card_slice.event_attrs` | `event_feature_discovery.CONTRACT_VALUE`, `binding_level` 등 [INFERENCE] | 기사 직접 사실 |
| Entity/context | as-of `entity_state` / `expectation` / `context` snapshot artifacts [INFERENCE] | `event_feature_discovery.revenue_ttm[SUPPLIER]`, `op_margin_ttm[SUPPLIER]` 등 [INFERENCE] | 분모/배경 상태 |
| Thread features | logical `thread_history_scd2` / `event_lineage` [INFERENCE] | `event_feature_discovery.n_prior_events`, `prior_outcome_same_actors`, `correction_or_denial_flag` [INFERENCE] | 전이/신뢰 정보 |
| Derived | registry lineage + 이미 로드된 raw/context/thread fields | `event_feature_discovery.annualized_value`, `revenue_share`, `op_impact_share` [INFERENCE] | 선언식 계산 결과 |
| Observation flags | logical `thread_transition_observation` + per-feature source availability [INFERENCE] | `event_feature_discovery.<feature>_state`, `is_censored`, `unknown_reason` [INFERENCE] | `KNOWN`, `UNKNOWN_MISSING_SOURCE`, `UNKNOWN_NOT_APPLICABLE`, `UNKNOWN_SPARSE`, `CENSORED_OPEN`, `OBSERVED_ZERO` |
| Combiner boundary | registry `direction` / `role_in_impact` refs | `event_feature_discovery.direction_rules_ref`, `role_in_impact_tags` [INFERENCE] | 마지막 결합기 메타데이터만 전달 |

<details><summary>예시 JSON</summary>

```json
{
  "event_id": "evt_20260314_kr_00017",
  "thread_id": "thr_8f2d7a1c",
  "event_type_id": "COMPANY.CONTRACT.SIGNING",
  "available_at": "2026-03-14T08:31:00Z",
  "current_stage": "SIGNED_BINDING",
  "prior_stage_max": "MOU_NON_BINDING",
  "days_since_prev_stage": 42,
  "novelty_status": "FOLLOW_UP",
  "CONTRACT_VALUE": {
    "basis": "TOTAL",
    "value": 120000000000
  },
  "CONTRACT_DURATION": 1095,
  "binding_level": "SIGNED_BINDING",
  "revenue_ttm": {
    "SUPPLIER": 300000000000
  },
  "op_margin_ttm": {
    "SUPPLIER": 0.1
  },
  "n_prior_events": 1,
  "prior_outcome_same_actors": "UNKNOWN",
  "correction_or_denial_flag": false,
  "annualized_value": 40000000000,
  "revenue_share": 0.1333,
  "op_impact_share": 0.1333,
  "revenue_share_state": "KNOWN",
  "is_censored": false,
  "unknown_reason": null,
  "direction_rules_ref": "feature_specs_v0_1.yaml#COMPANY.CONTRACT.SIGNING",
  "role_in_impact_tags": ["magnitude", "completion", "risk"]
}
```

</details>

핵심은 discovery table이 **상태변수의 스냅샷 + lineage 메타데이터**까지만 담고, 최종 영향판단은 담지 않는다는 점이다.

### 7. Final Impact Combiner Boundary

최종 impact combiner는 discovery substrate 밖의 별도 경계다.

| 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | combiner 밖 최종 sink | 비고 |
|---|---|---|---|
| logical `event_feature_discovery`의 raw/derived/thread features [INFERENCE] | `final_impact_input` artifact [INFERENCE] | final impact sign/size output artifact [INFERENCE] | substrate의 핵심 handoff |
| logical `event_feature_discovery`의 `UNKNOWN`/censoring flags [INFERENCE] | `final_impact_input` quality flags [INFERENCE] | calibration / 모델 추론 출력 [INFERENCE] | 0 대체 금지 |
| logical `event_feature_discovery.direction_rules_ref` / `role_in_impact_tags` [INFERENCE] | `final_impact_input` combiner metadata [INFERENCE] | cross-feature interaction 최적화 output [INFERENCE] | 방향 규칙은 참조값 |
| logical `thread_transition_observation` [INFERENCE] | `final_impact_input.transition_priors_ref` [INFERENCE] | realized outcome 결합 output [INFERENCE] | 전이 관측은 substrate 밖 활용 |

즉, substrate는 **무엇을 알 수 있는지**를 제공하고, combiner는 **그것을 어떻게 합칠지**를 결정한다.

### 8. Conceptual Example: `COMPANY.CONTRACT.SIGNING`

아래 계약 예시는 모두 설명용이다.

| 층 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | 설명 |
|---|---|---|---|
| Raw quantity | `event_card_slice.quantities` | `event_feature_discovery.CONTRACT_VALUE`, `CONTRACT_DURATION` [INFERENCE] | 기사에서 직접 추출한 수치 |
| Event attrs | `event_card_slice.event_attrs` | `event_feature_discovery.binding_level`, `contract_kind`, `disclosure_channel` [INFERENCE] | 성사도·신뢰도 축 |
| Entity state | as-of `entity_state` snapshot artifact [INFERENCE] | `event_feature_discovery.revenue_ttm`, `op_profit_ttm`, `op_margin_ttm` [INFERENCE] | supplier 분모 |
| Derived | registry lineage + loaded discovery inputs | `event_feature_discovery.annualized_value`, `revenue_share`, `op_impact_share` [INFERENCE] | 선언식 계산 결과 |
| Thread features | logical `thread_history_scd2` / `event_lineage` [INFERENCE] | `event_feature_discovery.n_prior_events`, `correction_or_denial_flag` [INFERENCE] | 기존 MOU에서 본계약으로 진전된 맥락 |
| Transition observation | logical `thread_transition_observation` [INFERENCE] | `event_feature_discovery.revenue_share_state`, `is_censored`, `unknown_reason` [INFERENCE] | 미관측·censoring 유지 |
| Discovery row result | logical `event_feature_discovery` [INFERENCE] | `final_impact_input` artifact [INFERENCE] | combiner 직전 상태 스냅샷 |
| Final combiner | `final_impact_input` artifact [INFERENCE] | final impact output artifact [INFERENCE] | **여기서만** 크기·성사확률·선반영·리스크를 종합 |

<details><summary>예시 JSON</summary>

```json
{
  "event_id": "evt_20260314_kr_00017",
  "thread_id": "thr_8f2d7a1c",
  "event_type_id": "COMPANY.CONTRACT.SIGNING",
  "available_at": "2026-03-14T08:31:00Z",
  "current_stage": "SIGNED_BINDING",
  "prior_stage_max": "MOU_NON_BINDING",
  "days_since_prev_stage": 42,
  "novelty_status": "FOLLOW_UP",
  "CONTRACT_VALUE": {
    "basis": "TOTAL",
    "value": 120000000000
  },
  "CONTRACT_DURATION": 1095,
  "START_LAG_DAYS": 30,
  "binding_level": "SIGNED_BINDING",
  "contract_kind": "NEW",
  "disclosure_channel": "DART_FILING",
  "revenue_ttm": {
    "SUPPLIER": 300000000000
  },
  "op_profit_ttm": {
    "SUPPLIER": 30000000000
  },
  "op_margin_ttm": {
    "SUPPLIER": 0.1
  },
  "n_prior_events": 1,
  "correction_or_denial_flag": false,
  "annualized_value": 40000000000,
  "revenue_share": 0.1333,
  "op_impact_share": 0.1333,
  "revenue_share_state": "KNOWN",
  "is_censored": false,
  "unknown_reason": null,
  "direction_rules_ref": "feature_specs_v0_1.yaml#COMPANY.CONTRACT.SIGNING",
  "role_in_impact_tags": ["magnitude", "completion", "risk"]
}
```

</details>

이 예시에서 `revenue_share`와 `op_impact_share`는 **feature**다. 하지만 “그래서 주가 impact가 +2냐 +5냐”는 substrate가 아니라 최종 combiner의 일이다.

## Alternatives

| 대안 | 장점 | 단점 | 판단 |
|---|---|---|---|
| 기사 단위만 저장하고 thread는 나중에 계산 | 초기 구현이 단순 | 후속보도/정정/censoring 정보가 뒤늦게 꼬임 | 기각 |
| dedup cluster를 thread로 재사용 | 저장 구조가 적음 | 같은 보도 묶음과 경제 사건 계보를 혼동 | 기각 |
| feature 생성 단계에서 중간 impact score까지 같이 계산 | downstream 단순화 | registry 원칙 위반, 디버깅/재학습 어려움 | 기각 |
| registry + thread substrate + final combiner를 분리 | 경계가 명확, 재처리/검증 용이 | 컴포넌트 수가 늘어남 | 채택 |

## Risks

| 리스크 | 설명 | 완화 |
|---|---|---|
| Thread over-merge | 다른 사건을 같은 thread로 묶음 | type + primary roles + qualifier 분리, correction/audit 샘플 유지 |
| Thread under-merge | 같은 사건이 새 thread로 쪼개짐 | prior same-actor/object lookup과 novelty 상태 유지 |
| UNKNOWN 오염 | 미관측이 0으로 흘러감 | discovery table에 unknown flag/reason을 별도 컬럼으로 강제 |
| Stage DAG drift | 타입별 실제 lifecycle이 단순 선형이 아님 | stage node/edge를 관측 기반으로 저장하고 hard-coded chain 회피 |
| Boundary 침범 | feature 단계에서 사실상 impact score 생성 | registry review에서 중간 합성치 금지 규칙 유지 |

## Validation Plan

| 검증 항목 | 무엇을 본다 | 성공 기준 |
|---|---|---|
| PIT 검증 | 모든 feature가 `available_at` 이전 정보만 사용 | no-lookahead 위반 0 |
| Registry 정합 | 기존 type/section/derived/direction 계약 + 신규 `thread_identity` 확장 계약 일치 | 현재 validator와 충돌하지 않고, 후속 `tests/events/test_feature_specs.py` 확장으로 qualifiers/window/lifecycle_edges/resolution_edges 검증 가능 |
| Thread 정합 | 후속보도·정정·승인이 올바른 thread로 연결 | 샘플 리뷰에서 identity/lifecycle 설명 가능 |
| UNKNOWN 정합 | 미관측/0/censored가 분리 저장됨 | 셀 상태가 혼동되지 않음 |
| Boundary 검증 | discovery row에 중간 impact score가 없음 | combiner 이전 출력이 상태변수만 포함 |
| Example sanity | `COMPANY.CONTRACT.SIGNING` 예시가 registry와 모순 없음 | `revenue_share`, `op_impact_share`, thread feature 경계가 명확 |

## Rollout Plan

| 단계 | 내용 | 산출물 |
|---|---|---|
| 1 | registry 기준으로 discovery table 스키마 확정 | feature/thread 관측 컬럼 정의 |
| 2 | thread identity + SCD2 history 계층 구현 | thread lineage store |
| 3 | transition observer와 censoring/UNKNOWN 셀 도입 | 전이 관측 행 |
| 4 | combiner 입력 계약 고정 | discovery-to-combiner interface |

## Deferred Decisions

아래 항목은 substrate 구현 계약을 막지 않는 후속 설계 주제다.

1. final impact combiner가 `direction` 규칙을 hard rule로 쓸지, feature로만 참고할지 후속 설계에서 확정한다.
2. PDF export가 필요해지면 Markdown canonical을 유지한 채 Pandoc/Typst/LaTeX 중 하나를 렌더링 경로로 추가한다.
