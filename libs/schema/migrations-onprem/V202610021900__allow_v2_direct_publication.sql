-- V2 has no v1 explanation classification or confidence assessment.
SET LOCAL lock_timeout = '5s';

ALTER TABLE analysis_item
    ADD COLUMN analysis_engine VARCHAR(10) NOT NULL DEFAULT 'v1',
    ALTER COLUMN explanation_type DROP NOT NULL,
    DROP CONSTRAINT ck_analysis_item_explanation_type,
    ADD CONSTRAINT ck_analysis_item_explanation_type CHECK (
        (analysis_engine = 'v1' AND explanation_type IS NOT NULL
            AND explanation_type IN ('PRICE_ONLY', 'EVENT_SUPPORTED', 'MIXED', 'UNCERTAIN'))
        OR (analysis_engine = 'v2' AND explanation_type IS NULL AND confidence_level IS NULL)
    );

COMMENT ON COLUMN analysis_item.analysis_engine IS
'전달 서버가 명시한 분석엔진. v1은 기존 검수 정책 적용, v2는 관리자 승인 없이 자동 노출.';
