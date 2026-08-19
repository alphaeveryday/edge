-- ALPHA-1002: attempt 엔티티 해소 카운터 CHECK의 기존 행 검증을 확장 DDL과 분리한다.
-- NOT VALID 상태에서도 새 행은 즉시 검사되며, 이 단계는 행 쓰기를 막지 않는 락으로 기존 행을 본다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_task_attempt
    VALIDATE CONSTRAINT ck_ops_task_attempt_entity_resolution_counts;
