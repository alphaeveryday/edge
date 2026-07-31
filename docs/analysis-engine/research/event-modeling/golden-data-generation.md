---
doc_type: report
status: Draft
owner: event-research
created: 2026-07-08
updated: 2026-07-11
related:
  - STATE.md
  - golden-data-inference.md
  - adaptive-review-loop.md
---
# 골든 데이터 생성 파이프라인

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

## Summary

이 문서는 현재 골든 데이터 생성 경로를 미국 title gold와 한국어 cold-start/teacher-label split 두 갈래로 정리한다. 미국은 pilot title label을 제목 해시 조인으로 `gold_title.jsonl`에 복구하고, 한국어는 BigKinds 제목 표본을 teacher label과 합쳐 `ko_gold_title.jsonl`과 `ko_review_queue.jsonl`로 나눈다. 세부 파일 경로는 문서 끝 `근거/출처`에 모았다.

핵심 결론은 세 가지다. 미국 gold는 41,920행이 복구되어 class 분포가 기대치와 일치한다. 한국어는 6,000 teacher-labeled 후보 중 H/M 4,295행만 학습 gold로 승격하고, L 1,705행은 자동 확정하지 않고 review queue로 보낸다. 미국 쪽은 전용 pytest 계약이 있지만 한국어 쪽은 현재 스크립트와 저장 산출물이 사실상 계약 역할을 한다.

## Context

이 문서는 `docs/research/event-modeling/` 아래의 current research document이며, canonical frontmatter/section 순서를 따른다.

이 저장소의 gold 관련 자산은 `data/fixtures/events/README.md` 기준으로 source/spec, benchmark fixture, generated report/output으로 나뉜다. legacy 통합 스펙은 영어 US FMP 기반 title gold와 한국어 BigKinds cold-start를 분리해야 하는 배경을 설명하며, 본 문서는 그중 현재 체크인된 생성기와 산출물만 current contract로 다룬다.

현재 gold generation 관련 수치는 아래 generated block을 authoritative snapshot으로 삼는다.

<!-- metrics:start gold-generation-metrics -->
**Current gold-generation metrics**

Metric-id snapshot for the title-gold and KO teacher-label split reports.

_Generated from `data/manifests/events/model_metrics.yaml` via `uv run alphamale events gold metrics-sync render-docs`._

| Metric ID | Snapshot |
|---|---|
| `events.gold.title.en` | written_rows=41920; event_rows=13794; event_type_count_total=14470 |
| `events.gold.title.ko` | labeled_rows=6000; gold_rows=4295; event_rows=1090; review_rows_low_conf=1705 |
<!-- metrics:end -->

## Problem

골든 데이터 관련 자산은 실제로 세 층에 흩어져 있다.

1. **구현 코드**: `build_gold.py`, `sample_ko_gold.py`, `build_ko_gold.py`
2. **생성 산출물**: `gold_title*.jsonl/json`, `ko_gold_candidates.jsonl`, `ko_gold_title.jsonl`, `ko_review_queue.jsonl`, `ko_gold_stats.json`
3. **legacy/draft 설명**: `news_event_integration_spec.md`, `news_event_5_7_flywheel.drawio`, `alphamale_graph_architecture.drawio`

이 문서는 gold 출처, row shape, title 복구 실패와 저신뢰 라벨 처리, stats의 증명 범위, 현재 deferred implementation boundary를 한 곳에 고정한다.

## Goals

- 미국 title gold와 한국어 cold-start/teacher-label gold의 입력, 단계, 출력, review split을 한 문서에서 설명한다.
- 각 산출 row의 실제 필드 shape를 코드와 저장된 예시 둘 다로 고정한다.
- stats와 quality gate가 무엇을 증명하는지, 무엇은 아직 증명하지 못하는지 구분한다.
- 구현된 것과 legacy/draft 의도를 섞지 않고 구분한다.

## Non-goals

- 이 문서는 ETF, 가격, 수익률, FF6, analyst 로직을 다루지 않는다.
- 이 문서는 body gold 9,864건의 생성 경로나 canonical event 조립기를 상세 설계하지 않는다. 현재 범위는 title-level gold generation이다.
- 이 문서는 코드를 수정하지 않는다. 다만 현재 관찰 가능한 계약과 deferred implementation contract는 명시한다.

## Current State and Proposed Pipeline Contract
### 입출력 테이블 맵

| ERD 구간 | 필요한 입력 테이블/아티팩트 | 최종 출력 마트/브리지 | 중간 산출(최종 아님) | 상태/owner |
|---|---|---|---|---|
| 미국 title gold 생성 | `financial_event_engine_spec_v1/pilot/gold_title/*.json`, `data/raw/news/us_fmp_news_rich.parquet`, `manifest_title_build.json`, `manifest_title.json`, `sample_ids.json` | `data/fixtures/events/gold_title.jsonl`, `data/fixtures/events/gold_title_missing.jsonl` | `data/manifests/events/gold_title_stats.json` | current |
| 한국어 cold-start 후보 표본화 | `data/raw/news/bigkinds/econ_*.parquet` logical source shard | 없음 — 다음 KO gold split의 upstream candidate only | `data/interim/events/ko_gold_candidates.jsonl` | current, intermediate-only |
| 한국어 teacher-labeled gold/review split | `data/interim/events/ko_gold_candidates.jsonl`, `data/interim/events/ko_teacher_labels_*.jsonl` | `data/fixtures/events/ko_gold_title.jsonl`, `data/interim/events/ko_review_queue.jsonl` | `data/manifests/events/ko_gold_stats.json` | current |
| 파생/legacy fixture surface | `data/fixtures/events/ko_gold_title.jsonl` 또는 동등 gold output, `data/interim/events/ko_review_queue.jsonl` | 없음 — canonical gold owner 아님 | `data/interim/events/ko_gate_gold_all.jsonl`, `data/interim/events/ko_gold_title_v2.jsonl` | derived/legacy |

### 1. 아티팩트 경계

| 구분 | 아티팩트 | 현재 역할 |
|---|---|---|
| 구현 코드 | `src/alphamale/events/gold/build.py` | 미국 title gold 빌더 |
| 구현 코드 | `src/alphamale/events/gold/sample.py` | 한국어 cold-start 후보 샘플러 |
| 구현 코드 | `src/alphamale/events/gold/ko.py` | teacher label을 한국어 gold/review로 분기 |
| 생성 산출물 | `data/fixtures/events/gold_title.jsonl`, `gold_title_missing.jsonl`, `gold_title_stats.json` | 미국 gold 본체, 복구 실패 큐, 집계 보고서 |
| 생성 산출물 | `data/interim/events/ko_gold_candidates.jsonl`, `ko_gold_title.jsonl`, `ko_review_queue.jsonl`, `ko_gold_stats.json` | 한국어 후보, 학습 gold, review queue, 집계 보고서 |
| legacy/draft spec | `docs/archive/events/news-event-integration-spec.md` | EN gold와 KO cold-start가 분리된 배경과 review loop 의도 설명 |
| 다이어그램 source | `docs/archive/diagrams/events/news-event-5-7-flywheel.drawio`, `docs/archive/diagrams/graph/alphamale-graph-architecture.drawio` | review→승격→재학습 루프와 BigKinds 상류 파이프라인 시각화 |

추가로 저장소에는 `ko_gate_gold_all.jsonl`과 `ko_gold_title_v2.jsonl`도 존재한다. 현재 계약에서는 전자를 **derived/legacy gate fixture**, 후자를 **후속 derived gold fixture**로 읽는다. 이번 범위에서 직접 확인한 빌더는 `ko_gold_candidates.jsonl`, `ko_gold_title.jsonl`, `ko_review_queue.jsonl`, `ko_gold_stats.json`만 명시적으로 쓰므로, **현재 canonical KO source는 `ko_gold_title.jsonl` + `ko_review_queue.jsonl`**이다. 별도 빌더가 추가되기 전까지 `ko_gate_gold_all.jsonl`은 gate 평가용 파생 fixture로 유지한다.

### 2. 미국 title gold 파이프라인 (`build_gold.py`)

#### 2.1 입력

미국 파이프라인의 기본 입력은 세 가지다.

1. pilot label 디렉터리 `financial_event_engine_spec_v1/pilot/gold_title/*.json`
2. 제목 복구용 `data/raw/news/us_fmp_news_rich.parquet`
3. pilot metadata 파일 `manifest_title_build.json`, `manifest_title.json`, `sample_ids.json`

코드는 `DEFAULT_PILOT`, `DEFAULT_TITLES_PARQUET`, `DEFAULT_OUT`를 상수로 두고, title recovery 규칙을 `md5(normalize(title))`로 고정한다. normalize는 공백 접기 + 소문자화다. 테스트 fixture도 같은 join recipe를 재현한다.
**raw input requirement → downstream output bridge (`build_gold.py`)**

| raw input requirement | downstream output | bridge rule |
|---|---|---|
| `pilot/gold_title/*.json`의 `doc_class`/`events[]` | `data/fixtures/events/gold_title.jsonl`의 `{doc_class,event_types}` | pilot label을 dedupe한 `event_types`로 정규화 |
| `data/raw/news/us_fmp_news_rich.parquet`의 `{article_id,published_at,title}` | `data/fixtures/events/gold_title.jsonl`의 `{title,published_at}` | `md5(normalize(title))` 해시 조인으로 제목/시각 복구 |
| 제목 복구 실패 pilot row | `data/fixtures/events/gold_title_missing.jsonl` | gold 본체로 가지 않고 missing sink로 격리 |
| pilot metadata (`manifest_*`, `sample_ids.json`) | `data/manifests/events/gold_title_stats.json`의 `pilot_metadata` | provenance를 stats artifact에 함께 보존 |

#### 2.2 단계

미국 빌더는 다음 순서로 동작한다.

1. `load_title_index()`가 parquet의 `article_id`, `published_at`, `title`을 읽고, 정규화 제목 해시별로 `TitleRecord`를 만든다. 동일 해시 후보가 여러 개면 `published_at` 존재 여부, 제목, 시간, article_id 순으로 가장 안정적인 record 하나로 고정한다.
2. `gold_title/*.json`을 순회하며 `doc_class`를 읽고, `events[]`에서 `event_type_id` 또는 `event_type`을 dedupe해 `event_types` 배열로 만든다.
3. 파일 stem 해시로 제목을 복구하면 최종 row를 쓰고, 복구하지 못하면 `_missing` 큐에 넣는다.
4. 마지막에 `gold_title.jsonl`, `gold_title_missing.jsonl`, `gold_title_stats.json`을 기록한다.

#### 2.3 row shape

구현 계약과 실제 출력은 일치한다. 전용 테스트는 첫 row를 다음 shape로 고정한다. 실제 저장 산출물도 같은 shape를 보인다.


| 파일 | row shape |
|---|---|
| `gold_title.jsonl` | `{gold_id, title, doc_class, event_types, published_at, source}` |
| `gold_title_missing.jsonl` | `{gold_id, doc_class, event_types, reason, source}` |
아래 예시는 실제 체크인 샘플 또는 현재 체크인 상태를 따른다.

**필드 설명 — `gold_title.jsonl`**

| 필드 | 의미 |
|---|---|
| `gold_id` | pilot gold 행의 안정 키 |
| `title` | 복구된 원문 제목 |
| `doc_class` | gate 학습에 쓰는 문서 class |
| `event_types` | dedupe된 event type 목록 |
| `published_at` | 복구된 기사 시각, 없으면 `NULL` |
| `source` | 생성 경로 태그 |

<details><summary>예시 JSON (`gold_title.jsonl`)</summary>

```json
{"gold_id":"00075b59da5a9f70c9b16cf2ffe8bb44","title":"Apple launches iPhone 17e with prices starting at $599","doc_class":"EVENT","event_types":["COMPANY.PRODUCT.LAUNCH"],"published_at":null,"source":"pilot_title_v1"}
```

</details>

**필드 설명 — `gold_title_missing.jsonl`**

| 필드 | 의미 |
|---|---|
| `gold_id` | 복구 실패 pilot row의 안정 키 |
| `doc_class` | pilot이 부여한 문서 class |
| `event_types` | pilot이 준 event type 목록 |
| `reason` | 누락 사유 코드 |
| `source` | 생성 경로 태그 |

<details><summary>예시 JSON (`gold_title_missing.jsonl`, 현재 체크인 상태)</summary>

```json
{"missing_titles":0,"rows":[]}
```

</details>

`event_types`는 중복 없이 정렬 보존된 리스트다. 테스트 fixture는 중복 `COMPANY.PRODUCT.LAUNCH`가 입력에 있어도 출력은 한 번만 남는다는 점을 검증한다.

#### 2.4 누락 행 처리와 stats

미국 파이프라인에서 “문제가 있는 행”은 두 종류로 표현된다.

- **제목 복구 실패**: `_missing` 큐로 격리되고 학습 gold 본체에는 들어가지 않는다.
- **제목 복구 성공**: 최종 gold row로 기록된다.

현재 체크인된 산출물에서는 `missing_titles`가 0이고 `gold_title_missing.jsonl`도 비어 있다. 즉 현재 snapshot 기준으로는 41,920개 pilot gold 파일이 모두 제목 복구에 성공했다.

stats는 다음을 증명한다.

- pilot gold 파일 수가 기대값 41,920와 정확히 일치한다.
- 4개 `doc_class` 분포가 기대치와 정확히 일치한다.
- 상위 event type 30개와 전체 event-type token 수를 집계한다.
- pilot metadata와 join recipe를 함께 남겨 입력 provenance를 복원할 수 있다.

반대로 이 stats가 증명하지 않는 것도 있다. 이 숫자는 **join completeness와 class/type 분포 정합성**은 보여주지만, 라벨 품질 자체나 body-level 정보 completeness를 증명하지는 않는다.

#### 2.5 테스트/계약

미국 파이프라인은 전용 pytest 계약을 가진다. 커버 범위는 다음과 같다.

- 해시 조인으로 제목/시각을 복구하는지
- `event_types` dedupe가 동작하는지
- missing title이 `_missing` 큐에 기록되는지
- stats payload가 `total_gold_files`, `written_rows`, `missing_titles`, `doc_class_counts`, `event_type_counts_top30`, `pilot_metadata`를 포함하는지
- 재실행 시 `gold_title.jsonl`, `gold_title_missing.jsonl`, `gold_title_stats.json` 바이트가 동일한지, 즉 출력이 결정적인지

### 3. 한국어 cold-start + teacher-label 파이프라인

#### 3.1 왜 별도 파이프라인인가

legacy spec은 영어 title gold가 US FMP 뉴스에만 연결되어 있어 한국 BigKinds에 직접 전이할 수 없다고 명시한다. 그래서 한국어는 E1b cold-start + teacher labeling을 별도 단계로 둔다. 구현 코드도 이를 그대로 따른다. 하나의 스크립트가 후보를 만들고(`sample_ko_gold.py`), 다른 스크립트가 teacher label을 붙여 최종 gold/review를 나눈다.

#### 3.2 단계 A: cold-start 후보 샘플링 (`sample_ko_gold.py`)

후보 샘플러의 입력은 BigKinds `econ_*.parquet`이며, 연대 버킷은 2006-2010 / 2011-2015 / 2016-2020 / 2021-2026 네 구간으로 고정돼 있다.

샘플링 단계는 다음과 같다.

1. `era_files()`가 연대별 shard를 묶고, `--files-per-era`만큼 균등하게 뽑아 비용을 제한한다.
2. 각 shard에서 `news_id`, `published_at`, `title`만 읽는다.
3. 제목 길이 `< 8` 또는 한글 미포함이면 버린다.
4. 앞 80자 기준으로 dedupe한다.
5. `event_rubric.label(title)`로 `EVENT / OPINION / AD / UNCERTAIN`과 규칙명을 붙인다.
6. `(era, rubric_label)` cell마다 최대 `--per-cell`개만 남긴 뒤 전체를 섞어 `ko_gold_candidates.jsonl`을 쓴다.

이 스크립트의 docstring은 기본 총량이 약 6k라고 설명하고, 실제 spec rollout 표도 `연대4×rubric4` 층화 표본 6,000을 기록한다.

후보 row shape는 다음과 같다.


| 파일 | row shape |
|---|---|
| `ko_gold_candidates.jsonl` | `{news_id, title, published_at, era, rubric_label, rubric_rule}` |
아래 예시는 실제 체크인 샘플을 따른다.

**필드 설명 — `ko_gold_candidates.jsonl`**

| 필드 | 의미 |
|---|---|
| `news_id` | BigKinds 기사 식별자 |
| `title` | cold-start 후보 제목 |
| `published_at` | KST offset을 포함한 기사 시각 |
| `era` | 층화 샘플링용 연대 버킷 |
| `rubric_label` | 규칙 기반 1차 라벨 |
| `rubric_rule` | 해당 라벨을 만든 규칙명 |

<details><summary>예시 JSON (`ko_gold_candidates.jsonl`)</summary>

```json
{"news_id":"04101008.20160729171500365","title":"대우조선, 1.6조 해양플랜트 계약 해지..\"불확실성 제거\"(상보)","published_at":"2016-07-29T17:15:00+09:00","era":"2016-2020","rubric_label":"EVENT","rubric_rule":"action"}
```

</details>

`published_at`은 KST 명시 offset을 강제한다.

#### 3.3 단계 B: teacher label 조인과 gold/review split (`build_ko_gold.py`)

`build_ko_gold.py`는 `ko_teacher_labels_*.jsonl`을 `ko_gold_candidates.jsonl`에 `news_id`로 조인한다. teacher label 입력 row는 실제로 `{news_id, doc_class, event_type, confidence}` shape다.
아래 예시는 실제 teacher label 샘플을 따른다.

**필드 설명 — teacher label 입력 row (`ko_teacher_labels_auto.jsonl`)**

| 필드 | 의미 |
|---|---|
| `news_id` | 후보와 조인할 기사 식별자 |
| `doc_class` | teacher가 부여한 문서 class |
| `event_type` | 단일 event type 예측값, 없으면 `NULL` |
| `confidence` | teacher 신뢰도 등급 |

<details><summary>예시 JSON (`ko_teacher_labels_auto.jsonl`)</summary>

```json
{"news_id":"04101008.20160729171500365","doc_class":"EVENT","event_type":"COMPANY.CONTRACT.SIGNING","confidence":"M"}
```

</details>

빌더 단계는 다음과 같다.

1. 후보를 `news_id` key로 로드한다.
2. `labels-glob`에 매칭되는 teacher label 파일을 읽고, 같은 `news_id`가 여러 번 나오면 마지막 값을 취하면서 duplicate 수를 센다.
3. `doc_class`가 허용 집합(`EVENT`, `OPINION_OR_ANALYSIS`, `NO_EVENT_MARKET_COMMENTARY`, `PROMOTIONAL_OR_SOLICITATION`)에 없으면 버린다.
4. `doc_class == "EVENT"`일 때만 단일 `event_type`을 `event_types:[...]`로 감싼다. 그 외 class는 빈 배열이다.
5. `confidence == "L"`이고 `--keep-low`가 없으면 `ko_review_queue.jsonl`로 보낸다. 그 외는 `ko_gold_title.jsonl`로 보낸다.
6. gold/review를 정렬해 기록하고 stats를 남긴다.
**raw input requirement → downstream output bridge (`sample_ko_gold.py` + `build_ko_gold.py`)**

| raw input requirement | downstream output | bridge rule |
|---|---|---|
| `data/raw/news/bigkinds/econ_*.parquet`의 `{news_id,published_at,title}` | `data/interim/events/ko_gold_candidates.jsonl` | 길이/한글 필터, 80자 dedupe, rubric 층화 샘플링 |
| `data/interim/events/ko_gold_candidates.jsonl` + `data/interim/events/ko_teacher_labels_*.jsonl` | `data/fixtures/events/ko_gold_title.jsonl` | `news_id` 조인 후 허용 `doc_class`만 gold sink로 정렬 기록 |
| 동일 입력 조인 결과 중 `confidence == "L"` | `data/interim/events/ko_review_queue.jsonl` | 자동 확정 금지, review sink로 분기 |
| 후보 수/라벨 수/분기 집계 | `data/manifests/events/ko_gold_stats.json` | 생성 경로의 split provenance를 report artifact로 저장 |

최종 row shape는 다음과 같다.


| 파일 | row shape |
|---|---|
| `ko_gold_title.jsonl` | `{gold_id, title, doc_class, event_types, published_at, era, confidence, source}` |
| `ko_review_queue.jsonl` | `ko_gold_title.jsonl`과 동일 shape, 단 저신뢰 행만 수록 |
아래 예시는 실제 체크인 샘플을 따른다.

**필드 설명 — `ko_gold_title.jsonl`**

| 필드 | 의미 |
|---|---|
| `gold_id` | 최종 한국어 gold 행 키 |
| `title` | 최종 학습 제목 |
| `doc_class` | 최종 문서 class |
| `event_types` | `EVENT`일 때만 채우는 type 목록 |
| `published_at` | KST offset을 포함한 기사 시각 |
| `era` | 후보 단계에서 물려받은 연대 버킷 |
| `confidence` | 학습 gold에 남은 teacher 신뢰도 |
| `source` | gold 생성 경로 태그 |

<details><summary>예시 JSON (`ko_gold_title.jsonl`)</summary>

```json
{"gold_id":"01100101.20070711100017031","title":"펀드 판매보수 폐지 검토...금감원, 수수료도 대폭 낮추기로","doc_class":"EVENT","event_types":["POLICY.REGULATION.RULE_CHANGE"],"published_at":"2007-07-11T10:00:17+09:00","era":"2006-2010","confidence":"M","source":"ko_teacher_v1"}
```

</details>

**필드 설명 — `ko_review_queue.jsonl`**

| 필드 | 의미 |
|---|---|
| `gold_id` | review 대기 한국어 행 키 |
| `title` | 사람이 다시 볼 저신뢰 제목 |
| `doc_class` | teacher가 준 문서 class |
| `event_types` | 저신뢰라 자동 확정하지 않은 type 목록 |
| `published_at` | 기사 시각 |
| `era` | 연대 버킷 |
| `confidence` | review 분기를 만든 저신뢰 등급 |
| `source` | review 행 생성 경로 태그 |

<details><summary>예시 JSON (`ko_review_queue.jsonl`)</summary>

```json
{"gold_id":"01100101.20070711100017044","title":"워런 버핏 또 2조원 기부","doc_class":"EVENT","event_types":[],"published_at":"2007-07-11T10:00:17+09:00","era":"2006-2010","confidence":"L","source":"ko_teacher_v1"}
```

</details>

현재 저장된 예시를 보면 review queue의 샘플은 모두 `confidence: "L"`이고, `EVENT`여도 `event_type`이 비어 있을 수 있다. 즉 저신뢰라는 이유만으로 event type을 자동 보정하지 않는다.

#### 3.4 저신뢰 행 처리와 stats

한국어 파이프라인의 핵심 분기점은 confidence다. 코드 docstring은 `L` 행을 “ambiguous -> review, never auto-confirmed”로 설명하고, legacy spec도 “애매 판정은 무조건 리뷰 큐”와 “L 1,705→리뷰 큐”를 명시한다.

현재 `ko_gold_stats.json`은 다음을 보여준다.

- labeled rows 6,000
- gold rows 4,295
- review rows 1,705
- missing title for label 0
- missing label for candidate 0
- bad doc class 0
- gold confidence 분포는 `M: 2621`, `H: 1674`
- gold 내 `EVENT` class는 1,090행

즉 이 stats가 증명하는 것은 다음이다.

1. 현재 snapshot에서는 후보와 teacher label의 `news_id` 조인이 완전했다.
2. 저신뢰 1,705행은 학습 gold로 섞이지 않고 review로 분리됐다.
3. 학습 gold에는 H/M confidence만 남았다.
4. gold 내부의 상위 event type 분포를 관찰할 수 있다.

하지만 이 stats 역시 teacher label의 의미론적 정확도까지 증명하지는 않는다. 이것은 split integrity와 현재 분포 snapshot을 보여줄 뿐이다.

#### 3.5 quality gate 요약

| 구간 | gate | 실패/저신뢰 처리 | 무엇을 보증하는가 |
|---|---|---|---|
| 미국 title recovery | `md5(normalize(title))` 해시 조인, `doc_class` 필수, `event_types` dedupe | 제목 미복구 행은 `gold_title_missing.jsonl`로 분리 | pilot label inventory를 title-level 학습 행으로 안정적으로 복원 |
| 미국 stats gate | expected total 41,920, expected 4-class 분포, delta 계산 | delta는 `gold_title_stats.json`에 남음 | 현재 snapshot이 기대 inventory와 맞는지 검산 |
| 한국어 후보 gate | 연대별 shard sampling, 한글 포함, 길이 ≥8, 앞 80자 dedupe, `(era,rubric)` cell cap | 기준 미달 제목은 후보에 들어가지 않음 | teacher labeling 이전 후보 풀이 연대·rubric 기준으로 퍼지도록 제한 |
| 한국어 build gate | 허용 `doc_class` 검사, `news_id` join, `confidence` split | bad class는 제외, `L`은 `ko_review_queue.jsonl`, 누락은 stats 카운트 | 학습 gold에 저신뢰 행이 섞이지 않도록 보장 |
| 한국어 stats gate | labeled/gold/review/missing/bad-class/confidence 집계 | 결과는 `ko_gold_stats.json`으로 기록 | 현재 snapshot의 split integrity와 분포를 검산 |

<details><summary>예시 JSON (quality gate 실제 값 묶음)</summary>

```json
{
 "us_title_recovery": {
 "doc_class": "EVENT",
 "event_types": ["COMPANY.PRODUCT.LAUNCH"],
 "missing_titles": 0
 },
 "us_stats_gate": {
 "written_rows": 41920,
 "missing_titles": 0,
 "event_rows": 13794
 },
 "ko_candidate_gate": {
 "news_id": "04101008.20160729171500365",
 "era": "2016-2020",
 "rubric_label": "EVENT",
 "rubric_rule": "action"
 },
 "ko_build_gate": {
 "gold_id": "01100101.20070711100017044",
 "confidence": "L",
 "target": "ko_review_queue.jsonl"
 },
 "ko_stats_gate": {
 "labeled_rows": 6000,
 "gold_rows": 4295,
 "review_rows_low_conf": 1705
 }
}
```

</details>

### 4. review loop와 다이어그램 정렬

gold generation은 단순 파일 변환이 아니라 review loop의 일부로 설계돼 있다.

- `news_event_5_7_flywheel.drawio`는 “리뷰 큐”, “승격 판정”, “effective_from = 최초관측일”, “rebuild_news_events”, “분류기 재학습(승격 결정 = 골드)”를 한 루프로 묶는다. 이 그림은 review 결과가 단순 보관이 아니라 재학습과 재처리로 되먹임된다는 설계 의도를 보여준다.
- 같은 legacy spec도 에폭 루프에서 “애매 판정 → review_queue”, “리뷰 판정 → 골드 append”, “동일 미지 패턴 누적 → ontology draft 제안”, “골드 +5k 또는 신규 타입 승인 시 재학습”을 명시한다.
- `alphamale_graph_architecture.drawio`의 개념 페이지는 “교사 라벨 42k 확보, 소형모델 증류 진행 중”을 적고, 뉴스 파이프라인 페이지는 BigKinds → `event_rubric (결정적)` → title-only profile shard 흐름을 보여준다. 이는 한국어 cold-start가 BigKinds 제목과 rubric 위에서 시작한다는 현재 구현과 맞아 떨어진다.

이 문서 범위에서 중요한 점은, review loop의 **전면 구현**이 아니라 gold generation과 직접 맞닿은 부분만 현재 코드로 관찰된다는 것이다. 즉 review queue 생성은 구현돼 있지만, 사람 승인/재학습/재빌드 전체 closed loop는 본 파일들의 직접 범위를 넘어선다.

### 5. 현재 파이프라인이 답하는 질문

#### 5.1 gold는 어디서 오는가?

- 미국: pilot `gold_title/*.json` 라벨 + `us_fmp_news_rich.parquet` 제목 조인
- 한국어 후보: BigKinds `econ_*.parquet` 제목 층화 샘플
- 한국어 최종 gold: 후보 + teacher labels 조인

#### 5.2 row는 어떻게 생겼는가?

- 미국 gold: `{gold_id,title,doc_class,event_types,published_at,source}`
- 미국 missing: `{gold_id,doc_class,event_types,reason,source}`
- 한국어 candidate: `{news_id,title,published_at,era,rubric_label,rubric_rule}`
- 한국어 teacher label: `{news_id,doc_class,event_type,confidence}`
- 한국어 gold/review: `{gold_id,title,doc_class,event_types,published_at,era,confidence,source}`

#### 5.3 missing/low-confidence는 어떻게 처리되는가?

- 미국: title을 복구하지 못한 행은 `_missing` 큐로 분리
- 한국어: `confidence == "L"`이면 자동 확정하지 않고 review queue로 분리

#### 5.4 stats는 무엇을 증명하는가?

- 미국: 41,920개 pilot label이 모두 복구되었고, 4-way class 분포가 기대치와 일치함
- 한국어: 6,000 teacher-labeled 후보가 4,295 gold / 1,705 review로 분기되었고, 현재 snapshot에서 join 누락과 bad class가 없었음 

#### 5.5 tests는 무엇을 덮는가?

- 미국: join/dedupe/missing/stats/determinism까지 전용 pytest가 덮음
- 한국어: 현재 저장소에서 전용 pytest 계약은 관찰되지 않았고, 현행 계약은 스크립트 docstring·필드 shape·생성 산출물에 의존한다

## Alternatives

1. **미국/한국어 gold를 한 파이프라인으로 설명하지 않고 스크립트별로 분리**
 - 장점: 파일별 구현 세부를 더 길게 풀 수 있다.
 - 단점: 사용자가 “gold가 어디서 오고 어디서 갈라지는가”를 한 번에 보기 어렵다.
2. **legacy spec 중심으로 문서를 쓰고 실제 코드/산출물은 부록으로 미루기**
 - 장점: review loop와 장기 로드맵을 더 풍부하게 서술할 수 있다.
 - 단점: 현재 구현과 draft 의도가 섞여, 무엇이 오늘 돌아가고 무엇이 아직 제안인지 흐려진다.
3. **현재 문서 기준: 구현 코드와 산출물을 주축으로 설명하고, legacy spec/diagram은 의도와 주변 맥락만 보조로 사용**
 - 장점: acceptance 질문에 가장 직접적으로 답할 수 있다.
 - 단점: closed-loop 전체 설명은 일부만 다루게 된다.

## Risks

- **draft-구현 혼동 위험**: `news_event_integration_spec.md`는 review loop 전체와 후속 적응 단계를 설명하지만, 현재 이 문서 범위에서 직접 구현이 확인되는 것은 gold generation과 review queue 생성까지다.
- **한국어 검증 사각지대 위험**: [INFERENCE] 미국은 결정성까지 pytest로 잠가 두었지만, 한국어는 같은 수준의 자동 계약이 아직 보이지 않는다.
- **fixture/generated 분류 혼선 위험**: `data/fixtures/events/README.md`는 `ko_gold_title.jsonl`과 `ko_gold_candidates.jsonl`을 fixture로 분류하지만, 코드 관점에서는 재생성 가능한 산출물이기도 하다. 문서가 이 이중 성격을 함께 설명하지 않으면 유지보수자가 source of truth를 오해할 수 있다.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 문서 규칙 | `docs/README.md` | canonical design 위치와 섹션 규칙 |
| 미국 generator / test | `src/alphamale/events/gold/build.py`<br>`tests/events/test_build_gold.py` | title recovery, stats, determinism 계약 |
| 한국어 generator | `src/alphamale/events/gold/sample.py`<br>`src/alphamale/events/gold/ko.py` | candidate sampling, teacher-label join, gold/review split |
| 자산 분류 | `data/fixtures/events/README.md` | fixture, generated report, generated output 분류 |
| 미국 산출물 | `data/fixtures/events/gold_title.jsonl`<br>`data/fixtures/events/gold_title_missing.jsonl`<br>`data/manifests/events/gold_title_stats.json` | US gold row shape, missing queue, stats provenance |
| 한국어 산출물 | `data/interim/events/ko_gold_candidates.jsonl`<br>`data/interim/events/ko_teacher_labels_auto.jsonl`<br>`data/fixtures/events/ko_gold_title.jsonl`<br>`data/interim/events/ko_review_queue.jsonl`<br>`data/manifests/events/ko_gold_stats.json` | KO candidate/teacher/gold/review/stats shape |
| legacy spec | `docs/archive/events/news-event-integration-spec.md` | EN gold와 KO cold-start 분리 배경, deferred closed loop |
| diagram artifact | `docs/archive/diagrams/events/news-event-5-7-flywheel.drawio`<br>`docs/archive/diagrams/graph/alphamale-graph-architecture.drawio` | review/retrain intent와 BigKinds 상류 맥락 |

## Rollout

1. 이 문서는 현재 저장소의 gold generation entry point로 읽는다. 새 독자는 먼저 여기서 구현 경계와 artifact 종류를 파악한 뒤, 세 스크립트와 stats 파일로 내려가면 된다.
2. 미국 gold 파이프라인이 바뀌면 `build_gold.py`와 `tests/events/test_build_gold.py`를 함께 갱신해야 한다. 현재 문서도 그 계약을 기준으로 유지한다.
3. 한국어 파이프라인의 current canonical KO source는 `ko_gold_title.jsonl`과 `ko_review_queue.jsonl`이다. `ko_gate_gold_all.jsonl`과 `ko_gold_title_v2.jsonl`은 별도 빌더가 문서화되기 전까지 derived fixture로 유지한다.

## Pending Decisions

1. 후속 승인 전까지는 `ko_gate_gold_all.jsonl`을 별도 빌더가 추가되기 전까지 derived/legacy gate fixture로 두는 안을 기준 후보로 둔다. 현재 title-gold 생성기의 canonical 산출물로는 취급하지 않는다.
2. 후속 승인 전까지는 canonical KO source 후보를 `ko_gold_title.jsonl`과 `ko_review_queue.jsonl`로 둔다. `ko_gold_title_v2.jsonl`은 후속 파생 fixture이며, generator source of truth를 대체하지 않는 방향을 유지한다.
3. 한국어 deterministic pytest는 후속 승인 전까지 **다음 generator 변경과 함께 추가 후보로 남겨 둔다**. 현재 근거는 `sample_ko_gold.py`/`build_ko_gold.py` 구현, 체크인된 artifact, 그리고 metrics registry snapshot이며, 이것이 현행 검증 경계다.
4. 후속 승인 전까지는 title-level gold와 legacy body gold 9,864건, 그리고 review 이후 재학습/재빌드 closed loop를 현재 생성기 범위 밖 항목으로 남겨 둔다. 이 연결은 legacy spec의 deferred implementation contract로 두고, 본 문서는 현행 title-gold builders와 산출물까지만 canonical 후보 범위로 다룬다.
