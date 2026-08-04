-- ============================================================================
-- policy_version 자동 제공 최소 확신도 — min_confidence (ALPHA-634)
--
-- 위험등급 융합 산정을 폐기하고 자동 제공을 독립 AND 게이트로 확장한다
-- (docs/superpowers/specs/2026-08-04-confidence-gate-design.md). max_risk 상한은
-- 확신도 임계와 동형이라(금칙어=룰 action·출처 수=min_source_count 가 이미 판정,
-- 융합 등급의 실효 입력은 confidence 뿐) 이중 반전 없이 확신도를 직접 기준으로
-- 저장한다. max_risk 는 은퇴 — 이 확장 단계에선 컬럼을 유지하고(쓰기 중단),
-- 수축(drop)은 후속 PR 이다(확장-수축 규약).
--
-- NULL 허용 = 미설정(게이트 꺼짐, 기존 행·min_source_count 와 동일 시맨틱).
-- LOW 는 어휘에 없다 — 보류(LOW)까지 허용은 미설정과 실질 동일이라 기준이 될
-- 수 없다(max_risk 가 HIGH 를 뺀 것과 같은 원리). 게이트 켜짐 상태의 confidence
-- 결측은 미달(REVIEW) — 정보 없으면 검수 쪽(fail-safe).
-- ============================================================================

SET search_path TO public;

ALTER TABLE policy_version
    ADD COLUMN min_confidence VARCHAR(10);

ALTER TABLE policy_version
    ADD CONSTRAINT ck_policy_version_min_confidence
        CHECK (min_confidence IS NULL OR min_confidence IN ('MEDIUM', 'HIGH'));

COMMENT ON COLUMN policy_version.min_confidence IS
'자동 제공 최소 확신도(MEDIUM/HIGH, NULL=미설정) — 콘솔 "자동 제공 기준" 저장 원천이자 screening-worker 확신도 게이트 입력(ALPHA-634). analysis_item.confidence_level(보류 LOW<중간 MEDIUM<높음 HIGH) 미달·결측이면 자동 제공 대신 검수행.';
