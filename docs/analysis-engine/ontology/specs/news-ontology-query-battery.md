---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - news-ontology-criteria.md
  - news-ontology-rulebook.md
---
# 뉴스 온톨로지 질의 배터리 — 수용 오라클 (decision B / Move5)

> **역할:** 온톨로지의 **반-Goodhart 수용 게이트.** 내부 지표(P·R·불변식)가 아니라 **"에이전트의 실제 질문에 답하는가"**로 온톨로지를 수용한다. 통과 = 의도 달성(프록시 아님, 그게 일 자체).
> **품질바(각 질의):** (a) 원자료로 답 가능 · (b) 한 능력만 검증 · (c) 골드 답셋 보유.
> **과적합 차단:** 각 능력을 **dev / held-out** 로 분할 — held-out은 스키마 튜닝에 절대 안 씀.
> **근거 referent:** v3 코퍼스 실측 — 타입(PRODUCT.LAUNCH 6,906·CONTRACT.SIGNING 3,403·REGULATION.RULE_CHANGE 1,519…), 주체(삼성전자 1,928·SK하이닉스 1,456…), AUTHORITY(공정거래위원회 222·정부 196·금융위원회 164…).

## 능력 → 질의 (구체 referent) → 골드 → 검증 규칙

| # | 능력 | 질의(구체) | 골드 답 산출법 | 검증 규칙 |
|---|---|---|---|---|
| Q1 | 주체 조회 | "삼성전자가 주체인 이벤트 전부" | `participant.entity_id=ORG_KR_005930 ∧ slot=subject` | R1·R9·D7 (엔티티 id 안정) |
| Q2 | 행위-타입 조회 | "계약 체결(CONTRACT.SIGNING) 사건 전부" | `event_type_id=COMPANY.CONTRACT.SIGNING` | D1·D3 (타입 경계·통제술어) |
| Q3 | 정책-주체 결합 | "공정거래위원회의 규제 시도(RULE_CHANGE)" | `type=POLICY.REGULATION.RULE_CHANGE ∧ AUTHORITY=공정거래위원회` | D1·엔티티 |
| Q4 | 라이프사이클 스레드 | "이 계약의 RUMORED→SIGNED 타임라인" | thread members, `available_at` 정렬 + stage 전이 | R5·R6·D4 (same-vs-new·novelty·stage) |
| Q5 | 다중-바인딩 | "삼성 140조·SK 100조·셀트리온 2조에서 사별 금액" | `group_ord`로 (주체↔금액) 짝 | R3·D6 (바인딩·단사성) |
| Q6 | 자매 구분 | "경영권 인수(M&A) vs 소수지분(STAKE) 구분" | 헷갈리는 셋에서 올바른 타입 | D1 hard-negative |
| Q7 | 시간창 조회 | "2026-07 체결된 계약 전부" | `available_at ∈ [07-01,07-31] ∧ type=CONTRACT.SIGNING` | R11 (시각 절대화) |
| Q8 | PIT/as-of | "6/1 시점에 알 수 있던 삼성전자 이벤트(미래발효 미실현)" | `available_at ≤ 6/1 ∧ realized=(event_time≤available_at)` | R11·R12 (PIT 정직) |
| Q9 | 혼합단위 | "빽다방 가격인상률과 인상액을 각각" | `measures` unit_family별(RATIO vs CURRENCY) | R4·R8 (단위·value_kind) |

## 수용 규칙 (반-Goodhart)
- **게이트 = held-out 배터리** 각 능력 precision·recall ≥ 사전선언 임계; 전 능력 통과 시 온톨로지 수용.
- **내부 지표(P0–P6·R1–R13 불변식) = 진단** — held-out 실패의 원인 규명용, 게이트 아님.
- **골드는 원자료에서** 산출(스키마 필드에서가 아니라) → 스키마에 유리하게 못 굽음.
- **왜 반-Goodhart인가:** 능력은 telos에서 유래(에이전트의 실제 일), held-out은 튜닝 격리, 골드는 소스 기준 → 배터리를 올리는 유일한 길 = 실제로 그 질문에 답하는 온톨로지를 만드는 것.

## 추적성 (능력 → 하부목표)
Q1→G8·G4 · Q2·Q3·Q6→G3·G5 · Q4→G1·G2 · Q5·Q9→G4 · Q7·Q8→G6(PIT) · 전체→telos(탐색·조인·스레드·이벤트스터디). 고아 능력 0.

## 상태 / 다음
- **지금:** 배터리 **설계**(능력·질의·골드산출법·수용규칙) 확정. 개념 산출물.
- **다음(green-light 시):** dev/held-out 실제 골드셋 주석 + LLM-as-judge 채점(실측 단계).
