---
doc_type: design
status: Draft
owner: event-research
created: 2026-07-08
updated: 2026-07-11
related:
  - STATE.md
  - golden-data-generation.md
  - event-ontology.md
---
# 골든 데이터 학습·추론 파이프라인

> **범위 메모:** 본문에 적힌 코드·데이터 경로는 원본 개발 저장소 기준이다. 이 문서 저장소에는 해당 실행 파일이 포함되지 않을 수 있다.

## Summary

이 문서는 골든 데이터가 head 학습으로 들어가고, 월별 epoch runtime을 거쳐 deterministic mapping과 canonical assembly로 이어지는 현재 추론 경로를 한 곳에 정리한다. 구현 경로는 `train_heads.py` → `score_titles.py`/`sweep_gate.py` → `epoch_runner.py` → `map_events.py`/`assemble_events.py`이며, `exploration_bench.py`는 같은 문제를 다루는 별도 연구 하네스다. 세부 근거는 문서 끝 `근거/출처`를 참조한다.

현재 구현 경계도 분명하다. `epoch_runner.py`는 그래프 투영 없이 월별 제목 배치에 대해 gate/classify/log/review만 수행하고, CLI는 아직 `--stub` smoke run만 허용한다. 반면 `exploration_bench.py`는 projection decision과 action ledger까지 평가하므로, bench의 `EVENT_ONLY`/`PENDING_EDGE`/`DURABLE_EDGE`를 production epoch 출력과 동일시하면 안 된다.

## Context

이 문서는 `docs/research/event-modeling/` 아래의 current research design 문서이며, canonical frontmatter/section 순서를 따른다.

입력 자산 분류는 `data/fixtures/events/README.md`가 제공한다. 여기서 gold fixture, generated report, generated output을 구분하고, 본 문서는 그 분류 위에서 학습·runtime·후처리·bench의 책임 경계를 설명한다. 현재 관찰 가능한 gold 통계와 epoch/mapping/bench 산출물은 아래 generated blocks와 문서 끝 `근거/출처`를 기준 근거로 사용한다.

## Problem

현재 저장소에는 학습, 점수화, 월별 처리, 결정론적 후처리, 구조화 탐색 벤치가 모두 존재하지만 책임 경계가 서로 다르다. `train_heads.py`는 gold 기반 title classifier 학습기이고, `score_titles.py`/`sweep_gate.py`는 체크포인트를 다시 적용하는 보조기이며, `epoch_runner.py`는 월별 BigKinds 배치를 처리하는 러너이고, `map_events.py`/`assemble_events.py`는 accepted 이벤트를 후처리하며, `exploration_bench.py`는 fixture 기반의 추론·투영·채점 하네스다.

또한 adaptive review loop의 “전체 설계”와 “현재 코드”를 구분하지 않으면 오해가 생긴다. legacy 통합 스펙은 append-only gold store, ontology minor bump, replay, audit까지 포함한 fuller loop를 설명하지만, 현재 `epoch_runner.py`가 실제로 남기는 것은 `_epoch_state.json`, `review_queue.jsonl`, `epoch_out/*.parquet`, `gate_log/*.parquet`이며 CLI는 stub smoke-run만 연다.

## Goals

- 골든 데이터가 gate/type 헤드에 어떻게 들어가는지 명확히 설명한다.
- scoring output이 무엇이고, calibration이 어디에서 수행되는지 설명한다.
- `epoch_runner.py`가 월별 배치를 어떻게 accept/drop/review로 나누는지와 현재 CLI 경계를 설명한다.
- accepted 이벤트가 어떻게 mapped row와 canonical event로 바뀌는지 설명한다.
- `exploration_bench.py`가 왜 production epoch 출력과 다른 별도 하네스인지 구분한다.

## Non-goals

- 이 문서는 코드나 데이터 파일을 수정하지 않는다.
- 이 문서는 `build_gold.py`, `sample_ko_gold.py`, `build_ko_gold.py`의 생성 로직 자체를 상세 설계로 다시 풀지 않는다. 여기서는 그 산출물이 학습·추론 경로에서 어떻게 소비되는지에 집중한다.
- 이 문서는 legacy 통합 스펙의 append-only `gold_store.jsonl`·ontology replay 설계를 현재 코드가 모두 구현했다고 주장하지 않는다.

## Current State and Proposed Pipeline Contract

### 1. 파이프라인 책임 경계

| ERD 구간 | 필요한 입력 테이블/아티팩트 | 최종 출력 마트/브리지 | 중간 산출(최종 아님) | 상태/owner |
|---|---|---|---|---|
| head 학습 | `data/fixtures/events/gold_title.jsonl`, `data/fixtures/events/ko_gold_title.jsonl`, `data/interim/events/ko_gate_gold_all.jsonl` | `<out>/best.pt` | `<out>/eval_report.json` | current |
| title score/calibration | titles jsonl input, gate/type checkpoint(`best.pt`), gold holdout split | scored jsonl(`--out`) | `<ckpt_dir>/gate_sweep.json` | current |
| month runtime accepted stream | `data/raw/news/bigkinds/econ_*.parquet` logical month batch, gate/type model 함수, ontology registry logical table | `epoch_out/<month>.parquet` | `data/interim/events/_epoch_state.json` | current, CLI stub-only |
| month runtime review/log | `data/raw/news/bigkinds/econ_*.parquet` logical month batch, gate/type model 함수, ontology registry logical table | `data/interim/events/review_queue.jsonl`, `gate_log/<month>.parquet` | 없음 | current, review/log sink |
| deterministic post-process | `epoch_out/<month>.parquet`, titles parquet, optional content parquet, ontology registry logical table | `mapped_*.parquet`, `news_events_*.jsonl` | `mapped_*.gauge.json`, `news_events_*.cov.json` | current |
| exploration audit harness | `data/fixtures/events/exploration_bench/benchmark_cases_v0_1.jsonl`, `data/fixtures/events/exploration_bench/scoring_rubric_v0_1.json`, `data/fixtures/events/exploration_bench/hq_registry_v0_1.json`, packaged `event_type_profiles_v0_1.json` logical table | `data/manifests/events/exploration_bench_report_v0_1.json` | per-case bench result logical artifact | current, audit/bench-only |
| legacy adaptive replay | `data/interim/events/review_queue.jsonl`, review cluster logical table, accepted/replay candidate epochs [INFERENCE] | `[INFERENCE] gold_store.jsonl`, `[INFERENCE] news_event_audit`, `[INFERENCE] replayed epoch output` | `[INFERENCE] news_gate_log` | [INFERENCE], legacy/draft |

핵심 구분은 **production-like 배치 출력**과 **research/bench 출력**이 다르다는 점이다. `epoch_runner.py`는 `epoch_out`, `gate_log`, `review_queue`, `_epoch_state.json`를 남기고 끝나며, `exploration_bench.py`는 `result.labels.trace.actions.data_gaps`와 aggregate score를 담은 보고서를 남긴다.
#### raw input requirement → downstream output bridge

| raw input requirement | downstream output | bridge rule |
|---|---|---|
| gold fixture row (`gold_title.jsonl` / `ko_gold_title.jsonl`) | `<out>/best.pt`, `<out>/eval_report.json` | `train_heads.py`가 `doc_class`/`event_types[0]`를 라벨로 읽어 head artifact를 기록 |
| titles jsonl + checkpoint(`best.pt`) | scored jsonl(`--out`) | `score_titles.py`가 `p_event`, `type_top1`, `type_p`, `type_margin`을 산출 |
| gold holdout split + gate checkpoint | `<ckpt_dir>/gate_sweep.json` | `sweep_gate.py`가 threshold별 drop/FN sweep report를 저장 |
| `data/raw/news/bigkinds/econ_*.parquet` month batch + model 함수 + ontology registry | `gate_log/<month>.parquet`, `epoch_out/<month>.parquet`, `review_queue.jsonl`, `_epoch_state.json` | `epoch_runner.py`가 drop/accept/review/state sink를 결정 |
| accepted epoch rows + titles/body + ontology logical table | `mapped_*.parquet`, `news_events_*.jsonl` | deterministic post-process가 enrichment row와 canonical-event sink를 분리 기록 |
| `data/fixtures/events/exploration_bench/benchmark_cases_v0_1.jsonl` + rubric + HQ registry + profiles logical table | per-case bench result, `data/manifests/events/exploration_bench_report_v0_1.json` | research harness이며 production epoch sink를 대체하지 않음 |

### 2. 골드가 gate/type 헤드로 들어가는 방식

#### 2.1 학습 입력 계약

`train_heads.py`의 `load_rows()`는 `--task gate`일 때 각 gold row의 `doc_class`를 label로 사용하고, `--task type`일 때는 `doc_class == "EVENT"`인 행만 남긴 뒤 `event_types[0]`을 label로 사용한다. type task에서 지원 수가 `--min-support`보다 적은 라벨은 `OTHER_REVIEW`로 접어 rare type이 자동 확정되지 않도록 만든다.

이 계약은 gold row 스키마와 직접 연결된다. `tests/events/test_build_gold.py`는 gold row가 `{gold_id, title, doc_class, event_types, published_at, source}` 형식임을 고정하고, 실제 생성된 통계는 41,920행·`EVENT` 13,794행을 기록한다.
아래 예시는 실제 체크인 gold row를 따른다.

**필드 설명 — 학습 입력 gold row**

| 필드 | 의미 |
|---|---|
| `gold_id` | gold 행의 안정 키 |
| `title` | 분류기에 들어가는 제목 문자열 |
| `doc_class` | gate task의 정답 라벨 |
| `event_types` | type task에서 첫 원소를 읽는 type 목록 |
| `published_at` | 기사 시각, 없으면 `NULL` |
| `source` | gold 생성 경로 태그 |

<details><summary>예시 JSON (학습 입력 gold row)</summary>

```json
{"gold_id":"00075b59da5a9f70c9b16cf2ffe8bb44","title":"Apple launches iPhone 17e with prices starting at $599","doc_class":"EVENT","event_types":["COMPANY.PRODUCT.LAUNCH"],"published_at":null,"source":"pilot_title_v1"}
```

</details>

#### 2.2 학습 산출물과 평가 의미

학습기는 stratified holdout split을 만들고, `teacher_relative_pct`, accuracy, macro-F1, confusion을 `eval_report.json`에 남기며, 가장 좋은 epoch의 `best.pt`를 저장한다. 문서상 teacher는 gold를 만든 teacher model/codex이므로 여기서의 accuracy는 teacher-relative fidelity다.
아래 JSON 토글은 현재 snapshot 또는 현재 저장소에서 관찰 가능한 실제 값으로 채웠다.

**필드 설명 — `eval_report.json` 핵심 필드**

| 필드 | 의미 |
|---|---|
| `teacher_relative_pct` | teacher 라벨 대비 일치율 |
| `accuracy` | holdout 정확도 |
| `macro_f1` | 클래스 불균형을 반영한 평균 F1 |

<details><summary>예시 JSON (`eval_report.json` 핵심 값)</summary>

```json
{"teacher_relative_pct":92.44,"accuracy":0.9244,"macro_f1":0.9159}
```

</details>

**필드 설명 — `score_titles.py` 출력 row**

| 필드 | 의미 |
|---|---|
| `p_event` | gate softmax의 `EVENT` 확률 |
| `type_top1` | 가장 높은 확률의 event type |
| `type_p` | `type_top1`의 확률 |
| `type_margin` | top1-top2 확률 차이 |

<details><summary>예시 JSON (`score_titles.py` 출력 필드명 기준 실제 값 묶음)</summary>

```json
{"p_event":0.08698,"type_top1":"POLICY.REGULATION.RULE_CHANGE","type_p":0.81389,"type_margin":0.30311}
```

</details>

**필드 설명 — `gate_sweep.json`의 `sweep[]` row**

| 필드 | 의미 |
|---|---|
| `t_lo` | reject 경계를 계산할 threshold |
| `drop_rate_pct` | 전체 dev row 중 drop 비율 |
| `event_fn_rate_pct` | 실제 `EVENT`를 drop한 비율 |
| `events_dropped` | drop된 실제 `EVENT` 건수 |

<details><summary>예시 JSON (`gate_sweep.json`의 `sweep[]` row)</summary>

```json
{"t_lo":0.02,"drop_rate_pct":40.71,"event_fn_rate_pct":0.363,"events_dropped":5}
```

</details>

<!-- metrics:start pipeline-ml-heads -->
**Current ML and LLM evaluation snapshot**

Authoritative values for the currently cited gate/type and ETF-view evaluation artifacts.

_Generated from `data/manifests/events/model_metrics.yaml` via `uv run alphamale events gold metrics-sync render-docs`._

| Metric ID | Snapshot |
|---|---|
| `events.ml.gate_bin_v2` | teacher_relative_pct=92.44; accuracy=0.9244; macro_f1=0.9159; n_eval=4191 |
| `events.ml.gate_bin_v2.sweep` | selected_t_lo=0.002; selected_drop_rate_pct=40.71; selected_event_fn_rate_pct=0.363; n_dev=4191; n_event=1379 |
| `events.ml.gate_ko_v5` | teacher_relative_pct=89.49; accuracy=0.8949; macro_f1=0.8863; n_eval=1999 |
| `events.ml.type_ko_v2` | teacher_relative_pct=90.91; accuracy=0.9091; macro_f1=0.8987; n_eval=121 |
| `events.llm.etf_view.2021_06` | dir_acc_3way=0.333; dir_acc_updown=0.429; n_trading_days=22; n_news_days=21; up_bias=15/21 |
<!-- metrics:end -->

#### 2.4 현재 한국어 fixture 경계

`data/fixtures/events/README.md`는 `ko_gold_title.jsonl`, `ko_gold_title_v2.jsonl`을 “한국어 gate/title gold 라벨 자산”, `ko_gate_gold_all.jsonl`을 “gate/후보 평가용 gold fixture”로 분류한다. 현재 운영 계약에서는 이 자산들이 fixture 분류와 별개로 **명시적 `--gold` 입력을 통해 first-class 학습/평가 자산**이 되며, 문서 예시는 대표 입력으로 `gold_title.jsonl`을 사용한다.

### 3. 월별 epoch runner의 accept / drop / review 루프

#### 3.1 월 슬라이스와 상태 관리

`iter_epochs()`는 BigKinds parquet에서 `news_id`, `published_at`, `title`만 읽고, `published_at` 기준으로 정렬한 뒤 **oldest-first 월별 DataFrame**을 만든다. `run_epoch()`는 각 DataFrame이 정확히 한 달치라고 가정하며, `_epoch_state.json`의 `done_months`를 읽어 이미 처리된 월은 `force=False`일 때 건너뛴다.

현재 생성물은 이 상태를 실제로 남기고 있다. `_epoch_state.json`에는 `2006-06`, `2021-06`이 완료 월로 기록돼 있다. 테스트도 월 grouping, skip-after-done, force rerun을 고정 검증한다.
아래 JSON 토글은 실제 체크인 산출물 또는 실제 산출 column에서 관찰한 값을 따른다.

**필드 설명 — 월별 입력 row**

| 필드 | 의미 |
|---|---|
| `news_id` | 월 배치에서 쓰는 기사 식별자 |
| `published_at` | 월 정렬 기준 시각 |
| `title` | gate/type에 전달할 제목 |

<details><summary>예시 JSON (월별 입력 row)</summary>

```json
{"news_id":"04104008.20060609010140002","published_at":"2006-06-09 01:01:40","title":"건교부 간부 '강남 주택공급 확대론' 정면 반박"}
```

</details>

**필드 설명 — `_epoch_state.json`**

| 필드 | 의미 |
|---|---|
| `done_months` | 이미 처리 완료로 기록된 월 목록 |
| `updated_at` | 상태 파일 최종 갱신 시각 |

<details><summary>예시 JSON (`_epoch_state.json`)</summary>

```json
{"done_months":["2006-06","2021-06"],"updated_at":"2026-07-03T10:54:37+00:00"}
```

</details>

**필드 설명 — `gate_log/<month>.parquet`**

| 필드 | 의미 |
|---|---|
| `news_id` | 판정된 기사 식별자 |
| `stage` | gate 단계 코드 |
| `action` | gate 결과 |
| `reason` | gate label 또는 drop 사유 |
| `score` | gate confidence score |
| `model_version` | gate 모델 버전 태그 |
| `decided_at` | UTC 판정 시각 |

<details><summary>예시 JSON (`gate_log/<month>.parquet`)</summary>

```json
{"news_id":"04104008.20060609010144002","stage":"G2","action":"pass","reason":"EVENT","score":0.08698,"model_version":"EN-bin-v2","decided_at":"2026-07-03T10:46:38+00:00"}
```

</details>

**필드 설명 — accepted row (`epoch_out/<month>.parquet`)**

| 필드 | 의미 |
|---|---|
| `news_id` | accepted 기사 식별자 |
| `published_at` | accepted 이벤트의 기준 시각 |
| `event_type` | accept된 ontology leaf |
| `top1` | type head의 top1 확률 |
| `gate_model_version` | gate 모델 버전 |
| `type_model_version` | type 모델 버전 |

<details><summary>예시 JSON (`epoch_out/<month>.parquet`)</summary>

```json
{"news_id":"04104008.20060609010140002","published_at":"2006-06-09 01:01:40","event_type":"POLICY.REGULATION.RULE_CHANGE","top1":0.81389,"gate_model_version":"EN-bin-v2","type_model_version":"ko-type-v2-balanced"}
```

</details>

**필드 설명 — review row (`review_queue.jsonl`)**

| 필드 | 의미 |
|---|---|
| `news_id` | review 대상 기사 식별자 |
| `published_at` | review 대상 기사 시각 |
| `title` | 사람이 다시 읽을 제목 |
| `reason` | review를 만든 사유 집합 |
| `top1` | top1 확률 |
| `margin` | top1-top2 margin |
| `model_versions.gate` | gate 모델 버전 태그 |
| `model_versions.type` | type 모델 버전 태그 |
| `decided_at` | append 시각 |

<details><summary>예시 JSON (`review_queue.jsonl`)</summary>

```json
{"decided_at":"2026-07-03T09:01:20+00:00","margin":0.30311,"model_versions":{"gate":"xlmr-bin-v2-EN-interim(t_lo=0.002)","type":"xlmr-type-en-v1"},"news_id":"04104008.20060609010140002","published_at":"2006-06-09 01:01:40","reason":"low_top1|unknown_type","title":"건교부 간부 '강남 주택공급 확대론' 정면 반박","top1":0.36641}
```

</details>

<details><summary>예시 JSON (month runtime 출력 계약 묶음)</summary>

```json
{
 "epoch_out_row": {"news_id":"04104008.20060609010140002","published_at":"2006-06-09 01:01:40","event_type":"POLICY.REGULATION.RULE_CHANGE","top1":0.81389,"gate_model_version":"EN-bin-v2","type_model_version":"ko-type-v2-balanced"},
 "review_queue_row": {"news_id":"04104008.20060609010140002","reason":"low_top1|unknown_type","top1":0.36641,"margin":0.30311},
 "_epoch_state": {"done_months":["2006-06","2021-06"],"updated_at":"2026-07-03T10:54:37+00:00"}
}
```

</details>

#### 3.4 출력 계약과 현재 CLI 경계

성공적으로 accepted된 행은 `epoch_out/<month>.parquet`로, review 행은 append-only `review_queue.jsonl`로, state는 `_epoch_state.json`로 저장된다. `data/fixtures/events/README.md`도 `epoch_out/*.parquet`, `gate_log/*.parquet`, `review_queue.jsonl`을 generated output으로 분류한다.

<details><summary>예시 JSON (3.4 출력 계약 실제 값 묶음)</summary>

```json
{
 "epoch_out_row": {"news_id":"04104008.20060609010140002","published_at":"2006-06-09 01:01:40","event_type":"POLICY.REGULATION.RULE_CHANGE","top1":0.81389,"gate_model_version":"EN-bin-v2","type_model_version":"ko-type-v2-balanced"},
 "review_queue_row": {"decided_at":"2026-07-03T09:01:20+00:00","margin":0.30311,"model_versions":{"gate":"xlmr-bin-v2-EN-interim(t_lo=0.002)","type":"xlmr-type-en-v1"},"news_id":"04104008.20060609010140002","published_at":"2006-06-09 01:01:40","reason":"low_top1|unknown_type","title":"건교부 간부 '강남 주택공급 확대론' 정면 반박","top1":0.36641},
 "_epoch_state": {"done_months":["2006-06","2021-06"],"updated_at":"2026-07-03T10:54:37+00:00"}
}
```

</details>

다만 CLI는 아직 실모델 어댑터를 직접 배선하지 않는다. `main()`은 `--stub`가 없으면 `"Model-backed gate_fn/type_fn are not wired yet"`로 종료하고, stub registry/stub gate/stub type만 허용한다. 따라서 현재 실운영 계약은 **`run_epoch()` 함수 시그니처**이고, CLI는 smoke-run 전용 경계다.

### 4. accepted 이벤트에서 mapped row와 canonical event로 가는 결정론적 후처리

#### 4.1 `map_events.py`: accepted → ticker/theme row

`map_events.py`는 `epoch_out` accepted events를 읽고, `titles` parquet와 조인한 뒤 **content source가 구성된 실행에서는 `--content-glob`를 통해 본문 일부를 결정론적으로 보강**한다. 그 다음 `news_match.link_article()`로 entity ticker를, `normalize.product_to_concept()`로 theme concept를 정하고, theme concept가 비는 경우 `--sector-fallback`가 켜진 프로파일에서는 `sector_fallback` 정책으로 sector group을 `sec:<group>` theme로 채운다.

출력 row는 `issuer_ticker`, `tickers`, `n_tickers`, `theme_concept`, `theme_method`를 포함하고, 별도 gauge JSON에는 `entity_hit_pct`, `theme_hit_pct`, `either_hit_pct`, `unmapped_pct`, `top_event_types`가 기록된다. 현재 비교에 쓰는 mapping coverage 수치는 아래 generated block을 authoritative snapshot으로 삼는다.
현재 관찰 가능한 구현이 남기는 fallback lineage는 row-level `theme_method`와 aggregate gauge JSON까지다. 즉 이 문서가 현재 코드에 대해 직접 주장할 수 있는 사실은 `theme_method`가 `sector_fallback`/`miss` 같은 경로를 노출한다는 점이지, 별도 구조화 감사 레코드가 이미 구현됐다는 점이 아니다. 이 문서의 proposed contract는 이 기존 lineage를 확장해, `product_to_concept()` miss 뒤 `sector_fallback`가 발동했는지 또는 fallback까지 실패했는지를 **coverage 수치와 별개인 fallback audit record**로 남기자는 것이다. sector/theme은 enrichment 계층이므로 fallback miss가 나와도 accepted event 자체를 막지 않고, `theme_concept = NULL` / `theme_method = "miss"`와 함께 `UNKNOWN` 또는 gap 상태를 감사선에 남기는 경계로 둔다.
아래 gauge JSON은 실제 체크인 산출물을 따르고, mapped row 예시는 실제 산출 row를 그대로 옮겼다.

**필드 설명 — mapped row (`mapped_*.parquet`)**

| 필드 | 의미 |
|---|---|
| `news_id` | 후처리 대상 기사 식별자 |
| `event_type` | upstream accepted event type |
| `issuer_ticker` | 대표 issuer ticker, 없으면 `NULL` |
| `tickers` | 매핑된 ticker 집합 직렬화 |
| `n_tickers` | 매핑된 ticker 개수 |
| `theme_concept` | 정규화된 theme concept |
| `theme_method` | theme을 만든 규칙 경로 |

<details><summary>예시 JSON (`mapped_*.parquet` row)</summary>

```json
{"news_id":"04104008.20060609010140002","event_type":"POLICY.REGULATION.RULE_CHANGE","issuer_ticker":null,"tickers":"","n_tickers":0,"theme_concept":null,"theme_method":"miss"}
```

</details>

**필드 설명 — mapping gauge JSON**

| 필드 | 의미 |
|---|---|
| `accepted_events` | mapping 입력 accepted 이벤트 수 |
| `entity_hit_pct` | entity ticker가 붙은 비율 |
| `theme_hit_pct` | theme concept가 붙은 비율 |
| `either_hit_pct` | entity 또는 theme 중 하나라도 붙은 비율 |
| `unmapped_pct` | 둘 다 비어 있는 비율 |
| `top_event_types` | coverage 집계에 많이 등장한 type 상위 목록 |
| `out` | row parquet 산출 경로 |

<details><summary>예시 JSON (mapping gauge)</summary>

```json
{"accepted_events":58604,"entity_hit_pct":29.3,"theme_hit_pct":30.6,"either_hit_pct":47.9,"unmapped_pct":52.1,"top_event_types":[["COMPANY.PRODUCT.LAUNCH",16054],["COMPANY.ALLIANCE.PARTNERSHIP",8580]],"out":"data/interim/events/mapped_2021-06_body.parquet"}
```

</details>

#### 4.1.1 fallback provenance 최소 계약

fallback path는 coverage metric만 남기고 끝나면 안 된다. `map_events.py`, `assemble_events.py`, `exploration_bench.py`에서 쓰는 모든 fallback은 아래 최소 필드를 가진 구조화 audit record를 남겨야 한다.

| 필드 | 최소 의미 |
|---|---|
| `fallback_id` | fallback 종류의 안정 식별자 (`sector_theme_v1`, `deterministic-v1-title-only`, `cue-table-complete-fn` 같은 이름) |
| `fallback_logic` | 어떤 규칙/함수/프롬프트 경로를 탔는지 |
| `trigger_condition` | 왜 기본 경로가 아니라 fallback으로 내려왔는지 |
| `input_evidence_ref` | `news_id`, `title`, `body_ref`, `asof` 등 fallback이 실제로 읽은 근거 위치 |
| `selected_output` | 선택된 theme/event_type/assembler 또는 명시적 `NULL` |
| `admissibility` 또는 `decision` | `admissible_baseline`, `enrichment_only`, `review_only`, `REVIEW_QUEUE` 같은 판정 |
| `confidence_or_status` | `resolved`, `UNKNOWN`, `failed` 또는 동등 confidence/status |
| `recorded_at` 또는 `asof` | 실행 시각 또는 PIT 기준 시각 |

이 최소 필드 집합은 gauge/cov report를 대체하지 않고 보완한다. aggregate coverage는 “얼마나 자주 일어났는가”를 설명하고, fallback audit record는 “왜 그 경로를 탔고 무엇을 산출했는가”를 사건 단위로 재구성하게 해 준다.

중요한 점은 이 단계가 **모델 재추론을 하지 않는다**는 것이다. docstring도 “No model, KO-native”라고 못 박고, 구현은 `epoch_out`에서 이미 정해진 `event_type`을 그대로 옮긴다.

#### 4.2 `assemble_events.py`: accepted → canonical-event-1.0 JSON

`assemble_events.py`는 동일한 `epoch_out` accepted events를 titles/body와 조인해 `canonical-event-1.0` JSONL을 만든다. 이때 ontology registry의 default predicate와 required roles를 읽고, ticker 링크를 event-type별 템플릿 역할(`ACQUIRER`, `SUPPLIER`, `PARTNER`, `ISSUER` 등)로 배정하며, 정규식 기반 `QUANTITY`와 `REPORTING_PERIOD`도 추출한다.

조립 결과 event 객체에는 `event_type_id`, `proposition.predicate_id`, `arguments`, `completeness`, `evidence`가 들어간다. `completeness`는 ontology required role 충족 여부에 따라 `complete`/`partial`로 정해지고, body가 제공된 실행의 기본 경로는 `deterministic-v2-lead`, body가 없는 baseline은 `deterministic-v1` fallback baseline으로 남는다. 테스트도 `epoch_out`의 `published_at`을 titles parquet보다 우선하고, `content_glob` 사용 시 `deterministic-v2-lead` 표기를 검증한다.
현재 체크 가능한 구현 증거는 `evidence.assembler`가 `deterministic-v2-lead`와 `deterministic-v1`을 구분한다는 점까지다. 따라서 이 문서가 요구하는 provenance 확장은 “이미 구현됐다”는 주장이 아니라, 기존 `assembler` lineage를 감사 레코드로 승격하는 proposed contract다. 본문이 없어 `deterministic-v1` title-only baseline으로 내려간 경우에는 `fallback_id = deterministic-v1-title-only`, `trigger_condition = body_unavailable`, `selected_output = assembler:deterministic-v1`, `admissibility = admissible_baseline`, `input_evidence_ref = {news_id,title,published_at}` 같은 audit record를 남겨야 한다. 이 경로는 body 부재만으로 canonical-event 생성을 막지 않는 passable baseline이지만, fallback 이후에도 required role이 비어 `completeness = partial`이 되면 그 부분충족 상태 역시 같은 provenance 계열에서 추적 가능해야 한다.
아래 canonical event와 coverage 예시는 실제 체크인 샘플을 따른다.

**필드 설명 — canonical-event document row**

| 필드 | 의미 |
|---|---|
| `schema_version` | event 문서 스키마 버전 |
| `ontology_version` | 사용한 ontology snapshot |
| `document_id` | 원문 기사 식별자 |
| `published_at` | event 문서 기준 시각 |
| `events` | 한 문서에서 조립된 canonical event 배열 |

<details><summary>예시 JSON (canonical-event document row)</summary>

```json
{"schema_version":"canonical-event-1.0","ontology_version":"2026-01","document_id":"04101808.20210604092213019","published_at":"2021-06-04 09:22:13","events":[{"event_type_id":"COMPANY.EARNINGS.RESULT_RELEASE","proposition":{"predicate_id":"REPORT","predicate_source":"ontology_default","subject_roles":["ISSUER"],"object_roles":[]},"arguments":[{"role_id":"ISSUER","mention":{"text":"삼성전자"},"normalized":{"kind":"ENTITY","entity_id":"ORG_KR_005930"},"role_source":"template"},{"role_id":"REPORTING_PERIOD","mention":{"text":"하반기","start_char":48,"end_char":51},"normalized":{"kind":"TIME","value":"2021H2","precision":"HALF"}}],"completeness":"complete","evidence":{"title":"'박스피' 탈출 시동…삼성전자·현대차가 끌고 간다","text_basis":"title+lead","assembler":"deterministic-v2-lead"}}]}
```

</details>

**필드 설명 — `events[]` 내부 객체**

| 필드 | 의미 |
|---|---|
| `event_type_id` | 조립된 ontology leaf |
| `proposition.predicate_id` | ontology default predicate |
| `arguments` | 역할별 정규화 argument 목록 |
| `completeness` | required role 충족 상태 |
| `evidence.title` | 근거 제목 |
| `evidence.text_basis` | title-only vs title+lead 근거 범위 |
| `evidence.assembler` | 조립 경로 버전 |

**필드 설명 — assembly coverage JSON**

| 필드 | 의미 |
|---|---|
| `assembled` | 조립된 canonical event 문서 수 |
| `has_entity_pct` | entity argument가 하나 이상 있는 비율 |
| `has_quantity_pct` | quantity argument가 하나 이상 있는 비율 |
| `has_time_pct` | time argument가 하나 이상 있는 비율 |
| `req_complete_pct` | required role이 모두 채워진 비율 |
| `out` | JSONL 산출 경로 |

<details><summary>예시 JSON (assembly coverage)</summary>

```json
{"assembled":58604,"has_entity_pct":29.3,"has_quantity_pct":37.5,"has_time_pct":17.0,"req_complete_pct":14.3,"out":"data/interim/events/news_events_2021-06_v2.jsonl"}
```

</details>

실제 coverage report와 샘플도 이 계약을 확인한다. coverage 수치 자체는 아래 generated block이 authoritative snapshot이고, 샘플 JSONL은 그 수치가 어떤 payload shape를 뜻하는지 보여 준다.
<!-- metrics:start pipeline-mapping-assembly -->
**Current mapping and assembly coverage snapshot**

Coverage values for deterministic mapping and canonical-event assembly; the prose below explains why they move.

_Generated from `data/manifests/events/model_metrics.yaml` via `uv run alphamale events gold metrics-sync render-docs`._

| Metric ID | Snapshot |
|---|---|
| `events.mapping.2006_06.full` | accepted_events=19388; entity_hit_pct=24.6; theme_hit_pct=44.9; either_hit_pct=57.1; unmapped_pct=42.9 |
| `events.mapping.assembly.2006_06.v2` | assembled=19388; has_entity_pct=24.6; has_quantity_pct=43.4; has_time_pct=16.2; req_complete_pct=11.5 |
| `events.mapping.2021_06.full` | accepted_events=58604; entity_hit_pct=29.3; theme_hit_pct=46.6; either_hit_pct=58.8; unmapped_pct=41.2 |
| `events.mapping.assembly.2021_06.v2` | assembled=58604; has_entity_pct=29.3; has_quantity_pct=37.5; has_time_pct=17; req_complete_pct=14.3 |
<!-- metrics:end -->

### 5. `exploration_bench.py`는 production epoch가 아니라 별도 구조화 추론 하네스다

#### 5.1 입력과 내부 흐름

`exploration_bench.py`는 `benchmark_cases_v0_1.jsonl`, `scoring_rubric_v0_1.json`, `hq_registry_v0_1.json`을 bench dir에서 읽고, event type profile은 `alphamale.events.ontology.load_profiles()`를 통해 packaged resource에서 읽는다. `explore_case()`는 headline+lead+asof를 받아 doc_class gate → type cue routing → configured `complete_fn` fallback → lifecycle stage/predicate → role resolution → corpus-grounded assertions/lineage → projection decision → action ledger → gaps를 남긴다. cue table이 라우팅하지 못했고 `complete_fn`이 없거나 답을 만들지 못하면 즉시 `event_type_unrouted` gap을 남기고 종료한다.
현재 bench 코드에서 직접 관찰 가능한 사실은 cue-table miss 뒤 `complete_fn`이 있으면 `_llm_card()`를 시도하고, 그래도 event type이 결정되지 않으면 `event_type_unrouted` gap과 `REVIEW_QUEUE` 결정을 남긴다는 점이다. 아직 개별 fallback 호출 자체를 `fallback_id`/`trigger_condition` 형태의 전용 구조 필드로 내보내는 구현 증거는 보이지 않는다. 따라서 proposed contract에서는 cue-table miss → `complete_fn` 호출 → 성공/실패를 모두 audit-recorded해야 하며, 성공 시에도 선택된 `event_type_id`와 `review_only`인지 `admissible`인지가 남아야 하고, fallback이 없거나 실패한 경우에는 지금처럼 `event_type_unrouted` gap과 `REVIEW_QUEUE`가 최종 결정으로 남아야 한다.
아래 bench 입력/출력 예시는 실제 fixture와 실제 aggregate report를 따른다.

**필드 설명 — bench case 입력**

| 필드 | 의미 |
|---|---|
| `headline` | 구조화 추론의 주 headline |
| `lead` | headline을 보강하는 lead 문장 |
| `asof` | PIT guard가 지키는 기준 시각 |

<details><summary>예시 JSON (bench case 입력)</summary>

```json
{"headline":"Micron begins volume shipment of 1-alpha DRAM and expands LPDDR4x supply","lead":"The report says Micron has moved its 1-alpha DRAM from initial shipment to broader commercial supply for mobile memory customers.","asof":"2021-06-08T09:00:00+09:00"}
```

</details>

#### 5.2 출력과 채점

bench 결과는 `epoch_out`이 아니라 `result.events`, `labels`, `trace`, `actions`, `data_gaps`를 포함한 per-case 구조와 aggregate score report다. scorer는 hard fail family와 8개 점수 항목을 계산하고, projection forbidden check까지 본다. 현재 문서에서 인용하는 aggregate bench 수치는 아래 generated block을 authoritative snapshot으로 삼는다.

**필드 설명 — per-case bench result**

| 필드 | 의미 |
|---|---|
| `case_id` | fixture 케이스 식별자 |
| `passed` | rubric 통과 여부 |
| `trace_coverage` | 기대 추론 단계 충족률 |
| `grounded` | corpus 근거를 실제 사용했는지 |
| `result.doc_class` | 문서 수준 gate 결과 |
| `result.events[0].event_type_id` | 추출된 대표 event type |
| `labels.projection_decision` | projection stage-gate 결과 |
| `labels.allowed_to_project` | projection 허용 여부 |
| `actions` | graph mutation ledger |
| `data_gaps` | 정직하게 남긴 미충족 정보 |

<details><summary>예시 JSON (per-case bench result)</summary>

```json
{"case_id":"TECH_PRODUCT_MICRON_1ALPHA_COMMERCIAL_SUPPLY_2021","passed":true,"trace_coverage":0.333,"grounded":true,"result":{"doc_class":"EVENT","events":[{"event_type_id":"COMPANY.PRODUCT.LAUNCH","predicate_id":"LAUNCH","lifecycle_stage":"COMMERCIAL_SUPPLY","claim_modality":"ANALYST_VIEW","roles":{"ISSUER":"ORG_US_MICRON"}}]},"labels":{"projection_decision":"DURABLE_EDGE","allowed_to_project":true},"actions":["create_edge:produces:ORG_US_MICRON->DST"],"data_gaps":["exposure_master","graph_neighbors","market_observation","official_ir_origin_source"]}
```

</details>

**필드 설명 — aggregate bench report**

| 필드 | 의미 |
|---|---|
| `cases` | 총 평가 케이스 수 |
| `passed` | 통과 케이스 수 |
| `mean_points` | 평균 총점 |
| `mean_trace_coverage` | 평균 trace coverage |
| `grounded_cases` | corpus grounding이 성립한 케이스 수 |
| `hq_answerable_rate` | HQ 질문에 답 가능한 비율 |

<details><summary>예시 JSON (aggregate bench report)</summary>

```json
{"cases":36,"passed":36,"mean_points":14.5,"mean_trace_coverage":0.888,"grounded_cases":2,"hq_answerable_rate":0.12}
```

</details>

#### 5.3 production epoch와의 차이

| 항목 | `epoch_runner.py` | `exploration_bench.py` |
|---|---|---|
| 입력 단위 | 월별 BigKinds title batch (`news_id`, `published_at`, `title`) | fixture case의 `headline`, `lead`, `asof` + configured corpus |
| 핵심 판단 | gate pass/drop, type accept/review | event card, stage, projection decision, action ledger, gaps |
| 출력 | `gate_log`, `epoch_out`, `review_queue`, `_epoch_state.json` | `exploration_bench_report_v0_1.json` aggregate + per-case result |
| projection | out of scope | `EVENT_ONLY`, `PENDING_EDGE`, `DURABLE_EDGE`, `DURABLE_EDGE_UPDATE_VALID_TO`까지 평가하며, 이 규칙 집합은 production graph-projection contract의 shared reference다 |
| fallback | CLI는 stub만 허용 | cue table 미매칭 시 configured `complete_fn` fallback을 사용할 수 있고, 이 호출/성공/실패는 coverage 메모가 아니라 구조화 provenance로 남겨야 한다. fallback이 없거나 실패하면 `event_type_unrouted` gap과 `REVIEW_QUEUE` 결정을 남긴다 |

즉 bench는 **실제 월별 생산물 생성기**가 아니라, 구조화 추론 체계가 “어떤 event card를 만들고, 언제 투영을 막아야 하며, 어떤 gaps를 정직하게 남겨야 하는지”를 점수화하는 연구 하네스다. 다만 여기서 검증하는 profile/PIT/projection 규칙 자체는 production graph-projection contract의 공유 규칙으로 읽는다.

### 6. 현재 adaptive loop의 구현 경계와 legacy 설계 경계

현재 코드에서 구현된 adaptive carry-over는 세 가지다. 첫째, `epoch_runner.py`의 `review_queue.jsonl` append-only 누적. 둘째, `_epoch_state.json`의 완료 월 기록. 셋째, 각 review row와 gate log에 남는 `model_versions`/`decided_at` 감사 정보다.

반면 legacy 통합 스펙은 이보다 넓다. 그 문서는 애매 판정을 review queue로 보내고, 리뷰 결과를 append-only `gold_store.jsonl`에 축적하고, 동일 미지 패턴 cluster가 30건 이상이면 ontology leaf draft를 제안하고, 승인 시 ontology minor bump 후 replay로 소급 재분류하는 fuller loop를 설명한다. 현재 타깃 스크립트들 안에서 이 `gold_store.jsonl`/auto ontology bump/replay가 직접 구현된 증거는 보이지 않는다. 따라서 이 문서에서는 이를 **legacy design boundary**로 둔다.

### 7. 관련 다이어그램 해석

- `docs/archive/diagrams/events/news-event-structured-exploration.drawio` p0는 “뉴스 구조화 → 매핑 캐스케이드 → 증거 그래프 → LLM 구조화 탐색 → 판정 카드 → 적응 진화 루프 → 저장·감사”의 상위 시스템도를 제공한다. 이 문서의 전체 문맥에는 맞지만, production epoch 출력보다 bench/graph 확장까지 더 넓게 그린 그림이다.
- 같은 파일 p1은 `Document`, `Company`, `Event`, `Theme / Concept`, `View (SCD-2)`, `TechNode`, `Response priors`, `검색 인덱스`의 링크 구조를 보여 주며, `assemble_events.py`가 만드는 canonical event와 `map_events.py`의 entity/theme 정규화가 어떤 그래프에 닿는지 설명해 준다.
- p2는 `S1 앵커 추출`부터 `S8 최종 판정`, 그리고 `DATA_GAPS 기록 → backlog 생성 → 재실행`까지 이어지는 추론 플로우를 그린다. 이는 `exploration_bench.py`의 `explore_case()` 흐름과 가장 직접적으로 대응한다.
- `docs/archive/diagrams/graph/alphamale-graph-architecture.drawio` p0는 “이벤트 구조화”와 “그래프 조인으로 카테고리/답변 생성”의 개념 브리지를 제공하고, p1은 BigKinds·DART·LLM·`update_cycle`을 한 장에 묶어 월별 epoch 이후 파이프라인이 더 큰 그래프 시스템 어디에 매달리는지 보여 준다.
- 같은 파일 p2는 “뉴스→종목→이벤트 rubric→profile→중심테마”를 보여 주므로 `map_events.py`의 deterministic mapping과 가장 가깝고, p4는 draft 리뷰 큐, 승격/기각, 자기적응 원리를 보여 주므로 legacy adaptive loop 문맥을 이해하는 데 가장 가깝다.

문서에서 바로 참조할 수 있는 생성 export도 이미 있다. 구조화 탐색 계열은 `docs/archive/diagrams/events/news-event-structured-exploration-p0.svg`, `docs/archive/diagrams/events/news-event-structured-exploration-p1.svg`, `docs/archive/diagrams/events/news-event-structured-exploration-p2.svg`가 page별 대응 export이고, 그래프 아키텍처 계열은 `docs/archive/diagrams/graph/arch-0-concept.drawio.png`, `docs/archive/diagrams/graph/arch-1-overview.drawio.png`, `docs/archive/diagrams/graph/arch-2-news-pipeline.drawio.png`, `docs/archive/diagrams/graph/arch-4-dart-cycle.drawio.png`가 대응 이미지다.

## Alternatives

1. **[INFERENCE] 학습·점수화·월별 러너·후처리를 하나의 model-backed CLI로 통합**
 - 장점: 운영 진입점이 하나가 된다.
 - 단점: 현재 코드가 분리해 둔 calibration(`sweep_gate.py`), batch scoring(`score_titles.py`), month runtime(`epoch_runner.py`), deterministic post-process(`map_events.py`, `assemble_events.py`)의 역할이 섞인다. 현 저장소는 명확히 분리된 경로를 택하고 있다.
2. **현재처럼 split pipeline 유지**
 - 장점: 학습, calibration, runtime, 후처리, 벤치를 각각 독립 검증할 수 있다.
 - 단점: 문서가 없으면 역할 혼동이 쉽다.
 - 현재 관찰 결과는 이 대안이 실제 구현 상태다.
3. **[INFERENCE] exploration bench를 곧바로 production runner로 승격**
 - 장점: projection/gap logic를 더 일찍 production 판단에 반영할 수 있다.
 - 단점: 현재 bench는 fixture 기반 scorer이며, monthly `epoch_out`/`gate_log`/`review_queue` 출력 계약을 대체하지 않는다.

## Risks

- `[INFERENCE]` **gate calibration drift 위험**: `epoch_runner.py`는 gate score를 기록만 하고 pass/drop 결정은 `gate_fn` label에 위임하므로, upstream thresholding 규칙이 달라지면 월별 수율이 크게 흔들릴 수 있다.
- **review queue 팽창 위험**: `low_margin`, `low_top1`, `unknown_type`가 모두 append-only `review_queue.jsonl`로 누적된다. 실제 산출물에서도 unknown-type 비중이 높다.
- **후처리 커버리지 과신 위험**: `map_events.py`와 `assemble_events.py`는 모델 재추론 없이 결정론적으로 동작하므로 explainability는 높지만, coverage report가 보여주듯 초기 v1 coverage는 낮고 body-augmented v2도 partial이 남는다.
- **bench/production 혼동 위험**: bench의 projection safety와 HQ answerability 지표를 곧바로 epoch output completeness로 오해하면 책임 경계가 무너진다.

## 근거/출처

| 구분 | 경로/아티팩트 | 쓰임 |
|---|---|---|
| 문서 규칙 | `docs/README.md` | canonical design format과 evidence rule |
| gold fixture / report | `data/fixtures/events/README.md`<br>`tests/events/test_build_gold.py`<br>`data/manifests/events/gold_title_stats.json`<br>`data/manifests/events/ko_gold_stats.json` | gold 입력 스키마와 현재 통계 |
| head 학습·점수화 | `src/alphamale/events/gold/training.py`<br>`src/alphamale/events/gold/scoring.py`<br>`src/alphamale/events/gold/gate_sweep.py` | head learning, score output, threshold sweep |
| month runtime | `src/alphamale/events/epoch/runner.py`<br>`tests/events/test_epoch_runner.py`<br>`data/interim/events/_epoch_state.json`<br>`data/interim/events/review_queue.jsonl` | accept/drop/review 루프와 CLI boundary |
| deterministic post-process | `src/alphamale/events/assembly/mapping.py`<br>`src/alphamale/events/assembly/assemble.py`<br>`tests/events/test_assemble_events.py` | mapped row, canonical event, fallback lineage boundary |
| mapping / assembly outputs | `data/manifests/events/mapped_2006-06.gauge.json`<br>`data/manifests/events/mapped_2021-06_body.gauge.json`<br>`data/manifests/events/news_events_2006-06.cov.json`<br>`data/manifests/events/news_events_2021-06_v2.cov.json`<br>`data/interim/events/news_events_2021-06_v2.jsonl` | coverage 수치와 sample payload |
| exploration harness | `src/alphamale/events/benchmarks/exploration.py`<br>`tests/events/test_exploration_bench.py`<br>`data/manifests/events/exploration_bench_report_v0_1.json` | bench-only inference/projection scoring |
| legacy boundary / diagrams | `docs/archive/events/news-event-integration-spec.md`<br>`docs/archive/diagrams/events/news-event-structured-exploration.drawio`<br>`docs/archive/diagrams/graph/alphamale-graph-architecture.drawio` | fuller adaptive loop와 시스템 문맥 |

## Rollout

1. 현재 기준으로 파이프라인을 읽을 때는 **gold asset → head 학습/score/calibration → `run_epoch()` month runtime → `map_events.py`/`assemble_events.py` 후처리** 순서로 이해한다.
2. bench는 같은 이벤트 문제의 상위 연구 하네스이므로, production epoch 산출물과 섞지 말고 별도 validation 축으로 둔다.
3. `epoch_runner.py`의 실모델 CLI 배선은 scorer/adapter layer가 `run_epoch()`의 주입식 `gate_fn`/`type_fn` 계약을 유지하는 방식으로 확장한다. CLI는 그 배선이 끝날 때까지 `--stub` smoke-run 경계를 유지한다.
4. fuller adaptive loop의 bridge 저장소는 separate store가 구현되기 전까지 현재 `review_queue`/`state`/`model_versions` 감사선과 기존 gold/review artifact 계열을 함께 사용한다. ontology bump와 replay는 이 append-only 감사선을 보존하는 범위에서만 확장한다.
5. 후속 승인 전까지는 fallback path를 coverage metric의 부속 메모가 아니라 **사건 단위 structured provenance**로 확장하는 안을 기준 후보로 둔다. `map_events.py`의 `theme_method`, `assemble_events.py`의 `evidence.assembler`, `exploration_bench.py`의 `event_type_unrouted`/`REVIEW_QUEUE` lineage를 재사용하되, sector/theme miss는 non-blocking `UNKNOWN`/gap, body-missing `deterministic-v1`는 admissible baseline, cue-table `complete_fn` miss/fail은 review-only 결정으로 구분해 감사 가능하게 남긴다.

## Pending Decisions

1. 후속 승인 전까지는 `epoch_runner.py`의 실모델 adapter ownership을 scorer/adapter layer에 두는 안을 기준 후보로 둔다. `run_epoch()`는 계속 주입식 `gate_fn`/`type_fn` callable 계약을 유지하고, CLI는 adapter가 배선될 때까지 `--stub` smoke-run 경계로 남긴다.
2. 후속 승인 전까지는 한국어 gold fixture를 명시적 `--gold` 지정 시 first-class 입력으로 취급하는 안을 기준 후보로 둔다. `ko_gold_title.jsonl`과 `ko_gate_gold_all.jsonl`은 README 분류와 별개로 학습/평가 실행에서 직접 주입할 수 있으며, 어떤 fixture를 썼는지는 커맨드 인자로 고정해 재현한다.
3. 후속 승인 전까지는 review queue에서 후속 판정된 라벨을 separate store가 구현되기 전까지 기존 gold/review artifact 계열에 append하는 bridge 계약으로 운영하는 안을 기준 후보로 둔다. 즉 현재 append-only 감사선은 `review_queue.jsonl`·`ko_review_queue.jsonl`과 gold fixture 계열이며, 별도 저장소는 deferred implementation contract다.
4. 후속 승인 전까지는 `exploration_bench.py`를 research harness로 남기는 안을 기준 후보로 둔다. 다만 profiles, PIT window, projection/forbidden rules는 production graph-projection contract의 shared rule set이며, bench는 그 계약을 fixture와 scorer로 검증하는 계층이다.
5. 후속 승인 전까지는 canonical-event assembly의 기본 경로를 body가 있을 때 `deterministic-v2-lead`로 두는 안을 기준 후보로 둔다. body가 없는 실행이나 baseline 비교에서는 `deterministic-v1` title-only 경로를 fallback baseline으로 유지한다.
6. 후속 승인 전까지는 fallback provenance 최소 필드를 `fallback_id`, `fallback_logic`, `trigger_condition`, `input_evidence_ref`, `selected_output`, `admissibility` 또는 `decision`, `confidence_or_status`, `recorded_at`/`asof`로 두는 안을 기준 후보로 둔다. 이 필드 집합은 sector/theme enrichment fallback, body-missing `deterministic-v1` baseline, cue-table `complete_fn` fallback에 공통 적용하고, fallback 부재나 실패는 해당 계층의 gap/review 결정과 연결되게 남긴다.
