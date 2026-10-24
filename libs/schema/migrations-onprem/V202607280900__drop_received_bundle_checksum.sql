-- ============================================================================
-- received_bundle.checksum — 컬럼 제거 (수축/contract)
--
-- ADR-0040 확장-수축의 마지막 단계. T1(V202607271500)이 CHECK·NOT NULL 을 풀었고(expand),
-- T2(ALPHA-585)로 cloud 가 X-Bundle-Checksum 헤더를 끊었으며, T3(ALPHA-586)에서 checksum 을
-- 읽고 쓰는 코드(sync-agent 검증·intake writer·SyncAgentClient 파싱)를 이 릴리스에서 함께
-- 제거했다. 이제 이 컬럼을 참조하는 writer/reader 가 없으므로 안전하게 DROP 한다.
--
-- 저장소 최초의 DROP COLUMN 이다. CHECK 제약(ck_received_bundle_checksum)·NOT NULL 은 relax
-- 마이그레이션이 이미 제거했으므로 여기선 컬럼만 드롭한다.
--
-- 배포 순서(ADR-0038 validate): checksum 매핑을 제거한 intake 와 같은 릴리스여야 한다 — 단일
-- 배포(deploy-demo-onprem 한 런의 flyway → 앱 recreate)라 이를 충족한다. 기존 데모 박스는
-- flyway DROP 직후~새 intake 기동 사이 구 intake 의 INSERT 가 수 초 실패하나 멱등 재-Pull 로
-- 자가치유되고, 신규 박스는 앱이 flyway 완료를 게이트해 무창이다.
--
-- Refs: ALPHA-586
-- ============================================================================

SET search_path TO public;

ALTER TABLE received_bundle
    DROP COLUMN checksum;

COMMENT ON TABLE received_bundle IS
'수신 번들 원본(응답 바이트 그대로, 불변) — Raw Event Store 최소형. 컬럼: cursor_from(PK)·cursor_to·body·received_at·screened_at. writer = intake. 앱 레벨 체크섬은 ADR-0040 으로 제거(무결성은 mTLS/TLS·목표 계약 서명 소관).';
