-- ============================================================================
-- 리비전 체인 컬럼 수축(contract) — ADR-0044 후속 (ALPHA-674)
--
-- expand 단계(#451)에서 writer/reader 코드를 제거하고 폐기 표기로 존치했던
-- supersedes_item_id·correction_reason 을 실제로 내린다. 자기참조 FK 와
-- ck_analysis_item_no_self_supersede CHECK 는 컬럼과 함께 자동 제거된다.
--
-- 적용 경로: 데모 박스는 deploy-demo-onprem 이 flyway 적용과 컨테이너 재생성을
-- 같은 런에서 수행한다 — expand 코드(#451)와 이 수축이 함께 실린다.
-- ============================================================================

SET search_path TO public;

ALTER TABLE analysis_item
    DROP COLUMN supersedes_item_id,
    DROP COLUMN correction_reason;
