-- ============================================================================
-- tenant_delivery writer 주석 정정 — fan-out 발번기 구현 반영(ALPHA-493)
--
-- NEW 발번은 analysis-engine 이 게시와 같은 트랜잭션에서 수행한다(write-time
-- fan-out). 원 마이그레이션(V202607211740)의 "writer = 후속" 주석은 적용 후
-- 수정 불가(Flyway 체크섬)라 정정만 새 버전으로 얹는다. 동작 변경 없음.
-- ============================================================================

SET search_path TO public;

COMMENT ON TABLE tenant_delivery IS
'테넌트별 전달 레코드(outbox) — 번들 조립 원장(event-bundle-schema.md). PK (tenant_id, cursor) = 전달 멱등 키. writer = analysis-engine write-time fan-out(NEW, ALPHA-493 — CORRECTION·INVALIDATION 발번은 후속), reader = tenant-sync-api.';
