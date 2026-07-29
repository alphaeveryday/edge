---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - news-ontology-acceptance-sets.md
  - news-ontology-remediation.md
  - news-ontology-query-battery.md
---
# 타입-모델 재학습 골드 스펙 — "결함=모델오류, gap=데이터기반"

> **read-before-write 3연쇄 결론:** (1) 프로덕션 타입-분류 = 학습된 체크포인트 모델(LLM digest 아님), (2) **골드는 내 8결함에 거의 clean → 결함은 모델 추론오류**, (3) 진짜 타입-gap = 골드의 `OTHER:` 이스케이프(데이터기반, ESG 아님).

## 골드 스캔 근거 (ko 4,295 + en 41,920; EVENT-labeled 14,814)
- **8 결함, 골드 내 오라벨 ≈0:** STAKE⊃capex **0/3204** · RESULT⊃ESG **0/1122** · EMPLOYMENT⊃social **0/30** · DEBT⊃투자유치 4/80 · LAWSUIT⊃분쟁 4/471.
  → **정의·골드는 정확.** v3 파이프라인의 오분류(capex→STAKE 등)는 **학습 모델의 일반화 오류**지 def/gold 결함이 아니다.
- **`OTHER:` 이스케이프 896건**(distinct 600) = 주석자가 부딪힌 **실제 타입 부재**.

## 정정: 8 결함의 진짜 성격 + 수정 경로
- 오분류 수정 = **재학습**(골드 유지, confusable 경계에 hard-negative 증강). **수용셋 A–F의 pos5/hard-negative5가 곧 증강셋.**
- 이전 회전의 def note 보강(6종)은 골드-teacher 라벨 품질·SSOT 명료성에 기여(유효, 재확인). 프로덕션 분류기엔 직접 무효(모델).

## 데이터기반 신규 타입 후보 (OTHER 빈도 ≥8 — ESG 아님)
| 후보 | 빈도 | 비고 |
|---|---|---|
| AWARD_RECOGNITION | 72 | 시장신호 약 → EVENT 대신 doc_class 흡수 검토 |
| MILESTONE (valuation/mktcap/cumulative) | 43 | 마일스톤 이벤트 |
| EXECUTIVE_COMPENSATION | 22 | 보상 결정/변경 |
| CORPORATE_INVESTMENT (capex) | 19 | 설비투자 — STAKE와 분리(cluster A 결함의 정식 홈) |
| SHAREHOLDER_PROPOSAL | 17 | 주총 안건/부결 |
| CORPORATE_STRUCTURE_CHANGE | 14 | 지주/구조 개편 |
| SALES_SUSPENSION | 13 | 판매중단(RECALL 인접) |
| PRODUCT_TEST_RESULT | 11 | 임상/제품 시험 결과 |
| BANK_STRESS_TEST | 10 | 규제 스트레스테스트 결과 |
| DISPUTE (비-소송 분쟁) | 10 | LAWSUIT 협소의 잔여 홈 |
→ 각 후보: **positive5/hard-negative5 저작 → 6파일 추가(count↑·테스트 갱신) → 재학습**해야 emit 가능(**inert-until-retrain**).

## Corrected-gold 라벨링 프로토콜 (재학습 입력)
1. **경계 hard-negative 증강(최대 기여):** 수용셋 A–F의 hard-negative(자매 오분류 실제 코퍼스 예시)를 **정답 라벨로 골드에 주입** → 모델이 경계를 학습. capex→CAPACITY, 투자유치→STAKE, social→비-macro, 분쟁→비-LAWSUIT 등. **구체 seed 산출: `data/interim/events/ontology_boundary_candidates.jsonl`** — 57행(유효타입 48·신규후보 7·doc_class흡수 2), `status=candidate`(teacher/human 판정 전), 각 행 `boundary_rule` 포함.
2. **`OTHER:` 896건 해소:** 위 신규타입 or 기존타입으로 재배정; 빈도<8은 `OTHER`/review 유지(evolution 채널).
3. **teacher = `golden-data-codex-teacher`** + 보강된 registry note를 타입메뉴에 노출 → LLM 라벨, 원문 span 검증, 애매건 human 판정.
4. **doc_class 정합:** AWARD 등 저신호 후보는 EVENT 승격 대신 doc_class 흡수 검토(오탐 축소).

## 수용 기준 (재학습 모델)
- held-out **수용셋 hard-negative 전수 거절 ∧ positive 전수 수용**(§acceptance-sets, D1 게이트).
- **질의배터리 Q1–Q9 held-out 통과**(§query-battery).
- 신규타입은 편입+재학습 후에만 emit.

## read-before-write 정정 3연쇄 (요약)
1. 8건 **일괄 SSOT 변경** → 대부분 분류오류(def 아님) → 폐기.
2. **digest note 노출** → 프로덕션 LLM 분류기 없음(학습모델) → 폐기.
3. **corrected-gold(내 8결함)** → 골드 이미 clean → 진짜 작업 = **hard-neg 증강 재학습** + OTHER기반 신규타입.

## 다음 (gated·대형)
- **타입-모델 재학습 파이프라인 스코핑**(데이터·체크포인트·컴퓨트) — ML 프로젝트, 별도 승인.
- OTHER≥8 신규타입 우선순위 심의 → 6파일 추가.
- 세 경로 모두 별도 검증 동반.
