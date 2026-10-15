-- ============================================================================
-- 구 Cloud SaaS 테이블 9종 수축
--
-- V202606300001 이 만든 SaaS 9종(organizations·members·invitations·roles·
-- super_admins·applications·widgets·compliance_settings·target_symbols)은
-- ADR-0010 하이브리드 On-Prem 피벗으로 폐기된 구 Cloud SaaS 콘솔·embed widget
-- 구조의 잔재다. V202607150001 은 구 분석 마트만 제거하고 이들을 범위 밖으로
-- 남겼다 — 이번에 제거한다.
--
-- 확장-수축 단계: 수축 단독. reader/writer 코드가 0건이라(JPA 엔티티·SQL 문·
-- DB 접근 코드 전수 검색, 2026-07-17) 롤아웃 시차 중 깨질 구버전 코드가 없다.
--
-- 9종의 FK 는 그룹 내부에서만 서로를 참조하고 존치 테이블에서 들어오는 FK 가
-- 없어, 한 문장으로 함께 지우면 CASCADE 가 필요 없다 (CASCADE 를 쓰지 않는
-- 이유는 V202607150001 과 같다 — 제거 범위를 본문에서 보이게 유지).

DROP TABLE
    invitations,
    target_symbols,
    widgets,
    members,
    compliance_settings,
    applications,
    super_admins,
    roles,
    organizations;

-- set_updated_at() 의 마지막 사용처가 위 9종의 트리거였다 (V202607150001 이
-- "SaaS 9개가 여전히 트리거로 쓴다"며 남긴 함수). 트리거는 테이블과 함께
-- 떨어지므로 함수만 마저 제거한다. pgcrypto 확장은 존치 테이블의
-- gen_random_uuid() 기본값이 계속 쓰므로 남긴다.
DROP FUNCTION set_updated_at();
