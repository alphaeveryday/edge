-- 논리 실행 1건을 어느 오케스트레이터가 실행하는지 원장에 남긴다 (Airflow 이관 1단계).
--
-- 레인별 단계 이관 동안 같은 슬롯(run_key)을 SFN과 Airflow가 함께 계획하면 같은 run_id로
-- 두 번 실행된다. Planner가 이 값으로 소유 충돌을 거부하고, Reconciler가 SFN history 대신
-- 원장 attempt를 증거로 쓸지 고른다. 기존 행은 전부 SFN이 실행했으므로 기본값이 곧 사실이다.
-- orchestrator_run_ref는 Airflow의 `dag_id/run_id`처럼 그 오케스트레이터 안의 실행 식별자다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_pipeline_run
    ADD COLUMN orchestrator TEXT NOT NULL DEFAULT 'SFN',
    ADD COLUMN orchestrator_run_ref TEXT,
    ADD CONSTRAINT ck_ops_pipeline_run_orchestrator CHECK (
        orchestrator IN ('SFN', 'AIRFLOW')
    ) NOT VALID;

COMMENT ON COLUMN ops_pipeline_run.orchestrator IS
'이 논리 실행을 계획하고 실행한 오케스트레이터(SFN|AIRFLOW). 같은 run_key를 다른 주체가 다시 계획하면 LAUNCH_CONFLICT다.';
COMMENT ON COLUMN ops_pipeline_run.orchestrator_run_ref IS
'오케스트레이터 내부 실행 식별자(Airflow는 dag_id/run_id). SFN은 sfn_execution_arn을 쓰므로 NULL이다.';
