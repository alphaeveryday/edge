# ADR-0046. 위험등급 융합 산정 폐기 — 확신도 AND 게이트 전환

- 상태: 승인 (2026-08-04)
- 관련: ALPHA-634 · ADR-0018(룰 로직=코드) · ADR-0037(점검≠검수≠정책) · ALPHA-438(정책 버전 모델) · ALPHA-607(확신도 재라벨 임시 매핑) · 스펙 [2026-08-04-confidence-gate-design](../superpowers/specs/2026-08-04-confidence-gate-design.md)

## 맥락

위험등급 산정(ALPHA-634)의 원안은 금칙어 risk·LLM 확신도·출처 수를 융합한 등급을
산정하고 정책 `max_risk`(자동 제공 위험 상한)가 이를 소비하는 구조였다. 구현 전
분해 결과 융합 등급은 동형 축소된다:

- 금칙어 매칭분은 이미 룰 action(REVIEW/BLOCK)으로 판정된다.
- 출처 수는 `min_source_count` 게이트가 이미 거른다.
- 남는 실효 입력은 confidence(+UNCERTAIN)뿐 — `maxRisk=MEDIUM` ≡ "보류·UNCERTAIN
  검수", `maxRisk=LOW` ≡ "높음 확신만 자동".

즉 max_risk 게이트는 확신도 임계를 두 번 뒤집어(확신→위험 역매핑→상한 비교) 표현한
것이다. 콘솔 "위험등급" 배지도 실체가 `confidence_level` 재라벨이라 의미가 전도돼
있었다(확신 높음 → "고위험" 표시, ALPHA-607 축소 계약의 임시 상태).

## 결정

1. **융합 위험등급 산정을 만들지 않는다.** 자동 제공 조건은 독립 AND 게이트로
   유지·확장한다: 룰 무매칭 AND 스위치 ON AND 출처 수 AND **UNCERTAIN 아님**(상시)
   AND **확신도 ≥ min_confidence**(노브).
2. `policy_version.min_confidence`(MEDIUM/HIGH, NULL=미설정) 신설. LOW 는 어휘에
   없다 — 보류까지 허용은 미설정과 실질 동일(max_risk 가 HIGH 를 뺀 것과 같은 원리).
   게이트 켜짐 상태의 confidence 결측은 미달(fail-safe).
3. `explanation_type=UNCERTAIN` 은 정책 설정과 무관하게 항상 검수 — "고위험은 항상
   검수·차단 경로" 결정의 확신도 등가물. 게이트는 BLOCK 을 만들지 않는다(차단은
   금칙어 action 소관).
4. **max_risk 는 은퇴한다.** 소비 코드가 생기기 전(소비자 0)이 은퇴 최저비용
   시점이다. 확장 단계(이 ADR 의 구현 PR)에서 코드 참조를 전부 제거하고, 컬럼
   drop 은 후속 수축 PR 로 분리한다(확장-수축 규약).
5. 콘솔 배지는 "위험등급"이 아니라 **확신도(높음/중간/보류)** 를 원값으로 표시한다
   — 설명 표면의 `risk` 필드는 `confidence` 로 리네임. 금칙어 심각도(risk) 어휘는
   걸린 사유의 심각도 표시로서 별개 유지.

산정 주체=온프렘 Screening Worker(2026-07-26 결정)는 유지된다 — 게이트 판정이 곧
산정의 실체이며, 입력(confidence_level·explanation_type)은 이미 와이어·원장에 있어
번들 계약·cloud 변경과 신규 등급 컬럼이 없다.

## 결과

- 얻는 것: 이중 반전 없는 정직한 정책 어휘, maxRisk 게이트 사문화 방지(확신도
  게이트는 실제로 거른다), 배지 의미 전도 해소, 스키마·계약 최소 변경.
- 잃는 것: "위험등급" 단일 축 표시 — 검수 사유(rule_type·기준 미충족)와 확신도
  배지가 그 역할을 나눠 담당한다. 새 판정 축이 필요해지면 융합이 아니라 새 AND
  게이트(스위치 하나로 여는 구조, ADR-0041 확장 로드맵)로 추가한다.
- 알려진 갭(범위 밖, 후속 티켓): 설명 목록 표면(ExplanationService)이 rule_id NULL
  판정 근거를 사유로 투영하지 않는다 — min_source 포함 기존 갭이며 검수 큐
  (ReviewService)는 정상 표시한다.
