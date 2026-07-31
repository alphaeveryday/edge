---
doc_type: design
status: Draft
owner: event-research
created: 2026-07-08
updated: 2026-07-11
related:
  - STATE.md
  - event-ontology.md
  - golden-data-generation.md
  - golden-data-inference.md
---
# 적응형 리뷰 루프

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

## Summary

이 문서는 현재 저장소에서 직접 관찰되는 적응 진화·리뷰 루프를 세 층으로 정리한다. 첫째, `epoch_runner.py`는 월별 제목 스트림을 oldest-first로 처리하고 review queue와 `_epoch_state.json`을 남긴다. 둘째, 한국어 cold-start 적응은 adaptive candidate → teacher label → gold/review split → `ko_review_queue.jsonl`로 이어진다. 셋째, ontology proposal API와 `exploration_bench.py`가 각각 draft type 기록과 PIT/projection safety를 맡는다. 세부 경로는 문서 끝 `근거/출처`를 참조한다.

핵심 경계도 분명하다. `propose_type()`까지는 구현 근거가 있지만, legacy spec이 말하는 `클러스터 ≥ 30 → ontology_version bump → replay` 자동 루프는 아직 설계 의도다. 반대로 `exploration_bench.py`의 PIT 필터, stage 추론, projection gate, forbidden-promotion 검사는 현재 코드로 직접 확인된다.

## Context

이 문서는 `docs/research/event-modeling/` 아래의 current research design 문서이며, canonical frontmatter/section 순서를 따른다.

adaptive loop의 장기 의도는 legacy spec과 다이어그램에 넓게 퍼져 있다. 본 문서는 그 의도 전체를 현재 구현으로 승격하지 않고, 코드와 산출물로 직접 확인한 review split, state carry-over, draft proposal, projection safety만 current contract로 정리한다.

## Problem

현재 적응 루프는 “무엇이 실제로 구현되었는가”와 “무엇이 spec/diagram 상의 목표 상태인가”가 섞여 보이기 쉽다. 월별 review split과 KO low-confidence split은 코드와 산출물로 직접 확인되지만, `클러스터 ≥ 30` 규칙이나 `gold_store.jsonl` append, `rebuild_news_events.py` replay는 이번 저장소에서 실행 코드보다 legacy spec에 더 강하게 존재한다.

또한 PIT/no-lookahead 안전장치도 층별로 다르다. `exploration_bench.py`는 `published_at < asof`와 projection 금지 규칙을 직접 집행하지만, feature/thread substrate는 아직 draft 문서와 current-state 정리 문서에서만 경계가 설명된다.

## Goals

- 현재 adaptive evolution/review-loop의 **구현 경로**와 **spec-only 경로**를 분리해 설명한다.
- 어떤 상태가 에폭 간 유지되는지, 어떤 행이 review로 가는지, 어떤 행이 자동 수용되는지 명시한다.
- PIT/no-lookahead 보호 장치와 feature/thread draft boundary를 한 곳에 모은다.
- legacy spec과 다이어그램이 현재 코드의 어느 부분을 설명하고, 어느 부분은 아직 목표 상태인지 매핑한다.

## Non-goals

- 이 문서는 `cluster >= 30` ontology bump, `gold_store.jsonl`, `rebuild_news_events.py`를 이미 구현된 파이프라인으로 격상하지 않는다. 해당 항목은 legacy spec의 설계 의도다.
- 이 문서는 `thread_identity`를 현재 runtime contract라고 주장하지 않는다. 이 항목은 schema, validator, runtime consumer가 함께 갱신될 때만 승격되는 deferred implementation contract다.
- 이 문서는 코드, 데이터, 다이어그램을 변경하지 않는다. 관찰 가능한 사실과 `[INFERENCE]`만 정리한다.

## Current State and Proposed Runtime Contract

이 문서에서 artifact는 다음 다섯 등급으로 읽는다.

- **구현 코드**: 현재 실행 규칙을 직접 강제하는 Python 스크립트 (`src/alphamale/events/epoch/runner.py`, `src/alphamale/events/gold/ko.py`, `alphamale.events.ontology`, `src/alphamale/events/benchmarks/exploration.py`).
- **구현 상태 파일**: 현재 런타임이 실제로 남긴 JSON/JSONL 산출물 (`data/interim/events/_epoch_state.json`, `data/interim/events/review_queue.jsonl`, `data/interim/events/ko_adaptive_cands.jsonl`, `data/interim/events/ko_adaptive_labels.jsonl`, `data/interim/events/ko_review_queue.jsonl`).
- **generated outputs**: 구현이 남긴 결과물이며, 파이프라인 존재와 현재 상태는 증명하지만 spec 전체 달성까지 증명하지는 않는다.
- **legacy spec / draft spec**: 목표 동작과 확장안을 설명하지만, 동일 수준의 실행 증거는 아니다.
- **diagram artifacts**: 설계 intent와 루프 모양을 보여주는 draw.io 근거이며, 코드 대응은 별도 확인이 필요하다.
### 입출력 테이블 맵

| ERD 구간 | 필요한 입력 테이블/아티팩트 | 최종 출력 마트/브리지 | 중간 산출(최종 아님) | 상태/owner |
|---|---|---|---|---|
| 월별 epoch review/accept runtime | `data/raw/news/bigkinds/econ_*.parquet` logical month batch, ontology registry logical table, gate/type model outputs | `epoch_out/<month>.parquet`, `data/interim/events/review_queue.jsonl` | `data/interim/events/_epoch_state.json` | current |
| KO adaptive gold/review split | `data/interim/events/ko_adaptive_cands.jsonl`, `data/interim/events/ko_adaptive_labels.jsonl` | `data/fixtures/events/ko_gold_title.jsonl`, `data/interim/events/ko_review_queue.jsonl` | 없음 | current |
| ontology draft proposal sink | `src/alphamale/events/ontology/resources/ontology_ref.txt`, review/example title evidence logical artifact, 기존 `data/manifests/events/ontology_drafts.jsonl` | `data/manifests/events/ontology_drafts.jsonl` | 없음 | current, append-only sink |
| projection/stage audit harness | `data/fixtures/events/exploration_bench/benchmark_cases_v0_1.jsonl`, `data/fixtures/events/exploration_bench/scoring_rubric_v0_1.json`, `data/fixtures/events/exploration_bench/hq_registry_v0_1.json`, packaged profiles logical table | `data/manifests/events/exploration_bench_report_v0_1.json` | per-case bench result logical artifact | current, audit-only |
| review-driven migration/replay | `data/interim/events/review_queue.jsonl`, `data/interim/events/ko_review_queue.jsonl`, review cluster logical table [INFERENCE] | `[INFERENCE] gold_store.jsonl`, `[INFERENCE] ontology version bump state`, `[INFERENCE] replayed epoch output` | `[INFERENCE] news_gate_log`, `[INFERENCE] news_event_audit` | [INFERENCE], spec-only |

### 1. 현재 adaptive surface map

| 적응 표면 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | 현재 동작 | 상태 판정 |
|---|---|---|---|---|
| 시간순 에폭 리뷰 분기 | `data/raw/news/bigkinds/econ_*.parquet` logical month batch, ontology registry logical table, gate/type model outputs | `data/interim/events/_epoch_state.json`, `data/interim/events/review_queue.jsonl`, `epoch_out/<month>.parquet` | 월별 oldest-first 처리, ambiguity/unknown type review 분기, done month 기억 | 구현됨 |
| KO teacher 적응 | `data/interim/events/ko_adaptive_cands.jsonl`, `data/interim/events/ko_adaptive_labels.jsonl` | `data/fixtures/events/ko_gold_title.jsonl`, `data/interim/events/ko_review_queue.jsonl` | 후보 수집, teacher 라벨, low-confidence 분리, gold/review 분기 | 구현됨 |
| 온톨로지 초안 제안 | `src/alphamale/events/ontology/resources/ontology_ref.txt`, 기존 `data/manifests/events/ontology_drafts.jsonl`, example title evidence logical artifact | `data/manifests/events/ontology_drafts.jsonl` | versioned registry load와 append-only draft proposal logging | 현재 런타임 경계: approval/bump/replay는 live runtime 아님 |
| stage/projection safety | `data/fixtures/events/exploration_bench/benchmark_cases_v0_1.jsonl`, `data/fixtures/events/exploration_bench/scoring_rubric_v0_1.json`, `data/fixtures/events/exploration_bench/hq_registry_v0_1.json`, packaged profiles logical table | per-case bench result logical artifact, `data/manifests/events/exploration_bench_report_v0_1.json` | PIT trace, stage inference, projection gating, forbidden promotion audit | 구현됨 |
| review-only growth → ontology bump → replay | `review_queue.jsonl`, `ko_review_queue.jsonl`, review cluster logical table [INFERENCE] | `gold_store.jsonl`, ontology version bump state, replayed epoch output [INFERENCE] | cluster-based 신규 타입 제안, additive bump, replay 재분류 | spec/diagram only |

현재 adaptive-loop 관련 수치는 아래 generated block을 authoritative snapshot으로 삼는다.

<!-- metrics:start adaptive-loop-metrics -->
**Current adaptive-loop metrics**

Current metrics for KO label split, adaptive type-head fidelity, and exploration-loop evidence grounding.

_Generated from `data/manifests/events/model_metrics.yaml` via `uv run alphamale events gold metrics-sync render-docs`._

| Metric ID | Snapshot |
|---|---|
| `events.gold.title.ko` | labeled_rows=6000; gold_rows=4295; event_rows=1090; review_rows_low_conf=1705 |
| `events.ml.type_ko_v2` | teacher_relative_pct=90.91; accuracy=0.9091; macro_f1=0.8987; n_eval=121 |
<!-- metrics:end -->

### 2. 무엇이 적응하는가

현재 코드 기준으로 실제 적응하는 것은 세 가지다.

1. **월별 수용률과 review backlog**: `epoch_runner.py`는 gate 통과 후 type head 결과를 보고 `accepted_rows`와 `review_rows`를 분기한다. [INFERENCE] 따라서 적응의 직접 대상은 “이번 달에 자동 수용 가능한 event-type 판정 비율”이다.
2. **한국어 gold의 범위와 신뢰도**: `ko_adaptive_cands.jsonl`는 아직 라벨되지 않은 능동 샘플 후보를, `ko_adaptive_labels.jsonl`는 teacher 결과를 담는다. `build_ko_gold.py`는 여기서 저신뢰(`L`)를 떼어내어 학습 gold와 review를 분리한다.
3. **온톨로지 초안 집합**: `propose_type()`은 새 label과 example title 최대 5개를 `ALPHAMALE_ONTOLOGY_DRAFTS_PATH`가 없으면 작업 디렉터리 기준 `data/manifests/events/ontology_drafts.jsonl`에 append한다. 이는 “새 타입 초안을 기록하는 적응”까지는 구현되었음을 뜻하지만, 승인·버전 범프·재실행은 여기서 수행하지 않는다.

[INFERENCE] 반대로 legacy spec이 말하는 적응 대상은 더 넓다. 미지 패턴 cluster가 30건 이상이면 신규 leaf 타입 draft를 만들고, 승인 시 `ontology_version`을 마이너 범프한 뒤 영향 에폭을 replay하는 흐름이 정의되어 있다. 이 루프는 현재 저장소에서 직접 실행되는 코드보다 설계 원칙으로 읽어야 한다.

### 3. 어떤 상태가 carry-over 되는가

#### 3.1 구현 상태

- `_epoch_state.json`은 완료한 월 목록 `done_months`와 `updated_at`만 저장한다. 현재 관찰된 상태는 `2006-06`, `2021-06` 두 달이 완료로 기록된 형태다.
- `review_queue.jsonl`은 append-only로 누적된다. 각 행에는 `reason`, `top1`, `margin`, `model_versions`, `decided_at`, `published_at`, `title`이 남아 다음 review 세대에서 근거를 재현할 수 있다.
- `epoch_out/<month>.parquet`로 가는 accepted 행은 `event_type`, `top1`, 그리고 flatten된 model version 컬럼을 가진다. [INFERENCE] 즉 수용 행도 “어느 모델/레지스트리 버전으로 결정되었는가”를 carry한다.
- KO 적응 경로에서는 `ko_adaptive_cands.jsonl`이 pre-label 후보 상태를, `ko_adaptive_labels.jsonl`이 teacher label 상태를, `ko_review_queue.jsonl`이 low-confidence 잔여 상태를 보존한다.
- ontology 적응 경로에서는 `ontology_drafts.jsonl`이 `proposed_type_id`, `label`, `example_titles`, `status=draft`, `decided_at`를 보존하도록 설계되어 있다. 기본 기록 위치는 env override가 없으면 작업 디렉터리 기준 `data/manifests/events/ontology_drafts.jsonl`이다.
아래는 상태·입력·출력 형태를 설명하기 위한 값 예시 JSON이다.

**`_epoch_state.json` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `done_months` | 완료 처리되어 다음 실행에서 건너뛸 월 목록 |
| `updated_at` | 상태 파일 마지막 갱신 시각 |

<details>
<summary>예시 JSON</summary>

```json
{
 "done_months": ["2006-06", "2021-06"],
 "updated_at": "2026-07-03T10:54:37+00:00"
}
```

</details>

**`review_queue.jsonl` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `news_id` | review 대상 기사 식별자 |
| `reason` | review로 보낸 사유 조합 |
| `top1` | 1위 타입 점수 |
| `margin` | 1위와 2위 간 점수 차 |
| `model_versions` | gate/type 모델 버전 묶음 |
| `decided_at` | review 판단 시각 |
| `published_at` | 기사 발행 시각 |
| `title` | review 대상 제목 |

<details>
<summary>예시 JSON</summary>

```json
{
 "news_id": "04104008.20060609010140002",
 "reason": "low_top1|unknown_type",
 "top1": 0.36641,
 "margin": 0.30311,
 "model_versions": {
 "gate": "xlmr-bin-v2-EN-interim(t_lo=0.002)",
 "type": "xlmr-type-en-v1"
 },
 "decided_at": "2026-07-03T09:01:20+00:00",
 "published_at": "2006-06-09 01:01:40",
 "title": "건교부 간부 '강남 주택공급 확대론' 정면 반박"
}
```

</details>

**`epoch_out/<month>.parquet` accepted row 필드별 의미**

| 필드 | 의미 |
|---|---|
| `news_id` | accepted row 기사 식별자 |
| `published_at` | accepted row 기사 발행 시각 |
| `event_type` | 자동 수용된 canonical event type |
| `top1` | 수용 당시 1위 점수 |
| `gate_model_version` | gate 모델 버전 컬럼 |
| `type_model_version` | type head 버전 컬럼 |

<details>
<summary>예시 JSON</summary>

```json
{
 "news_id": "04104008.20060609010140002",
 "published_at": "2006-06-09 01:01:40",
 "event_type": "POLICY.REGULATION.RULE_CHANGE",
 "top1": 0.81389,
 "gate_model_version": "EN-bin-v2",
 "type_model_version": "ko-type-v2-balanced"
}
```

</details>

**`ko_adaptive_cands.jsonl` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `news_id` | 라벨링 대기 후보 식별자 |
| `title` | teacher에 넘길 후보 제목 |

<details>
<summary>예시 JSON</summary>

```json
{
 "news_id": "02100601.20160120121703041",
 "title": "오엘케이, 청약경쟁 280대 1 ‥ 포인트아이, 74.84대 1 마감"
}
```

</details>

**`ko_adaptive_labels.jsonl` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `news_id` | 후보 기사 식별자 |
| `doc_class` | teacher가 준 문서 분류 |
| `event_type` | teacher가 붙인 이벤트 타입 |
| `confidence` | teacher 신뢰도 |

<details>
<summary>예시 JSON</summary>

```json
{
 "news_id": "01200101.20060607172805111",
 "doc_class": "EVENT",
 "event_type": null,
 "confidence": "L"
}
```

</details>

**`ko_review_queue.jsonl` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `gold_id` | gold/review row 식별자 |
| `title` | review로 남긴 제목 |
| `doc_class` | 문서 분류 |
| `event_types` | gold 후보 타입 배열 |
| `published_at` | 기사 발행 시각 |
| `era` | 샘플링 시대 구간 |
| `confidence` | low-confidence 분기 여부를 결정하는 값 |
| `source` | 라벨 공급자 식별자 |

<details>
<summary>예시 JSON</summary>

```json
{
 "gold_id": "01100101.20070713100017430",
 "title": "개인 기부금 15~20% 소득공제, 권오규 부총리 \"종교법인 과세 추진 안해\"",
 "doc_class": "EVENT",
 "event_types": ["POLICY.REGULATION.RULE_CHANGE"],
 "published_at": "2007-07-13T10:00:17+09:00",
 "era": "2006-2010",
 "confidence": "L",
 "source": "ko_teacher_v1"
}
```

</details>

**[INFERENCE] `ontology_drafts.jsonl` 필드별 의미**

| 필드 | 의미 |
|---|---|
| `proposed_type_id` | 제안된 draft 타입 id |
| `label` | 사람이 읽는 제안 라벨 |
| `example_titles` | 제안 근거 제목 최대 5건 |
| `status` | 현재 초안 상태 |
| `decided_at` | 초안 row append 시각 |

<details>
<summary>예시 JSON [INFERENCE]</summary>

```json
{
 "proposed_type_id": "PROPOSED.CROSS_BORDER_SUPPLY_CHAIN_ALERT",
 "label": "Cross-border supply chain alert",
 "example_titles": ["동남아 공장 셧다운으로 공급 차질 우려"],
 "status": "draft",
 "decided_at": "2026-07-08T01:22:31+00:00"
}
```

</details>

#### 3.2 spec 상태

[INFERENCE] legacy spec은 여기서 더 나아가 `gold_store.jsonl` append, `parser_version`/`ontology_version`/`mapping_version` replay, `news_gate_log`/`news_event_audit` 기반 재현성을 정의한다. 이 상태 체계는 설계상 일관되지만, 이번 문서 범위에서 직접 확인한 런타임 파일은 `_epoch_state.json`, `review_queue.jsonl`, `ko_review_queue.jsonl`, adaptive candidates/labels 쪽이다.
**[INFERENCE] review-driven growth spec bridge**

| spec 단계 | 읽는 테이블/아티팩트 | 쓰는 테이블/아티팩트 | 비고 |
|---|---|---|---|
| append-only gold 축적 | `review_queue.jsonl`, `ko_review_queue.jsonl`, 사람 확정 결과 logical table | `gold_store.jsonl` [INFERENCE] | legacy spec이 정의하지만 현재 저장소에서 직접 확인한 구현 sink는 아님 |
| replay provenance | accepted/review candidate epoch logical artifact, parser/mapping/ontology version state | `news_gate_log`, `news_event_audit` [INFERENCE] | 현재 문서는 logical table 이름만 유지 |

**[INFERENCE] legacy spec carry-over 필드별 의미**

| 필드 | 의미 |
|---|---|
| `parser_version` | replay에 사용한 parser 규칙 버전 |
| `ontology_version` | 재분류 기준 온톨로지 버전 |
| `mapping_version` | type mapping 규칙 버전 |
| `news_gate_log` | gate 판단 재현용 로그 식별자 |
| `news_event_audit` | event replay 감사용 로그 식별자 |

<details>
<summary>예시 JSON [INFERENCE]</summary>

```json
{
 "parser_version": "v2.1",
 "ontology_version": "0.1.3",
 "mapping_version": "2026-07-08",
 "news_gate_log": "news_gate_log/2021-06",
 "news_event_audit": "news_event_audit/2021-06"
}
```

</details>

### 4. 언제 review로 가고 무엇이 auto-accepted 되는가

#### 4.1 `epoch_runner.py`

`epoch_runner.py`의 review 조건은 세 가지다.

- `margin < 0.15` → `low_margin`
- `top1 < 0.5` → `low_top1`
- registry에 없는 type_id → `unknown_type`

세 조건 중 하나라도 참이면 review row가 생성되고, 아니면 accepted row로 간다. [INFERENCE] 따라서 **자동 수용(auto-accept)** 가능한 행은 “gate가 EVENT로 통과시킨 뒤, margin과 top1 임계값을 넘기고, registry 안에 있는 type으로 분류된 행”이다. `review_queue.jsonl`의 실제 행도 `unknown_type`, `low_top1|unknown_type`, `low_margin|low_top1` 같은 이유 조합을 보여준다.

**`epoch_runner.py` review gate 필드별 의미**

| 필드 | 의미 |
|---|---|
| `margin` | 1위와 2위 간 확신 차이 |
| `top1` | 1위 타입 점수 |
| `type_id` | registry 존재 여부를 검사할 분류 결과 |

<details>
<summary>예시 JSON [INFERENCE]</summary>

```json
{
 "margin": 0.30311,
 "top1": 0.36641,
 "type_id": "COMPANY.UNKNOWN.NON_REGISTRY_LEAF"
}
```

</details>

#### 4.2 `build_ko_gold.py`

KO adaptive loop의 review 조건은 더 단순하다. teacher label의 `confidence`가 `L`이고 `--keep-low`가 꺼져 있으면 해당 row는 `ko_review_queue.jsonl`로 가고, 학습 gold에서는 제외된다. 반대로 `confidence != "L"`인 행, 또는 명시적으로 `--keep-low`를 켠 행만 gold 쪽으로 자동 수용된다.

**KO gold/review row 필드별 의미**

| 필드 | 의미 |
|---|---|
| `gold_id` | gold/review row 식별자 |
| `doc_class` | 문서 분류 |
| `event_types` | gold row에 남는 canonical type 배열 |
| `published_at` | 기사 발행 시각 |
| `era` | 샘플링 시대 구간 |
| `confidence` | 저신뢰 분기 여부를 결정하는 값 |
| `source` | 라벨 공급자 식별자 |

<details>
<summary>예시 JSON</summary>

```json
{
 "gold_id": "01100101.20070713100017430",
 "doc_class": "EVENT",
 "event_types": ["POLICY.REGULATION.RULE_CHANGE"],
 "published_at": "2007-07-13T10:00:17+09:00",
 "era": "2006-2010",
 "confidence": "L",
 "source": "ko_teacher_v1"
}
```

</details>

[INFERENCE] 이 구조는 legacy spec §7.7의 “애매 판정은 무조건 리뷰 큐, 자동 확정 금지”와 일치한다. 다만 현재 구현은 KO teacher confidence split과 margin/top1 split까지는 직접 보이지만, 사람 확정 후 append-only `gold_store.jsonl`로 들어가는 후속 저장소는 spec 층에 남아 있다.

### 5. 온톨로지 적응: 구현과 설계의 경계

현재 구현된 온톨로지 적응은 `load_registry(..., version=...)`와 `propose_type()` 두 가지다. 전자는 registry 전체에 version label을 붙이고, 후자는 중복 draft 여부를 확인한 뒤 새 draft row를 append한다.

하지만 legacy spec이 요구하는 핵심 정책은 더 넓다.

- review 큐의 **동일 미지 패턴 cluster ≥ 30건**
- 신규 leaf 타입 draft 제안
- 승인 시 `ontology_version` 마이너 범프
- 영향 에폭 replay 재분류
- additive-only, meaning-preserving evolution

[INFERENCE] 이 다섯 단계는 legacy spec에만 직접 존재한다. 따라서 현재 문서에서 `src/alphamale/events/ontology/` 패키지를 “자동 온톨로지 진화 엔진”으로 설명하면 과장이다. 정확한 표현은 **draft proposal API는 구현되었고, cluster·approval·replay orchestration은 아직 spec/diagram 경로**다. 세부 경로는 `근거/출처`를 따른다.

### 6. PIT / no-lookahead 보호와 stage 안전장치

#### 6.1 구현된 보호 장치

`exploration_bench.py`는 PIT 보호를 코드로 집행하며, 이 규칙 집합을 현재 production graph-projection policy의 authoritative reference로 사용한다.

- corpus grounding은 `published_at < asof`인 문서만 lookup에 사용한다.
- trace 시작점에서 `pit_guard = PIT_OK`를 남기고, forbidden check는 이 trace가 없으면 위반으로 간주한다.
- `_projection()`은 resolution stage를 `DURABLE_EDGE_UPDATE_VALID_TO`, activation stage를 `DURABLE_EDGE`, pending stage를 `PENDING_EDGE`, 그 외를 `EVENT_ONLY` 또는 `NODE_EVENT_ONLY`로 제한한다.
- `_forbidden_violated()`는 `use_future_alias_or_master`, `valid_from_before_available_at`, `create_graph_edge:member_of_before_effective`, `create_control_owns_edge` 같은 조기 승격을 기계적으로 잡는다.

또한 stage 자체도 정규식 기반 규칙으로 추론된다. 예를 들어 `COMPANY.M_AND_A`는 rumor, pending approval, closed를 구분하고, `POLICY.TRADE.TARIFF_CHANGE`는 pending effective를, `MARKET_STRUCTURE.INDEX`는 announced와 effective를 분리한다. 즉 PIT 보호는 “시간 필터 + stage gate + forbidden promotion audit”의 삼중 구조다.

feature/thread 쪽 PIT 원칙은 아직 deferred implementation contract다. `src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml`은 모든 feature가 `available_at` 시점에서 계산 가능해야 하고, 값 없음은 `UNKNOWN`으로 남겨야 하며, outcome join이나 중간 impact score를 금지한다고 적는다. draft thread 문서는 이를 확장해 `thread_identity`, `observation_window_days`, `SCD2 thread state`, `UNKNOWN_MISSING_QUALIFIER` 규칙을 제안한다.

다만 current-state 온톨로지 문서는 이 영역을 아직 runtime contract로 올리지 않는다. feature registry는 packaged split authoring 상태이며 runtime 소비자는 패키지 공개 로더를 통해 profiles/ontology를 읽고, `thread_identity`도 schema/validator/runtime이 함께 바뀔 때까지 deferred implementation contract로 남아 있다.

### 7. 다이어그램과 코드의 대응 관계

| 다이어그램 요소 | 코드/데이터 대응 | 현재 판정 |
|---|---|---|
| flywheel의 `리뷰 큐` | `review_queue.jsonl`, `ko_review_queue.jsonl` | 구현 대응 |
| flywheel의 `승격 판정` | `propose_type()` 초안 append까지만 직접 대응 | 부분 대응 |
| flywheel의 `effective_from = 최초관측일` | legacy spec의 identity/attribute 분리와 동일 의도 | spec/diagram only |
| flywheel의 `rebuild_news_events` / `분류기 재학습` | §7.6 replay, §7.7 재학습 케이던스 | spec/diagram only |
| structured exploration의 `DATA_GAPS 기록` | `exploration_bench.py` S12가 `data_gaps`를 반환 | 구현 대응 |
| structured exploration의 `backlog 생성 → 재실행` | [INFERENCE] 현재 스크립트는 gap을 산출하지만 backlog 생성/재측정 orchestrator는 직접 관찰되지 않음 | 부분 대응 |
| architecture page 4의 `draft 엣지 리뷰 큐 생성 → 사람/LLM 검토 → 승격 or 기각` | review queue 기반 인간/teacher 개입 설계와 합치 | 구현+의도 혼합 |

## Alternatives

1. **현재처럼 얇은 적응 표면 유지**
 - 장점: `epoch_runner.py`, `build_ko_gold.py`, `ontology.py`, `exploration_bench.py`가 각자 단순한 책임을 가진다.
 - 단점: review→gold→ontology bump→replay가 한 런타임 루프로 봉합되지 않는다.

2. **spec의 end-to-end adaptive controller를 구현**
 - 장점: legacy spec §7.7이 의도한 `review_queue → gold_store → ontology_version → replay` 닫힌 루프를 코드화할 수 있다.
 - 단점: 현재 문서 범위를 넘는 orchestration, 저장소, 승인 프로토콜이 추가로 필요하다.

3. **feature/thread substrate를 먼저 런타임 계약으로 승격**
 - 장점: adaptive loop가 type 분류뿐 아니라 thread lineage와 transition observation까지 다룰 수 있다.
 - 단점: current-state 문서가 지적하듯 runtime 소비자와 validator가 아직 해당 계약을 읽지 않는다.

## Risks

- **구현/설계 혼동 위험**: flywheel 그림만 보면 승격·재빌드·재학습이 모두 자동화된 것처럼 보일 수 있다. 실제 구현 근거는 review split과 draft append까지가 더 강하다.
- **review backlog 누적 위험**: `review_queue.jsonl`와 `ko_review_queue.jsonl`는 append-only라 세대가 쌓일수록 소비 정책이 중요해진다.
- **PIT 과신 위험**: `exploration_bench.py`의 PIT guard는 bench harness에는 강하지만, feature/thread draft 전체가 동일 수준으로 런타임 강제된 것은 아니다.
- **온톨로지 과확장 위험**: spec의 additive evolution 원칙 없이 draft proposal만 남발하면 type namespace drift가 생길 수 있다.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 문서 규칙 | `docs/README.md` | canonical design format과 evidence rule |
| epoch runtime / state | `src/alphamale/events/epoch/runner.py`<br>`data/interim/events/_epoch_state.json`<br>`data/interim/events/review_queue.jsonl` | oldest-first month processing, review append, done-month carry-over |
| KO adaptive split | `src/alphamale/events/gold/ko.py`<br>`data/interim/events/ko_adaptive_cands.jsonl`<br>`data/interim/events/ko_adaptive_labels.jsonl`<br>`data/interim/events/ko_review_queue.jsonl` | adaptive candidate → label → gold/review path |
| ontology draft API | `src/alphamale/events/ontology/__init__.py`<br>`src/alphamale/events/ontology/registry/loader.py`<br>`src/alphamale/events/ontology/proposal.py`<br>`src/alphamale/events/ontology/constants.py` | versioned registry load와 draft append contract |
| projection / PIT safety | `src/alphamale/events/benchmarks/exploration.py`<br>`src/alphamale/events/ontology/resources/feature_specs_v0_1.yaml` | PIT guard, stage/projection rules, feature-side PIT principles |
| deferred thread/runtime boundary | `docs/research/event-modeling/event-ontology.md`<br>`docs/research/event-modeling/event-feature-thread-discovery.md` | `thread_identity`와 feature/thread substrate 경계 |
| legacy adaptive intent | `docs/archive/events/news-event-integration-spec.md`<br>`docs/archive/diagrams/events/news-event-5-7-flywheel.drawio`<br>`docs/archive/diagrams/events/news-event-structured-exploration.drawio`<br>`docs/archive/diagrams/graph/alphamale-graph-architecture.drawio` | cluster/bump/replay 루프와 diagram intent |

## Rollout

이 문서는 current-state + design-intent 정렬 문서다. 따라서 rollout은 코드 배포 순서가 아니라 **해석 순서**다.

1. 현재 운영 사실은 `epoch_runner.py`, `build_ko_gold.py`, `exploration_bench.py`, 각 JSONL/JSON 산출물에서 읽는다.
2. 온톨로지 적응은 `propose_type()`까지를 현재 구현으로 보고, cluster/bump/replay는 legacy spec의 후속 구현 범위로 읽는다.
3. feature/thread adaptive substrate는 draft boundary로 유지한다. current-state 문서가 지적하듯 runtime consumer와 schema validator가 함께 움직이기 전까지는 deferred implementation contract다.
4. 다이어그램은 “코드가 이미 다 구현했다”는 증거가 아니라, review-driven growth를 어떤 닫힌 루프로 만들고 싶은지 보여주는 intent artifact로 사용한다.

## Pending Operational Decisions

1. 후속 승인 전까지는 `review_queue.jsonl`와 `ko_review_queue.jsonl`의 소비 정책을 최신 `model_version`/generation 필터 우선 적용 + 승격 완료 행 promotion 후 compaction으로 두는 안을 기준 후보로 둔다. append-only 파일은 증거 보존층이고, 운영 소비자는 세대 필터를 거친 active slice만 읽는다.
2. 후속 승인 전까지는 `propose_type()` 이후 승인 owner를 event-research/ontology maintainer로 두는 안을 기준 후보로 둔다. 현재 API는 append-only draft logging까지만 제공하며, 승인·version bump·replay orchestration은 maintainer-controlled deferred implementation contract로 둔다.
3. 후속 승인 전까지는 spec의 `cluster >= 30` 기준을 normalized type cue, entity/theme linkage, dedup/thread signal을 합친 feature space로 읽는 안을 기준 후보로 둔다. current code는 이 군집기를 노출하지 않지만, 운영 문서에서는 이 조합을 cluster contract 후보로 남긴다.
4. 후속 승인 전까지는 `thread_identity`를 profile lifecycle/roles와 feature qualifiers를 함께 묶는 방식으로만 병합하는 안을 기준 후보로 둔다. 단 이 병합은 schema, validator, runtime consumer가 같은 변경 집합으로 올라갈 때만 허용되며, 그 전까지는 deferred implementation contract다.
5. 후속 승인 전까지는 `exploration_bench.py`의 PIT/stage/projection gate를 production graph-projection policy 후보로 유지한다. 현재 저장소에서 bench harness가 이 정책을 실행·감사하고 있을 뿐이며, production runtime도 동일 규칙을 따르는 방향을 전제로 검토한다.
