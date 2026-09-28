-- 오케스트레이터 CHECK의 기존 행 검증을 확장 DDL과 분리한다(기존 행은 기본값 SFN).

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_pipeline_run
    VALIDATE CONSTRAINT ck_ops_pipeline_run_orchestrator;
