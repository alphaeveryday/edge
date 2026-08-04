# 확신도 AND 게이트 설계 — 위험등급 융합 산정 폐기 (ALPHA-634 재정의)

- 날짜: 2026-08-04
- 상태: 설계 승인됨 (사용자, 2026-08-04)
- 관련: ALPHA-634 · ALPHA-607(확신도 재라벨 임시 매핑) · V202607271340(max_risk 컬럼) · ADR-0018(룰 로직=코드)

## 문제

콘솔의 "저/중/고위험" 배지는 `analysis_item.confidence_level`(LLM 확신도)의 재라벨이라 의미가 전도돼 있다(확신 높음 → "고위험" 표시). 실제 위험등급 산정(ALPHA-634)은 미구현이고, 정책의 `max_risk`(자동 제공 위험 상한)는 저장·표시만 될 뿐 소비자가 없다.

## 핵심 발견 — 융합 등급과 maxRisk 게이트는 확신도 임계와 동형

원안(금칙어 risk + confidence 역매핑 + UNCERTAIN을 max 결합한 등급을 maxRisk 상한과 비교)을 분해하면:

- 금칙어 매칭분은 이미 룰 action(REVIEW/BLOCK)으로 판정된다 — 등급 기여는 게이트에 무의미.
- 출처 수는 min_source_count 게이트가 이미 거른다.
- 남는 실효 입력은 confidence(+UNCERTAIN)뿐 — `maxRisk=MEDIUM` ≡ "보류·UNCERTAIN 검수", `maxRisk=LOW` ≡ "높음 확신만 자동".

즉 maxRisk 게이트는 확신도 임계를 두 번 뒤집어(확신→위험 역매핑→상한 비교) 표현한 것이다. 잘못된 추상이 계약으로 굳기 전에(소비 코드 0) 교정한다.

## 결정

위험등급 융합 산정을 폐기하고, 자동 제공 조건을 **독립 AND 게이트**로 유지·확장한다:

```
자동 제공 = 룰 무매칭(청정) AND auto_publish ON
          AND 출처 수 ≥ min_source_count      (기존)
          AND explanation_type ≠ UNCERTAIN    (신설, 정책 무관 상시)
          AND confidence ≥ min_confidence     (신설 노브, 미설정 시 게이트 꺼짐)
```

### 1. 정책 모델 (policy_version)

- **신설** `min_confidence VARCHAR(10)` NULL 허용, `CHECK (min_confidence IS NULL OR min_confidence IN ('MEDIUM','HIGH'))` — 자동 제공 최소 확신도. NULL=미설정(게이트 꺼짐), MEDIUM=중간 이상 자동, HIGH=높음만 자동. LOW는 어휘에 없다(보류까지 허용은 미설정과 실질 동일 — maxRisk가 HIGH를 어휘에서 뺐던 것과 같은 원리).
- **은퇴** `max_risk` — expand-contract 2단계: PR1에서 코드 참조 전부 제거·교체(확장), 후속 PR에서 컬럼 drop(수축). 기존 버전 행의 max_risk 값 이력은 console_action_log 감사로 충분(소비자 없음).
- 게이트가 켜진 상태에서 confidence **결측은 미달**(REVIEW) — 정보 없으면 검수 쪽(fail-safe).

### 2. 평가기 (screening-worker `policy/PolicyEvaluator`)

기존 집계(BLOCK > REVIEW > PASS)와 auto_publish·min_source 층은 불변. PASS 후보층에 같은 패턴으로 추가:

- `explanation_type = UNCERTAIN` → 항상 REVIEW_REQUIRED (정책 무관 — "고위험 항상 검수" 확정 결정 2026-07-26의 등가물).
- `min_confidence` 설정 시: confidence 순위(보류 LOW < 중간 MEDIUM < 높음 HIGH) 미달·결측 → REVIEW_REQUIRED.
- 각 판정은 screening_check에 rule_id NULL 행으로 근거 append (min_source 기존 패턴): 예 `explanation_type=UNCERTAIN`, `confidence=LOW<min=MEDIUM`.
- 게이트는 BLOCK을 만들지 않는다 — 차단은 금칙어 action 소관.
- 입력(`confidence_level`·`explanation_type`)은 이미 와이어·원장에 있다 — **번들 계약·cloud 변경 없음, analysis_item 신규 컬럼 없음**. 티켓의 "등급 컬럼·번들 확장" 항목은 "기존 신호로 충분"으로 종결.
- `ActivePolicy`에 minConfidence 추가, `BundleScreener.loadActivePolicy()`에서 배선. 산정에 LLM 없음(AGENTS Rule 5, ADR-0018).

### 3. 콘솔 (tenant-console-api + ui)

- 처리 기준: maxRisk 설정 → **min_confidence 설정으로 교체** — `ScreeningService.updateCriteria` 검증 어휘, Criteria DTO(Patch/Response), `PolicyVersionSummary`, ScreeningPage 셀렉터.
- 배지: "저/중/고위험" → **"확신도: 높음/중간/보류"** — 필드 `risk` → `confidence` 정직 리네임(API·UI 동시), ALPHA-607 임시 재라벨 해소. 결측 시 생략은 기존 계약 유지.
- 금칙어 룰의 risk 메타데이터(LOW/MEDIUM/HIGH)는 유지 — 걸린 사유의 심각도 표시로 유효, 판정은 action 소관.
- UNCERTAIN·confidence 게이트의 검수 사유 표시: reviewReason 파생(rule_id NULL 행 처리)은 min_source 기존 처리와 동일 경로 — 구현 시 확인.

### 4. 거버넌스·문서·소급

- **ADR 1건 신규**: "위험등급 융합 산정 폐기 — 확신도 AND 게이트 전환" (동형성 논증 + maxRisk 은퇴 근거). ALPHA-634는 이 방향으로 재정의 코멘트.
- state-machine.md("저위험/정책 통과" 서술)·tenant-console.md(처리 기준·위험등급 이력 서술)·permission-matrix.md 동기화는 docs-sync 게이트 몫.
- 기존 항목 소급 재평가 없음. 데모 박스의 신규 정책 발행(금칙어 4건+min_source=2, 2026-08-04 진행)과 충돌 없음 — max_risk 저장값은 소비자가 없다.

## 테스트 (의도)

- PolicyEvaluator 단위: 확신도 순위 비교·결측=미달·UNCERTAIN 상시 검수·min_confidence NULL 게이트 꺼짐 — 불변식 "모호성은 전부 검수 쪽"(보수적 온보딩)이 깨지면 실패해야 한다.
- BundleScreener 통합: UNCERTAIN·저확신 항목이 REVIEW_REQUIRED로 착지 + check 근거 행 검증.
- 콘솔: criteria PATCH 어휘 검증(min_confidence)·버전 발행 승계.
