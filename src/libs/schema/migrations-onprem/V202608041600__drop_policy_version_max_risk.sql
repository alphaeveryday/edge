-- ============================================================================
-- policy_version.max_risk 은퇴 — 수축(contract) 단계 (ALPHA-634 / ADR-0046)
--
-- 위험등급 융합 산정 폐기로 max_risk 는 소비자가 사라졌다(확신도 게이트가
-- min_confidence 로 대체). 확장 단계(V202608041500)에서 코드 참조를 전부 제거하고
-- 쓰기를 중단했으므로, 이 마이그레이션이 컬럼을 제거한다(확장-수축 규약의 수축).
--
-- 온프렘 앱은 데모 박스 단일 런타임이고 배포는 stop-the-world(flyway 완료 후 새
-- 이미지 기동)라, 이 DROP 은 min_confidence 확장과 같은 재배포에서 순서대로 적용된 뒤
-- 새 코드가 뜬다 — 구 max_risk 코드가 제거된 컬럼을 만나는 창이 없다.
-- 컬럼 DROP 이 ck_policy_version_max_risk CHECK 도 함께 제거한다(단일 컬럼 참조).
-- ============================================================================

SET search_path TO public;

ALTER TABLE policy_version
    DROP COLUMN max_risk;
