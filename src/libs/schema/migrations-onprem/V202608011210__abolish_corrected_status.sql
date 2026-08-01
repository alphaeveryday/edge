-- ============================================================================
-- CORRECTED 상태·리비전 체인 폐지 (ADR-0044) — 정정 전달이 계약에서 사라졌다.
--
-- 틀린 게시의 종결 경로는 INVALIDATED 하나다. 기존 CORRECTED 행(로컬 시드
-- 관통분만 존재 가능 — 실환경엔 정정 발번이 구현된 적 없다)은 INVALIDATED 로
-- 이관하고, 상태 이력 규율대로 이관 이력을 SYSTEM 행으로 남긴다.
--
-- analysis_item_status_history 의 CHECK 어휘(CORRECTED 포함)는 건드리지 않는다
-- — append-only 원장의 과거 이력 보존이 우선이고, writer 코드 제거로 새 쓰기는
-- 없다(ADR-0044 결과 절).
-- ============================================================================

SET search_path TO public;

WITH migrated AS (
    UPDATE analysis_item
    SET status = 'INVALIDATED', updated_at = now()
    WHERE status = 'CORRECTED'
    RETURNING explanation_result_id
)
INSERT INTO analysis_item_status_history
    (analysis_item_id, from_status, to_status, actor_type, reason)
SELECT explanation_result_id, 'CORRECTED', 'INVALIDATED', 'SYSTEM',
       'CORRECTION 폐지 데이터 이관 (ADR-0044)'
FROM migrated;

ALTER TABLE analysis_item
    DROP CONSTRAINT ck_analysis_item_status,
    ADD CONSTRAINT ck_analysis_item_status
        CHECK (status IN ('RECEIVED', 'AUTO_PUBLISHED', 'REVIEW_REQUIRED', 'APPROVED',
                          'REJECTED', 'BLOCKED', 'UNPUBLISHED', 'INVALIDATED'));

-- 리비전 체인(정정분→원본 참조)은 폐지된 개념 — FK·자기참조 CHECK 는 컬럼과 함께
-- 내려간다.
ALTER TABLE analysis_item
    DROP COLUMN supersedes_item_id,
    DROP COLUMN correction_reason;

COMMENT ON TABLE analysis_item IS
'분석 항목 상태 원장(state-machine.md) — PK = Cloud 발번 explanation_result_id(멱등 upsert 키). writer 는 전이별 분담: screening-worker(수신·자동 분기·Cloud 무효화 반영), tenant-console-api(검수 결정·사후 운영 전이).';

COMMENT ON TABLE publication IS
'게시 원장(Published Store) — publication-api 서빙 소스. writer 는 전이별 분담: screening-worker(자동 게시·무효화), tenant-console-api(검수 승인 재발행·수동 제공 중단).';
