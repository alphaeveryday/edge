-- ============================================================================
-- policy_version.max_risk 은퇴 — 수축(contract) 단계 (ALPHA-634 / ADR-0046)
--
-- 위험등급 융합 산정 폐기로 max_risk 는 소비자가 사라졌다(확신도 게이트가
-- min_confidence 로 대체). 확장 단계(V202608041500)에서 코드 참조를 전부 제거하고
-- 쓰기를 중단했으므로, 이 마이그레이션이 컬럼을 제거한다(확장-수축 규약의 수축).
--
-- 배포 순서 주의(deploy-demo-onprem.yml): flyway-onprem 을 먼저 force-recreate 한 뒤
-- (여기서 이 DROP 적용) 앱 컨테이너를 재생성하므로 배포가 완전한 stop-the-world 는
-- 아니다. 박스가 아직 확장(#527) 코드를 안 받은 상태에서 확장+수축을 한 번에 점프하면,
-- flyway 가 컬럼을 내린 뒤~앱 교체 전 사이에 구 tenant-console-api(max_risk 매핑) 가
-- 제거된 컬럼을 SELECT 해 수 초간 실패하는 창이 생긴다. **확장 릴리스(#527)를 박스에
-- 먼저 배포**해 실행 중 앱이 max_risk 를 더는 매핑하지 않게 한 뒤 이 수축을 배포하면
-- 창이 없다(운영 순서 — 스크립트가 강제하진 않는다).
-- 컬럼 DROP 이 ck_policy_version_max_risk CHECK 도 함께 제거한다(단일 컬럼 참조).
-- ============================================================================

SET search_path TO public;

ALTER TABLE policy_version
    DROP COLUMN max_risk;
