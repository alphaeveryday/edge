-- ALPHA-440: 무효화(INVALIDATION) 발번 — 감사 어휘 확장.
-- 운영자 무효화 액션이 admin_activity_log 에 ANALYSIS_INVALIDATED 로 남는다.
-- reason 필수 CHECK(ck_admin_activity_reason)는 "복원 외 필수" 배제형이라
-- 무효화에 자동 적용된다 — 재선언 불요.

ALTER TABLE admin_activity_log DROP CONSTRAINT ck_admin_activity_action;
ALTER TABLE admin_activity_log ADD CONSTRAINT ck_admin_activity_action CHECK (action IN
    ('ANALYSIS_RESULT_CORRECTED', 'ANALYSIS_EXCLUDED', 'ANALYSIS_RESTORED',
     'ANALYSIS_INVALIDATED'));

-- writer 현행화: INVALIDATION 발번이 super-admin-api 무효화 액션으로 붙었다(ALPHA-440).
-- 두 writer 는 같은 advisory lock('tenant-delivery-fanout')으로 cursor 채번을 직렬화한다.
COMMENT ON TABLE tenant_delivery IS
'테넌트별 전달 레코드(outbox) — 번들 조립 원장(event-bundle-schema.md). PK (tenant_id, cursor) = 전달 멱등 키. 전달 유형은 NEW·INVALIDATION 2형상(ADR-0044 — CORRECTION 폐지). writer = analysis-engine write-time fan-out(NEW, ALPHA-493) + super-admin-api 무효화 액션(INVALIDATION, ALPHA-440 — 동일 advisory lock 공유), reader = tenant-sync-api.';
