-- ALPHA-999: 엔티티 해소 카운터 CHECK의 기존 행 검증을 확장 DDL과 분리한다.
--
-- `ADD CONSTRAINT` 즉시 검증은 ACCESS EXCLUSIVE를 잡은 채 기존 원장 전체를 스캔한다.
-- 앞 마이그레이션은 NOT VALID로 상수 시간에 끝내고, 여기서는 행 쓰기를 막지 않는
-- SHARE UPDATE EXCLUSIVE로 검증한다. 새 행에는 NOT VALID 상태에서도 CHECK가 즉시 적용된다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_expected_task
    VALIDATE CONSTRAINT ck_ops_expected_task_entity_resolution_counts;
