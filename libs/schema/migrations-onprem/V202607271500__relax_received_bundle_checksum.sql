-- ============================================================================
-- received_bundle.checksum — CHECK 제거 + NOT NULL 해제 (순수 확장)
--
-- ADR-0040(sync 번들 무결성 MVP 스코프 재검토)이 발신자 SHA-256 체크섬 + byte[] 응답을
-- 목표 계약(서명)으로 이관하기로 확정했다. 무결성 검증은 DMZ Sync Agent 의 와이어 검증에
-- 남고, received_bundle.checksum 컬럼은 아무도 재검증하지 않는 중복 저장이라 제거 대상이다.
--
-- 이 마이그레이션은 그 제거의 1단계(expand)다. cloud 가 봉투(ApiResponse<EventBundle>)로
-- 전환하면(T2, ALPHA-585) 와이어에서 X-Bundle-Checksum 헤더가 사라져 intake 가 받는 checksum
-- 이 null 이 된다 — NOT NULL·CHECK 이 살아 있으면 INSERT 가 깨지므로, cloud 전환 전에 제약을
-- 먼저 푼다(ADR-0005 확장-수축: 신구 앱이 DB 를 공유하는 롤아웃 창을 견딘다).
--
-- 확장-수축 단계: 순수 확장(제약 완화). 현행 writer(intake ReceivedBundleRepository)는 여전히
--   checksum 을 non-null 로 INSERT 하므로 이 완화는 기존 동작을 깨지 않는다. 컬럼 DROP 은
--   모든 writer 가 checksum 을 안 쓰도록 배포된 뒤 별도 마이그레이션(T3, ALPHA-586)에서 한다.
--
-- V202607150003 을 그 자리에서 고치지 않는 이유: schema-migrate CD 가 이미 적용해 체크섬이
--   깨진다(적용된 마이그레이션 불변 규율).
--
-- Refs: ALPHA-584
-- ============================================================================

SET search_path TO public;

ALTER TABLE received_bundle
    DROP CONSTRAINT ck_received_bundle_checksum;

ALTER TABLE received_bundle
    ALTER COLUMN checksum DROP NOT NULL;

COMMENT ON COLUMN received_bundle.checksum IS
'수신 체크섬(sha256=<hex>). ADR-0040 으로 목표 계약 이관 중 — T2(cloud 봉투 전환) 후 헤더 소멸 시 null 가능, T3 에서 컬럼 제거 예정. 무결성 검증은 Sync Agent 와이어 단계 소관(중복 저장, 재검증 없음).';
