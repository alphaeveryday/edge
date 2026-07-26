-- ============================================================================
-- tenant 온보딩 필드 확장 + 환경 어휘 정렬 (ALPHA-121)
--
-- Super Admin 테넌트 생성(IA: docs/console-ia/super-admin-console.md)의 생성
-- 폼 필드(초기 Tenant Admin 이름·이메일, 메모)를 보존할 컬럼이 없어 앱이
-- "미보존"으로 보류 중이던 것을 편입한다. 환경 어휘는 IA 의 PoC/Production 으로
-- 정렬한다(구 DEV 는 POC 로 — 표기 경계 변환은 앱: POC↔PoC, PROD↔Production).
-- ============================================================================

SET search_path TO public;

-- 신규 생성의 필수성은 앱 계층(super-admin-api)이 강제한다 — 기존 행 호환을
-- 위해 컬럼은 NULL 허용(확장 단계).
ALTER TABLE tenant ADD COLUMN initial_admin_name  VARCHAR(100);
ALTER TABLE tenant ADD COLUMN initial_admin_email VARCHAR(255);
ALTER TABLE tenant ADD COLUMN memo                TEXT;

COMMENT ON COLUMN tenant.initial_admin_name IS
'생성 시 지정한 초기 Tenant Admin 이름 — 온보딩 연락 창구 기록(온프렘 member 원장과 별개).';
COMMENT ON COLUMN tenant.initial_admin_email IS
'초기 Tenant Admin 이메일 — 온보딩 연락 창구 기록.';
COMMENT ON COLUMN tenant.memo IS '운영 메모(자유 서식).';

-- 환경 어휘 확장 — IA 의 PoC 를 허용값에 추가한다. 확장 단계는 어휘 허용까지만:
-- 기존 DEV 행의 POC 백필과 CHECK 에서의 DEV 제거는 앱(super-admin-api·ui)이
-- POC 읽기·쓰기를 지원하도록 롤아웃된 뒤의 수축 마이그레이션 몫이다 — 지금
-- 백필하면 현재 배포본(Prod/Dev 만 처리)의 표시 계약이 깨진다.
ALTER TABLE tenant DROP CONSTRAINT ck_tenant_environment;
ALTER TABLE tenant ADD CONSTRAINT ck_tenant_environment
    CHECK (environment IN ('POC', 'PROD', 'DEV'));

-- writer 규약 기입(엔티티 주석이 예고한 후속) — 생성·상태 갱신 표면은 super-admin-api.
COMMENT ON TABLE tenant IS
'EDGE를 사용하는 고객사 환경 한 개 — writer = super-admin-api(테넌트 생성, ADR-0008).';
