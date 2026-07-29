---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - event-argument-schema-v1.md
  - news-ontology-criteria.md
  - news-ontology-rulebook.md
  - news-ontology-acceptance-sets.md
  - news-ontology-query-battery.md
---
# 최종 뉴스 이벤트 스키마 v1 — 통합 (3층 온톨로지 + 추출·정본·스레드·조회)

> 흩어진 결정을 **하나의 스키마**로 통합. 각 필드의 규칙은 §rulebook(R1–R13)·§acceptance-sets·§criteria에서 도출. 상태: **설계 확정·machine-valid(온톨로지 테스트 23/23)**; v1.1은 **제안**(프로덕션 현행=canonical-1.0 + 학습 파이프라인, 적용=마이그레이션 §event-argument-schema-v1 §5).

## 0. 파이프라인 한 눈에
```
원문 → [① LLM 추출 7축] → [② 코드 결정론 보강] → [③ 정본 이벤트 v1.1 저장] → [④ 스레드] → [⑤ 에이전트 조회뷰]
        3 온톨로지 층(타입·프로필·피처)이 각 필드의 어휘·규칙을 규정
```

## 1. 추출 계약 (LLM 출력, 콜당) — R1–R10
```json
{"items":[{
  "i": 0,
  "type": "COMPANY.CONTRACT.SIGNING",          // ①타입 (ontology_ref 53)
  "predicate": "SIGN",                          // 통제 술어 (타입 pred 메뉴, 해석배제)
  "trigger": "원유운반선 2척을 2734억원에 계약",  // verbatim span (감사·스팬검증)
  "stage": "DEFINITIVE_SIGNED",                 // 라이프사이클 축 (null 가능)
  "participants": [                             // 개체값 역할만
    {"role":"SUPPLIER","slot":"subject","mention":"한화오션","group":0},
    {"role":"CONTRACT_OBJECT","slot":"object","mention":"유조선","group":1}],
  "measures": [                                 // 수량값 역할만; value/unit은 LLM이 안 냄
    {"role":"QUANTITY","surface":"2척","basis":"UNKNOWN","group":1},
    {"role":"CONTRACT_VALUE","surface":"2734억원","basis":"UNKNOWN","group":1}],
  "confidence": "H"}]}
```
규칙: mention/surface = 원문 복사 · role = 타입 메뉴 밖 금지 · `group` = 라인아이템 바인딩(0=이벤트전역) · basis 미명시=UNKNOWN.

## 2. 코드 결정론 보강 (LLM이 안 하는 것) — R3·R4·R8·R9·D5·D8
| 산출 | 규칙 |
|---|---|
| `measures.value/unit/unit_family` | surface 결정론 파싱, 폐쇄 unit 집합, 무환산 · `value_source=PARSED` · `parse_flag` |
| span 검증 | `norm(mention/surface) ⊂ norm(제목+리드)` 실패→드롭+completeness 강등 |
| `participants.entity_id/entity_kind/resolution` | alias_map → `ORG_KR_*`/`CONCEPT:*`/`COHORT:*`/`ENTITY_UNLISTED:*`; kind ⊥ resolution |
| `event_id, thread_id/key, completeness` | §4·§스레드 |
| DART 주입(선택) | 금액매칭 결정론만, `value_source=DART` |

## 3. 정본 이벤트 v1.1 (저장 형태) — 전체 필드
```json
{
  "schema_version": "canonical-event-1.1", "ontology_version": "2026-01",
  "document_id": "…", "published_at": "…", "available_at": "…",
  "events": [{
    "event_id": "…#0", "event_type_id": "COMPANY.CONTRACT.SIGNING", "family": "COMPANY",
    "proposition": {"predicate_id":"SIGN","subject_roles":["SUPPLIER"],"object_roles":["CONTRACT_OBJECT"]},
    "lifecycle": {"stage":"DEFINITIVE_SIGNED","stage_source":"llm-extract-v3"},
    "participants": [{"role_id":"SUPPLIER","slot":"subject","mention":{"text":"한화오션"},
        "normalized":{"kind":"ENTITY","entity_id":"ORG_KR_042660","resolution":"LISTED"},"group_ord":0},
      {"role_id":"CONTRACT_OBJECT","slot":"object","mention":{"text":"유조선"},
        "normalized":{"kind":"CONCEPT","entity_id":"CONCEPT:oil_tanker","resolution":"CONCEPT"},"group_ord":1}],
    "measures": [{"role_id":"QUANTITY","surface":{"text":"2척"},"value":2,"unit":"척","unit_family":"COUNT","group_ord":1},
      {"role_id":"CONTRACT_VALUE","surface":{"text":"2734억원"},"value":273400000000,"unit":"KRW",
        "unit_family":"CURRENCY","basis":"UNKNOWN","value_source":"PARSED","group_ord":1}],
    "completeness": "complete", "confidence": "H"
  }]
}
```
전환 1릴리스: `arguments[]`(canonical-1.0)를 participants+measures에서 파생 병기 → 소비자 이관 후 제거.

## 4. 3 온톨로지 층 — 스키마가 참조하는 어휘·규칙 (SSOT)
| 층 | 파일 | 규정 |
|---|---|---|
| ① 타입 | `ontology_ref.txt` (53) | `type_id | pred | required_roles | note(자매 disambiguation)` |
| ② 프로필 | `event_type_profiles_v0_1.json` | required/optional/**identity_roles**, lifecycle_model, projection(그래프 edge) |
| ③ **피처** | `feature_specs_v0_1.yaml` + `parts/partA–G` | **타입별 객관 상태·측정 변수(해석중립)** — `quantities`(unit_family·basis) · `event_attrs` · `entity_state` · `expectation` · `context` · `thread` · `derived`(lineage) + `common_blocks`(전타입 상속: 시총·변동성·베타·선반영…). **direction·discovery_hypotheses·role_in_impact 제거**(해석/가설 = 상위 에이전트 층). |

**③가 telos의 기질:** measures(§1)는 원문 추출 수치, **feature_specs는 그것을 해석하기 위한 객관적 상태·파생 변수**(이벤트스터디 *입력 기질*). 방향·영향·가설 형성은 **feature_specs 위 상위 에이전트 층** 소관(온톨로지 해석중립, G5). 원칙(기계검증): PIT · NULL정직 · **결합금지**(impact score 없음) · lineage · **해석중립**. `measures.role` ↔ `feature_specs.quantities` 키로 맞물림.

## 5. 스레드 (사후 변화 계보) — R5–R7
```
event_thread(thread_id PK, thread_key = type + identity_roles의 정규화 entity_id, current_stage, opened_at)
event_thread_link(event_id PK, thread_id, novelty_status, dedup_cluster_id, asof)
```
novelty_status: FIRST_IN_THREAD · STAGE_PROGRESSION · VALUE_REVISION · CORRECTION · SCOPE_AMENDMENT · CANCELLATION · DUPLICATE_REBROADCAST · UNKNOWN. `thread_id ≠ dedup_cluster_id`(불변식).

## 6. 에이전트 조회뷰 (평면 읽기모델) — Q1–Q9
```
event_fact(event_id PK, event_type_id, family, predicate_id, stage, thread_id, published_at, available_at, confidence, completeness, subject_entity_id)
event_participant(event_id, role_id, slot, mention, entity_id, entity_kind, group_ord)
event_measure(event_id, role_id, value, unit, unit_family, basis, value_source, group_ord)
event_thread(thread_id PK, event_type_id, thread_key, current_stage)
```
라인아이템 복원: `event_participant ⋈ event_measure USING (event_id, group_ord)`.

## 상태 / 미결
- **설계 확정:** §1–§6 전 층. ①②③ machine-valid(테스트 23/23). 추출 7축·정본 v1.1·스레드·조회뷰 명세 완료.
- **제안(미적용):** v1.1 정본은 마이그레이션 필요; 프로덕션 현행=canonical-1.0 + 학습 타입모델. stage 축(D4)·group 바인딩·entity_id 스레드키·신규타입은 **재학습/구현 gated**(§gold-spec·§remediation).
- **즉시 유효분:** `ontology_ref` note 보강(6종, 검증됨) · 재학습 seed(`ontology_boundary_candidates.jsonl`).
