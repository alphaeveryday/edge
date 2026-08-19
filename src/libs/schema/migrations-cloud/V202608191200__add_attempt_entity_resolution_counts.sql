-- 뉴스 assertion 엔티티 해소율을 물리 실행 시도와 직접 연결한다 (ALPHA-1002).
--
-- expected_task의 마지막 값만으로는 겹친 재시도·Reconciler 복구에서 어느 attempt가 센 pair인지
-- 증명할 수 없다. 후속 writer/API가 같은 attempt의 성공 증거와 카운터를 함께 읽도록 확장한다.
-- 기존 task-level 컬럼은 호환성을 위해 유지하고, 과거 attempt는 측정하지 않은 행이라 백필하지 않는다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_task_attempt
    ADD COLUMN entity_resolution_arguments_total    BIGINT,
    ADD COLUMN entity_resolution_arguments_resolved BIGINT,
    ADD CONSTRAINT ck_ops_task_attempt_entity_resolution_counts CHECK (
        (entity_resolution_arguments_total IS NULL
            AND entity_resolution_arguments_resolved IS NULL)
        OR (
            entity_resolution_arguments_total IS NOT NULL
            AND entity_resolution_arguments_resolved IS NOT NULL
            AND entity_resolution_arguments_total >= 0
            AND entity_resolution_arguments_resolved >= 0
            AND entity_resolution_arguments_resolved <= entity_resolution_arguments_total
        )
    ) NOT VALID;

COMMENT ON COLUMN ops_task_attempt.entity_resolution_arguments_total IS
'이 물리 시도가 센 뉴스 assertion 엔티티 해소 분모(argument 수). entity 역할만 세며 NULL은 계측 없음이다. 기존 행은 백필하지 않는다.';

COMMENT ON COLUMN ops_task_attempt.entity_resolution_arguments_resolved IS
'이 물리 시도가 센 뉴스 assertion 엔티티 해소 분자(argument 수). quality log argument_resolution.resolved_any를 같은 시도의 분모와 함께 저장하며 NULL은 계측 없음이다.';
