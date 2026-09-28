-- Airflow 실행과 업무 원장을 잇는 추적 컬럼 (ALPHA-1088, 확장 단계만).
--
-- ops_task_attempt.orchestrator_attempt_ref: 이 물리 시도(ECS 태스크 1개)를 띄운 오케스트레이터 시도.
--   Airflow 는 'airflow:<dag_id>/<run_id>/<task_id>/<try_number>'. SFN 은 sfn_execution_arn·sfn_state_name
--   이 이미 같은 역할을 하므로 NULL 이다. Airflow 재시도 번호와 ECS 태스크는 1:1 이 아니다 — 실행 중
--   태스크에 재접속하면 새 시도가 새 attempt 를 만들지 않고, 응답 유실 재시도는 새 ECS 태스크를 띄운다.
--   그래서 참조는 "그 ECS 태스크를 띄운 시도"만 뜻한다.
-- ops_pipeline_run.orchestration_reported_at: 오케스트레이터가 자기 판정(orchestration_status)을 마지막으로
--   보고한 시각. Reconciler 는 이보다 나중에 생긴 업무 시도가 있을 때만 원장 증거로 상태를 다시 투영한다.
-- 둘 다 nullable 이라 기존 행·SFN 경로는 그대로다(메타데이터 전용 ALTER).

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_task_attempt
    ADD COLUMN orchestrator_attempt_ref TEXT;

ALTER TABLE ops_pipeline_run
    ADD COLUMN orchestration_reported_at TIMESTAMPTZ;

COMMENT ON COLUMN ops_task_attempt.orchestrator_attempt_ref IS
'이 ECS 태스크를 띄운 오케스트레이터 시도(Airflow: airflow:dag_id/run_id/task_id/try_number). SFN 은 NULL(sfn_execution_arn 사용).';
COMMENT ON COLUMN ops_pipeline_run.orchestration_reported_at IS
'오케스트레이터가 orchestration_status 를 마지막으로 보고한 시각. 이후 업무 시도가 생기면 Reconciler 가 다시 투영한다.';
COMMENT ON TABLE ops_task_attempt IS
'논리 작업 1건에 대한 물리 실행 시도. (expected_task_id, ecs_task_arn) 멱등. ECS ARN 없는 submit 실패는 여기 안 남긴다. record_source 로 정상 계측(WRAPPER)·사후 복구(RECONCILER_BACKFILL)·업무를 실행하지 않은 중복 재시도 컨테이너(DUPLICATE_SKIP)를 구분한다.';
