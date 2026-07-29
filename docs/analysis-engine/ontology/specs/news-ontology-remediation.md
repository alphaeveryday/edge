---
doc_type: spec
status: Proposed
owner: engineering
created: 2026-07-22
updated: 2026-07-22
method: skill://ontology-design-criteria
related:
  - news-ontology-acceptance-sets.md
  - news-ontology-rulebook.md
  - news-ontology-criteria.md
---
# 온톨로지 경계결함 정제 계획 (evolution) — 수용셋에서 발견된 8건

> **입력:** `news-ontology-acceptance-sets.md`(A–F, ~49타입)에서 코퍼스 실측으로 발견한 경계결함 8건.
> **규율:** `ontology-design-criteria` evolution — 의미변경=새 ID, 삭제 대신 deprecate, 한 건씩, positive5/hard-negative5(이미 작성) 통과 후 반영, `ontology_version` 보존.
> **주의:** SSOT(`src/alphamale/events/ontology/resources/`)는 로더·테스트가 의존 → 실제 반영은 타입 하나씩 + 로더/테스트 재실행 검증. **본 문서는 계획(설계); 실 SSOT 변경은 green-light 후.**

## 8건 정제 매트릭스

| # | 결함(관측) | 정제 action | 유형 | 영향 파일 | 재처리 |
|---|---|---|---|---|---|
| 1 | capex가 STAKE_ACQUISITION에 혼입 | STAKE를 **"지분(equity)만"**으로 협소화; 설비 capex → 기존 `PRODUCTION.CAPACITY_CHANGE` | def 협소(minor) | `ontology_ref.txt`·`event_type_profiles`·추출프롬프트 | STAKE·CAPACITY 재분류 |
| 2 | 우협선정 등 stage가 type과 혼동 | **stage 축 추출(D4)** 도입 — PREFERRED_BIDDER/MOU_LOI 등 lifecycle.stage로; type 유지 | 신규 필드(minor) | 추출 스키마·assembler·thread 계약 | DEAL 계열 재추출 |
| 3 | ESG(온실가스·지속가능보고서)가 RESULT/CAPACITY로 누출·홈 부재 | **신규 타입 `COMPANY.ESG.DISCLOSURE`**(가칭) 신설 | 새 ID(patch/minor) | `ontology_ref.txt`·`profiles`·`feature_specs` | RESULT/CAPACITY 오분류 회수 |
| 4 | 투자유치(equity received)·보증/대출지원이 DEBT_ISSUANCE에 혼입 | DEBT를 **"회사의 채권/사채 발행"**으로 협소화; 투자유치 → STAKE(피투자 관점 역할 주석) | def 협소(minor) | `ontology_ref.txt`·`profiles`·추출프롬프트 | DEBT 재분류 |
| 5 | 분쟁/불만/주주행동이 LAWSUIT에 혼입 | LAWSUIT를 **"정식 소송 제기·진행·판결전"**으로 협소화; 단순 분쟁 배제 | def 협소(minor) | `ontology_ref.txt`·`profiles` | LAWSUIT 재분류 |
| 6 | 사회·세금 통계가 macro DATA_RELEASE에 혼입 | INFLATION/GDP/EMPLOYMENT.DATA_RELEASE를 **공식 지표(CPI/PCE·GDP/경상수지·고용/실업률)로 한정**(profiles에 indicator 화이트리스트) | def 협소(minor) | `profiles`·추출프롬프트 | macro 3종 재분류 |
| 7 | INSIDER vs BUYBACK 주체 혼동 | 정의 명확화: **개인(오너/임원)=INSIDER, 회사법인=BUYBACK** (양 타입 desc + 프롬프트) | def 명확(patch) | `ontology_ref.txt`·`profiles`·프롬프트 | 경계 표본만 |
| 8 | EXCHANGE_OUTAGE 참양성 희소·24h개장 오용 | OUTAGE를 **"거래소 시스템 장애"**로 한정; 24시간개장 등 구조변경은 별도 처리(신규 `MARKET_STRUCTURE.TRADING_SESSION_CHANGE` 검토 or REGULATION) | def 협소 + 신 ID 검토 | `ontology_ref.txt`·`profiles` | OUTAGE 재검토 |

## 반영 순서 (한 건씩, 게이트 통과 후)
1. **새 ID 신설(3 ESG · 8 세션변경)** — 가장 안전(가산). positive5/hard-negative5 작성 → 불변식 lint → 반영.
2. **def 협소(1·4·5·6)** — 멤버십 변경 → minor 버전 + 해당 타입만 재처리(05 reprocessing 매트릭스).
3. **def 명확(7)** — patch, 경계 표본 재검.
4. **stage 축(2)** — 별도 추출 기능(D4), rulebook C2 의존.

## 검증(각 건)
- SSOT 편집 후 `load_ontology_bundle()` 교차검증 통과(타입/역할/피처 정합) + 기존 테스트 그린.
- 해당 타입 positive5 전수 수용 ∧ hard-negative5 전수 거절(수용셋 doc) 재확인.
- 재처리 범위만 재추출·재조립(전수 아님).

## SSOT recon 결과 (read-before-write — 계획 정정)

실제 SSOT/로더/테스트를 읽어 계획을 정정한다:
- **`parse_ontology_line`**: `TYPE | pred | req | note` — 4번째 `|` 세그먼트는 **note로만** 파싱. desc 보강 = **제로 blast**(count·cross-file 무관).
- **`bundle.py`**: profiles/features ⊆ registry, profile roles ⊇ registry required만 검증. registry-only 타입은 통과하나 **추출 불가**(추출은 profiles 역할메뉴 사용).
- **새 타입 = 6파일 조율 + 하드코딩 `==53` 테스트**: `ontology_ref`(+1)·`event_type_profiles`(+1·meta 54)·`feature_specs`(+1·meta)·`future_feature_backlog`(+1·meta)·`news_thread_contract`(+1·meta) + `test_feature_specs`(53 coverage)·`test_news_thread_contract`(`==53` 2곳) 갱신. → **고blast, 심의 필요.**
- **핵심 정정:** 결함 대부분은 **정의 결함이 아니라 추출/분류 오류** — 기존 def에 이미 NOT-절 존재(#7 INSIDER "NOT a buyback", #1 STAKE "NOT own capital action", PRICING "NOT commodity price"). SSOT 변경으론 안 고쳐짐 → **추출 프롬프트가 note를 노출**해야 함(현행 digest는 `type|pred|req`만 사용, note 미노출).

### 이번 회전 적용(안전·검증됨)
- thin def 6종에 **NOT-절 note 보강**(제로 blast): `DEBT_ISSUANCE`(#4) · `PRODUCTION.CAPACITY_CHANGE`(#1 capex 라우팅) · `LAWSUIT`(#5) · `INFLATION/EMPLOYMENT/GDP.DATA_RELEASE`(#6). → `ontology_ref.txt` 편집.
- **검증:** `tests/events/{test_events_ontology,test_feature_specs,test_news_thread_contract}` **23/23 green** — count 53·bundle·thread 정합 유지.

### 파이프라인 recon (정정 #2 — 활성화 경로)
**프로덕션 타입-분류 = 학습된 체크포인트 모델**(`epoch_runner.py` → `epoch_out/<month>.parquet`, `type_model_version=ko-type-v2/xlmr-type`), **LLM digest 프롬프트가 아님**. teacher_extract_v3는 타입 동질 배치(타입 이미 확정). assemble_events는 registry default predicate·required roles만 사용(note 미사용).
→ 따라서 "digest에 note 노출"은 **프로덕션에 무효**. 보강한 NOT-절의 실효 경로:
- **골드-주석 teacher**(`ko_teacher`/`golden-data-codex-teacher`)가 타입 라벨링 시 note 사용 → 라벨 품질↑ → **타입 모델 재학습**으로 전파. (진짜 활성화 경로, ML 태스크)
- **SSOT/사람 명료성** + assembler/thread 계약 참조.

### 잔여(정정된 분류)
- **타입-분류 정확도(#1·#6·#7 등):** 프롬프트 아님 → **corrected 골드로 타입모델 재학습** 필요(대형 ML). note 보강은 골드 품질 입력.
- **신규 타입(고blast, 심의):** #3 ESG → `COMPANY.ESG.DISCLOSURE` 6파일. #8 세션변경 → 신설 or OUTAGE 협소+deprecate.
- **결정론 규칙:** #4 DEBT·#5 LAWSUIT 협소는 assembler/teacher가 참조 → note 반영됨(적용 완료).
- **추출 기능:** #2 stage 축(D4).

## 상태
- **완료:** 8건 정제 계획 + **SSOT recon 정정** + thin def 6종 note 보강(검증 23/23 green).
- **다음(각각 gated·대형):** ① 골드-주석 teacher에 registry note 통합 → 타입모델 재학습(ML) ② ESG 신타입 6파일 심의 ③ stage 축(D4). 세 경로 모두 별도 검증 동반. **"digest note 노출"은 프로덕션 무효로 폐기**(정정 #2).
